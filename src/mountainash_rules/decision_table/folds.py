from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import Relation

from mountainash_rules.core.aggregate import Aggregate, AggregateOp


def fold_contributions(
    relation: Relation,
    aggregates: t.Sequence[Aggregate],
    keys: t.Sequence[str],
) -> Relation:
    """Fold already-qualified contribution rows independently for each cell."""
    if not aggregates:
        raise ValueError("At least one aggregate declaration is required")
    missing = [name for name in keys if name not in relation.columns]
    missing.extend(
        f"a{index:04d}"
        for index in range(1, len(aggregates) + 1)
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
        else:  # pragma: no cover - Aggregate validates its enum
            raise ValueError(f"Unsupported aggregate operation {aggregate.operation!r}")
        expressions.append(folded.alias(aggregate.output_name))
    return relation.group_by(*keys).agg(*expressions)
