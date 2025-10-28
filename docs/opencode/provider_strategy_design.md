# Provider Strategy Pattern Design

**Date**: 2025-01-10  
**Component**: Backend Provider Strategy  
**Status**: Design Phase  

## Overview

This document details the design of the provider strategy pattern for the VectorizedRulesEngine, enabling support for multiple backends (Polars, Ibis+DuckDB, Ibis+SQLite, etc.) without changing the core engine logic.

## Design Goals

1. **Backend Flexibility**: Easy switching between different data processing backends
2. **Performance Preservation**: Maintain current Polars performance characteristics
3. **Extensibility**: Allow custom providers for specialized use cases
4. **Simplicity**: Clean abstraction without over-engineering
5. **Type Safety**: Strong typing for all provider interfaces

## Core Architecture

### Abstract Provider Interface

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
from mountainash_dataframes import BaseDataFrame
from mountainash_utils_rules.constants import RuleTrinaryFlags, MatchStrategy
from mountainash_utils_rules.dimension import Dimension

class RuleEvaluationProvider(ABC):
    """
    Abstract base class for rule evaluation backends.
    
    This interface defines the contract that all providers must implement
    to support rule evaluation with prime-based ternary logic.
    """
    
    @abstractmethod
    def materialize_rules(self, rules: BaseDataFrame) -> Any:
        """
        Convert BaseDataFrame to backend-specific format.
        
        Args:
            rules: BaseDataFrame containing rules to evaluate
            
        Returns:
            Backend-specific data structure (e.g., pl.DataFrame, ibis.Table)
        """
        pass
    
    @abstractmethod
    def build_exact_match_expression(self, column: str, value: Any) -> Any:
        """
        Build exact match expression for backend.
        
        Args:
            column: Column name to match against
            value: Value to match exactly
            
        Returns:
            Backend-specific expression that returns ternary flags (2, 3, 5)
        """
        pass
    
    @abstractmethod
    def build_range_match_expression(self, column: str, value: float, 
                                   min_col: str, max_col: str) -> Any:
        """
        Build range match expression for backend.
        
        Args:
            column: Column name for the dimension
            value: Context value to check if within range
            min_col: Column containing minimum range values
            max_col: Column containing maximum range values
            
        Returns:
            Backend-specific expression that returns ternary flags (2, 3, 5)
        """
        pass
    
    @abstractmethod
    def build_regex_match_expression(self, column: str, pattern_column: str, 
                                   context_value: str) -> Any:
        """
        Build regex match expression for backend.
        
        Args:
            column: Column name for the dimension
            pattern_column: Column containing regex patterns
            context_value: Context value to match against patterns
            
        Returns:
            Backend-specific expression that returns ternary flags (2, 3, 5)
        """
        pass
    
    @abstractmethod
    def build_unknown_expression(self, column: str) -> Any:
        """
        Build expression that returns PRIME_UNKNOWN for missing context.
        
        Args:
            column: Column name for the dimension
            
        Returns:
            Backend-specific expression that returns PRIME_UNKNOWN (5)
        """
        pass
    
    @abstractmethod
    def combine_expressions(self, expressions: List[Any]) -> Any:
        """
        Combine multiple expressions using prime-based ternary logic.
        
        Logic: ALL_TRUE - all conditions must be TRUE (2) for final TRUE
        - If any expression is UNKNOWN (5), result is UNKNOWN (5)
        - If any expression is FALSE (3), result is FALSE (3)  
        - Only if all expressions are TRUE (2), result is TRUE (2)
        
        Args:
            expressions: List of backend-specific expressions
            
        Returns:
            Backend-specific combined expression
        """
        pass
    
    @abstractmethod
    def execute_query(self, data: Any, expressions: List[Any], 
                     final_expression: Any) -> Any:
        """
        Execute the query and return results.
        
        Args:
            data: Backend-specific data structure
            expressions: Individual dimension expressions
            final_expression: Combined ternary logic expression
            
        Returns:
            Backend-specific result with all columns plus 'keep' flag
        """
        pass
    
    @abstractmethod
    def to_base_dataframe(self, result: Any) -> BaseDataFrame:
        """
        Convert result back to BaseDataFrame.
        
        Args:
            result: Backend-specific result
            
        Returns:
            BaseDataFrame compatible with mountainash-dataframes
        """
        pass
    
    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Name of the backend for logging and monitoring."""
        pass
    
    @property
    @abstractmethod
    def supports_lazy_evaluation(self) -> bool:
        """Whether this provider supports lazy evaluation."""
        pass
```

## Concrete Provider Implementations

### PolarsProvider

```python
import polars as pl
import re
from functools import lru_cache
from typing import Pattern

class PolarsProvider(RuleEvaluationProvider):
    """
    High-performance Polars-based provider.
    
    This is the default provider that maintains the current performance
    characteristics of the VectorizedRulesEngine.
    """
    
    def __init__(self, cache_patterns: bool = True):
        self.cache_patterns = cache_patterns
        self._pattern_cache: Dict[str, Pattern] = {} if cache_patterns else None
    
    def materialize_rules(self, rules: BaseDataFrame) -> pl.DataFrame:
        """Convert BaseDataFrame to Polars DataFrame."""
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
            raise ValueError(f"Failed to materialize rules for polars processing: {e}")
    
    def build_exact_match_expression(self, column: str, value: Any) -> pl.Expr:
        """Build Polars exact match expression with ternary logic."""
        return pl.when(
            pl.col(column).is_null() | (pl.col(column) == "")
        ).then(
            pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
        ).when(
            pl.col(column) == value
        ).then(
            pl.lit(RuleTrinaryFlags.PRIME_TRUE)
        ).otherwise(
            pl.lit(RuleTrinaryFlags.PRIME_FALSE)
        ).alias(f"{column}_match")
    
    def build_range_match_expression(self, column: str, value: float, 
                                   min_col: str, max_col: str) -> pl.Expr:
        """Build Polars range match expression with ternary logic."""
        return pl.when(
            pl.col(min_col).is_null() | pl.col(max_col).is_null()
        ).then(
            pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
        ).when(
            (pl.col(min_col) <= value) & (value <= pl.col(max_col))
        ).then(
            pl.lit(RuleTrinaryFlags.PRIME_TRUE)
        ).otherwise(
            pl.lit(RuleTrinaryFlags.PRIME_FALSE)
        ).alias(f"{column}_match")
    
    def build_regex_match_expression(self, column: str, pattern_column: str, 
                                   context_value: str) -> pl.Expr:
        """Build Polars regex match expression with ternary logic."""
        return (
            pl.when(pl.col(pattern_column).is_null())
            .then(pl.lit(int(RuleTrinaryFlags.PRIME_UNKNOWN)))
            .otherwise(
                pl.col(pattern_column)
                .map_elements(
                    lambda pattern: self._evaluate_regex(pattern, context_value),
                    return_dtype=pl.Int32
                )
            )
            .alias(f"{column}_match")
        )
    
    def build_unknown_expression(self, column: str) -> pl.Expr:
        """Build expression that returns PRIME_UNKNOWN."""
        return pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN).alias(f"{column}_missing_match")
    
    def combine_expressions(self, expressions: List[pl.Expr]) -> pl.Expr:
        """Combine expressions using prime-based ternary ALL_TRUE logic."""
        if not expressions:
            return pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
        
        if len(expressions) == 1:
            return expressions[0]
        
        # Use prime arithmetic for efficient ternary logic combination
        combined = expressions[0]
        
        for expr in expressions[1:]:
            combined = pl.when(
                (combined == RuleTrinaryFlags.PRIME_UNKNOWN) | 
                (expr == RuleTrinaryFlags.PRIME_UNKNOWN)
            ).then(
                pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN)
            ).when(
                (combined == RuleTrinaryFlags.PRIME_FALSE) |
                (expr == RuleTrinaryFlags.PRIME_FALSE)
            ).then(
                pl.lit(RuleTrinaryFlags.PRIME_FALSE)
            ).otherwise(
                pl.lit(RuleTrinaryFlags.PRIME_TRUE)
            )
        
        return combined.alias("final_match")
    
    def execute_query(self, data: pl.DataFrame, expressions: List[pl.Expr], 
                     final_expression: pl.Expr) -> pl.DataFrame:
        """Execute Polars query with lazy evaluation."""
        # Create keep flag based on final match result
        keep_expression = (final_expression == RuleTrinaryFlags.PRIME_TRUE).alias("keep")
        
        # Execute optimized polars query
        return (
            data
            .with_columns(expressions + [final_expression, keep_expression])
            .select([
                pl.col("*"),  # Include all original columns
                pl.col("keep")  # Keep flag for filtering
            ])
        )
    
    def to_base_dataframe(self, result: pl.DataFrame) -> BaseDataFrame:
        """Convert Polars result back to BaseDataFrame."""
        from mountainash_dataframes import IbisDataFrame
        return IbisDataFrame(result, ibis_backend_schema='polars')
    
    @property
    def backend_name(self) -> str:
        return "polars"
    
    @property
    def supports_lazy_evaluation(self) -> bool:
        return True
    
    def _evaluate_regex(self, pattern: Any, context_value: str) -> int:
        """Evaluate regex pattern with caching and error handling."""
        if pattern is None or pattern == "" or str(pattern).lower() == 'none':
            return int(RuleTrinaryFlags.PRIME_UNKNOWN)
        
        try:
            if self.cache_patterns and self._pattern_cache is not None:
                if str(pattern) not in self._pattern_cache:
                    self._pattern_cache[str(pattern)] = re.compile(str(pattern))
                compiled_pattern = self._pattern_cache[str(pattern)]
            else:
                compiled_pattern = re.compile(str(pattern))
            
            if compiled_pattern.match(context_value):
                return int(RuleTrinaryFlags.PRIME_TRUE)
            else:
                return int(RuleTrinaryFlags.PRIME_FALSE)
        except Exception:
            return int(RuleTrinaryFlags.PRIME_UNKNOWN)
    
    def clear_caches(self):
        """Clear pattern cache."""
        if self._pattern_cache:
            self._pattern_cache.clear()
```

### IbisProvider

```python
import ibis
from mountainash_dataframes import IbisDataFrame

class IbisProvider(RuleEvaluationProvider):
    """
    Ibis-based provider for cross-backend compatibility.
    
    Supports multiple backends through Ibis: DuckDB, SQLite, PostgreSQL, etc.
    """
    
    def __init__(self, backend: str = "polars"):
        self.backend = backend
        self._connection = None
    
    def materialize_rules(self, rules: BaseDataFrame) -> ibis.Table:
        """Convert BaseDataFrame to Ibis Table."""
        if hasattr(rules, 'ibis_table'):
            return rules.ibis_table
        else:
            # Convert via pandas
            pandas_df = rules.to_pandas()
            return ibis.memtable(pandas_df)
    
    def build_exact_match_expression(self, column: str, value: Any) -> ibis.Expr:
        """Build Ibis exact match expression with ternary logic."""
        col = ibis.col(column)
        return ibis.case().when(
            col.isnull() | (col == ""), RuleTrinaryFlags.PRIME_UNKNOWN
        ).when(
            col == value, RuleTrinaryFlags.PRIME_TRUE
        ).else_(
            RuleTrinaryFlags.PRIME_FALSE
        ).name(f"{column}_match")
    
    def build_range_match_expression(self, column: str, value: float, 
                                   min_col: str, max_col: str) -> ibis.Expr:
        """Build Ibis range match expression with ternary logic."""
        min_col_expr = ibis.col(min_col)
        max_col_expr = ibis.col(max_col)
        
        return ibis.case().when(
            min_col_expr.isnull() | max_col_expr.isnull(), 
            RuleTrinaryFlags.PRIME_UNKNOWN
        ).when(
            (min_col_expr <= value) & (value <= max_col_expr),
            RuleTrinaryFlags.PRIME_TRUE
        ).else_(
            RuleTrinaryFlags.PRIME_FALSE
        ).name(f"{column}_match")
    
    def build_regex_match_expression(self, column: str, pattern_column: str, 
                                   context_value: str) -> ibis.Expr:
        """Build Ibis regex match expression with ternary logic."""
        pattern_col = ibis.col(pattern_column)
        
        # Note: Regex support varies by backend
        return ibis.case().when(
            pattern_col.isnull(),
            RuleTrinaryFlags.PRIME_UNKNOWN
        ).when(
            ibis.literal(context_value).re_search(pattern_col),
            RuleTrinaryFlags.PRIME_TRUE
        ).else_(
            RuleTrinaryFlags.PRIME_FALSE
        ).name(f"{column}_match")
    
    def build_unknown_expression(self, column: str) -> ibis.Expr:
        """Build expression that returns PRIME_UNKNOWN."""
        return ibis.literal(RuleTrinaryFlags.PRIME_UNKNOWN).name(f"{column}_missing_match")
    
    def combine_expressions(self, expressions: List[ibis.Expr]) -> ibis.Expr:
        """Combine expressions using ternary ALL_TRUE logic."""
        if not expressions:
            return ibis.literal(RuleTrinaryFlags.PRIME_UNKNOWN)
        
        if len(expressions) == 1:
            return expressions[0]
        
        # Build nested case statements for ternary logic
        combined = expressions[0]
        
        for expr in expressions[1:]:
            combined = ibis.case().when(
                (combined == RuleTrinaryFlags.PRIME_UNKNOWN) | 
                (expr == RuleTrinaryFlags.PRIME_UNKNOWN),
                RuleTrinaryFlags.PRIME_UNKNOWN
            ).when(
                (combined == RuleTrinaryFlags.PRIME_FALSE) |
                (expr == RuleTrinaryFlags.PRIME_FALSE),
                RuleTrinaryFlags.PRIME_FALSE
            ).else_(
                RuleTrinaryFlags.PRIME_TRUE
            )
        
        return combined.name("final_match")
    
    def execute_query(self, data: ibis.Table, expressions: List[ibis.Expr], 
                     final_expression: ibis.Expr) -> ibis.Table:
        """Execute Ibis query."""
        # Create keep flag
        keep_expression = (final_expression == RuleTrinaryFlags.PRIME_TRUE).name("keep")
        
        # Add all expressions to the table
        result = data
        for expr in expressions:
            result = result.mutate(**{expr.get_name(): expr})
        
        result = result.mutate(
            final_match=final_expression,
            keep=keep_expression
        )
        
        return result
    
    def to_base_dataframe(self, result: ibis.Table) -> BaseDataFrame:
        """Convert Ibis result back to BaseDataFrame."""
        return IbisDataFrame(result, ibis_backend_schema=self.backend)
    
    @property
    def backend_name(self) -> str:
        return f"ibis_{self.backend}"
    
    @property
    def supports_lazy_evaluation(self) -> bool:
        return True  # Ibis supports lazy evaluation
```

## Provider Factory

```python
from typing import Dict, Callable, Type

class ProviderFactory:
    """Factory for creating rule evaluation providers."""
    
    _providers: Dict[str, Callable[..., RuleEvaluationProvider]] = {
        'polars': lambda **kwargs: PolarsProvider(**kwargs),
        'ibis_polars': lambda **kwargs: IbisProvider('polars', **kwargs),
        'ibis_duckdb': lambda **kwargs: IbisProvider('duckdb', **kwargs),
        'ibis_sqlite': lambda **kwargs: IbisProvider('sqlite', **kwargs),
    }
    
    @classmethod
    def create_provider(cls, provider_type: str, **kwargs) -> RuleEvaluationProvider:
        """
        Create a provider instance.
        
        Args:
            provider_type: Type of provider to create
            **kwargs: Additional arguments for provider constructor
            
        Returns:
            Configured provider instance
            
        Raises:
            ValueError: If provider_type is not registered
        """
        if provider_type not in cls._providers:
            available = ', '.join(cls.available_providers())
            raise ValueError(f"Unknown provider: {provider_type}. Available: {available}")
        
        provider_factory = cls._providers[provider_type]
        return provider_factory(**kwargs)
    
    @classmethod
    def register_provider(cls, name: str, provider_factory: Callable[..., RuleEvaluationProvider]):
        """
        Register a custom provider.
        
        Args:
            name: Name for the provider
            provider_factory: Factory function that creates provider instances
        """
        cls._providers[name] = provider_factory
    
    @classmethod
    def available_providers(cls) -> List[str]:
        """Get list of available provider names."""
        return list(cls._providers.keys())
    
    @classmethod
    def get_provider_info(cls, provider_type: str) -> Dict[str, Any]:
        """Get information about a provider."""
        if provider_type not in cls._providers:
            raise ValueError(f"Unknown provider: {provider_type}")
        
        # Create a temporary instance to get info
        provider = cls.create_provider(provider_type)
        return {
            'name': provider.backend_name,
            'supports_lazy_evaluation': provider.supports_lazy_evaluation,
            'type': type(provider).__name__
        }
```

## Usage Examples

```python
# Create different providers
polars_provider = ProviderFactory.create_provider('polars')
duckdb_provider = ProviderFactory.create_provider('ibis_duckdb')

# Register custom provider
class CustomProvider(RuleEvaluationProvider):
    # Implementation...
    pass

ProviderFactory.register_provider('custom', lambda: CustomProvider())

# Use in engine
config = VectorizedEngineConfig(provider='ibis_duckdb')
engine = ImprovedVectorizedRulesEngine(rules, dimensions, config)
```

## Testing Strategy

1. **Provider Interface Tests**: Ensure all providers implement the interface correctly
2. **Ternary Logic Tests**: Verify prime-based logic works across all providers
3. **Performance Tests**: Compare provider performance characteristics
4. **Cross-Backend Tests**: Ensure consistent results across different backends
5. **Error Handling Tests**: Test provider behavior with invalid inputs

## Extension Points

1. **Custom Providers**: Easy to add new backends by implementing the interface
2. **Provider Configuration**: Providers can accept configuration parameters
3. **Provider Capabilities**: Providers can expose their specific capabilities
4. **Provider Optimization**: Each provider can implement backend-specific optimizations

---

*This design provides a clean, extensible way to support multiple backends while maintaining the performance and simplicity of the current VectorizedRulesEngine.*