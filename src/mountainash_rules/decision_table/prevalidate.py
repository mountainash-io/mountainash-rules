"""Capture and validate authored DecisionTable rules before construction."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import mountainash.expressions as ma
from mountainash import DecimalDtype, MountainashDtype
from mountainash.core.dtypes import ListDtype, TimestampDtype, dtype_family

from mountainash_rules.core.compiler import _SCALAR_DTYPES, typed_marker_predicate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.core.numeric import exact_literal
from mountainash_rules.core.set_wildcard import canonicalize_set_expr
from mountainash_rules.decision_table.fields import BOUND_SCOPE, BOUND_UUID, SOURCE_UUID, family_for
from mountainash_rules.decision_table.strategies import strategy_for, unsatisfiable_rules


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


def _declared_scopes(metadata: DimensionsMetadata) -> set[str]:
    scopes = {output.scope or "default" for output in metadata.outputs}
    return scopes or {"default"}


def _strategy_allowed(dim: Dimension) -> bool:
    strategy, data_type = dim.match_strategy, dim.data_type
    if strategy in (MatchStrategy.OUTCOME, MatchStrategy.PRESENCE, MatchStrategy.TRUTHY):
        return True
    if strategy in _PATTERNS:
        return data_type is DataType.STR
    if strategy in _SET | {MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL}:
        return data_type is not DataType.FLOAT
    return strategy in {MatchStrategy.RANGE, MatchStrategy.MIN, MatchStrategy.MAX} and data_type in _ORDERED


def _timezone_matches(dtype: Any, timezone: str | None) -> bool:
    """A timestamp descriptor whose awareness is known must agree with the declared interpretation."""
    aware = dtype.timezone_aware
    return not isinstance(aware, bool) or aware == (timezone == "utc")


def _scalar_dtype_compatible(dtype: Any, data_type: DataType, timezone: str | None) -> bool:
    """Whether a scalar descriptor can carry ``data_type`` (and, for DATETIME, its interpretation)."""
    if data_type is DataType.DECIMAL:
        return isinstance(dtype, DecimalDtype)
    expected = {
        DataType.STR: MountainashDtype.STRING,
        DataType.BOOL: MountainashDtype.BOOL,
        DataType.INT: {MountainashDtype.I8, MountainashDtype.I16, MountainashDtype.I32, MountainashDtype.I64, MountainashDtype.U8, MountainashDtype.U16, MountainashDtype.U32, MountainashDtype.U64},
        DataType.DATE: MountainashDtype.DATE,
        DataType.DATETIME: MountainashDtype.TIMESTAMP,
    }.get(data_type)
    family = dtype_family(dtype)
    if not (family in expected if isinstance(expected, set) else family is expected):
        return False
    return data_type is not DataType.DATETIME or not isinstance(dtype, TimestampDtype) or _timezone_matches(dtype, timezone)


def _schema_type_compatible(dtype: Any, field: ContextField, is_set: bool) -> bool:
    """Cheap schema admission for empty relations; values receive full checking."""
    family = dtype_family(dtype)
    if field.wildcard_declared and field.wildcard is None and "null" in str(family).lower():
        return True
    if is_set:
        # List descriptors differ among supported backends but expose a stable
        # family name; values are still inspected below when rows exist.
        return "list" in str(family).lower() or "array" in str(family).lower()
    return _scalar_dtype_compatible(dtype, field.data_type, field.timezone)


def _list_element_schema_compatible(dtype: Any, field: ContextField) -> bool:
    """Check a concrete list element descriptor without materializing rows."""
    if not isinstance(dtype, ListDtype):
        return False
    element = dtype.element
    if field.data_type is DataType.DATETIME and isinstance(element, TimestampDtype):
        return _timezone_matches(element, field.timezone)
    return (
        element in _SCALAR_DTYPES.get(field.data_type, frozenset())
        or (field.data_type is DataType.DECIMAL and isinstance(element, DecimalDtype))
    )


def _dimension_findings(dim: Dimension, schema: dict[str, Any]) -> list[Finding]:
    """Schema-level breaches of one dimension's strategy, rule columns and rule dtypes."""
    errors: list[Finding] = []
    if not _strategy_allowed(dim):
        errors.append(Finding("unsupported_strategy_type", "error", f"{dim.dimension_name}: {dim.match_strategy.value} is incompatible with {dim.data_type.value}", ()))
    if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
        return errors
    columns = ((dim.range_min_field, False), (dim.range_max_field, False)) if dim.match_strategy is MatchStrategy.RANGE else ((dim.resolved_rule_field, dim.match_strategy in _SET),)
    for column, is_set in columns:
        if column not in schema:
            errors.append(Finding("missing_rule_column", "error", f"Missing rule column {column!r}", ()))
        elif is_set and not _schema_type_compatible(schema[column], dim.field, True):
            errors.append(Finding("incompatible_list_dtype", "error", f"{column!r} must be a list", ()))
        elif is_set and not _list_element_schema_compatible(schema[column], dim.field):
            errors.append(Finding("incompatible_list_element", "error", f"{column!r} has incompatible element type", ()))
        elif dim.match_strategy not in {MatchStrategy.PRESENCE, MatchStrategy.OUTCOME} and not _schema_type_compatible(schema[column], dim.field, is_set):
            errors.append(Finding("incompatible_rule_dtype", "error", f"Rule column {column!r} is incompatible with {dim.data_type.value}", ()))
    return errors


