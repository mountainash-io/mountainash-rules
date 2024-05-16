import pytest
from pytest_check import check 

import polars as pl
import ibis
import ibis.expr.types as ir
from mountainash_utils_rules import apply_context_rules_engine_ibis  # Replace `your_module` with the actual module name
from dataclasses import dataclass
from typing import Optional

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

CONTEXT = context(rule_name="rule_1", DIM_1="A", DIM_2="2", DIM_3=UNKNOWN)


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
    ("DIM_4", 3)
]
@pytest.mark.parametrize("dimension, count_matching", dimension_tests)
def test_single_dimension(dimension, count_matching):
        
    dimensions = [dimension]
    rules = apply_context_rules_engine_ibis(CONTEXT, df_rules, dimensions, keep_all=False)

    print(rules)

    with check:
        assert rules.count().execute() == count_matching




def test_apply_context_rules_engine_ibis_no_rules_specified():
    empty_rules = pl.DataFrame({})
    df_empty_rules = ibis.memtable(data=empty_rules, columns = empty_rules.columns)   


    with pytest.raises(ValueError):
        apply_context_rules_engine_ibis(CONTEXT=CONTEXT, rules=df_empty_rules, dimensions=dimensions)

def test_apply_context_rules_engine_ibis_result_type():
    result = apply_context_rules_engine_ibis(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions)
    assert isinstance(result, ir.Table)
