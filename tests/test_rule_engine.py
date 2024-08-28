import pytest
from mountainash_utils_rules import RulesEngine, RuleMetadata, DimensionMetadata, RuleType
from mountainash_utils_rules.constants import RuleConstants
from mountainash_data import BaseDataFrame, DataFrameFactory
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
    return DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules_df, ibis_backend_schema="sqlite")

@pytest.fixture
def rule_metadata():
    return RuleMetadata(
        dimensions=[
            DimensionMetadata(dimension_name="DIM_1", rule_type=RuleType.EXACT, data_type="string"),
            DimensionMetadata(dimension_name="DIM_2", rule_type=RuleType.RANGE, data_type="int", range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            DimensionMetadata(dimension_name="DIM_3", rule_type=RuleType.REGEX, data_type="string")
        ]
    )

@pytest.fixture
def rules_engine(sample_rules, rule_metadata):
    return RulesEngine(rules=sample_rules, rule_metadata=rule_metadata)

def test_rules_engine_initialization(rules_engine):
    assert isinstance(rules_engine, RulesEngine)
    assert isinstance(rules_engine.rule_manager.rules, BaseDataFrame)
    assert isinstance(rules_engine.metadata_manager.raw_rule_metadata, RuleMetadata)

def test_apply_context_rules_engine_exact_match(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 1
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_apply_context_rules_engine_no_match(rules_engine):
    context = Context(DIM_1="E", DIM_2=50, DIM_3="ABC")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 0

def test_apply_context_rules_engine_partial_match(rules_engine):
    context = Context(DIM_1="A", DIM_2=15, DIM_3="ABC")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 0

def test_apply_context_rules_engine_unknown_value(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3="WXY")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 1
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_4"

def test_apply_context_rules_engine_unknown_rule(rules_engine):
    context = Context(DIM_1="D", DIM_2=45, DIM_3="ABC")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 1
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_5"

def test_apply_context_rules_engine_subset_dimensions(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2"])
    assert result.filter(ibis._.keep == True).count() == 1
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_apply_context_rules_engine_invalid_dimension(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    with pytest.raises(ValueError):
        rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "INVALID_DIM"])

def test_apply_context_rules_engine_empty_dimensions(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    with pytest.raises(ValueError):
        rules_engine.apply_context_rules_engine(context, [])

def test_apply_context_rules_engine_keep_all(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    assert result.count() == 5

def test_apply_context_rules_engine_priority(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 2
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_4"

def test_apply_context_rules_engine_invalid_context_type(rules_engine):
    invalid_context = {"DIM_1": "A", "DIM_2": 5, "DIM_3": "XYZ"}
    with pytest.raises(ValueError):
        rules_engine.apply_context_rules_engine(invalid_context, ["DIM_1", "DIM_2", "DIM_3"])

def test_apply_context_rules_engine_missing_context_field(rules_engine):
    class InvalidContext(BaseModel):
        DIM_1: str
        DIM_2: int
    
    invalid_context = InvalidContext(DIM_1="A", DIM_2=5)
    result = rules_engine.apply_context_rules_engine(invalid_context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 1
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_apply_context_rules_engine_type_mismatch(rules_engine):
    context = Context(DIM_1="A", DIM_2="5", DIM_3="XYZ")  # DIM_2 is a string instead of int
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 1
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_1"

def test_tracability(rules_engine):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert "DIM_1" in rules_engine.tracability_manager.intermediate_values
    assert "DIM_2" in rules_engine.tracability_manager.intermediate_values
    assert "DIM_3" in rules_engine.tracability_manager.intermediate_values

def test_rule_priority_calculation(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    priorities = result.get_column_as_list("priority")
    assert priorities == sorted(priorities)  # Ensure priorities are in ascending order

def test_apply_context_rules_engine_with_all_unknown_values(rules_engine):
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=-1, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 2
    assert set(result.filter(ibis._.keep == True).get_column_as_list("rule_name")) == {"rule_4", "rule_5"}

def test_apply_context_rules_engine_with_mixed_rule_types(rules_engine):
    context = Context(DIM_1="B", DIM_2=15, DIM_3="YYY")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.filter(ibis._.keep == True).count() == 1
    assert result.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_2"

def test_apply_context_rules_engine_edge_cases(rules_engine):
    # Test lower bound of range
    context1 = Context(DIM_1="B", DIM_2=10, DIM_3="YYY")
    result1 = rules_engine.apply_context_rules_engine(context1, ["DIM_1", "DIM_2", "DIM_3"])
    assert result1.filter(ibis._.keep == True).count() == 1
    assert result1.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_2"

    # Test upper bound of range
    context2 = Context(DIM_1="B", DIM_2=19, DIM_3="YYY")
    result2 = rules_engine.apply_context_rules_engine(context2, ["DIM_1", "DIM_2", "DIM_3"])
    assert result2.filter(ibis._.keep == True).count() == 1
    assert result2.filter(ibis._.keep == True).get_first_row_as_dict()['rule_name'] == "rule_2"

def test_apply_context_rules_engine_multiple_matches(rules_engine):
    # Create a context that matches multiple rules
    context = Context(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3="WXY")
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    matched_rules = result.filter(ibis._.keep == True)
    assert matched_rules.count() == 2
    assert set(matched_rules.get_column_as_list("rule_name")) == {"rule_4", "rule_5"}

def test_apply_context_rules_engine_soft_vs_hard_match(rules_engine):
    # Test a case where we have both soft (UNKNOWN) and hard matches
    context = Context(DIM_1="D", DIM_2=45, DIM_3=RuleConstants.UNKNOWN)
    result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    matched_rules = result.filter(ibis._.keep == True)
    assert matched_rules.count() == 1
    assert matched_rules.get_first_row_as_dict()['rule_name'] == "rule_5"

def test_apply_context_rules_engine_dimension_order(rules_engine):
    # Test if changing the order of dimensions affects the result
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    result1 = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    result2 = rules_engine.apply_context_rules_engine(context, ["DIM_3", "DIM_2", "DIM_1"])
    assert result1.filter(ibis._.keep == True).count() == result2.filter(ibis._.keep == True).count()
    assert result1.filter(ibis._.keep == True).get_first_row_as_dict() == result2.filter(ibis._.keep == True).get_first_row_as_dict()

def test_apply_context_rules_engine_performance(rules_engine, benchmark):
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    benchmark(rules_engine.apply_context_rules_engine, context, ["DIM_1", "DIM_2", "DIM_3"])

def test_apply_context_rules_engine_with_empty_rules(rule_metadata):
    empty_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(pl.DataFrame(), ibis_backend_schema="sqlite")
    empty_engine = RulesEngine(rules=empty_rules, rule_metadata=rule_metadata)
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    with pytest.raises(ValueError):
        empty_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])

# def test_apply_context_rules_engine_with_single_rule(rule_metadata):
#     single_rule = pl.DataFrame({
#         "rule_name": ["rule_1"],
#         "DIM_1": ["A"],
#         "DIM_2_MIN": [0],
#         "DIM_2_MAX": [10],
#         "DIM_3": ["X.*"]
#     })
#     single_rule_df = DataFrame