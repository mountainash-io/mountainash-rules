import pytest
from mountainash_utils_rules.rule_manager import RuleManager
from mountainash_data import BaseDataFrame, IbisDataFrame
from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags
import polars as pl
import sqlite3

@pytest.fixture
def sample_rules():
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", "C"],
        "DIM_2": ["1", "2", "3"],
        "DIM_3": ["X", RuleConstants.UNKNOWN, RuleConstants.UNKNOWN]
    })
    return IbisDataFrame(rules_df, ibis_backend_schema="sqlite")

def test_rule_manager_initialization(sample_rules):
    rule_manager = RuleManager(sample_rules)
    assert isinstance(rule_manager.rules, BaseDataFrame)
    assert rule_manager.rules.count() == 3

def test_get_rules(sample_rules):
    rule_manager = RuleManager(sample_rules)
    rules = rule_manager.get_rules()
    assert isinstance(rules, BaseDataFrame)
    assert rules.count() == 3

def test_update_rules(sample_rules):
    rule_manager = RuleManager(sample_rules)
    
    new_rules_df = pl.DataFrame({
        "rule_name": ["rule_4", "rule_5"],
        "DIM_1": ["D", "E"],
        "DIM_2": ["4", "5"],
        "DIM_3": ["Y", "Z"]
    })
    new_rules = IbisDataFrame(new_rules_df, ibis_backend_schema="sqlite")
    
    rule_manager.update_rules(new_rules)
    assert rule_manager.rules.count() == 2

def test_init_rules_with_invalid_input():
    with pytest.raises(ValueError):
        RuleManager(None)
    
    with pytest.raises(ValueError):
        RuleManager("not a BaseDataFrame")

def test_init_rules_with_empty_dataframe():
    with pytest.raises(sqlite3.OperationalError):
        empty_df = IbisDataFrame(pl.DataFrame(), ibis_backend_schema="sqlite")
        RuleManager(empty_df)