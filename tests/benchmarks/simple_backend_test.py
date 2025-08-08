"""
Simple backend test to debug the benchmarking framework
"""

import time
from mountainash_utils_rules import RulesEngine, DimensionsMetadata, Dimension, MatchStrategy
from mountainash_dataframes import DataFrameFactory
from mountainash_utils_rules.constants import RuleConstants
import polars as pl
from pydantic import BaseModel

# Simple test context
class SimpleContext(BaseModel):
    DIM_1: str
    DIM_2: int

def test_simple_backend_comparison():
    """Simple test to verify backends work"""
    print("=== Simple Backend Test ===")
    
    # Create simple test data
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", RuleConstants.UNKNOWN],
        "DIM_2": [10, 20, 30]
    })
    
    # Create dimension metadata
    dimension_metadata = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.EXACT, data_type=int)
    ])
    
    # Test context
    context = SimpleContext(DIM_1="A", DIM_2=10)
    
    backends = ['sqlite', 'duckdb', 'polars']
    results = {}
    
    for backend in backends:
        try:
            print(f"\nTesting {backend} backend...")
            
            # Convert to ibis dataframe with specific backend
            rules_ibis = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(
                rules_df, 
                ibis_backend_schema=backend
            )
            
            # Create engine
            start_time = time.perf_counter()
            engine = RulesEngine(rules=rules_ibis, dimension_metadata=dimension_metadata)
            init_time = (time.perf_counter() - start_time) * 1000
            
            # Test evaluation
            start_time = time.perf_counter()
            result = engine.apply_context_rules_engine(
                context=context,
                dimension_names=["DIM_1", "DIM_2"],
                keep_all=True
            )
            # Force materialization
            count = result.count()
            eval_time = (time.perf_counter() - start_time) * 1000
            
            results[backend] = {
                'init_time_ms': init_time,
                'eval_time_ms': eval_time,
                'result_count': count,
                'success': True
            }
            
            print(f"  ✓ {backend}: init={init_time:.2f}ms, eval={eval_time:.2f}ms, results={count}")
            
        except Exception as e:
            results[backend] = {
                'init_time_ms': float('inf'),
                'eval_time_ms': float('inf'), 
                'result_count': 0,
                'success': False,
                'error': str(e)
            }
            print(f"  ✗ {backend}: {e}")
    
    # Print summary
    print("\n=== Summary ===")
    successful_backends = [b for b, r in results.items() if r['success']]
    print(f"Working backends: {successful_backends}")
    
    if successful_backends:
        print("\nPerformance comparison:")
        for backend in successful_backends:
            r = results[backend]
            print(f"  {backend}: {r['init_time_ms']:.2f}ms init, {r['eval_time_ms']:.2f}ms eval")
    
    return results

if __name__ == "__main__":
    test_simple_backend_comparison()