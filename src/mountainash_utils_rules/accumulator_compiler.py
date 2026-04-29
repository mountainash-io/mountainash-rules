"""AccumulatorCompiler: compiles coalesce/compatible expressions for the accumulator engine."""

from __future__ import annotations

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_utils_rules.constants import (
    UNKNOWN,
    UNKNOWN_NUMERIC,
    MatchStrategy,
)
from mountainash_utils_rules.dimension import Dimension


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
            case _:
                raise ValueError(
                    f"Strategy {dim.match_strategy.name} not supported by accumulator"
                )

    def compile_coalesce(self, dim: Dimension) -> list[BaseExpressionAPI]:
        """Expression(s) producing the coalesced value from two compatible rules."""
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._coalesce_exact(dim)
            case _:
                raise ValueError(
                    f"Strategy {dim.match_strategy.name} not supported by accumulator"
                )

    def compile_coalesce_na_flag(self, dim: Dimension) -> BaseExpressionAPI:
        """Expression for the coalesced NA flag (1 = both sides don't-care)."""
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
