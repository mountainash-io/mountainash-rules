import pytest
from mountainash_utils_rules.observer import ObservabilityManager
from mountainash_utils_rules.dimension import Dimension
# from mountainash_dataframes import BaseDataFrame, IbisDataFrame

import polars as pl
import ibis

@pytest.fixture
def observability_manager():
    return ObservabilityManager()

@pytest.fixture
def sample_rules():
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2"],
        "DIM_1": ["A", "B"],
        "filter_match": [True, False],
        "dimension_filter_product": [2, 3],
        "dimension_any_false": [False, True],
        "dimension_any_true": [True, False],
        "cumu_dimension_count": [1, 1],
        "cumu_soft_match_count": [1, 0],
        "cumu_hard_match_count": [1, 0],
        "dropped": [False, True],
        "dropped_by_dimension": [None, "DIM_1"]
    })
    return IbisDataFrame(rules_df, ibis_backend_schema="sqlite")

@pytest.fixture
def dim_1() -> Dimension:
    return Dimension(dimension_name="DIM_1")



def test_log_intermediate_values(observability_manager, sample_rules, dim_1):
    observability_manager.save_dimension_intermediate_values(sample_rules, dim_1)
    assert "DIM_1" in observability_manager.intermediate_values
    assert isinstance(observability_manager.intermediate_values["DIM_1"], BaseDataFrame)

# def test_log_warning(observability_manager):
#     observability_manager.log_warning("DIM_1", "test_warning", "This is a test warning")
#     assert "DIM_1" in observability_manager.warnings
#     assert "test_warning" in observability_manager.warnings["DIM_1"]
#     assert observability_manager.warnings["DIM_1"]["test_warning"] == "This is a test warning"

# def test_log_context_cast_warning(observability_manager):
#     observability_manager._log_context_cast_warning("DIM_1", "1", str, "int")
#     assert "DIM_1" in observability_manager.warnings
#     assert "context_cast" in observability_manager.warnings["DIM_1"]
#     assert "Context value 1 of type <class 'str'> has been cast to int for dimension DIM_1" in observability_manager.warnings["DIM_1"]["context_cast"]

# def test_save_dimension_intermediate_values(observability_manager, sample_rules):
#     observability_manager.save_dimension_intermediate_values(sample_rules, dim_1)
#     assert "DIM_1" in observability_manager.intermediate_values
#     saved_values = observability_manager.intermediate_values["DIM_1"]
#     assert saved_values.count() == 2
#     assert set(saved_values.get_column_names()) == {
#         'rule_name', 'dimension_filter_product', 'dimension_any_false', 'dimension_any_true',
#         'cumu_dimension_count', 'cumu_soft_match_count', 'cumu_hard_match_count',
#         'dropped', 'dropped_by_dimension'
#     }

# def test_multiple_warnings_for_same_dimension(observability_manager):
#     observability_manager.log_warning("DIM_1", "warning1", "First warning")
#     observability_manager.log_warning("DIM_1", "warning2", "Second warning")
#     assert len(observability_manager.warnings["DIM_1"]) == 2
#     assert observability_manager.warnings["DIM_1"]["warning1"] == "First warning"
#     assert observability_manager.warnings["DIM_1"]["warning2"] == "Second warning"
