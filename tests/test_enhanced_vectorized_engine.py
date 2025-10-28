"""
Tests for the Enhanced VectorizedRulesEngine.

This module tests the enhanced engine with provider pattern, monitoring,
and memory management features.
"""

import pytest
import polars as pl
from pydantic import BaseModel
from mountainash_dataframes import IbisDataFrame

from mountainash_utils_rules import (
    EnhancedVectorizedRulesEngine,
    DimensionsMetadata,
    Dimension,
    MatchStrategy,
    create_polars_engine,
    create_production_engine,
    ProviderFactory,
    PerformanceMonitor,
    MemoryManager
)
from mountainash_utils_rules.vectorized_config import VectorizedEngineConfig


class TestContext(BaseModel):
    """Test context model."""
    customer_tier: str
    age: int
    product_code: str


@pytest.fixture
def sample_rules():
    """Create sample rules for testing."""
    rules_data = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3", "rule_4"],
        "customer_tier": ["PREMIUM", "STANDARD", "PREMIUM", "STANDARD"],
        "age_MIN": [18, 25, 30, 18],
        "age_MAX": [65, 50, 60, 100],
        "product_code": ["PROD_A.*", "PROD_B.*", "PROD_C.*", "PROD_.*"],
        "discount": [0.20, 0.10, 0.15, 0.05]
    })
    
    return IbisDataFrame(rules_data, ibis_backend_schema='polars')


