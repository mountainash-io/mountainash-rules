"""
Performance benchmarking framework for Mountain Ash Rules Engine.
Provides comprehensive timing, memory, and resource usage measurement.
"""

import time
import tracemalloc
import psutil
from contextlib import contextmanager
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import statistics

@dataclass
class PerformanceMetrics:
    """Container for comprehensive performance metrics"""
    test_name: str
    execution_time_ms: float
    peak_memory_mb: float
    cpu_percent: float
    timestamp: str
    iterations: int = 1
    
    # Additional metrics
    memory_current_mb: Optional[float] = None
    memory_peak_mb: Optional[float] = None
    
    # Statistics for multiple runs
    execution_times: List[float] = field(default_factory=list)
    
    def add_execution_time(self, time_ms: float):
        """Add execution time for statistical analysis"""
        self.execution_times.append(time_ms)
        
    def get_statistics(self) -> Dict[str, float]:
        """Get statistical summary of multiple runs"""
        if not self.execution_times:
            return {}
            
        return {
            'mean_ms': statistics.mean(self.execution_times),
            'median_ms': statistics.median(self.execution_times),
            'stdev_ms': statistics.stdev(self.execution_times) if len(self.execution_times) > 1 else 0,
            'min_ms': min(self.execution_times),
            'max_ms': max(self.execution_times),
            'count': len(self.execution_times)
        }

class PerformanceProfiler:
    """Comprehensive performance profiler for rules engine benchmarks"""
    
    def __init__(self, name: str = "benchmark"):
        self.name = name
        self.results: Dict[str, PerformanceMetrics] = {}
        self.process = psutil.Process()
        
    @contextmanager
    def measure(self, test_name: str, iterations: int = 1):
        """Context manager for measuring performance"""
        # Start memory tracing
        tracemalloc.start()
        
        # Record initial state
        start_time = time.perf_counter()
        start_memory = self.process.memory_info().rss / 1024 / 1024  # MB
        
        try:
            yield
        finally:
            # Record final state
            end_time = time.perf_counter()
            end_memory = self.process.memory_info().rss / 1024 / 1024  # MB
            
            # Get memory tracing info
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            
            # Calculate metrics
            execution_time = (end_time - start_time) * 1000  # Convert to ms
            peak_memory = peak / 1024 / 1024  # Convert to MB
            cpu_percent = self.process.cpu_percent()
            
            # Store results
            metrics = PerformanceMetrics(
                test_name=test_name,
                execution_time_ms=execution_time,
                peak_memory_mb=peak_memory,
                cpu_percent=cpu_percent,
                timestamp=datetime.now().isoformat(),
                iterations=iterations,
                memory_current_mb=current / 1024 / 1024,
                memory_peak_mb=peak_memory
            )
            
            self.results[test_name] = metrics
    
    def measure_multiple_runs(self, test_name: str, test_func: Callable, iterations: int = 5):
        """Run test multiple times for statistical analysis"""
        execution_times = []
        memory_peaks = []
        
        for i in range(iterations):
            with self.measure(f"{test_name}_run_{i}"):
                test_func()
            
            # Collect timing data
            run_metrics = self.results[f"{test_name}_run_{i}"]
            execution_times.append(run_metrics.execution_time_ms)
            memory_peaks.append(run_metrics.peak_memory_mb)
        
        # Create summary metrics
        summary_metrics = PerformanceMetrics(
            test_name=test_name,
            execution_time_ms=statistics.mean(execution_times),
            peak_memory_mb=statistics.mean(memory_peaks),
            cpu_percent=0,  # Not meaningful for average
            timestamp=datetime.now().isoformat(),
            iterations=iterations,
            execution_times=execution_times
        )
        
        self.results[test_name] = summary_metrics
        return summary_metrics
    
    def get_results(self) -> Dict[str, PerformanceMetrics]:
        """Get all performance results"""
        return self.results
    
    def save_results(self, output_path: str):
        """Save results to JSON file"""
        output_data = {}
        for test_name, metrics in self.results.items():
            output_data[test_name] = {
                'test_name': metrics.test_name,
                'execution_time_ms': metrics.execution_time_ms,
                'peak_memory_mb': metrics.peak_memory_mb,
                'cpu_percent': metrics.cpu_percent,
                'timestamp': metrics.timestamp,
                'iterations': metrics.iterations,
                'statistics': metrics.get_statistics()
            }
        
        with open(output_path, 'w') as f:
            json.dump(output_data, f, indent=2)

