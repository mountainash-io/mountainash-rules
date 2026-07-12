"""BatchRuleResult: backend-agnostic accessors over batch evaluation output."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.hit_policy import SelectionInfo
from mountainash_rules.result import RuleResult


class BatchRuleResult:
    """Wraps the batch survivor frame (one row per context x surviving rule)."""

    def __init__(
        self,
        dataframe: t.Any,
        active_dimensions: list[str],
        context_id_field: str,
        selection_info: SelectionInfo | None = None,
    ) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions
        self._context_id_field = context_id_field
        self._selection_info = selection_info

    @property
    def survivors(self) -> t.Any:
        return self._df

    @property
    def best_matches(self) -> t.Any:
        return (
            relation(self._df).filter(ma.col("__rank").eq(ma.lit(1))).collect()
        )

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def active_dimensions(self) -> list[str]:
        return self._active_dimensions

    @property
    def context_id_field(self) -> str:
        return self._context_id_field

    @property
    def counts_per_context(self) -> t.Any:
        return (
            relation(self._df)
            .group_by("__context_id")
            .agg(ma.col("__rank").count().alias("__n"))
            .collect()
        )

    @property
    def matched_context_ids(self) -> list:
        rows = (
            relation(self._df).select(ma.col("__context_id")).unique().to_dict()
        )
        return sorted(rows["__context_id"])

    def unmatched_context_ids(self, contexts: t.Any) -> list:
        matched = set(self.matched_context_ids)
        ctx_rel = relation(contexts)
        if self._context_id_field in ctx_rel.columns:
            all_ids = ctx_rel.select(
                ma.col(self._context_id_field)
            ).unique().to_dict()[self._context_id_field]
        else:
            all_ids = list(range(ctx_rel.count_rows()))
        return sorted(i for i in all_ids if i not in matched)

    def for_context(self, context_id) -> RuleResult:
        frame = (
            relation(self._df)
            .filter(ma.col("__context_id").eq(ma.lit(context_id)))
            .collect()
        )
        return RuleResult(
            dataframe=frame,
            active_dimensions=self._active_dimensions,
            selection_info=self._selection_info,
        )
