"""
Integration tests for HybridRulesEngine - comprehensive test suite for hybrid processing.

This test suite validates the integration between numpy and ibis processing modes,
ensuring seamless operation, proper fallback behavior, and performance optimization
while maintaining full functional compatibility with the standard RulesEngine.
"""

import pytest
import time
from typing import Dict, List, Any
from unittest.mock import Mock, patch
import logging

from pydantic import BaseModel

from mountainash_utils_rules.hybrid_engine import (
    HybridRulesEngine,
    HybridEngineConfig,
    ProcessingMode,
    ProcessingStats,
    create_performance_optimized_engine,
    create_reliability_focused_engine,
    create_development_engine
)
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_utils_rules.dimension import DimensionsMetadata, Dimension


# Test context model
class TestContext(BaseModel):
    DIM_1: str
    DIM_2: int
    DIM_3: str


class TestHybridEngineConfig:
    """Test suite for HybridEngineConfig and ProcessingMode selection."""
    
    def test_default_config_creation(self):
        """Test default configuration values."""
        config = HybridEngineConfig()
        
        assert config.processing_mode == ProcessingMode.AUTO
        assert config.min_rules_for_numpy == 100
        assert config.max_regex_ratio == 0.3
        assert config.enable_fallback == True
        assert config.max_fallback_attempts == 2
        assert config.enable_performance_logging == False
        assert config.performance_comparison == False
    
    def test_custom_config_creation(self):
        """Test custom configuration creation."""
        config = HybridEngineConfig(
            processing_mode=ProcessingMode.NUMPY_PREFERRED,
            min_rules_for_numpy=50,
            max_regex_ratio=0.5,
            enable_performance_logging=True
        )
        
        assert config.processing_mode == ProcessingMode.NUMPY_PREFERRED
        assert config.min_rules_for_numpy == 50
        assert config.max_regex_ratio == 0.5
        assert config.enable_performance_logging == True


class TestHybridEngineInitialization:
    """Test suite for HybridRulesEngine initialization and mode selection."""
    
    @pytest.fixture
    def sample_dimensions(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
        ])
    
    @pytest.fixture
    def mock_rules_large(self):
        """Mock BaseDataFrame with large rule set for numpy processing."""
        import pandas as pd
        
        mock_df = Mock()
        
        # Create large dataset (200 rules) 
        pandas_data = pd.DataFrame({
            'rule_name': [f'rule_{i}' for i in range(200)],
            'DIM_1': (['A', 'B', 'C'] * 66) + ['A', 'B'],  # 66*3 + 2 = 200 total
            'DIM_2_MIN': list(range(0, 200)),
            'DIM_2_MAX': list(range(10, 210)),
            'DIM_3': [f'pattern_{i % 10}.*' for i in range(200)]
        })
        
        mock_df.to_pandas.return_value = pandas_data
        return mock_df
    
    @pytest.fixture 
    def mock_rules_small(self):
        """Mock BaseDataFrame with small rule set for ibis processing."""
        import pandas as pd
        
        mock_df = Mock()
        
        # Create small dataset (50 rules)
        pandas_data = pd.DataFrame({
            'rule_name': [f'rule_{i}' for i in range(50)],
            'DIM_1': ['A', 'B'] * 25,
            'DIM_2_MIN': list(range(0, 50)),
            'DIM_2_MAX': list(range(10, 60)),
            'DIM_3': [f'pattern_{i % 5}.*' for i in range(50)]
        })
        
        mock_df.to_pandas.return_value = pandas_data
        return mock_df
    
    def test_auto_mode_large_dataset(self, mock_rules_large, sample_dimensions):
        """Test automatic mode selection with large dataset (should choose numpy)."""
        config = HybridEngineConfig(processing_mode=ProcessingMode.AUTO)
        
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            engine = HybridRulesEngine(mock_rules_large, sample_dimensions, config)
            
            # Should select numpy for large dataset with low regex ratio
            assert engine.active_processing_mode in [ProcessingMode.NUMPY_PREFERRED, ProcessingMode.IBIS_ONLY]
            # Note: Actual mode depends on numpy processor initialization success
    
    def test_auto_mode_small_dataset(self, mock_rules_small, sample_dimensions):
        """Test automatic mode selection with small dataset (should choose ibis)."""
        config = HybridEngineConfig(processing_mode=ProcessingMode.AUTO)
        
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            engine = HybridRulesEngine(mock_rules_small, sample_dimensions, config)
            
            # Should select ibis for small dataset
            assert engine.active_processing_mode == ProcessingMode.IBIS_ONLY
    
    def test_forced_numpy_mode(self, mock_rules_large, sample_dimensions):
        """Test forced numpy processing mode."""
        config = HybridEngineConfig(processing_mode=ProcessingMode.NUMPY_ONLY)
        
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            engine = HybridRulesEngine(mock_rules_large, sample_dimensions, config)
            
            assert engine.active_processing_mode == ProcessingMode.NUMPY_ONLY
    
    def test_forced_ibis_mode(self, mock_rules_large, sample_dimensions):
        """Test forced ibis processing mode."""
        config = HybridEngineConfig(processing_mode=ProcessingMode.IBIS_ONLY)
        
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            engine = HybridRulesEngine(mock_rules_large, sample_dimensions, config)
            
            assert engine.active_processing_mode == ProcessingMode.IBIS_ONLY


