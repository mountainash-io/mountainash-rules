"""Declared set markers, preserving concrete empty sets and authored list payloads."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash import DecimalDtype, MountainashDtype
from mountainash.core.dtypes import ListDtype, TimestampDtype
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import DataType
from mountainash_rules.core.dimension import Dimension


def canonicalize_set_expr(col: BaseExpressionAPI) -> BaseExpressionAPI:
    """Sort and deduplicate concrete sets without synthesizing a wildcard."""
    return col.list.unique().list.sort()


_EMPTY_SET_ELEMENTS = {
    DataType.STR: MountainashDtype.STRING,
    DataType.INT: MountainashDtype.I64,
    DataType.FLOAT: MountainashDtype.FP64,
    DataType.DATE: MountainashDtype.DATE,
    DataType.DATETIME: MountainashDtype.TIMESTAMP,
    DataType.BOOL: MountainashDtype.BOOL,
}


def _empty_set_expr(dim: Dimension) -> BaseExpressionAPI:
    element = dim.decimal_dtype if dim.data_type is DataType.DECIMAL else _EMPTY_SET_ELEMENTS[dim.data_type]
    return ma.lit([]).list.cast_items(
        dtype=ListDtype(element),
        field_name=dim.resolved_rule_field,
    )


def set_state(
    rel: t.Any,
    dim: Dimension,
    field: str,
) -> tuple[BaseExpressionAPI, BaseExpressionAPI, BaseExpressionAPI]:
    """Return original wildcard, invalid authoring and safe comparison payload."""
    from mountainash_rules.core.compiler import _SCALAR_DTYPES, same_scalar_type
    from mountainash_rules.core.numeric import exact_literal, literal_dtype

    col = ma.col(field)
    marker_values = [dim.field.wildcard] if dim.field.wildcard_declared else []
    wildcard = col.is_null() if any(value is None for value in marker_values) else ma.lit(False)
    dtype = rel.collect_schema(allow_execution=False)[field]
    if not isinstance(dtype, ListDtype):
        return wildcard, ma.lit(True), _empty_set_expr(dim)
    element_dtype = dtype.element
    embedded = ma.lit(False)
    for marker in marker_values:
        if marker is None or not same_scalar_type(element_dtype, literal_dtype(marker)):
            continue
        contains = col.list.contains(exact_literal(marker)).fill_null(False)
        singleton = col.list.len().eq(1)
        wildcard = wildcard | (contains & singleton)
        embedded = embedded | (contains & ~singleton)
    ordered = col.list.sort()
    null_element = col.list.len().gt(0) & (
        ordered.list.first().is_null() | ordered.list.last().is_null()
    )
    if (
        element_dtype not in _SCALAR_DTYPES.get(dim.data_type, frozenset())
        and not (
            dim.data_type is DataType.DECIMAL
            and isinstance(element_dtype, DecimalDtype)
        )
        and not (
            dim.data_type is DataType.DATETIME
            and isinstance(element_dtype, TimestampDtype)
        )
    ):
        return wildcard.fill_null(False), ma.lit(True), _empty_set_expr(dim)
    payload = canonicalize_set_expr(col)
    if dim.data_type is DataType.FLOAT and element_dtype not in {
        MountainashDtype.FP32,
        MountainashDtype.FP64,
    }:
        payload = payload.list.cast_items(
            dtype=ListDtype(MountainashDtype.FP64), field_name=field
        )
    return (
        wildcard.fill_null(False),
        (col.is_null() | embedded | null_element).fill_null(False),
        payload,
    )
