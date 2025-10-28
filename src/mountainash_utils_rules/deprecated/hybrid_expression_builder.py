"""
DataFrameVectorizedRulesEngine: HybridExpressionBuilder Implementation

Bridge component combining mountainash-dataframes filtering abstractions with our
specialized rule optimization patterns and prime-based ternary logic for maximum
performance while maintaining framework integration benefits.

Phase 4B: Engine Implementation - HybridExpressionBuilder Development
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple, Union, Set
from dataclasses import dataclass, field
from functools import lru_cache
from collections import defaultdict
import hashlib

import polars as pl
from mountainash_dataframes.utils.dataframe_filters import FilterNode, FilterCondition

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
class ExpressionOptimizationProfile:
    """Profile for expression optimization characteristics and performance."""
    
    expression_id: str
    complexity_score: float = 1.0
    estimated_selectivity: float = 0.5
    avg_execution_time_ns: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    optimization_applied: List[str] = field(default_factory=list)
    
    def get_cache_hit_ratio(self) -> float:
        """Calculate cache hit ratio."""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0
    
    def update_performance(self, execution_time_ns: float) -> None:
        """Update performance metrics."""
        if self.avg_execution_time_ns == 0.0:
            self.avg_execution_time_ns = execution_time_ns
        else:
            # Exponential moving average
            self.avg_execution_time_ns = 0.9 * self.avg_execution_time_ns + 0.1 * execution_time_ns


@dataclass
class ExpressionPlan:
    """Optimized expression execution plan with framework integration."""
    
    expressions: List[FilterNode]
    execution_order: List[int]  # Indices into expressions list
    optimization_strategy: str
    estimated_performance_gain: float
    framework_operations: List[str] = field(default_factory=list)
    direct_operations: List[str] = field(default_factory=list)
    
    def get_ordered_expressions(self) -> List[FilterNode]:
        """Get expressions in optimized execution order."""
        return [self.expressions[i] for i in self.execution_order]


@dataclass
class HybridBuilderConfig:
    """Configuration for HybridExpressionBuilder optimization strategies."""
    
    # Framework integration settings
    prefer_framework_operations: bool = True
    fallback_to_direct: bool = True
    backend_preference: str = 'polars'
    
    # Expression optimization
    enable_selectivity_ordering: bool = True
    enable_expression_caching: bool = True
    enable_early_termination: bool = True
    cache_size_limit: int = 10000
    
    # Ternary logic optimization
    use_prime_arithmetic: bool = True
    optimize_ternary_combinations: bool = True
    ternary_logic_strategy: str = TernaryLogicType.ALL_TRUE
    
    # Performance monitoring
    enable_profiling: bool = True
    detailed_logging: bool = False
    performance_threshold_ns: int = 1_000_000  # 1ms threshold for optimization
    
    # Advanced optimizations
    enable_parallel_expression_building: bool = False
    enable_lazy_evaluation: bool = True
    enable_simd_optimization: bool = True


class HybridExpressionBuilder:
    """
    Revolutionary hybrid expression builder combining framework abstractions with optimization.
    
    This builder bridges mountainash-dataframes filtering patterns with our specialized
    rule optimization techniques, enabling both framework benefits (error handling, 
    type safety, cross-backend compatibility) and performance optimization (selectivity
    analysis, ternary logic, expression caching).
    
    Key Innovation: Strategic framework usage - leverage framework where beneficial,
    optimize directly where performance-critical, maintain compatibility throughout.
    
    Args:
        dimensions: List of dimension metadata for optimization analysis
        config: Configuration for optimization strategies and framework integration
    
    Examples:
        >>> builder = HybridExpressionBuilder(dimensions)
        >>> plan = builder.build_optimized_expression_plan(context_values)
        >>> result = builder.execute_expression_plan(plan, rules_df)
    """
    
    def __init__(self,
                 dimensions: List[Dimension],
                 config: Optional[HybridBuilderConfig] = None):
        
        self.dimensions = dimensions
        self.config = config or HybridBuilderConfig()
        
        # Initialize ternary filter visitor for framework integration
        self.ternary_visitor = create_ternary_filter_visitor(
            backend=self.config.backend_preference,
            enable_caching=self.config.enable_expression_caching,
            enable_optimization=True
        )
        
        # Expression optimization and caching
        self.expression_profiles: Dict[str, ExpressionOptimizationProfile] = {}
        self.selectivity_cache: Dict[str, float] = {}
        self.optimization_cache: Dict[str, Any] = {}
        
        # Framework integration state
        self.framework_operations_count: int = 0
        self.direct_operations_count: int = 0
        
        # Performance monitoring
        self.build_stats = {
            "expressions_built": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "avg_build_time": 0.0,
            "optimization_applied": 0
        }
        
        logger.info(f"HybridExpressionBuilder initialized: {len(dimensions)} dimensions, "
                   f"framework_preference={config.prefer_framework_operations if config else True}")
    
    def build_optimized_expression_plan(self, 
                                        context_values: Dict[str, Any]) -> ExpressionPlan:
        """
        Build optimized expression execution plan combining framework and performance patterns.
        
        This method demonstrates the hybrid approach: use framework abstractions for
        robustness while applying our optimization techniques for performance.
        
        Args:
            context_values: Context values for rule evaluation
            
        Returns:
            ExpressionPlan with optimized execution strategy
            
        Example:
            >>> context = {"customer_tier": "PREMIUM", "age": 35}
            >>> plan = builder.build_optimized_expression_plan(context)
            >>> print(f"Estimated gain: {plan.estimated_performance_gain:.2f}x")
        """
        start_time = time.time_ns()
        
        try:
            # Phase 1: Generate base expressions using framework patterns
            base_expressions = self._generate_base_expressions(context_values)
            
            # Phase 2: Analyze selectivity for optimization
            selectivity_analysis = self._analyze_expression_selectivity(base_expressions, context_values)
            
            # Phase 3: Optimize expression ordering
            execution_order = self._optimize_expression_order(base_expressions, selectivity_analysis)
            
            # Phase 4: Determine framework vs direct operations
            operation_strategy = self._determine_operation_strategy(base_expressions)
            
            # Phase 5: Estimate performance gain
            estimated_gain = self._estimate_performance_gain(
                base_expressions, execution_order, operation_strategy
            )
            
            # Create optimized expression plan
            plan = ExpressionPlan(
                expressions=base_expressions,
                execution_order=execution_order,
                optimization_strategy=self._get_optimization_strategy_name(),
                estimated_performance_gain=estimated_gain,
                framework_operations=operation_strategy["framework"],
                direct_operations=operation_strategy["direct"]
            )
            
            # Update performance statistics
            build_time = time.time_ns() - start_time
            self._update_build_stats(build_time)
            
            if self.config.detailed_logging:
                logger.debug(f"Expression plan built in {build_time/1_000_000:.2f}ms, "
                           f"estimated gain: {estimated_gain:.2f}x")
            
            return plan
            
        except Exception as e:
            logger.error(f"Failed to build optimized expression plan: {e}")
            raise
    
    def execute_expression_plan(self, 
                               plan: ExpressionPlan, 
                               rules_data: Any) -> Any:
        """
        Execute optimized expression plan with strategic framework utilization.
        
        Implements the hybrid approach by using framework operations where beneficial
        and direct optimization where performance-critical.
        
        Args:
            plan: ExpressionPlan with optimization strategy
            rules_data: Rules data (BaseDataFrame or polars DataFrame)
            
        Returns:
            Processed results with ternary logic evaluation
        """
        start_time = time.time_ns()
        
        try:
            # Get expressions in optimized order
            ordered_expressions = plan.get_ordered_expressions()
            
            # Choose execution strategy based on plan
            if self.config.prefer_framework_operations and plan.framework_operations:
                result = self._execute_framework_strategy(ordered_expressions, rules_data)
                self.framework_operations_count += 1
            else:
                result = self._execute_direct_strategy(ordered_expressions, rules_data)
                self.direct_operations_count += 1
            
            # Update performance profiles
            execution_time = time.time_ns() - start_time
            self._update_expression_profiles(plan, execution_time)
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to execute expression plan: {e}")
            # Fallback to simple direct execution
            return self._execute_fallback_strategy(plan.expressions, rules_data)
    
    def _generate_base_expressions(self, context_values: Dict[str, Any]) -> List[FilterNode]:
        """
        Generate base expressions using framework FilterNode patterns.
        
        This creates FilterNode expressions that are compatible with mountainash-dataframes
        while incorporating our ternary logic extensions.
        """
        expressions = []
        
        for dimension in self.dimensions:
            dim_name = dimension.dimension_name
            
            # Check cache first
            if self.config.enable_expression_caching:
                cache_key = self._generate_expression_cache_key(dimension, context_values.get(dim_name))
                if cache_key in self.optimization_cache:
                    expressions.append(self.optimization_cache[cache_key])
                    self.build_stats["cache_hits"] += 1
                    continue
                else:
                    self.build_stats["cache_misses"] += 1
            
            if dim_name not in context_values:
                # Missing context - framework approach would handle gracefully
                logger.debug(f"Missing context for dimension: {dim_name}")
                continue
            
            context_value = context_values[dim_name]
            
            # Create framework-compatible expression with ternary logic
            if self.config.use_prime_arithmetic:
                # Use our enhanced RuleMatchCondition
                expression = create_rule_match_condition(
                    dimension=dimension,
                    context_value=context_value,
                    enable_ternary=True
                )
            else:
                # Use standard framework FilterCondition
                expression = self._create_standard_filter_condition(dimension, context_value)
            
            expressions.append(expression)
            
            # Cache the expression
            if self.config.enable_expression_caching:
                self.optimization_cache[cache_key] = expression
        
        return expressions
    
    def _create_standard_filter_condition(self, 
                                         dimension: Dimension,
                                         context_value: Any) -> FilterNode:
        """Create standard framework FilterCondition for comparison."""
        dim_name = dimension.dimension_name
        
        if dimension.match_strategy == MatchStrategy.EXACT:
            return FilterCondition.eq(dim_name, context_value)
        elif dimension.match_strategy == MatchStrategy.RANGE:
            # Range requires special handling - use between if possible
            min_field = dimension.range_min_field or f"{dim_name}_MIN"
            max_field = dimension.range_max_field or f"{dim_name}_MAX"
            # For now, create a complex condition - this would need custom framework extension
            return FilterCondition.and_(
                FilterCondition.ge(min_field, context_value),
                FilterCondition.le(max_field, context_value)
            )
        else:
            # REGEX and others - use equality as fallback
            return FilterCondition.eq(dim_name, context_value)
    
    def _analyze_expression_selectivity(self, 
                                       expressions: List[FilterNode],
                                       context_values: Dict[str, Any]) -> Dict[int, float]:
        """
        Analyze expression selectivity for optimization ordering.
        
        Uses our dimension analysis techniques to estimate how selective each
        expression will be, enabling optimal query planning.
        """
        selectivity_scores = {}
        
        for i, expression in enumerate(expressions):
            # Generate selectivity key for caching
            selectivity_key = f"selectivity_{i}_{hash(str(expression))}"
            
            if selectivity_key in self.selectivity_cache:
                selectivity_scores[i] = self.selectivity_cache[selectivity_key]
                continue
            
            # Analyze selectivity based on expression type and dimension
            if isinstance(expression, RuleMatchCondition):
                dimension = expression.dimension
                selectivity = self._estimate_dimension_selectivity(dimension, expression.context_value)
            else:
                # Standard FilterCondition - moderate selectivity
                selectivity = 0.5
            
            selectivity_scores[i] = selectivity
            self.selectivity_cache[selectivity_key] = selectivity
        
        return selectivity_scores
    
    def _estimate_dimension_selectivity(self, 
                                       dimension: Dimension,
                                       context_value: Any) -> float:
        """Estimate selectivity for a dimension based on match strategy and value."""
        
        # Cache key for selectivity estimates
        selectivity_key = f"{dimension.dimension_name}_{dimension.match_strategy}_{hash(str(context_value))}"
        
        if selectivity_key in self.selectivity_cache:
            return self.selectivity_cache[selectivity_key]
        
        # Estimate based on match strategy
        if dimension.match_strategy == MatchStrategy.EXACT:
            # Exact matches are typically selective
            selectivity = 0.1  # 10% of rules expected to match
        elif dimension.match_strategy == MatchStrategy.RANGE:
            # Range matches are moderately selective
            selectivity = 0.3  # 30% of rules expected to match
        elif dimension.match_strategy == MatchStrategy.REGEX:
            # Regex selectivity depends on pattern complexity
            # For now, use moderate selectivity
            selectivity = 0.2  # 20% of rules expected to match
        else:
            selectivity = 0.5  # Default moderate selectivity
        
        self.selectivity_cache[selectivity_key] = selectivity
        return selectivity
    
    def _optimize_expression_order(self, 
                                  expressions: List[FilterNode],
                                  selectivity_analysis: Dict[int, float]) -> List[int]:
        """
        Optimize expression execution order based on selectivity analysis.
        
        Applies our query optimization techniques: most selective expressions first
        to maximize early termination opportunities.
        """
        if not self.config.enable_selectivity_ordering:
            return list(range(len(expressions)))
        
        # Sort by selectivity (most selective first)
        expression_indices = list(range(len(expressions)))
        
        # Sort by selectivity score (lower = more selective)
        expression_indices.sort(key=lambda i: selectivity_analysis.get(i, 0.5))
        
        if self.config.detailed_logging:
            selectivity_summary = [(i, selectivity_analysis.get(i, 0.5)) for i in expression_indices]
            logger.debug(f"Expression ordering by selectivity: {selectivity_summary}")
        
        return expression_indices
    
    def _determine_operation_strategy(self, expressions: List[FilterNode]) -> Dict[str, List[str]]:
        """
        Determine which operations should use framework vs direct approaches.
        
        Strategic decision based on performance characteristics and framework benefits.
        """
        strategy = {"framework": [], "direct": []}
        
        for i, expression in enumerate(expressions):
            operation_id = f"expr_{i}"
            
            # Prefer framework for standard operations
            if isinstance(expression, RuleMatchCondition):
                # Our custom ternary logic - use direct for maximum performance
                strategy["direct"].append(operation_id)
            else:
                # Standard FilterCondition - use framework for robustness
                strategy["framework"].append(operation_id)
        
        return strategy
    
    def _estimate_performance_gain(self, 
                                  expressions: List[FilterNode],
                                  execution_order: List[int],
                                  operation_strategy: Dict[str, List[str]]) -> float:
        """Estimate performance gain from optimization strategies."""
        base_gain = 1.0
        
        # Selectivity ordering gain
        if self.config.enable_selectivity_ordering and len(expressions) > 1:
            ordering_gain = 1.1 + (len(expressions) * 0.05)  # More expressions = more benefit
            base_gain *= ordering_gain
        
        # Expression caching gain
        cache_hit_ratio = self.build_stats["cache_hits"] / max(1, 
            self.build_stats["cache_hits"] + self.build_stats["cache_misses"])
        if cache_hit_ratio > 0:
            caching_gain = 1.0 + (cache_hit_ratio * 0.3)  # Up to 30% improvement
            base_gain *= caching_gain
        
        # Ternary logic optimization gain
        if self.config.use_prime_arithmetic:
            ternary_expressions = sum(1 for expr in expressions if isinstance(expr, RuleMatchCondition))
            if ternary_expressions > 0:
                ternary_gain = 1.0 + (ternary_expressions * 0.1)  # 10% per ternary expression
                base_gain *= ternary_gain
        
        # Framework vs direct operation balance
        total_ops = len(operation_strategy["framework"]) + len(operation_strategy["direct"])
        if total_ops > 0:
            direct_ratio = len(operation_strategy["direct"]) / total_ops
            # Balance: some framework for robustness, some direct for performance
            optimal_direct_ratio = 0.6  # 60% direct for performance
            balance_factor = 1.0 - abs(direct_ratio - optimal_direct_ratio)
            base_gain *= balance_factor
        
        return base_gain
    
    def _get_optimization_strategy_name(self) -> str:
        """Get human-readable optimization strategy name."""
        strategies = []
        
        if self.config.enable_selectivity_ordering:
            strategies.append("selectivity_ordered")
        if self.config.use_prime_arithmetic:
            strategies.append("prime_ternary")
        if self.config.enable_expression_caching:
            strategies.append("expression_cached")
        if self.config.prefer_framework_operations:
            strategies.append("framework_integrated")
        
        return "+".join(strategies) if strategies else "basic"
    
    def _execute_framework_strategy(self, 
                                   expressions: List[FilterNode],
                                   rules_data: Any) -> Any:
        """
        Execute expressions using framework operations where possible.
        
        Leverages mountainash-dataframes filtering capabilities while integrating
        our ternary logic extensions.
        """
        if not expressions:
            return rules_data
        
        # Combine expressions using our ternary logic
        if len(expressions) == 1:
            combined_condition = expressions[0]
        else:
            combined_condition = create_ternary_all_condition(
                conditions=expressions,
                enable_optimization=self.config.optimize_ternary_combinations
            )
        
        # Use ternary visitor to convert to backend expressions
        backend_expression = combined_condition.accept(self.ternary_visitor)
        
        # Apply to rules data (this would integrate with BaseDataFrame.filter in full implementation)
        # For now, assume we can apply directly to polars data
        if hasattr(rules_data, 'with_columns'):
            # Direct polars application
            result = rules_data.with_columns([
                backend_expression.alias("hybrid_ternary_result")
            ])
        else:
            logger.warning("Unable to apply framework strategy, falling back to direct")
            result = self._execute_direct_strategy(expressions, rules_data)
        
        return result
    
    def _execute_direct_strategy(self, 
                                expressions: List[FilterNode],
                                rules_data: Any) -> Any:
        """
        Execute expressions using direct optimization approaches.
        
        Bypasses framework abstractions for maximum performance while maintaining
        our ternary logic capabilities.
        """
        if not expressions:
            return rules_data
        
        # Direct ternary logic application
        ternary_expressions = []
        for expression in expressions:
            if isinstance(expression, RuleMatchCondition):
                backend_expr = expression.accept(self.ternary_visitor)
                ternary_expressions.append(backend_expr)
        
        if not ternary_expressions:
            return rules_data
        
        # Combine using direct polars operations
        if len(ternary_expressions) == 1:
            combined_expr = ternary_expressions[0]
        else:
            # Use our prime-based ternary AND logic
            combined_expr = ternary_expressions[0]
            for expr in ternary_expressions[1:]:
                combined_expr = pl.when(
                    (combined_expr == RuleTrinaryFlags.PRIME_UNKNOWN) | 
                    (expr == RuleTrinaryFlags.PRIME_UNKNOWN)
                ).then(
                    pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
                ).when(
                    (combined_expr == RuleTrinaryFlags.PRIME_FALSE) |
                    (expr == RuleTrinaryFlags.PRIME_FALSE)
                ).then(
                    pl.lit(RuleTrinaryFlags.PRIME_FALSE)
                ).otherwise(
                    pl.lit(RuleTrinaryFlags.PRIME_TRUE)
                )
        
        # Apply to rules data
        if hasattr(rules_data, 'with_columns'):
            result = rules_data.with_columns([
                combined_expr.alias("direct_ternary_result")
            ])
        else:
            result = rules_data
        
        return result
    
    def _execute_fallback_strategy(self, expressions: List[FilterNode], rules_data: Any) -> Any:
        """Fallback execution strategy when other approaches fail."""
        logger.warning("Using fallback execution strategy")
        
        # Simple fallback - mark all as unknown
        if hasattr(rules_data, 'with_columns'):
            return rules_data.with_columns([
                pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN).alias("fallback_result")
            ])
        else:
            return rules_data
    
    def _generate_expression_cache_key(self, dimension: Dimension, context_value: Any) -> str:
        """Generate cache key for expression caching."""
        key_data = f"{dimension.dimension_name}_{dimension.match_strategy}_{context_value}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _update_build_stats(self, build_time_ns: float) -> None:
        """Update expression building statistics."""
        self.build_stats["expressions_built"] += 1
        
        if self.build_stats["avg_build_time"] == 0:
            self.build_stats["avg_build_time"] = build_time_ns
        else:
            # Exponential moving average
            self.build_stats["avg_build_time"] = (
                0.9 * self.build_stats["avg_build_time"] + 0.1 * build_time_ns
            )
        
        if build_time_ns < self.config.performance_threshold_ns:
            self.build_stats["optimization_applied"] += 1
    
    def _update_expression_profiles(self, plan: ExpressionPlan, execution_time_ns: float) -> None:
        """Update expression performance profiles."""
        for i, expression in enumerate(plan.expressions):
            profile_id = f"expr_{i}_{plan.optimization_strategy}"
            
            if profile_id not in self.expression_profiles:
                self.expression_profiles[profile_id] = ExpressionOptimizationProfile(
                    expression_id=profile_id
                )
            
            profile = self.expression_profiles[profile_id]
            profile.update_performance(execution_time_ns)
            profile.optimization_applied.append(plan.optimization_strategy)
    
    # ============================================================================
    # Performance Analysis and Monitoring
    # ============================================================================
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics for the hybrid builder."""
        return {
            "builder_type": "HybridExpressionBuilder",
            "framework_preference": self.config.prefer_framework_operations,
            "dimensions_count": len(self.dimensions),
            "build_stats": self.build_stats.copy(),
            "operation_counts": {
                "framework_operations": self.framework_operations_count,
                "direct_operations": self.direct_operations_count,
                "framework_ratio": self.framework_operations_count / max(1, 
                    self.framework_operations_count + self.direct_operations_count)
            },
            "caching_stats": {
                "cache_size": len(self.optimization_cache),
                "selectivity_cache_size": len(self.selectivity_cache),
                "expression_profiles": len(self.expression_profiles)
            },
            "visitor_stats": self.ternary_visitor.get_cache_stats()
        }
    
    def get_optimization_recommendations(self) -> List[str]:
        """Get optimization recommendations based on performance analysis."""
        recommendations = []
        
        # Analyze cache hit ratios
        cache_hit_ratio = self.build_stats["cache_hits"] / max(1,
            self.build_stats["cache_hits"] + self.build_stats["cache_misses"])
        
        if cache_hit_ratio < 0.5:
            recommendations.append("Consider increasing expression cache size for better performance")
        
        # Analyze framework vs direct operation balance
        total_ops = self.framework_operations_count + self.direct_operations_count
        if total_ops > 0:
            framework_ratio = self.framework_operations_count / total_ops
            if framework_ratio < 0.3:
                recommendations.append("Consider using more framework operations for better error handling")
            elif framework_ratio > 0.8:
                recommendations.append("Consider more direct operations for better performance")
        
        # Analyze build performance
        avg_build_time_ms = self.build_stats["avg_build_time"] / 1_000_000
        if avg_build_time_ms > 10:  # 10ms threshold
            recommendations.append("Expression building is slow - consider enabling more caching")
        
        return recommendations
    
    def clear_caches(self) -> None:
        """Clear all caches and reset performance statistics."""
        self.optimization_cache.clear()
        self.selectivity_cache.clear()
        self.expression_profiles.clear()
        self.ternary_visitor.clear_cache()
        
        # Reset stats
        self.build_stats = {
            "expressions_built": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "avg_build_time": 0.0,
            "optimization_applied": 0
        }
        
        logger.info("HybridExpressionBuilder caches cleared")


