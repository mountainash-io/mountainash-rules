import pytest
from pytest_check import check
import polars as pl
import ibis
from mountainash_utils_rules import RulesEngine, DimensionsMetadata, Dimension, MatchStrategy
from mountainash_data import BaseDataFrame, DataFrameFactory
from dataclasses import dataclass
from typing import Optional, Any, List
from pydantic import BaseModel

ibis.set_backend(backend="polars")

UNKNOWN = "<NA>"




# @dataclass
class Context(BaseModel):
    DIM_1: Optional[Any]
    DIM_2: Optional[Any]
    DIM_3: Optional[Any]

CONTEXT = Context(DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)

@pytest.fixture
def rules_df() -> pl.DataFrame:
    return pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", "C"],
        "DIM_2": ["1", "2", "3"],
        "DIM_3": ["X", UNKNOWN, UNKNOWN]
    })

@pytest.fixture
def df_rules(rules_df):
    return DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules_df, ibis_backend_schema="sqlite")

@pytest.fixture
def rule_metadata():
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", rule_type=MatchStrategy.EXACT, data_type="string"),
            Dimension(dimension_name="DIM_2", rule_type=MatchStrategy.EXACT, data_type="int"),
            Dimension(dimension_name="DIM_3", rule_type=MatchStrategy.EXACT, data_type="string")
        ]
    )

@pytest.fixture
def rules_engine(df_rules, rule_metadata) -> RulesEngine:
    return RulesEngine(rules=df_rules, rule_metadata=rule_metadata)



########
# Tests

def test_rules_engine_initialization(rules_engine):
    assert isinstance(rules_engine, RulesEngine)
    assert isinstance(rules_engine.rule_manager.rules, BaseDataFrame)
    assert isinstance(rules_engine.metadata_manager.raw_rule_metadata, DimensionsMetadata)

def test_apply_context_rules_engine_single_dimension(rules_engine):
    dimension_tests = [
        (["DIM_1"], 1),
        (["DIM_2"], 1),
        (["DIM_3"], 3),
    ]
    for dimensions, expected_count in dimension_tests:
        result = rules_engine.apply_context_rules_engine(CONTEXT, dimensions, keep_all=True)
        matches = result.filter(ibis._.keep == True)
        with check:
            assert matches.count() == expected_count, f"Expected {expected_count} for dimensions {dimensions}, got {matches.count()}"

def test_apply_context_rules_engine_no_rules_specified():
    empty_rules = pl.DataFrame({})
    df_empty_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(empty_rules, ibis_backend_schema="sqlite")
    empty_metadata = DimensionsMetadata(dimensions=[])

    with pytest.raises(ValueError):
        empty_engine = RulesEngine(rules=df_empty_rules, rule_metadata=empty_metadata)
    
        # empty_engine.apply_context_rules_engine(CONTEXT, ["DIM_1"])

def test_apply_context_rules_engine_no_dimensions_specified(rules_engine):
    with pytest.raises(ValueError):
        rules_engine.apply_context_rules_engine(CONTEXT, [])

def test_apply_context_rules_engine_result_type(rules_engine):
    result = rules_engine.apply_context_rules_engine(CONTEXT, ["DIM_1", "DIM_2", "DIM_3"])
    assert isinstance(result, BaseDataFrame), "Result should be an instance of BaseDataFrame"

