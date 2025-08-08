#!/usr/bin/env python3
"""
Phase 2 Performance Validation - Hybrid Engine Benchmark

This script validates that Phase 2 implementation achieves the 50-80% performance
improvement target by comparing HybridRulesEngine with the original RulesEngine.
"""

import time
import statistics
import numpy as np
from typing import Dict, List, Any
import polars as pl
from pydantic import BaseModel

# Import both engines for comparison
from mountainash_utils_rules import (
    RulesEngine, 
    HybridRulesEngine,
    DimensionsMetadata, 
    Dimension, 
    MatchStrategy,
    create_performance_optimized_engine
)
from mountainash_dataframes import DataFrameFactory


class TestContext(BaseModel):
    DIM_1: str
    DIM_2: int
    DIM_3: str


def create_test_data(rule_count: int = 1000) -> tuple:
    """Create test data for benchmark comparison."""
    
    # Generate larger rule set for meaningful comparison
    rules_data = {
        'rule_name': [f'rule_{i}' for i in range(rule_count)],
        'DIM_1': ['A', 'B', 'C', 'D'] * (rule_count // 4) + ['A'] * (rule_count % 4),
        'DIM_2_MIN': list(range(0, rule_count * 10, 10)),
        'DIM_2_MAX': list(range(9, rule_count * 10 + 9, 10)),
        'DIM_3': [f'pattern_{i % 20}.*' for i in range(rule_count)]
    }
    
    rules_df = pl.DataFrame(rules_data)
    rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(
        rules_df, 
        ibis_backend_schema="duckdb"
    )
    
    # Define dimension metadata
    dimensions = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                 range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
        Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    ])
    
    # Test contexts with different selectivity
    test_contexts = [
        # High selectivity (few matches)
        TestContext(DIM_1="A", DIM_2=5, DIM_3="pattern_1_test"),
        TestContext(DIM_1="B", DIM_2=25, DIM_3="pattern_5_test"),
        TestContext(DIM_1="C", DIM_2=45, DIM_3="pattern_10_test"),
        
        # Medium selectivity
        TestContext(DIM_1="D", DIM_2=100, DIM_3="pattern_15_test"),
        TestContext(DIM_1="A", DIM_2=200, DIM_3="pattern_18_test"),
        
        # Low selectivity (many matches)
        TestContext(DIM_1="A", DIM_2=500, DIM_3="pattern_0_test"),
    ]
    
    return rules, dimensions, test_contexts


def benchmark_engine(engine_name: str, 
                    engine, 
                    test_contexts: List[TestContext],
                    active_dimensions: List[str],
                    iterations: int = 3) -> Dict[str, float]:
    """Benchmark an engine with multiple test contexts."""
    
    print(f"\n🔥 Benchmarking {engine_name}...")
    
    execution_times = []
    
    for iteration in range(iterations):
        start_time = time.time()
        
        for context in test_contexts:
            try:
                result = engine.apply_context_rules_engine(context, active_dimensions)
                # Force evaluation to ensure fair comparison
                if hasattr(result, 'count'):
                    _ = result.count()
            except Exception as e:
                print(f"   ⚠️  Error in {engine_name}: {e}")
                return {"error": True, "execution_time": float('inf')}
        
        end_time = time.time()
        execution_time = (end_time - start_time) * 1000  # Convert to milliseconds
        execution_times.append(execution_time)
        
        print(f"   Iteration {iteration + 1}: {execution_time:.2f}ms")
    
    return {
        "error": False,
        "execution_time": statistics.mean(execution_times),
        "min_time": min(execution_times),
        "max_time": max(execution_times),
        "std_dev": statistics.stdev(execution_times) if len(execution_times) > 1 else 0
    }


