import pytest
from mountainash_utils_rules.rule_strategies import ExactMatchStrategy, RangeMatchStrategy, RegexMatchStrategy, MatchStrategyFactory
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags
from mountainash_data import BaseDataFrame, DataFrameFactory
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
    return DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules_df, ibis_backend_schema="sqlite")


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

    context = Context(DIM_1="A")

    result = exact_match_strategy.apply_match_filter(sample_rules, dimension, context)
    print( result.materialise())    
    assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 1  # PRIME_TRUE = 2

def test_range_match_strategy(range_match_strategy, sample_rules):
    dimension = Dimension(
        dimension_name="DIM_2",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="DIM_2_MIN",
        range_max_field="DIM_2_MAX"
    )

    context = Context(DIM_2=15)

    result = range_match_strategy.apply_match_filter(sample_rules, dimension, context)
    print( result.materialise())    
    assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE).count() == 1  # PRIME_TRUE = 2

def test_regex_match_strategy(regex_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    context = Context(DIM_3="XYZ")

    result = regex_match_strategy.apply_match_filter(sample_rules, dimension, context)
    print( result.materialise())    
    assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 1  # PRIME_TRUE = 2

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
    assert result.filter(ibis._.filter_rule_unknown == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 0  # No UNKNOWN values in DIM_1

def test_apply_filter_rule_one_unknown(exact_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_4", match_strategy=MatchStrategy.EXACT, data_type=str)
    result = exact_match_strategy.apply_filter_rule_unknown(sample_rules, dimension)
    assert result.filter(ibis._.filter_rule_unknown == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 1  # One UNKNOWN values in DIM_4. 


def test_apply_filter_context_unknown(exact_match_strategy, sample_rules):

    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)
    context = Context(DIM_1=RuleConstants.UNKNOWN)

    result = exact_match_strategy.apply_filter_context_unknown(sample_rules, dimension=dimension, context=context)
    print( result.materialise())    
    assert result.filter(ibis._.filter_context_unknown == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 3  # All rows should match UNKNOWN


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

    context = Context(DIM_2=0)
    result_min = range_match_strategy.apply_match_filter(sample_rules, dimension, context)
    context = Context(DIM_2=29)
    result_max = range_match_strategy.apply_match_filter(sample_rules, dimension, context)

    print( result_min.materialise())    
    print( result_max.materialise())    

    assert result_min.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 1
    assert result_max.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 1

def test_regex_match_strategy_with_complex_pattern(regex_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    complex_rules = sample_rules.mutate(DIM_3=ibis.literal("^[A-Z][a-z]+$"))

    context = Context(DIM_3="Hello")

    result = regex_match_strategy.apply_match_filter(complex_rules, dimension, context)
    print( result.materialise())    
    assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 3  # All should match


def test_regex_match_strategy_with_context_all_none(regex_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    context = Context(DIM_1=None, DIM_2=None, DIM_3=None)

    result = regex_match_strategy.apply_match_filter(sample_rules, dimension, context)
    print( result.materialise())    
    assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 0  # All should match    


def test_exact_match_strategy_with_context_all_none(exact_match_strategy, sample_rules):
    dimension = Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)

    context = Context(DIM_1=None, DIM_2=None, DIM_3=None)

    result = exact_match_strategy.apply_match_filter(sample_rules, dimension, context)
    print( result.materialise())    
    assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).count() == 0  # PRIME_TRUE = 2

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
    print( result.materialise())    
    assert result.filter(ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE).count() == 0  # PRIME_TRUE = 2    