def test_apply_context_rules_engine_first_row(rules_engine):
    result = rules_engine.apply_context_rules_engine(CONTEXT, ["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    dict_best_result = result.get_first_row_as_dict()
    assert dict_best_result['rule_name'] == "rule_1", "First row should be rule_1"

@pytest.fixture
def bad_contexts():
    return [
        # Context(DIM_1=None, DIM_2=None, DIM_3=None),
        Context(DIM_1=1, DIM_2="1", DIM_3=[1]),
        Context(DIM_1="1", DIM_2="2", DIM_3=Context(DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)),
        Context(DIM_1={1:"four"}, DIM_2=("tuples", "AHHHHH"), DIM_3=7)
    ]

def test_bad_contexts(rules_engine, bad_contexts):
    for ctx in bad_contexts:
        with pytest.raises(expected_exception=TypeError):
            rules_engine.apply_context_rules_engine(ctx, ["DIM_1", "DIM_2", "DIM_3"], keep_all=False)


def test_rules_sqlite_backend(rules_df, rule_metadata):
    df_rules_sqlite = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules_df, ibis_backend_schema="sqlite")
    engine = RulesEngine(rules=df_rules_sqlite, rule_metadata=rule_metadata)
    result = engine.apply_context_rules_engine(CONTEXT, ["DIM_1", "DIM_2", "DIM_3"])
    assert isinstance(result, BaseDataFrame), "Result should be an instance of BaseDataFrame"

@pytest.fixture
def problematic_rules():
    return [
        pl.DataFrame({"rule_name": ["rule_1", "rule_2", "rule_3"],
                      "DIM_1": [None, None, None],
                      "DIM_2": [None, None, None],
                      "DIM_3": [None, None, None]}),
        pl.DataFrame({"rule_name": ["rule_1", "rule_2", "rule_3"],
                      "DIM_1": ["A", 1, 2],
                      "DIM_2": ["1", "2", "3"],
                      "DIM_3": [None, "X", UNKNOWN]}),
        pl.DataFrame({"rule_name": ["rule_1", "rule_2", "rule_3"],
                      "DIM_1": [complex(1, 1), [1, 2, 3], {"key": "value"}],
                      "DIM_2": [None, None, None],
                      "DIM_3": [None, None, None]})
    ]

def test_problematic_rules(problematic_rules, rule_metadata):
    for rules in problematic_rules:
        try:
            df_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules, ibis_backend_schema="sqlite")
            engine = RulesEngine(rules=df_rules, rule_metadata=rule_metadata)
            result = engine.apply_context_rules_engine(CONTEXT, ["DIM_1", "DIM_2", "DIM_3"])
            assert isinstance(result, BaseDataFrame), f"Result should be BaseDataFrame for rules: {rules}"
        except Exception as e:
            print(f"Error occurred for rules: {rules}")
            print(f"Error message: {str(e)}")

@pytest.mark.parametrize("dimensions", [
    ["DIM_1", "DIM_2"],
    ["DIM_1", "DIM_2", "DIM_3", "DIM_4"],
    ["DIM_1"],
    []
])
def test_various_dimensions(rules_engine, dimensions):
    if not dimensions:
        with pytest.raises(ValueError):
            rules_engine.apply_context_rules_engine(CONTEXT, dimensions)
    else:
        result = rules_engine.apply_context_rules_engine(CONTEXT, dimensions)
        assert isinstance(result, BaseDataFrame), f"Result should be BaseDataFrame for dimensions: {dimensions}"

def test_invalid_dimensions(rules_engine):
    with pytest.raises(TypeError):
        rules_engine.apply_context_rules_engine(CONTEXT, [None, None, None])

    with pytest.raises(TypeError):
        rules_engine.apply_context_rules_engine(CONTEXT, [1, 2, 3])

class NonDataclassContext:
    def __init__(self, DIM_1, DIM_2, DIM_3):
        self.DIM_1 = DIM_1
        self.DIM_2 = DIM_2
        self.DIM_3 = DIM_3

def test_non_dataclass_context(rules_engine):
    non_dataclass_context = NonDataclassContext(DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)
    result = rules_engine.apply_context_rules_engine(non_dataclass_context, ["DIM_1", "DIM_2", "DIM_3"])
    assert isinstance(result, BaseDataFrame), "Result should be an instance of BaseDataFrame"

    # Compare with dataclass context result
    dataclass_result = rules_engine.apply_context_rules_engine(CONTEXT, ["DIM_1", "DIM_2", "DIM_3"])
    assert result.count() == dataclass_result.count(), "Non-dataclass and dataclass context should yield same count"
    assert result.get_first_row_as_dict() == dataclass_result.get_first_row_as_dict(), "First row should be the same for both contexts"

