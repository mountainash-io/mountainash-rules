"""Native numeric, marker and set mechanics shared by typed-value consumers."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext

import mountainash.expressions as ma
from mountainash import DecimalDtype, MountainashDtype, ValueKind, value_kind
from mountainash.core.dtypes import dtype_family
from mountainash.expressions import BaseExpressionAPI


_INTEGER_TYPES = frozenset(
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
_FLOAT_TYPES = frozenset({MountainashDtype.FP32, MountainashDtype.FP64})
_DECIMAL_TOKEN = r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$"


@dataclass(frozen=True, slots=True)
class NumericOperand:
    """Typed payload and independent admission status; invalid payload is unused."""

    value: BaseExpressionAPI
    invalid: BaseExpressionAPI


def _decimal_operation_precision(*values: Decimal) -> int:
    """Precision sufficient to align finite Decimal operands without rounding."""
    exponents = [t.cast(int, value.as_tuple().exponent) for value in values]
    lowest_exponent = min(exponents)
    highest_digit = max(
        exponent + len(value.as_tuple().digits)
        for value, exponent in zip(values, exponents, strict=True)
    )
    return max(1, highest_digit - lowest_exponent + 1)


def decimal_add_exact(left: Decimal, right: Decimal) -> Decimal:
    """Add finite Decimals exactly, independent of the ambient context."""
    with localcontext() as context:
        context.prec = _decimal_operation_precision(left, right)
        return left + right


def decimal_subtract_exact(left: Decimal, right: Decimal) -> Decimal:
    """Subtract finite Decimals exactly, independent of the ambient context."""
    with localcontext() as context:
        context.prec = _decimal_operation_precision(left, right)
        return left - right


def literal_dtype(value: t.Any) -> MountainashDtype | DecimalDtype | None:
    """Classify the original literal without coercion or premature rounding."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal literals must be finite")
        _, digits, exponent = value.as_tuple()
        exponent = t.cast(int, exponent)
        if not any(digits):
            return DecimalDtype(1, 0)
        # Insignificant trailing zeroes need no native storage; no decimal arithmetic
        # or ambient Decimal context participates in source binding.
        while len(digits) > 1 and digits[-1] == 0:
            digits = digits[:-1]
            exponent += 1
        scale = max(0, -exponent)
        precision = max(len(digits) + max(0, exponent), scale, 1)
        if precision > 38:
            raise ValueError(
                "Decimal literal requires more than 38 native digits; use plain decimal text"
            )
        return DecimalDtype(precision, scale)
    if value_kind(value) is ValueKind.INTEGER:
        return MountainashDtype.I64
    return {
        bool: MountainashDtype.BOOL,
        float: MountainashDtype.FP64,
        str: MountainashDtype.STRING,
        date: MountainashDtype.DATE,
        datetime: MountainashDtype.TIMESTAMP,
        list: MountainashDtype.LIST,
    }.get(type(value))


def exact_literal(value: t.Any) -> BaseExpressionAPI:
    """Bind Decimal in its original exact native source type before target casting."""
    dtype = literal_dtype(value)
    if dtype is MountainashDtype.I64:
        value = int(value)
    if isinstance(dtype, DecimalDtype) or dtype is MountainashDtype.DATE:
        return ma.lit(value, dtype=dtype)
    if dtype is MountainashDtype.TIMESTAMP and value.tzinfo is None:
        return ma.lit(value, dtype=dtype)
    return ma.lit(value)


