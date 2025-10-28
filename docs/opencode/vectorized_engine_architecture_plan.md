# VectorizedRulesEngine Architecture and Improvement Plan

**Date**: 2025-01-10  
**Status**: Architecture Design Phase  
**Component**: VectorizedRulesEngine Enhancement  

## Executive Summary

This document presents a comprehensive architecture and improvement plan for the `VectorizedRulesEngine` based on analysis of the current implementation, requirements from the opencode documentation, and integration opportunities with the `dataframe_ternary_filters` module. The plan maintains compatibility with the original `RulesEngine` while introducing meaningful enhancements for production use.

## Current State Analysis

### Strengths of Current VectorizedRulesEngine

1. **Performance Excellence**
   - Pure Polars implementation with lazy evaluation
   - Prime-based ternary logic (2, 3, 5) for mathematical precision
   - Query plan optimization with selectivity analysis
   - 93.9% performance improvement over original engine

2. **Clean Architecture**
   - Clear separation of concerns (Expression Builder, Query Optimizer, Rule Processor)
   - Minimal abstraction overhead
   - Direct, understandable code flow

3. **Advanced Features**
   - Rule selectivity profiling for optimization
   - Parallel processing capability
   - Expression caching for repeated patterns
   - Memory pooling configuration

### Areas for Improvement

1. **Backend Flexibility**: Currently hardcoded to Polars only
2. **Integration**: Not utilizing the `dataframe_ternary_filters` module
3. **Monitoring**: Limited production monitoring capabilities
4. **Memory Management**: No automatic cleanup for long-running processes
5. **API Compatibility**: Some differences from original `RulesEngine` interface

## Architecture Design

### Core Architecture Principles

1. **Provider Strategy Pattern**: Enable multiple backend support
2. **Filter Visitor Integration**: Leverage `dataframe_ternary_filters` for expression building
3. **Compatibility Layer**: Maintain API compatibility with original engine
4. **Production Readiness**: Add monitoring and memory management
5. **Performance Preservation**: Keep current performance characteristics

### Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    VectorizedRulesEngine                     │
│                         (Main API)                           │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────┐│
│  │ Provider Manager │  │ Filter Builder   │  │ Monitor    ││
│  │                  │  │                  │  │            ││
│  │ - Provider       │  │ - Ternary Filter │  │ - Metrics  ││
│  │   Selection      │  │   Visitor        │  │ - Timing   ││
│  │ - Fallback       │  │ - Expression     │  │ - Memory   ││
│  │   Strategy       │  │   Caching        │  │            ││
│  └──────────────────┘  └──────────────────┘  └────────────┘│
│                                                               │
├─────────────────────────────────────────────────────────────┤
│                     Provider Interface                        │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Polars       │  │ Ibis         │  │ Custom           │  │
│  │ Provider     │  │ Provider     │  │ Providers        │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Detailed Implementation Plan

### Phase 1: Provider Strategy Pattern Implementation

#### 1.1 Abstract Provider Interface

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from mountainash_dataframes import BaseDataFrame
from mountainash_utils_rules.dataframe_ternary_filters import RuleTrinaryFilterVisitor

class RuleEvaluationProvider(ABC):
    """Abstract base class for rule evaluation backends."""
    
    @abstractmethod
    def get_filter_visitor(self) -> RuleTrinaryFilterVisitor:
        """Get the appropriate filter visitor for this provider."""
        pass
    
    @abstractmethod
    def materialize_rules(self, rules: BaseDataFrame) -> Any:
        """Convert BaseDataFrame to backend-specific format."""
        pass
    
    @abstractmethod
    def execute_evaluation(self, 
                          rules_data: Any,
                          context_values: Dict[str, Any],
                          dimensions: List[Dimension]) -> Any:
        """Execute rule evaluation with the backend."""
        pass
    
    @abstractmethod
    def to_base_dataframe(self, result: Any) -> BaseDataFrame:
        """Convert result back to BaseDataFrame."""
        pass
