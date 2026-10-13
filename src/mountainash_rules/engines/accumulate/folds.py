"""Native folds over already-qualified distinct source contributions."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash import MountainashDtype
from mountainash.relations import Relation

from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.constants import DataType


def fold_contributions(
    relation: Relation,
    aggregates: t.Sequence[Aggregate],
    keys: t.Sequence[str],
) -> Relation:
    """Fold source payload slots by qualified contributor-set keys.

    The caller supplies one row per distinct qualified source membership. This
    function deliberately does not deduplicate payload values: equal-valued
    sources remain separate contributions. Empty groups are absent from this
    relation and are represented by the caller's outer join, never an initializer.
    """
    if not aggregates:
        raise ValueError("At least one aggregate declaration is required")
    missing = [name for name in keys if name not in relation.columns]
    missing.extend(
        f"a{index:04d}" for index in range(1, len(aggregates) + 1)
        if f"a{index:04d}" not in relation.columns
    )
    if missing:
        raise ValueError(f"Missing fold columns: {', '.join(missing)}")

    expressions = []
    for index, aggregate in enumerate(aggregates, 1):
        value = ma.col(f"a{index:04d}")
        if aggregate.operation is AggregateOp.SUM:
            folded = value.sum()
        elif aggregate.operation is AggregateOp.MIN:
            folded = value.min()
        elif aggregate.operation is AggregateOp.MAX:
            folded = value.max()
        elif aggregate.operation is AggregateOp.PRODUCT:
            if aggregate.data_type is DataType.DECIMAL:
                # Fractional products use FP64 working arithmetic and one final
                # conversion to the declared decimal result type.
                folded = value.cast(MountainashDtype.FP64).product()
            else:
                folded = value.product()
        else:  # pragma: no cover - Aggregate validates its enum
            raise ValueError(f"Unsupported aggregate operation {aggregate.operation!r}")
        if aggregate.output_numeric_dtype is not None:
            assert aggregate.output_rounding is not None
            folded = folded.cast(
                aggregate.output_numeric_dtype,
                rounding=aggregate.output_rounding,
            )
        expressions.append(folded.alias(f"v{index:04d}"))
    return relation.group_by(*keys).agg(*expressions)
