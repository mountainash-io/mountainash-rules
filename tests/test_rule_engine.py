import pytest
from mountainash_utils_rules import RulesEngine, DimensionsMetadata, Dimension, MatchStrategy
from mountainash_utils_rules.constants import RuleConstants, RuleTrinaryFlags
# from mountainash_dataframes import BaseDataFrame, IbisDataFrame
from mountainash_dataframes.utils.dataframe_filters import FilterCondition as fc
import sqlite3
import polars as pl
import ibis
from pydantic import BaseModel

class Context(BaseModel):
    DIM_1: str
    DIM_2: int
    DIM_3: str

@pytest.fixture
def sample_rules():
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3", "rule_4", "rule_5"],
        "DIM_1": ["A", "B", "C", RuleConstants.UNKNOWN, "D"],
        "DIM_2_MIN": [0, 10, 20, 30, 40],
        "DIM_2_MAX": [9, 19, 29, 39, 49],
        "DIM_3": ["X.*", "Y.*", "Z.*", "W.*", RuleConstants.UNKNOWN]
    })
    return IbisDataFrame(rules_df, ibis_backend_schema="sqlite")

@pytest.fixture
def dimension_metadata():
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int, range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
        ]
    )

@pytest.fixture
def rules_engine(sample_rules, dimension_metadata):
    return RulesEngine(rules=sample_rules, dimension_metadata=dimension_metadata)

def test_rules_engine_initialization(rules_engine):
    assert isinstance(rules_engine, RulesEngine), "RulesEngine object not created successfully"
    assert isinstance(rules_engine.rule_manager.rules, BaseDataFrame), "Rules not loaded successfully"
    assert isinstance(rules_engine.metadata_manager.raw_dimension_metadata, DimensionsMetadata), "Dimension metadata not loaded successfully"

