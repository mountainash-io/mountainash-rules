"""
Comprehensive test suite for Phase 3 VectorizedRulesEngine.

This test suite validates the revolutionary polars-based vectorized architecture,
ensuring correctness while achieving maximum performance through advanced optimizations.
"""

import pytest
import polars as pl
import numpy as np
from typing import Dict, List, Any
from unittest.mock import Mock, patch
import time

from pydantic import BaseModel

from mountainash_utils_rules.vectorized_engine import (
    VectorizedRulesEngine,
    VectorizedEngineConfig,
    PolarsRuleProcessor,
    PolarsExpressionBuilder,
    QueryPlanOptimizer,
    RuleSelectivityProfile,
    QueryExecutionPlan,
    create_ultra_performance_engine,
    create_memory_optimized_engine
)
from mountainash_utils_rules.constants import RuleTrinaryFlags, MatchStrategy
from mountainash_utils_rules.dimension import Dimension


class TestVectorizedContext(BaseModel):
    DIM_1: str
    DIM_2: int
    DIM_3: str


class TestPolarsExpressionBuilder:
    """Test suite for polars expression building with prime-based ternary logic."""
    
    @pytest.fixture
    def expression_builder(self):
        return PolarsExpressionBuilder()
    
    def test_exact_match_expression_creation(self, expression_builder):
        """Test exact match expression building and caching."""
        expr1 = expression_builder.build_exact_match_expression("DIM_1", "A")
        expr2 = expression_builder.build_exact_match_expression("DIM_1", "A")
        
        # Should use cached expression
        assert expr1 is expr2
        
        # Different value should create new expression
        expr3 = expression_builder.build_exact_match_expression("DIM_1", "B")
        assert expr3 is not expr1
    
    def test_range_match_expression_creation(self, expression_builder):
        """Test range match expression building with optimization."""
        expr = expression_builder.build_range_match_expression(
            "DIM_2", 15.0, "DIM_2_MIN", "DIM_2_MAX"
        )
        
        # Test with polars DataFrame
        test_data = pl.DataFrame({
            "DIM_2_MIN": [10, 20, 5, None],
            "DIM_2_MAX": [20, 30, 15, 25]
        })
        
        result = test_data.with_columns(expr)
        expected_flags = [
            RuleTrinaryFlags.PRIME_TRUE,     # 15 in [10,20]
            RuleTrinaryFlags.PRIME_FALSE,    # 15 not in [20,30]
            RuleTrinaryFlags.PRIME_TRUE,     # 15 in [5,15]
            RuleTrinaryFlags.PRIME_UNKNOWN   # null min value
        ]
        
        assert result.get_column("DIM_2_match").to_list() == expected_flags
    
    def test_regex_match_expression_creation(self, expression_builder):
        """Test regex match expression building with pattern caching."""
        expr = expression_builder.build_regex_match_expression("DIM_3", "test123")
        
        test_data = pl.DataFrame({
            "DIM_3": [r"test.*", r".*123", r"nomatch", None]
        })
        
        result = test_data.with_columns(expr)
        expected_flags = [
            RuleTrinaryFlags.PRIME_TRUE,     # "test.*" matches "test123"
            RuleTrinaryFlags.PRIME_TRUE,     # ".*123" matches "test123"
            RuleTrinaryFlags.PRIME_FALSE,    # "nomatch" doesn't match
            RuleTrinaryFlags.PRIME_UNKNOWN   # null pattern
        ]
        
        assert result.get_column("DIM_3_match").to_list() == expected_flags
    
    def test_combined_expression_prime_logic(self, expression_builder):
        """Test prime-based ternary logic for combining expressions."""
        # Create individual expressions
        expr1 = pl.lit(RuleTrinaryFlags.PRIME_TRUE).alias("match1")
        expr2 = pl.lit(RuleTrinaryFlags.PRIME_FALSE).alias("match2")
        expr3 = pl.lit(RuleTrinaryFlags.PRIME_UNKNOWN).alias("match3")
        
        # Test various combinations
        combined_true_true = expression_builder.build_combined_expression([expr1, expr1])
        combined_true_false = expression_builder.build_combined_expression([expr1, expr2])
        combined_true_unknown = expression_builder.build_combined_expression([expr1, expr3])
        
        test_df = pl.DataFrame({"dummy": [1]})
        
        result_true_true = test_df.with_columns(combined_true_true).get_column("final_match")[0]
        result_true_false = test_df.with_columns(combined_true_false).get_column("final_match")[0]  
        result_true_unknown = test_df.with_columns(combined_true_unknown).get_column("final_match")[0]
        
        assert result_true_true == RuleTrinaryFlags.PRIME_TRUE
        assert result_true_false == RuleTrinaryFlags.PRIME_FALSE
        assert result_true_unknown == RuleTrinaryFlags.PRIME_UNKNOWN


