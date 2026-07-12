"""RuleResult: wrapper for evaluated rule results with backend-agnostic accessors."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.constants import HitPolicy
from mountainash_rules.hit_policy import (
    SelectionInfo,
    apply_cardinality,
    check_assertions,
    ordering_keys,
)


class RuleResult:
    """Wraps the evaluated rules DataFrame with convenience accessors.

    The DataFrame is expected to contain:
    - Original rule columns (passed through unchanged)
    - __t_{dim_name} columns: ternary values (1=match, 0=unknown, -1=non-match)
    - __specificity: count of hard matches (TRUE=1 values)
    - __rank: 1-based ranking by specificity descending

    All accessors are backend-agnostic — they reach the DataFrame only through
    mountainash.relations.Relation. The `survivors` property returns the native
    input backend so users can chain backend-specific operations on the result.
    """

    def __init__(
        self,
        dataframe: t.Any,
        active_dimensions: list[str],
        selection_info: SelectionInfo | None = None,
    ) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions
        self._selection_info = selection_info

    def select(
        self, policy: HitPolicy, priority_field: str | None = None
    ) -> "RuleResult":
        """Re-apply a hit policy over an untruncated COLLECT result."""
        info = self._selection_info
        if info is None:
            raise ValueError(
                "select() requires a result produced by evaluate() with "
                "metadata (no selection info available)"
            )
        if info.truncated:
            raise ValueError(
                "select() on a truncated result (top_n/min_specificity was "
                "applied); re-evaluate without truncation instead"
            )
        rel = relation(self._df)
        for required in ("__specificity", "__rule_index"):
            if required not in rel.columns:
                raise ValueError(f"select() requires the {required} column")
        pf = priority_field or info.priority_field
        keys = ordering_keys(policy, pf)
        rel = (
            rel
            .sort(*[k for k, _ in keys], descending=[d for _, d in keys])
            .drop("__rank")
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )
        check_assertions(rel, policy, info)
        rel = apply_cardinality(rel, policy)
        return RuleResult(
            dataframe=rel.collect(),
            active_dimensions=self._active_dimensions,
            selection_info=info,
        )

    @property
    def survivors(self) -> t.Any:
        """All surviving rules, ranked by specificity descending.

        Returns the native DataFrame in the same backend as the input.
        """
        return self._df

    @property
    def best_match(self) -> t.Any:
        """The single most specific surviving rule."""
        return relation(self._df).head(1).collect()

    @property
    def count(self) -> int:
        """Number of surviving rules."""
        return relation(self._df).count_rows()

    @property
    def active_dimensions(self) -> list[str]:
        """Dimensions that were evaluated."""
        return self._active_dimensions

    def explain(self, rule_name: str) -> dict[str, int]:
        """Per-dimension ternary values for a specific rule.

        Args:
            rule_name: The value in the 'rule_name' column to look up.

        Returns:
            Dict mapping dimension name to ternary value (1, 0, or -1).

        Raises:
            KeyError: If the rule_name is not found in survivors.
        """
        rel = (
            relation(self._df)
            .filter(ma.col("rule_name").eq(ma.lit(rule_name)))
            .head(1)
        )
        if rel.count_rows() == 0:
            raise KeyError(f"Rule '{rule_name}' not found in survivors")
        return {
            dim: rel.item(f"__t_{dim}")
            for dim in self._active_dimensions
        }

    def at_least(self, n: int) -> t.Any:
        """Return survivors with specificity >= n.

        Args:
            n: Minimum number of hard matches required.

        Returns:
            Filtered DataFrame in the same backend as the input.
        """
        return (
            relation(self._df)
            .filter(ma.col("__specificity").ge(ma.lit(n)))
            .collect()
        )