```

#### 1.2 Polars Provider with Ternary Filter Integration

```python
class PolarsProvider(RuleEvaluationProvider):
    """High-performance Polars provider using ternary filters."""
    
    def __init__(self, enable_caching: bool = True):
        self.visitor = RuleTrinaryFilterVisitor(
            backend='polars',
            enable_caching=enable_caching,
            enable_optimization=True
        )
    
    def get_filter_visitor(self) -> RuleTrinaryFilterVisitor:
        return self.visitor
    
    def execute_evaluation(self, 
                          rules_data: pl.DataFrame,
                          context_values: Dict[str, Any],
                          dimensions: List[Dimension]) -> pl.DataFrame:
        """Execute using ternary filter visitor for expression building."""
        
        # Build match conditions using ternary filters
        conditions = []
        for dimension in dimensions:
            if dimension.dimension_name in context_values:
                condition = create_rule_match_condition(
                    dimension=dimension,
                    context_value=context_values[dimension.dimension_name],
                    enable_ternary=True
                )
                conditions.append(condition)
            else:
                # Missing context - add unknown condition
                conditions.append(self._create_unknown_condition(dimension))
        
        # Combine using ternary logic
        combined_condition = create_ternary_all_condition(
            conditions=conditions,
            enable_optimization=True
        )
        
        # Generate expression through visitor
        final_expression = combined_condition.accept(self.visitor)
        
        # Execute with Polars
        keep_expression = (final_expression == RuleTrinaryFlags.PRIME_TRUE).alias("keep")
        
        return rules_data.with_columns([final_expression, keep_expression])
```

### Phase 2: Enhanced VectorizedRulesEngine

#### 2.1 Core Engine Enhancement

```python
class EnhancedVectorizedRulesEngine:
    """
    Enhanced VectorizedRulesEngine with provider strategy and ternary filter integration.
    
    Key improvements:
    - Provider strategy pattern for backend flexibility
    - Integration with dataframe_ternary_filters
    - API compatibility with original RulesEngine
    - Production monitoring and memory management
    """
    
    def __init__(self,
                 rules: BaseDataFrame,
                 dimension_metadata: Optional[DimensionsMetadata] = None,
                 config: Optional[VectorizedEngineConfig] = None):
        
        self.config = config or VectorizedEngineConfig()
        
        # Initialize components similar to original engine
        self.rule_manager = RuleManager(rules=rules)
        self.metadata_manager = MetadataManager(
            rules=self.rule_manager.rules,
            dimension_metadata=dimension_metadata
        )
        self.observability_manager = ObservabilityManager()
        
        # Initialize provider
        self.provider = ProviderFactory.create_provider(
            self.config.provider,
            enable_caching=self.config.cache_expressions
        )
        
        # Materialize rules for the provider
        self.rules_data = self.provider.materialize_rules(self.rule_manager.get_rules())
        
        # Optional monitoring
        self.monitor = PerformanceMonitor(
            enabled=self.config.enable_monitoring
        ) if self.config.enable_monitoring else None
        
        # Optional memory management
        self.memory_manager = MemoryManager(
            cleanup_interval=self.config.cleanup_interval
        ) if self.config.enable_cleanup else None
    
    def apply_context_rules_engine(self,
                                   context: BaseModel,
                                   dimension_names: List[str] | str,
                                   keep_all: bool = True) -> BaseDataFrame:
        """
        Apply rules with provider-based evaluation.
        
        Maintains API compatibility with original RulesEngine while using
        optimized provider-based evaluation.
        """
        
        # Start monitoring if enabled
        if self.monitor:
            monitor_context = self.monitor.time_evaluation(self.provider.backend_name)
            monitor_context.__enter__()
        
        try:
            # Validate dimension names (same as original)
            if isinstance(dimension_names, str):
                dimension_names = [dimension_names]
            
            if len(dimension_names) == 0:
                raise ValueError("No dimension names specified.")
            
            # Get active dimensions (same as original)
            active_dimension_names = self.metadata_manager.get_active_dimension_names(
                context=context,
                rules=self.rule_manager.get_rules(),
                dimension_names=dimension_names
            )
            active_dimensions = self.metadata_manager.get_dimensions_list(
                dimension_names=active_dimension_names
            )
            
            # Extract context values (same as original)
            context_values = ContextHelper.get_all_context_values(
                context=context,
                dimensions=active_dimensions
            )
            
            # Execute provider-based evaluation
            result = self.provider.execute_evaluation(
                rules_data=self.rules_data,
                context_values=context_values,
                dimensions=active_dimensions
            )
            
            # Convert back to BaseDataFrame
            result_df = self.provider.to_base_dataframe(result)
            
            # Apply keep_all filter (same as original)
            if not keep_all:
                result_df = result_df.filter(fc.eq("keep", True))
            
            # Store intermediate state for observability
            for dimension in active_dimensions:
                self.observability_manager.save_dimension_intermediate_values(
                    rules=result_df,
                    dimension=dimension
                )
            
            # Memory cleanup if needed
            if self.memory_manager:
                self.memory_manager.check_and_cleanup()
            
            return result_df
            
        finally:
            if self.monitor:
                monitor_context.__exit__(None, None, None)
