# VectorizedRulesEngine Improvement Plan

**Date**: 2025-01-10
**Status**: Planning Phase
**Priority**: High

## Overview

This document outlines a practical plan to improve the existing `VectorizedRulesEngine` with meaningful enhancements while avoiding the over-engineering present in the `DataFrameVectorizedRulesEngine`.

## Current State Analysis

The current `VectorizedRulesEngine` has these strengths:
- Clean, direct implementation
- Excellent performance with Polars
- Prime-based ternary logic system (2, 3, 5)
- Minimal abstraction overhead

**Areas for improvement:**
- Hardcoded to Polars only
- No backend flexibility
- No performance monitoring capabilities
- No memory management for long-running processes
- Limited configuration options

## Proposed Improvements

### 1. Backend Provider Strategy Pattern

**Goal**: Allow switching between different backends (Polars, Ibis+DuckDB, Ibis+SQLite, etc.) without changing engine code.

#### Core Abstraction

```python
class RuleEvaluationProvider(ABC):
    """Abstract base class for rule evaluation backends."""

    @abstractmethod
    def materialize_rules(self, rules: BaseDataFrame) -> Any:
        """Convert BaseDataFrame to backend-specific format."""
        pass

    @abstractmethod
    def build_exact_match_expression(self, column: str, value: Any) -> Any:
        """Build exact match expression for backend."""
        pass

    @abstractmethod
    def build_range_match_expression(self, column: str, value: float,
                                   min_col: str, max_col: str) -> Any:
        """Build range match expression for backend."""
        pass

    @abstractmethod
    def build_regex_match_expression(self, column: str, pattern: str,
                                   context_value: str) -> Any:
        """Build regex match expression for backend."""
        pass

    @abstractmethod
    def combine_expressions(self, expressions: List[Any]) -> Any:
        """Combine multiple expressions using ternary logic."""
        pass

    @abstractmethod
    def execute_query(self, data: Any, expressions: List[Any],
                     final_expression: Any) -> Any:
        """Execute the query and return results."""
        pass

    @abstractmethod
    def to_base_dataframe(self, result: Any) -> BaseDataFrame:
        """Convert result back to BaseDataFrame."""
        pass

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Name of the backend."""
        pass
```

#### Concrete Implementations

- **PolarsProvider**: High-performance Polars-based provider (current implementation)
- **IbisProvider**: Ibis-based provider for cross-backend compatibility
- **Custom providers**: Extensible for future backends

#### Provider Factory

```python
class ProviderFactory:
    """Factory for creating rule evaluation providers."""

    _providers = {
        'polars': PolarsProvider,
        'ibis_polars': lambda: IbisProvider('polars'),
        'ibis_duckdb': lambda: IbisProvider('duckdb'),
        'ibis_sqlite': lambda: IbisProvider('sqlite'),
    }

    @classmethod
    def create_provider(cls, provider_type: str, **kwargs) -> RuleEvaluationProvider:
        """Create provider instance."""
        pass

    @classmethod
    def register_provider(cls, name: str, provider_class):
        """Register a custom provider."""
        pass
```

### 2. Lightweight Performance Monitoring

**Goal**: Optional performance tracking with minimal overhead when disabled.

#### Performance Metrics

```python
@dataclass
class PerformanceMetrics:
    """Lightweight performance metrics without overhead."""

    # Basic counters
    total_evaluations: int = 0
    successful_evaluations: int = 0
    failed_evaluations: int = 0

    # Timing (only track if enabled)
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0

    # Provider usage
    provider_usage: Dict[str, int] = field(default_factory=dict)

    # Recent performance (sliding window)
    recent_times: deque = field(default_factory=lambda: deque(maxlen=100))
```

#### Performance Monitor

```python
class PerformanceMonitor:
    """Optional performance monitoring with minimal overhead."""

    def __init__(self, enabled: bool = True, detailed_timing: bool = False):
        self.enabled = enabled
        self.detailed_timing = detailed_timing
        self.metrics = PerformanceMetrics() if enabled else None

    @contextmanager
    def time_evaluation(self, provider: str):
        """Context manager for timing evaluations."""
        # Zero overhead when disabled
        pass
```

### 3. Memory Management

**Goal**: Prevent memory leaks in long-running processes through periodic cleanup.

#### Memory Manager

```python
class MemoryManager:
    """Lightweight memory management for long-running processes."""

    def __init__(self,
                 cleanup_interval: int = 1000,
                 enable_gc: bool = True,
                 cache_size_limit: int = 10000):
        self.cleanup_interval = cleanup_interval
        self.enable_gc = enable_gc
        self.cache_size_limit = cache_size_limit

        # Track objects for cleanup
        self._cached_objects = weakref.WeakSet()

    def perform_cleanup(self):
        """Perform memory cleanup."""
        # Clear registered caches
        # Optional garbage collection
        pass
```

