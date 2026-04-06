"""RuleResult: wrapper for evaluated rule results with observability."""

from __future__ import annotations

import typing as t


class RuleResult:
    """Wraps the evaluated rules DataFrame with convenience accessors.

    The DataFrame is expected to contain:
    - Original rule columns (passed through unchanged)
    - __t_{dim_name} columns: ternary values (1=match, 0=unknown, -1=non-match)
    - __specificity: count of hard matches (TRUE=1 values)
    - __rank: 1-based ranking by specificity descending
    """

    def __init__(self, dataframe: t.Any, active_dimensions: list[str]) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions

    @property
    def survivors(self) -> t.Any:
        """All surviving rules, ranked by specificity descending."""
        return self._df

    @property
    def best_match(self) -> t.Any:
        """The single most specific surviving rule."""
        return self._df.head(1)

    @property
    def count(self) -> int:
        """Number of surviving rules."""
        return self._df.shape[0]

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
        filtered = self._df.filter(self._df["rule_name"] == rule_name)
        if filtered.shape[0] == 0:
            raise KeyError(f"Rule '{rule_name}' not found in survivors")

        row = filtered.head(1)
        return {
            dim: row[f"__t_{dim}"][0]
            for dim in self._active_dimensions
        }

    def at_least(self, n: int) -> t.Any:
        """Return survivors with specificity >= n.

        Args:
            n: Minimum number of hard matches required.

        Returns:
            Filtered DataFrame.
        """
        return self._df.filter(self._df["__specificity"] >= n)
