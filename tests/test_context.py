"""Tests for mountainash_utils_rules.context module."""

import pytest
from mountainash_utils_rules.context import ContextHelper
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.constants import MatchStrategy, RuleConstants
from pydantic import BaseModel
from typing import Optional


class ValidStringContext(BaseModel):
    """Valid context with string field."""
    DIM_1: str


class ValidIntContext(BaseModel):
    """Valid context with integer field."""
    DIM_2: int


class ValidFloatContext(BaseModel):
    """Valid context with float field."""
    DIM_3: float


class ValidBoolContext(BaseModel):
    """Valid context with boolean field."""
    DIM_4: bool


class ValidNoneContext(BaseModel):
    """Valid context with optional field."""
    DIM_5: Optional[str] = None


class InvalidTypeContext(BaseModel):
    """Invalid context with unsupported field type."""
    DIM_INVALID: dict


class ComplexTypeContext(BaseModel):
    """Context with complex unsupported types."""
    DIM_LIST: list
    DIM_SET: set
    DIM_DICT: dict


class TestContextHelper:
    """Test suite for ContextHelper class."""

    @pytest.fixture
    def string_dimension(self):
        """Create a string dimension for testing."""
        return Dimension(
            dimension_name="DIM_1",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )

    @pytest.fixture
    def int_dimension(self):
        """Create an integer dimension for testing."""
        return Dimension(
            dimension_name="DIM_2",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="DIM_2_MIN",
            range_max_field="DIM_2_MAX"
        )

    @pytest.fixture
    def float_dimension(self):
        """Create a float dimension for testing."""
        return Dimension(
            dimension_name="DIM_3",
            match_strategy=MatchStrategy.RANGE,
            data_type=float,
            range_min_field="DIM_3_MIN",
            range_max_field="DIM_3_MAX"
        )

    @pytest.fixture
    def bool_dimension(self):
        """Create a boolean dimension for testing."""
        return Dimension(
            dimension_name="DIM_4",
            match_strategy=MatchStrategy.EXACT,
            data_type=bool
        )

    def test_allowed_context_types_contains_expected_types(self):
        """Test that ALLOWED_CONTEXT_TYPES contains expected types."""
        expected_types = [str, int, float, bool, type(None)]
        assert ContextHelper.ALLOWED_CONTEXT_TYPES == expected_types

    def test_get_context_value_valid_string(self, string_dimension):
        """Test getting valid string context value."""
        context = ValidStringContext(DIM_1="test_value")
        result = ContextHelper.get_context_value(context, string_dimension)
        assert result == "test_value"

    def test_get_context_value_valid_int(self, int_dimension):
        """Test getting valid integer context value."""
        context = ValidIntContext(DIM_2=42)
        result = ContextHelper.get_context_value(context, int_dimension)
        assert result == 42

    def test_get_context_value_valid_float(self, float_dimension):
        """Test getting valid float context value."""
        context = ValidFloatContext(DIM_3=3.14)
        result = ContextHelper.get_context_value(context, float_dimension)
        assert result == 3.14

    def test_get_context_value_valid_bool_true(self, bool_dimension):
        """Test getting valid boolean context value (True)."""
        context = ValidBoolContext(DIM_4=True)
        result = ContextHelper.get_context_value(context, bool_dimension)
        assert result == 1  # Boolean True should be converted to int 1

    def test_get_context_value_valid_bool_false(self, bool_dimension):
        """Test getting valid boolean context value (False)."""
        context = ValidBoolContext(DIM_4=False)
        result = ContextHelper.get_context_value(context, bool_dimension)
        assert result == 0  # Boolean False should be converted to int 0

    def test_get_context_value_none_type_string_dimension(self, string_dimension):
        """Test getting None context value for string dimension."""
        context = ValidNoneContext(DIM_5=None)
        # Need to create a dimension that matches the field name
        dimension = Dimension(
            dimension_name="DIM_5",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET

    def test_get_context_value_none_type_numeric_dimension(self, int_dimension):
        """Test getting None context value for numeric dimension."""
        class NoneIntContext(BaseModel):
            DIM_2: Optional[int] = None
        
        context = NoneIntContext(DIM_2=None)
        result = ContextHelper.get_context_value(context, int_dimension)
        assert result == RuleConstants.NOT_SET_NUMERIC

    def test_get_context_value_invalid_type_dict(self, string_dimension):
        """Test getting invalid context value (dict type)."""
        # Create dimension that matches the field name
        dimension = Dimension(
            dimension_name="DIM_INVALID",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )
        context = InvalidTypeContext(DIM_INVALID={"key": "value"})
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET

    def test_get_context_value_invalid_type_list(self, string_dimension):
        """Test getting invalid context value (list type)."""
        dimension = Dimension(
            dimension_name="DIM_LIST",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )
        context = ComplexTypeContext(
            DIM_LIST=[1, 2, 3],
            DIM_SET={1, 2, 3},
            DIM_DICT={"key": "value"}
        )
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET

    def test_get_context_value_invalid_type_set(self, string_dimension):
        """Test getting invalid context value (set type)."""
        dimension = Dimension(
            dimension_name="DIM_SET",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )
        context = ComplexTypeContext(
            DIM_LIST=[1, 2, 3],
            DIM_SET={1, 2, 3},
            DIM_DICT={"key": "value"}
        )
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET

    def test_get_context_value_fallback_to_dimension_type_string(self):
        """Test fallback to dimension type for unmapped cases (string)."""
        dimension = Dimension(
            dimension_name="DIM_TEST",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )
        class TestContext(BaseModel):
            DIM_TEST: Optional[str] = None
        
        context = TestContext(DIM_TEST=None)
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET

    def test_get_context_value_fallback_to_dimension_type_int(self):
        """Test fallback to dimension type for unmapped cases (int)."""
        dimension = Dimension(
            dimension_name="DIM_TEST",
            match_strategy=MatchStrategy.EXACT,
            data_type=int
        )
        class TestContext(BaseModel):
            DIM_TEST: Optional[int] = None
        
        context = TestContext(DIM_TEST=None)
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET_NUMERIC

    def test_get_context_value_fallback_to_dimension_type_float(self):
        """Test fallback to dimension type for unmapped cases (float)."""
        dimension = Dimension(
            dimension_name="DIM_TEST",
            match_strategy=MatchStrategy.EXACT,
            data_type=float
        )
        class TestContext(BaseModel):
            DIM_TEST: Optional[float] = None
        
        context = TestContext(DIM_TEST=None)
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET_NUMERIC

    def test_get_context_value_fallback_to_dimension_type_bool(self):
        """Test fallback to dimension type for unmapped cases (bool)."""
        dimension = Dimension(
            dimension_name="DIM_TEST",
            match_strategy=MatchStrategy.EXACT,
            data_type=bool
        )
        class TestContext(BaseModel):
            DIM_TEST: Optional[bool] = None
        
        context = TestContext(DIM_TEST=None)
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET_NUMERIC

    def test_get_context_value_fallback_to_not_set_for_unknown_dimension_type(self):
        """Test fallback to NOT_SET for unknown dimension types."""
        # This tests the final else clause in get_context_value
        dimension = Dimension(
            dimension_name="DIM_TEST",
            match_strategy=MatchStrategy.EXACT,
            data_type=tuple  # Unusual type not in the logic
        )
        class TestContext(BaseModel):
            DIM_TEST: Optional[tuple] = None
        
        context = TestContext(DIM_TEST=None)
        result = ContextHelper.get_context_value(context, dimension)
        assert result == RuleConstants.NOT_SET

    def test_check_context_and_dimension_types_match_string_match(self, string_dimension):
        """Test type matching for string types."""
        context = ValidStringContext(DIM_1="test")
        result = ContextHelper.check_context_and_dimension_types_match(context, string_dimension)
        assert result is True

    def test_check_context_and_dimension_types_match_int_match(self, int_dimension):
        """Test type matching for integer types."""
        context = ValidIntContext(DIM_2=42)
        result = ContextHelper.check_context_and_dimension_types_match(context, int_dimension)
        assert result is True

    def test_check_context_and_dimension_types_match_float_match(self, float_dimension):
        """Test type matching for float types."""
        context = ValidFloatContext(DIM_3=3.14)
        result = ContextHelper.check_context_and_dimension_types_match(context, float_dimension)
        assert result is True

    def test_check_context_and_dimension_types_match_bool_match(self, bool_dimension):
        """Test type matching for boolean types."""
        context = ValidBoolContext(DIM_4=True)
        result = ContextHelper.check_context_and_dimension_types_match(context, bool_dimension)
        assert result is True

    def test_check_context_and_dimension_types_mismatch_string_vs_int(self, string_dimension):
        """Test type mismatch between string and int."""
        class MismatchedContext(BaseModel):
            DIM_1: int  # Should be str
        
        context = MismatchedContext(DIM_1=123)
        result = ContextHelper.check_context_and_dimension_types_match(context, string_dimension)
        assert result is False

    def test_check_context_and_dimension_types_mismatch_int_vs_string(self, int_dimension):
        """Test type mismatch between int and string."""
        class MismatchedContext(BaseModel):
            DIM_2: str  # Should be int
        
        context = MismatchedContext(DIM_2="123")
        result = ContextHelper.check_context_and_dimension_types_match(context, int_dimension)
        assert result is False

    def test_check_context_and_dimension_types_mismatch_float_vs_int(self, int_dimension):
        """Test type mismatch between float and int."""
        class MismatchedContext(BaseModel):
            DIM_2: float  # Should be int
        
        context = MismatchedContext(DIM_2=123.45)
        result = ContextHelper.check_context_and_dimension_types_match(context, int_dimension)
        assert result is False

    def test_check_context_and_dimension_types_none_vs_string(self, string_dimension):
        """Test type mismatch between None and string."""
        context = ValidNoneContext(DIM_5=None)
        dimension = Dimension(
            dimension_name="DIM_5",
            match_strategy=MatchStrategy.EXACT,
            data_type=str
        )
        result = ContextHelper.check_context_and_dimension_types_match(context, dimension)
        assert result is False

    @pytest.mark.parametrize("context_value,dimension_type,expected_match", [
        ("test", str, True),
        (42, int, True),
        (3.14, float, True),
        (True, bool, True),
        (False, bool, True),
        ("test", int, False),
        (42, str, False),
        (3.14, int, False),
        (True, str, False),
        (None, str, False),
        (None, int, False),
    ])
    def test_check_context_and_dimension_types_parametrized(self, context_value, dimension_type, expected_match):
        """Parametrized test for type matching scenarios."""
        class GenericContext(BaseModel):
            TEST_DIM: type(context_value) if context_value is not None else type(None)
        
        dimension = Dimension(
            dimension_name="TEST_DIM",
            match_strategy=MatchStrategy.EXACT,
            data_type=dimension_type
        )
        context = GenericContext(TEST_DIM=context_value)
        result = ContextHelper.check_context_and_dimension_types_match(context, dimension)
        assert result is expected_match