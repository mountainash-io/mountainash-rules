"""Immutable owners for a captured accumulator geometry version."""

from __future__ import annotations

import json
import os
import typing as t
from dataclasses import dataclass, field as dataclass_field, replace
from types import MappingProxyType
from uuid import UUID, uuid4

import mountainash.expressions as ma
from mountainash import materialization_scope
from mountainash.relations import Relation, relation

from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.engines.accumulate.folds import fold_contributions
from mountainash_rules.engines.accumulate.sources import SourceBundle, SourceReport, capture_sources
from mountainash_rules.engines.accumulate.updates import UpdateResult, classify_update

if t.TYPE_CHECKING:
    from mountainash_rules.engines.accumulate.manifest import UnavailableEvidenceReference
    from mountainash_rules.engines.accumulate.validate import Approval, ArtifactReport
    from mountainash_rules.engines.accumulate.result import ResolutionResult


@dataclass(frozen=True, slots=True)
class TableDescriptor:
    """Manifest metadata for one physical generated relation."""

    table_id: str
    role: str
    schema: tuple[tuple[str, t.Any], ...]
    key: tuple[str, ...]
    scope: str | None = None
    field: str | None = None
    capture_id: UUID = dataclass_field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class Lattice:
    """A retained geometry/source snapshot with immutable generated relations."""

    tables: t.Mapping[str, Relation]
    table_descriptors: tuple[TableDescriptor, ...]
    sources: SourceBundle
    geometry_namespace: UUID
    _metadata_json: str
    output_columns: t.Mapping[str, t.Mapping[str, str]]
    version_id: UUID = dataclass_field(default_factory=uuid4)
    parent_version_id: UUID | None = None
    published: bool = False
    evidence: tuple[SourceReport | ArtifactReport | UnavailableEvidenceReference, ...] = ()
    approvals: tuple[Approval, ...] = ()
    _observations_json: str = "[]"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tables", MappingProxyType(dict(self.tables)))
        object.__setattr__(self, "table_descriptors", tuple(self.table_descriptors))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "approvals", tuple(self.approvals))
        object.__setattr__(
            self, "output_columns",
            MappingProxyType({name: MappingProxyType(dict(columns))
                              for name, columns in self.output_columns.items()}),
        )

    @property
    def metadata(self) -> DimensionsMetadata:
        """Return a fresh metadata copy; callers cannot mutate this snapshot."""
        return DimensionsMetadata.model_validate_json(self._metadata_json)

    @property
    def observations(self) -> list[t.Any]:
        """Return independent copies of retained transport observations."""
        return json.loads(self._observations_json)

    def resolve(
        self, contexts: Relation, scopes: t.Sequence[str] | None = None, *,
        request_control: Relation | None = None, masks: Relation | None = None,
        include_cell_key: bool = False, include_contributors: bool = False,
        include_candidates: bool = False,
    ) -> ResolutionResult:
        """Read retained geometry and outputs under the selected request profiles."""
        from mountainash_rules.engines.accumulate.resolve import resolve_lattice

        return resolve_lattice(
            self, contexts, scopes, request_control=request_control, masks=masks,
            include_cell_key=include_cell_key, include_contributors=include_contributors,
            include_candidates=include_candidates,
        )

    def publish(
        self, *, policy: str = "unchecked",
        source_report: SourceReport | None = None,
        artifact_report: ArtifactReport | None = None,
        approvals: t.Iterable[Approval] = (),
    ) -> Lattice:
        """Publish the same captured version; validation is explicitly separate."""
        from mountainash_rules.engines.accumulate.validate import publish_lattice

        return publish_lattice(
            self, policy=policy, source_report=source_report,
            artifact_report=artifact_report, approvals=tuple(approvals),
        )

    def save(self, directory: str | os.PathLike[str]) -> None:
        """Write a new immutable bundle without replacing an existing path."""
        from mountainash_rules.engines.accumulate.manifest import save

        save(self, directory)

    @classmethod
    def load(cls, directory: str | os.PathLike[str]) -> Lattice:
        """Restore stored tables without construction or source folding."""
        from mountainash_rules.engines.accumulate.manifest import load

        return load(directory)

    @classmethod
    def from_tables(
        cls, relations: t.Mapping[str, Relation], *, manifest: t.Mapping[str, t.Any],
        evidence: t.Iterable[SourceReport | ArtifactReport | UnavailableEvidenceReference] = (),
    ) -> Lattice:
        """Capture attached tables; manifest labels do not establish equality."""
        from mountainash_rules.engines.accumulate.manifest import from_tables

        return from_tables(relations, manifest=manifest, evidence=tuple(evidence))

    def _role(self, role: str, *, scope: str | None = None, field: str | None = None) -> Relation:
        descriptors = [d for d in self.table_descriptors
                       if d.role == role and d.scope == scope and d.field == field]
        if len(descriptors) != 1:
            raise KeyError((role, scope, field))
        return self.tables[descriptors[0].table_id]

    @property
    def cells(self) -> Relation:
        return self._role("cells")

    @property
    def cell_boxes(self) -> Relation:
        return self._role("cell_boxes")

    @property
    def contributors(self) -> Relation:
        return self._role("contributors")

    @property
    def cell_ordered_bounds(self) -> t.Mapping[str, Relation]:
        return MappingProxyType({d.field: self.tables[d.table_id]
                                 for d in self.table_descriptors
                                 if d.role == "cell_ordered_bounds" and d.field is not None})

    @property
    def cell_category_sets(self) -> t.Mapping[str, Relation]:
        return MappingProxyType({d.field: self.tables[d.table_id]
                                 for d in self.table_descriptors
                                 if d.role == "cell_category_sets" and d.field is not None})

    @property
    def cell_category_values(self) -> t.Mapping[str, Relation]:
        return MappingProxyType({d.field: self.tables[d.table_id]
                                 for d in self.table_descriptors
                                 if d.role == "cell_category_values" and d.field is not None})

    @property
    def outputs(self) -> t.Mapping[str, Relation]:
        return MappingProxyType({d.scope: self.tables[d.table_id]
                                 for d in self.table_descriptors
                                 if d.role == "outputs" and d.scope is not None})

    def update(
        self, rules: Relation, *, metadata: DimensionsMetadata | None = None,
    ) -> UpdateResult:
        """Compare a complete candidate against this immutable receiver."""
        candidate = capture_sources(rules, self.metadata if metadata is None else metadata)
        return classify_update(self, candidate, "full")

    def update_contributions(self, patch: Relation) -> UpdateResult:
        """Apply qualified contribution presence without an implicit current head."""
        return classify_update(self, patch, "patch")

    def recompute_contributions(self, candidate_sources: SourceBundle) -> Lattice:
        """Recompute complete distinct contributor-set folds without changing geometry."""
        metadata = self.metadata
        scopes = metadata.outcome_scopes or {"default": metadata.aggregates}
        replacements: dict[str, Relation] = {}
        for scope, aggregates in scopes.items():
            memberships = self.contributors.filter(ma.col("scope").eq(scope))
            payload = candidate_sources.payloads[scope]
            joined = memberships.join(
                payload,
                on=["partition", "scope", "source_uuid"],
                how="inner",
            ).select(
                "partition", "scope", "contributor_set_id",
                *[ma.col(f"a{index:04d}") for index in range(1, len(aggregates) + 1)],
            )
            output = fold_contributions(
                joined, aggregates, keys=["partition", "scope", "contributor_set_id"],
            )
            output_descriptor = next(
                d for d in self.table_descriptors
                if d.role == "outputs" and d.scope == scope
            )
            replacements[output_descriptor.table_id] = output

        output_ids = tuple(replacements)

        for descriptor in self.table_descriptors:
            if descriptor.role == "source_rules":
                replacements[descriptor.table_id] = candidate_sources.rules
            elif descriptor.role == "source_state" and descriptor.field is not None:
                replacements[descriptor.table_id] = candidate_sources.states[descriptor.field]
            elif descriptor.role == "source_payloads" and descriptor.scope is not None:
                replacements[descriptor.table_id] = candidate_sources.payloads[descriptor.scope]

        new_tables = dict(self.tables)
        # Candidate sources already own their captures; retain those handles.
        new_tables.update(replacements)
        ordered = [
            (table_id, relation(replacements[table_id].collect()))
            for table_id in output_ids
        ]
        with materialization_scope() as owner:
            owned = [owner.detach(rel) for rel in owner.capture(
                *(relation for _, relation in ordered)
            )]
        for (table_id, _), captured in zip(ordered, owned):
            new_tables[table_id] = captured
        descriptors = tuple(
            replace(
                descriptor,
                schema=tuple(new_tables[descriptor.table_id].schema.items()),
                capture_id=uuid4(),
            ) if descriptor.table_id in replacements else descriptor
            for descriptor in self.table_descriptors
        )
        return Lattice(
            tables=new_tables, table_descriptors=tuple(descriptors), sources=candidate_sources,
            geometry_namespace=self.geometry_namespace, _metadata_json=self._metadata_json,
            output_columns=self.output_columns,
            parent_version_id=self.version_id,
            evidence=self.evidence, approvals=self.approvals,
        )
