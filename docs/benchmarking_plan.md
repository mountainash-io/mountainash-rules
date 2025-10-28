# Rules Engine Performance Benchmarking Plan

**Date**: 2025-08-08  
**Version**: Mountain Ash Utils Rules v25.x  
**Purpose**: Validate performance improvements across optimization phases

## Overview

This document outlines the comprehensive benchmarking strategy to measure, validate, and monitor performance improvements throughout the rules engine optimization project. The benchmarking plan ensures objective measurement of the 20-95% performance improvements targeted across the three optimization phases.

## Benchmarking Objectives

### Primary Goals
1. **Baseline Establishment**: Measure current performance across various scenarios
2. **Improvement Validation**: Quantify performance gains for each optimization phase  
3. **Regression Detection**: Identify any performance regressions during development
4. **Scalability Assessment**: Validate linear scaling characteristics
5. **Production Monitoring**: Ongoing performance monitoring in production environments

### Success Criteria
- **Phase 1**: 20-40% improvement in processing time and memory usage
- **Phase 2**: 50-80% improvement with maintained accuracy  
- **Phase 3**: 80-95% improvement with linear scalability
- **Accuracy**: 100% functional correctness across all performance improvements

## Benchmarking Framework

### Test Environment Specifications
```yaml
Hardware Configuration:
  CPU: 8-core minimum (Intel/AMD x64)
  Memory: 32GB minimum  
  Storage: SSD with >1GB/s throughput
  Network: Isolated from external dependencies

Software Configuration:
  OS: Ubuntu 22.04 LTS
  Python: 3.12+
  Dependencies: Latest versions of all required packages
  Monitoring: Memory profilers, CPU profilers, custom timing utilities
```

### Benchmarking Infrastructure
```python
# Core benchmarking framework
class RulesEngineBenchmark:
    """Comprehensive benchmarking suite for rules engine performance"""
    
    def __init__(self, name: str, engine_factory: callable):
        self.name = name
        self.engine_factory = engine_factory
        self.results = {}
        self.memory_profiler = MemoryProfiler()
        self.time_profiler = TimeProfiler()
    
    def run_benchmark_suite(self):
        """Execute complete benchmark suite"""
        self.run_scalability_tests()
        self.run_dimension_complexity_tests()
        self.run_match_strategy_tests()
        self.run_memory_tests()
        self.run_concurrent_access_tests()
        
    def run_scalability_tests(self):
        """Test performance scaling with rule count"""
        rule_counts = [100, 500, 1000, 5000, 10000, 50000, 100000]
        for count in rule_counts:
            self._measure_performance(f"scalability_{count}", 
                                    self._generate_rules(count))
    
    def _measure_performance(self, test_name: str, rules_df):
        """Core performance measurement method"""
        with self.time_profiler.measure(test_name):
            with self.memory_profiler.measure(test_name):
                engine = self.engine_factory(rules_df)
                result = engine.apply_context_rules_engine(
                    context=self.test_context,
                    dimension_names=self.dimension_names
                )
                # Force materialization for accurate measurement
                _ = result.to_pylist()
```

## Test Scenarios

### Scenario 1: Scalability Testing
**Objective**: Measure performance scaling with increasing rule counts

```python
class ScalabilityBenchmark:
    """Test performance across different rule set sizes"""
    
    RULE_COUNTS = [100, 500, 1000, 5000, 10000, 25000, 50000, 100000]
    DIMENSIONS = 5  # Standard dimension count
    
    def generate_test_cases(self):
        """Generate test cases for scalability testing"""
        test_cases = []
        
        for rule_count in self.RULE_COUNTS:
            # Create balanced rule distribution
            rules_df = pl.DataFrame({
                "rule_name": [f"rule_{i}" for i in range(rule_count)],
                "DIM_1": self._generate_exact_values(rule_count),
                "DIM_2_MIN": self._generate_range_mins(rule_count),
                "DIM_2_MAX": self._generate_range_maxs(rule_count),
                "DIM_3": self._generate_regex_patterns(rule_count),
                "DIM_4": self._generate_exact_values(rule_count),
                "DIM_5": self._generate_mixed_values(rule_count)
            })
            
            test_cases.append({
                'name': f'scalability_{rule_count}',
                'rules_df': rules_df,
                'expected_matches': self._calculate_expected_matches(rules_df)
            })
            
        return test_cases
    
    def _generate_exact_values(self, count: int) -> List[str]:
        """Generate realistic exact match values"""
        values = ['A', 'B', 'C', 'D', 'E', RuleConstants.UNKNOWN]
        return [random.choice(values) for _ in range(count)]
```

