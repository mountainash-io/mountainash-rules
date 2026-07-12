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