class TestQueryPlanOptimizer:
    """Test suite for query plan optimization and selectivity analysis."""
    
    @pytest.fixture
    def sample_rules_df(self):
        return pl.DataFrame({
            'rule_name': [f'rule_{i}' for i in range(100)],
            'DIM_1': ['A', 'B', 'C'] * 33 + ['A'],
            'DIM_2_MIN': list(range(0, 100)),
            'DIM_2_MAX': list(range(10, 110)),
            'DIM_3': [f'pattern_{i % 5}.*' for i in range(100)]
        })
    
    @pytest.fixture
    def sample_dimensions(self):
        return [
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
        ]
    
    @pytest.fixture
    def optimizer(self):
        config = VectorizedEngineConfig(enable_selectivity_analysis=True)
        return QueryPlanOptimizer(config)
    
    def test_selectivity_analysis(self, optimizer, sample_rules_df, sample_dimensions):
        """Test rule selectivity analysis for optimization."""
        optimizer.analyze_rule_selectivity(sample_rules_df, sample_dimensions)
        
        # Verify profiles were created
        assert len(optimizer.selectivity_profiles) == 3
        
        # Check exact match analysis
        dim1_profile = optimizer.selectivity_profiles["DIM_1"]
        assert isinstance(dim1_profile, RuleSelectivityProfile)
        assert dim1_profile.estimated_selectivity > 0
        
        # Check range match analysis
        dim2_profile = optimizer.selectivity_profiles["DIM_2"]  
        assert isinstance(dim2_profile, RuleSelectivityProfile)
        
        # Check regex match analysis
        dim3_profile = optimizer.selectivity_profiles["DIM_3"]
        assert isinstance(dim3_profile, RuleSelectivityProfile)
    
    def test_execution_plan_optimization(self, optimizer, sample_dimensions):
        """Test execution plan optimization with selectivity ordering."""
        # Create mock selectivity profiles
        optimizer.selectivity_profiles = {
            "DIM_1": RuleSelectivityProfile("DIM_1", 0.8, 100, set(), 0.8),  # Low selectivity
            "DIM_2": RuleSelectivityProfile("DIM_2", 0.2, 200, set(), 0.2),  # High selectivity  
            "DIM_3": RuleSelectivityProfile("DIM_3", 0.5, 300, set(), 0.5)   # Medium selectivity
        }
        
        execution_plan = optimizer.optimize_execution_plan(sample_dimensions)
        
        assert isinstance(execution_plan, QueryExecutionPlan)
        
        # Most selective dimension (DIM_2) should be first
        assert execution_plan.execution_order[0] == "DIM_2"
        
        # Should have estimated performance gain
        assert execution_plan.estimated_performance_gain > 1.0
    
    def test_range_overlap_calculation(self, optimizer):
        """Test range overlap calculation for selectivity analysis."""
        # Non-overlapping ranges
        non_overlapping = np.array([[0, 10], [20, 30], [40, 50]])
        overlap_score1 = optimizer._calculate_range_overlap(non_overlapping)
        
        # Heavily overlapping ranges  
        overlapping = np.array([[0, 50], [10, 60], [20, 70]])
        overlap_score2 = optimizer._calculate_range_overlap(overlapping)
        
        # Overlapping should have higher score
        assert overlap_score2 > overlap_score1
    
    def test_regex_complexity_calculation(self, optimizer):
        """Test regex complexity calculation for selectivity analysis."""
        simple_patterns = ["abc", "def", "xyz"]
        complex_patterns = [".*test.*", "^[a-z]+$", "\\d{3,5}"]
        
        simple_score = optimizer._calculate_regex_complexity(simple_patterns)
        complex_score = optimizer._calculate_regex_complexity(complex_patterns)
        
        assert complex_score > simple_score