def _add_schema_findings(metadata: DimensionsMetadata, schema: dict[str, Any], errors: list[Finding]) -> None:
    if metadata.uuid_column is None:
        errors.append(Finding("missing_uuid_column", "error", "Metadata must declare uuid_column", ()))
    elif metadata.uuid_column not in schema:
        errors.append(Finding("missing_uuid_column", "error", f"Missing UUID column {metadata.uuid_column!r}", ()))
    if metadata.scope_column is not None and metadata.scope_column not in schema:
        errors.append(Finding("missing_scope_column", "error", f"Missing scope column {metadata.scope_column!r}", ()))
    for dim in metadata.dimensions:
        errors.extend(_dimension_findings(dim, schema))
    for output in metadata.outputs:
        if output.contribution_column not in schema:
            errors.append(Finding("missing_contribution_column", "error", f"Missing contribution column {output.contribution_column!r}", ()))
        elif not _scalar_dtype_compatible(schema[output.contribution_column], output.data_type, output.timezone):
            errors.append(Finding(
                "incompatible_contribution_dtype",
                "error",
                f"Contribution column {output.contribution_column!r} is incompatible with {output.data_type.value}",
                (),
            ))
    types: dict[str, set[DataType]] = {}
    scopes: dict[str, set[str]] = {}
    for output in metadata.outputs:
        types.setdefault(output.output_name, set()).add(output.data_type)
        scopes.setdefault(output.output_name, set()).add(output.scope or "default")
    for name, data_types in types.items():
        if len(data_types) > 1:
            errors.append(Finding("output_type_conflict", "error", f"Output {name!r} has incompatible types across scopes", tuple(sorted(scopes[name]))))


def _signature_placeholder(
    data_type: DataType,
    decimal_dtype: DecimalDtype | None = None,
    timezone: str | None = None,
) -> Any:
    values = {
        DataType.STR: "",
        DataType.BOOL: False,
        DataType.INT: 0,
        DataType.FLOAT: 0.0,
        DataType.DATE: dt.date(1970, 1, 1),
        DataType.DATETIME: dt.datetime(
            1970,
            1,
            1,
            tzinfo=dt.timezone.utc if timezone == "utc" else None,
        ),
        DataType.DECIMAL: Decimal(0),
    }
    return ma.lit(values[data_type], dtype=decimal_dtype) if decimal_dtype else ma.lit(values[data_type])