def test_flexible_type_matching(rules_engine):

    @dataclass
    class FlexibleTypeContext:
        DIM_1: str
        DIM_2: Any
        DIM_3: Any

    # Test with string, int, and float
    context1 = FlexibleTypeContext(DIM_1="A", DIM_2="1", DIM_3="X")
    result1 = rules_engine.apply_context_rules_engine(context1, ["DIM_1", "DIM_2", "DIM_3"])
    print(f"DIM_1: {rules_engine.intermediate_values['DIM_1'].as_dict()}")
    print(f"DIM_2: {rules_engine.intermediate_values['DIM_2'].as_dict()}")
    print(f"DIM_3: {rules_engine.intermediate_values['DIM_3'].as_dict()}")

    with check:
        assert result1.filter(ibis._.keep == True).count() > 0#, "Should find matches for string, int, and string"

    # Test with all strings
    context2 = FlexibleTypeContext(DIM_1="A", DIM_2="1", DIM_3="X")
    result2 = rules_engine.apply_context_rules_engine(context2, ["DIM_1", "DIM_2", "DIM_3"])
    print(rules_engine.intermediate_values)
    with check:
        assert result2.filter(ibis._.keep == True).count() > 0#, "Should find matches for all strings"

    # Test with mixed types
    context3 = FlexibleTypeContext(DIM_1="A", DIM_2=1.0, DIM_3="X")
    result3 = rules_engine.apply_context_rules_engine(context3, ["DIM_1", "DIM_2", "DIM_3"])
    print(rules_engine.intermediate_values)

    with check:
        assert result3.filter(ibis._.keep == True).count() > 0#, "Should find matches for string, float, and string"

def test_unsupported_type_handling(rules_engine):
    @dataclass
    class UnsupportedTypeContext:
        DIM_1: str
        DIM_2: str
        DIM_3: List[int]  # Previously unsupported type

    context = UnsupportedTypeContext(DIM_1="A", DIM_2="1", DIM_3=[1, 2, 3])

    with pytest.raises(expected_exception=TypeError):
        rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])

    # result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"], keep_all=True)
    # print(rules_engine.intermediate_values)

    # # Now, we expect to find matches for DIM_1 and DIM_2, but not for DIM_3
    # matches = result.filter(ibis._.keep == True)
    # assert matches.count() > 0, "Should find matches for supported types (DIM_1 and DIM_2)"

def test_rule_type_exact(df_rules):
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", rule_type=MatchStrategy.EXACT),
            Dimension(dimension_name="DIM_2", rule_type=MatchStrategy.EXACT),
            Dimension(dimension_name="DIM_3", rule_type=MatchStrategy.EXACT)
        ]
    )
    engine = RulesEngine(rules=df_rules, rule_metadata=metadata)
    result = engine.apply_context_rules_engine(CONTEXT, ["DIM_1", "DIM_2", "DIM_3"])
    # print(engine.intermediate_values)
    assert result.filter(ibis._.keep == True).count() == 1, "Should find exactly one match for EXACT rule type"

def test_rule_type_range():
    range_rules = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", "C"],
        "DIM_2_MIN": [0, 2, 4],
        "DIM_2_MAX": [2, 4, 6],
        "DIM_3": ["X", UNKNOWN, UNKNOWN]
    })

    range_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(range_rules, ibis_backend_schema="sqlite")

    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", rule_type=MatchStrategy.EXACT),
            Dimension(dimension_name="DIM_2", rule_type=MatchStrategy.RANGE, range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX", data_type="int"),
            Dimension(dimension_name="DIM_3", rule_type=MatchStrategy.EXACT)
        ]
    )
    engine = RulesEngine(rules=range_rules, rule_metadata=metadata)
    context = Context(DIM_1="A", DIM_2=1, DIM_3=UNKNOWN)
    result = engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    # print(engine.intermediate_values)
    assert result.filter(ibis._.keep == True).count() == 1, "Should find one match for RANGE rule type with correct type in context"

    context = Context(DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)
    result2 = engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    # print(engine.intermediate_values)
    assert result2.filter(ibis._.keep == True).count() == 1, "Should find one match for RANGE rule type with Incorrect type in context cast to int"

