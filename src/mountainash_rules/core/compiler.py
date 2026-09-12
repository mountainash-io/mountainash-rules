"""DimensionCompiler: translates Dimension metadata into expression templates."""

from __future__ import annotations

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import (
    CTX_PREFIX,
    UNKNOWN,
    NOT_SET,
    DataType,
    MatchStrategy,
    sentinels_for,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.set_wildcard import (
    normalize_set_expr,
    set_wildcard_predicate,
)


class DimensionCompiler:
    """Compiles Dimension metadata into backend-agnostic expression templates.

    Each compiled expression references a context placeholder column (__ctx_<name>)
    that the engine populates at evaluation time.
    """

    def compile_dimensions(
        self, metadata: DimensionsMetadata
    ) -> dict[str, BaseExpressionAPI]:
        """Compile all dimensions in a metadata set to expression templates."""
        return {
            dim.dimension_name: self.compile_dimension(dim)
            for dim in metadata.dimensions
        }

    def compile_dimension(self, dim: Dimension) -> BaseExpressionAPI:
        """Compile a single dimension to an expression template."""
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._compile_exact(dim)
            case MatchStrategy.EXACT_KEY:
                return self._compile_exact_key(dim)
            case MatchStrategy.RANGE:
                return self._compile_range(dim)
            case MatchStrategy.REGEX:
                return self._compile_regex_per_row(dim)
            case MatchStrategy.CONTEXT_REGEX:
                return self._compile_context_regex(dim)
            case MatchStrategy.NOT_EQUAL:
                return self._compile_not_equal(dim)
            case MatchStrategy.GREATER_THAN:
                return self._compile_greater_than(dim)
            case MatchStrategy.LESS_THAN:
                return self._compile_less_than(dim)
            case MatchStrategy.PREFIX:
                return self._compile_prefix(dim)
            case MatchStrategy.SUFFIX:
                return self._compile_suffix(dim)
            case MatchStrategy.CONTAINS:
                return self._compile_contains(dim)
            case MatchStrategy.SET_MEMBERSHIP:
                return self._compile_set_membership(dim)
            case MatchStrategy.SET_EXCLUSION:
                return self._compile_set_exclusion(dim)
            case _:
                raise ValueError(f"Unknown match strategy: {dim.match_strategy}")

    def _compile_exact(self, dim: Dimension) -> BaseExpressionAPI:
        if dim.data_type is DataType.BOOL:
            return self._compile_bool_ternary(dim, "__eq__")
        sentinels = sentinels_for(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return rule_col.t_eq(ctx_col)

    def _compile_exact_key(self, dim: Dimension) -> BaseExpressionAPI:
        """Rule-side-wildcard-only exact match (partition-key routing).

        Only the rule side has a wildcard (UNKNOWN sentinel; null for
        bool). A context-side sentinel is non-concrete:
        specific keys must never match an unknown/unset context.
        """
        rule_col = ma.col(dim.resolved_rule_field)
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        if dim.data_type is DataType.BOOL:
            # Boolean keys use null as their only wildcard representation.
            wildcard = rule_col.is_null()
        else:
            wildcard = rule_col.__eq__(ma.lit(unknown_sentinel_for(dim.data_type)))
        return (
            ma.when(wildcard)
            .then(0)
            .when(self._context_is_nonconcrete(dim))
            .then(-1)
            .when(rule_col.__eq__(ctx_col))
            .then(1)
            .otherwise(-1)
        )

    def _compile_not_equal(self, dim: Dimension) -> BaseExpressionAPI:
        if dim.data_type is DataType.BOOL:
            return self._compile_bool_ternary(dim, "__ne__")
        sentinels = sentinels_for(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return rule_col.t_ne(ctx_col)

    def _compile_bool_ternary(self, dim: Dimension, op_name: str) -> BaseExpressionAPI:
        """Bool dimensions: null (rule or context) is the don't-care state."""
        rule_col = ma.col(dim.resolved_rule_field)
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        either_null = rule_col.is_null().__or__(ctx_col.is_null())
        compared = getattr(rule_col, op_name)(ctx_col)
        return ma.when(either_null).then(0).when(compared).then(1).otherwise(-1)

    def _compile_greater_than(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = sentinels_for(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return ctx_col.t_gt(rule_col)

    def _compile_less_than(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = sentinels_for(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return ctx_col.t_lt(rule_col)

    def _compile_range(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = sentinels_for(dim.data_type)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        min_col = ma.t_col(dim.range_min_field, unknown=sentinels)
        max_col = ma.t_col(dim.range_max_field, unknown=sentinels)

        if dim.range_min_inclusive:
            lower = min_col.t_le(ctx_col)
        else:
            lower = min_col.t_lt(ctx_col)

        if dim.range_max_inclusive:
            upper = max_col.t_ge(ctx_col)
        else:
            upper = max_col.t_gt(ctx_col)

        return lower.t_and(upper)

    def _context_is_nonconcrete(self, dim: Dimension) -> BaseExpressionAPI:
        """Context nulls and reserved markers cannot satisfy a concrete predicate."""
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        missing = ctx_col.is_null()
        if dim.data_type is not DataType.BOOL:
            for sentinel in sentinels_for(dim.data_type):
                missing = missing | ctx_col.eq(ma.lit(sentinel))
        return missing

    def _compile_string_match(self, dim: Dimension, op_name: str) -> BaseExpressionAPI:
        """Shared wrapper for PREFIX/SUFFIX/CONTAINS/REGEX.

        Wraps a boolean-returning string operation in a sentinel-aware
        ternary expression: unknown rule/context → 0, match → 1, no-match → -1.
        """
        rule_col = ma.col(dim.resolved_rule_field)
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        rule_is_sentinel = rule_col.__eq__(ma.lit(UNKNOWN)) | rule_col.__eq__(
            ma.lit(NOT_SET)
        )
        match = getattr(ctx_col.str, op_name)(rule_col)
        unknown = rule_is_sentinel | self._context_is_nonconcrete(dim)
        return ma.when(unknown).then(0).when(match).then(1).otherwise(-1)

    def _compile_prefix(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "starts_with")

    def _compile_suffix(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "ends_with")

    def _compile_contains(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "contains")

    def _compile_regex_per_row(self, dim: Dimension) -> BaseExpressionAPI:
        """Per-row REGEX: the rule column holds the pattern for each rule.

        Sentinel patterns or non-concrete contexts act as don't-care (0);
        otherwise search semantics apply against the context per rule row.

        mountainash's regex_contains only accepts a literal pattern, so this
        uses a Polars-native expression pending upstream support for
        column-valued patterns (same workaround precedent as
        SET_MEMBERSHIP/SET_EXCLUSION before t_is_in landed). Non-polars
        backends fail at evaluation with mountainash's native-expression
        error.
        """
        import polars as pl  # allow: native fallback pending mountainash column-pattern regex_contains

        rule_field = dim.resolved_rule_field
        ctx_name = CTX_PREFIX + dim.dimension_name
        rule_col = ma.col(rule_field)
        rule_is_sentinel = rule_col.__eq__(ma.lit(UNKNOWN)) | rule_col.__eq__(
            ma.lit(NOT_SET)
        )
        match = ma.native(pl.col(ctx_name).str.contains(pl.col(rule_field)))
        unknown = rule_is_sentinel | self._context_is_nonconcrete(dim)
        return ma.when(unknown).then(0).when(match).then(1).otherwise(-1)

    def _compile_context_regex(self, dim: Dimension) -> BaseExpressionAPI:
        """CONTEXT_REGEX dimensions use a literal pattern from metadata.

        All rules in the engine share the same ternary outcome for a
        CONTEXT_REGEX dimension — it acts as a global context validator.
        There is no unknown state: non-concrete context fails the guard,
        regardless of whether the pattern matches the marker's spelling.
        """
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        match = ctx_col.str.regex_contains(dim.regex_pattern)
        return (
            ma.when(self._context_is_nonconcrete(dim))
            .then(-1)
            .when(match)
            .then(1)
            .otherwise(-1)
        )

    def _compile_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
        """SET_MEMBERSHIP: context value in the rule list; wildcard rule -> ternary 0."""
        rule_col = normalize_set_expr(dim, ma.col(dim.resolved_rule_field))
        ctx_col = ma.t_col(
            CTX_PREFIX + dim.dimension_name, unknown=sentinels_for(dim.data_type)
        )
        is_wild = set_wildcard_predicate(dim, rule_col)
        return ma.when(is_wild).then(0).otherwise(ctx_col.t_is_in(rule_col))

    def _compile_set_exclusion(self, dim: Dimension) -> BaseExpressionAPI:
        """SET_EXCLUSION: context value NOT in the rule list; wildcard rule -> ternary 0."""
        rule_col = normalize_set_expr(dim, ma.col(dim.resolved_rule_field))
        ctx_col = ma.t_col(
            CTX_PREFIX + dim.dimension_name, unknown=sentinels_for(dim.data_type)
        )
        is_wild = set_wildcard_predicate(dim, rule_col)
        return ma.when(is_wild).then(0).otherwise(ctx_col.t_is_not_in(rule_col))
