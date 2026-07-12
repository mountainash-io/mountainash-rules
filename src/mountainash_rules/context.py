"""Context value extraction utilities."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel

from mountainash_rules.constants import NOT_SET, not_set_sentinel_for

if t.TYPE_CHECKING:
    from mountainash_rules.dimension import DimensionsMetadata


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
            use the type-correct sentinel (NOT_SET_NUMERIC for int/float
            dimensions, NOT_SET otherwise). Without metadata, values are read
            by dimension name and missing values use NOT_SET.

    Returns:
        Dict mapping dimension name to its value or sentinel.
    """
    if isinstance(context, BaseModel):
        raw = context.model_dump()
    elif isinstance(context, dict):
        raw = context
    else:
        raise TypeError(f"Context must be a BaseModel or dict, got {type(context).__name__}")

    result: dict[str, t.Any] = {}
    for name in dimension_names:
        dim = metadata.get_dimension(name) if metadata is not None else None
        field = dim.resolved_context_field if dim is not None else name
        value = raw.get(field)
        if value is None:
            if dim is not None:
                result[name] = not_set_sentinel_for(dim.data_type)
            else:
                result[name] = NOT_SET
        else:
            result[name] = value
    return result
