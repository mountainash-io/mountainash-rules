"""
Enhanced TernaryRuleProcessor - One-Shot Evaluation Using ExpressionBuilder

This module implements the original goal: use mountainash-dataframes TernaryExpressionBuilder
to create a single complex expression that evaluates all dimensions in one operation,
eliminating the need for iterative mutate() calls.

Key Innovation:
- Build list of TernaryColumnExpression objects for each dimension
- Combine them with TernaryExpressionBuilder.and_() into single complex expression
- Evaluate once using the PolarsTernaryExpressionVisitor
- Single mutate() call instead of M+2 calls

Benefits:
- Dramatic reduction in intermediate columns
- Better query optimization by backend
- Maintains original dimension-by-dimension logic in expression form
- True vectorization without losing soft/hard match tracking
"""

import logging
from typing import Dict, List, Any, Optional
from functools import lru_cache
import re

import polars as pl
from mountainash_dataframes import BaseDataFrame
from mountainash_dataframes.utils.expressions.ternary import (
    TernaryColumnExpression,
    TernaryLogicalExpression,
    PolarsTernaryExpressionVisitor,
    TernaryExpressionBuilder
)
from mountainash_dataframes.utils.expressions.ternary.constants import TernaryLogicValues
from mountainash_dataframes.utils.expressions.ternary.value_mappings import TernaryValueMapper, configure_ternary_mappings
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_utils_rules.dimension import Dimension

logger = logging.getLogger(__name__)


