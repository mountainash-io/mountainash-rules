import pytest
from mountainash_utils_rules.rule_strategies import ExactMatchStrategy, RangeMatchStrategy, RegexMatchStrategy, RuleTypeFactory
from mountainash_utils_rules.metadata import DimensionMetadata
from mountainash_utils_rules.constants import RuleType, RuleConstants
from mountainash_data import BaseDataFrame, DataFrameFactory
import polars as pl
import ibis

@pytest.fixture
def sample_rules():
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", "C"],
        "DIM_2_MIN": [0, 10, 20],
        "DIM_2_MAX": [9, 19, 29],
        "DIM_3": ["X.*", "Y.*", "Z.*"]
    })
    return DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules_df, ibis_backend_schema="sqlite")

@pytest.fixture
def exact_match_strategy():
    return ExactMatchStrategy()

@pytest.fixture
def range_match_strategy():
    return RangeMatchStrategy()

@pytest.fixture
def regex_match_strategy():
    return RegexMatchStrategy()

def test_exact_match_strategy(exact_match_strategy, sample_rules):
    dimension = DimensionMetadata(dimension_name="DIM_1", rule_type=RuleType.EXACT, data_type="string")
    result = exact_match_strategy.apply_match_filter(sample_rules, dimension, "A")
    assert result.filter(ibis._.filter_match == 2).count() == 1  # PRIME_TRUE = 2

def test_range_match_strategy(range_match_strategy, sample_rules):
    dimension = DimensionMetadata(
        dimension_name="DIM_2",
        rule_type=RuleType.RANGE,
        data_type="int",
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )
    result = range_match_strategy.apply_match_filter(sample_rules, dimension, 15)
    assert result.filter(ibis._.filter_match == 2).count() == 1  # PRIME_TRUE = 2

def test_regex_match_strategy(regex_match_strategy, sample_rules):
    dimension = DimensionMetadata(dimension_name="DIM_3", rule_type=RuleType.REGEX, data_type="string")
    result = regex_match_strategy.apply_match_filter(sample_rules, dimension, "XYZ")
    assert result.filter(ibis._.filter_match == 2).count() == 1  # PRIME_TRUE = 2

def test_rule_type_factory():
    assert isinstance(RuleTypeFactory.get_rule_strategy_class(RuleType.EXACT), ExactMatchStrategy)
    assert isinstance(RuleTypeFactory.get_rule_strategy_class(RuleType.RANGE), RangeMatchStrategy)
    assert isinstance(RuleTypeFactory.get_rule_strategy_class(RuleType.REGEX), RegexMatchStrategy)
    with pytest.raises(ValueError):
        RuleTypeFactory.get_rule_strategy_class("INVALID_TYPE")

def test_apply_filter_rule_unknown(exact_match_strategy, sample_rules):
    dimension = DimensionMetadata(dimension_name="DIM_1", rule_type=RuleType.EXACT, data_type="string")
    result = exact_match_strategy.apply_filter_rule_unknown(sample_rules, dimension)
    assert result.filter(ibis._.filter_rule_unknown == 2).count() == 0  # No UNKNOWN values in DIM_1

def test_apply_filter_context_unknown(exact_match_strategy, sample_rules):
    result = exact_match_strategy.apply_filter_context_unknown(sample_rules, RuleConstants.UNKNOWN)
    assert result.filter(ibis._.filter_context_unknown == 2).count() == 3  # All rows should match UNKNOWN

def test_exact_match_strategy_with_invalid_input(exact_match_strategy, sample_rules):
    dimension = DimensionMetadata(dimension_name="DIM_1", rule_type=RuleType.EXACT, data_type="int")
    result = exact_match_strategy.apply_match_filter(sample_rules, dimension, "NOT_AN_INT")
    assert result.filter(ibis._.filter_match == 3).count() == 3  # All should be PRIME_FALSE

def test_range_match_strategy_with_edge_cases(range_match_strategy, sample_rules):
    dimension = DimensionMetadata(
        dimension_name="DIM_2",
        rule_type=RuleType.RANGE,
        data_type="int",
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )
    result_min = range_match_strategy.apply_match_filter(sample_rules, dimension, 0)
    result_max = range_match_strategy.apply_match_filter(sample_rules, dimension, 29)
    assert result_min.filter(ibis._.filter_match == 2).count() == 1
    assert result_max.filter(ibis._.filter_match == 2).count() == 1

def test_regex_match_strategy_with_complex_pattern(regex_match_strategy, sample_rules):
    dimension = DimensionMetadata(dimension_name="DIM_3", rule_type=RuleType.REGEX, data_type="string")
    complex_rules = sample_rules.mutate(DIM_3=ibis.literal("^[A-Z][a-z]+$"))
    result = regex_match_strategy.apply_match_filter(complex_rules, dimension, "Hello")
    assert result.filter(ibis._.filter_match == 2).count() == 3  # All should match