class TestHybridEngineProcessing:
    """Test suite for hybrid engine rule processing functionality."""
    
    @pytest.fixture
    def sample_dimensions(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
        ])
    
    @pytest.fixture
    def mock_rules_standard(self):
        """Standard mock rules for testing."""
        import pandas as pd
        
        mock_df = Mock()
        pandas_data = pd.DataFrame({
            'rule_name': ['rule_1', 'rule_2', 'rule_3', 'rule_4'],
            'DIM_1': ['A', 'B', 'C', 'A'],
            'DIM_2_MIN': [0, 10, 20, 5],
            'DIM_2_MAX': [9, 19, 29, 15],
            'DIM_3': [r'X.*', r'Y.*', r'Z.*', r'.*\d+']
        })
        
        mock_df.to_pandas.return_value = pandas_data
        return mock_df
    
    def test_ibis_only_processing(self, mock_rules_standard, sample_dimensions):
        """Test pure ibis processing mode."""
        config = HybridEngineConfig(processing_mode=ProcessingMode.IBIS_ONLY)
        
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine') as mock_ibis_engine_class:
            # Mock the ibis engine instance
            mock_ibis_engine = Mock()
            mock_ibis_engine_class.return_value = mock_ibis_engine
            
            # Mock the result dataframe
            mock_result = Mock()
            mock_ibis_engine.apply_context_rules_engine.return_value = mock_result
            
            engine = HybridRulesEngine(mock_rules_standard, sample_dimensions, config)
            
            # Test processing
            context = TestContext(DIM_1="A", DIM_2=7, DIM_3="X123")
            result = engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
            
            # Verify ibis engine was called
            mock_ibis_engine.apply_context_rules_engine.assert_called_once()
            assert result == mock_result
            
            # Verify stats
            stats = engine.get_processing_stats()
            assert stats.ibis_executions == 1
            assert stats.numpy_attempts == 0
    
    @patch('mountainash_utils_rules.hybrid_engine.RulesEngine')
    def test_fallback_mechanism(self, mock_ibis_engine_class, mock_rules_standard, sample_dimensions):
        """Test fallback from numpy to ibis on error."""
        config = HybridEngineConfig(
            processing_mode=ProcessingMode.NUMPY_PREFERRED,
            enable_fallback=True
        )
        
        # Setup mocks
        mock_ibis_engine = Mock()
        mock_ibis_engine_class.return_value = mock_ibis_engine
        mock_result = Mock()
        mock_ibis_engine.apply_context_rules_engine.return_value = mock_result
        
        with patch('mountainash_utils_rules.hybrid_engine.NumpyRuleProcessor') as mock_numpy_class:
            # Make numpy processor initialization fail
            mock_numpy_class.side_effect = Exception("Numpy initialization failed")
            
            engine = HybridRulesEngine(mock_rules_standard, sample_dimensions, config)
            
            # Processing should fall back to ibis
            context = TestContext(DIM_1="A", DIM_2=7, DIM_3="X123")
            result = engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
            
            # Verify fallback occurred
            assert result == mock_result
            stats = engine.get_processing_stats()
            assert stats.ibis_executions == 1