def main():
    """Main benchmark execution and comparison."""
    
    print("🚀 Phase 2 Performance Validation - Hybrid Engine Benchmark")
    print("=" * 60)
    
    # Create test data
    print("📊 Creating test data...")
    rules, dimensions, test_contexts = create_test_data(rule_count=500)
    active_dimensions = ["DIM_1", "DIM_2", "DIM_3"]
    
    print(f"   Rules: {len(test_contexts)} contexts, {rules.count()} rules")
    print(f"   Dimensions: {len(active_dimensions)} active dimensions")
    
    # Initialize engines
    print("🏗️  Initializing engines...")
    
    try:
        # Standard RulesEngine (Phase 1 optimized)
        standard_engine = RulesEngine(rules=rules, dimension_metadata=dimensions)
        print("   ✅ Standard RulesEngine initialized")
        
        # HybridRulesEngine (Phase 2)
        hybrid_engine = create_performance_optimized_engine(
            rules=rules, 
            dimension_metadata=dimensions
        )
        print("   ✅ HybridRulesEngine initialized")
        print(f"   🔧 Processing mode: {hybrid_engine.active_processing_mode.value}")
        print(f"   📈 Numpy processor available: {hybrid_engine.numpy_processor is not None}")
        
    except Exception as e:
        print(f"   ❌ Engine initialization failed: {e}")
        return
    
    # Run benchmarks
    print("\n🏁 Running benchmarks...")
    iterations = 3
    
    # Benchmark standard engine
    standard_results = benchmark_engine(
        "Standard RulesEngine", 
        standard_engine, 
        test_contexts, 
        active_dimensions, 
        iterations
    )
    
    # Benchmark hybrid engine
    hybrid_results = benchmark_engine(
        "HybridRulesEngine", 
        hybrid_engine, 
        test_contexts, 
        active_dimensions, 
        iterations
    )
    
    # Performance comparison
    print("\n📊 Performance Comparison Results")
    print("=" * 60)
    
    if standard_results.get("error") or hybrid_results.get("error"):
        print("❌ Benchmark failed due to errors")
        return
    
    standard_time = standard_results["execution_time"]
    hybrid_time = hybrid_results["execution_time"]
    
    print(f"Standard Engine:    {standard_time:.2f} ms (±{standard_results['std_dev']:.2f})")
    print(f"Hybrid Engine:      {hybrid_time:.2f} ms (±{hybrid_results['std_dev']:.2f})")
    
    if hybrid_time > 0:
        improvement_percent = ((standard_time - hybrid_time) / standard_time) * 100
        speedup_factor = standard_time / hybrid_time
        
        print(f"\n🎯 Performance Improvement: {improvement_percent:.1f}%")
        print(f"🚀 Speedup Factor: {speedup_factor:.2f}x")
        
        # Phase 2 target validation
        target_min = 50  # 50% minimum improvement target
        target_max = 80  # 80% maximum improvement target
        
        print(f"\n🎯 Phase 2 Target Validation:")
        print(f"   Target Range: {target_min}%-{target_max}% improvement")
        
        if improvement_percent >= target_min:
            if improvement_percent <= target_max:
                print(f"   ✅ SUCCESS: {improvement_percent:.1f}% improvement within target range!")
            else:
                print(f"   🎉 EXCEEDED: {improvement_percent:.1f}% improvement exceeds target!")
        else:
            print(f"   ⚠️  BELOW TARGET: {improvement_percent:.1f}% improvement below {target_min}% target")
            
        # Additional insights
        print(f"\n📈 Performance Insights:")
        print(f"   Memory efficiency: Numpy arrays vs repeated dataframe operations")
        print(f"   Vectorization: Prime-based ternary logic with numpy operations")
        print(f"   Context optimization: Batch extraction vs individual field access")
        
        # Hybrid engine statistics
        if hasattr(hybrid_engine, 'get_performance_summary'):
            summary = hybrid_engine.get_performance_summary()
            print(f"\n🔧 Hybrid Engine Statistics:")
            for key, value in summary.items():
                print(f"   {key}: {value}")
    
    else:
        print("❌ Invalid benchmark results")


if __name__ == "__main__":
    main()