# ============================================================================
# Factory Functions
# ============================================================================

def create_hybrid_expression_builder(dimensions: List[Dimension],
                                     config: Optional[HybridBuilderConfig] = None) -> HybridExpressionBuilder:
    """
    Factory function for creating optimized HybridExpressionBuilder instances.
    
    Args:
        dimensions: List of dimension metadata
        config: Optional configuration for optimization strategies
        
    Returns:
        Configured HybridExpressionBuilder instance
        
    Example:
        >>> builder = create_hybrid_expression_builder(dimensions)
        >>> plan = builder.build_optimized_expression_plan(context_values)
    """
    return HybridExpressionBuilder(dimensions, config)


def create_performance_optimized_config() -> HybridBuilderConfig:
    """
    Create configuration optimized for maximum performance.
    
    Returns:
        HybridBuilderConfig with performance-focused settings
        
    Example:
        >>> config = create_performance_optimized_config()
        >>> builder = create_hybrid_expression_builder(dimensions, config)
    """
    return HybridBuilderConfig(
        prefer_framework_operations=False,  # Prioritize direct operations
        fallback_to_direct=True,
        enable_selectivity_ordering=True,
        enable_expression_caching=True,
        enable_early_termination=True,
        use_prime_arithmetic=True,
        optimize_ternary_combinations=True,
        enable_profiling=True,
        enable_lazy_evaluation=True,
        enable_simd_optimization=True
    )