### 4. Simple Configuration System

**Goal**: Practical configuration without over-engineering.

```python
@dataclass
class VectorizedEngineConfig:
    """Simple, practical configuration for the vectorized engine."""

    # Backend selection
    provider: str = "polars"  # "polars", "ibis_polars", "ibis_duckdb", etc.

    # Performance monitoring (minimal overhead)
    enable_monitoring: bool = False
    detailed_timing: bool = False

    # Memory management for long-running processes
    cleanup_interval: int = 10000  # Clean caches every N evaluations
    enable_cleanup: bool = True

    # Expression caching
    cache_expressions: bool = True
    max_cache_size: int = 1000

    # Optional result metadata
    include_metadata: bool = False
```

### 5. Improved Engine Architecture

```python
class ImprovedVectorizedRulesEngine:
    """
    Improved VectorizedRulesEngine with provider strategy pattern and optional monitoring.

    Key improvements:
    - Pluggable backend providers (Polars, Ibis, etc.)
    - Optional lightweight performance monitoring
    - Memory management for long-running processes
    - Simple, practical configuration
    """

    def __init__(self,
                 rules: BaseDataFrame,
                 dimensions: List[Dimension],
                 config: Optional[VectorizedEngineConfig] = None):

        self.config = config or VectorizedEngineConfig()
        self.dimensions = dimensions

        # Initialize provider
        self.provider = ProviderFactory.create_provider(self.config.provider)
        self.rules_data = self.provider.materialize_rules(rules)

        # Optional components (zero overhead when disabled)
        self.monitor = PerformanceMonitor(...) if self.config.enable_monitoring else None
        self.memory_manager = MemoryManager(...) if self.config.enable_cleanup else None

    def apply_context_rules_engine(self, context: Any, active_dimensions: List[str]) -> BaseDataFrame:
        """Apply rules with the configured provider."""
        # Same core logic as current engine
        # Optional monitoring and memory management
        pass
```

## Factory Functions

```python
def create_polars_engine(rules: BaseDataFrame,
                        dimensions: List[Dimension]) -> ImprovedVectorizedRulesEngine:
    """Create engine optimized for Polars performance."""
    pass

def create_ibis_engine(rules: BaseDataFrame,
                      dimensions: List[Dimension],
                      backend: str = "polars") -> ImprovedVectorizedRulesEngine:
    """Create engine using Ibis for cross-backend compatibility."""
    pass

def create_monitored_engine(rules: BaseDataFrame,
                           dimensions: List[Dimension],
                           provider: str = "polars") -> ImprovedVectorizedRulesEngine:
    """Create engine with performance monitoring enabled."""
    pass
```

## What This Plan Avoids

**Rejected over-engineering from DataFrameVectorizedRulesEngine:**

- ❌ Complex fallback strategies (unnecessary for local processing)
- ❌ "Adaptive optimization" (premature optimization)
- ❌ Multiple execution strategies (adds complexity without benefit)
- ❌ Complex performance baselines and triggers
- ❌ Extensive configuration options that don't matter
- ❌ "Strategic optimization decision" logic
- ❌ Multiple wrapper layers and delegation

## Benefits

**Real improvements over the current vectorized engine:**

1. **Provider Strategy Pattern**: Allows switching between Polars, Ibis+DuckDB, Ibis+SQLite, etc. without changing engine code
2. **Optional Performance Monitoring**: Lightweight metrics collection when needed, zero overhead when disabled
3. **Memory Management**: Prevents memory leaks in long-running processes through periodic cache cleanup
4. **Simple Configuration**: Practical options without over-engineering
5. **Optional Metadata**: Can add provider/timestamp info to results when debugging

**Key benefits:**
- **Flexibility**: Easy to switch backends based on deployment needs
- **Maintainability**: Clean separation of concerns with provider pattern
- **Production-ready**: Memory management for long-running services
- **Optional overhead**: Monitoring and metadata only when needed
- **Backward compatible**: Same core API as current engine

## Implementation Priority

1. **High Priority**: Provider strategy pattern and factory
2. **Medium Priority**: Performance monitoring and memory management
3. **Low Priority**: Configuration system and factory functions

## Success Criteria

- Maintain current performance characteristics
- Enable backend flexibility without complexity
- Provide optional monitoring with zero overhead when disabled
- Support long-running processes without memory leaks
- Keep the API simple and focused

## Next Steps

1. Implement the provider strategy pattern
2. Create Polars and Ibis providers
3. Add optional performance monitoring
4. Implement memory management
5. Create factory functions for common use cases
6. Update tests and documentation

---

*This plan focuses on practical improvements that solve real problems while avoiding the over-engineering present in the DataFrameVectorizedRulesEngine.*
