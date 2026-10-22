"""Build a DecisionTable from prevalidated rules.

``build_decision_table`` runs stages a-f once, in order, over ``ValidatedRules.rules`` (the
relation prevalidation already captured: nothing is validated or fetched again).  The stages
share a Mountainash materialization scope: relations read more than once are checkpointed
(``owner.checkpoint``), every terminal relation is snapshotted whatever the checkpoint policy
and detached so that it outlives the scope, and a failure at any point closes the scope and
with it every snapshot it owns.

Backend execution counts (scalar reads and snapshots the build issues, plus the one
fingerprint read of the rules) are logged at each stage boundary on this module's logger;
they are observability, not part of the result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import typing as t
from dataclasses import dataclass

from mountainash import CheckpointPolicy, materialization_scope
from mountainash.relations import Relation

from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.metadata_tables import metadata_to_tables
from mountainash_rules.decision_table.prevalidate import ValidatedRules
from mountainash_rules.decision_table.stages import (
    Materializer,
    stage_a_boundaries,
    stage_b_pieces,
    stage_c_coverage,
    stage_d_membership,
    stage_e_entry_sources,
    stage_f_entries,
)
from mountainash_rules.decision_table.table import DecisionTable

logger = logging.getLogger(__name__)


@dataclass
class _Executions:
    """Backend executions issued by the build, logged per stage."""

    total: int = 0
    reported: int = 0

    def boundary(self, stage: str) -> None:
        since, self.reported = self.total - self.reported, self.total
        logger.info(
            "decision table build stage %s: %d backend executions (%d in total)",
            stage,
            since,
            self.total,
            extra={"build_stage": stage, "executions": since, "executions_total": self.total},
        )


class _ScopeMaterializer(Materializer):
    """Stage hooks backed by a materialization scope; counts what executes."""

    def __init__(self, owner: t.Any, executions: _Executions):
        self._owner = owner
        self._executions = executions

    def checkpoint(self, relation: Relation, *, replaces: Relation | None = None) -> Relation:
        result = self._owner.checkpoint(relation, replaces=replaces)
        if result is not relation:  # the policy copied this stage
            self._executions.total += 1
        return result

    def count_rows(self, relation: Relation) -> int:
        self._executions.total += 1
        return relation.count_rows()


def declared_rule_columns(metadata: DimensionsMetadata) -> tuple[str, ...]:
    """Every rule column the metadata declares: identity, scope, dimension cells, contributions."""
    columns: list[str | None] = [metadata.uuid_column, metadata.scope_column]
    for dimension in metadata.dimensions:
        if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue  # the pattern is metadata, not a rule column
        if dimension.match_strategy is MatchStrategy.RANGE:
            columns += [dimension.range_min_field, dimension.range_max_field]
        else:
            columns.append(dimension.resolved_rule_field)
    columns += [output.contribution_column for output in metadata.outputs]
    return tuple(dict.fromkeys(column for column in columns if column is not None))


def compute_build_id(validated: ValidatedRules) -> str:
    """Deterministic identity of the build inputs: the declared rule columns and the metadata.

    The rule columns are fingerprinted by the backend (``Relation.fingerprint``: per-column,
    row-order independent, no rows reach Python objects); the canonical metadata tables are
    small host-side values.  Identical inputs give the identical id.
    """
    metadata = validated.source_metadata
    declared = declared_rule_columns(metadata)
    keys = (metadata.uuid_column,)
    values = tuple(column for column in declared if column not in keys) or keys
    fingerprint = validated.rules.fingerprint(keys=keys, columns=values).to_dicts()
    tables = metadata_to_tables(metadata, relation_factory=list)
    tables["truthy"] = sorted(
        tables["truthy"], key=lambda row: (row["context_field"], row["value"] is None, row["value"] or "")
    )
    canonical = json.dumps(
        {"rules": fingerprint, "metadata": tables},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_decision_table(
    validated: ValidatedRules,
    *,
    checkpoint: CheckpointPolicy = CheckpointPolicy.EVERY_STAGE,
) -> DecisionTable:
    """Build the DecisionTable of ``validated`` rules.

    ``checkpoint`` chooses which reusable intermediates are copied (``EVERY_STAGE`` or
    ``NEVER``, or ``CheckpointPolicy.every(k)``); the table is the same whichever is chosen.
    """
    executions = _Executions()
    with materialization_scope(checkpoint=checkpoint) as owner:
        reuse = _ScopeMaterializer(owner, executions)

        boundaries = stage_a_boundaries(validated, reuse)
        executions.boundary("a")
        pieces = stage_b_pieces(boundaries)
        executions.boundary("b")
        coverage = stage_c_coverage(boundaries, pieces)
        executions.boundary("c")
        membership = stage_d_membership(coverage, reuse)
        executions.boundary("d")
        folded = stage_e_entry_sources(validated, membership, reuse)
        executions.boundary("e")
        emitted = stage_f_entries(boundaries, membership, folded)

        # Every terminal relation is snapshotted whatever the checkpoint policy, then detached
        # so it outlives the scope; everything else the scope owns is released at its close.
        terminals = []
        for relation in (emitted.entries, emitted.entry_sources, emitted.pattern_conditions):
            terminals.append(owner.detach(owner.snapshot(relation)))
            executions.total += 1
        build_id = compute_build_id(validated)
        executions.total += 1
        executions.boundary("f")
    entries, entry_sources, pattern_conditions = terminals
    return DecisionTable(
        entries=entries,
        source_metadata=validated.source_metadata,
        entry_sources=entry_sources,
        pattern_conditions=pattern_conditions,
        build_id=build_id,
    )