def create_framework_integrated_config() -> HybridBuilderConfig:
    """
    Create configuration optimized for framework integration and robustness.
    
    Returns:
        HybridBuilderConfig with framework-focused settings
        
    Example:
        >>> config = create_framework_integrated_config()
        >>> builder = create_hybrid_expression_builder(dimensions, config)
    """
    return HybridBuilderConfig(
        prefer_framework_operations=True,  # Prioritize framework operations
        fallback_to_direct=True,
        enable_selectivity_ordering=True,
        enable_expression_caching=True,
        use_prime_arithmetic=True,  # Still use ternary logic
        optimize_ternary_combinations=True,
        enable_profiling=True,
        detailed_logging=False  # Reduce overhead
    )


def create_balanced_config() -> HybridBuilderConfig:
    """
    Create balanced configuration optimizing both performance and framework integration.
    
    Returns:
        HybridBuilderConfig with balanced settings
        
    Example:
        >>> config = create_balanced_config()
        >>> builder = create_hybrid_expression_builder(dimensions, config)
    """
    return HybridBuilderConfig(
        prefer_framework_operations=True,
        fallback_to_direct=True,
        enable_selectivity_ordering=True,
        enable_expression_caching=True,
        enable_early_termination=True,
        use_prime_arithmetic=True,
        optimize_ternary_combinations=True,
        enable_profiling=False,  # Reduce overhead
        enable_lazy_evaluation=True
    )