### Scenario 2: Dimension Complexity Testing  
**Objective**: Measure performance impact of increasing dimension counts

```python
class DimensionComplexityBenchmark:
    """Test performance across different dimension counts"""
    
    DIMENSION_COUNTS = [1, 3, 5, 10, 15, 20, 25]
    RULE_COUNT = 10000  # Fixed rule count
    
    def generate_dimension_test_cases(self):
        """Generate test cases with varying dimension complexity"""
        test_cases = []
        
        for dim_count in self.DIMENSION_COUNTS:
            # Create rules with specified dimension count
            rules_data = {"rule_name": [f"rule_{i}" for i in range(self.RULE_COUNT)]}
            dimension_metadata = []
            
            for dim_idx in range(dim_count):
                dim_name = f"DIM_{dim_idx + 1}"
                
                if dim_idx % 3 == 0:  # Exact match
                    rules_data[dim_name] = self._generate_exact_values(self.RULE_COUNT)
                    dimension_metadata.append(
                        Dimension(dimension_name=dim_name, match_strategy=MatchStrategy.EXACT)
                    )
                elif dim_idx % 3 == 1:  # Range match
                    rules_data[f"{dim_name}_MIN"] = self._generate_range_values(self.RULE_COUNT, 'min')
                    rules_data[f"{dim_name}_MAX"] = self._generate_range_values(self.RULE_COUNT, 'max')
                    dimension_metadata.append(
                        Dimension(dimension_name=dim_name, match_strategy=MatchStrategy.RANGE,
                                range_min_field=f"{dim_name}_MIN", range_max_field=f"{dim_name}_MAX")
                    )
                else:  # Regex match
                    rules_data[dim_name] = self._generate_regex_patterns(self.RULE_COUNT)
                    dimension_metadata.append(
                        Dimension(dimension_name=dim_name, match_strategy=MatchStrategy.REGEX)
                    )
            
            test_cases.append({
                'name': f'dimensions_{dim_count}',
                'rules_df': pl.DataFrame(rules_data),
                'dimensions': dimension_metadata,
                'context': self._generate_test_context(dim_count)
            })
            
        return test_cases
```

### Scenario 3: Match Strategy Performance
**Objective**: Compare performance of different matching strategies

```python
class MatchStrategyBenchmark:
    """Test performance of individual match strategies"""
    
    def test_exact_match_performance(self):
        """Benchmark exact match strategy performance"""
        # High selectivity (few matches)
        self._test_exact_strategy(selectivity=0.1, name="exact_high_selectivity")
        
        # Medium selectivity  
        self._test_exact_strategy(selectivity=0.5, name="exact_medium_selectivity")
        
        # Low selectivity (many matches)
        self._test_exact_strategy(selectivity=0.9, name="exact_low_selectivity")
    
    def test_range_match_performance(self):
        """Benchmark range match strategy performance"""  
        # Narrow ranges (high selectivity)
        self._test_range_strategy(range_width=10, name="range_narrow")
        
        # Medium ranges
        self._test_range_strategy(range_width=50, name="range_medium")
        
        # Wide ranges (low selectivity)
        self._test_range_strategy(range_width=200, name="range_wide")
    
    def test_regex_match_performance(self):
        """Benchmark regex match strategy performance"""
        # Simple patterns
        self._test_regex_strategy(complexity='simple', name="regex_simple")
        
        # Complex patterns
        self._test_regex_strategy(complexity='complex', name="regex_complex")
        
        # Mixed patterns
        self._test_regex_strategy(complexity='mixed', name="regex_mixed")
```

