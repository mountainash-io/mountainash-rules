"""Immutable native resolution collections with explicit output selection."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass
from types import MappingProxyType

import mountainash.expressions as ma
from mountainash.relations import Relation

from mountainash_rules.core.dimension import DimensionsMetadata


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Decision payloads, admission and provenance have independent relations."""

    requests: Relation
    status: Relation
    requested_outputs: Relation
    payloads: t.Mapping[str, Relation]
    payload_columns: t.Mapping[str, t.Mapping[str, str]]
    _metadata_json: str
    contributors: Relation | None = None
    candidates: Relation | None = None
    cell_identity: Relation | None = None
    cell_identity_boxes: Relation | None = None
    cell_identity_contributors: Relation | None = None
    cell_identity_ordered_bounds: t.Mapping[str, Relation] | None = None
    cell_identity_category_sets: t.Mapping[str, Relation] | None = None
    cell_identity_category_values: t.Mapping[str, Relation] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payloads", MappingProxyType(dict(self.payloads)))
        object.__setattr__(self, "payload_columns", MappingProxyType({
            scope: MappingProxyType(dict(columns)) for scope, columns in self.payload_columns.items()
        }))
        for name in ("cell_identity_ordered_bounds", "cell_identity_category_sets", "cell_identity_category_values"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))

    @property
    def metadata(self) -> DimensionsMetadata:
        return DimensionsMetadata.model_validate_json(self._metadata_json)

    def payload(self, scope: str) -> Relation:
        """Return one selected scope; absent selection is not an empty decision."""
        return self.payloads[scope]

    def wide(self) -> Relation:
        """Join typed payloads under unambiguous scope-qualified logical names.

        Selection remains in requested_outputs; null transport slots do not add
        certainty or turn an unresolved request into a decision.
        """
        result = self.requests.select("request_id", "request_order")
        for scope, payload in self.payloads.items():
            named = payload.select("request_id", *[
                ma.col(physical).alias(f"{scope}.{logical}")
                for logical, physical in self.payload_columns[scope].items()
            ])
            result = result.join(named, on="request_id", how="left")
        return result.sort("request_order").drop("request_order")
