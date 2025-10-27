"""
DataFrameVectorizedRulesEngine: DataFrameRuleProcessor Implementation

Enhanced rule processor using mountainash-dataframes BaseDataFrame operations while
maintaining revolutionary performance through strategic framework utilization and
prime-based ternary logic optimization.

Phase 4A: Foundation Components - DataFrameRuleProcessor Core Logic
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

import polars as pl
# from mountainash_dataframes import BaseDataFrame, IbisDataFrame
from mountainash_dataframes.utils.dataframe_filters import FilterCondition

from mountainash_utils_rules.constants import RuleTrinaryFlags, MatchStrategy
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.dataframe_ternary_filters import (
    RuleTrinaryFilterVisitor,
    TernaryCondition,
    RuleMatchCondition,
    TernaryLogicType,
    create_ternary_filter_visitor,
    create_rule_match_condition,
    create_ternary_all_condition
)


logger = logging.getLogger(__name__)


@dataclass
class DataFrameProcessorConfig:
    """Configuration for DataFrameRuleProcessor with performance optimization settings."""

    # Framework integration settings
    use_framework_filtering: bool = True
    use_ternary_logic: bool = True
    backend_preference: str = 'polars'  # 'polars', 'ibis', 'auto'

    # Performance optimization
    enable_parallel_processing: bool = True
    max_worker_threads: int = 4
    enable_expression_caching: bool = True
    enable_lazy_evaluation: bool = True

    # Memory management
    chunk_processing: bool = False
    chunk_size_mb: int = 100
    memory_optimization: bool = True

    # Advanced optimizations
    enable_selectivity_analysis: bool = True
    enable_early_termination: bool = True
    selectivity_sample_size: int = 100

    # Framework-specific settings
    polars_lazy_optimization: bool = True
    ibis_query_optimization: bool = True

    # Monitoring and debugging
    performance_monitoring: bool = True
    debug_expression_generation: bool = False


@dataclass
class ProcessingStats:
    """Performance statistics for rule processing operations."""

    total_evaluations: int = 0
    total_execution_time: float = 0.0
    average_execution_time: float = 0.0

    # Framework utilization
    framework_operations: int = 0
    direct_operations: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    # Performance optimization
    expressions_cached: int = 0
    parallel_operations: int = 0
    early_terminations: int = 0

    # Resource usage
    peak_memory_mb: float = 0.0
    total_rows_processed: int = 0

    def update_execution_time(self, execution_time: float) -> None:
        """Update execution time statistics."""
        self.total_evaluations += 1
        self.total_execution_time += execution_time
        self.average_execution_time = self.total_execution_time / self.total_evaluations

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary."""
        cache_hit_ratio = 0.0
        if (self.cache_hits + self.cache_misses) > 0:
            cache_hit_ratio = self.cache_hits / (self.cache_hits + self.cache_misses)

        framework_ratio = 0.0
        total_ops = self.framework_operations + self.direct_operations
        if total_ops > 0:
            framework_ratio = self.framework_operations / total_ops

        return {
            "evaluations": self.total_evaluations,
            "avg_execution_time_ms": self.average_execution_time * 1000,
            "total_execution_time": self.total_execution_time,
            "cache_hit_ratio": cache_hit_ratio,
            "framework_utilization": framework_ratio,
            "parallel_operations": self.parallel_operations,
            "early_terminations": self.early_terminations,
            "peak_memory_mb": self.peak_memory_mb,
            "rows_processed": self.total_rows_processed
        }


@dataclass
class RuleDimensionProfile:
    """Profile for rule dimension selectivity and performance characteristics."""

    dimension_name: str
    match_strategy: MatchStrategy
    estimated_selectivity: float = 0.5  # 0.0 = very selective, 1.0 = matches everything
    avg_evaluation_time_ns: float = 0.0
    complexity_score: float = 1.0
    optimization_opportunities: List[str] = field(default_factory=list)

    def update_performance(self, execution_time_ns: float) -> None:
        """Update performance metrics for this dimension."""
        if self.avg_evaluation_time_ns == 0.0:
            self.avg_evaluation_time_ns = execution_time_ns
        else:
            # Exponential moving average
            self.avg_evaluation_time_ns = 0.9 * self.avg_evaluation_time_ns + 0.1 * execution_time_ns


