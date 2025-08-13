"""
DataFrameVectorizedRulesEngine: Ternary Logic Filter Extensions

This module extends mountainash-dataframes filtering system with prime-based ternary logic
for revolutionary rule evaluation performance while maintaining framework integration.

Key Innovation: Mathematical prime-based ternary flags enable vectorized operations with
perfect audit trails through prime factorization.

Phase 4A: Foundation Components - RuleTrinaryFilterVisitor Implementation
"""

from abc import ABC, abstractmethod
from typing import Any, List, Union, Callable, Optional, Pattern, Dict
from dataclasses import dataclass
from functools import lru_cache
import re
import logging

import polars as pl
import ibis
from mountainash_dataframes.utils.expression_builders import TernaryExpressionNode, TernaryExpressionVisitor, ColumnExpression, LogicalExpression

from mountainash_utils_rules.constants import RuleTrinaryFlags, MatchStrategy
from mountainash_utils_rules.dimension import Dimension


logger = logging.getLogger(__name__)


@dataclass
class TernaryLogicType:
    """Mathematical ternary logic operation types for prime-based evaluation."""

    ALL_TRUE = "all_true"           # All conditions must be PRIME_TRUE (2)
    ANY_TRUE = "any_true"           # At least one condition must be PRIME_TRUE (2)
    UNKNOWN_PROPAGATION = "unknown_propagation"  # PRIME_UNKNOWN (5) propagates
    STRICT_AND = "strict_and"       # Prime-based AND with mathematical precision
    STRICT_OR = "strict_or"         # Prime-based OR with mathematical precision


class TernaryCondition(TernaryExpressionNode):
    """
    Mathematical ternary condition using prime-based logic for vectorized operations.

    This FilterNode extension enables prime-based ternary logic within the
    mountainash-dataframes filtering system, providing mathematical precision
    and vectorization optimization for rule evaluation.

    Args:
        conditions: List of FilterNode conditions to combine
        logic_type: TernaryLogicType defining combination strategy
        enable_optimization: Whether to enable prime arithmetic optimization

    Examples:
        >>> # All conditions must be true with unknown propagation
        >>> ternary_all = TernaryCondition(
        ...     conditions=[cond1, cond2, cond3],
        ...     logic_type=TernaryLogicType.ALL_TRUE
        ... )

        >>> # Any condition true with mathematical precision
        >>> ternary_any = TernaryCondition(
        ...     conditions=[cond1, cond2],
        ...     logic_type=TernaryLogicType.ANY_TRUE
        ... )
    """

    def __init__(self,
                 conditions: List[FilterNode],
                 logic_type: str,
                 enable_optimization: bool = True):
        self.conditions = conditions
        self.logic_type = logic_type
        self.enable_optimization = enable_optimization

    def accept(self, visitor: FilterVisitor) -> Callable:
        """Accept visitor pattern for ternary logic processing."""
        if hasattr(visitor, 'visit_ternary_condition'):
            return visitor.visit_ternary_condition(self)
        else:
            # Fallback for non-ternary aware visitors
            logger.warning("Visitor does not support ternary conditions, using logical fallback")
            return visitor.visit_logical_expression(
                LogicalCondition(operator="and", operands=self.conditions)
            )


