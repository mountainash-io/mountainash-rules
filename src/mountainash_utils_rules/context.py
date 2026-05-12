"""Context value extraction utilities."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel

from mountainash_rules.constants import NOT_SET


def extract_context_values(
    context: BaseModel | dict,
    dimension_names: list[str],
) -> dict[str, t.Any]:
    """Extract context values for the given dimension names.

    Args:
        context: A Pydantic model or dict containing context values.
        dimension_names: The dimension names to extract values for.

    Returns:
        Dict mapping dimension name to its value, or NOT_SET/NOT_SET_NUMERIC
        if the field is missing or None.
    """
    if isinstance(context, BaseModel):
        raw = context.model_dump()
    elif isinstance(context, dict):
        raw = context
    else:
        raise TypeError(f"Context must be a BaseModel or dict, got {type(context).__name__}")

    result: dict[str, t.Any] = {}
    for name in dimension_names:
        value = raw.get(name)
        if value is None:
            result[name] = NOT_SET
        else:
            result[name] = value
    return result
