"""Context value extraction utilities."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash import MountainashDtype, ValueKind
from mountainash.core.dtypes import TimestampDtype
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import BooleanCoercion
from mountainash_rules.core.numeric import typed_marker_predicate
from mountainash_rules.core.type_families import type_family

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


def _validate_boolean_coercion(policy: BooleanCoercion) -> None:
    """Reject implicit configuration conversions and unknown flag bits."""
    if not isinstance(policy, BooleanCoercion) or int(policy) & ~7:
        raise ValueError(
            "boolean_coercion must be a BooleanCoercion with only known flags"
        )


def extract_context_values(
    context: BaseModel | dict,
    dimension_names: list[str],
    metadata: DimensionsMetadata | None = None,
    *,
    boolean_coercion: BooleanCoercion = BooleanCoercion.NONE,
) -> dict[str, t.Any]:
    """Project supplied original values by dimension; omission stays omitted."""
    _validate_boolean_coercion(boolean_coercion)
    if isinstance(context, BaseModel):
        raw = context.model_dump(exclude_unset=True)
    elif isinstance(context, dict):
        raw = context
    else:
        raise TypeError(
            f"Context must be a BaseModel or dict, got {type(context).__name__}"
        )
    result = {}
    for name in dimension_names:
        dim = metadata.get_dimension(name) if metadata is not None else None
        field = dim.resolved_context_field if dim is not None else name
        if field in raw:
            result[name] = raw[field]
    return result


def context_binding_expressions(
    dim: Dimension,
    raw: BaseExpressionAPI,
    source_dtype: t.Any,
    supplied: BaseExpressionAPI,
    boolean_coercion: BooleanCoercion = BooleanCoercion.NONE,
) -> tuple[list[BaseExpressionAPI], BaseExpressionAPI]:
    """Build reusable native payload/presence/provenance and invalid admission."""

    markers = [dim.field.wildcard] if dim.field.wildcard_declared else []
    marker = supplied & typed_marker_predicate(raw, source_dtype, markers)
    absent = (
        raw.is_null()
        if source_dtype in (MountainashDtype.DATE, MountainashDtype.TIMESTAMP)
        or isinstance(source_dtype, TimestampDtype)
        else raw.value_kind().eq(ValueKind.ABSENT.value)
    )
    present = supplied & ~marker & ~absent
    payload, invalid = type_family(dim.data_type).bind_context(
        dim.field, raw, source_dtype, present, boolean_coercion,
        source_name=dim.resolved_context_field,
    )
    origin = (
        ma.when(~supplied)
        .then("omitted")
        .when(marker)
        .then("dont_care")
        .when(raw.is_null())
        .then("null")
        .when(absent)
        .then("native_missing")
        .when(invalid)
        .then("invalid")
        .otherwise("concrete")
    )
    name = dim.dimension_name
    return [
        payload.alias(f"__ctx_value_{name}"),
        (present & ~invalid).alias(f"__ctx_present_{name}"),
        supplied.alias(f"__ctx_supplied_{name}"),
        (supplied & raw.is_null()).fill_null(False).alias(f"__ctx_null_{name}"),
        origin.alias(f"__ctx_origin_{name}"),
    ], invalid


def bind_context_columns(
    rel: t.Any,
    dimensions: t.Sequence[Dimension],
    *,
    values: dict[str, t.Any] | None = None,
    boolean_coercion: BooleanCoercion = BooleanCoercion.NONE,
) -> t.Any:
    """Filter admission convenience around reusable context binding expressions."""
    schema = rel.schema if values is None else {}
    columns = []
    invalid = {}
    for dim in dimensions:
        if values is None:
            field = dim.resolved_context_field
            supplied = field in rel.columns
            raw = ma.col(field) if supplied else ma.lit(None)
            dtype = schema.get(field)
        else:
            supplied = dim.dimension_name in values
            value = values.get(dim.dimension_name)
            raw, dtype = type_family(dim.data_type).context_literal(
                value, source_name=dim.resolved_context_field, coercion=boolean_coercion
            )
        bindings, failure = context_binding_expressions(
            dim, raw, dtype, ma.lit(supplied), boolean_coercion
        )
        columns.extend(bindings)
        invalid[dim.resolved_context_field + ":" + dim.dimension_name] = failure
    return rel.with_columns(*columns)