### Scenario 4: Memory Usage Testing
**Objective**: Monitor memory consumption patterns

```python
class MemoryBenchmark:
    """Memory usage and efficiency testing"""
    
    def test_memory_scaling(self):
        """Test memory usage scaling with rule count"""
        rule_counts = [1000, 5000, 10000, 50000, 100000]
        
        for rule_count in rule_counts:
            with MemoryProfiler(f"memory_scaling_{rule_count}") as profiler:
                rules_df = self._generate_large_ruleset(rule_count)
                engine = self.create_engine(rules_df)
                
                # Measure baseline memory
                profiler.checkpoint("baseline")
                
                # Measure engine initialization memory
                profiler.checkpoint("engine_init")
                
                # Measure evaluation memory
                result = engine.apply_context_rules_engine(
                    context=self.test_context,
                    dimension_names=self.dimension_names
                )
                profiler.checkpoint("evaluation")
                
                # Measure result materialization memory
                _ = result.to_pylist()
                profiler.checkpoint("materialization")
                
    def test_memory_efficiency(self):
        """Test memory efficiency optimizations"""
        # Test temporary column cleanup
        self._test_temporary_column_cleanup()
        
        # Test memory reuse
        self._test_memory_reuse_patterns()
        
        # Test large dataset handling
        self._test_large_dataset_memory()
```

## Performance Baselines

### Current Implementation Baseline
```yaml
Current Performance Profile (10K rules, 5 dimensions):
  Processing Time: ~1200ms ± 200ms
  Memory Usage: ~150MB peak
  Memory Efficiency: 60% (40% temporary columns)
  CPU Usage: 85% single-core utilization
  Scalability: O(n²) with high constant factors

Breakdown by Component:
  Context Extraction: ~50ms per dimension (250ms total)
  Dimension Processing: ~180ms per dimension (900ms total) 
  Flag Calculations: ~30ms
  Priority Ranking: ~20ms
  Result Materialization: ~50ms
```

### Target Performance Profiles

#### Phase 1 Targets (20-40% improvement)
```yaml
Phase 1 Performance Profile:
  Processing Time: 720-960ms (40-20% improvement)
  Memory Usage: ~100MB peak (33% improvement)
  Memory Efficiency: 75% (25% temporary columns)
  CPU Usage: 80% single-core (5% improvement)
  Scalability: O(n²) with reduced constants

Expected Improvements:
  Context Extraction: ~15ms total (one-time extraction)
  Dimension Processing: ~600-750ms (combined operations)
  Flag Calculations: ~10ms (simplified logic)
  Backend Overhead: 30% reduction (DuckDB vs SQLite)
```

#### Phase 2 Targets (50-80% improvement)  
```yaml
Phase 2 Performance Profile:
  Processing Time: 240-600ms (80-50% improvement)
  Memory Usage: ~80MB peak (47% improvement)
  Memory Efficiency: 85% (15% temporary columns)
  CPU Usage: 95% single-core (vectorized operations)
  Scalability: O(n) with moderate constants

Expected Improvements:
  Numpy Vectorization: 5-10x faster core operations
  Memory Layout: Optimized array operations
  Batch Processing: Reduced per-dimension overhead
  Algorithmic: O(n) instead of O(n²) complexity
```

#### Phase 3 Targets (80-95% improvement)
```yaml
Phase 3 Performance Profile:
  Processing Time: 60-240ms (95-80% improvement)
  Memory Usage: ~45MB peak (70% improvement)  
  Memory Efficiency: 95% (5% temporary data)
  CPU Usage: 98% utilization (pure vectorization)
  Scalability: O(n) with minimal constants

Expected Improvements:
  Single-Pass Processing: Eliminate intermediate steps
  Polars Optimization: Native vectorized operations
  Memory Management: Minimal allocation overhead
  Advanced Algorithms: Query plan optimization
```

