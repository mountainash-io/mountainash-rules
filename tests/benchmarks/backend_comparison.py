"""
Backend performance comparison benchmarks.
Compares sqlite, duckdb, and polars backends for the rules engine.
"""

import pytest
from typing import Dict, List, Any
from pathlib import Path
import json
from datetime import datetime

from mountainash_utils_rules import RulesEngine
from mountainash_dataframes import DataFrameFactory

from .performance_framework import PerformanceProfiler, BenchmarkComparison
from .test_data_generator import TestDataGenerator, BenchmarkTestCases, BenchmarkConfig


class BackendBenchmarkSuite:
    """Comprehensive backend performance comparison suite"""
    
    def __init__(self, output_dir: str = "benchmark_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.data_generator = TestDataGenerator()
        
        # Test configurations (exclude polars for now due to window function issues)
        self.backend_configs = {
            'sqlite': 'sqlite',
            'duckdb': 'duckdb'
            # 'polars': 'polars'  # Temporarily disabled due to window function translation issue
        }
        
    def create_rules_engine(self, backend_name: str, rules_df, dimension_metadata) -> RulesEngine:
        """Create rules engine with specified backend"""
        # Convert to ibis dataframe with specific backend
        rules_ibis = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(
            rules_df, 
            ibis_backend_schema=self.backend_configs[backend_name]
        )
        
        return RulesEngine(rules=rules_ibis, dimension_metadata=dimension_metadata)
    
    def run_single_backend_benchmark(self, backend_name: str, config: BenchmarkConfig) -> PerformanceProfiler:
        """Run comprehensive benchmark for a single backend"""
        profiler = PerformanceProfiler(f"backend_{backend_name}")
        
        # Generate test data
        rules_df = self.data_generator.generate_rules_dataframe(config.rule_count)
        dimension_metadata = self.data_generator.generate_dimension_metadata()
        
        # Create different context selectivities
        contexts = {
            'high_selectivity': self.data_generator.generate_test_context('low'),    # Few matches
            'medium_selectivity': self.data_generator.generate_test_context('medium'), # Some matches  
            'low_selectivity': self.data_generator.generate_test_context('high')     # Many matches
        }
        
        # Get dimension names
        dimension_names = [dim.dimension_name for dim in dimension_metadata.dimensions]
        
        # Test 1: Engine initialization
        with profiler.measure(f"init_{backend_name}"):
            engine = self.create_rules_engine(backend_name, rules_df, dimension_metadata)
        
        # Test 2: Rule evaluation with different selectivities
        for selectivity_name, context in contexts.items():
            with profiler.measure(f"eval_{selectivity_name}_{backend_name}"):
                result = engine.apply_context_rules_engine(
                    context=context,
                    dimension_names=dimension_names,
                    keep_all=True
                )
                # Force materialization to ensure complete execution
                _ = result.count()
        
        # Test 3: Rule evaluation with filtering (keep_all=False)
        for selectivity_name, context in contexts.items():
            with profiler.measure(f"eval_filtered_{selectivity_name}_{backend_name}"):
                result = engine.apply_context_rules_engine(
                    context=context,
                    dimension_names=dimension_names,
                    keep_all=False
                )
                # Force materialization
                _ = result.count()
        
        # Test 4: Multiple evaluations (engine reuse)
        def multiple_evaluations():
            for context in contexts.values():
                result = engine.apply_context_rules_engine(
                    context=context,
                    dimension_names=dimension_names,
                    keep_all=True
                )
                _ = result.count()
        
        # Run multiple times for statistical analysis
        profiler.measure_multiple_runs(
            f"multi_eval_{backend_name}", 
            multiple_evaluations, 
            iterations=3
        )
        
        return profiler
    
    def run_backend_comparison(self, config: BenchmarkConfig = None) -> BenchmarkComparison:
        """Run comparison across all backends"""
        if config is None:
            config = BenchmarkTestCases.get_backend_comparison_config()
        
        comparison = BenchmarkComparison("backend_comparison")
        
        print(f"Running backend comparison with {config.rule_count} rules, {config.dimension_count} dimensions...")
        
        for backend_name in self.backend_configs.keys():
            print(f"  Benchmarking {backend_name} backend...")
            try:
                profiler = self.run_single_backend_benchmark(backend_name, config)
                comparison.add_benchmark_results(backend_name, profiler)
                print(f"    ✓ {backend_name} completed")
            except Exception as e:
                print(f"    ✗ {backend_name} failed: {e}")
        
        return comparison
    
    def run_scalability_comparison(self, backends: List[str] = None) -> Dict[str, BenchmarkComparison]:
        """Run scalability comparison across backends"""
        if backends is None:
            backends = list(self.backend_configs.keys())
        
        scalability_configs = BenchmarkTestCases.get_scalability_test_configs()
        results = {}
        
        print("Running scalability comparison...")
        
        for config in scalability_configs:
            config_name = f"rules_{config.rule_count}"
            print(f"  Testing with {config.rule_count} rules...")
            
            comparison = BenchmarkComparison(f"scalability_{config_name}")
            
            for backend_name in backends:
                if backend_name in self.backend_configs:
                    print(f"    Benchmarking {backend_name}...")
                    try:
                        profiler = self.run_single_backend_benchmark(backend_name, config)
                        comparison.add_benchmark_results(backend_name, profiler)
                        print(f"      ✓ {backend_name} completed")
                    except Exception as e:
                        print(f"      ✗ {backend_name} failed: {e}")
            
            results[config_name] = comparison
        
        return results
    
    def save_benchmark_results(self, comparison: BenchmarkComparison, filename: str):
        """Save benchmark results to files"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save detailed JSON results
        json_path = self.output_dir / f"{filename}_{timestamp}.json"
        detailed_results = {}
        
        for config_name, results in comparison.comparisons.items():
            detailed_results[config_name] = {}
            for test_name, metrics in results.items():
                detailed_results[config_name][test_name] = {
                    'execution_time_ms': metrics.execution_time_ms,
                    'peak_memory_mb': metrics.peak_memory_mb,
                    'cpu_percent': metrics.cpu_percent,
                    'timestamp': metrics.timestamp,
                    'iterations': metrics.iterations,
                    'statistics': metrics.get_statistics()
                }
        
        with open(json_path, 'w') as f:
            json.dump(detailed_results, f, indent=2)
        
        # Save markdown report
        md_path = self.output_dir / f"{filename}_{timestamp}.md"
        # Use sqlite as baseline for comparison
        baseline_backend = 'sqlite' if 'sqlite' in comparison.comparisons else list(comparison.comparisons.keys())[0]
        comparison.save_comparison_report(md_path, baseline_config=baseline_backend)
        
        print(f"Results saved to:")
        print(f"  JSON: {json_path}")
        print(f"  Report: {md_path}")
        
        return json_path, md_path


# Pytest fixtures for integration with test framework
@pytest.fixture(scope="session")
def benchmark_suite():
    """Create benchmark suite for session-level testing"""
    return BackendBenchmarkSuite()

@pytest.fixture(scope="session") 
def small_test_config():
    """Small test configuration for quick tests"""
    return BenchmarkConfig(rule_count=1000, dimension_count=3)

@pytest.fixture(scope="session")
def medium_test_config():
    """Medium test configuration for comprehensive tests"""
    return BenchmarkConfig(rule_count=5000, dimension_count=5)


class TestBackendPerformance:
    """Pytest test cases for backend performance"""
    
    def test_backend_initialization(self, benchmark_suite, small_test_config):
        """Test backend initialization performance"""
        print("\n=== Backend Initialization Performance ===")
        
        data_generator = TestDataGenerator(small_test_config)
        rules_df = data_generator.generate_rules_dataframe()
        dimension_metadata = data_generator.generate_dimension_metadata()
        
        results = {}
        
        for backend_name in benchmark_suite.backend_configs.keys():
            profiler = PerformanceProfiler(f"init_{backend_name}")
            
            try:
                with profiler.measure(f"initialization"):
                    engine = benchmark_suite.create_rules_engine(backend_name, rules_df, dimension_metadata)
                
                # Check if results were recorded
                if 'initialization' in profiler.results:
                    results[backend_name] = profiler.results['initialization'].execution_time_ms
                else:
                    print(f"  ✗ {backend_name}: No results recorded")
                    results[backend_name] = float('inf')
                    
            except Exception as e:
                print(f"  ✗ {backend_name}: {e}")
                results[backend_name] = float('inf')
        
        # Print results
        print("\nInitialization Times:")
        for backend, time_ms in sorted(results.items(), key=lambda x: x[1]):
            if time_ms == float('inf'):
                print(f"  {backend}: FAILED")
            else:
                print(f"  {backend}: {time_ms:.2f}ms")
        
        # Ensure at least one backend works
        working_backends = [b for b, t in results.items() if t != float('inf')]
        assert len(working_backends) > 0, "No backends successfully initialized"
    
    def test_backend_evaluation_performance(self, benchmark_suite, small_test_config):
        """Test rule evaluation performance across backends"""
        print("\n=== Backend Evaluation Performance ===")
        
        comparison = benchmark_suite.run_backend_comparison(small_test_config)
        
        # Verify we have results
        assert len(comparison.comparisons) > 0, "No benchmark results generated"
        
        # Print summary
        print("\nPerformance Summary:")
        for backend_name, results in comparison.comparisons.items():
            print(f"\n{backend_name.upper()} Backend:")
            for test_name, metrics in results.items():
                if 'eval_' in test_name:
                    print(f"  {test_name}: {metrics.execution_time_ms:.2f}ms, {metrics.peak_memory_mb:.2f}MB")
        
        # Save results
        benchmark_suite.save_benchmark_results(comparison, "backend_evaluation_test")
    
    @pytest.mark.slow
    def test_backend_scalability(self, benchmark_suite):
        """Test backend scalability (marked as slow)"""
        print("\n=== Backend Scalability Comparison ===")
        
        # Run scalability test with subset of configurations
        configs = BenchmarkTestCases.get_scalability_test_configs()[:3]  # First 3 sizes only
        
        scalability_results = {}
        for config in configs:
            config_name = f"rules_{config.rule_count}"
            comparison = benchmark_suite.run_backend_comparison(config)
            scalability_results[config_name] = comparison
        
        # Print summary
        print("\nScalability Summary:")
        for config_name, comparison in scalability_results.items():
            print(f"\n{config_name}:")
            for backend_name, results in comparison.comparisons.items():
                if 'eval_medium_selectivity_' + backend_name in results:
                    metrics = results['eval_medium_selectivity_' + backend_name]
                    print(f"  {backend_name}: {metrics.execution_time_ms:.2f}ms")
        
        assert len(scalability_results) > 0, "No scalability results generated"


# CLI runner for manual execution
def main():
    """Main function for running benchmarks from command line"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run backend performance benchmarks")
    parser.add_argument("--backends", nargs="+", default=["sqlite", "duckdb", "polars"],
                       help="Backends to benchmark")
    parser.add_argument("--rules", type=int, default=10000,
                       help="Number of rules for benchmark")
    parser.add_argument("--dimensions", type=int, default=5,
                       help="Number of dimensions for benchmark")
    parser.add_argument("--scalability", action="store_true",
                       help="Run scalability comparison")
    parser.add_argument("--output", default="benchmark_results",
                       help="Output directory")
    
    args = parser.parse_args()
    
    # Create benchmark suite
    suite = BackendBenchmarkSuite(args.output)
    
    if args.scalability:
        print("Running scalability comparison...")
        results = suite.run_scalability_comparison(args.backends)
        for config_name, comparison in results.items():
            suite.save_benchmark_results(comparison, f"scalability_{config_name}")
    else:
        config = BenchmarkConfig(rule_count=args.rules, dimension_count=args.dimensions)
        print(f"Running backend comparison with {args.rules} rules, {args.dimensions} dimensions...")
        comparison = suite.run_backend_comparison(config)
        suite.save_benchmark_results(comparison, "backend_comparison")
    
    print("Benchmark completed!")


if __name__ == "__main__":
    main()