"""Context value extraction utilities."""

from __future__ import annotations

import typing as t
from itertools import product

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash import (
    CaseFailureBehaviour,
    MountainashDtype,
    ValueKind,
    boolean_value,
    text_value,
    value_kind,
)
from mountainash.core.dtypes import TimestampDtype
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import BooleanCoercion, DataType
from mountainash_rules.core.numeric import (
    decimal_operand,
    exact_literal,
    literal_dtype,
)

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import Dimension, DimensionsMetadata

_CONTEXT_FLOAT_DTYPES = frozenset({MountainashDtype.FP32, MountainashDtype.FP64})


def context_absence_expression(
    raw: BaseExpressionAPI, source_dtype: t.Any, data_type: DataType,
) -> BaseExpressionAPI:
    """Recognize native missing values for strict concrete context admission."""
    absent = (
        raw.is_null()
        if source_dtype in (MountainashDtype.DATE, MountainashDtype.TIMESTAMP)
        or isinstance(source_dtype, TimestampDtype)
        else raw.value_kind().eq(ValueKind.ABSENT.value)
    )
    if (
        data_type in (DataType.INT, DataType.FLOAT)
        and source_dtype in _CONTEXT_FLOAT_DTYPES
    ):
        absent = absent | raw.is_nan()
    return absent.fill_null(False)

_BOOLEAN_TRIM = " \t\n\r\f\v"

_BOOLEAN_TRUE = tuple(
    "".join(chars) for chars in product(*[(c, c.upper()) for c in "true"])
) + ("1",)
_BOOLEAN_FALSE = tuple(
    "".join(chars) for chars in product(*[(c, c.upper()) for c in "false"])
) + ("0",)


def _validate_boolean_coercion(policy: BooleanCoercion) -> None:
    """Reject implicit configuration conversions and unknown flag bits."""
    if not isinstance(policy, BooleanCoercion) or int(policy) & ~7:
        raise ValueError(
            "boolean_coercion must be a BooleanCoercion with only known flags"
        )


def _numeric_boolean_source(policy: BooleanCoercion) -> str | None:
    if policy & BooleanCoercion.NUMERIC_TRUTHINESS:
        return "finite_number"
    if policy & BooleanCoercion.BINARY_NUMBERS:
        return "binary_number"
    return None


def _normalize_boolean(
    value: t.Any,
    field: str,
    policy: BooleanCoercion = BooleanCoercion.NONE,
) -> bool | None:
    """Admit candidates from the original domain, never a conversion chain."""
    if value_kind(value) is ValueKind.ABSENT:
        return None
    normalized = boolean_value(value, source="boolean")
    if normalized is not None:
        return normalized
    numeric_source = _numeric_boolean_source(policy)
    if numeric_source is not None:
        normalized = boolean_value(value, source=numeric_source)
        if normalized is not None:
            return normalized
    if policy & BooleanCoercion.BOOLEAN_TEXT:
        text = text_value(value)
        if text is not None:
            token = text.strip(_BOOLEAN_TRIM)
            if token in _BOOLEAN_TRUE:
                return True
            if token in _BOOLEAN_FALSE:
                return False
    raise ValueError(
        f"Invalid Boolean input for context field {field!r} under {policy!r}"
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
    *,
    strict_concrete: bool = False,
) -> tuple[list[BaseExpressionAPI], BaseExpressionAPI]:
    """Build reusable native payload/presence/provenance and invalid admission."""
    from mountainash_rules.core.compiler import scalar_operand, typed_marker_predicate

    markers = [dim.field.wildcard] if dim.field.wildcard_declared else []
    marker = supplied & typed_marker_predicate(raw, source_dtype, markers)
    absent = (
        context_absence_expression(raw, source_dtype, dim.data_type)
        if strict_concrete
        else (
            raw.is_null()
            if source_dtype in (MountainashDtype.DATE, MountainashDtype.TIMESTAMP)
            or isinstance(source_dtype, TimestampDtype)
            else raw.value_kind().eq(ValueKind.ABSENT.value)
        )
    )
    present = supplied & ~marker & ~absent
    invalid = ma.lit(False)
    if dim.data_type is DataType.DECIMAL:
        assert dim.rounding is not None
        operand = decimal_operand(
            raw, source_dtype, dim.decimal_dtype, dim.rounding, inactive=~present
        )
        payload, invalid = operand.value, operand.invalid
    elif dim.data_type is DataType.BOOL:
        candidates = [raw.boolean_value(source="boolean")]
        numeric_source = _numeric_boolean_source(boolean_coercion)
        if numeric_source is not None:
            candidates.append(raw.boolean_value(source=numeric_source))
        if boolean_coercion & BooleanCoercion.BOOLEAN_TEXT:
            candidates.append(
                raw.text_value()
                .str.strip_chars(_BOOLEAN_TRIM)
                .parse_boolean(
                    true_values=_BOOLEAN_TRUE,
                    false_values=_BOOLEAN_FALSE,
                    field_name=dim.resolved_context_field,
                    failure_behavior=CaseFailureBehaviour.NULL,
                )
            )
        payload = candidates[0] if len(candidates) == 1 else ma.coalesce(*candidates)
        invalid = present & payload.is_null()
    elif strict_concrete:
        payload, invalid = scalar_operand(dim, raw, source_dtype, ~present)
    else:
        # Only the inactive branch gets a typed null. Original business values
        # are not inspected after this binding to infer availability.
        payload = (
            ma.when(~present)
            .then(ma.lit(None))
            .otherwise(raw)
            .cast(dim.data_type.python_type)
        )
    if dim.data_type is DataType.DATETIME:
        payload = payload.dt.truncate(f"1{dim.field.datetime_unit}")
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
        origin.alias(f"__ctx_origin_{name}"),
    ], invalid


def _boolean_literal_source(
    value: t.Any, field: str, policy: BooleanCoercion
) -> tuple[BaseExpressionAPI, t.Any]:
    """Preserve upstream scalar admission, including values wider than native INT."""
    kind = value_kind(value)
    if kind is ValueKind.BOOLEAN:
        value = boolean_value(value, source="boolean")
    elif kind is ValueKind.INTEGER:
        value = int(value)
        if not -(1 << 63) <= value < (1 << 64):
            # Outside metadata's supported INT transport there can be no typed
            # marker collision. Admit original truthiness before native binding.
            return ma.lit(_normalize_boolean(value, field, policy)), None
    elif kind is ValueKind.FLOAT:
        value = float(value)
    elif kind is ValueKind.TEXT:
        value = text_value(value)
    elif kind is ValueKind.ABSENT:
        value = None
    elif literal_dtype(value) in (None, MountainashDtype.LIST):
        raise ValueError(f"Invalid Boolean input for context field {field!r}")
    return exact_literal(value), literal_dtype(value)


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
            if dim.data_type is DataType.BOOL:
                raw, dtype = _boolean_literal_source(
                    value, dim.resolved_context_field, boolean_coercion
                )
            else:
                raw, dtype = exact_literal(value), literal_dtype(value)
        bindings, failure = context_binding_expressions(
            dim, raw, dtype, ma.lit(supplied), boolean_coercion
        )
        columns.extend(bindings)
        invalid[dim.resolved_context_field + ":" + dim.dimension_name] = failure
    return rel.with_columns(*columns)


