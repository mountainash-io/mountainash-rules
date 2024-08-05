import pytest
from pytest_check import check 

import polars as pl
import ibis
import ibis.expr.types as ir
from mountainash_utils_rules import RulesEngine  # Replace `your_module` with the actual module name
from mountainash_data import BaseDataFrame, DataFrameFactory
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


@pytest.mark.parametrize("non_rules, error", [
    (1, TypeError),
    ("A", TypeError),
    ([1,2,3], TypeError),
    (None, ValueError),
    (True, TypeError),
    (["A", "B", "C"], TypeError)
])
def test_apply_context_rules_engine_non_datafram_rules1(non_rules, error):
    with pytest.raises(error):
        RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=non_rules, dimensions=dimensions)
        #Error goes all the way to the dataframe factory, it is caught and raised when trying to convert the rules to Polars
        #Only small issue is that None will be caught by a different ValueError, but it is still caught

#Attemps to break function

CONTEXT_ONE = context(rule_name="None", DIM_1=None, DIM_2=None, DIM_3=None)

CONTEXT_TWO = context(rule_name="rule_1", DIM_1=1, DIM_2="1", DIM_3=[1])

CONTEXT_THREE = context(rule_name="rule_1", DIM_1="1", DIM_2="2", DIM_3=context(rule_name="rule_1", DIM_1="A", DIM_2="1", DIM_3=UNKNOWN))

CONTEXT_FOUR = context(rule_name="rule_1", DIM_1={1:"four"}, DIM_2=("tuples", "AHHHHH"), DIM_3=7)


def test_bad_contexts_one():
    rules = RulesEngine.apply_context_rules_engine(CONTEXT_ONE, df_rules, dimensions, keep_all=False)
    assert isinstance(rules, BaseDataFrame)
    #Returns an empty dataframe with keep_all=False but does return a full dataframe with keep_all=True



def test_bad_contexts_two():
    rules = RulesEngine.apply_context_rules_engine(CONTEXT_TWO, df_rules, dimensions, keep_all=False)

def test_bad_contexts_three():
    rules = RulesEngine.apply_context_rules_engine(CONTEXT_THREE, df_rules, dimensions, keep_all=False)

def test_bad_contexts_four():
    rules = RulesEngine.apply_context_rules_engine(CONTEXT_FOUR, df_rules, dimensions, keep_all=False)
"""
Problem
TODO: 
    - The function is not able to handle the context dataclass with nested dataclasses. 
    - The function is not able to handle the context dataclass with complex data types.

    It breaks unpredictably if the context dataclass uses unsupported data types.

"""




rules_one = pl.DataFrame({  "rule_name": ["rule_1", "rule_2", "rule_3"],
                        "DIM_1": [None, None, None],
                        "DIM_2": [None, None, None],
                        "DIM_3": [None, None, None]
                    })

rules_two = pl.DataFrame({  "rule_name": ["rule_1", "rule_2", "rule_3"],
                        "DIM_1": ["A", 1, 2],
                        "DIM_2": ["1", "2", "3"],
                        "DIM_3": [None, "X", UNKNOWN]
                    })
rules_three = pl.DataFrame({
    "rule_name": ["rule_1", "rule_2", "rule_3"],
    "DIM_1": [complex(1, 1), [1, 2, 3], {"key": "value"}],
    "DIM_2": [None, None, None],
    "DIM_3": [None, None, None]
})


def test_rules_sqlite_backend():
    df_rules_sqlite = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules, ibis_backend_schema = "sqlite")
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules_sqlite, dimensions=dimensions)
    assert isinstance(result, BaseDataFrame)
    
def test_bad_rules_one():
    df_rules_one = ibis.memtable(data=rules_one, columns = rules.columns)
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules_one, dimensions=dimensions)
    print(result.materialize())
    assert isinstance(result, BaseDataFrame)

    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT_ONE, rules=df_rules_one, dimensions=dimensions)
    print(result.materialize())
    assert isinstance(result, BaseDataFrame)
    print(result.materialize())
    #Doesn't break, but returns None for the columns

def test_bad_rules_two():
    df_rules_two = ibis.memtable(data=rules_two, columns = rules.columns)
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules_two, dimensions=dimensions)
    print(result.materialize())
    assert isinstance(result, BaseDataFrame)
    assert True == False
    #This is allowed through, I dont understand this function enough to know if this is a problem or if it returns incorrect things
    #TODO: Investigate this further

def test_bad_rules_three():
    df_rules_three = ibis.memtable(data=rules_three, columns = rules.columns)
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules_three, dimensions=dimensions)
    print(result.materialize())
    assert isinstance(result, BaseDataFrame)
    """
    Problem
    TODO: Incompatible rules are allowed through. I do not know where the KeyError is coming from, but it is not caught by the function.
    """

#Dimension tests
dimensions_one = ["DIM_1", "DIM_2"]

dimensions_two = ["DIM_1", "DIM_2", "DIM_3", "DIM_4"]

dimensions_three = [None, None, None]

dimensions_four = [1, 2, 3]


def test_bad_dimensions_one():
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions_one)
    print(result.materialize())
    assert isinstance(result, BaseDataFrame)
    #Doesn't seem to be a difference when the dimensions are not in the rules

def test_bad_dimensions_two():
    result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions_two)
    print(result.materialize())
    assert isinstance(result, BaseDataFrame)
    #Prints out the missing DIM_4 but doesn't seem to change anything

def test_bad_dimensions_three():
    with pytest.raises(TypeError):
        result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions_three)
        print(result.materialize())

    #Raises error when translating the dimension, might be worth doing independent validation

def test_bad_dimensions_four():
    with pytest.raises(TypeError):
        result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions_four)
        print(result.materialize())
    #Same as above


#Breaking the context class
@dataclass
class context_one:
    None

class context_two:
    def __init__(self, rule_name, DIM_1, DIM_2, DIM_3):
        self.rule_name = rule_name
        self.DIM_1 = DIM_1
        self.DIM_2 = DIM_2
        self.DIM_3 = DIM_3



CONTEXT_EMPTY = context_one()

CONTEXT_NON_DATACLASS = context_two(rule_name="rule_1", DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)

def test_context_empty():
    with pytest.raises(Exception):
        result = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT_EMPTY, rules=df_rules, dimensions=dimensions)
    #Catches and raises error with the correct exception message

def test_context_non_dataclass():
    result1 = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT_NON_DATACLASS, rules=df_rules, dimensions=dimensions)
    print(result1.materialize())

    result2 = RulesEngine.apply_context_rules_engine(CONTEXT=CONTEXT, rules=df_rules, dimensions=dimensions)
    print(result2.materialize())

    assert result1.count() == result2.count()
    assert isinstance(result1, BaseDataFrame)
    assert isinstance(result2, BaseDataFrame)
    assert result1.get_first_row_as_dict() == result2.get_first_row_as_dict()
    #I may have just created a dataclass but if not then it allows for the context to be a non @dataclass class. returns same as a @dataclass class with same values


#I am not really sure how to test "if not instance(rules, BaseDataFrame):" line 
