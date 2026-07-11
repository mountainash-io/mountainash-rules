"""AccumulatorCompiler: compiles coalesce/compatible expressions for the accumulator engine."""

from __future__ import annotations

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.constants import (
    UNKNOWN,
    UNKNOWN_NUMERIC,
    MatchStrategy,
)
from mountainash_rules.dimension import Dimension


class AccumulatorCompiler:
    """Compiles accumulator-specific expressions for dimension pairs.

    Expressions operate on two rule rows: LHS (coalesced combination, co_ prefix)
    and RHS (candidate rule, _rhs suffix from the cross-join).
    """

    def compile_compatible(self, dim: Dimension) -> BaseExpressionAPI:
        """Expression that is True when two rules can coexist on this dimension."""
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._compatible_exact(dim)
            case MatchStrategy.RANGE:
                return self._compatible_range(dim)
            case MatchStrategy.GREATER_THAN | MatchStrategy.LESS_THAN:
                return self._compatible_threshold(dim)
            case _:
                raise ValueError(
                    f"Strategy {dim.match_strategy.name} not supported by accumulator"
                )

    def compile_coalesce(self, dim: Dimension) -> list[BaseExpressionAPI]:
        """Expression(s) producing the coalesced value from two compatible rules."""
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._coalesce_exact(dim)
            case MatchStrategy.RANGE:
                return self._coalesce_range(dim)
            case MatchStrategy.GREATER_THAN:
                return self._coalesce_threshold(dim, ma.greatest)
            case MatchStrategy.LESS_THAN:
                return self._coalesce_threshold(dim, ma.least)
            case _:
                raise ValueError(
                    f"Strategy {dim.match_strategy.name} not supported by accumulator"
                )

    def compile_coalesce_na_flag(self, dim: Dimension) -> BaseExpressionAPI:
        """Expression for the coalesced NA flag (1 = both sides don't-care)."""
        if dim.match_strategy == MatchStrategy.RANGE:
            co_s = ma.col(f"co_{dim.range_min_field}").eq(ma.lit(UNKNOWN_NUMERIC))
            rhs_s = ma.col(f"{dim.range_min_field}_rhs").eq(ma.lit(UNKNOWN_NUMERIC))
            return co_s.__and__(rhs_s).cast(int).alias(f"co_{dim.dimension_name}_na")
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        return co_sentinel.__and__(rhs_sentinel).cast(int).alias(f"co_{field}_na")

    def _sentinel_checks(self, dim: Dimension) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        """Return (co_is_sentinel, rhs_is_sentinel) expressions."""
        field = dim.resolved_rule_field
        sentinel = UNKNOWN_NUMERIC if dim.data_type in (int, float) else UNKNOWN
        co_is_sentinel = ma.col(f"co_{field}").eq(ma.lit(sentinel))
        rhs_is_sentinel = ma.col(f"{field}_rhs").eq(ma.lit(sentinel))
        return co_is_sentinel, rhs_is_sentinel

    def _compatible_exact(self, dim: Dimension) -> BaseExpressionAPI:
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        values_match = ma.col(f"co_{field}").eq(ma.col(f"{field}_rhs"))
        return co_sentinel.__or__(rhs_sentinel).__or__(values_match)

    def _coalesce_exact(self, dim: Dimension) -> list[BaseExpressionAPI]:
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        co_hard = ma.when(co_sentinel).then(None).otherwise(ma.col(f"co_{field}"))
        rhs_hard = ma.when(rhs_sentinel).then(None).otherwise(ma.col(f"{field}_rhs"))
        return [ma.coalesce(co_hard, rhs_hard, ma.col(f"co_{field}")).alias(f"co_{field}")]

    def _range_sentinel_checks(
        self, dim: Dimension,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI, BaseExpressionAPI, BaseExpressionAPI]:
        sentinel = UNKNOWN_NUMERIC
        co_min_s = ma.col(f"co_{dim.range_min_field}").eq(ma.lit(sentinel))
        co_max_s = ma.col(f"co_{dim.range_max_field}").eq(ma.lit(sentinel))
        rhs_min_s = ma.col(f"{dim.range_min_field}_rhs").eq(ma.lit(sentinel))
        rhs_max_s = ma.col(f"{dim.range_max_field}_rhs").eq(ma.lit(sentinel))
        return co_min_s, co_max_s, rhs_min_s, rhs_max_s

    def _compatible_range(self, dim: Dimension) -> BaseExpressionAPI:
        co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
        either_sentinel = co_min_s.__or__(rhs_min_s)
        intervals_overlap = (
            ma.col(f"co_{dim.range_min_field}")
            .lt(ma.col(f"{dim.range_max_field}_rhs"))
            .__and__(
                ma.col(f"co_{dim.range_max_field}")
                .gt(ma.col(f"{dim.range_min_field}_rhs"))
            )
        )
        return either_sentinel.__or__(intervals_overlap)

    def _coalesce_range(self, dim: Dimension) -> list[BaseExpressionAPI]:
        co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)

        new_min = (
            ma.when(co_min_s.__and__(rhs_min_s))
              .then(ma.lit(UNKNOWN_NUMERIC))
            .when(co_min_s)
              .then(ma.col(f"{dim.range_min_field}_rhs"))
            .when(rhs_min_s)
              .then(ma.col(f"co_{dim.range_min_field}"))
            .otherwise(
              ma.greatest(ma.col(f"co_{dim.range_min_field}"), ma.col(f"{dim.range_min_field}_rhs"))
            )
            .alias(f"co_{dim.range_min_field}")
        )

        new_max = (
            ma.when(co_max_s.__and__(rhs_max_s))
              .then(ma.lit(UNKNOWN_NUMERIC))
            .when(co_max_s)
              .then(ma.col(f"{dim.range_max_field}_rhs"))
            .when(rhs_max_s)
              .then(ma.col(f"co_{dim.range_max_field}"))
            .otherwise(
              ma.least(ma.col(f"co_{dim.range_max_field}"), ma.col(f"{dim.range_max_field}_rhs"))
            )
            .alias(f"co_{dim.range_max_field}")
        )

        return [new_min, new_max]

    def _compatible_threshold(self, dim: Dimension) -> BaseExpressionAPI:
        return ma.lit(True)

    def _coalesce_threshold(self, dim: Dimension, combine_fn) -> list[BaseExpressionAPI]:
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        new_val = (
            ma.when(co_sentinel.__and__(rhs_sentinel))
              .then(ma.lit(UNKNOWN_NUMERIC))
            .when(co_sentinel)
              .then(ma.col(f"{field}_rhs"))
            .when(rhs_sentinel)
              .then(ma.col(f"co_{field}"))
            .otherwise(
              combine_fn(ma.col(f"co_{field}"), ma.col(f"{field}_rhs"))
            )
            .alias(f"co_{field}")
        )
        return [new_val]
