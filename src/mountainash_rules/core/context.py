"""Context value extraction utilities."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import NOT_SET, DataType, not_set_sentinel_for

if t.TYPE_CHECKING:
    from mountainash_rules.core.dimension import DimensionsMetadata


def _validate_context_ids(rel: t.Any, context_id_field: str) -> None:
    """Reject missing, null or duplicate caller IDs before conversion/chunking.

    Aggregate row, non-null and distinct counts together; never collect the
    full input merely to validate identity. A single null is invalid even
    when uniqueness would otherwise allow it.
    """
    if not isinstance(context_id_field, str) or not context_id_field:
        raise ValueError("context_id_field must be a nonempty string or None")
    if context_id_field not in rel.columns:
        raise ValueError(f"context_id_field {context_id_field!r} not found in contexts")
    stats = (
        rel.group_by()
        .agg(
            ma.count_records().alias("__total"),
            ma.col(context_id_field).count().alias("__non_null"),
            ma.col(context_id_field).n_unique().alias("__distinct"),
        )
        .to_dict()
    )
    total = stats["__total"][0]
    non_null = stats["__non_null"][0]
    distinct = stats["__distinct"][0]
    if non_null != total:
        raise ValueError(
            f"context_id_field {context_id_field!r} must be non-null "
            f"({total - non_null} null value(s) found among {total} rows)"
        )
    if distinct != total:
        raise ValueError(
            f"context_id_field {context_id_field!r} must be globally unique "
            f"({total} rows, {distinct} distinct value(s))"
        )


def _absent_context_value(data_type: DataType | None) -> t.Any:
    """Choose the declared type's missing value; bool has no in-band sentinel."""
    if data_type is DataType.BOOL:
        return None
    return not_set_sentinel_for(data_type) if data_type is not None else NOT_SET


def _nullable_bool(expr: BaseExpressionAPI) -> BaseExpressionAPI:
    """Preserve absence when a backend's Boolean cast maps null to False."""
    return ma.when(expr.is_null()).then(None).otherwise(expr.cast(bool))


def _context_literal(value: t.Any, data_type: DataType | None) -> BaseExpressionAPI:
    """Bind Boolean absence with a type even when no concrete values exist."""
    literal = ma.lit(value)
    return (
        _nullable_bool(literal)
        if value is None and data_type is DataType.BOOL
        else literal
    )


def extract_context_values(
    context: BaseModel | dict,
    dimension_names: list[str],
    metadata: DimensionsMetadata | None = None,
) -> dict[str, t.Any]:
    """Extract context values for the given dimension names.

    Args:
        context: A Pydantic model or dict containing context values.
        dimension_names: The dimension names to extract values for.
        metadata: Optional dimension metadata. When provided, values are read
            from each dimension's resolved_context_field and missing values
            use its typed NOT_SET sentinel (Boolean null for bool). Without
            metadata, values are read by dimension name and use string NOT_SET.

    Returns:
        Dict mapping dimension name to its value or sentinel.
    """
    if isinstance(context, BaseModel):
        raw = context.model_dump()
    elif isinstance(context, dict):
        raw = context
    else:
        raise TypeError(
            f"Context must be a BaseModel or dict, got {type(context).__name__}"
        )

    result: dict[str, t.Any] = {}
    for name in dimension_names:
        dim = metadata.get_dimension(name) if metadata is not None else None
        field = dim.resolved_context_field if dim is not None else name
        value = raw.get(field)
        if value is None:
            result[name] = _absent_context_value(
                dim.data_type if dim is not None else None
            )
        else:
            result[name] = value
    return result
