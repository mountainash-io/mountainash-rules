"""Explicit scoped drift observations over retained accumulator tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TableSelection:
    table_id: str
    columns: tuple[str, ...] | str
    where: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.table_id, str) or not self.table_id:
            raise ValueError("table_id must be non-empty")
        if self.columns != "all":
            if isinstance(self.columns, (str, bytes)) or not isinstance(self.columns, tuple):
                raise TypeError("columns must be 'all' or a tuple of names")
            if not self.columns or not all(isinstance(name, str) for name in self.columns):
                raise ValueError("columns must select at least one named column")
            if len(set(self.columns)) != len(self.columns):
                raise ValueError("columns cannot repeat names")


@dataclass(frozen=True, slots=True)
class TableComparison:
    table_id: str
    role: str
    scope: str | None
    field: str | None
    keys: tuple[str, ...]
    columns: tuple[str, ...]
    where: Any
    status: str
    reason: str | None
    baseline: Any = None
    current: Any = None


def _profile_map(frame: Any) -> dict[tuple[str, str | None], dict[str, Any]]:
    return {(row["kind"], row["column"]): row for row in frame.to_dicts()}


def _entry(baseline: Any, current: Any, left: Any, right: Any, columns: tuple[str, ...], where: Any) -> TableComparison:
    base = left.fingerprint(keys=baseline.key, columns=columns)
    deployed = right.fingerprint(keys=current.key, columns=columns)
    a, b = _profile_map(base), _profile_map(deployed)
    reason: str | None
    if a.keys() != b.keys() or any(
        a[name]["profile"] != b[name]["profile"]
        or a[name]["key_schema"] != b[name]["key_schema"]
        or a[name]["value_schema"] != b[name]["value_schema"]
        for name in a
    ):
        status, reason = "not_comparable", "fingerprint profiles or schemas differ"
    else:
        drift = any(
            a[name]["row_count"] != b[name]["row_count"]
            or a[name]["fingerprint"] != b[name]["fingerprint"]
            for name in a
        )
        status, reason = ("drift_detected", None) if drift else ("no_drift_detected", None)
    return TableComparison(
        table_id=baseline.table_id, role=baseline.role, scope=baseline.scope,
        field=baseline.field, keys=baseline.key, columns=columns, where=where,
        status=status, reason=reason, baseline=base, current=deployed,
    )


def compare(baseline: Any, current: Any, *, scope: str | tuple[TableSelection, ...]) -> tuple[TableComparison, ...]:
    """Fingerprint only explicitly selected table rows and value columns."""
    left_by_id = {d.table_id: d for d in baseline.table_descriptors}
    right_by_semantics: dict[tuple[str, str | None, str | None], list[Any]] = {}
    for descriptor in current.table_descriptors:
        right_by_semantics.setdefault((descriptor.role, descriptor.scope, descriptor.field), []).append(descriptor)
    if scope == "all":
        selections = tuple(TableSelection(d.table_id, "all") for d in baseline.table_descriptors)
        explicit_all = True
    elif isinstance(scope, tuple) and all(isinstance(item, TableSelection) for item in scope):
        selections = scope
        explicit_all = False
    else:
        raise TypeError("scope must be 'all' or a tuple of TableSelection records")
    if not selections and not explicit_all:
        return ()
    results: list[TableComparison] = []
    for selection in selections:
        if selection.table_id not in left_by_id:
            raise KeyError(selection.table_id)
        descriptor = left_by_id[selection.table_id]
        candidates = right_by_semantics.get((descriptor.role, descriptor.scope, descriptor.field), [])
        current_descriptor = candidates[0] if len(candidates) == 1 else None
        values = (
            tuple(name for name in dict(descriptor.schema) if name not in descriptor.key)
            if selection.columns == "all" else tuple(selection.columns)
        )
        if not values:
            if not descriptor.key:
                raise ValueError("keyless tables cannot be fingerprinted")
            values = (descriptor.key[0],)
        if any(name not in dict(descriptor.schema) for name in values):
            raise ValueError("selected baseline columns do not exist")
        if current_descriptor is None:
            reason = "no matching current descriptor" if not candidates else "ambiguous current descriptors"
            results.append(TableComparison(descriptor.table_id, descriptor.role, descriptor.scope,
                descriptor.field, descriptor.key, values, selection.where, "not_comparable", reason))
            continue
        if any(name not in dict(current_descriptor.schema) for name in values):
            results.append(TableComparison(descriptor.table_id, descriptor.role, descriptor.scope,
                descriptor.field, descriptor.key, values, selection.where, "not_comparable",
                "selected current column is absent"))
            continue
        if descriptor.key != current_descriptor.key:
            results.append(TableComparison(descriptor.table_id, descriptor.role, descriptor.scope,
                descriptor.field, descriptor.key, values, selection.where, "not_comparable",
                "complete key declarations differ"))
            continue
        if descriptor.role not in {"source_rules", "source_state", "source_payloads"} and baseline.geometry_namespace != current.geometry_namespace:
            results.append(TableComparison(descriptor.table_id, descriptor.role, descriptor.scope,
                descriptor.field, descriptor.key, values, selection.where, "not_comparable",
                "generated geometry namespaces differ"))
            continue
        left = baseline.tables[descriptor.table_id]
        right = current.tables[current_descriptor.table_id]
        if selection.where is not None:
            left, right = left.filter(selection.where), right.filter(selection.where)
        results.append(_entry(descriptor, current_descriptor, left, right, values, selection.where))
    if explicit_all:
        baseline_semantics = {(d.role, d.scope, d.field) for d in baseline.table_descriptors}
        for descriptor in current.table_descriptors:
            if (descriptor.role, descriptor.scope, descriptor.field) not in baseline_semantics:
                values = tuple(name for name in dict(descriptor.schema) if name not in descriptor.key)
                if not values:
                    values = (descriptor.key[0],) if descriptor.key else ()
                results.append(TableComparison(
                    descriptor.table_id, descriptor.role, descriptor.scope, descriptor.field,
                    descriptor.key, values, None, "not_comparable", "no matching baseline descriptor",
                ))
    return tuple(results)