# def test_rule_type_wildcard(rules_df):
#     wildcard_rules = pl.DataFrame({
#         "rule_name": ["rule_1", "rule_2", "rule_3"],
#         "DIM_1": ["A*", "B*", "C*"],
#         "DIM_2": ["1", "2", "3"],
#         "DIM_3": ["X", UNKNOWN, UNKNOWN]
#     })
#     wildcard_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(wildcard_rules, ibis_backend_schema="sqlite")

#     metadata = DimensionsMetadata(
#         dimensions=[
#             Dimension(name="DIM_1", rule_type=MatchStrategy.WILDCARD),
#             Dimension(name="DIM_2", rule_type=MatchStrategy.EXACT),
#             Dimension(name="DIM_3", rule_type=MatchStrategy.EXACT)
#         ]
#     )
#     engine = RulesEngine(rules=wildcard_rules, rule_metadata=metadata)
    
#     # Test exact match
#     context1 = Context(DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)
#     result1 = engine.apply_context_rules_engine(context1, ["DIM_1", "DIM_2", "DIM_3"])
#     print(engine.intermediate_values)
#     assert result1.filter(ibis._.keep == True).count() == 1, "Should find one match for exact wildcard match"
    
#     # Test wildcard match
#     context2 = Context(DIM_1="ABC", DIM_2="1", DIM_3=UNKNOWN)
#     result2 = engine.apply_context_rules_engine(context2, ["DIM_1", "DIM_2", "DIM_3"])
#     print(engine.intermediate_values)
#     assert result2.filter(ibis._.keep == True).count() == 1, "Should find one match for wildcard match"
    
#     # Test no match
#     context3 = Context(DIM_1="D", DIM_2="1", DIM_3=UNKNOWN)
#     result3 = engine.apply_context_rules_engine(context3, ["DIM_1", "DIM_2", "DIM_3"])
#     print(engine.intermediate_values)
#     assert result3.filter(ibis._.keep == True).count() == 0, "Should find no matches for non-matching wildcard"

def test_rule_type_regex():
    regex_rules = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A[0-9]", "B[a-z]", "C.*"],
        "DIM_2": ["1", "2", "3"],
        "DIM_3": ["X", UNKNOWN, UNKNOWN]
    })

    regex_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(regex_rules, ibis_backend_schema="sqlite")


    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", rule_type=MatchStrategy.REGEX),
            Dimension(dimension_name="DIM_2", rule_type=MatchStrategy.EXACT),
            Dimension(dimension_name="DIM_3", rule_type=MatchStrategy.EXACT)
        ]
    )
    engine = RulesEngine(rules=regex_rules, rule_metadata=metadata)
    
    # Test regex match
    context1 = Context(DIM_1="A5", DIM_2="1", DIM_3=UNKNOWN)
    result1 = engine.apply_context_rules_engine(context1, ["DIM_1", "DIM_2", "DIM_3"])
    # print(engine.tracability_manager.intermediate_values)
    assert result1.filter(ibis._.keep == True).count() == 1, "Should find one match for regex match A[0-9]"
    
    # Test another regex match
    context2 = Context(DIM_1="Bz", DIM_2="2", DIM_3=UNKNOWN)
    result2 = engine.apply_context_rules_engine(context2, ["DIM_1", "DIM_2", "DIM_3"])
    # print(engine.intermediate_values)
    assert result2.filter(ibis._.keep == True).count() == 1, "Should find one match for regex match B[a-z]"
    
    # Test wildcard-like regex match
    context3 = Context(DIM_1="CAnything", DIM_2="3", DIM_3=UNKNOWN)
    result3 = engine.apply_context_rules_engine(context3, ["DIM_1", "DIM_2", "DIM_3"])
    # print(engine.intermediate_values)
    assert result3.filter(ibis._.keep == True).count() == 1, "Should find one match for regex match C.*"
    
    # Test no match
    context4 = Context(DIM_1="D1", DIM_2="1", DIM_3=UNKNOWN)
    result4 = engine.apply_context_rules_engine(context4, ["DIM_1", "DIM_2", "DIM_3"])
    # print(engine.intermediate_values)
    assert result4.filter(ibis._.keep == True).count() == 0, "Should find no matches for non-matching regex"

