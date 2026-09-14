"""BatchRuleResult: backend-agnostic accessors over batch evaluation output."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.core.context import _validate_context_ids
from mountainash_rules.core.hit_policy import SelectionInfo, first_per_context
from mountainash_rules.core.result import RuleResult


class BatchRuleResult:
    """Wrap retained rows correlated by canonical ``__context_id``."""

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
        """Retained rows ordered by canonical context ID and applied-policy rank."""
        return self._df

    @property
    def best_matches(self) -> t.Any:
        """One minimum-remaining-rank row for each context with retained matches."""
        return (
            first_per_context(relation(self._df))
            .sort("__context_id", "__rank")
            .collect()
        )

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def active_dimensions(self) -> list[str]:
        return self._active_dimensions

    @property
    def context_id_field(self) -> str:
        """Input ID field, or ``__context_id`` for generated positional IDs.

        Result rows always use canonical ``__context_id``; the source field
        is not echoed from contexts.
        """
        return self._context_id_field

    @property
    def counts_per_context(self) -> t.Any:
        """Counts after filters/cardinality, excluding contexts with no retained rows."""
        return (
            relation(self._df)
            .group_by("__context_id")
            .agg(ma.col("__rank").count().alias("__n"))
            .collect()
        )

    @property
    def matched_context_ids(self) -> list:
        """Sorted IDs with retained rows, not all contexts that survived scoring."""
        rows = relation(self._df).select(ma.col("__context_id")).unique().to_dict()
        return sorted(rows["__context_id"])

    def unmatched_context_ids(self, contexts: t.Any) -> list:
        """Return submitted IDs absent from retained rows, in sorted order.

        Pass the original context collection. Supplied IDs must retain their
        non-null, unique source field. Generated IDs require the original row
        order and count; this result stores no input copy to verify provenance.
        """
        ctx_rel = relation(contexts)
        if self._context_id_field == "__context_id":
            all_ids = range(ctx_rel.count_rows())
        else:
            _validate_context_ids(ctx_rel, self._context_id_field)
            all_ids = ctx_rel.select(ma.col(self._context_id_field)).to_dict()[
                self._context_id_field
            ]
        matched = set(self.matched_context_ids)
        return sorted(i for i in all_ids if i not in matched)

    def for_context(self, context_id) -> RuleResult:
        """Return an ordered view, preserving the batch's completeness state.

        Unmatched and never-submitted IDs both produce a typed empty view;
        emptiness does not make a potentially truncated batch re-selectable.
        """
        frame = (
            relation(self._df)
            .filter(ma.col("__context_id").eq(ma.lit(context_id)))
            .sort("__rank")
            .collect()
        )
        return RuleResult(
            dataframe=frame,
            active_dimensions=self._active_dimensions,
            selection_info=self._selection_info,
        )
