"""Tests for ExpressionRulesEngine."""

import polars as pl
import pytest

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine
from mountainash_utils_rules.result import RuleResult


@pytest.fixture
def rules_df():
    """Rules with 3 dimensions: region (EXACT), amount (RANGE), code (REGEX)."""
    return pl.DataFrame({
        "rule_name": ["specific", "general", "mid", "no_match"],
        "region": ["AU", UNKNOWN, "AU", "US"],
        "amount_min": [0, UNKNOWN_NUMERIC, 0, 0],
        "amount_max": [100, UNKNOWN_NUMERIC, 100, 100],
        "code": ["^PRE.*", UNKNOWN, UNKNOWN, "^PRE.*"],
    })


@pytest.fixture
def metadata():
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        ),
        Dimension(dimension_name="code", match_strategy=MatchStrategy.REGEX, data_type=str),
    ])


@pytest.fixture
def engine(rules_df, metadata):
    return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)


class TestSurvival:
    def test_non_matching_rules_eliminated(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = result.survivors["rule_name"].to_list()
        assert "no_match" not in names  # region=US doesn't match AU

    def test_matching_rules_survive(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = result.survivors["rule_name"].to_list()
        assert "specific" in names
        assert "general" in names
        assert "mid" in names


class TestSpecificity:
    def test_specific_rule_ranks_first(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        best = result.best_match
        assert best["rule_name"][0] == "specific"

    def test_specificity_values(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        df = result.survivors
        # specific: all 3 hard matches → specificity=3
        specific_row = df.filter(pl.col("rule_name") == "specific")
        assert specific_row["__specificity"][0] == 3

        # general: all unknown → specificity=0
        general_row = df.filter(pl.col("rule_name") == "general")
        assert general_row["__specificity"][0] == 0

        # mid: region match + amount match + unknown code → specificity=2
        mid_row = df.filter(pl.col("rule_name") == "mid")
        assert mid_row["__specificity"][0] == 2


class TestRanking:
    def test_rank_order(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        df = result.survivors
        names_in_order = df.sort("__rank")["rule_name"].to_list()
        assert names_in_order == ["specific", "mid", "general"]


class TestEmptyResult:
    def test_no_survivors(self):
        rules_df = pl.DataFrame({
            "rule_name": ["only_us"],
            "region": ["US"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 0