```

#### 2.2 Configuration System

```python
@dataclass
class VectorizedEngineConfig:
    """Enhanced configuration for the vectorized engine."""
    
    # Provider selection
    provider: str = "polars"  # "polars", "ibis_polars", "ibis_duckdb", etc.
    
    # Performance optimization (from current engine)
    enable_query_optimization: bool = True
    enable_parallel_processing: bool = True
    max_worker_threads: int = 4
    
    # Memory management
    enable_memory_pooling: bool = True
    chunk_size_mb: int = 100
    cleanup_interval: int = 10000
    enable_cleanup: bool = True
    
    # Expression caching
    cache_expressions: bool = True
    max_cache_size: int = 1000
    
    # Monitoring
    enable_monitoring: bool = False
    detailed_timing: bool = False
    
    # Selectivity analysis (from current engine)
    enable_selectivity_analysis: bool = True
    enable_early_termination: bool = True
    selectivity_sample_size: int = 100
    
    # Compatibility mode
    strict_compatibility: bool = False  # Strict API compatibility with original
```

### Phase 3: Integration with dataframe_ternary_filters

#### 3.1 Expression Building Integration

```python
class TernaryFilterExpressionBuilder:
    """
    Expression builder using dataframe_ternary_filters.
    
    Replaces PolarsExpressionBuilder with ternary filter visitor pattern.
    """
    
    def __init__(self, provider: RuleEvaluationProvider):
        self.provider = provider
        self.visitor = provider.get_filter_visitor()
    
    def build_dimension_expression(self,
                                   dimension: Dimension,
                                   context_value: Any) -> FilterNode:
        """Build dimension expression using ternary filters."""
        
        return create_rule_match_condition(
            dimension=dimension,
            context_value=context_value,
            enable_ternary=True
        )
    
    def combine_dimension_expressions(self,
                                     expressions: List[FilterNode]) -> FilterNode:
        """Combine expressions using ternary ALL_TRUE logic."""
        
        return create_ternary_all_condition(
            conditions=expressions,
            enable_optimization=True
        )
    
    def generate_backend_expression(self, filter_node: FilterNode) -> Any:
        """Generate backend-specific expression through visitor."""
        
        return filter_node.accept(self.visitor)