def _signature_scalar_payload(
    dim: Dimension,
    field: str,
    schema: dict[str, Any],
) -> tuple[Any, Any, Any]:
    raw = ma.col(field)
    wildcard = typed_marker_predicate(
        raw,
        schema[field],
        [dim.field.wildcard] if dim.field.wildcard_declared else [],
    )
    missing = raw.is_null() & ~wildcard
    if dim.match_strategy is MatchStrategy.PRESENCE:
        # A PRESENCE rule column holds text whatever the field's data type.
        return wildcard, missing, ma.when(wildcard | missing).then(ma.lit("")).otherwise(raw)
    if dim.match_strategy is MatchStrategy.OUTCOME:
        # An OUTCOME rule column holds -1/0/1 whatever the field's data type.
        return wildcard, missing, ma.when(wildcard | missing).then(ma.lit(0)).otherwise(raw)
    payload = (
        raw.cast(dim.decimal_dtype, rounding=dim.rounding)
        if dim.data_type is DataType.DECIMAL
        else raw
    )
    return wildcard, missing, ma.when(wildcard | missing).then(
        _signature_placeholder(dim.data_type, dim.decimal_dtype, dim.field.timezone)
    ).otherwise(payload)


def _signature_set_payload(dim: Dimension, field: str) -> tuple[Any, Any]:
    raw = ma.col(field)
    marker = dim.field.wildcard
    wildcard = (
        raw.is_null()
        if dim.field.wildcard_declared and marker is None
        else raw.list.contains(exact_literal(marker)).fill_null(False)
        if dim.field.wildcard_declared
        else ma.lit(False)
    )
    payload = ma.when(wildcard).then(
        raw.list.filter(ma.lit(False))
    ).otherwise(raw)
    if dim.data_type is DataType.DECIMAL:
        payload = payload.list.agg(
            ma.element().cast(dim.decimal_dtype, rounding=dim.rounding)
        )
    return wildcard, canonicalize_set_expr(payload)


def _signature_output_payload(output: Any) -> Any:
    raw = ma.col(output.contribution_column)
    if output.data_type is DataType.DECIMAL:
        return raw.cast(output.decimal_dtype, rounding=output.rounding)
    return raw


def _signature_columns_available(schema: dict[str, Any], metadata: DimensionsMetadata) -> bool:
    if metadata.uuid_column is None or metadata.uuid_column not in schema:
        return False
    if metadata.scope_column is not None and metadata.scope_column not in schema:
        return False
    output_types: dict[str, set[DataType]] = {}
    for output in metadata.outputs:
        output_types.setdefault(output.output_name, set()).add(output.data_type)
        if (
            output.contribution_column not in schema
            or not _scalar_dtype_compatible(
                schema[output.contribution_column],
                output.data_type,
                output.timezone,
            )
        ):
            return False
    if any(len(types) > 1 for types in output_types.values()):
        return False
    for dim in metadata.dimensions:
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        fields = (
            (dim.range_min_field, dim.range_max_field)
            if dim.match_strategy is MatchStrategy.RANGE
            else (dim.resolved_rule_field,)
        )
        if any(field is None or field not in schema for field in fields):
            return False
        if dim.match_strategy in _SET and not _list_element_schema_compatible(
            schema[dim.resolved_rule_field],
            dim.field,
        ):
            return False
        if (
            dim.match_strategy not in _SET | {MatchStrategy.PRESENCE, MatchStrategy.OUTCOME}
            and any(
                not _schema_type_compatible(schema[field], dim.field, False)
                and "null" not in str(dtype_family(schema[field])).lower()
                for field in fields
            )
        ):
            return False
    return True


def _signature_findings(
    evidence: Any,
    keys: list[str],
    uuid_column: str,
    kind: str,
    detail: str,
    errors: list[Finding],
) -> None:
    def freeze(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((key, freeze(item)) for key, item in value.items()))
        return value

    grouped: dict[tuple[Any, ...], list[Any]] = {}
    for row in evidence.select(*keys, uuid_column).to_dicts():
        grouped.setdefault(tuple(freeze(row[key]) for key in keys), []).append(row[uuid_column])
    for group, uuids in grouped.items():
        errors.append(Finding(kind, "error", detail.format(scope=group[0]), _uuids(uuids)))


