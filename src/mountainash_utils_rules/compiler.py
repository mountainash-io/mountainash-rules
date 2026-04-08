"""DimensionCompiler: translates Dimension metadata into expression templates."""

from __future__ import annotations

import polars as pl  # allow: SET_MEMBERSHIP workaround pending t_list_contains upstream

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_utils_rules.constants import (
    CTX_PREFIX,
    UNKNOWN,
    NOT_SET,
    STRING_SENTINELS,
    NUMERIC_SENTINELS,
    MatchStrategy,
)
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata


class DimensionCompiler:
    """Compiles Dimension metadata into backend-agnostic expression templates.

    Each compiled expression references a context placeholder column (__ctx_<name>)
    that the engine populates at evaluation time.
    """

    def compile_dimensions(self, metadata: DimensionsMetadata) -> dict[str, BaseExpressionAPI]:
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
            case MatchStrategy.RANGE:
                return self._compile_range(dim)
            case MatchStrategy.REGEX:
                return self._compile_regex(dim)
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

    def _sentinels_for_type(self, data_type: type) -> set:
        """Return the appropriate sentinel set for a data type."""
        if data_type in (int, float):
            return NUMERIC_SENTINELS
        return STRING_SENTINELS

    def _compile_exact(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return rule_col.t_eq(ctx_col)

    def _compile_not_equal(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return rule_col.t_ne(ctx_col)

    def _compile_greater_than(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return ctx_col.t_gt(rule_col)

    def _compile_less_than(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return ctx_col.t_lt(rule_col)

    def _compile_range(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
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

    def _compile_string_match(self, dim: Dimension, op_name: str) -> BaseExpressionAPI:
        """Shared wrapper for PREFIX/SUFFIX/CONTAINS/REGEX.

        Wraps a boolean-returning string operation in a sentinel-aware
        ternary expression: unknown rule → 0, match → 1, no-match → -1.
        """
        rule_col = ma.col(dim.resolved_rule_field)
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        rule_is_sentinel = (
            rule_col.__eq__(ma.lit(UNKNOWN)) | rule_col.__eq__(ma.lit(NOT_SET))
        )
        match = getattr(ctx_col.str, op_name)(rule_col)
        return ma.when(rule_is_sentinel).then(0).when(match).then(1).otherwise(-1)

    def _compile_prefix(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "starts_with")

    def _compile_suffix(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "ends_with")

    def _compile_contains(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "contains")

    def _compile_regex(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "regex_contains")

    def _compile_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
        """Compile SET_MEMBERSHIP: context value is in the rule's list column."""
        ctx_field = CTX_PREFIX + dim.dimension_name
        rule_field = dim.resolved_rule_field
        ctx_is_sentinel = (
            ma.col(ctx_field).__eq__(ma.lit(UNKNOWN))
            | ma.col(ctx_field).__eq__(ma.lit(NOT_SET))
        )
        match = ma.native(pl.col(rule_field).list.contains(pl.col(ctx_field)))
        return ma.when(ctx_is_sentinel).then(0).when(match).then(1).otherwise(-1)

    def _compile_set_exclusion(self, dim: Dimension) -> BaseExpressionAPI:
        """Compile SET_EXCLUSION: context value is NOT in the rule's list column."""
        ctx_field = CTX_PREFIX + dim.dimension_name
        rule_field = dim.resolved_rule_field
        ctx_is_sentinel = (
            ma.col(ctx_field).__eq__(ma.lit(UNKNOWN))
            | ma.col(ctx_field).__eq__(ma.lit(NOT_SET))
        )
        not_in = ma.native(~pl.col(rule_field).list.contains(pl.col(ctx_field)))
        return ma.when(ctx_is_sentinel).then(0).when(not_in).then(1).otherwise(-1)