## Benchmarking Tools and Utilities

### Performance Measurement Framework
```python
class ComprehensiveProfiler:
    """Integrated profiling for time, memory, and system resources"""
    
    def __init__(self, benchmark_name: str):
        self.benchmark_name = benchmark_name
        self.time_profiler = TimeProfiler()
        self.memory_profiler = MemoryProfiler()
        self.cpu_profiler = CPUProfiler()
        self.results = {}
    
    @contextmanager
    def profile_execution(self, test_name: str):
        """Profile complete execution including all metrics"""
        with self.time_profiler.measure(test_name) as time_ctx:
            with self.memory_profiler.measure(test_name) as memory_ctx:
                with self.cpu_profiler.measure(test_name) as cpu_ctx:
                    start_time = time.perf_counter()
                    yield
                    end_time = time.perf_counter()
                    
        # Collect comprehensive metrics
        self.results[test_name] = {
            'execution_time_ms': (end_time - start_time) * 1000,
            'peak_memory_mb': memory_ctx.peak_usage / 1024 / 1024,
            'cpu_utilization': cpu_ctx.average_utilization,
            'memory_efficiency': memory_ctx.efficiency_ratio,
            'timestamp': datetime.now().isoformat()
        }

class BenchmarkComparison:
    """Compare performance between different engine implementations"""
    
    def compare_engines(self, engines: Dict[str, RulesEngine], test_cases: List[Dict]):
        """Run comparative benchmarks across multiple engines"""
        results = {}
        
        for engine_name, engine in engines.items():
            results[engine_name] = {}
            
            for test_case in test_cases:
                with ComprehensiveProfiler(f"{engine_name}_{test_case['name']}") as profiler:
                    with profiler.profile_execution(test_case['name']):
                        result = engine.apply_context_rules_engine(
                            context=test_case['context'],
                            dimension_names=test_case['dimensions']
                        )
                        # Force materialization
                        materialized = result.to_pylist()
                        
                        # Validate correctness
                        self._validate_result_correctness(
                            materialized, 
                            test_case['expected_results']
                        )
                
                results[engine_name][test_case['name']] = profiler.results[test_case['name']]
        
        return results
```

### Automated Benchmark Execution
```python
class BenchmarkRunner:
    """Automated benchmark execution and reporting"""
    
    def __init__(self, output_dir: str = "benchmark_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
    def run_complete_benchmark_suite(self):
        """Execute comprehensive benchmark suite"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Phase 1: Baseline measurement
        baseline_results = self._run_baseline_benchmarks()
        
        # Phase 2: Optimization comparison
        optimization_results = self._run_optimization_benchmarks()
        
        # Phase 3: Scalability validation  
        scalability_results = self._run_scalability_benchmarks()
        
        # Generate comprehensive report
        report = BenchmarkReport(
            baseline=baseline_results,
            optimizations=optimization_results,
            scalability=scalability_results,
            timestamp=timestamp
        )
        
        # Save results
        self._save_benchmark_results(report, timestamp)
        self._generate_html_report(report, timestamp)
        
        return report
```

## Continuous Monitoring

### Performance Regression Detection
```python
class PerformanceMonitor:
    """Continuous performance monitoring and regression detection"""
    
    def __init__(self, baseline_file: str):
        self.baseline = self._load_baseline(baseline_file)
        self.alerts = []
    
    def check_performance_regression(self, current_results: Dict):
        """Detect performance regressions against baseline"""
        regressions = []
        
        for test_name, current_metrics in current_results.items():
            if test_name in self.baseline:
                baseline_metrics = self.baseline[test_name]
                
                # Check execution time regression (>10% slower)
                time_regression = (
                    (current_metrics['execution_time_ms'] - baseline_metrics['execution_time_ms']) 
                    / baseline_metrics['execution_time_ms']
                )
                
                if time_regression > 0.1:  # 10% regression threshold
                    regressions.append({
                        'test': test_name,
                        'type': 'execution_time',
                        'regression_pct': time_regression * 100,
                        'current': current_metrics['execution_time_ms'],
                        'baseline': baseline_metrics['execution_time_ms']
                    })
                
                # Check memory regression (>15% increase)
                memory_regression = (
                    (current_metrics['peak_memory_mb'] - baseline_metrics['peak_memory_mb'])
                    / baseline_metrics['peak_memory_mb']
                )
                
                if memory_regression > 0.15:  # 15% regression threshold
                    regressions.append({
                        'test': test_name,
                        'type': 'memory_usage',
                        'regression_pct': memory_regression * 100,
                        'current': current_metrics['peak_memory_mb'],
                        'baseline': baseline_metrics['peak_memory_mb']
                    })
        
        return regressions
```