```

### Phase 4: Production Features

#### 4.1 Performance Monitoring

```python
class PerformanceMonitor:
    """Lightweight performance monitoring."""
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        if enabled:
            self.metrics = {
                'total_evaluations': 0,
                'total_time': 0.0,
                'provider_usage': defaultdict(int),
                'recent_times': deque(maxlen=100)
            }
    
    @contextmanager
    def time_evaluation(self, provider: str):
        """Time an evaluation with minimal overhead."""
        if not self.enabled:
            yield
            return
        
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.metrics['total_evaluations'] += 1
            self.metrics['total_time'] += elapsed
            self.metrics['provider_usage'][provider] += 1
            self.metrics['recent_times'].append(elapsed)
```

#### 4.2 Memory Management

```python
class MemoryManager:
    """Memory management for long-running processes."""
    
    def __init__(self, cleanup_interval: int = 10000):
        self.cleanup_interval = cleanup_interval
        self.evaluation_count = 0
        self._cached_objects = weakref.WeakSet()
    
    def check_and_cleanup(self):
        """Check if cleanup is needed and perform it."""
        self.evaluation_count += 1
        
        if self.evaluation_count % self.cleanup_interval == 0:
            self.perform_cleanup()
    
    def perform_cleanup(self):
        """Perform memory cleanup."""
        # Clear expression caches
        for obj in self._cached_objects:
            if hasattr(obj, 'clear_cache'):
                obj.clear_cache()
        
        # Optional garbage collection
        gc.collect()
```

## Migration Strategy

### Backward Compatibility

1. **API Compatibility**: The enhanced engine maintains the same public API as the original `RulesEngine`
2. **Default Behavior**: By default, uses Polars provider matching current performance
3. **Optional Features**: All new features are optional with zero overhead when disabled

### Migration Path

```python
# Current usage (unchanged)
engine = VectorizedRulesEngine(rules, dimensions)
result = engine.apply_context_rules_engine(context, dimension_names)

# Enhanced usage (opt-in to new features)
config = VectorizedEngineConfig(
    provider="ibis_duckdb",  # Different backend
    enable_monitoring=True,   # Production monitoring
    enable_cleanup=True       # Memory management
)
engine = EnhancedVectorizedRulesEngine(rules, dimension_metadata, config)
result = engine.apply_context_rules_engine(context, dimension_names)
```

## Testing Strategy

### Test Categories

1. **Compatibility Tests**
   - Ensure enhanced engine produces same results as original
   - Verify API compatibility
   - Test with existing test suite

2. **Provider Tests**
   - Test each provider implementation
   - Verify ternary logic consistency across providers
   - Performance comparison tests

3. **Integration Tests**
   - Test ternary filter visitor integration
   - Test expression building pipeline
   - Test with real-world rule sets

4. **Production Tests**
   - Memory leak tests for long-running processes
   - Performance monitoring accuracy tests
   - Cleanup mechanism tests

### Test Implementation

```python
class TestEnhancedVectorizedEngine:
    """Test suite for enhanced vectorized engine."""
    
    def test_compatibility_with_original(self):
        """Ensure results match original engine."""
        original = RulesEngine(rules, dimension_metadata)
        enhanced = EnhancedVectorizedRulesEngine(rules, dimension_metadata)
        
        original_result = original.apply_context_rules_engine(context, dims)
        enhanced_result = enhanced.apply_context_rules_engine(context, dims)
        
        assert_dataframes_equal(original_result, enhanced_result)
    
    def test_provider_consistency(self):
        """Test consistency across different providers."""
        polars_engine = create_engine(provider="polars")
        ibis_engine = create_engine(provider="ibis_polars")
        
        polars_result = polars_engine.apply_context_rules_engine(context, dims)
        ibis_result = ibis_engine.apply_context_rules_engine(context, dims)
        
        assert_results_equivalent(polars_result, ibis_result)
    
    def test_ternary_filter_integration(self):
        """Test ternary filter visitor integration."""
        engine = EnhancedVectorizedRulesEngine(rules, dimension_metadata)
        
        # Verify ternary logic is being used
        result = engine.apply_context_rules_engine(context, dims)
        
        # Check for prime-based ternary flags in intermediate results
        assert_ternary_logic_applied(result)
