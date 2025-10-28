#!/usr/bin/env python
"""
Standalone test for Enhanced VectorizedRulesEngine.

This script tests the enhanced engine without pytest dependencies.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import polars as pl
from pydantic import BaseModel

# Test basic imports
print("Testing imports...")
try:
    from mountainash_utils_rules.providers import ProviderFactory, PolarsProvider
    from mountainash_utils_rules.vectorized_config import VectorizedEngineConfig
    from mountainash_utils_rules.monitoring import PerformanceMonitor, MemoryManager
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Test provider factory
print("\nTesting ProviderFactory...")
try:
    providers = ProviderFactory.available_providers()
    print(f"  Available providers: {providers}")
    
    provider = ProviderFactory.create_provider("polars")
    print(f"  Created provider: {provider.backend_name}")
    print(f"  Supports lazy evaluation: {provider.supports_lazy_evaluation}")
    print("✓ ProviderFactory works")
except Exception as e:
    print(f"✗ ProviderFactory failed: {e}")

# Test configuration
print("\nTesting VectorizedEngineConfig...")
try:
    config = VectorizedEngineConfig()
    print(f"  Default provider: {config.provider}")
    print(f"  Monitoring enabled: {config.enable_monitoring}")
    
    prod_config = VectorizedEngineConfig.production()
    print(f"  Production monitoring: {prod_config.enable_monitoring}")
    print(f"  Production cleanup: {prod_config.enable_cleanup}")
    print("✓ Configuration works")
except Exception as e:
    print(f"✗ Configuration failed: {e}")

# Test monitoring
print("\nTesting PerformanceMonitor...")
try:
    monitor = PerformanceMonitor(enabled=True)
    
    with monitor.time_evaluation("polars"):
        # Simulate some work
        import time
        time.sleep(0.01)
    
    metrics = monitor.get_metrics()
    print(f"  Total evaluations: {metrics['total_evaluations']}")
    print(f"  Average time: {metrics['average_time']*1000:.2f}ms")
    print("✓ PerformanceMonitor works")
except Exception as e:
    print(f"✗ PerformanceMonitor failed: {e}")

# Test memory manager
print("\nTesting MemoryManager...")
try:
    memory_mgr = MemoryManager(cleanup_interval=10)
    
    # Simulate evaluations
    for i in range(11):
        memory_mgr.check_and_cleanup()
    
    stats = memory_mgr.get_memory_stats()
    print(f"  Evaluations: {stats.evaluation_count}")
    print(f"  Cleanups performed: {stats.cleanups_performed}")
    print(f"  Process memory: {stats.process_memory_mb:.1f}MB")
    print("✓ MemoryManager works")
except Exception as e:
    print(f"✗ MemoryManager failed: {e}")

# Test Polars provider with ternary filters
print("\nTesting PolarsProvider with ternary filters...")
try:
    from mountainash_utils_rules.dimension import Dimension
    from mountainash_utils_rules.constants import MatchStrategy
    
    # Create test data
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2"],
        "customer_tier": ["PREMIUM", "STANDARD"],
        "age_MIN": [18, 25],
        "age_MAX": [65, 50],
        "product_code": ["PROD_A.*", "PROD_B.*"]
    })
    
    # Create dimensions
    dimensions = [
        Dimension(
            dimension_name="customer_tier",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        ),
        Dimension(
            dimension_name="age",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="age_MIN",
            range_max_field="age_MAX"
        ),
        Dimension(
            dimension_name="product_code",
            match_strategy=MatchStrategy.REGEX,
            data_type=str
        )
    ]
    
    # Create provider
    provider = PolarsProvider(enable_caching=True)
    
    # Test context
    context_values = {
        "customer_tier": "PREMIUM",
        "age": 35,
        "product_code": "PROD_A_001"
    }
    
    # Execute evaluation
    result = provider.execute_evaluation(
        rules_data=rules_df,
        context_values=context_values,
        dimensions=dimensions
    )
    
    print(f"  Result shape: {result.shape}")
    print(f"  Columns: {result.columns}")
    print(f"  Has 'keep' column: {'keep' in result.columns}")
    
    # Check results
    keep_values = result["keep"].to_list()
    print(f"  Keep values: {keep_values}")
    print("✓ PolarsProvider evaluation works")
    
except Exception as e:
    print(f"✗ PolarsProvider failed: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*50)
print("All standalone tests completed!")
print("="*50)