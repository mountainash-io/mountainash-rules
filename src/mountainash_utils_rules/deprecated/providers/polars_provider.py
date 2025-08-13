"""
Polars provider for high-performance rule evaluation.

This module implements the PolarsProvider which uses Polars DataFrames
and integrates with the ternary filter visitor for expression building.
"""

import polars as pl
import logging
from typing import Any, Dict, List, Optional
from mountainash_dataframes import BaseDataFrame, IbisDataFrame
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.constants import RuleTrinaryFlags, MatchStrategy
# Now using mountainash-dataframes ternary system instead of old dataframe_ternary_filters
from mountainash_dataframes.utils.expression_builders.ternary import (
    TernaryColumnExpression,
    TernaryLogicalExpression,
    PolarsTernaryExpressionVisitor,
    TernaryExpressionBuilder
)
from .base import RuleEvaluationProvider


logger = logging.getLogger(__name__)


class PolarsProvider(RuleEvaluationProvider):
    """
    High-performance Polars provider using ternary filters.
    
    This provider leverages Polars' columnar data processing and lazy evaluation
    capabilities for maximum performance. It integrates with the ternary filter
    visitor pattern for clean expression building.
    
    Key features:
    - Lazy evaluation with query optimization
    - Vectorized operations for performance
    - Integration with dataframe_ternary_filters
    - Expression caching for repeated patterns
    """
    
    def __init__(self, enable_caching: bool = True, enable_optimization: bool = True):
        """
        Initialize the Polars provider.
        
        Args:
            enable_caching: Whether to enable expression caching
            enable_optimization: Whether to enable query optimization
        """
        self.enable_caching = enable_caching
        self.enable_optimization = enable_optimization
        
        # Initialize the ternary filter visitor for Polars
        self.visitor = RuleTrinaryFilterVisitor(
            backend='polars',
            enable_caching=enable_caching,
            enable_optimization=enable_optimization
        )
        
        logger.info(f"PolarsProvider initialized: caching={enable_caching}, "
                   f"optimization={enable_optimization}")
    
    def get_filter_visitor(self) -> RuleTrinaryFilterVisitor:
        """Get the Polars-configured filter visitor."""
        return self.visitor
    
    def materialize_rules(self, rules: BaseDataFrame) -> pl.DataFrame:
        """
        Convert BaseDataFrame to Polars DataFrame.
        
        Args:
            rules: BaseDataFrame containing rules
            
        Returns:
            pl.DataFrame with materialized rules
            
        Raises:
            ValueError: If conversion fails
        """
        try:
            # Try multiple conversion paths for flexibility
            if hasattr(rules, 'to_polars'):
                logger.debug("Converting rules using to_polars method")
                return rules.to_polars()
            elif hasattr(rules, 'to_pandas'):
                logger.debug("Converting rules via pandas")
                return pl.from_pandas(rules.to_pandas())
            elif hasattr(rules, 'ibis_table'):
                logger.debug("Converting rules from ibis table via pandas")
                return pl.from_pandas(rules.ibis_table.to_pandas())
            else:
                # Try direct conversion as last resort
                logger.debug("Attempting direct polars conversion")
                return pl.DataFrame(rules)
        except Exception as e:
            raise ValueError(f"Failed to materialize rules for Polars processing: {e}")
    
    def execute_evaluation(self, 
                          rules_data: pl.DataFrame,
                          context_values: Dict[str, Any],
                          dimensions: List[Dimension]) -> pl.DataFrame:
        """
        Execute rule evaluation using ternary filter visitor.
        
        This method builds match conditions using the ternary filter pattern
        and executes them efficiently with Polars.
        
        Args:
            rules_data: Polars DataFrame with rules
            context_values: Dictionary of dimension values from context
            dimensions: List of Dimension objects
            
        Returns:
            Polars DataFrame with evaluation results and 'keep' column
        """
        logger.debug(f"Executing evaluation for {len(dimensions)} dimensions")
        
        # Build match conditions using ternary filters
        conditions = []
        dimension_expressions = []
        
        for dimension in dimensions:
            dim_name = dimension.dimension_name
            
            if dim_name in context_values:
                # Create rule match condition for this dimension
                condition = create_rule_match_condition(
                    dimension=dimension,
                    context_value=context_values[dim_name],
                    enable_ternary=True
                )
                conditions.append(condition)
                
                # Generate the Polars expression through the visitor
                expr = condition.accept(self.visitor)
                dimension_expressions.append(expr.alias(f"{dim_name}_match"))
                
                logger.debug(f"Created match condition for {dim_name} with "
                           f"strategy {dimension.match_strategy}")
            else:
                # Missing context - create unknown expression
                unknown_expr = pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN).alias(f"{dim_name}_match")
                dimension_expressions.append(unknown_expr)
                logger.debug(f"Missing context for {dim_name}, using UNKNOWN")
        
        # Combine all conditions using ternary ALL_TRUE logic
        if conditions:
            combined_condition = create_ternary_all_condition(
                conditions=conditions,
                enable_optimization=self.enable_optimization
            )
            
            # Generate the combined expression
            final_expression = combined_condition.accept(self.visitor)
        else:
            # No conditions - all unknown
            final_expression = pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
        
        # Create keep flag based on final match result
        keep_expression = (final_expression == RuleTrinaryFlags.PRIME_TRUE).alias("keep")
        
        # Execute the evaluation with all expressions
        result = rules_data.with_columns(
            dimension_expressions + [
                final_expression.alias("final_match"),
                keep_expression
            ]
        )
        
        logger.debug(f"Evaluation complete: {len(result)} rules processed")
        
        return result
    
    def to_base_dataframe(self, result: pl.DataFrame) -> BaseDataFrame:
        """
        Convert Polars result back to BaseDataFrame.
        
        Args:
            result: Polars DataFrame with evaluation results
            
        Returns:
            BaseDataFrame for compatibility with the system
        """
        try:
            # Convert to IbisDataFrame with Polars backend
            return IbisDataFrame(result, ibis_backend_schema='polars')
        except Exception as e:
            logger.warning(f"Failed to create IbisDataFrame with Polars backend: {e}")
            # Fallback to pandas conversion
            try:
                pandas_df = result.to_pandas()
                return IbisDataFrame(pandas_df, ibis_backend_schema='pandas')
            except Exception as e2:
                raise ValueError(f"Failed to convert Polars result to BaseDataFrame: {e2}")
    
    @property
    def backend_name(self) -> str:
        """Return the backend name."""
        return "polars"
    
    @property
    def supports_lazy_evaluation(self) -> bool:
        """Polars supports lazy evaluation."""
        return True
    
    @property
    def supports_parallel_processing(self) -> bool:
        """Polars supports parallel processing."""
        return True
    
    def clear_caches(self) -> None:
        """Clear the visitor's expression cache."""
        if self.visitor and hasattr(self.visitor, 'clear_cache'):
            self.visitor.clear_cache()
            logger.debug("Cleared Polars provider caches")
    
    def get_performance_hints(self) -> Dict[str, Any]:
        """Get Polars-specific performance hints."""
        hints = super().get_performance_hints()
        hints.update({
            'recommended_chunk_size': 10000,
            'supports_simd': True,
            'columnar_processing': True,
            'zero_copy_possible': True
        })
        return hints