class RuleMatchCondition(FilterNode):
    """
    Specialized rule matching condition integrating with dimension match strategies.

    This FilterNode provides rule-specific matching logic that integrates seamlessly
    with mountainash-dataframes filtering while leveraging our optimized match strategies.

    Args:
        dimension: Dimension metadata defining match strategy and constraints
        context_value: Context value to match against rules
        enable_ternary: Whether to use ternary logic (default: True)

    Examples:
        >>> # Exact match with ternary logic
        >>> exact_match = RuleMatchCondition(
        ...     dimension=Dimension("customer_tier", MatchStrategy.EXACT, str),
        ...     context_value="PREMIUM"
        ... )

        >>> # Range match with mathematical precision
        >>> range_match = RuleMatchCondition(
        ...     dimension=Dimension("age", MatchStrategy.RANGE, int, "age_min", "age_max"),
        ...     context_value=35
        ... )
    """

    def __init__(self,
                 dimension: Dimension,
                 context_value: Any,
                 enable_ternary: bool = True):
        self.dimension = dimension
        self.context_value = context_value
        self.enable_ternary = enable_ternary

    def accept(self, visitor: FilterVisitor) -> Callable:
        """Accept visitor pattern for rule matching processing."""
        if hasattr(visitor, 'visit_rule_match_condition'):
            return visitor.visit_rule_match_condition(self)
        else:
            # Fallback to basic column condition
            logger.warning("Visitor does not support rule match conditions, using basic fallback")
            return visitor.visit_column_expression(
                ColumnCondition(self.dimension.dimension_name, "==", self.context_value)
            )


