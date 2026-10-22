"""Capture and validate authored DecisionTable rules before construction."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Any, Literal

from mountainash import DecimalDtype, MountainashDtype
from mountainash.core.dtypes import dtype_family

from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata


@dataclass(frozen=True, order=True)
class Finding:
    """One deterministic authored-data contract finding."""

    kind: str
    severity: Literal["error", "warning"]
    detail: str
    rule_uuids: tuple[Any, ...] = ()


class DataContractError(ValueError):
    """All blocking rule-data breaches and concurrently discovered warnings."""

    def __init__(self, breaches: tuple[Finding, ...], warnings: tuple[Finding, ...]):
        self.breaches = breaches
        self.warnings = warnings
        super().__init__(f"{len(breaches)} decision-table data-contract breach(es)")


@dataclass(frozen=True)
class ValidatedRules:
    """The captured source relation and immutable validation result."""

    rules: Any
    source_metadata: DimensionsMetadata
    warnings: tuple[Finding, ...]


_ORDERED = {DataType.INT, DataType.DECIMAL, DataType.DATE, DataType.DATETIME}
_SET = {MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION}
_PATTERNS = {MatchStrategy.REGEX, MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS, MatchStrategy.CONTEXT_REGEX}


def _sorted_findings(findings: list[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(findings, key=lambda finding: (finding.kind, finding.detail, finding.rule_uuids)))


def _uuids(values: list[Any]) -> tuple[Any, ...]:
    return tuple(sorted(set(value for value in values if value is not None), key=repr))


def _scope(row: dict[str, Any], metadata: DimensionsMetadata) -> str:
    return "default" if metadata.scope_column is None else row.get(metadata.scope_column)


def _declared_scopes(metadata: DimensionsMetadata) -> set[str]:
    scopes = {output.scope or "default" for output in metadata.outputs}
    return scopes or {"default"}


def _is_dtype(value: Any, field: ContextField) -> bool:
    if value is None:
        return field.wildcard_declared and field.wildcard is None
    if field.data_type is DataType.STR:
        return type(value) is str
    if field.data_type is DataType.BOOL:
        return type(value) is bool
    if field.data_type is DataType.INT:
        return type(value) is int
    if field.data_type is DataType.DECIMAL:
        return isinstance(value, Decimal)
    if field.data_type is DataType.DATE:
        return isinstance(value, dt.date) and not isinstance(value, dt.datetime)
    if field.data_type is DataType.DATETIME:
        return isinstance(value, dt.datetime) and (
            (value.tzinfo is None) if field.timezone == "naive" else (value.tzinfo is not None)
        )
    if field.data_type is DataType.FLOAT:
        return type(value) in (int, float)
    return False


def _is_wildcard(value: Any, field: ContextField) -> bool:
    return field.wildcard_declared and value == field.wildcard and _is_dtype(value, field)


def _decimal(value: Decimal, field: ContextField) -> Decimal:
    rounding = ROUND_HALF_EVEN if field.rounding == "half_to_even" else ROUND_HALF_UP
    return value.quantize(Decimal(1).scaleb(-field.scale), rounding=rounding)


def _atom(value: Any, field: ContextField) -> Any:
    if field.data_type is DataType.DECIMAL and isinstance(value, Decimal):
        return _decimal(value, field)
    return value


def _normal(value: Any, field: ContextField, is_set: bool) -> Any:
    if is_set:
        if any(_is_wildcard(item, field) for item in value):
            return ("wildcard",)
        return tuple(sorted({_atom(item, field) for item in value}, key=repr))
    return ("wildcard",) if _is_wildcard(value, field) else _atom(value, field)


def _strategy_allowed(dim: Dimension) -> bool:
    strategy, data_type = dim.match_strategy, dim.data_type
    if strategy in (MatchStrategy.OUTCOME, MatchStrategy.PRESENCE, MatchStrategy.TRUTHY):
        return True
    if strategy in _PATTERNS:
        return data_type is DataType.STR
    if strategy in _SET | {MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL}:
        return data_type is not DataType.FLOAT
    return strategy in {MatchStrategy.RANGE, MatchStrategy.MIN, MatchStrategy.MAX} and data_type in _ORDERED


def _schema_type_compatible(dtype: Any, field: ContextField, is_set: bool) -> bool:
    """Cheap schema admission for empty relations; values receive full checking."""
    family = dtype_family(dtype)
    if is_set:
        # List descriptors differ among supported backends but expose a stable
        # family name; values are still inspected below when rows exist.
        return "list" in str(family).lower() or "array" in str(family).lower()
    if field.data_type is DataType.DECIMAL:
        return isinstance(dtype, DecimalDtype)
    expected = {
        DataType.STR: MountainashDtype.STRING,
        DataType.BOOL: MountainashDtype.BOOL,
        DataType.INT: {MountainashDtype.I8, MountainashDtype.I16, MountainashDtype.I32, MountainashDtype.I64, MountainashDtype.U8, MountainashDtype.U16, MountainashDtype.U32, MountainashDtype.U64},
        DataType.DATE: MountainashDtype.DATE,
        DataType.DATETIME: MountainashDtype.TIMESTAMP,
    }.get(field.data_type)
    return family in expected if isinstance(expected, set) else family is expected


def _add_schema_findings(metadata: DimensionsMetadata, schema: dict[str, Any], errors: list[Finding]) -> None:
    if metadata.uuid_column is None:
        errors.append(Finding("missing_uuid_column", "error", "Metadata must declare uuid_column", ()))
    elif metadata.uuid_column not in schema:
        errors.append(Finding("missing_uuid_column", "error", f"Missing UUID column {metadata.uuid_column!r}", ()))
    if metadata.scope_column is not None and metadata.scope_column not in schema:
        errors.append(Finding("missing_scope_column", "error", f"Missing scope column {metadata.scope_column!r}", ()))
    for dim in metadata.dimensions:
        if not _strategy_allowed(dim):
            errors.append(Finding("unsupported_strategy_type", "error", f"{dim.dimension_name}: {dim.match_strategy.value} is incompatible with {dim.data_type.value}", ()))
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        columns = ((dim.range_min_field, False), (dim.range_max_field, False)) if dim.match_strategy is MatchStrategy.RANGE else ((dim.resolved_rule_field, dim.match_strategy in _SET),)
        for column, is_set in columns:
            if column not in schema:
                errors.append(Finding("missing_rule_column", "error", f"Missing rule column {column!r}", ()))
            elif not _schema_type_compatible(schema[column], dim.field, is_set):
                errors.append(Finding("incompatible_rule_dtype", "error", f"Rule column {column!r} is incompatible with {dim.data_type.value}", ()))
    for output in metadata.outputs:
        if output.contribution_column not in schema:
            errors.append(Finding("missing_contribution_column", "error", f"Missing contribution column {output.contribution_column!r}", ()))
    types: dict[str, set[DataType]] = {}
    scopes: dict[str, set[str]] = {}
    for output in metadata.outputs:
        types.setdefault(output.output_name, set()).add(output.data_type)
        scopes.setdefault(output.output_name, set()).add(output.scope or "default")
    for name, data_types in types.items():
        if len(data_types) > 1:
            errors.append(Finding("output_type_conflict", "error", f"Output {name!r} has incompatible types across scopes", tuple(sorted(scopes[name]))))


def _value_findings(row: dict[str, Any], uuid: Any, metadata: DimensionsMetadata, errors: list[Finding], warnings: list[Finding]) -> None:
    for dim in metadata.dimensions:
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        pairs = ((dim.range_min_field, False), (dim.range_max_field, False)) if dim.match_strategy is MatchStrategy.RANGE else ((dim.resolved_rule_field, dim.match_strategy in _SET),)
        for column, is_set in pairs:
            value = row.get(column)
            if value is None and not (dim.field.wildcard_declared and dim.field.wildcard is None):
                errors.append(Finding("null_rule_cell", "error", f"{column!r} is null without a null wildcard", _uuids([uuid])))
                continue
            if is_set:
                if not isinstance(value, list):
                    errors.append(Finding("incompatible_list_dtype", "error", f"{column!r} must be a list", _uuids([uuid])))
                    continue
                for item in value:
                    if item is None:
                        errors.append(Finding("null_list_element", "error", f"{column!r} contains null", _uuids([uuid])))
                    elif not _is_dtype(item, dim.field):
                        errors.append(Finding("incompatible_list_element", "error", f"{column!r} has incompatible element", _uuids([uuid])))
            elif not _is_wildcard(value, dim.field) and not _is_dtype(value, dim.field):
                errors.append(Finding("incompatible_rule_value", "error", f"{column!r} is incompatible with {dim.data_type.value}", _uuids([uuid])))
        value = row.get(dim.resolved_rule_field)
        if dim.match_strategy is MatchStrategy.TRUTHY and not _is_wildcard(value, dim.field) and value not in dim.field.truthy:
            errors.append(Finding("unmapped_truthy_value", "error", f"{dim.resolved_rule_field!r} is not mapped", _uuids([uuid])))
        if dim.match_strategy is MatchStrategy.PRESENCE and not _is_wildcard(value, dim.field) and value not in {"IS_NULL", "IS_NOT_NULL"}:
            errors.append(Finding("invalid_presence_value", "error", f"{dim.resolved_rule_field!r} must be IS_NULL or IS_NOT_NULL", _uuids([uuid])))
        if dim.match_strategy is MatchStrategy.OUTCOME and value not in {-1, 0, 1}:
            errors.append(Finding("invalid_outcome_value", "error", f"{dim.resolved_rule_field!r} must be -1, 0, or 1", _uuids([uuid])))
    for field_name in {dim.context_field for dim in metadata.dimensions}:
        presence = [dim for dim in metadata.dimensions if dim.context_field == field_name and dim.match_strategy is MatchStrategy.PRESENCE]
        concrete = [
            dim for dim in metadata.dimensions
            if dim.context_field == field_name
            and dim.match_strategy not in {MatchStrategy.PRESENCE, MatchStrategy.CONTEXT_REGEX}
            and not _is_wildcard(row.get(dim.resolved_rule_field), dim.field)
        ]
        if any(row.get(dim.resolved_rule_field) == "IS_NULL" for dim in presence) and concrete:
            warnings.append(Finding("unsatisfiable_rule", "warning", f"{field_name!r} is both null and concrete", _uuids([uuid])))
    for output in metadata.outputs:
        if (output.scope or "default") != _scope(row, metadata):
            continue
        value = row.get(output.contribution_column)
        field = ContextField(name=output.output_name, data_type=output.data_type, precision=output.precision, scale=output.scale, rounding=output.rounding, timezone=output.timezone)
        if value is None:
            errors.append(Finding("null_contribution", "error", f"{output.contribution_column!r} is null", _uuids([uuid])))
        elif not _is_dtype(value, field):
            errors.append(Finding("incompatible_contribution_dtype", "error", f"{output.contribution_column!r} has incompatible type", _uuids([uuid])))


def _signatures(rows: list[dict[str, Any]], metadata: DimensionsMetadata, errors: list[Finding]) -> None:
    by_conditions: dict[Any, list[tuple[Any, Any]]] = {}
    for row in rows:
        uuid = row.get(metadata.uuid_column) if metadata.uuid_column else None
        scope = _scope(row, metadata)
        conditions = []
        for dim in metadata.dimensions:
            if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
                conditions.append((dim.dimension_name, dim.match_strategy.value, dim.regex_pattern))
                continue
            if dim.match_strategy is MatchStrategy.RANGE:
                value = (_normal(row.get(dim.range_min_field), dim.field, False), _normal(row.get(dim.range_max_field), dim.field, False))
            else:
                value = _normal(row.get(dim.resolved_rule_field), dim.field, dim.match_strategy in _SET)
            conditions.append((dim.dimension_name, dim.match_strategy.value, value))
        outputs = tuple((output.output_name, row.get(output.contribution_column)) for output in metadata.outputs if (output.scope or "default") == scope)
        by_conditions.setdefault((scope, tuple(conditions)), []).append((uuid, outputs))
    for (scope, _), group in by_conditions.items():
        output_groups: dict[Any, list[Any]] = {}
        for uuid, output in group:
            output_groups.setdefault(output, []).append(uuid)
        for uuids in output_groups.values():
            if len(uuids) > 1:
                errors.append(Finding("duplicate_rule", "error", f"Duplicate normalized rule in scope {scope!r}", _uuids(uuids)))
        if len(output_groups) > 1:
            errors.append(Finding("conflicting_rule", "error", f"Conflicting normalized rules in scope {scope!r}", _uuids([uuid for uuid, _ in group])))


def prevalidate(rules: Any, metadata: DimensionsMetadata) -> ValidatedRules:
    """Capture ``rules`` once, aggregate all breaches, or return owned validation."""
    captured = rules.snapshot()
    copied_metadata = metadata.model_copy(deep=True)
    schema = captured.collect_schema(allow_execution=False)
    rows = captured.to_dicts()
    errors: list[Finding] = []
    warnings: list[Finding] = []
    _add_schema_findings(copied_metadata, schema, errors)
    scopes = _declared_scopes(copied_metadata)
    seen: dict[tuple[Any, Any], list[Any]] = {}
    for row in rows:
        uuid = row.get(copied_metadata.uuid_column) if copied_metadata.uuid_column else None
        scope = _scope(row, copied_metadata)
        if uuid is None:
            errors.append(Finding("missing_uuid", "error", "Rule UUID is absent or null", ()))
        if copied_metadata.scope_column is not None:
            if scope is None:
                errors.append(Finding("null_scope", "error", "Rule scope is null", _uuids([uuid])))
            elif scope not in scopes:
                errors.append(Finding("undeclared_scope", "error", f"Undeclared scope {scope!r}", _uuids([uuid])))
        seen.setdefault((scope, uuid), []).append(uuid)
        _value_findings(row, uuid, copied_metadata, errors, warnings)
    for (scope, uuid), values in seen.items():
        if uuid is not None and len(values) > 1:
            errors.append(Finding("duplicate_uuid", "error", f"Duplicate UUID in scope {scope!r}", _uuids(values)))
    _signatures(rows, copied_metadata, errors)
    breaches, found_warnings = _sorted_findings(errors), _sorted_findings(warnings)
    if breaches:
        raise DataContractError(breaches, found_warnings)
    return ValidatedRules(captured, copied_metadata, found_warnings)