def _signatures(relation: Any, metadata: DimensionsMetadata, errors: list[Finding]) -> None:
    """Diagnose duplicate and conflicting signatures without materializing authored rows."""
    schema = relation.collect_schema(allow_execution=False)
    if not _signature_columns_available(schema, metadata):
        return

    scope_name = "__pv_signature_scope"
    uuid_name = "__pv_signature_uuid"
    scope = ma.lit("default") if metadata.scope_column is None else ma.col(metadata.scope_column)
    columns = [scope.alias(scope_name), ma.col(metadata.uuid_column).alias(uuid_name)]
    condition_keys = [scope_name]
    for index, dim in enumerate(metadata.dimensions):
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            name = f"__pv_condition_{index}"
            columns.append(ma.lit(dim.regex_pattern).alias(name))
            condition_keys.append(name)
            continue
        fields = (
            (dim.range_min_field, dim.range_max_field)
            if dim.match_strategy is MatchStrategy.RANGE
            else (dim.resolved_rule_field,)
        )
        for endpoint, field in enumerate(fields):
            if dim.match_strategy in _SET:
                wildcard, payload = _signature_set_payload(dim, field)
                missing = ma.lit(False)
            else:
                wildcard, missing, payload = _signature_scalar_payload(dim, field, schema)
            wildcard_name = f"__pv_condition_{index}_{endpoint}_wildcard"
            missing_name = f"__pv_condition_{index}_{endpoint}_missing"
            payload_name = f"__pv_condition_{index}_{endpoint}"
            columns.extend(
                [
                    wildcard.alias(wildcard_name),
                    missing.alias(missing_name),
                    payload.alias(payload_name),
                ]
            )
            condition_keys.extend([wildcard_name, missing_name, payload_name])

    output_keys = []
    for index, output in enumerate(metadata.outputs):
        name = f"__pv_output_{index}"
        output_keys.append(name)
        active = scope.eq(output.scope or "default").fill_null(False)
        columns.append(
            ma.when(active)
            .then(_signature_output_payload(output))
            .otherwise(
                _signature_placeholder(
                    output.data_type,
                    output.decimal_dtype,
                    output.timezone,
                )
            )
            .alias(name)
        )
    signatures = relation.select(*columns)
    duplicate_keys = [*condition_keys, *output_keys]
    duplicate_groups = (
        signatures.group_by(*duplicate_keys)
        .agg(ma.count_records().alias("__pv_count"))
        .filter(ma.col("__pv_count").gt(1))
    )
    duplicate_rows = signatures.join(
        duplicate_groups.select(*duplicate_keys),
        on=duplicate_keys,
        how="inner",
    )
    _signature_findings(
        duplicate_rows,
        duplicate_keys,
        uuid_name,
        "duplicate_rule",
        "Duplicate normalized rule in scope {scope!r}",
        errors,
    )
    if not output_keys:
        return

    output_groups = signatures.group_by(*duplicate_keys).agg(
        ma.count_records().alias("__pv_count")
    )
    conflict_groups = (
        output_groups.group_by(*condition_keys)
        .agg(ma.count_records().alias("__pv_output_count"))
        .filter(ma.col("__pv_output_count").gt(1))
    )
    conflict_rows = signatures.join(
        conflict_groups.select(*condition_keys),
        on=condition_keys,
        how="inner",
    )
    _signature_findings(
        conflict_rows,
        condition_keys,
        uuid_name,
        "conflicting_rule",
        "Conflicting normalized rules in scope {scope!r}",
        errors,
    )

def _relation_finding(
    relation: Any,
    predicate: Any,
    kind: str,
    detail: str,
    uuid_column: str | None,
) -> Finding | None:
    """Materialize only UUID evidence for one backend-native invalidity predicate."""
    selected = relation.filter(predicate)
    if selected.count_rows() == 0:
        return None
    if uuid_column is None or uuid_column not in selected.columns:
        return Finding(kind, "error", detail, ())
    return Finding(
        kind,
        "error",
        detail,
        _uuids([row[uuid_column] for row in selected.select(uuid_column).to_dicts()]),
    )