class BenchmarkComparison:
    """Compare performance between different configurations"""
    
    def __init__(self, name: str = "comparison"):
        self.name = name
        self.comparisons: Dict[str, Dict[str, PerformanceMetrics]] = {}
    
    def add_benchmark_results(self, config_name: str, profiler: PerformanceProfiler):
        """Add results from a performance profiler"""
        self.comparisons[config_name] = profiler.get_results()
    
    def compare_configurations(self, test_name: str) -> Dict[str, Dict[str, float]]:
        """Compare specific test across configurations"""
        comparison = {}
        
        for config_name, results in self.comparisons.items():
            if test_name in results:
                metrics = results[test_name]
                comparison[config_name] = {
                    'execution_time_ms': metrics.execution_time_ms,
                    'peak_memory_mb': metrics.peak_memory_mb,
                    'cpu_percent': metrics.cpu_percent
                }
        
        return comparison
    
    def get_performance_ratios(self, baseline_config: str, test_name: str) -> Dict[str, Dict[str, float]]:
        """Get performance ratios relative to baseline configuration"""
        if baseline_config not in self.comparisons:
            raise ValueError(f"Baseline configuration '{baseline_config}' not found")
        
        baseline_metrics = self.comparisons[baseline_config][test_name]
        ratios = {}
        
        for config_name, results in self.comparisons.items():
            if config_name == baseline_config or test_name not in results:
                continue
                
            metrics = results[test_name]
            ratios[config_name] = {
                'execution_time_ratio': metrics.execution_time_ms / baseline_metrics.execution_time_ms,
                'memory_ratio': metrics.peak_memory_mb / baseline_metrics.peak_memory_mb,
                'execution_improvement_pct': (1 - metrics.execution_time_ms / baseline_metrics.execution_time_ms) * 100,
                'memory_improvement_pct': (1 - metrics.peak_memory_mb / baseline_metrics.peak_memory_mb) * 100
            }
        
        return ratios
    
    def generate_summary_report(self, baseline_config: str = None) -> str:
        """Generate a text summary report"""
        report = [f"# Performance Comparison Report: {self.name}"]
        report.append(f"Generated: {datetime.now().isoformat()}")
        report.append("")
        
        # Get all test names
        all_tests = set()
        for results in self.comparisons.values():
            all_tests.update(results.keys())
        
        # Generate comparison for each test
        for test_name in sorted(all_tests):
            report.append(f"## Test: {test_name}")
            
            comparison = self.compare_configurations(test_name)
            if not comparison:
                report.append("No data available")
                continue
            
            # Basic comparison table
            report.append("| Configuration | Execution Time (ms) | Peak Memory (MB) | CPU % |")
            report.append("|---------------|-------------------|------------------|--------|")
            
            for config_name, metrics in comparison.items():
                report.append(f"| {config_name} | {metrics['execution_time_ms']:.2f} | {metrics['peak_memory_mb']:.2f} | {metrics['cpu_percent']:.1f} |")
            
            # Performance ratios if baseline specified
            if baseline_config and baseline_config in comparison:
                report.append("")
                report.append(f"### Performance vs {baseline_config} (baseline)")
                ratios = self.get_performance_ratios(baseline_config, test_name)
                
                for config_name, ratio_data in ratios.items():
                    exec_improvement = ratio_data['execution_improvement_pct']
                    mem_improvement = ratio_data['memory_improvement_pct']
                    report.append(f"- **{config_name}**: {exec_improvement:+.1f}% execution time, {mem_improvement:+.1f}% memory")
            
            report.append("")
        
        return "\n".join(report)
    
    def save_comparison_report(self, output_path: str, baseline_config: str = None):
        """Save comparison report to file"""
        report = self.generate_summary_report(baseline_config)
        with open(output_path, 'w') as f:
            f.write(report)

# Utility functions for common benchmark operations
def time_function(func: Callable, *args, **kwargs) -> float:
    """Time a single function execution in milliseconds"""
    start_time = time.perf_counter()
    result = func(*args, **kwargs)
    end_time = time.perf_counter()
    return (end_time - start_time) * 1000

def benchmark_function(func: Callable, iterations: int = 5, *args, **kwargs) -> Dict[str, float]:
    """Benchmark a function with statistical analysis"""
    times = []
    
    for _ in range(iterations):
        execution_time = time_function(func, *args, **kwargs)
        times.append(execution_time)
    
    return {
        'mean_ms': statistics.mean(times),
        'median_ms': statistics.median(times),
        'stdev_ms': statistics.stdev(times) if len(times) > 1 else 0,
        'min_ms': min(times),
        'max_ms': max(times),
        'iterations': iterations
    }