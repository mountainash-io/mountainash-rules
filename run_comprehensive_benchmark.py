"""
Run comprehensive backend benchmarks
"""

from tests.benchmarks.backend_comparison import BackendBenchmarkSuite
from tests.benchmarks.test_data_generator import BenchmarkConfig
import time

def main():
    # Create benchmark suite
    suite = BackendBenchmarkSuite()
    
    # Test different rule counts
    rule_counts = [1000, 5000, 10000]
    dimension_count = 5
    
    print('=== Comprehensive Backend Benchmark ===')
    print(f'Testing {len(rule_counts)} different rule set sizes')
    print('Backends: sqlite vs duckdb')
    print()
    
    all_results = {}
    summary_data = []
    
    for rule_count in rule_counts:
        print(f'Testing with {rule_count} rules...')
        config = BenchmarkConfig(rule_count=rule_count, dimension_count=dimension_count)
        
        start_time = time.time()
        comparison = suite.run_backend_comparison(config)
        end_time = time.time()
        
        all_results[f'rules_{rule_count}'] = comparison
        
        # Collect summary data
        row_data = {'rule_count': rule_count, 'test_time': end_time - start_time}
        
        # Quick summary
        for backend_name, results in comparison.comparisons.items():
            if f'eval_medium_selectivity_{backend_name}' in results:
                metrics = results[f'eval_medium_selectivity_{backend_name}']
                row_data[f'{backend_name}_eval_time'] = metrics.execution_time_ms
                row_data[f'{backend_name}_memory'] = metrics.peak_memory_mb
                print(f'  {backend_name}: {metrics.execution_time_ms:.0f}ms, {metrics.peak_memory_mb:.1f}MB')
        
        summary_data.append(row_data)
        print()
    
    # Save all results
    for config_name, comparison in all_results.items():
        suite.save_benchmark_results(comparison, f'comprehensive_{config_name}')
    
    # Generate final summary
    print('=== Final Summary ===')
    print('Rule Count | SQLite Time | DuckDB Time | Performance Ratio | SQLite Memory | DuckDB Memory')
    print('-----------|-------------|-------------|-------------------|---------------|---------------')
    
    for row in summary_data:
        rule_count = row['rule_count']
        sqlite_time = row.get('sqlite_eval_time', 0)
        duckdb_time = row.get('duckdb_eval_time', 0)
        sqlite_memory = row.get('sqlite_memory', 0)
        duckdb_memory = row.get('duckdb_memory', 0)
        
        if sqlite_time > 0 and duckdb_time > 0:
            ratio = sqlite_time / duckdb_time
            print(f'{rule_count:10d} | {sqlite_time:8.0f}ms | {duckdb_time:8.0f}ms | {ratio:10.2f}x     | {sqlite_memory:8.1f}MB | {duckdb_memory:8.1f}MB')
        else:
            print(f'{rule_count:10d} | {"N/A":>8} | {"N/A":>8} | {"N/A":>10} | {"N/A":>8} | {"N/A":>8}')
    
    print()
    print('Comprehensive benchmarks completed!')
    print('Detailed results saved to benchmark_results/ directory')
    
    # Determine winner
    if len(summary_data) > 0 and summary_data[0].get('sqlite_eval_time') and summary_data[0].get('duckdb_eval_time'):
        avg_sqlite = sum(row.get('sqlite_eval_time', 0) for row in summary_data) / len(summary_data)
        avg_duckdb = sum(row.get('duckdb_eval_time', 0) for row in summary_data) / len(summary_data)
        
        if avg_sqlite < avg_duckdb:
            print(f'🏆 SQLite is faster on average: {avg_sqlite:.0f}ms vs {avg_duckdb:.0f}ms ({avg_duckdb/avg_sqlite:.1f}x slower)')
        else:
            print(f'🏆 DuckDB is faster on average: {avg_duckdb:.0f}ms vs {avg_sqlite:.0f}ms ({avg_sqlite/avg_duckdb:.1f}x slower)')

if __name__ == "__main__":
    main()