def test_apply_context_rules_engine_exact_match(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    print(result.to_pylist())
    assert result.filter(filter_condition=fc.eq("keep", True)).count() == 1
    assert result.filter(filter_condition=fc.eq("keep", True)).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_apply_context_rules_engine_no_match(rules_engine):
    context = Context(DIM_1="E", DIM_2=50, DIM_3="ABC")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(filter_condition=fc.eq("keep", True)).count() == 0

def test_apply_context_rules_engine_partial_match(rules_engine):
    context = Context(DIM_1="A", DIM_2=15, DIM_3="ABC")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(filter_condition=fc.eq("keep", True)).count() == 0

def test_apply_context_rules_engine_unknown_value(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3="WXY")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(filter_condition=fc.eq("keep", True)).count() == 1
    assert result.filter(filter_condition=fc.eq("keep", True)).get_first_row_as_dict()['rule_name'] == "rule_4"

def test_apply_context_rules_engine_unknown_rule(rules_engine):
    context = Context(DIM_1="D", DIM_2=45, DIM_3="ABC")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(filter_condition=fc.eq("keep", True)).count() == 1
    assert result.filter(filter_condition=fc.eq("keep", True)).get_first_row_as_dict()['rule_name'] == "rule_5"

def test_apply_context_rules_engine_subset_dimensions(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2"])
    assert result.filter(filter_condition=fc.eq("keep",  True)).count() == 1
    assert result.filter(filter_condition=fc.eq("keep",  True)).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_apply_context_rules_engine_invalid_dimension(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    # with pytest.raises(ValueError):
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "INVALID_DIM"])
    assert result.filter(filter_condition=fc.eq("keep",  True)).count() > 0

def test_apply_context_rules_engine_empty_dimensions(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    with pytest.raises(ValueError):
        rules_engine.apply_context_rules_engine(context, dimension_names=[])

def test_apply_context_rules_engine_keep_all(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    assert result.count() == 5

def test_apply_context_rules_engine_priority(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=RuleConstants.UNKNOWN_NUMERIC, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])

    assert result.filter(filter_condition=fc.eq("keep",  True)).count() == 5
    assert result.filter(filter_condition=fc.eq("keep",  True)).get_first_row_as_dict()['rule_name'] == "rule_4"

def test_apply_context_rules_engine_invalid_context_type(rules_engine):
    invalid_context = {"DIM_1": "A", "DIM_2": 5, "DIM_3": "XYZ"}

    with pytest.raises(Exception):
        rules_engine.apply_context_rules_engine(invalid_context, ["DIM_1", "DIM_2", "DIM_3"])

def test_apply_context_rules_engine_missing_context_field(rules_engine):
    class TruncatedContext(BaseModel):
        DIM_1: str
        DIM_2: int

    truncated_context = TruncatedContext(DIM_1="A", DIM_2=5)
    result = rules_engine.apply_context_rules_engine(truncated_context, ["DIM_1", "DIM_2", "DIM_3"])

    assert result.filter(filter_condition=fc.eq("keep",  True)).count() == 1
    assert result.filter(filter_condition=fc.eq("keep",  True)).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_apply_context_rules_engine_type_mismatch(rules_engine):
    context = Context(DIM_1="A", DIM_2="5", DIM_3="XYZ")  # DIM_2 is a string instead of int
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(filter_condition=fc.eq("keep",  True)).count() == 1
    assert result.filter(filter_condition=fc.eq("keep",  True)).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_tracability(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert "DIM_1" in rules_engine.observability_manager.intermediate_values
    assert "DIM_2" in rules_engine.observability_manager.intermediate_values
    assert "DIM_3" in rules_engine.observability_manager.intermediate_values

def test_rule_priority_calculation(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    priorities = result.get_column_as_list("priority")
    assert priorities == sorted(priorities)  # Ensure priorities are in ascending order

def test_apply_context_rules_engine_with_all_unknown_values(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=RuleConstants.UNKNOWN_NUMERIC, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(filter_condition=fc.eq("keep",  True)).count() == 5
    assert set(result.filter(filter_condition=fc.eq("keep",  True)).get_column_as_list("rule_name")) == {"rule_1", "rule_2", "rule_3", "rule_4", "rule_5"}


def test_apply_context_rules_engine_with_mixed_match_strategys(rules_engine):
    context = Context(DIM_1="B", DIM_2=15, DIM_3="YYY")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(filter_condition=fc.eq("keep",  True)).count() == 1
    assert result.filter(filter_condition=fc.eq("keep",  True)).get_first_row_as_dict()['rule_name'] == "rule_2"

def test_apply_context_rules_engine_edge_cases(rules_engine):
    # Test lower bound of range
    context1 = Context(DIM_1="B", DIM_2=10, DIM_3="YYY")
    result1 = rules_engine.apply_context_rules_engine(context1, ["DIM_1", "DIM_2", "DIM_3"])
    assert result1.filter(filter_condition=fc.eq("keep",  True)).count() == 1
    assert result1.filter(filter_condition=fc.eq("keep",  True)).get_first_row_as_dict()['rule_name'] == "rule_2"

    # Test upper bound of range
    context2 = Context(DIM_1="B", DIM_2=19, DIM_3="YYY")
    result2 = rules_engine.apply_context_rules_engine(context2, ["DIM_1", "DIM_2", "DIM_3"])
    assert result2.filter(filter_condition=fc.eq("keep",  True)).count() == 1
    assert result2.filter(filter_condition=fc.eq("keep",  True)).get_first_row_as_dict()['rule_name'] == "rule_2"

def test_apply_context_rules_engine_multiple_matches(rules_engine):
    # Create a context that matches multiple rules
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3="WXY")
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    matched_rules = result.filter(filter_condition=fc.eq("keep",  True))
    assert matched_rules.count() == 1
    assert set(matched_rules.get_column_as_list("rule_name")) == {"rule_4"}


def test_apply_context_rules_engine_soft_vs_hard_match(rules_engine):
    # Test a case where we have both soft (UNKNOWN) and hard matches
    context = Context(DIM_1="D", DIM_2=45, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    matched_rules = result.filter(filter_condition=fc.eq("keep",  True))
    assert matched_rules.count() == 1
    assert matched_rules.get_first_row_as_dict()['rule_name'] == "rule_5"

def test_apply_context_rules_engine_dimension_order(rules_engine):
    # Test if changing the order of dimensions affects the result
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result1 = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    result2 = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_3", "DIM_2", "DIM_1"])
    result3 = rules_engine.apply_context_rules_engine(context, dimension_names=["DIM_2", "DIM_1", "DIM_3",])

    assert result1.filter(filter_condition=fc.eq("keep",  True)).count() == result2.filter(filter_condition=fc.eq("keep",  True)).count()
    assert result1.filter(filter_condition=fc.eq("keep",  True)).count() == result3.filter(filter_condition=fc.eq("keep",  True)).count()
    assert result1.filter(filter_condition=fc.eq("keep",  True)).select(["rule_name", "priority"]).get_first_row_as_dict() == result2.filter(filter_condition=fc.eq("keep",  True)).select(["rule_name", "priority"]).get_first_row_as_dict()
    assert result1.filter(filter_condition=fc.eq("keep",  True)).select(["rule_name", "priority"]).get_first_row_as_dict() == result3.filter(filter_condition=fc.eq("keep",  True)).select(["rule_name", "priority"]).get_first_row_as_dict()


def test_apply_context_rules_engine_with_empty_rules(dimension_metadata):

    with pytest.raises(sqlite3.OperationalError):
        empty_rules = IbisDataFrame(pl.DataFrame(), ibis_backend_schema="sqlite")
        RulesEngine(rules=empty_rules, dimension_metadata=dimension_metadata)
        # context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
        # empty_engine.apply_context_rules_engine(context=context, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