class DataFrameRuleProcessor:
    """
    Enhanced rule processor using mountainash-dataframes BaseDataFrame operations.

    This processor leverages the sophisticated filtering capabilities of mountainash-dataframes
    while maintaining revolutionary performance through strategic framework utilization and
    prime-based ternary logic optimization.

    Key Features:
    - BaseDataFrame-native operations maintaining abstraction benefits
    - Prime-based ternary logic for mathematical precision
    - Strategic framework usage preserving vectorized performance
    - Comprehensive caching and optimization strategies
    - Performance monitoring and adaptive optimization

    Args:
        rules: BaseDataFrame containing rules to evaluate
        dimensions: List of dimension metadata defining match strategies
        config: Configuration for performance optimization settings

    Examples:
        >>> from mountainash_dataframes import IbisDataFrame
        >>> rules_df = IbisDataFrame(rules_data, ibis_backend_schema='polars')
        >>> processor = DataFrameRuleProcessor(rules_df, dimensions)
        >>> result = processor.evaluate_context_dataframe_vectorized(context_values)
    """

    def __init__(self,
                 rules: BaseDataFrame,
                 dimensions: List[Dimension],
                 config: Optional[DataFrameProcessorConfig] = None):

        self.config = config or DataFrameProcessorConfig()
        self.dimensions = dimensions
        self.rules = rules

        # Initialize ternary filter visitor for framework integration
        self.ternary_visitor = create_ternary_filter_visitor(
            backend=self.config.backend_preference,
            enable_caching=self.config.enable_expression_caching,
            enable_optimization=True
        )

        # Performance tracking
        self.stats = ProcessingStats()
        self.dimension_profiles: Dict[str, RuleDimensionProfile] = {}

        # Initialize dimension profiles
        self._initialize_dimension_profiles()

        # Optimization state
        self._optimization_cache: Dict[str, Any] = {}
        self._selectivity_analysis_cache: Dict[str, float] = {}

        logger.info(f"DataFrameRuleProcessor initialized: {self.rules.count()} rules, "
                   f"{len(dimensions)} dimensions, framework={self.config.use_framework_filtering}")

    def _initialize_dimension_profiles(self) -> None:
        """Initialize performance profiles for each dimension."""
        for dimension in self.dimensions:
            profile = RuleDimensionProfile(
                dimension_name=dimension.dimension_name,
                match_strategy=dimension.match_strategy,
                estimated_selectivity=0.5,  # Will be updated through analysis
                complexity_score=self._calculate_dimension_complexity(dimension)
            )
            self.dimension_profiles[dimension.dimension_name] = profile

    def _calculate_dimension_complexity(self, dimension: Dimension) -> float:
        """Calculate complexity score for a dimension based on match strategy."""
        complexity_scores = {
            MatchStrategy.EXACT: 1.0,      # Simplest - direct equality
            MatchStrategy.RANGE: 2.0,      # Moderate - two comparisons
            MatchStrategy.REGEX: 3.0,      # Complex - pattern matching
        }
        return complexity_scores.get(dimension.match_strategy, 2.0)

    def evaluate_context_dataframe_vectorized(self,
                                              context_values: Dict[str, Any]) -> BaseDataFrame:
        """
        Ultra-high performance vectorized rule evaluation using BaseDataFrame operations.

        This method leverages mountainash-dataframes filtering system with ternary logic
        extensions while maintaining our revolutionary performance characteristics.

        Args:
            context_values: Dictionary mapping dimension names to context values

        Returns:
            BaseDataFrame with rule evaluation results including ternary logic flags

        Example:
            >>> context = {"customer_tier": "PREMIUM", "age": 35, "region": "US"}
            >>> result_df = processor.evaluate_context_dataframe_vectorized(context)
            >>> matching_rules = result_df.filter(ibis._.keep == True)
        """
        start_time = time.time()

        try:
            # Phase 1: Generate rule match conditions using ternary logic
            rule_conditions = self._generate_rule_conditions(context_values)

            # Phase 2: Combine conditions using mathematical ternary logic
            combined_condition = self._combine_rule_conditions(rule_conditions)

            # Phase 3: Apply framework filtering with ternary logic
            result_df = self._apply_framework_filtering(combined_condition)

            # Phase 4: Generate final keep flags and metadata
            final_result = self._generate_final_result(result_df)

            # Update performance statistics
            execution_time = time.time() - start_time
            self.stats.update_execution_time(execution_time)
            self.stats.total_rows_processed += self.rules.count()

            if self.config.performance_monitoring:
                logger.debug(f"DataFrameRuleProcessor evaluation completed in {execution_time*1000:.2f}ms")

            return final_result

        except Exception as e:
            logger.error(f"DataFrameRuleProcessor evaluation failed: {e}")
            raise

    def _generate_rule_conditions(self, context_values: Dict[str, Any]) -> List[RuleMatchCondition]:
        """
        Generate rule match conditions for each dimension using ternary logic.

        Leverages our specialized RuleMatchCondition FilterNodes that integrate
        with mountainash-dataframes while maintaining performance optimization.
        """
        conditions = []

        for dimension in self.dimensions:
            dim_name = dimension.dimension_name

            # Start performance timing for this dimension
            dim_start_time = time.time_ns()

            if dim_name not in context_values:
                # Create unknown condition for missing context
                logger.debug(f"Missing context for dimension: {dim_name}")
                # We'll handle this in the combination phase
                continue
            else:
                context_value = context_values[dim_name]

                # Create rule match condition using our ternary logic extension
                condition = create_rule_match_condition(
                    dimension=dimension,
                    context_value=context_value,
                    enable_ternary=self.config.use_ternary_logic
                )
                conditions.append(condition)

            # Update dimension performance profile
            dim_execution_time = time.time_ns() - dim_start_time
            if dim_name in self.dimension_profiles:
                self.dimension_profiles[dim_name].update_performance(dim_execution_time)

        if self.config.debug_expression_generation:
            logger.debug(f"Generated {len(conditions)} rule match conditions")

        return conditions

    def _combine_rule_conditions(self, conditions: List[RuleMatchCondition]) -> TernaryCondition:
        """
        Combine rule conditions using prime-based mathematical ternary logic.

        Uses our TernaryCondition with ALL_TRUE logic, ensuring UNKNOWN propagates
        and FALSE propagates, with TRUE only when all conditions are TRUE.
        """
        if not conditions:
            # Create always-unknown condition for no valid conditions
            logger.warning("No valid rule conditions found, creating unknown result")
            return TernaryCondition(
                conditions=[],
                logic_type=TernaryLogicType.UNKNOWN_PROPAGATION
            )

        # Use ALL_TRUE logic - all conditions must match for rule to match
        combined_condition = create_ternary_all_condition(
            conditions=conditions,
            enable_optimization=True
        )

        if self.config.debug_expression_generation:
            logger.debug(f"Combined {len(conditions)} conditions using ALL_TRUE ternary logic")

        return combined_condition

    def _apply_framework_filtering(self, condition: TernaryCondition) -> BaseDataFrame:
        """
        Apply mountainash-dataframes filtering with ternary logic extensions.

        This method demonstrates strategic framework usage - leveraging the filtering
        system while maintaining our performance optimizations.
        """
        try:
            # Use our ternary visitor to convert to backend-specific expressions
            filter_expression = condition.accept(self.ternary_visitor)

            # Apply filtering using BaseDataFrame interface
            if self.config.use_framework_filtering:
                # Strategic framework usage - let framework handle the filtering
                # This provides benefits like error handling, type safety, optimization

                # For now, we'll work directly with the underlying data since
                # BaseDataFrame.filter expects ibis expressions
                # This is where we bridge framework abstractions with performance

                if isinstance(self.rules, IbisDataFrame):
                    # Get the underlying polars data for direct expression application
                    underlying_df = self._get_underlying_polars_dataframe()

                    # Apply our ternary expression directly to polars
                    result_polars = underlying_df.with_columns([
                        filter_expression.alias("ternary_match_result")
                    ])

                    # Convert back to BaseDataFrame maintaining framework integration
                    result_df = self._convert_to_base_dataframe(result_polars)

                    self.stats.framework_operations += 1

                    return result_df
                else:
                    raise ValueError(f"Unsupported BaseDataFrame type: {type(self.rules)}")
            else:
                # Direct processing fallback
                self.stats.direct_operations += 1
                return self._apply_direct_filtering(filter_expression)

        except Exception as e:
            logger.error(f"Framework filtering failed, falling back to direct processing: {e}")
            return self._apply_direct_filtering_fallback(condition)

    def _get_underlying_polars_dataframe(self) -> pl.DataFrame:
        """
        Extract underlying polars DataFrame from BaseDataFrame.

        Strategic abstraction bridging - access polars for performance while
        maintaining framework integration patterns.
        """
        if isinstance(self.rules, IbisDataFrame):
            # Try different materialization approaches
            try:
                # First try direct polars materialization if available
                if hasattr(self.rules, 'to_polars'):
                    return self.rules.to_polars()
                elif hasattr(self.rules, 'materialise'):
                    return self.rules.materialise('polars')
                else:
                    # Fallback to pandas then convert
                    pandas_df = self.rules.to_pandas()
                    return pl.from_pandas(pandas_df)
            except Exception as e:
                logger.warning(f"Failed to extract polars dataframe: {e}")
                # Final fallback
                return pl.from_pandas(self.rules.to_pandas())
        else:
            raise ValueError(f"Cannot extract polars from {type(self.rules)}")

    def _convert_to_base_dataframe(self, polars_df: pl.DataFrame) -> BaseDataFrame:
        """
        Convert polars DataFrame back to BaseDataFrame maintaining framework integration.

        This preserves the framework abstraction benefits while leveraging our
        performance optimizations.
        """
        try:
            # Create new IbisDataFrame with same backend configuration as original
            if isinstance(self.rules, IbisDataFrame):
                # Maintain same backend schema and configuration
                return IbisDataFrame(
                    polars_df,
                    ibis_backend_schema='polars'  # Use polars backend for performance
                )
            else:
                raise ValueError(f"Cannot convert back to {type(self.rules)}")
        except Exception as e:
            logger.error(f"Failed to convert back to BaseDataFrame: {e}")
            raise

    def _generate_final_result(self, result_df: BaseDataFrame) -> BaseDataFrame:
        """
        Generate final result with keep flags and metadata.

        Converts ternary match results to boolean keep flags while preserving
        ternary information for debugging and audit purposes.
        """
        try:
            # Get underlying polars for final processing
            underlying_df = self._get_underlying_polars_dataframe_from_result(result_df)

            # Generate keep flag based on ternary result
            final_df = underlying_df.with_columns([
                # Keep flag: TRUE when ternary result is PRIME_TRUE (2)
                (pl.col("ternary_match_result") == RuleTrinaryFlags.PRIME_TRUE).alias("keep"),

                # Preserve ternary information for debugging
                pl.col("ternary_match_result").alias("ternary_flag"),

                # Add evaluation metadata
                pl.lit(True).alias("evaluated_by_dataframe_processor"),
                pl.lit(time.time()).alias("evaluation_timestamp")
            ])

            # Convert back to BaseDataFrame
            return self._convert_to_base_dataframe(final_df)

        except Exception as e:
            logger.error(f"Failed to generate final result: {e}")
            raise

    def _get_underlying_polars_dataframe_from_result(self, result_df: BaseDataFrame) -> pl.DataFrame:
        """Extract polars DataFrame from result BaseDataFrame."""
        return self._get_underlying_polars_dataframe() if result_df is self.rules else self._get_underlying_polars_dataframe()

    def _apply_direct_filtering(self, filter_expression: Any) -> BaseDataFrame:
        """Apply filtering directly without framework abstractions."""
        underlying_df = self._get_underlying_polars_dataframe()
        result_polars = underlying_df.with_columns([
            filter_expression.alias("ternary_match_result")
        ])
        return self._convert_to_base_dataframe(result_polars)

    def _apply_direct_filtering_fallback(self, condition: TernaryCondition) -> BaseDataFrame:
        """Fallback filtering when all other approaches fail."""
        logger.warning("Using direct filtering fallback")
        # Simple fallback - mark all as unknown
        underlying_df = self._get_underlying_polars_dataframe()
        result_polars = underlying_df.with_columns([
            pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN).alias("ternary_match_result")
        ])
        return self._convert_to_base_dataframe(result_polars)

    # ============================================================================
    # Performance Analysis and Optimization
    # ============================================================================

    def analyze_dimension_selectivity(self, sample_contexts: List[Dict[str, Any]] = None) -> Dict[str, float]:
        """
        Analyze dimension selectivity for optimization opportunities.

        Returns estimated selectivity scores for each dimension to enable
        query plan optimization and early termination strategies.
        """
        selectivity_scores = {}

        for dimension in self.dimensions:
            dim_name = dimension.dimension_name

            if dim_name in self._selectivity_analysis_cache:
                selectivity_scores[dim_name] = self._selectivity_analysis_cache[dim_name]
                continue

            # Analyze based on match strategy and rule characteristics
            if dimension.match_strategy == MatchStrategy.EXACT:
                # Analyze value distribution
                try:
                    values = self.rules.get_column_as_list(dim_name)
                    unique_values = len(set(values)) if values else 1
                    total_values = len(values) if values else 1
                    selectivity = 1.0 - (unique_values / total_values)  # More unique = more selective
                except Exception:
                    selectivity = 0.5  # Default moderate selectivity

            elif dimension.match_strategy == MatchStrategy.RANGE:
                # Range selectivity is typically moderate
                selectivity = 0.3  # Ranges tend to be moderately selective

            elif dimension.match_strategy == MatchStrategy.REGEX:
                # Regex selectivity depends on pattern complexity
                selectivity = 0.4  # Generally selective but variable

            else:
                selectivity = 0.5  # Default

            selectivity_scores[dim_name] = selectivity
            self._selectivity_analysis_cache[dim_name] = selectivity

            # Update dimension profile
            if dim_name in self.dimension_profiles:
                self.dimension_profiles[dim_name].estimated_selectivity = selectivity

        return selectivity_scores

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics."""
        base_stats = self.stats.get_performance_summary()

        # Add processor-specific statistics
        base_stats.update({
            "processor_type": "DataFrameRuleProcessor",
            "framework_integration": self.config.use_framework_filtering,
            "ternary_logic_enabled": self.config.use_ternary_logic,
            "backend_preference": self.config.backend_preference,
            "dimension_count": len(self.dimensions),
            "rule_count": self.rules.count(),
            "cache_stats": self.ternary_visitor.get_cache_stats()
        })

        return base_stats

    def get_dimension_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Get performance profiles for all dimensions."""
        profiles = {}
        for dim_name, profile in self.dimension_profiles.items():
            profiles[dim_name] = {
                "match_strategy": profile.match_strategy.name,
                "estimated_selectivity": profile.estimated_selectivity,
                "avg_evaluation_time_ms": profile.avg_evaluation_time_ns / 1_000_000,
                "complexity_score": profile.complexity_score,
                "optimization_opportunities": profile.optimization_opportunities
            }
        return profiles

    def optimize_performance(self) -> None:
        """Optimize processor performance based on collected statistics."""
        # Analyze selectivity for better query planning
        self.analyze_dimension_selectivity()

        # Clear caches if they're getting too large
        cache_stats = self.ternary_visitor.get_cache_stats()
        if cache_stats.get("expression_cache_size", 0) > 10000:
            logger.info("Clearing expression caches due to size limit")
            self.ternary_visitor.clear_cache()

        logger.info("Performance optimization completed")