## Reporting and Analysis

### Benchmark Report Generation
```python
class BenchmarkReport:
    """Comprehensive benchmark reporting"""
    
    def generate_performance_comparison_chart(self):
        """Generate visual performance comparison charts"""
        fig, axes = plt.subplots(2, 2, figsize=(15, 12))
        
        # Execution time comparison
        self._plot_execution_time_comparison(axes[0, 0])
        
        # Memory usage comparison  
        self._plot_memory_usage_comparison(axes[0, 1])
        
        # Scalability analysis
        self._plot_scalability_analysis(axes[1, 0])
        
        # Performance improvement summary
        self._plot_improvement_summary(axes[1, 1])
        
        plt.tight_layout()
        return fig
    
    def generate_markdown_report(self) -> str:
        """Generate detailed markdown performance report"""
        report = f"""
# Rules Engine Performance Benchmark Report

**Generated**: {self.timestamp}
**Test Environment**: {self.test_environment}

## Executive Summary

### Performance Improvements
{self._generate_improvement_summary()}

### Key Findings
{self._generate_key_findings()}

## Detailed Results

### Scalability Testing
{self._generate_scalability_section()}

### Memory Usage Analysis
{self._generate_memory_analysis_section()}

### Match Strategy Performance
{self._generate_strategy_performance_section()}

## Recommendations
{self._generate_recommendations()}
        """
        return report
```

## Implementation Timeline

### Week 1-2: Benchmark Infrastructure Setup
- [ ] Implement core benchmarking framework
- [ ] Create test data generation utilities  
- [ ] Set up automated benchmark execution pipeline
- [ ] Establish baseline performance measurements

### Week 3-4: Phase 1 Validation
- [ ] Run comprehensive Phase 1 benchmarks
- [ ] Validate 20-40% improvement targets
- [ ] Document baseline vs. Phase 1 comparison
- [ ] Create performance regression test suite

### Week 5-7: Phase 2 Validation  
- [ ] Implement hybrid engine benchmarks
- [ ] Validate 50-80% improvement targets
- [ ] Cross-validate numpy vs. ibis accuracy
- [ ] Create scalability validation suite

### Week 8-10: Phase 3 Validation
- [ ] Implement vectorized engine benchmarks
- [ ] Validate 80-95% improvement targets
- [ ] Test linear scalability characteristics
- [ ] Create production monitoring framework

## Success Criteria

### Quantitative Metrics
- **Processing Time**: Achieve targeted improvements (20-95%) across all phases
- **Memory Usage**: Reduce peak memory consumption by 30-90%
- **Scalability**: Demonstrate linear scaling up to 1M+ rules
- **Accuracy**: Maintain 100% functional correctness across all optimizations

### Qualitative Metrics  
- **Reproducibility**: Benchmarks produce consistent results (±5% variance)
- **Comprehensive Coverage**: All major use cases and edge cases tested
- **Actionable Insights**: Clear recommendations for optimization priorities
- **Monitoring Integration**: Seamless integration with production monitoring

## Conclusion

This comprehensive benchmarking plan ensures objective validation of performance improvements while maintaining functional correctness. The phased approach allows for continuous validation and optimization throughout the development process, ensuring that the final optimized rules engine delivers the promised 20-95% performance improvements while maintaining reliability and accuracy.