from __future__ import annotations

import typing as t

from mountainash.relations import Relation

from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.decision_table.errors import DecisionTableError


def select_scopes(
    metadata: DimensionsMetadata, scopes: t.Sequence[str] | None
) -> tuple[str, ...]:
    """Select declared output scopes, preserving the caller's order."""
    declared = tuple(dict.fromkeys(output.scope for output in metadata.outputs if output.scope is not None)) or ("default",)
    if scopes is None:
        return declared
    if not isinstance(scopes, (list, tuple)) or any(
        not isinstance(scope, str) for scope in scopes
    ):
        raise DecisionTableError("scopes must be a sequence of declared scope names")
    unknown = [scope for scope in scopes if scope not in declared]
    if unknown:
        raise DecisionTableError(f"Unknown outcome scope: {unknown[0]!r}")
    return tuple(dict.fromkeys(scopes))


def check_sources(rules: Relation, metadata: DimensionsMetadata) -> Relation:
    """Check that the rules relation provides its declared stable identity."""
    if not isinstance(rules, Relation):
        raise DecisionTableError("rules must be a Mountainash Relation")
    identity = getattr(metadata, "uuid_column", None) or getattr(metadata, "uuid_field", None)
    if identity is None or identity not in rules.columns:
        raise DecisionTableError("Rules are missing the declared source identity column")
    return rules
