"""RuleResult: wrapper for evaluated rule results with backend-agnostic accessors."""

from __future__ import annotations

import typing as t
from dataclasses import replace

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.core.constants import HitPolicy
from mountainash_rules.core.hit_policy import (
    SelectionInfo,
    apply_cardinality,
    check_assertions,
    check_policy_config,
    check_priority,
    normalize_policy,
    ordering_keys,
    selection_is_truncated,
    validate_priority_field,
)


class RuleResult:
    """Wraps the evaluated rules DataFrame with convenience accessors.

    The DataFrame is expected to contain:
    - Original rule columns (passed through unchanged)
    - __t_{dim_name} columns: ternary values (1=match, 0=unknown, -1=non-match)
    - __specificity: count of hard matches (TRUE=1 values)
    - __rank: 1-based applied-policy rank, assigned before caller filters

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
        self, policy: HitPolicy | str, priority_field: str | None = None
    ) -> "RuleResult":
        """Re-apply a policy only when the full candidate basis is retained."""
        policy = normalize_policy(policy)
        validate_priority_field(priority_field)
        info = self._selection_info
        if info is None:
            raise ValueError(
                "select() requires selection information; re-evaluate "
                "with the engine before selecting another policy"
            )
        if info.truncated:
            raise ValueError(
                "select() on a possibly truncated result (filters or cardinality "
                "were applied); re-evaluate the complete candidate set instead"
            )
        rel = relation(self._df)
        for required in ("__rank", "__specificity", "__rule_index"):
            if required not in rel.columns:
                raise ValueError(f"select() requires the {required} column")
        pf = priority_field if priority_field is not None else info.priority_field
        info = replace(info, priority_field=pf)
        check_policy_config(rel.columns, policy, info)
        check_priority(rel, policy, info)
        keys = ordering_keys(policy, pf)
        rel = (
            rel.sort(*[k for k, _ in keys], descending=[d for _, d in keys])
            .drop("__rank")
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )
        check_assertions(rel, policy, info)
        rel = apply_cardinality(rel, policy)
        return RuleResult(
            dataframe=rel.sort("__rank").collect(),
            active_dimensions=self._active_dimensions,
            selection_info=replace(info, truncated=selection_is_truncated(policy)),
        )

    @property
    def survivors(self) -> t.Any:
        """Retained rules in applied-policy rank order, not necessarily all candidates.

        Returns the native DataFrame in the same backend as the input.
        """
        return self._df

    @property
    def best_match(self) -> t.Any:
        """The retained rule with minimum applied-policy rank, or an empty frame."""
        return relation(self._df).sort("__rank").head(1).collect()

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
            relation(self._df).filter(ma.col("rule_name").eq(ma.lit(rule_name))).head(1)
        )
        if rel.count_rows() == 0:
            raise KeyError(f"Rule '{rule_name}' not found in survivors")
        return {dim: rel.item(f"__t_{dim}") for dim in self._active_dimensions}

    def at_least(self, n: int) -> t.Any:
        """Return survivors with specificity >= n.

        Args:
            n: Minimum number of hard matches required.

        Returns:
            Filtered DataFrame in the same backend as the input.
        """
        return (
            relation(self._df).filter(ma.col("__specificity").ge(ma.lit(n))).collect()
        )


class ExplainResult:
    """Every rule scored against a context — ternaries, __survived, __specificity.

    No selection has been applied: there is no __rank and hit policies are
    not consulted. Not a RuleResult subclass — RuleResult's selection
    surface (best_match, select(), SelectionInfo) assumes ranked survivor
    frames, and inheriting the frame accessors would drag that API along.
    """

    def __init__(self, dataframe: t.Any, active_dimensions: list[str]) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions

    @property
    def frame(self) -> t.Any:
        return self._df

    @property
    def active_dimensions(self) -> list[str]:
        return self._active_dimensions

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def survivors(self) -> t.Any:
        return relation(self._df).filter(ma.col("__survived")).collect()

    @property
    def non_survivors(self) -> t.Any:
        return (
            relation(self._df).filter(ma.col("__survived").eq(ma.lit(False))).collect()
        )
