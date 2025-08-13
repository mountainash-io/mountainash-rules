"""
Unit tests for NumpyRuleProcessor - comprehensive test suite for vectorized rule evaluation.

This test suite validates the numpy-based rule processor performance and correctness,
ensuring that vectorized operations maintain functional compatibility with the ibis-based
engine while delivering significant performance improvements.
"""

import pytest
import numpy as np
import re
from typing import Dict, List, Any
from unittest.mock import Mock, patch

from mountainash_utils_rules.numpy_processor import (
    NumpyRuleProcessor, 
    NumpyMatchEngine, 
    NumpyRuleData
)
from mountainash_utils_rules.constants import RuleTrinaryFlags, MatchStrategy
from mountainash_utils_rules.dimension import Dimension


class TestNumpyMatchEngine:
    """Test suite for the NumpyMatchEngine vectorized matching operations."""
    
    @pytest.fixture
    def match_engine(self):
        return NumpyMatchEngine()
    
    def test_exact_match_vectorized_string_matches(self, match_engine):
        """Test exact string matching with vectorized operations."""
        rule_values = np.array(['A', 'B', 'C', 'A', 'D'])
        context_value = 'A'
        
        result = match_engine.exact_match_vectorized(context_value, rule_values)
        
        expected = np.array([
            RuleTrinaryFlags.PRIME_TRUE,   # 'A' matches
            RuleTrinaryFlags.PRIME_FALSE,  # 'B' doesn't match
            RuleTrinaryFlags.PRIME_FALSE,  # 'C' doesn't match
            RuleTrinaryFlags.PRIME_TRUE,   # 'A' matches
            RuleTrinaryFlags.PRIME_FALSE   # 'D' doesn't match
        ])
        
        np.testing.assert_array_equal(result, expected)
        assert result.dtype == np.int32
    
    def test_exact_match_vectorized_numeric_matches(self, match_engine):
        """Test exact numeric matching with vectorized operations."""
        rule_values = np.array([1, 2, 3, 1, 5])
        context_value = 1
        
        result = match_engine.exact_match_vectorized(context_value, rule_values)
        
        expected = np.array([
            RuleTrinaryFlags.PRIME_TRUE,   # 1 matches
            RuleTrinaryFlags.PRIME_FALSE,  # 2 doesn't match
            RuleTrinaryFlags.PRIME_FALSE,  # 3 doesn't match
            RuleTrinaryFlags.PRIME_TRUE,   # 1 matches
            RuleTrinaryFlags.PRIME_FALSE   # 5 doesn't match
        ])
        
        np.testing.assert_array_equal(result, expected)
    
    def test_exact_match_vectorized_null_handling(self, match_engine):
        """Test exact matching with null/invalid values."""
        rule_values = np.array(['A', None, '', 'A', 'None'])
        context_value = 'A'
        
        result = match_engine.exact_match_vectorized(context_value, rule_values)
        
        expected = np.array([
            RuleTrinaryFlags.PRIME_TRUE,     # 'A' matches
            RuleTrinaryFlags.PRIME_UNKNOWN,  # None is unknown
            RuleTrinaryFlags.PRIME_UNKNOWN,  # Empty string is unknown
            RuleTrinaryFlags.PRIME_TRUE,     # 'A' matches
            RuleTrinaryFlags.PRIME_UNKNOWN   # 'None' string is unknown
        ])
        
        np.testing.assert_array_equal(result, expected)
    
    def test_range_match_vectorized_numeric_ranges(self, match_engine):
        """Test vectorized range matching with numeric values."""
        min_values = np.array([0, 10, 20, 5, 15])
        max_values = np.array([9, 19, 29, 15, 25])
        context_value = 12
        
        result = match_engine.range_match_vectorized(context_value, min_values, max_values)
        
        expected = np.array([
            RuleTrinaryFlags.PRIME_FALSE,  # 12 not in [0,9]
            RuleTrinaryFlags.PRIME_TRUE,   # 12 in [10,19] 
            RuleTrinaryFlags.PRIME_FALSE,  # 12 not in [20,29]
            RuleTrinaryFlags.PRIME_TRUE,   # 12 in [5,15]
            RuleTrinaryFlags.PRIME_TRUE    # 12 in [15,25]
        ])
        
        np.testing.assert_array_equal(result, expected)
    
    def test_range_match_vectorized_boundary_values(self, match_engine):
        """Test range matching with boundary values."""
        min_values = np.array([10, 10, 10])
        max_values = np.array([20, 20, 20])
        
        # Test lower boundary
        result_lower = match_engine.range_match_vectorized(10, min_values, max_values)
        np.testing.assert_array_equal(
            result_lower, 
            np.full(3, RuleTrinaryFlags.PRIME_TRUE)
        )
        
        # Test upper boundary
        result_upper = match_engine.range_match_vectorized(20, min_values, max_values)
        np.testing.assert_array_equal(
            result_upper, 
            np.full(3, RuleTrinaryFlags.PRIME_TRUE)
        )
        
        # Test below range
        result_below = match_engine.range_match_vectorized(9, min_values, max_values)
        np.testing.assert_array_equal(
            result_below, 
            np.full(3, RuleTrinaryFlags.PRIME_FALSE)
        )
        
        # Test above range
        result_above = match_engine.range_match_vectorized(21, min_values, max_values)
        np.testing.assert_array_equal(
            result_above, 
            np.full(3, RuleTrinaryFlags.PRIME_FALSE)
        )
    
    def test_range_match_vectorized_null_handling(self, match_engine):
        """Test range matching with null/invalid values."""
        min_values = np.array([0, np.nan, 10, 20])
        max_values = np.array([10, 20, np.nan, 30])
        context_value = 15
        
        result = match_engine.range_match_vectorized(context_value, min_values, max_values)
        
        expected = np.array([
            RuleTrinaryFlags.PRIME_FALSE,   # 15 not in [0,10]
            RuleTrinaryFlags.PRIME_UNKNOWN, # NaN min value
            RuleTrinaryFlags.PRIME_UNKNOWN, # NaN max value
            RuleTrinaryFlags.PRIME_TRUE     # 15 in [20,30]
        ])
        
        np.testing.assert_array_equal(result, expected)
    
    def test_regex_match_vectorized_pattern_matches(self, match_engine):
        """Test vectorized regex matching with compiled patterns."""
        patterns = [
            re.compile(r'^A.*'),
            re.compile(r'.*B$'),
            re.compile(r'C+'),
            None,  # Null pattern
            re.compile(r'\d+')
        ]
        
        context_value = "ABC123"
        
        result = match_engine.regex_match_vectorized(context_value, patterns)
        
        expected = np.array([
            RuleTrinaryFlags.PRIME_TRUE,     # Starts with A
            RuleTrinaryFlags.PRIME_FALSE,    # Doesn't end with B
            RuleTrinaryFlags.PRIME_TRUE,     # Contains C
            RuleTrinaryFlags.PRIME_UNKNOWN,  # Null pattern
            RuleTrinaryFlags.PRIME_TRUE      # Contains digits
        ])
        
        np.testing.assert_array_equal(result, expected)
    
    def test_regex_match_vectorized_invalid_context(self, match_engine):
        """Test regex matching with invalid context value."""
        patterns = [re.compile(r'.*'), re.compile(r'\d+')]
        context_value = 123  # Invalid (numeric instead of string)
        
        result = match_engine.regex_match_vectorized(context_value, patterns)
        
        expected = np.full(2, RuleTrinaryFlags.PRIME_UNKNOWN)
        np.testing.assert_array_equal(result, expected)


