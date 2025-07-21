"""Tests for mountainash_utils_rules.observer module."""

import pytest
from mountainash_utils_rules.observer import ObservabilityManager
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_data import IbisDataFrame
import polars as pl


class TestObservabilityManager:
    """Test suite for ObservabilityManager class."""

    @pytest.fixture
    def observability_manager(self):
        """Create an ObservabilityManager instance for testing."""
        return ObservabilityManager()

    @pytest.fixture
    def sample_dimension(self):
        """Create a sample dimension for testing."""
        return Dimension(
            dimension_name="test_dim",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )

    @pytest.fixture
    def sample_rules_with_intermediate_cols(self):
        """Create sample rules with intermediate columns for testing."""
        df = pl.DataFrame({
            "rule_name": ["rule_1", "rule_2"],
            "dimension_filter_product": [True, False],
            "dimension_any_false": [False, True],
            "dimension_any_true": [True, False],
            "cumu_dimension_count": [1, 2],
            "cumu_soft_match_count": [1, 1],
            "cumu_hard_match_count": [0, 1],
            "dropped": [False, True],
            "dropped_by_dimension": ["", "test_dim"]
        })
        return IbisDataFrame(df, ibis_backend_schema="sqlite")

    def test_observability_manager_initialization(self, observability_manager):
        """Test ObservabilityManager initialization."""
        assert isinstance(observability_manager, ObservabilityManager)
        assert observability_manager.intermediate_values == {}
        assert observability_manager.warnings == {}

    def test_log_intermediate_values(self, observability_manager):
        """Test logging intermediate values."""
        dimension_name = "test_dimension"
        values = {"key1": "value1", "key2": "value2"}
        
        observability_manager.log_intermediate_values(dimension_name, values)
        
        assert dimension_name in observability_manager.intermediate_values
        assert observability_manager.intermediate_values[dimension_name] == values

    def test_log_intermediate_values_multiple_dimensions(self, observability_manager):
        """Test logging intermediate values for multiple dimensions."""
        dim1_name = "dimension_1"
        dim1_values = {"key1": "value1"}
        dim2_name = "dimension_2"
        dim2_values = {"key2": "value2"}
        
        observability_manager.log_intermediate_values(dim1_name, dim1_values)
        observability_manager.log_intermediate_values(dim2_name, dim2_values)
        
        assert len(observability_manager.intermediate_values) == 2
        assert observability_manager.intermediate_values[dim1_name] == dim1_values
        assert observability_manager.intermediate_values[dim2_name] == dim2_values

    def test_log_intermediate_values_overwrite(self, observability_manager):
        """Test that logging intermediate values overwrites previous values."""
        dimension_name = "test_dimension"
        original_values = {"key1": "original"}
        new_values = {"key1": "updated"}
        
        observability_manager.log_intermediate_values(dimension_name, original_values)
        observability_manager.log_intermediate_values(dimension_name, new_values)
        
        assert observability_manager.intermediate_values[dimension_name] == new_values

    def test_log_warning_new_dimension(self, observability_manager):
        """Test logging warning for a new dimension."""
        dimension_name = "test_dimension"
        warning_type = "validation_error"
        message = "Test warning message"
        
        observability_manager.log_warning(dimension_name, warning_type, message)
        
        assert dimension_name in observability_manager.warnings
        assert warning_type in observability_manager.warnings[dimension_name]
        assert observability_manager.warnings[dimension_name][warning_type] == message

    def test_log_warning_existing_dimension(self, observability_manager):
        """Test logging warning for an existing dimension."""
        dimension_name = "test_dimension"
        warning_type1 = "validation_error"
        warning_type2 = "type_mismatch"
        message1 = "First warning"
        message2 = "Second warning"
        
        observability_manager.log_warning(dimension_name, warning_type1, message1)
        observability_manager.log_warning(dimension_name, warning_type2, message2)
        
        assert dimension_name in observability_manager.warnings
        assert len(observability_manager.warnings[dimension_name]) == 2
        assert observability_manager.warnings[dimension_name][warning_type1] == message1
        assert observability_manager.warnings[dimension_name][warning_type2] == message2

    def test_log_warning_overwrite_warning_type(self, observability_manager):
        """Test that logging same warning type overwrites previous message."""
        dimension_name = "test_dimension"
        warning_type = "validation_error"
        original_message = "Original warning"
        new_message = "Updated warning"
        
        observability_manager.log_warning(dimension_name, warning_type, original_message)
        observability_manager.log_warning(dimension_name, warning_type, new_message)
        
        assert observability_manager.warnings[dimension_name][warning_type] == new_message

    def test_log_context_cast_warning_new_dimension(self, observability_manager):
        """Test logging context cast warning for a new dimension."""
        dimension_name = "test_dimension"
        context_value = "123"
        context_type = str
        target_type = "int"
        
        observability_manager._log_context_cast_warning(
            dimension_name, context_value, context_type, target_type
        )
        
        expected_message = f"Context value {context_value} of type {context_type} has been cast to {target_type} for dimension {dimension_name}"
        
        assert dimension_name in observability_manager.warnings
        assert "context_cast" in observability_manager.warnings[dimension_name]
        assert observability_manager.warnings[dimension_name]["context_cast"] == expected_message

    def test_log_context_cast_warning_existing_dimension(self, observability_manager):
        """Test logging context cast warning for an existing dimension with warnings."""
        dimension_name = "test_dimension"
        
        # First add a regular warning
        observability_manager.log_warning(dimension_name, "validation_error", "Test warning")
        
        # Then add context cast warning
        context_value = 123
        context_type = int
        target_type = "str"
        
        observability_manager._log_context_cast_warning(
            dimension_name, context_value, context_type, target_type
        )
        
        expected_message = f"Context value {context_value} of type {context_type} has been cast to {target_type} for dimension {dimension_name}"
        
        assert len(observability_manager.warnings[dimension_name]) == 2
        assert observability_manager.warnings[dimension_name]["context_cast"] == expected_message
        assert observability_manager.warnings[dimension_name]["validation_error"] == "Test warning"

    def test_save_dimension_intermediate_values(
        self, 
        observability_manager, 
        sample_dimension, 
        sample_rules_with_intermediate_cols
    ):
        """Test saving dimension intermediate values from rules."""
        observability_manager.save_dimension_intermediate_values(
            sample_rules_with_intermediate_cols, 
            sample_dimension
        )
        
        assert sample_dimension.dimension_name in observability_manager.intermediate_values
        
        # Verify that the intermediate values contain the expected columns
        intermediate_data = observability_manager.intermediate_values[sample_dimension.dimension_name]
        
        # Check that it's a BaseDataFrame-like object with the expected columns
        assert hasattr(intermediate_data, 'select')
        
        # The intermediate values should be the selected dataframe
        expected_columns = [
            'dimension_filter_product',
            'dimension_any_false', 
            'dimension_any_true',
            'cumu_dimension_count',
            'cumu_soft_match_count',
            'cumu_hard_match_count',
            'dropped',
            'dropped_by_dimension'
        ]
        
        # Verify the selection worked by checking the intermediate data is not None
        assert intermediate_data is not None

    def test_save_dimension_intermediate_values_multiple_dimensions(
        self, 
        observability_manager, 
        sample_rules_with_intermediate_cols
    ):
        """Test saving intermediate values for multiple dimensions."""
        dim1 = Dimension(dimension_name="dim1", match_strategy=MatchStrategy.EXACT, data_type=str)
        dim2 = Dimension(dimension_name="dim2", match_strategy=MatchStrategy.RANGE, data_type=int)
        
        observability_manager.save_dimension_intermediate_values(sample_rules_with_intermediate_cols, dim1)
        observability_manager.save_dimension_intermediate_values(sample_rules_with_intermediate_cols, dim2)
        
        assert len(observability_manager.intermediate_values) == 2
        assert dim1.dimension_name in observability_manager.intermediate_values
        assert dim2.dimension_name in observability_manager.intermediate_values

    def test_context_cast_warning_various_types(self, observability_manager):
        """Test context cast warning with various data types."""
        test_cases = [
            ("test_dim_1", "123", str, "int"),
            ("test_dim_2", 123, int, "str"),
            ("test_dim_3", 12.5, float, "int"),
            ("test_dim_4", True, bool, "str"),
            ("test_dim_5", [1, 2, 3], list, "str")
        ]
        
        for dimension_name, context_value, context_type, target_type in test_cases:
            observability_manager._log_context_cast_warning(
                dimension_name, context_value, context_type, target_type
            )
            
            expected_message = f"Context value {context_value} of type {context_type} has been cast to {target_type} for dimension {dimension_name}"
            assert observability_manager.warnings[dimension_name]["context_cast"] == expected_message

    def test_multiple_warning_types_per_dimension(self, observability_manager):
        """Test logging multiple warning types for the same dimension."""
        dimension_name = "test_dimension"
        
        # Add different types of warnings
        observability_manager.log_warning(dimension_name, "validation_error", "Validation failed")
        observability_manager.log_warning(dimension_name, "type_mismatch", "Type doesn't match")
        observability_manager._log_context_cast_warning(dimension_name, "123", str, "int")
        
        warnings = observability_manager.warnings[dimension_name]
        assert len(warnings) == 3
        assert "validation_error" in warnings
        assert "type_mismatch" in warnings
        assert "context_cast" in warnings

    def test_empty_intermediate_values_and_warnings_initially(self, observability_manager):
        """Test that manager starts with empty collections."""
        assert len(observability_manager.intermediate_values) == 0
        assert len(observability_manager.warnings) == 0
        assert observability_manager.intermediate_values == {}
        assert observability_manager.warnings == {}