def same_scalar_type(left: t.Any, right: t.Any) -> bool:
    """Compare original scalar domains, allowing widths but not coercive equality."""
    return (
        left == right
        or (left in _INTEGER_TYPES and right in _INTEGER_TYPES)
        or (left in _FLOAT_TYPES and right in _FLOAT_TYPES)
        or (isinstance(left, DecimalDtype) and isinstance(right, DecimalDtype))
        # A column reports its timestamp parameters; a Python literal reports only the family.
        or (dtype_family(left) is MountainashDtype.TIMESTAMP and dtype_family(right) is MountainashDtype.TIMESTAMP)
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
            if isinstance(source_dtype, DecimalDtype) and isinstance(marker_dtype, DecimalDtype):
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


def canonicalize_set_expr(col: BaseExpressionAPI) -> BaseExpressionAPI:
    """Sort and deduplicate concrete sets without synthesizing a wildcard."""
    return col.list.unique().list.sort()



def _text_overflow(expr: BaseExpressionAPI, dtype: DecimalDtype) -> BaseExpressionAPI:
    """Compare magnitude lexically against the first value rounding out of range."""
    magnitude = expr.str.regex_replace(r"^[+-]?0*", "")
    integral = magnitude.str.regex_replace(r"\..*$", "")
    fraction = magnitude.str.regex_replace(r"^[^.]*\.?", "")
    width = dtype.precision - dtype.scale
    # The maximum coefficient ends in 9 (odd): both supported tie policies carry
    # at max + half a quantum. This is declaration arithmetic, never row scaling.
    integer_limit = "9" * width
    fraction_limit = "9" * dtype.scale + "5"
    return (
        integral.str.char_length().gt(width)
        | (
            integral.eq(integer_limit)
            & fraction.str.slice(0, dtype.scale + 1).ge(fraction_limit)
        )
    ).fill_null(False)


def decimal_operand(
    expr: BaseExpressionAPI,
    source_dtype: t.Any,
    numeric_dtype: DecimalDtype,
    rounding: str,
    *,
    inactive: BaseExpressionAPI | None = None,
) -> NumericOperand:
    """Admit original operands, suppress unused payload, then convert natively once.

    Consumers must reject/report ``invalid``; the selected zero is not a semantic
    default. Range admission precedes casting because native overflow deliberately
    raises even under null-on-invalid conversion.
    """
    if rounding not in {"half_to_even", "half_away_from_zero"}:
        raise ValueError("Decimal operands require half_to_even or half_away_from_zero")
    unused = ma.lit(False) if inactive is None else inactive
    active = ~unused
    if source_dtype is MountainashDtype.STRING:
        invalid = (
            ~expr.str.regex_contains(_DECIMAL_TOKEN).fill_null(False)
        ) | _text_overflow(expr, numeric_dtype)
        safe = ma.when(unused | invalid).then(ma.lit("0")).otherwise(expr)
    elif isinstance(source_dtype, DecimalDtype):
        width = numeric_dtype.precision - numeric_dtype.scale
        source_width = source_dtype.precision - source_dtype.scale
        invalid = expr.is_null()
        if source_width > width or (
            source_width == width and source_dtype.scale > numeric_dtype.scale
        ):
            boundary = (
                Decimal(10**width)
                if source_dtype.scale <= numeric_dtype.scale
                else Decimal(
                    ("9" * width or "0") + "." + "9" * numeric_dtype.scale + "5"
                )
            )
            # Compare in the original source type. A wider source fractional
            # domain need not admit the target's entire integer domain.
            invalid = (
                invalid
                | expr.ge(ma.lit(boundary, dtype=source_dtype))
                | expr.le(ma.lit(boundary.copy_negate(), dtype=source_dtype))
            )
        safe = (
            ma.when(unused | invalid)
            .then(ma.lit(0, dtype=source_dtype))
            .otherwise(expr)
        )
    elif source_dtype in _INTEGER_TYPES:
        boundary = 10 ** (numeric_dtype.precision - numeric_dtype.scale)
        invalid = expr.is_null() | expr.ge(boundary) | expr.le(-boundary)
        safe = ma.when(unused | invalid).then(ma.lit(0)).otherwise(expr)
    else:
        return NumericOperand(
            ma.lit("0").cast(numeric_dtype, rounding=rounding), active
        )
    return NumericOperand(
        safe.cast(numeric_dtype, rounding=rounding),
        (active & invalid).fill_null(False),
    )


def reject_invalid(rel: t.Any, predicates: dict[str, BaseExpressionAPI]) -> None:
    """Reduce admission failures natively; only one Boolean per operand leaves SQL."""
    if not predicates:
        return
    names = [f"__admission_invalid_{i}" for i in range(len(predicates))]
    admitted = rel.select(
        *[predicate.alias(name) for name, predicate in zip(names, predicates.values())]
    )
    flags = (
        admitted.group_by()
        .agg(
            *[
                ma.col(name).any().fill_null(False).alias(f"invalid_{i}")
                for i, name in enumerate(names)
            ]
        )
        .to_dict()
    )
    bad = [name for i, name in enumerate(predicates) if flags[f"invalid_{i}"][0]]
    if bad:
        raise ValueError(f"Invalid concrete input for {', '.join(bad)}")