class EnhancedTernaryRuleProcessor:
    """
    One-shot rule evaluation using TernaryExpressionBuilder.

    This processor builds a single complex ternary expression that evaluates
    all dimensions simultaneously, eliminating the iterative approach while
    maintaining all the logic from the original dimension-by-dimension processing.
    """

    def __init__(self, rules: BaseDataFrame, dimensions: List[Dimension]):
        self.dimensions = dimensions

        # Initialize ternary expression visitor with mountainash-utils-rules mappings
        custom_mapper = TernaryValueMapper(configure_ternary_mappings(
            string_unknown="<NA>",
            string_not_set="<NOT_SET>",
            numeric_unknown=-999999999,
            numeric_not_set=-999999998
        ))
        self.ternary_visitor = PolarsTernaryExpressionVisitor(custom_mapper)

        # Convert rules to polars for processing
        self.rules_df = self._materialize_rules(rules)

        logger.info(f"EnhancedTernaryRuleProcessor initialized: {len(self.rules_df)} rules, {len(dimensions)} dimensions")

    def _materialize_rules(self, rules: BaseDataFrame) -> pl.DataFrame:
        """Convert BaseDataFrame to polars DataFrame for processing."""
        try:
            if hasattr(rules, 'to_polars'):
                return rules.to_polars()
            elif hasattr(rules, 'to_pandas'):
                return pl.from_pandas(rules.to_pandas())
            elif hasattr(rules, 'ibis_table'):
                return pl.from_pandas(rules.ibis_table.to_pandas())
            else:
                raise ValueError("Unable to convert rules to polars DataFrame")
        except Exception as e:
            raise ValueError(f"Failed to materialize rules: {e}")

    @lru_cache(maxsize=1000)
    def _compile_regex(self, pattern: str) -> re.Pattern:
        """Compile and cache regex patterns for performance optimization."""
        return re.compile(pattern)

    def evaluate_context_one_shot(self, context_values: Dict[str, Any]) -> BaseDataFrame:
        """
        One-shot evaluation using TernaryExpressionBuilder and visitor pattern.

        This demonstrates the TRUE architectural improvement:
        1. Build TernaryColumnExpression for each dimension
        2. Combine with TernaryExpressionBuilder.and_()
        3. Evaluate once using PolarsTernaryExpressionVisitor
        4. Single complex expression instead of M separate mutate() calls

        Args:
            context_values: Dictionary of dimension names to context values

        Returns:
            BaseDataFrame with evaluation results and 'keep' column
        """

        # Step 1: Build TernaryColumnExpression for each dimension
        dimension_expressions = []
        dimension_names = []

        for dimension in self.dimensions:
            dim_name = dimension.dimension_name
            dimension_names.append(dim_name)

            if dim_name not in context_values:
                # Missing context - this dimension evaluates to UNKNOWN
                dimension_expressions.append(TernaryLogicalExpression.always_unknown())
                continue

            context_value = context_values[dim_name]

            # Build dimension expression based on match strategy
            if dimension.match_strategy == MatchStrategy.EXACT:
                dim_expr = TernaryExpressionBuilder.eq(dim_name, context_value)

            elif dimension.match_strategy == MatchStrategy.RANGE:
                min_field = dimension.range_min_field or f"{dim_name}_MIN"
                max_field = dimension.range_max_field or f"{dim_name}_MAX"

                # Range match: min_field <= context_value <= max_field
                # This combines the original filter_rule_unknown + filter_match logic
                dim_expr = TernaryExpressionBuilder.and_(
                    TernaryExpressionBuilder.le(min_field, context_value),
                    TernaryExpressionBuilder.ge(max_field, context_value)
                )

            elif dimension.match_strategy == MatchStrategy.REGEX:
                # For regex, we'll use exact match as fallback since regex isn't built-in
                # In a full implementation, this would extend TernaryExpressionBuilder
                dim_expr = TernaryExpressionBuilder.eq(dim_name, context_value)

            else:
                # Unknown match strategy - treat as UNKNOWN
                dim_expr = TernaryLogicalExpression.always_unknown()

            dimension_expressions.append(dim_expr)

        # Step 2: Combine all dimension expressions with soft AND logic
        # This replicates the original engine's soft matching behavior
        combined_expression = TernaryExpressionBuilder.and_(*dimension_expressions)

        # Step 3: Convert TernaryExpression to callable using visitor
        expression_callable = combined_expression.accept(self.ternary_visitor)

        # Step 4: ONE-SHOT EVALUATION - Single complex expression evaluation
        # The expression_callable is a lambda that takes a DataFrame and returns a polars expression
        dummy_df = None  # We'll pass None since the expressions use pl.col() which works without df context

        try:
            # Get the polars expression by calling the lambda
            combined_polars_expr = expression_callable(dummy_df)

            # Also get individual dimension expressions for metrics
            individual_expressions = []
            for dim_expr, dim_name in zip(dimension_expressions, dimension_names):
                dim_callable = dim_expr.accept(self.ternary_visitor)
                dim_polars_expr = dim_callable(dummy_df)
                individual_expressions.append((dim_polars_expr, dim_name))

        except Exception as e:
            # Fallback to direct polars implementation if visitor fails
            print(f"⚠️ TernaryExpressionBuilder failed: {e}")
            return self._fallback_direct_polars_evaluation(context_values)

        # Single evaluation with comprehensive metrics calculation
        result_df = (
            self.rules_df
            .with_columns([
                # Evaluate the combined expression
                combined_polars_expr.alias("combined_match_result"),

                # Also evaluate individual dimensions for metrics
                *[
                    dim_expr.alias(f"{dim_name}_match")
                    for dim_expr, dim_name in individual_expressions
                ]
            ])
            .with_columns([
                # Calculate metrics from individual dimension results
                pl.sum_horizontal([
                    (pl.col(f"{dim_name}_match") == TernaryLogicValues.PRIME_TRUE).cast(pl.Int32)
                    for _, dim_name in individual_expressions
                ]).alias("cumu_hard_match_count"),

                pl.sum_horizontal([
                    (pl.col(f"{dim_name}_match") == TernaryLogicValues.PRIME_UNKNOWN).cast(pl.Int32)
                    for _, dim_name in individual_expressions
                ]).alias("cumu_soft_match_count"),

                pl.lit(len(self.dimensions)).alias("cumu_dimension_count"),

                # Keep logic: rule is kept if combined result is not FALSE
                # This matches original engine's soft matching: UNKNOWN and TRUE both kept
                (pl.col("combined_match_result") != TernaryLogicValues.PRIME_FALSE).alias("keep"),

                # For compatibility, mark as dropped if combined result is FALSE
                pl.when(pl.col("combined_match_result") == TernaryLogicValues.PRIME_FALSE)
                .then(pl.lit(True))
                .otherwise(pl.lit(None))
                .alias("dropped")
            ])
            .with_columns([
                # Calculate priority (matches original engine logic)
                pl.int_range(pl.len()).alias("row_number")
            ])
            .with_columns([
                pl.col("row_number").rank(
                    method="ordinal",
                    descending=False
                ).over(
                    pl.col("cumu_hard_match_count").sort(descending=True),
                    pl.col("cumu_soft_match_count").sort(descending=True),
                    pl.col("row_number").sort(descending=False)
                ).alias("priority")
            ])
            .drop([
                "row_number",
                "combined_match_result",
                *[f"{dim_name}_match" for _, dim_name in individual_expressions]  # Clean up temp columns
            ])
        )

        # Return the polars DataFrame directly
        return result_df

    def _fallback_direct_polars_evaluation(self, context_values: Dict[str, Any]) -> BaseDataFrame:
        """Fallback to direct polars implementation if TernaryExpressionBuilder fails."""

        # Build individual polars expressions for each dimension
        dimension_exprs = []
        dimension_names = []

        for dimension in self.dimensions:
            dim_name = dimension.dimension_name
            dimension_names.append(dim_name)

            if dim_name not in context_values:
                # Missing context - UNKNOWN (5)
                expr = pl.lit(5).alias(f"{dim_name}_result")
            else:
                context_value = context_values[dim_name]

                if dimension.match_strategy == MatchStrategy.EXACT:
                    # EXACT match logic with UNKNOWN handling
                    expr = pl.when(
                        pl.col(dim_name).is_null() | (pl.col(dim_name) == "<NA>")
                    ).then(
                        pl.lit(5)  # UNKNOWN
                    ).when(
                        pl.col(dim_name) == context_value
                    ).then(
                        pl.lit(3)  # TRUE
                    ).otherwise(
                        pl.lit(2)  # FALSE
                    ).alias(f"{dim_name}_result")

                elif dimension.match_strategy == MatchStrategy.RANGE:
                    min_field = dimension.range_min_field or f"{dim_name}_MIN"
                    max_field = dimension.range_max_field or f"{dim_name}_MAX"

                    expr = pl.when(
                        (pl.col(min_field) == -999999999) | (pl.col(max_field) == -999999999)
                    ).then(
                        pl.lit(5)  # UNKNOWN
                    ).when(
                        (pl.col(min_field) <= context_value) & (context_value <= pl.col(max_field))
                    ).then(
                        pl.lit(3)  # TRUE
                    ).otherwise(
                        pl.lit(2)  # FALSE
                    ).alias(f"{dim_name}_result")

                elif dimension.match_strategy == MatchStrategy.REGEX:
                    # Simple regex handling
                    expr = pl.when(
                        pl.col(dim_name).is_null() | (pl.col(dim_name) == "<NA>")
                    ).then(
                        pl.lit(5)  # UNKNOWN
                    ).when(
                        pl.col(dim_name).str.contains(f"^{context_value}.*", strict=False)
                    ).then(
                        pl.lit(3)  # TRUE
                    ).otherwise(
                        pl.lit(2)  # FALSE
                    ).alias(f"{dim_name}_result")
                else:
                    expr = pl.lit(5).alias(f"{dim_name}_result")  # UNKNOWN for unsupported

            dimension_exprs.append(expr)

        # ONE-SHOT EVALUATION: Single with_columns call for all dimensions
        result_df = (
            self.rules_df
            .with_columns(dimension_exprs)  # Evaluate ALL dimensions at once
            .with_columns([
                # Calculate metrics in single operation
                pl.sum_horizontal([
                    (pl.col(f"{dim_name}_result") == 3).cast(pl.Int32)  # TRUE count
                    for dim_name in dimension_names
                ]).alias("cumu_hard_match_count"),

                pl.sum_horizontal([
                    (pl.col(f"{dim_name}_result") == 5).cast(pl.Int32)  # UNKNOWN count
                    for dim_name in dimension_names
                ]).alias("cumu_soft_match_count"),

                pl.lit(len(self.dimensions)).alias("cumu_dimension_count"),

                # Soft match logic: keep if ANY dimension is not FALSE (2)
                pl.any_horizontal([
                    pl.col(f"{dim_name}_result") != 2  # Not FALSE
                    for dim_name in dimension_names
                ]).alias("keep"),

                # Dropped: TRUE if ALL dimensions are FALSE
                pl.when(
                    pl.all_horizontal([
                        pl.col(f"{dim_name}_result") == 2  # All FALSE
                        for dim_name in dimension_names
                    ])
                ).then(pl.lit(True)).otherwise(pl.lit(None)).alias("dropped")
            ])
            .with_columns([
                # Priority calculation (same as original)
                pl.int_range(pl.len()).alias("row_number")
            ])
            .with_columns([
                pl.col("row_number").rank(
                    method="ordinal",
                    descending=False
                ).over(
                    pl.col("cumu_hard_match_count").sort(descending=True),
                    pl.col("cumu_soft_match_count").sort(descending=True),
                    pl.col("row_number").sort(descending=False)
                ).alias("priority")
            ])
            .drop([
                "row_number",
                *[f"{dim_name}_result" for dim_name in dimension_names]  # Clean up temp columns
            ])
        )

        # Return the polars DataFrame directly
        return result_df

    def _create_regex_expression(self, dim_name: str, context_value: str) -> TernaryColumnExpression:
        """
        Create a custom ternary expression for regex matching.

        Note: This is a simplified approach. In a full implementation, you might
        extend TernaryExpressionBuilder to support regex operations natively.
        """
        # For now, we'll create a custom column expression that the visitor can handle
        # This would need to be extended in the visitor to handle regex operations
        return TernaryExpressionBuilder.eq(dim_name, context_value)  # Fallback to exact match

    def _convert_to_base_dataframe(self, polars_df: pl.DataFrame) -> BaseDataFrame:
        """Convert polars DataFrame back to BaseDataFrame."""
        # This would depend on your BaseDataFrame implementation
        # For now, return the polars DataFrame directly
        return polars_df


def create_enhanced_ternary_engine(rules: BaseDataFrame,
                                  dimensions: List[Dimension]) -> EnhancedTernaryRuleProcessor:
    """
    Factory function to create an enhanced ternary rule processor.

    Args:
        rules: BaseDataFrame containing rules to evaluate
        dimensions: List of Dimension objects defining match strategies

    Returns:
        EnhancedTernaryRuleProcessor configured for one-shot evaluation
    """
    return EnhancedTernaryRuleProcessor(rules, dimensions)
