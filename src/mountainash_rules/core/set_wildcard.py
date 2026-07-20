"""Shared in-band sentinel representation for set-dimension wildcards.

A SET_MEMBERSHIP/SET_EXCLUSION wildcard is the single-element list
``[unknown_sentinel_for(dim.data_type)]`` — never a null. These helpers are the
single source of truth for both the filter engine (``core/compiler.py``) and the
accumulator engine (``engines/accumulator/``), so the two cannot diverge.

The sentinel is a reserved, out-of-domain value; ``validate_set_columns``
enforces that a concrete rule list never embeds it, so ``set_wildcard_predicate``
is unambiguous.
"""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import DataType, unknown_sentinel_for
from mountainash_rules.core.dimension import Dimension


def _typed_sentinel(dim: Dimension) -> t.Any:
    """The dimension's wildcard sentinel, coerced to the dim's Python element type."""
    sent = unknown_sentinel_for(dim.data_type)
    return float(sent) if dim.data_type == DataType.FLOAT else sent


def sentinel_list_expr(dim: Dimension) -> BaseExpressionAPI:
    """A ``[sentinel]`` list literal whose element carries the dim's Python type.

    Element coercion (not a native list-dtype cast) keeps this backend-pure: a
    Python-float element yields a ``List(Float64)`` literal with no cast.
    """
    return ma.lit([_typed_sentinel(dim)])


def canonicalize_set_expr(col: BaseExpressionAPI) -> BaseExpressionAPI:
    """Sort + dedupe a list column so equal sets compare/fingerprint identically."""
    return col.list.unique().list.sort()


def normalize_set_expr(dim: Dimension, col: BaseExpressionAPI) -> BaseExpressionAPI:
    """Null list -> ``[sentinel]``; concrete list -> sorted-unique. The one normaliser.

    Idempotent: re-applying to an already-normalized column is a no-op.
    """
    return ma.when(col.is_null()).then(sentinel_list_expr(dim)).otherwise(
        canonicalize_set_expr(col)
    )


def set_wildcard_predicate(dim: Dimension, col: BaseExpressionAPI) -> BaseExpressionAPI:
    """True when ``col`` (post-normalization) is the wildcard.

    Because ``validate_set_columns`` rejects any concrete list embedding the
    sentinel, ``list.contains(sentinel)`` is true iff the list is exactly
    ``[sentinel]``. Must be evaluated after normalization (``contains`` returns
    null on a null cell).
    """
    return col.list.contains(ma.lit(_typed_sentinel(dim)))


def _embedded_sentinel_predicate(dim: Dimension, field: str) -> BaseExpressionAPI:
    """True for a non-null list that contains the sentinel but is not ``[sentinel]``."""
    col = ma.col(field)
    has_sentinel = col.list.contains(ma.lit(_typed_sentinel(dim)))
    length = col.list.len()
    return col.is_not_null().__and__(has_sentinel.__and__(length.ne(ma.lit(1))))


def _null_element_predicate(field: str) -> BaseExpressionAPI:
    """True for a non-null list that contains a null element.

    Uses ``list.drop_nulls`` — mountainash's IBIS backend raises
    ``BackendCapabilityError`` for this op, so callers must only run this on the
    polars-internal accumulator build path (see ``validate_set_no_null_elements``).
    """
    col = ma.col(field)
    length = col.list.len()
    non_null_length = col.list.drop_nulls().list.len()
    return col.is_not_null().__and__(length.ne(non_null_length))


def validate_set_columns(rules_rel: t.Any, set_dims: list[Dimension]) -> None:
    """Raise ``ValueError`` if a concrete set-rule list embeds the reserved sentinel.

    Portable — uses only ``list.contains`` + ``list.len`` (Ibis/Narwhals-safe), so
    it runs in BOTH engines on any backend. A whole-list null is valid (the
    wildcard). ``rules_rel`` is a ``mountainash`` relation; row existence is tested
    with the relation's portable ``count_rows`` (backend-pure — no native import;
    builtin ``len`` on a collected frame is NOT portable — Ibis rejects it).
    """
    for dim in set_dims:
        field = dim.resolved_rule_field
        n_embedded = rules_rel.filter(_embedded_sentinel_predicate(dim, field)).count_rows()
        if n_embedded > 0:
            raise ValueError(
                f"Dimension '{dim.dimension_name}': a concrete rule list embeds the "
                f"reserved wildcard sentinel {unknown_sentinel_for(dim.data_type)!r}. "
                f"The sentinel is only valid as the sole element (the wildcard)."
            )


def validate_set_no_null_elements(rules_rel: t.Any, set_dims: list[Dimension]) -> None:
    """Raise ``ValueError`` if a set-rule list contains a null element.

    Uses ``list.drop_nulls`` (Ibis-unsupported), so this is called ONLY on the
    polars-internal accumulator build path. The filter engine does not call it:
    ``t_is_in`` tolerates a null element (it matches nothing), so a standalone
    filter engine over Ibis set rules is unaffected.
    """
    for dim in set_dims:
        field = dim.resolved_rule_field
        n_null_elem = rules_rel.filter(_null_element_predicate(field)).count_rows()
        if n_null_elem > 0:
            raise ValueError(
                f"Dimension '{dim.dimension_name}': a rule list contains a null "
                f"element. Element-level nulls are not allowed; use a whole-list "
                f"null (or omit the cell) for a wildcard."
            )