class TestPolarsRuleProcessor:
    """Test suite for core polars rule processor."""
    
    @pytest.fixture
    def mock_rules_dataframe(self):
        mock_df = Mock()
        
        # Create polars DataFrame directly
        polars_data = pl.DataFrame({
            'rule_name': ['rule_1', 'rule_2', 'rule_3', 'rule_4'],
            'DIM_1': ['A', 'B', 'C', 'A'],
            'DIM_2_MIN': [0, 10, 20, 5],
            'DIM_2_MAX': [9, 19, 29, 15],
            'DIM_3': [r'X.*', r'Y.*', r'Z.*', r'.*\d+']
        })
        
        mock_df.to_polars.return_value = polars_data
        return mock_df
    
    @pytest.fixture
    def sample_dimensions(self):
        return [
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
        ]
    
    def test_polars_processor_initialization(self, mock_rules_dataframe, sample_dimensions):
        """Test polars processor initialization and rule materialization."""
        config = VectorizedEngineConfig()
        processor = PolarsRuleProcessor(mock_rules_dataframe, sample_dimensions, config)
        
        assert len(processor.rules_df) == 4
        assert len(processor.dimensions) == 3
        assert isinstance(processor.execution_plan, QueryExecutionPlan)
    
    def test_vectorized_context_evaluation(self, mock_rules_dataframe, sample_dimensions):
        """Test vectorized context evaluation with polars expressions."""
        config = VectorizedEngineConfig()
        processor = PolarsRuleProcessor(mock_rules_dataframe, sample_dimensions, config)
        
        context_values = {
            'DIM_1': 'A',
            'DIM_2': 7,
            'DIM_3': 'X123'
        }
        
        result_df = processor.evaluate_context_vectorized(context_values)
        
        # Verify result structure
        assert 'keep' in result_df.columns
        assert len(result_df) == 4
        
        # Check that evaluation produced boolean keep flags
        keep_values = result_df.get_column('keep').to_list()
        assert all(isinstance(val, bool) for val in keep_values)
    
    def test_missing_context_handling(self, mock_rules_dataframe, sample_dimensions):
        """Test handling of missing context values."""
        config = VectorizedEngineConfig()
        processor = PolarsRuleProcessor(mock_rules_dataframe, sample_dimensions, config)
        
        # Missing DIM_2 context value
        context_values = {
            'DIM_1': 'A',
            'DIM_3': 'X123'
            # DIM_2 missing
        }
        
        result_df = processor.evaluate_context_vectorized(context_values)
        
        # Should handle missing context gracefully
        assert 'keep' in result_df.columns
        assert len(result_df) == 4