@pytest.fixture
def dimension_metadata():
    """Create dimension metadata for testing."""
    return DimensionsMetadata(
        dimensions=[
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
    )


class TestEnhancedVectorizedEngine:
    """Test suite for Enhanced VectorizedRulesEngine."""
    
    def test_engine_initialization(self, sample_rules, dimension_metadata):
        """Test basic engine initialization."""
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        
        assert engine is not None
        assert engine.provider is not None
        assert engine.provider.backend_name == "polars"
        assert engine.config.provider == "polars"
    
    def test_engine_with_custom_config(self, sample_rules, dimension_metadata):
        """Test engine with custom configuration."""
        config = VectorizedEngineConfig(
            provider="polars",
            enable_monitoring=True,
            enable_cleanup=True,
            cleanup_interval=100
        )
        
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata,
            config=config
        )
        
        assert engine.monitor is not None
        assert engine.memory_manager is not None
        assert engine.memory_manager.cleanup_interval == 100
    
    def test_apply_context_exact_match(self, sample_rules, dimension_metadata):
        """Test applying context with exact match."""
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        
        context = TestContext(
            customer_tier="PREMIUM",
            age=35,
            product_code="PROD_A_001"
        )
        
        result = engine.apply_context_rules_engine(
            context=context,
            dimension_names=["customer_tier", "age", "product_code"],
            keep_all=False
        )
        
        # Convert result to check
        result_df = result.to_pandas()
        
        # Should match rule_1 and rule_3
        assert len(result_df) > 0
        assert "keep" in result_df.columns
        assert all(result_df["keep"] == True)
    
    def test_apply_context_range_match(self, sample_rules, dimension_metadata):
        """Test applying context with range match."""
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        
        context = TestContext(
            customer_tier="STANDARD",
            age=30,
            product_code="PROD_B_002"
        )
        
        result = engine.apply_context_rules_engine(
            context=context,
            dimension_names=["customer_tier", "age", "product_code"],
            keep_all=True
        )
        
        result_df = result.to_pandas()
        
        assert len(result_df) == 4  # All rules returned with keep_all=True
        assert "keep" in result_df.columns
        
        # Check which rules matched
        matched = result_df[result_df["keep"] == True]
        assert len(matched) >= 1  # At least rule_2 should match
    
    def test_apply_context_regex_match(self, sample_rules, dimension_metadata):
        """Test applying context with regex match."""
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        
        context = TestContext(
            customer_tier="PREMIUM",
            age=40,
            product_code="PROD_C_XYZ"
        )
        
        result = engine.apply_context_rules_engine(
            context=context,
            dimension_names=["customer_tier", "age", "product_code"],
            keep_all=False
        )
        
        result_df = result.to_pandas()
        
        # Should match rules with PREMIUM tier, age in range, and matching product pattern
        assert len(result_df) > 0
    
    def test_performance_monitoring(self, sample_rules, dimension_metadata):
        """Test performance monitoring functionality."""
        config = VectorizedEngineConfig(
            provider="polars",
            enable_monitoring=True,
            detailed_timing=True
        )
        
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata,
            config=config
        )
        
        context = TestContext(
            customer_tier="PREMIUM",
            age=35,
            product_code="PROD_A_001"
        )
        
        # Run evaluation
        result = engine.apply_context_rules_engine(
            context=context,
            dimension_names=["customer_tier", "age", "product_code"]
        )
        
        # Check metrics
        metrics = engine.get_performance_metrics()
        
        assert metrics['monitoring_enabled'] == True
        assert metrics['total_evaluations'] == 1
        assert metrics['successful_evaluations'] == 1
        assert metrics['average_time'] > 0
    
    def test_memory_management(self, sample_rules, dimension_metadata):
        """Test memory management functionality."""
        config = VectorizedEngineConfig(
            provider="polars",
            enable_cleanup=True,
            cleanup_interval=2  # Low interval for testing
        )
        
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata,
            config=config
        )
        
        context = TestContext(
            customer_tier="PREMIUM",
            age=35,
            product_code="PROD_A_001"
        )
        
        # Run multiple evaluations to trigger cleanup
        for i in range(3):
            result = engine.apply_context_rules_engine(
                context=context,
                dimension_names=["customer_tier", "age", "product_code"]
            )
        
        # Check memory stats
        memory_stats = engine.get_memory_stats()
        
        assert memory_stats is not None
        assert memory_stats.evaluation_count == 3
        assert memory_stats.cleanups_performed >= 1  # At least one cleanup should have occurred
    
    def test_factory_functions(self, sample_rules, dimension_metadata):
        """Test convenience factory functions."""
        # Test polars engine factory
        engine1 = create_polars_engine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        assert engine1.config.provider == "polars"
        
        # Test production engine factory
        engine2 = create_production_engine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        assert engine2.config.enable_monitoring == True
        assert engine2.config.enable_cleanup == True
    
    def test_provider_factory(self):
        """Test provider factory functionality."""
        # Test available providers
        providers = ProviderFactory.available_providers()
        assert "polars" in providers
        
        # Test provider creation
        provider = ProviderFactory.create_provider("polars")
        assert provider is not None
        assert provider.backend_name == "polars"
        
        # Test provider info
        info = ProviderFactory.get_provider_info("polars")
        assert info['backend_name'] == "polars"
        assert info['supports_lazy_evaluation'] == True
    
    def test_configuration_presets(self):
        """Test configuration preset methods."""
        # Test high performance preset
        config1 = VectorizedEngineConfig.high_performance()
        assert config1.enable_monitoring == False
        assert config1.enable_cleanup == False
        assert config1.max_worker_threads == 8
        
        # Test production preset
        config2 = VectorizedEngineConfig.production()
        assert config2.enable_monitoring == True
        assert config2.enable_cleanup == True
        
        # Test memory constrained preset
        config3 = VectorizedEngineConfig.memory_constrained()
        assert config3.chunk_size_mb == 50
        assert config3.max_cache_size == 500
        
        # Test debugging preset
        config4 = VectorizedEngineConfig.debugging()
        assert config4.detailed_timing == True
        assert config4.enable_result_validation == True
    
    def test_api_compatibility(self, sample_rules, dimension_metadata):
        """Test API compatibility with original RulesEngine."""
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        
        # Test that we can access the same managers as original
        assert engine.get_rule_manager() is not None
        assert engine.get_metadata_manager() is not None
        assert engine.get_observability_data() is not None
        
        # Test dimension_names as string (single dimension)
        context = TestContext(
            customer_tier="PREMIUM",
            age=35,
            product_code="PROD_A_001"
        )
        
        result = engine.apply_context_rules_engine(
            context=context,
            dimension_names="customer_tier"  # Single string instead of list
        )
        
        assert result is not None
    
    def test_error_handling(self, sample_rules, dimension_metadata):
        """Test error handling."""
        engine = EnhancedVectorizedRulesEngine(
            rules=sample_rules,
            dimension_metadata=dimension_metadata
        )
        
        context = TestContext(
            customer_tier="PREMIUM",
            age=35,
            product_code="PROD_A_001"
        )
        
        # Test with empty dimension names
        with pytest.raises(ValueError, match="No dimension names specified"):
            engine.apply_context_rules_engine(
                context=context,
                dimension_names=[]
            )