# def test_mixed_rule_types():
#     mixed_rules = pl.DataFrame({
#         "rule_name": ["rule_1", "rule_2", "rule_3", "rule_4"],
#         "DIM_1": ["A*", "[0-9]", "C", "D"],
#         "DIM_2_MIN": [0, 10, 20, 30],
#         "DIM_2_MAX": [9, 19, 29, 39],
#         "DIM_3": ["X", "Y", UNKNOWN, "Z"]
#     })

#     mixed_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(mixed_rules, ibis_backend_schema="sqlite")

#     metadata = DimensionsMetadata(
#         dimensions=[
#             Dimension(name="DIM_1", rule_type=MatchStrategy.WILDCARD),
#             Dimension(name="DIM_2", rule_type=MatchStrategy.RANGE, range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX", data_type="int"),
#             Dimension(name="DIM_3", rule_type=MatchStrategy.EXACT)
#         ]
#     )
#     engine = RulesEngine(rules=mixed_rules, rule_metadata=metadata)
    
#     with check:
#         # Test mixed rule types
#         context1 = Context(DIM_1="ABC", DIM_2=5, DIM_3="X")
#         result1 = engine.apply_context_rules_engine(context1, ["DIM_1", "DIM_2", "DIM_3"])
#         print(engine.intermediate_values)
#         assert result1.filter(ibis._.keep == True).count() == 1, "Should find one match for mixed rule types"
        
#         # Test another mixed rule scenario
#         context2 = Context(DIM_1="B7", DIM_2="15", DIM_3=UNKNOWN)
#         result2 = engine.apply_context_rules_engine(context2, ["DIM_1", "DIM_2", "DIM_3"])
#         print(engine.intermediate_values)
#         assert result2.filter(ibis._.keep == True).count() == 1, "Should find one match for another mixed rule scenario"
        
#         # Test no match scenario
#         context3 = Context(DIM_1="E", DIM_2="40", DIM_3="W")
#         result3 = engine.apply_context_rules_engine(context3, ["DIM_1", "DIM_2", "DIM_3"])
#         print(engine.intermediate_values)

#         assert result3.filter(ibis._.keep == True).count() == 0, "Should find no matches for non-matching mixed rules"

# def test_rule_priority():
#     priority_rules = pl.DataFrame({
#         "rule_name": ["rule_1", "rule_2", "rule_3"],
#         "DIM_1": ["A*", "AB*", "ABC"],
#         "DIM_2": ["1", "1", "1"],
#         "DIM_3": [UNKNOWN, UNKNOWN, UNKNOWN],
#         "priority": [3, 2, 1]
#     })

#     priority_rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(priority_rules, ibis_backend_schema="sqlite")

#     metadata = DimensionsMetadata(
#         dimensions=[
#             Dimension(name="DIM_1", rule_type=MatchStrategy.WILDCARD),
#             Dimension(name="DIM_2", rule_type=MatchStrategy.EXACT),
#             Dimension(name="DIM_3", rule_type=MatchStrategy.EXACT)
#         ]
#     )
#     engine = RulesEngine(rules=priority_rules, rule_metadata=metadata)
    
#     context = Context(DIM_1="ABC", DIM_2="1", DIM_3=UNKNOWN)
#     result = engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])
    
#     # matched_rules = result.filter(ibis._.keep == True).sort_by("priority")
#     assert matched_rules.count() == 3, "Should find all three matching rules"
#     assert matched_rules.get_first_row_as_dict()['rule_name'] == "rule_3", "Highest priority (lowest number) rule should be first"