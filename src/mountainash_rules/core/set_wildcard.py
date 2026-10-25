"""Declared set markers, preserving concrete empty sets and authored list payloads."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.core.dtypes import ListDtype
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.dimension import Dimension
from mountainash_rules.core.numeric import exact_literal, literal_dtype, same_scalar_type
from mountainash_rules.core.type_families import type_family




def set_state(
    rel: t.Any,
    dim: Dimension,
    field: str,
) -> tuple[BaseExpressionAPI, BaseExpressionAPI, BaseExpressionAPI]:
    """Return original wildcard, invalid authoring and safe comparison payload."""
    family = type_family(dim.data_type)

    col = ma.col(field)
    marker_values = [dim.field.wildcard] if dim.field.wildcard_declared else []
    wildcard = col.is_null() if any(value is None for value in marker_values) else ma.lit(False)
    dtype = rel.collect_schema(allow_execution=False)[field]
    if not isinstance(dtype, ListDtype):
        return wildcard, ma.lit(True), family.empty_list(dim.field, source_name=dim.resolved_rule_field)
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
    if not family.admits_engine_element(element_dtype):
        return wildcard.fill_null(False), ma.lit(True), family.empty_list(dim.field, source_name=dim.resolved_rule_field)
    payload = family.engine_list_payload(col, element_dtype, source_name=field)
    return (
        wildcard.fill_null(False),
        (col.is_null() | embedded | null_element).fill_null(False),
        payload,
    )