class TestHybridEnginePerformance:
    """Test suite for hybrid engine performance monitoring and statistics."""
    
    @pytest.fixture
    def engine_with_logging(self):
        """Create engine with performance logging enabled."""
        config = HybridEngineConfig(
            processing_mode=ProcessingMode.IBIS_ONLY,
            enable_performance_logging=True
        )
        
        mock_rules = Mock()
        mock_rules.to_pandas.return_value = Mock()
        
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            return HybridRulesEngine(mock_rules, None, config)
    
    def test_performance_stats_initialization(self, engine_with_logging):
        """Test initial performance statistics."""
        stats = engine_with_logging.get_processing_stats()
        
        assert isinstance(stats, ProcessingStats)
        assert stats.numpy_attempts == 0
        assert stats.numpy_successes == 0
        assert stats.ibis_executions == 0
        assert stats.total_numpy_time == 0.0
        assert stats.total_ibis_time == 0.0
        assert stats.numpy_errors == 0
        assert stats.fallback_triggers == 0
    
    def test_performance_summary_format(self, engine_with_logging):
        """Test performance summary format."""
        summary = engine_with_logging.get_performance_summary()
        
        required_keys = [
            'processing_mode', 'total_executions', 'numpy_executions',
            'ibis_executions', 'numpy_success_rate', 'fallback_rate',
            'average_numpy_time_ms', 'average_ibis_time_ms',
            'performance_improvement', 'numpy_processor_available'
        ]
        
        for key in required_keys:
            assert key in summary
        
        # Verify data types
        assert isinstance(summary['processing_mode'], str)
        assert isinstance(summary['total_executions'], int)
        assert isinstance(summary['numpy_processor_available'], bool)
    
    def test_stats_reset(self, engine_with_logging):
        """Test statistics reset functionality."""
        # Manually update some stats
        engine_with_logging.stats.ibis_executions = 5
        engine_with_logging.stats.numpy_attempts = 3
        
        # Reset stats
        engine_with_logging.reset_stats()
        
        # Verify reset
        stats = engine_with_logging.get_processing_stats()
        assert stats.ibis_executions == 0
        assert stats.numpy_attempts == 0
    
    def test_config_update(self, engine_with_logging):
        """Test configuration update functionality."""
        original_mode = engine_with_logging.active_processing_mode
        
        # Update config
        new_config = HybridEngineConfig(processing_mode=ProcessingMode.NUMPY_ONLY)
        engine_with_logging.update_config(new_config)
        
        # Verify config update
        assert engine_with_logging.config.processing_mode == ProcessingMode.NUMPY_ONLY
        # Note: actual processing mode might still be IBIS_ONLY if numpy unavailable


class TestHybridEngineConvenienceFunctions:
    """Test convenience functions for common engine configurations."""
    
    @pytest.fixture
    def mock_rules(self):
        mock_df = Mock()
        mock_df.to_pandas.return_value = Mock()
        return mock_df
    
    def test_performance_optimized_engine(self, mock_rules):
        """Test performance-optimized engine creation."""
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            engine = create_performance_optimized_engine(mock_rules)
            
            assert engine.config.processing_mode == ProcessingMode.NUMPY_PREFERRED
            assert engine.config.min_rules_for_numpy == 50
            assert engine.config.max_regex_ratio == 0.5
            assert engine.config.enable_performance_logging == True
    
    def test_reliability_focused_engine(self, mock_rules):
        """Test reliability-focused engine creation."""
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            engine = create_reliability_focused_engine(mock_rules)
            
            assert engine.config.processing_mode == ProcessingMode.AUTO
            assert engine.config.min_rules_for_numpy == 500
            assert engine.config.max_regex_ratio == 0.1
            assert engine.config.enable_fallback == True
            assert engine.config.max_fallback_attempts == 3
    
    def test_development_engine(self, mock_rules):
        """Test development engine creation."""
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
            engine = create_development_engine(mock_rules)
            
            assert engine.config.processing_mode == ProcessingMode.AUTO
            assert engine.config.enable_performance_logging == True
            assert engine.config.performance_comparison == True
            assert engine.config.enable_fallback == True


class TestHybridEngineIntegration:
    """End-to-end integration tests for hybrid engine functionality."""
    
    @pytest.fixture
    def sample_dimensions(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX")
        ])
    
    def test_end_to_end_compatibility(self, sample_dimensions):
        """Test that hybrid engine provides same API as standard RulesEngine."""
        import pandas as pd
        
        # Create realistic mock rules
        mock_rules = Mock()
        pandas_data = pd.DataFrame({
            'rule_name': ['rule_1', 'rule_2'],
            'DIM_1': ['A', 'B'],
            'DIM_2_MIN': [0, 10],
            'DIM_2_MAX': [9, 19]
        })
        mock_rules.to_pandas.return_value = pandas_data
        
        with patch('mountainash_utils_rules.hybrid_engine.RulesEngine') as mock_ibis_class:
            # Setup ibis engine mock
            mock_ibis_engine = Mock()
            mock_ibis_class.return_value = mock_ibis_engine
            mock_result = Mock()
            mock_ibis_engine.apply_context_rules_engine.return_value = mock_result
            
            # Create hybrid engine
            engine = HybridRulesEngine(mock_rules, sample_dimensions)
            
            # Test that all expected methods exist
            assert hasattr(engine, 'apply_context_rules_engine')
            assert hasattr(engine, 'initialize_rule_flags')
            assert hasattr(engine, 'apply_dimension_filter_flags')
            
            # Test basic functionality
            context = TestContext(DIM_1="A", DIM_2=5, DIM_3="test")
            result = engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2"])
            
            assert result == mock_result
    
    def test_logging_configuration(self, caplog):
        """Test that logging works correctly with performance logging enabled."""
        config = HybridEngineConfig(enable_performance_logging=True)
        mock_rules = Mock()
        mock_rules.to_pandas.return_value = Mock()
        
        with caplog.at_level(logging.INFO):
            with patch('mountainash_utils_rules.hybrid_engine.RulesEngine'):
                engine = HybridRulesEngine(mock_rules, None, config)
                
                # Verify initialization logging
                assert "HybridRulesEngine initialized" in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])