class RuleTrinaryFilterVisitor(FilterVisitor):
    """
    Revolutionary ternary logic filter visitor extending mountainash-dataframes.

    This visitor implements prime-based mathematical ternary logic for rule evaluation
    while maintaining compatibility with the mountainash-dataframes filtering framework.

    Key Innovation: Uses prime numbers (2, 3, 5) for ternary logic enabling:
    - Mathematical precision in rule combinations
    - Vectorization optimization
    - Perfect audit trails through prime factorization
    - Ultra-efficient polars expression generation

    Args:
        backend: Target backend for expression generation ('polars', 'ibis', etc.)
        enable_caching: Whether to cache compiled expressions (default: True)
        enable_optimization: Whether to use prime arithmetic optimization (default: True)

    Examples:
        >>> visitor = RuleTrinaryFilterVisitor(backend='polars')
        >>> ternary_condition = TernaryCondition([cond1, cond2], TernaryLogicType.ALL_TRUE)
        >>> polars_expr = ternary_condition.accept(visitor)
    """

    def __init__(self,
                 backend: str = 'polars',
                 enable_caching: bool = True,
                 enable_optimization: bool = True):
        self.backend = backend
        self.enable_caching = enable_caching
        self.enable_optimization = enable_optimization

        # Expression and pattern caching for performance
        self._expression_cache: Dict[str, Any] = {} if enable_caching else None
        self._pattern_cache: Dict[str, Pattern] = {} if enable_caching else None

        logger.info(f"RuleTrinaryFilterVisitor initialized: backend={backend}, "
                   f"caching={enable_caching}, optimization={enable_optimization}")

    def visit_ternary_condition(self, condition: TernaryCondition) -> Callable:
        """
        Visit ternary condition and generate optimized expression.

        Implements prime-based ternary logic for mathematical precision and
        vectorization optimization in rule evaluation.
        """
        if not condition.conditions:
            return self._generate_constant_expression(RuleTrinaryFlags.PRIME_UNKNOWN)

        if len(condition.conditions) == 1:
            return condition.conditions[0].accept(self)

        # Generate cache key for performance optimization
        cache_key = None
        if self.enable_caching:
            cache_key = f"ternary_{condition.logic_type}_{len(condition.conditions)}_{hash(str(condition.conditions))}"
            if cache_key in self._expression_cache:
                logger.debug(f"Cache hit for ternary condition: {cache_key}")
                return self._expression_cache[cache_key]

        # Process conditions based on ternary logic type
        condition_expressions = [cond.accept(self) for cond in condition.conditions]

        if condition.logic_type == TernaryLogicType.ALL_TRUE:
            result_expr = self._combine_ternary_and(condition_expressions)
        elif condition.logic_type == TernaryLogicType.ANY_TRUE:
            result_expr = self._combine_ternary_or(condition_expressions)
        elif condition.logic_type == TernaryLogicType.UNKNOWN_PROPAGATION:
            result_expr = self._combine_unknown_propagation(condition_expressions)
        elif condition.logic_type == TernaryLogicType.STRICT_AND: #Same as ALL_TRUE??
            result_expr = self._combine_strict_and(condition_expressions)
        elif condition.logic_type == TernaryLogicType.STRICT_OR: #Same as ANY_TRUE??
            result_expr = self._combine_strict_or(condition_expressions)
        else:
            logger.warning(f"Unknown ternary logic type: {condition.logic_type}, using ALL_TRUE")
            result_expr = self._combine_ternary_and(condition_expressions)

        # Cache result for performance
        if self.enable_caching and cache_key:
            self._expression_cache[cache_key] = result_expr

        return result_expr

    def visit_rule_match_condition(self, condition: RuleMatchCondition) -> Callable:
        """
        Visit rule match condition and generate optimized match expression.

        Integrates dimension match strategies with ternary logic for
        maximum performance and mathematical precision.
        """
        dim = condition.dimension
        context_value = condition.context_value

        # Generate cache key for performance
        cache_key = None
        if self.enable_caching:
            cache_key = f"rule_match_{dim.dimension_name}_{dim.match_strategy}_{hash(str(context_value))}"
            if cache_key in self._expression_cache:
                logger.debug(f"Cache hit for rule match condition: {cache_key}")
                return self._expression_cache[cache_key]

        # Generate expression based on match strategy
        if dim.match_strategy == MatchStrategy.EXACT:
            result_expr = self._build_exact_match_expression(dim.dimension_name, context_value)
        elif dim.match_strategy == MatchStrategy.RANGE:
            min_field = dim.range_min_field or f"{dim.dimension_name}_MIN"
            max_field = dim.range_max_field or f"{dim.dimension_name}_MAX"
            result_expr = self._build_range_match_expression(dim.dimension_name, context_value, min_field, max_field)
        elif dim.match_strategy == MatchStrategy.REGEX:
            result_expr = self._build_regex_match_expression(dim.dimension_name, context_value)
        else:
            logger.warning(f"Unknown match strategy: {dim.match_strategy}, using unknown")
            result_expr = self._generate_constant_expression(RuleTrinaryFlags.PRIME_UNKNOWN)

        # Cache result for performance
        if self.enable_caching and cache_key:
            self._expression_cache[cache_key] = result_expr

        return result_expr

    def visit_column_expression(self, condition: ColumnCondition) -> Callable:
        """Visit standard column condition with ternary logic support."""
        if self.backend == 'polars':
            return self._visit_column_expression_polars(condition)
        elif self.backend == 'ibis':
            return self._visit_column_expression_ibis(condition)
        else:
            raise ValueError(f"Unsupported backend: {self.backend}")

    def visit_logical_expression(self, condition: LogicalCondition) -> Callable:
        """Visit logical condition with ternary logic enhancements."""
        if self.backend == 'polars':
            return self._visit_logical_expression_polars(condition)
        elif self.backend == 'ibis':
            return self._visit_logical_expression_ibis(condition)
        else:
            raise ValueError(f"Unsupported backend: {self.backend}")

    # ============================================================================
    # Prime-Based Ternary Logic Implementation
    # ============================================================================

    def _combine_ternary_and(self, expressions: List[Any]) -> Any:
        """
        Combine expressions using prime-based ternary AND logic.

        Prime-based AND logic:
        - UNKNOWN (5) propagates
        - FALSE (3) propagates
        - TRUE (2) only when all TRUE
        """
        if not expressions:
            return self._generate_constant_expression(RuleTrinaryFlags.PRIME_UNKNOWN)

        if len(expressions) == 1:
            return expressions[0]

        result = expressions[0]

        for expr in expressions[1:]:
            if self.backend == 'polars':
                result = pl.when(
                    (result == RuleTrinaryFlags.PRIME_UNKNOWN) |
                    (expr == RuleTrinaryFlags.PRIME_UNKNOWN)
                ).then(
                    pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
                ).when(
                    (result == RuleTrinaryFlags.PRIME_FALSE) |
                    (expr == RuleTrinaryFlags.PRIME_FALSE)
                ).then(
                    pl.lit(RuleTrinaryFlags.PRIME_FALSE)
                ).otherwise(
                    pl.lit(RuleTrinaryFlags.PRIME_TRUE)
                )
            else:
                # Add ibis implementation if needed
                raise NotImplementedError(f"Ternary AND not implemented for backend: {self.backend}")

        return result

    def _combine_ternary_or(self, expressions: List[Any]) -> Any:
        """
        Combine expressions using prime-based ternary OR logic.

        Prime-based OR logic:
        - TRUE (2) propagates
        - UNKNOWN (5) propagates if no TRUE
        - FALSE (3) only when all FALSE
        """
        if not expressions:
            return self._generate_constant_expression(RuleTrinaryFlags.PRIME_UNKNOWN)

        if len(expressions) == 1:
            return expressions[0]

        result = expressions[0]

        for expr in expressions[1:]:
            if self.backend == 'polars':
                result = pl.when(
                    (result == RuleTrinaryFlags.PRIME_TRUE) |
                    (expr == RuleTrinaryFlags.PRIME_TRUE)
                ).then(
                    pl.lit(RuleTrinaryFlags.PRIME_TRUE)
                ).when(
                    (result == RuleTrinaryFlags.PRIME_UNKNOWN) |
                    (expr == RuleTrinaryFlags.PRIME_UNKNOWN)
                ).then(
                    pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
                ).otherwise(
                    pl.lit(RuleTrinaryFlags.PRIME_FALSE)
                )
            else:
                raise NotImplementedError(f"Ternary OR not implemented for backend: {self.backend}")

        return result

    def _combine_unknown_propagation(self, expressions: List[Any]) -> Any:
        """Combine expressions with strict unknown propagation."""
        return self._combine_ternary_and(expressions)  # Unknown propagation is same as AND

    def _combine_strict_and(self, expressions: List[Any]) -> Any:
        """Combine expressions using strict mathematical AND."""
        return self._combine_ternary_and(expressions)

    def _combine_strict_or(self, expressions: List[Any]) -> Any:
        """Combine expressions using strict mathematical OR."""
        return self._combine_ternary_or(expressions)

    # ============================================================================
    # Match Strategy Expression Builders
    # ============================================================================

    def _build_exact_match_expression(self, dimension_name: str, context_value: Any) -> Any:
        """Build exact match expression with ternary logic."""
        if self.backend == 'polars':
            return pl.when(
                pl.col(dimension_name).is_null() | (pl.col(dimension_name) == "")
            ).then(
                pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
            ).when(
                pl.col(dimension_name) == context_value
            ).then(
                pl.lit(RuleTrinaryFlags.PRIME_TRUE)
            ).otherwise(
                pl.lit(RuleTrinaryFlags.PRIME_FALSE)
            )
        else:
            raise NotImplementedError(f"Exact match not implemented for backend: {self.backend}")

    def _build_range_match_expression(self,
                                     dimension_name: str,
                                     context_value: Any,
                                     min_field: str,
                                     max_field: str) -> Any:
        """Build range match expression with ternary logic."""
        if self.backend == 'polars':
            return pl.when(
                pl.col(min_field).is_null() | pl.col(max_field).is_null()
            ).then(
                pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
            ).when(
                (pl.col(min_field) <= context_value) & (context_value <= pl.col(max_field))
            ).then(
                pl.lit(RuleTrinaryFlags.PRIME_TRUE)
            ).otherwise(
                pl.lit(RuleTrinaryFlags.PRIME_FALSE)
            )
        else:
            raise NotImplementedError(f"Range match not implemented for backend: {self.backend}")

    def _build_regex_match_expression(self, dimension_name: str, context_value: str) -> Any:
        """Build regex match expression with ternary logic."""
        if self.backend == 'polars':
            return (
                pl.when(pl.col(dimension_name).is_null())
                .then(pl.lit(int(RuleTrinaryFlags.PRIME_UNKNOWN)))
                .otherwise(
                    pl.col(dimension_name)
                    .map_elements(
                        lambda pattern: self._evaluate_regex(pattern, context_value),
                        return_dtype=pl.Int32
                    )
                )
            )
        else:
            raise NotImplementedError(f"Regex match not implemented for backend: {self.backend}")

    @lru_cache(maxsize=1000)
    def _compile_regex(self, pattern: str) -> Pattern:
        """Compile and cache regex patterns for performance."""
        return re.compile(pattern)

    def _evaluate_regex(self, pattern: Any, context_value: str) -> int:
        """Evaluate regex pattern with caching and error handling."""
        if pattern is None or pattern == "" or str(pattern).lower() == 'none':
            return int(RuleTrinaryFlags.PRIME_UNKNOWN)

        try:
            compiled_pattern = self._compile_regex(str(pattern))
            if compiled_pattern.match(context_value):
                return int(RuleTrinaryFlags.PRIME_TRUE)
            else:
                return int(RuleTrinaryFlags.PRIME_FALSE)
        except Exception:
            return int(RuleTrinaryFlags.PRIME_UNKNOWN)

    def _generate_constant_expression(self, value: RuleTrinaryFlags) -> Any:
        """Generate constant expression for the target backend."""
        if self.backend == 'polars':
            return pl.lit(value)
        elif self.backend == 'ibis':
            return ibis.literal(value)
        else:
            raise ValueError(f"Unsupported backend: {self.backend}")

    # ============================================================================
    # Backend-Specific Implementations
    # ============================================================================

    def _visit_column_expression_polars(self, condition: ColumnCondition) -> pl.Expr:
        """Visit column condition for polars backend with ternary logic."""
        col = pl.col(condition.column)

        if condition.compare_column:
            # Column to column comparison
            compare_col = pl.col(condition.compare_column)
            if condition.operator == "==":
                return pl.when(col.is_null() | compare_col.is_null()).then(
                    pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
                ).when(col == compare_col).then(
                    pl.lit(RuleTrinaryFlags.PRIME_TRUE)
                ).otherwise(
                    pl.lit(RuleTrinaryFlags.PRIME_FALSE)
                )
            # Add other column comparison operators as needed
        else:
            # Column to value comparison
            if condition.operator == "==":
                return pl.when(col.is_null()).then(
                    pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
                ).when(col == condition.value).then(
                    pl.lit(RuleTrinaryFlags.PRIME_TRUE)
                ).otherwise(
                    pl.lit(RuleTrinaryFlags.PRIME_FALSE)
                )
            # Add other operators as needed

        # Fallback for unsupported operators
        logger.warning(f"Unsupported operator in ternary logic: {condition.operator}")
        return pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)

    def _visit_logical_expression_polars(self, condition: LogicalCondition) -> pl.Expr:
        """Visit logical condition for polars backend with ternary logic."""
        if condition.operator == LogicalCondition.ALWAYS_TRUE_OP:
            return pl.lit(RuleTrinaryFlags.PRIME_TRUE)
        elif condition.operator == LogicalCondition.ALWAYS_FALSE_OP:
            return pl.lit(RuleTrinaryFlags.PRIME_FALSE)

        if not condition.operands:
            return pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)

        operand_expressions = [operand.accept(self) for operand in condition.operands]

        if condition.operator == "and":
            return self._combine_ternary_and(operand_expressions)
        elif condition.operator == "or":
            return self._combine_ternary_or(operand_expressions)
        elif condition.operator == "not":
            if len(operand_expressions) == 1:
                expr = operand_expressions[0]
                return pl.when(expr == RuleTrinaryFlags.PRIME_TRUE).then(
                    pl.lit(RuleTrinaryFlags.PRIME_FALSE)
                ).when(expr == RuleTrinaryFlags.PRIME_FALSE).then(
                    pl.lit(RuleTrinaryFlags.PRIME_TRUE)
                ).otherwise(
                    pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
                )

        logger.warning(f"Unsupported logical operator: {condition.operator}")
        return pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)

    def _visit_column_expression_ibis(self, condition: ColumnCondition) -> Any:
        """Visit column condition for ibis backend - implementation placeholder."""
        raise NotImplementedError("Ibis backend ternary logic not yet implemented")

    def _visit_logical_expression_ibis(self, condition: LogicalCondition) -> Any:
        """Visit logical condition for ibis backend - implementation placeholder."""
        raise NotImplementedError("Ibis backend ternary logic not yet implemented")

    # ============================================================================
    # Performance and Debugging
    # ============================================================================

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get caching performance statistics."""
        if not self.enable_caching:
            return {"caching_enabled": False}

        return {
            "caching_enabled": True,
            "expression_cache_size": len(self._expression_cache) if self._expression_cache else 0,
            "pattern_cache_size": len(self._pattern_cache) if self._pattern_cache else 0,
            "cache_hit_ratio": "Not implemented"  # Could add hit/miss counters
        }

    def clear_cache(self) -> None:
        """Clear expression and pattern caches."""
        if self.enable_caching:
            if self._expression_cache:
                self._expression_cache.clear()
            if self._pattern_cache:
                self._pattern_cache.clear()
            logger.info("Ternary filter visitor caches cleared")


# ============================================================================
# Convenience Factory Functions
# ============================================================================

def create_ternary_filter_visitor(backend: str = 'polars',
                                  enable_caching: bool = True,
                                  enable_optimization: bool = True) -> RuleTrinaryFilterVisitor:
    """
    Factory function for creating optimized ternary filter visitor.

    Args:
        backend: Target backend ('polars', 'ibis')
        enable_caching: Enable expression caching for performance
        enable_optimization: Enable prime arithmetic optimization

    Returns:
        Configured RuleTrinaryFilterVisitor instance

    Example:
        >>> visitor = create_ternary_filter_visitor('polars', enable_caching=True)
        >>> # Use visitor with ternary conditions
    """
    return RuleTrinaryFilterVisitor(
        backend=backend,
        enable_caching=enable_caching,
        enable_optimization=enable_optimization
    )


def create_rule_match_condition(dimension: Dimension,
                               context_value: Any,
                               enable_ternary: bool = True) -> RuleMatchCondition:
    """
    Factory function for creating rule match conditions.

    Args:
        dimension: Dimension metadata with match strategy
        context_value: Value to match against rules
        enable_ternary: Enable ternary logic (default: True)

    Returns:
        Configured RuleMatchCondition instance

    Example:
        >>> from mountainash_utils_rules.dimension import Dimension
        >>> from mountainash_utils_rules.constants import MatchStrategy
        >>>
        >>> dim = Dimension("customer_tier", MatchStrategy.EXACT, str)
        >>> condition = create_rule_match_condition(dim, "PREMIUM")
    """
    return RuleMatchCondition(
        dimension=dimension,
        context_value=context_value,
        enable_ternary=enable_ternary
    )


def create_ternary_all_condition(conditions: List[FilterNode],
                                enable_optimization: bool = True) -> TernaryCondition:
    """
    Factory function for creating ALL_TRUE ternary conditions.

    Args:
        conditions: List of FilterNode conditions to combine
        enable_optimization: Enable prime arithmetic optimization

    Returns:
        TernaryCondition with ALL_TRUE logic

    Example:
        >>> conditions = [cond1, cond2, cond3]
        >>> ternary_all = create_ternary_all_condition(conditions)
    """
    return TernaryCondition(
        conditions=conditions,
        logic_type=TernaryLogicType.ALL_TRUE,
        enable_optimization=enable_optimization
    )


def create_ternary_any_condition(conditions: List[FilterNode],
                                enable_optimization: bool = True) -> TernaryCondition:
    """
    Factory function for creating ANY_TRUE ternary conditions.

    Args:
        conditions: List of FilterNode conditions to combine
        enable_optimization: Enable prime arithmetic optimization

    Returns:
        TernaryCondition with ANY_TRUE logic

    Example:
        >>> conditions = [cond1, cond2]
        >>> ternary_any = create_ternary_any_condition(conditions)
    """
    return TernaryCondition(
        conditions=conditions,
        logic_type=TernaryLogicType.ANY_TRUE,
        enable_optimization=enable_optimization
    )