```

## Performance Considerations

### Performance Goals

1. **Maintain Current Performance**: Polars provider should match current engine speed
2. **Minimal Overhead**: Optional features should have zero overhead when disabled
3. **Efficient Caching**: Expression caching should improve repeated evaluations
4. **Memory Efficiency**: Prevent memory leaks in long-running processes

### Benchmarking Plan

```python
def benchmark_enhanced_engine():
    """Benchmark enhanced engine against current implementation."""
    
    # Setup
    rules = generate_test_rules(10000)
    dimensions = generate_dimensions(10)
    contexts = generate_contexts(1000)
    
    # Current engine
    current_engine = VectorizedRulesEngine(rules, dimensions)
    current_times = []
    
    for context in contexts:
        start = time.perf_counter()
        current_engine.apply_context_rules_engine(context, dimension_names)
        current_times.append(time.perf_counter() - start)
    
    # Enhanced engine (Polars provider)
    enhanced_engine = EnhancedVectorizedRulesEngine(
        rules, 
        dimension_metadata,
        VectorizedEngineConfig(provider="polars")
    )
    enhanced_times = []
    
    for context in contexts:
        start = time.perf_counter()
        enhanced_engine.apply_context_rules_engine(context, dimension_names)
        enhanced_times.append(time.perf_counter() - start)
    
    # Compare
    print(f"Current avg: {np.mean(current_times):.4f}s")
    print(f"Enhanced avg: {np.mean(enhanced_times):.4f}s")
    print(f"Overhead: {(np.mean(enhanced_times) / np.mean(current_times) - 1) * 100:.2f}%")
```

## Implementation Timeline

### Phase 1: Foundation (Week 1)
- [ ] Implement provider interface
- [ ] Create Polars provider
- [ ] Integrate ternary filter visitor
- [ ] Basic testing framework

### Phase 2: Enhancement (Week 2)
- [ ] Implement Ibis provider
- [ ] Add performance monitoring
- [ ] Add memory management
- [ ] Configuration system

### Phase 3: Integration (Week 3)
- [ ] Full ternary filter integration
- [ ] API compatibility layer
- [ ] Comprehensive testing
- [ ] Performance benchmarking

### Phase 4: Production (Week 4)
- [ ] Documentation
- [ ] Migration guide
- [ ] Performance tuning
- [ ] Release preparation

## Risk Mitigation

### Identified Risks

1. **Performance Regression**: Enhanced features might slow down evaluation
   - **Mitigation**: Optional features with zero overhead when disabled
   
2. **API Breaking Changes**: Changes might break existing code
   - **Mitigation**: Maintain strict API compatibility, new features are opt-in
   
3. **Provider Inconsistency**: Different providers might produce different results
   - **Mitigation**: Comprehensive testing across all providers
   
4. **Complexity Increase**: Added features might make code harder to maintain
   - **Mitigation**: Clean separation of concerns, clear documentation

## Success Criteria

1. **Performance**: No regression in Polars provider performance
2. **Compatibility**: 100% API compatibility with original engine
3. **Flexibility**: Support for at least 3 different backends
4. **Production Ready**: Memory management prevents leaks in 24-hour runs
5. **Testing**: 95%+ code coverage with all tests passing

## Conclusion

This architecture and improvement plan provides a clear path to enhance the VectorizedRulesEngine with meaningful production features while maintaining its current performance excellence. The integration with dataframe_ternary_filters provides a clean abstraction for expression building, while the provider strategy pattern enables backend flexibility without sacrificing the simplicity that makes the current engine effective.

The plan prioritizes:
- **Practical improvements** that solve real problems
- **Optional features** with zero overhead when disabled
- **Clean architecture** with clear separation of concerns
- **Production readiness** with monitoring and memory management
- **Backward compatibility** to protect existing users

By following this plan, we can create an enhanced VectorizedRulesEngine that maintains the performance and simplicity of the current implementation while adding the flexibility and production features needed for real-world deployments.