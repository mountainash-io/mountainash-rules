"""DimensionCompiler: translates Dimension metadata into expression templates."""

from __future__ import annotations

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_utils_rules.constants import (
    CTX_PREFIX,
    UNKNOWN,
    UNKNOWN_NUMERIC,
    NOT_SET,
    NOT_SET_NUMERIC,
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

    def _compile_range(self, dim: Dimension) -> BaseExpressionAPI:
        raise NotImplementedError("RANGE compilation is Task 5")

    def _compile_regex(self, dim: Dimension) -> BaseExpressionAPI:
        raise NotImplementedError("REGEX compilation is Task 6")