def _relation_value_findings(
    relation: Any,
    metadata: DimensionsMetadata,
    errors: list[Finding],
) -> None:
    """Extract scalar invalidity through relation predicates, never source rows."""
    uuid_column = metadata.uuid_column
    if uuid_column is not None and uuid_column in relation.columns:
        finding = _relation_finding(
            relation, ma.col(uuid_column).is_null(), "missing_uuid", "Rule UUID is absent or null", uuid_column
        )
        if finding:
            errors.append(finding)
    if metadata.scope_column is not None and metadata.scope_column in relation.columns:
        scope = ma.col(metadata.scope_column)
        for kind, predicate, detail in (
            ("null_scope", scope.is_null(), "Rule scope is null"),
            ("undeclared_scope", ~scope.is_in(list(_declared_scopes(metadata))), "Rule scope is undeclared"),
        ):
            finding = _relation_finding(relation, predicate.fill_null(False), kind, detail, uuid_column)
            if finding:
                errors.append(finding)
    for output in metadata.outputs:
        column = output.contribution_column
        if column not in relation.columns:
            continue
        scope = output.scope or "default"
        predicate = ma.col(column).is_null()
        if metadata.scope_column is not None and metadata.scope_column in relation.columns:
            predicate = predicate & ma.col(metadata.scope_column).eq(scope)
        finding = _relation_finding(
            relation,
            predicate.fill_null(False),
            "null_contribution",
            f"{column!r} is null",
            uuid_column,
        )
        if finding:
            errors.append(finding)
    for dim in metadata.dimensions:
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        fields = (dim.range_min_field, dim.range_max_field) if dim.match_strategy is MatchStrategy.RANGE else (dim.resolved_rule_field,)
        for field in fields:
            if field not in relation.columns:
                continue
            if not (dim.field.wildcard_declared and dim.field.wildcard is None):
                finding = _relation_finding(relation, ma.col(field).is_null(), "null_rule_cell", f"{field!r} is null without a null wildcard", uuid_column)
                if finding:
                    errors.append(finding)
        field = dim.resolved_rule_field
        if field not in relation.columns:
            continue
        if dim.match_strategy in _SET and _schema_type_compatible(
            relation.collect_schema(allow_execution=False)[field], dim.field, True
        ):
            ordered = ma.col(field).list.sort()
            null_element = ma.col(field).list.len().gt(0) & (
                ordered.list.first().is_null() | ordered.list.last().is_null()
            )
            finding = _relation_finding(relation, null_element.fill_null(False), "null_list_element", f"{field!r} contains null", uuid_column)
            if finding:
                errors.append(finding)
        if dim.match_strategy is MatchStrategy.PRESENCE:
            value = ma.col(field)
            markers = [dim.field.wildcard] if dim.field.wildcard_declared else []
            declared_wildcard = typed_marker_predicate(
                value, relation.collect_schema(allow_execution=False)[field], markers
            )
            invalid = value.ne("IS_NULL") & value.ne("IS_NOT_NULL") & ~declared_wildcard
            finding = _relation_finding(relation, invalid.fill_null(False), "invalid_presence_value", f"{field!r} must be IS_NULL or IS_NOT_NULL", uuid_column)
            if finding:
                errors.append(finding)
        elif dim.match_strategy is MatchStrategy.OUTCOME:
            value = ma.col(field)
            invalid = value.ne(-1) & value.ne(0) & value.ne(1)
            finding = _relation_finding(relation, invalid.fill_null(False), "invalid_outcome_value", f"{field!r} must be -1, 0, or 1", uuid_column)
            if finding:
                errors.append(finding)
        if dim.match_strategy is MatchStrategy.TRUTHY:
            dtype = relation.collect_schema(allow_execution=False)[field]
            if _schema_type_compatible(dtype, dim.field, False):
                cell = strategy_for(dim.match_strategy).cells(family_for(dim), relation, dim)[0]
                mapped = [key for key in dim.field.truthy if key is not None]
                unmapped = ~cell.wildcard & cell.value.is_not_null()
                if mapped:
                    unmapped = unmapped & ~cell.value.is_in(mapped)
                finding = _relation_finding(relation, unmapped.fill_null(False), "unmapped_truthy_value", f"{field!r} is not mapped", uuid_column)
                if finding:
                    errors.append(finding)


