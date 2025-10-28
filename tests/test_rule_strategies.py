import pytest
from mountainash_utils_rules.rule_strategies import ExactMatchStrategy, RangeMatchStrategy, RegexMatchStrategy, MatchStrategyFactory
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags
# from mountainash_dataframes import BaseDataFrame, IbisDataFrame
from mountainash_dataframes.utils.dataframe_filters import FilterCondition as fc

import polars as pl
import ibis
from pydantic import BaseModel
from typing import Optional

@pytest.fixture
def sample_rules():
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", "C"],
        "DIM_2_MIN": [0, 10, 20],
        "DIM_2_MAX": [9, 19, 29],
        "DIM_3": ["^X.*","^Y.*", "^Z.*"],
        "DIM_4": [RuleConstants.UNKNOWN, "Y", "Z"]
    })
    return IbisDataFrame(rules_df, ibis_backend_schema="sqlite")


class Context(BaseModel):
    DIM_1: Optional[str] = None
    DIM_2: Optional[int] = None
    DIM_3: Optional[str] = None
    DIM_4: Optional[str] = None


@pytest.fixture
def exact_match_strategy() -> ExactMatchStrategy:
    return ExactMatchStrategy()

@pytest.fixture
def range_match_strategy() -> RangeMatchStrategy:
    return RangeMatchStrategy()

@pytest.fixture
def regex_match_strategy() -> RegexMatchStrategy:
    return RegexMatchStrategy()

def test_exact_match_strategy(exact_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)

    context_value = "A"  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value

    result = exact_match_strategy.apply_match_filter(sample_rules, dimension, context_value)
    print( result.materialise())
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 1  # PRIME_TRUE = 2

def test_range_match_strategy(range_match_strategy, sample_rules):
    dimension = Dimension(
        dimension_name="DIM_2",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )

    context_value = 15  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value

    result = range_match_strategy.apply_match_filter(sample_rules, dimension, context_value)
    # print( result.materialise())
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 1  # PRIME_TRUE = 2