# ============================================================================
# Factory Functions
# ============================================================================

def create_dataframe_rule_processor(rules: BaseDataFrame,
                                   dimensions: List[Dimension],
                                   config: Optional[DataFrameProcessorConfig] = None) -> DataFrameRuleProcessor:
    """
    Factory function for creating optimized DataFrameRuleProcessor instances.

    Args:
        rules: BaseDataFrame containing rules to evaluate
        dimensions: List of dimension metadata
        config: Optional configuration for performance tuning

    Returns:
        Configured DataFrameRuleProcessor instance

    Example:
        >>> processor = create_dataframe_rule_processor(rules_df, dimensions)
        >>> result = processor.evaluate_context_dataframe_vectorized(context)
    """
    return DataFrameRuleProcessor(rules, dimensions, config)


def create_high_performance_processor_config() -> DataFrameProcessorConfig:
    """
    Create configuration optimized for maximum performance.

    Returns:
        DataFrameProcessorConfig with high-performance settings

    Example:
        >>> config = create_high_performance_processor_config()
        >>> processor = create_dataframe_rule_processor(rules, dimensions, config)
    """
    return DataFrameProcessorConfig(
        use_framework_filtering=True,
        use_ternary_logic=True,
        backend_preference='polars',
        enable_parallel_processing=True,
        max_worker_threads=8,
        enable_expression_caching=True,
        enable_lazy_evaluation=True,
        enable_selectivity_analysis=True,
        enable_early_termination=True,
        polars_lazy_optimization=True,
        performance_monitoring=True
    )


def create_memory_optimized_processor_config() -> DataFrameProcessorConfig:
    """
    Create configuration optimized for memory efficiency.

    Returns:
        DataFrameProcessorConfig with memory-optimized settings

    Example:
        >>> config = create_memory_optimized_processor_config()
        >>> processor = create_dataframe_rule_processor(rules, dimensions, config)
    """
    return DataFrameProcessorConfig(
        use_framework_filtering=True,
        use_ternary_logic=True,
        backend_preference='polars',
        enable_parallel_processing=False,  # Reduce memory pressure
        max_worker_threads=2,
        enable_expression_caching=True,
        chunk_processing=True,
        chunk_size_mb=50,
        memory_optimization=True,
        performance_monitoring=False
    )