_CELL_BREACHES = frozenset({
    "null_rule_cell", "unmapped_truthy_value", "invalid_presence_value", "invalid_outcome_value",
    "null_list_element",
})


def _unsatisfiable_findings(
    relation: Any, metadata: DimensionsMetadata, dimensions: list[Dimension], blocked: set[Any]
) -> list[Finding]:
    """Warnings for rules whose conditions cannot all hold, from the shared family coverage.

    Rules with a malformed rule cell cannot be bound; they are reported as breaches instead.
    """
    uuid_column = metadata.uuid_column
    scope = ma.lit("default") if metadata.scope_column is None else ma.col(metadata.scope_column)
    bound = relation.filter(ma.col(uuid_column).is_not_null()).with_columns(
        scope.alias(BOUND_SCOPE), ma.col(uuid_column).alias(BOUND_UUID)
    )
    findings: list[Finding] = []
    for field_name, reason, rules in unsatisfiable_rules(bound, dimensions):
        uuids = _uuids([row[SOURCE_UUID] for row in rules.select(SOURCE_UUID).to_dicts()])
        uuids = tuple(uuid for uuid in uuids if uuid not in blocked)
        if uuids:
            findings.append(Finding("unsatisfiable_rule", "warning", f"{field_name!r}: {reason}", uuids))
    return findings


def prevalidate(rules: Any, metadata: DimensionsMetadata) -> ValidatedRules:
    """Capture ``rules`` once, aggregate all breaches, or return owned validation."""
    captured = rules.snapshot()
    copied_metadata = metadata.model_copy(deep=True)
    schema = captured.collect_schema(allow_execution=False)
    errors: list[Finding] = []
    _add_schema_findings(copied_metadata, schema, errors)
    _relation_value_findings(captured, copied_metadata, errors)
    # Scope-dependent checks need the declared scope column; a missing one is already a finding.
    identified = copied_metadata.uuid_column in schema and (
        copied_metadata.scope_column is None or copied_metadata.scope_column in schema
    )
    if identified:
        scoped = (
            captured.with_columns(ma.lit("default").alias("__pv_scope"))
            if copied_metadata.scope_column is None
            else captured.with_columns(ma.col(copied_metadata.scope_column).alias("__pv_scope"))
        )
        duplicate_ids = scoped.group_by("__pv_scope", copied_metadata.uuid_column).agg(
            ma.count_records().alias("__pv_n")
        ).filter(
            ma.col(copied_metadata.uuid_column).is_not_null() & ma.col("__pv_n").gt(1)
        )
        for row in duplicate_ids.select("__pv_scope", copied_metadata.uuid_column).to_dicts():
            errors.append(Finding(
                "duplicate_uuid", "error",
                f"Duplicate UUID in scope {row['__pv_scope']!r}",
                (row[copied_metadata.uuid_column],),
            ))
    _signatures(captured, copied_metadata, errors)
    warnings: list[Finding] = []
    if identified:
        # A warning needs only its own condition columns, so dimensions with schema breaches are
        # skipped while the others still report.
        sound = [dim for dim in copied_metadata.dimensions if not _dimension_findings(dim, schema)]
        blocked = {uuid for finding in errors if finding.kind in _CELL_BREACHES for uuid in finding.rule_uuids}
        warnings = _unsatisfiable_findings(captured, copied_metadata, sound, blocked)
    breaches, found_warnings = _sorted_findings(errors), _sorted_findings(warnings)
    if breaches:
        raise DataContractError(breaches, found_warnings)
    return ValidatedRules(captured, copied_metadata, found_warnings)