class TestVectorizedRulesEngine:
    """Test suite for complete vectorized rules engine."""
    
    @pytest.fixture
    def sample_dimensions(self):
        return [
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX")
        ]
    
    @pytest.fixture
    def mock_rules_with_polars(self):
        """Mock rules that can convert to polars."""
        mock_df = Mock()
        
        polars_data = pl.DataFrame({
            'rule_name': ['rule_1', 'rule_2'],
            'DIM_1': ['A', 'B'],
            'DIM_2_MIN': [0, 10],
            'DIM_2_MAX': [9, 19]
        })
        
        mock_df.to_polars.return_value = polars_data
        return mock_df
    
    def test_vectorized_engine_initialization(self, mock_rules_with_polars, sample_dimensions):
        """Test vectorized engine initialization with configuration."""
        config = VectorizedEngineConfig(enable_query_optimization=True)
        
        engine = VectorizedRulesEngine(mock_rules_with_polars, sample_dimensions, config)
        
        assert engine.config.enable_query_optimization == True
        assert len(engine.dimensions) == 2
        assert isinstance(engine.processor, PolarsRuleProcessor)
    
    def test_context_evaluation_performance_monitoring(self, mock_rules_with_polars, sample_dimensions):
        """Test performance monitoring during context evaluation."""
        engine = VectorizedRulesEngine(mock_rules_with_polars, sample_dimensions)
        
        context = TestVectorizedContext(DIM_1="A", DIM_2=5, DIM_3="test")
        
        # Execute evaluation
        result = engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2"])
        
        # Check performance stats were updated
        stats = engine.get_performance_stats()
        assert stats['total_evaluations'] == 1
        assert stats['total_execution_time'] > 0
        assert stats['average_execution_time'] > 0
    
    def test_performance_stats_collection(self, mock_rules_with_polars, sample_dimensions):
        """Test comprehensive performance statistics collection."""
        config = VectorizedEngineConfig(
            enable_query_optimization=True,
            enable_parallel_processing=True,
            enable_memory_pooling=True
        )
        
        engine = VectorizedRulesEngine(mock_rules_with_polars, sample_dimensions, config)
        stats = engine.get_performance_stats()
        
        required_stats = [
            'total_evaluations', 'total_execution_time', 'average_execution_time',
            'query_optimization_enabled', 'parallel_processing_enabled',
            'memory_pooling_enabled', 'estimated_performance_gain',
            'dimension_count', 'rule_count'
        ]
        
        for stat in required_stats:
            assert stat in stats


class TestVectorizedEngineConfigurations:
    """Test different vectorized engine configurations."""
    
    @pytest.fixture
    def mock_rules(self):
        mock_df = Mock()
        polars_data = pl.DataFrame({
            'rule_name': ['rule_1'],
            'DIM_1': ['A']
        })
        mock_df.to_polars.return_value = polars_data
        return mock_df
    
    @pytest.fixture
    def simple_dimensions(self):
        return [Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)]
    
    def test_ultra_performance_configuration(self, mock_rules, simple_dimensions):
        """Test ultra-performance engine configuration."""
        engine = create_ultra_performance_engine(mock_rules, simple_dimensions)
        
        config = engine.config
        assert config.enable_query_optimization == True
        assert config.enable_parallel_processing == True
        assert config.max_worker_threads == 8
        assert config.enable_selectivity_analysis == True
        assert config.enable_simd_optimization == True
    
    def test_memory_optimized_configuration(self, mock_rules, simple_dimensions):
        """Test memory-optimized engine configuration."""
        engine = create_memory_optimized_engine(mock_rules, simple_dimensions)
        
        config = engine.config
        assert config.enable_query_optimization == True
        assert config.enable_parallel_processing == False  # Memory conservation
        assert config.chunk_size_mb == 50  # Smaller chunks
        assert config.max_cached_patterns == 500  # Reduced cache
    
    def test_custom_configuration(self, mock_rules, simple_dimensions):
        """Test custom vectorized engine configuration."""
        custom_config = VectorizedEngineConfig(
            enable_query_optimization=False,
            enable_parallel_processing=True,
            max_worker_threads=2,
            enable_early_termination=False
        )
        
        engine = VectorizedRulesEngine(mock_rules, simple_dimensions, custom_config)
        
        assert engine.config.enable_query_optimization == False
        assert engine.config.max_worker_threads == 2
        assert engine.config.enable_early_termination == False


class TestVectorizedEngineEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_invalid_rules_conversion(self):
        """Test handling of rules that cannot be converted to polars."""
        mock_rules = Mock()
        mock_rules.to_polars.side_effect = Exception("Conversion failed")
        mock_rules.to_pandas.side_effect = Exception("Pandas conversion failed")
        mock_rules.ibis_table = None
        
        dimensions = [Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)]
        
        with pytest.raises(ValueError, match="Failed to materialize rules"):
            VectorizedRulesEngine(mock_rules, dimensions)
    
    def test_empty_dimensions_list(self):
        """Test handling of empty dimensions list."""
        mock_rules = Mock()
        polars_data = pl.DataFrame({'rule_name': ['rule_1']})
        mock_rules.to_polars.return_value = polars_data
        
        engine = VectorizedRulesEngine(mock_rules, [])  # Empty dimensions
        
        assert len(engine.dimensions) == 0
        stats = engine.get_performance_stats()
        assert stats['dimension_count'] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])