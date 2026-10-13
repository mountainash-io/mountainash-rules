"""Exact native full-table and qualified-patch updates against an explicit base."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash import materialization_scope
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.codec import canonical_bytes
from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.numeric import reject_invalid
from mountainash_rules.engines.accumulate.sources import KEYS, SourceBundle, capture_sources

if t.TYPE_CHECKING:
    from mountainash_rules.engines.accumulate.lattice import Lattice


@dataclass(frozen=True, slots=True)
class UpdateResult:
    change: t.Literal["unchanged", "payload_changed", "structure_changed"]
    lattice: Lattice | None
    candidate_rules: Relation
    _metadata_json: str
    changed_sources: Relation

    @property
    def candidate_metadata(self) -> DimensionsMetadata:
        return DimensionsMetadata.model_validate_json(self._metadata_json)


def _key_expressions(metadata: DimensionsMetadata):
    return [
        (ma.col(metadata.partition_field) if metadata.partition_field else ma.lit("default")).alias("partition"),
        (ma.col(metadata.outcome_scope_field) if metadata.outcome_scope_field else ma.lit("default")).alias("scope"),
        ma.col(metadata.uuid_field).alias("source_uuid"),
    ]


def _scopes(metadata: DimensionsMetadata):
    return metadata.outcome_scopes or {"default": metadata.aggregates}


def _structural_columns(metadata: DimensionsMetadata, columns: t.Sequence[str], scope: str) -> set[str]:
    contributions = {aggregate.column_name for aggregate in _scopes(metadata)[scope]}
    structural = set(columns) - contributions
    structural.update(field for field in (metadata.uuid_field, metadata.partition_field, metadata.outcome_scope_field) if field)
    for dim in metadata.dimensions:
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        fields: tuple[str | None, ...]
        if dim.match_strategy is MatchStrategy.RANGE:
            fields = (dim.range_min_field, dim.range_max_field, dim.range_min_unbounded_field, dim.range_max_unbounded_field)
        else:
            fields = (dim.resolved_rule_field, dim.rule_wildcard_field)
        structural.update(field for field in fields if field)
    return structural.intersection(columns)


def _changed_rows(left: SourceBundle, right: SourceBundle, columns: t.Sequence[str]) -> Relation:
    """Return actual changed identities, including inserted and removed keys."""
    old = left.rules.select(*_key_expressions(left.metadata), *[
        ma.col(column).alias(f"__old_{index}") for index, column in enumerate(columns)
    ])
    new = right.rules.select(*_key_expressions(right.metadata), *[
        ma.col(column).alias(f"__new_{index}") for index, column in enumerate(columns)
    ])
    removed = old.select(*KEYS).join(new.select(*KEYS), on=list(KEYS), how="anti")
    inserted = new.select(*KEYS).join(old.select(*KEYS), on=list(KEYS), how="anti")
    changed = ma.lit(False)
    for index in range(len(columns)):
        before, after = ma.col(f"__old_{index}"), ma.col(f"__new_{index}")
        same = before.eq(after).fill_null(False) | (before.is_null() & after.is_null())
        changed = changed | ~same
    matched = old.join(new, on=list(KEYS), how="inner", coalesce=True).filter(changed).select(*KEYS)
    return concat([removed, inserted, matched]).unique(subset=list(KEYS))


def _patch_candidate(base: SourceBundle, patch: Relation) -> SourceBundle:
    if not isinstance(patch, Relation):
        raise TypeError("A contribution patch must be a Mountainash Relation")
    metadata = base.metadata
    if not set(KEYS).issubset(patch.columns):
        raise ValueError("A contribution patch requires partition, scope and source_uuid")
    supplied = [column for column in patch.columns if column not in KEYS]
    if any(column not in base.rules.columns for column in supplied):
        raise ValueError("A contribution patch contains an unknown column")
    with materialization_scope() as operation:
        patch = operation.snapshot(patch)
        for key in KEYS:
            if patch.schema[key] != base.identities.schema[key]:
                raise ValueError("Contribution patch identity dtypes are incompatible")
        reject_invalid(patch, {"patch identity": ma.col("partition").is_null() | ma.col("scope").is_null() | ma.col("source_uuid").is_null()})
        duplicate = patch.group_by(*KEYS).agg(ma.count_records().alias("occurrences"))
        reject_invalid(duplicate, {"duplicate patch identity": ma.col("occurrences").gt(1)})
        if patch.select(*KEYS).join(base.identities, on=list(KEYS), how="anti").count_rows():
            raise ValueError("Contribution patch contains unknown qualified identities")
        for scope in _scopes(metadata):
            forbidden = set(supplied) & _structural_columns(metadata, base.rules.columns, scope)
            if forbidden and patch.filter(ma.col("scope").eq(scope)).count_rows():
                raise ValueError("Contribution patch contains structural or out-of-scope columns")
        # Project both sides into private slots before joining, so an authored
        # column named like a transport key is never overwritten by that key.
        originals = base.rules.columns
        authored = base.rules.select(*_key_expressions(metadata), *[
            ma.col(column).alias(f"__base_{index}") for index, column in enumerate(originals)
        ])
        changes = patch.select(*KEYS, ma.lit(True).alias("__patch_present"), *[
            ma.col(column).alias(f"__patch_{index}") for index, column in enumerate(supplied)
        ])
        merged = authored.join(changes, on=list(KEYS), how="left", coalesce=True)
        positions = {column: index for index, column in enumerate(supplied)}
        expressions = []
        for index, column in enumerate(originals):
            value = ma.col(f"__base_{index}")
            if column in positions:
                value = ma.when(ma.col("__patch_present").fill_null(False)).then(
                    ma.col(f"__patch_{positions[column]}")
                ).otherwise(value)
            expressions.append(value.alias(column))
        # Logical collection resolves supported cross-connection joins first;
        # capture_sources then takes the independently owned native snapshot.
        return capture_sources(relation(merged.select(*expressions).collect()), metadata)


def classify_update(base: Lattice, candidate: SourceBundle | Relation, form: str) -> UpdateResult:
    """Classify actual values; never build structural changes or consult reports."""
    if form == "patch":
        candidate = _patch_candidate(base.sources, candidate)
    elif form != "full":
        raise ValueError("Update form must be full or patch")
    if not isinstance(candidate, SourceBundle):
        raise TypeError("A full update requires captured candidate sources")
    original = base.sources
    for key in KEYS:
        if original.identities.schema[key] != candidate.identities.schema[key]:
            raise ValueError("Update identity dtypes are incompatible; build a new lattice")
    old_metadata, new_metadata = original.metadata, candidate.metadata
    metadata_changed = canonical_bytes(old_metadata.model_dump(mode="json")) != canonical_bytes(new_metadata.model_dump(mode="json"))
    schema_changed = original.rules.schema != candidate.rules.schema
    if schema_changed:
        changed = concat([original.identities, candidate.identities]).unique(subset=list(KEYS))
    else:
        changed = _changed_rows(original, candidate, original.rules.columns)
    with materialization_scope() as operation:
        # Snapshot's documented SQL precondition is one physical connection.
        # collect keeps Ibis deferred/native while resolving the join placement.
        changed = operation.snapshot(relation(changed.collect()))
        structural = metadata_changed or schema_changed
        changed_count = changed.count_rows()
        if not structural and changed_count:
            for scope in _scopes(old_metadata):
                fields = sorted(_structural_columns(old_metadata, original.rules.columns, scope))
                if _changed_rows(original, candidate, fields).filter(ma.col("scope").eq(scope)).count_rows():
                    structural = True
                    break
        change: t.Literal["unchanged", "payload_changed", "structure_changed"]
        if structural:
            change, lattice = "structure_changed", None
        elif not changed_count:
            change, lattice = "unchanged", base
        else:
            change, lattice = "payload_changed", base.recompute_contributions(candidate)
        retained = operation.detach(changed)
    return UpdateResult(change, lattice, candidate.rules, new_metadata.model_dump_json(), retained)
