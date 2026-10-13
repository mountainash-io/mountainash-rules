"""Typed authoring normalization and state-only dimension expressions."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash import DecimalDtype, MountainashDtype
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.numeric import (
    decimal_operand,
    exact_literal,
    literal_dtype,
    reject_invalid,
)
from mountainash_rules.core.set_wildcard import set_state


_INTEGER_DTYPES = frozenset(
    {
        MountainashDtype.I8,
        MountainashDtype.I16,
        MountainashDtype.I32,
        MountainashDtype.I64,
        MountainashDtype.U8,
        MountainashDtype.U16,
        MountainashDtype.U32,
        MountainashDtype.U64,
    }
)
_FLOAT_DTYPES = frozenset({MountainashDtype.FP32, MountainashDtype.FP64})
_SCALAR_DTYPES = {
    DataType.STR: frozenset({MountainashDtype.STRING}),
    DataType.BOOL: frozenset({MountainashDtype.BOOL}),
    DataType.INT: _INTEGER_DTYPES,
    DataType.FLOAT: _FLOAT_DTYPES | _INTEGER_DTYPES,
    DataType.DATE: frozenset({MountainashDtype.DATE}),
    DataType.DATETIME: frozenset({MountainashDtype.TIMESTAMP}),
}


def same_scalar_type(left: t.Any, right: t.Any) -> bool:
    """Compare original scalar domains, allowing widths but not coercive equality."""
    return (
        left == right
        or (left in _INTEGER_DTYPES and right in _INTEGER_DTYPES)
        or (left in _FLOAT_DTYPES and right in _FLOAT_DTYPES)
        or (isinstance(left, DecimalDtype) and isinstance(right, DecimalDtype))
    )


def typed_marker_predicate(
    expr: BaseExpressionAPI,
    source_dtype: t.Any,
    markers: list[t.Any],
) -> BaseExpressionAPI:
    """Recognize declared markers in the original native scalar domain."""
    result = ma.lit(False)
    for marker in markers:
        if marker is None:
            result = result | expr.is_null()
        else:
            marker_dtype = literal_dtype(marker)
            if not same_scalar_type(source_dtype, marker_dtype):
                continue
            if isinstance(source_dtype, DecimalDtype) and isinstance(
                marker_dtype, DecimalDtype
            ):
                if (
                    marker_dtype.scale > source_dtype.scale
                    or marker_dtype.precision - marker_dtype.scale
                    > source_dtype.precision - source_dtype.scale
                ):
                    # No original value in this column can denote the marker.
                    # Binding it in the target interpretation would round it.
                    continue
                literal = ma.lit(marker, dtype=source_dtype)
            else:
                literal = exact_literal(marker)
            result = result | expr.eq(literal).fill_null(False)
    return result


def rule_binding_names(dim: Dimension, endpoint: str | None = None) -> tuple[str, str]:
    name = dim.dimension_name
    if endpoint is not None:
        return f"__rule_{endpoint}_value_{name}", f"__rule_{endpoint}_unbounded_{name}"
    return f"__rule_value_{name}", f"__rule_wildcard_{name}"


def rule_flag_state(
    rel: t.Any, field: str | None, marker: BaseExpressionAPI
) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
    """Mapped flags are authoritative only when their authoring is consistent."""
    if field is None:
        return marker, ma.lit(False)
    if field not in rel.columns or rel.schema[field] is not MountainashDtype.BOOL:
        raise ValueError(f"Rule flag {field!r} must be an existing Boolean column")
    flag = ma.col(field)
    return flag.fill_null(False), flag.is_null() | ((~flag).fill_null(False) & marker)


def scalar_operand(
    dim: Dimension,
    raw: BaseExpressionAPI,
    source_dtype: t.Any,
    inactive: BaseExpressionAPI,
) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
    """Admit the original scalar domain and provide an unused typed payload."""
    if dim.data_type is DataType.DECIMAL:
        assert dim.numeric_dtype is not None and dim.rounding is not None
        operand = decimal_operand(
            raw, source_dtype, dim.numeric_dtype, dim.rounding, inactive=inactive
        )
        return operand.value, operand.invalid
    if source_dtype not in _SCALAR_DTYPES[dim.data_type]:
        # Never ask the backend to compare or cast an unused incompatible value.
        # Null here is physical storage only; explicit state controls semantics.
        return ma.lit(None).cast(dim.data_type.python_type), ~inactive
    invalid = ~inactive & raw.is_null()
    if dim.data_type is DataType.INT and source_dtype is MountainashDtype.U64:
        invalid = invalid | (~inactive & raw.gt((1 << 63) - 1))
    elif dim.data_type is DataType.FLOAT and source_dtype in _FLOAT_DTYPES:
        invalid = invalid | (~inactive & ~raw.is_finite())
    payload = ma.when(inactive | invalid).then(None).otherwise(raw)
    if dim.data_type in (DataType.INT, DataType.FLOAT):
        payload = payload.cast(dim.data_type.python_type)
    return payload, invalid.fill_null(False)


def rule_binding_expressions(
    rel: t.Any,
    dimensions: t.Sequence[Dimension],
) -> tuple[list[BaseExpressionAPI], dict[str, BaseExpressionAPI]]:
    """Return native bindings/admission so callers can retain invalid source rows."""
    columns = []
    invalid = {}
    schema = rel.schema
    for dim in dimensions:
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        operands: list[tuple[str | None, str | None, str | None]]
        if dim.match_strategy is MatchStrategy.RANGE:
            operands = [
                ("min", dim.range_min_field, dim.range_min_unbounded_field),
                ("max", dim.range_max_field, dim.range_max_unbounded_field),
            ]
        else:
            operands = [(None, dim.resolved_rule_field, dim.rule_wildcard_field)]
        for endpoint, field, flag_field in operands:
            if field is None or field not in schema:
                raise ValueError(f"Missing rule operand {field!r}")
            raw = ma.col(field)
            source_dtype = schema[field]
            is_set = dim.match_strategy in (
                MatchStrategy.SET_MEMBERSHIP,
                MatchStrategy.SET_EXCLUSION,
            )
            if is_set:
                marker, malformed, payload = set_state(rel, dim, field)
            else:
                marker = typed_marker_predicate(raw, source_dtype, dim.wildcard_values)
                malformed = raw.is_null()
            wildcard, conflict = rule_flag_state(rel, flag_field, marker)
            invalid[f"{dim.dimension_name}:{field}:authoring"] = conflict | (
                ~wildcard & malformed
            )
            if not is_set:
                payload, failure = scalar_operand(dim, raw, source_dtype, wildcard)
                invalid[f"{dim.dimension_name}:{field}"] = failure
                if dim.match_strategy is MatchStrategy.REGEX:
                    payload = ma.when(wildcard).then("").otherwise(payload)
            value_name, state_name = rule_binding_names(dim, endpoint)
            columns.extend([payload.alias(value_name), wildcard.alias(state_name)])
    return columns, invalid


def normalize_rule_relation(rel: t.Any, dimensions: t.Sequence[Dimension]) -> t.Any:
    """Filter admission around the shared binding expressions."""
    columns, invalid = rule_binding_expressions(rel, dimensions)
    reject_invalid(rel, invalid)
    return rel.with_columns(*columns) if columns else rel


class DimensionCompiler:
    """Compile normalized state/payload bindings, independent of authoring form."""

    def compile_dimensions(
        self, metadata: DimensionsMetadata
    ) -> dict[str, BaseExpressionAPI]:
        return {
            dim.dimension_name: self.compile_dimension(dim)
            for dim in metadata.dimensions
        }

    @staticmethod
    def _ternary(
        dim: Dimension, matched: BaseExpressionAPI, endpoint: str | None = None
    ) -> BaseExpressionAPI:
        _, state_name = rule_binding_names(dim, endpoint)
        missing = ~ma.col(f"__ctx_present_{dim.dimension_name}")
        if dim.match_strategy is MatchStrategy.EXACT_KEY:
            return (
                ma.when(ma.col(state_name))
                .then(0)
                .when(missing)
                .then(-1)
                .when(matched)
                .then(1)
                .otherwise(-1)
            )
        return (
            ma.when(ma.col(state_name) | missing)
            .then(0)
            .when(matched)
            .then(1)
            .otherwise(-1)
        )

    def compile_dimension(self, dim: Dimension) -> BaseExpressionAPI:
        if dim.data_type is DataType.BOOL and dim.match_strategy in (
            MatchStrategy.SET_MEMBERSHIP,
            MatchStrategy.SET_EXCLUSION,
        ):
            raise ValueError(
                "Boolean set strategies are not supported by the ternary filter"
            )
        context_name = f"__ctx_value_{dim.dimension_name}"
        context = ma.col(context_name)
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            return (
                ma.when(~ma.col(f"__ctx_present_{dim.dimension_name}"))
                .then(-1)
                .when(context.str.regex_contains(dim.regex_pattern))
                .then(1)
                .otherwise(-1)
            )
        if dim.match_strategy is MatchStrategy.RANGE:
            lower = ma.col(rule_binding_names(dim, "min")[0])
            upper = ma.col(rule_binding_names(dim, "max")[0])
            lo = self._ternary(
                dim,
                lower.le(context) if dim.range_min_inclusive else lower.lt(context),
                "min",
            )
            hi = self._ternary(
                dim,
                upper.ge(context) if dim.range_max_inclusive else upper.gt(context),
                "max",
            )
            return ma.when(lo.le(hi)).then(lo).otherwise(hi)
        value_name, _ = rule_binding_names(dim)
        rule = ma.col(value_name)
        match dim.match_strategy:
            case MatchStrategy.EXACT | MatchStrategy.EXACT_KEY:
                matched = rule.eq(context)
            case MatchStrategy.NOT_EQUAL:
                matched = rule.ne(context)
            case MatchStrategy.GREATER_THAN:
                matched = context.gt(rule)
            case MatchStrategy.LESS_THAN:
                matched = context.lt(rule)
            case MatchStrategy.PREFIX:
                matched = context.str.starts_with(rule)
            case MatchStrategy.SUFFIX:
                matched = context.str.ends_with(rule)
            case MatchStrategy.CONTAINS:
                matched = context.str.contains(rule)
            case MatchStrategy.REGEX:
                matched = context.str.regex_contains(rule)
            case MatchStrategy.SET_MEMBERSHIP:
                matched = rule.list.contains(context)
            case MatchStrategy.SET_EXCLUSION:
                matched = ~rule.list.contains(context)
            case _:
                raise ValueError(f"Unknown match strategy: {dim.match_strategy}")
        return self._ternary(dim, matched)
