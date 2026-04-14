"""
Quick backend benchmark for initial baseline
"""

from tests.benchmarks.backend_comparison import BackendBenchmarkSuite
from tests.benchmarks.test_data_generator import BenchmarkConfig

def main():
    # Create benchmark suite
    suite = BackendBenchmarkSuite()
    
    # Single test with moderate size
    rule_count = 2000
    dimension_count = 5
    
    print('=== Quick Backend Baseline ===')
    print(f'Testing with {rule_count} rules, {dimension_count} dimensions')
    print('Backends: sqlite vs duckdb')
    print()
    
    config = BenchmarkConfig(rule_count=rule_count, dimension_count=dimension_count)
    comparison = suite.run_backend_comparison(config)
    
    # Generate summary
    print('=== Results Summary ===')
    print('Backend | Init Time | Eval Time | Memory Usage')
    print('--------|-----------|-----------|-------------')
    
    for backend_name, results in comparison.comparisons.items():
        # Get initialization time
        init_time = results.get(f'init_{backend_name}', None)
        init_ms = init_time.execution_time_ms if init_time else 0
        
        # Get evaluation time (medium selectivity)
        eval_time = results.get(f'eval_medium_selectivity_{backend_name}', None) 
        eval_ms = eval_time.execution_time_ms if eval_time else 0
        eval_mem = eval_time.peak_memory_mb if eval_time else 0
        
        print(f'{backend_name:7} | {init_ms:6.0f}ms | {eval_ms:6.0f}ms | {eval_mem:6.1f}MB')
    
    # Performance comparison
    sqlite_results = comparison.comparisons.get('sqlite', {})
    duckdb_results = comparison.comparisons.get('duckdb', {})
    
    sqlite_eval = sqlite_results.get('eval_medium_selectivity_sqlite')
    duckdb_eval = duckdb_results.get('eval_medium_selectivity_duckdb')
    
    if sqlite_eval and duckdb_eval:
        sqlite_time = sqlite_eval.execution_time_ms
        duckdb_time = duckdb_eval.execution_time_ms
        
        if sqlite_time < duckdb_time:
            ratio = duckdb_time / sqlite_time
            print(f'\n🏆 SQLite is {ratio:.1f}x faster than DuckDB')
        else:
            ratio = sqlite_time / duckdb_time
            print(f'\n🏆 DuckDB is {ratio:.1f}x faster than SQLite')
    
    # Save results
    suite.save_benchmark_results(comparison, 'quick_baseline')
    
    print('\nBaseline benchmark completed!')
    print('Results saved to benchmark_results/ directory')

if __name__ == "__main__":
    main()