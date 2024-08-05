import pytest
from pytest_check import check 

import polars as pl
import ibis
import ibis.expr.types as ir
from mountainash_utils_rules import RulesEngine  # Replace `your_module` with the actual module name
from mountainash_data import BaseDataFrame
from dataclasses import dataclass
from typing import Optional

ibis.set_backend(backend="polars")

# from mountainash_data import BaseDataFrame, DataFrameUtils, IbisDataFrame

UNKNOWN = "<NA>"

# import pytest
# from module_name import apply_context_rules_engine_ibis, rules, dimensions, context
@dataclass
class context:
    rule_name:  Optional[str]
    DIM_1:      Optional[str]
    DIM_2:      Optional[str]
    DIM_3:      Optional[str]

CONTEXT = context(rule_name="rule_1", DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)


rules = pl.DataFrame({  "rule_name": ["rule_1", "rule_2", "rule_3"],
                        "DIM_1": ["A", "B", "C"],
                        "DIM_2": ["1", "2", "3"],
                        "DIM_3": ["X", UNKNOWN, UNKNOWN]
                    })
df_rules = ibis.memtable(data=rules, columns = rules.columns)

dimensions = ["DIM_1", "DIM_2", "DIM_3"]

dimension_tests = [
    ("DIM_1", 1),
    ("DIM_2", 1),
    ("DIM_3", 3),
]
@pytest.mark.parametrize("dimension, count_matching", dimension_tests)
def test_single_dimension(dimension, count_matching):
        
    dimensions = [dimension]
    rules = RulesEngine.apply_context_rules_engine(CONTEXT, df_rules, dimensions, keep_all=False)

    with check:
        assert rules.count() == count_matching


def test_apply_context_rules_engine_ibis_no_rules_specified():
    empty_rules = pl.DataFrame({})
    df_empty_rules = ibis.memtable(data=empty_rules, columns = empty_rules.columns)   
    with pytest.raises(ValueError):
        RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_empty_rules, dimensions=dimensions)


def test_apply_context_rules_engine_ibis_no_dimensions_specified():
    with pytest.raises(ValueError):
        RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=[])



def test_apply_context_rules_engine_ibis_result_type():
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions)
    assert isinstance(result, BaseDataFrame)


def test_apply_context_rules_engine_firtst_row():
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions, keep_all=False)
    dict_best_result = result.get_first_row_as_dict()
    assert dict_best_result['rule_name' ] == "rule_1"   


@pytest.mark.parametrize("non_rules", [
    (1),
    ("A"),
    ([1,2,3]),
    (None),
    (True),
    (["A", "B", "C"])
])
def test_apply_context_rules_engine_non_datafram_rules1(non_rules):
    with pytest.raises(TypeError):
        RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=non_rules, dimensions=dimensions)
        #Error goes all the way to the dataframe factory, it is caught and raised when trying to convert the rules to Polars
        #Pretty sure this is good


    