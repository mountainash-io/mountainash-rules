"""Declared set markers, preserving concrete empty sets and authored list payloads."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash import MountainashDtype
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import relation

from mountainash_rules.core.constants import DataType
from mountainash_rules.core.dimension import Dimension


def canonicalize_set_expr(col: BaseExpressionAPI) -> BaseExpressionAPI:
    """Sort and deduplicate concrete sets without synthesizing a wildcard."""
    return col.list.unique().list.sort()


def _empty_set_expr(dim: Dimension) -> BaseExpressionAPI:
    item_type = {
        DataType.STR: "string",
        DataType.INT: "integer",
        DataType.FLOAT: "number",
        DataType.DATE: "date",
        DataType.DATETIME: "datetime",
        DataType.BOOL: "boolean",
    }[dim.data_type]
    return ma.lit([]).list.cast_items(
        item_type=item_type, field_name=dim.resolved_rule_field
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
    wildcard = (
        col.is_null()
        if any(value is None for value in dim.wildcard_values)
        else ma.lit(False)
    )
    if rel.schema[field] is not MountainashDtype.LIST:
        return wildcard, ma.lit(True), _empty_set_expr(dim)
    # Empty native schema projection preserves the actual element type without
    # reading source rows or confusing Boolean/integer/string marker domains.
    element_dtype = relation(
        rel.select(col.list.first().alias(field)).head(0).collect()
    ).schema[field]
    embedded = ma.lit(False)
    for marker in dim.wildcard_values:
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
    if element_dtype not in _SCALAR_DTYPES[dim.data_type]:
        return wildcard.fill_null(False), ma.lit(True), _empty_set_expr(dim)
    payload = canonicalize_set_expr(col)
    if dim.data_type is DataType.FLOAT and element_dtype not in {
        MountainashDtype.FP32,
        MountainashDtype.FP64,
    }:
        payload = payload.list.cast_items(item_type="number", field_name=field)
    return (
        wildcard.fill_null(False),
        (col.is_null() | embedded | null_element).fill_null(False),
        payload,
    )