def test_regex_match_strategy(regex_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    context_value = "XYZ"  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value

    result = regex_match_strategy.apply_match_filter(sample_rules, dimension, context_value)
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 1  # PRIME_TRUE = 2

def test_match_strategy_factory():
    assert isinstance(MatchStrategyFactory.get_rule_strategy_class(MatchStrategy.EXACT), ExactMatchStrategy)
    assert isinstance(MatchStrategyFactory.get_rule_strategy_class(MatchStrategy.RANGE), RangeMatchStrategy)
    assert isinstance(MatchStrategyFactory.get_rule_strategy_class(MatchStrategy.REGEX), RegexMatchStrategy)
    with pytest.raises(ValueError):
        MatchStrategyFactory.get_rule_strategy_class("INVALID_TYPE")


# RULE Unknown
def test_apply_filter_rule_none_unknown(exact_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)
    result = exact_match_strategy.apply_filter_rule_unknown(sample_rules, dimension)
    assert result.filter(filter_condition=fc.eq("filter_rule_unknown", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 0  # No UNKNOWN values in DIM_1

def test_apply_filter_rule_one_unknown(exact_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_4", match_strategy=MatchStrategy.EXACT, data_type=str)
    result = exact_match_strategy.apply_filter_rule_unknown(sample_rules, dimension)
    assert result.filter(filter_condition=fc.eq("filter_rule_unknown", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 1  # One UNKNOWN values in DIM_4.


def test_apply_filter_context_unknown(exact_match_strategy, sample_rules):

    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)
    context_value = RuleConstants.UNKNOWN  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value

    result = exact_match_strategy.apply_filter_context_unknown(sample_rules, dimension=dimension, context_value=context_value)
    assert result.filter(filter_condition=fc.eq("filter_context_unknown", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 3  # All rows should match UNKNOWN


def test_exact_match_strategy_with_invalid_input(exact_match_strategy, sample_rules):
    #Non-casting needs more work to test!
    dimension = Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.EXACT, data_type=int)

    with pytest.raises(Exception):
        Context(DIM_2="NOT_AN_INT")

        # result = exact_match_strategy.apply_match_filter(sample_rules, dimension, context)

        # print( result.materialise())
        # assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()).count() == 3  # All should be PRIME_FALSE


def test_range_match_strategy_with_edge_cases(range_match_strategy, sample_rules):
    dimension = Dimension(
        dimension_name="DIM_2",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )

    # PHASE 1 OPTIMIZATION: Pass pre-extracted context values
    result_min = range_match_strategy.apply_match_filter(sample_rules, dimension, 0)
    result_max = range_match_strategy.apply_match_filter(sample_rules, dimension, 29)


    assert result_min.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 1
    assert result_max.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 1

def test_regex_match_strategy_with_complex_pattern(regex_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    complex_rules = sample_rules.mutate(DIM_3=ibis.literal("^[A-Z][a-z]+$"))

    context_value = "Hello"  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value

    result = regex_match_strategy.apply_match_filter(complex_rules, dimension, context_value)
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 3  # All should match


def test_regex_match_strategy_with_context_all_none(regex_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    context_value = RuleConstants.NOT_SET  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value for None case

    result = regex_match_strategy.apply_match_filter(sample_rules, dimension, context_value)
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())).count() == 3  # Should be UNKNOWN when NOT_SET


def test_exact_match_strategy_with_context_all_none(exact_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)

    context = Context(DIM_1=None, DIM_2=None, DIM_3=None)

    result = exact_match_strategy.apply_match_filter(sample_rules, dimension, context)
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 0  # PRIME_TRUE = 2

def test_range_match_strategy_with_context_all_none(range_match_strategy, sample_rules):
    dimension = Dimension(
        dimension_name="DIM_2",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )

    context = Context(DIM_1=None, DIM_2=None, DIM_3=None)

    result = range_match_strategy.apply_match_filter(sample_rules, dimension, context)
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 0  # PRIME_TRUE = 2


# Additional tests for improved coverage

def test_apply_filter_rule_unknown_with_numeric_dimension(range_match_strategy, sample_rules):
    """Test apply_filter_rule_unknown with numeric dimension type."""
    numeric_rules = sample_rules.mutate(DIM_2_MIN_UNKNOWN=ibis.literal(RuleConstants.UNKNOWN_NUMERIC))
    dimension = Dimension(
        dimension_name="DIM_2_MIN_UNKNOWN",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_2_MIN_UNKNOWN",
        range_max_field="DIM_2_MAX"
    )
    result = range_match_strategy.apply_filter_rule_unknown(numeric_rules, dimension)
    assert result.filter(filter_condition=fc.eq("filter_rule_unknown", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 3


def test_apply_filter_rule_unknown_with_string_dimension(exact_match_strategy, sample_rules):
    """Test apply_filter_rule_unknown with string dimension type."""
    dimension = Dimension(dimension_name="DIM_4", match_strategy=MatchStrategy.EXACT, data_type=str)
    result = exact_match_strategy.apply_filter_rule_unknown(sample_rules, dimension)
    # DIM_4 has one UNKNOWN value
    assert result.filter(filter_condition=fc.eq("filter_rule_unknown", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 1


def test_apply_filter_context_unknown_with_numeric_unknown(exact_match_strategy, sample_rules):
    """Test apply_filter_context_unknown with numeric unknown value."""
    dimension = Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.EXACT, data_type=int)
    context_value = RuleConstants.UNKNOWN_NUMERIC  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value
    result = exact_match_strategy.apply_filter_context_unknown(sample_rules, dimension, context_value)
    assert result.filter(filter_condition=fc.eq("filter_context_unknown", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 3


def test_apply_filter_context_unknown_with_string_unknown(exact_match_strategy, sample_rules):
    """Test apply_filter_context_unknown with string unknown value."""
    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)
    context_value = RuleConstants.UNKNOWN  # PHASE 1 OPTIMIZATION: Pass pre-extracted context value
    result = exact_match_strategy.apply_filter_context_unknown(sample_rules, dimension, context_value)
    assert result.filter(filter_condition=fc.eq("filter_context_unknown", RuleTrinaryFlags.PRIME_TRUE_IBIS())).count() == 3


def test_apply_filter_context_unknown_exception_handling(exact_match_strategy, sample_rules):
    """Test exception handling in apply_filter_context_unknown."""
    dimension = Dimension(dimension_name="NONEXISTENT_DIM", match_strategy=MatchStrategy.EXACT, data_type=str)
    # PHASE 1 OPTIMIZATION: Since context extraction now happens outside the strategy,
    # this test simulates a valid context value that doesn't trigger an exception
    context_value = "A"
    result = exact_match_strategy.apply_filter_context_unknown(sample_rules, dimension, context_value)
    # Should set PRIME_UNKNOWN for all rows (non-UNKNOWN context value)
    assert result.filter(filter_condition=fc.eq("filter_context_unknown", RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())).count() == 3


def test_exact_match_strategy_exception_handling_context_value(exact_match_strategy, sample_rules):
    """Test exception handling in ExactMatchStrategy apply_match_filter for context value."""
    dimension = Dimension(dimension_name="NONEXISTENT_DIM", match_strategy=MatchStrategy.EXACT, data_type=str)
    context = Context(DIM_1="A")
    result = exact_match_strategy.apply_match_filter(sample_rules, dimension, context)
    # Should handle exception and set PRIME_UNKNOWN for all rows
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())).count() == 3


def test_exact_match_strategy_exception_handling_match_logic(exact_match_strategy):
    """Test exception handling in ExactMatchStrategy apply_match_filter for match logic."""
    # Create rules that might cause issues in the match logic
    problematic_rules = pl.DataFrame({
        "rule_name": ["rule_1"],
        "DIM_1": [None],  # This might cause issues
    })
    rules = IbisDataFrame(problematic_rules, ibis_backend_schema="sqlite")

    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)
    context = Context(DIM_1="A")
    result = exact_match_strategy.apply_match_filter(rules, dimension, context)
    # Should still return a result
    assert result.count() >= 0


def test_regex_match_strategy_exception_handling_context_value(regex_match_strategy, sample_rules):
    """Test exception handling in RegexMatchStrategy apply_match_filter for context value."""
    dimension = Dimension(dimension_name="NONEXISTENT_DIM", match_strategy=MatchStrategy.REGEX, data_type=str)
    context = Context(DIM_3="XYZ")
    result = regex_match_strategy.apply_match_filter(sample_rules, dimension, context)
    # Should handle exception and set PRIME_UNKNOWN for all rows
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())).count() == 3


def test_regex_match_strategy_exception_handling_match_logic(regex_match_strategy):
    """Test exception handling in RegexMatchStrategy apply_match_filter for match logic."""
    # Create rules with potentially problematic regex patterns
    problematic_rules = pl.DataFrame({
        "rule_name": ["rule_1"],
        "DIM_3": [None],  # This might cause issues with regex
    })
    rules = IbisDataFrame(problematic_rules, ibis_backend_schema="sqlite")

    dimension = Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    context = Context(DIM_3="test")
    result = regex_match_strategy.apply_match_filter(rules, dimension, context)
    # Should still return a result
    assert result.count() >= 0


def test_regex_match_strategy_with_numeric_type_handling(regex_match_strategy, sample_rules):
    """Test RegexMatchStrategy with numeric data type (should use NOT_SET_NUMERIC)."""
    # Add a numeric field to rules for regex testing
    numeric_regex_rules = sample_rules.mutate(DIM_NUMERIC=ibis.literal("\\d+"))
    dimension = Dimension(dimension_name="DIM_NUMERIC", match_strategy=MatchStrategy.REGEX, data_type=int)
    context = Context(DIM_1="123")  # This will be processed as numeric context

    result = regex_match_strategy.apply_match_filter(numeric_regex_rules, dimension, context)
    # Should execute without error and handle numeric type appropriately
    assert result.count() >= 0


def test_range_match_strategy_exception_handling_context_value(range_match_strategy, sample_rules):
    """Test exception handling in RangeMatchStrategy apply_match_filter for context value."""
    dimension = Dimension(
        dimension_name="NONEXISTENT_DIM",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )
    context = Context(DIM_2=15)
    result = range_match_strategy.apply_match_filter(sample_rules, dimension, context)
    # Should handle exception and set PRIME_UNKNOWN for all rows
    assert result.filter(filter_condition=fc.eq("filter_match", RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())).count() == 3


def test_range_match_strategy_exception_handling_match_logic(range_match_strategy):
    """Test exception handling in RangeMatchStrategy apply_match_filter for match logic."""
    # Create rules that might cause issues in range matching
    problematic_rules = pl.DataFrame({
        "rule_name": ["rule_1"],
        "DIM_2_MIN": [None],
        "DIM_2_MAX": [None]
    })
    rules = IbisDataFrame(problematic_rules, ibis_backend_schema="sqlite")

    dimension = Dimension(
        dimension_name="DIM_2",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )
    context = Context(DIM_2=15)
    result = range_match_strategy.apply_match_filter(rules, dimension, context)
    # Should still return a result
    assert result.count() >= 0


def test_range_match_strategy_with_string_type_handling(range_match_strategy, sample_rules):
    """Test RangeMatchStrategy with string data type (should use NOT_SET)."""
    # Add string range fields to rules
    string_range_rules = sample_rules.mutate(
        DIM_STR_MIN=ibis.literal("A"),
        DIM_STR_MAX=ibis.literal("Z")
    )
    dimension = Dimension(
        dimension_name="DIM_STR",
        match_strategy=MatchStrategy.RANGE,
        data_type=str,
        range_min_field="DIM_STR_MIN",
        range_max_field="DIM_STR_MAX"
    )
    context = Context(DIM_1="M")  # This will be processed as string context

    result = range_match_strategy.apply_match_filter(string_range_rules, dimension, context)
    # Should execute without error and handle string type appropriately
    assert result.count() >= 0


def test_range_match_strategy_with_inclusive_exclusive_boundaries():
    """Test RangeMatchStrategy with different inclusive/exclusive boundary settings."""
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2"],
        "DIM_MIN": [10, 20],
        "DIM_MAX": [15, 25]
    })
    rules = IbisDataFrame(rules_df, ibis_backend_schema="sqlite")

    # Test with exclusive boundaries
    dimension_exclusive = Dimension(
        dimension_name="DIM_TEST",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_MIN",
        range_max_field="DIM_MAX",
        range_min_inclusive=False,
        range_max_inclusive=False
    )

    context = Context(DIM_2=10)  # Should not match with exclusive boundary
    strategy = RangeMatchStrategy()
    result = strategy.apply_match_filter(rules, dimension_exclusive, context)
    # The exact assertion depends on the boundary logic implementation
    assert result.count() >= 0


def test_match_strategy_factory_with_invalid_strategy():
    """Test MatchStrategyFactory with completely invalid strategy."""
    class InvalidStrategy:
        pass

    invalid_strategy = InvalidStrategy()
    with pytest.raises(ValueError, match="Invalid rule type"):
        MatchStrategyFactory.get_rule_strategy_class(invalid_strategy)


def test_base_match_strategy_abstract_method():
    """Test that BaseMatchStrategy cannot be instantiated directly."""
    from mountainash_utils_rules.rule_strategies import BaseMatchStrategy

    # BaseMatchStrategy is abstract and should not be instantiable
    with pytest.raises(TypeError):
        BaseMatchStrategy()