class TestNumpyRuleProcessor:
    """Test suite for the complete NumpyRuleProcessor functionality."""
    
    @pytest.fixture
    def sample_dimensions(self):
        return [
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
        ]
    
    @pytest.fixture
    def mock_rules_dataframe(self):
        """Mock BaseDataFrame with sample rule data."""
        mock_df = Mock()
        
        # Mock pandas DataFrame with sample data
        import pandas as pd
        pandas_data = pd.DataFrame({
            'rule_name': ['rule_1', 'rule_2', 'rule_3', 'rule_4'],
            'DIM_1': ['A', 'B', 'C', 'A'],
            'DIM_2_MIN': [0, 10, 20, 5],
            'DIM_2_MAX': [9, 19, 29, 15],
            'DIM_3': [r'X.*', r'Y.*', r'Z.*', r'.*\d+']
        })
        
        mock_df.to_pandas.return_value = pandas_data
        return mock_df
    
    def test_numpy_processor_initialization(self, mock_rules_dataframe, sample_dimensions):
        """Test NumpyRuleProcessor initialization and data extraction."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        assert processor.rule_data.rule_count == 4
        assert len(processor.rule_data.rule_names) == 4
        assert 'DIM_1' in processor.rule_data.exact_dimensions
        assert 'DIM_2' in processor.rule_data.range_dimensions
        assert 'DIM_3' in processor.rule_data.regex_dimensions
    
    def test_evaluate_context_vectorized_all_matches(self, mock_rules_dataframe, sample_dimensions):
        """Test vectorized context evaluation with matching rules."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        context_values = {
            'DIM_1': 'A',
            'DIM_2': 7,
            'DIM_3': 'X123'
        }
        
        active_dimensions = ['DIM_1', 'DIM_2', 'DIM_3']
        
        result = processor.evaluate_context_vectorized(context_values, active_dimensions)
        
        # Only rule_1 should match: A, [0,9], X.* pattern
        expected_matches = (result == RuleTrinaryFlags.PRIME_TRUE)
        assert expected_matches.sum() == 1  # Only one rule matches all criteria
    
    def test_evaluate_context_vectorized_partial_matches(self, mock_rules_dataframe, sample_dimensions):
        """Test vectorized evaluation with partial matches."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        context_values = {
            'DIM_1': 'B',
            'DIM_2': 15,  # Matches rule_2 and rule_4 ranges
            'DIM_3': 'Y456'  # Matches rule_2 regex
        }
        
        active_dimensions = ['DIM_1', 'DIM_2', 'DIM_3']
        
        result = processor.evaluate_context_vectorized(context_values, active_dimensions)
        
        # Only rule_2 should match: B, [10,19], Y.* pattern  
        expected_matches = (result == RuleTrinaryFlags.PRIME_TRUE)
        assert expected_matches.sum() == 1
    
    def test_evaluate_context_vectorized_no_matches(self, mock_rules_dataframe, sample_dimensions):
        """Test vectorized evaluation with no matching rules."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        context_values = {
            'DIM_1': 'X',  # Doesn't match any exact values
            'DIM_2': 50,   # Outside all ranges
            'DIM_3': 'NoMatch'
        }
        
        active_dimensions = ['DIM_1', 'DIM_2', 'DIM_3']
        
        result = processor.evaluate_context_vectorized(context_values, active_dimensions)
        
        # No rules should match
        expected_matches = (result == RuleTrinaryFlags.PRIME_TRUE)
        assert expected_matches.sum() == 0
    
    def test_evaluate_context_vectorized_missing_context(self, mock_rules_dataframe, sample_dimensions):
        """Test vectorized evaluation with missing context values."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        context_values = {
            'DIM_1': 'A',
            # DIM_2 missing
            'DIM_3': 'X123'
        }
        
        active_dimensions = ['DIM_1', 'DIM_2', 'DIM_3']
        
        result = processor.evaluate_context_vectorized(context_values, active_dimensions)
        
        # All rules should be UNKNOWN due to missing DIM_2
        assert np.all(result == RuleTrinaryFlags.PRIME_UNKNOWN)
    
    def test_get_matching_rules(self, mock_rules_dataframe, sample_dimensions):
        """Test getting matching rule names and flags."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        context_values = {
            'DIM_1': 'A',
            'DIM_2': 7,
            'DIM_3': 'X123'
        }
        
        active_dimensions = ['DIM_1', 'DIM_2', 'DIM_3']
        
        rule_names, flags = processor.get_matching_rules(context_values, active_dimensions)
        
        assert len(rule_names) == len(flags)
        assert len(rule_names) >= 0  # May have matches
        assert np.all(flags == RuleTrinaryFlags.PRIME_TRUE)
    
    def test_combine_dimension_flags_prime_logic(self, mock_rules_dataframe, sample_dimensions):
        """Test prime-based ternary logic for combining dimension flags."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        # Test various combinations
        current_flags = np.array([
            RuleTrinaryFlags.PRIME_TRUE,
            RuleTrinaryFlags.PRIME_TRUE,
            RuleTrinaryFlags.PRIME_FALSE,
            RuleTrinaryFlags.PRIME_UNKNOWN
        ])
        
        dimension_flags = np.array([
            RuleTrinaryFlags.PRIME_TRUE,
            RuleTrinaryFlags.PRIME_FALSE,
            RuleTrinaryFlags.PRIME_TRUE,
            RuleTrinaryFlags.PRIME_TRUE
        ])
        
        result = processor._combine_dimension_flags(current_flags, dimension_flags)
        
        expected = np.array([
            RuleTrinaryFlags.PRIME_TRUE,     # TRUE & TRUE = TRUE
            RuleTrinaryFlags.PRIME_FALSE,    # TRUE & FALSE = FALSE
            RuleTrinaryFlags.PRIME_FALSE,    # FALSE & TRUE = FALSE  
            RuleTrinaryFlags.PRIME_UNKNOWN   # UNKNOWN & TRUE = UNKNOWN
        ])
        
        np.testing.assert_array_equal(result, expected)
    
    def test_get_performance_stats(self, mock_rules_dataframe, sample_dimensions):
        """Test performance statistics collection."""
        processor = NumpyRuleProcessor(mock_rules_dataframe, sample_dimensions)
        
        stats = processor.get_performance_stats()
        
        assert 'rule_count' in stats
        assert 'exact_dimensions' in stats
        assert 'range_dimensions' in stats
        assert 'regex_dimensions' in stats
        assert 'total_regex_patterns' in stats
        assert 'memory_usage_mb' in stats
        
        assert stats['rule_count'] == 4
        assert stats['exact_dimensions'] == 1
        assert stats['range_dimensions'] == 1
        assert stats['regex_dimensions'] == 1
        assert isinstance(stats['memory_usage_mb'], (int, float))


class TestNumpyProcessorEdgeCases:
    """Test edge cases and error conditions for numpy processor."""
    
    def test_invalid_rules_conversion(self, sample_dimensions):
        """Test handling of rules that cannot be converted to pandas."""
        mock_rules = Mock()
        mock_rules.to_pandas.side_effect = Exception("Conversion failed")
        mock_rules.ibis_table = None
        
        with pytest.raises(ValueError, match="Failed to extract rule data"):
            NumpyRuleProcessor(mock_rules, sample_dimensions)
    
    def test_empty_rules_dataframe(self, sample_dimensions):
        """Test handling of empty rules dataframe."""
        import pandas as pd
        
        mock_rules = Mock()
        empty_df = pd.DataFrame()  # Empty dataframe
        mock_rules.to_pandas.return_value = empty_df
        
        processor = NumpyRuleProcessor(mock_rules, sample_dimensions)
        
        assert processor.rule_data.rule_count == 0
        assert len(processor.rule_data.rule_names) == 0
    
    def test_malformed_regex_patterns(self, sample_dimensions):
        """Test handling of invalid regex patterns."""
        import pandas as pd
        
        mock_rules = Mock()
        pandas_data = pd.DataFrame({
            'rule_name': ['rule_1', 'rule_2'],
            'DIM_1': ['A', 'B'],
            'DIM_2_MIN': [0, 10],
            'DIM_2_MAX': [9, 19],
            'DIM_3': [r'[invalid', r'valid.*']  # One invalid regex
        })
        
        mock_rules.to_pandas.return_value = pandas_data
        
        # Should not raise exception - invalid patterns become None
        processor = NumpyRuleProcessor(mock_rules, sample_dimensions)
        
        patterns = processor.rule_data.regex_dimensions['DIM_3']
        assert patterns[0] is None  # Invalid pattern becomes None
        assert patterns[1] is not None  # Valid pattern preserved
    
    def test_missing_dimension_columns(self, sample_dimensions):
        """Test handling of missing dimension columns in rules."""
        import pandas as pd
        
        mock_rules = Mock()
        pandas_data = pd.DataFrame({
            'rule_name': ['rule_1', 'rule_2'],
            # Missing DIM_1 column
            'DIM_2_MIN': [0, 10],
            'DIM_2_MAX': [9, 19],
            'DIM_3': [r'X.*', r'Y.*']
        })
        
        mock_rules.to_pandas.return_value = pandas_data
        
        processor = NumpyRuleProcessor(mock_rules, sample_dimensions)
        
        # Missing columns should be filled with None values
        assert len(processor.rule_data.exact_dimensions['DIM_1']) == 2
        assert processor.rule_data.exact_dimensions['DIM_1'][0] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])