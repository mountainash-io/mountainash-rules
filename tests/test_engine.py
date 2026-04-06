"""Tests for ExpressionRulesEngine."""

import mountainash.expressions as ma
import polars as pl
import pytest

from mountainash_utils_rules.constants import CTX_PREFIX, UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
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


class TestTopN:
    def test_top_n_limits_results(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=2,
        )
        assert result.count == 2
        # Should be the top 2 by specificity
        assert result.survivors["rule_name"][0] == "specific"

    def test_top_n_larger_than_survivors(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=100,
        )
        assert result.count == 3  # only 3 survivors exist


class TestMinSpecificity:
    def test_min_specificity_filters(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            min_specificity=2,
        )
        names = result.survivors["rule_name"].to_list()
        assert "specific" in names
        assert "mid" in names
        assert "general" not in names  # specificity=0


class TestDimensionsSubset:
    def test_subset_dimensions(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            dimensions=["region"],
        )
        # Only evaluating region: specific(AU), general(unknown), mid(AU) survive
        # no_match(US) eliminated
        assert result.count == 3
        assert "no_match" not in result.survivors["rule_name"].to_list()

    def test_invalid_dimension_raises(self, engine):
        with pytest.raises(KeyError, match="nonexistent"):
            engine.evaluate(
                context={"region": "AU"},
                dimensions=["nonexistent"],
            )


class TestObservability:
    def test_observability_columns_present_by_default(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        cols = result.survivors.columns
        assert "__t_region" in cols
        assert "__t_amount" in cols
        assert "__t_code" in cols

    def test_observability_columns_absent_when_disabled(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            include_observability=False,
        )
        cols = result.survivors.columns
        assert "__t_region" not in cols
        assert "__t_amount" not in cols
        assert "__t_code" not in cols
        # __specificity and __rank should still be present
        assert "__specificity" in cols
        assert "__rank" in cols


class TestCustomExpressions:
    def test_custom_expression_exact(self):
        rules_df = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "region": ["AU", "US"],
        })

        engine = ExpressionRulesEngine(
            rules=rules_df,
            dimension_expressions={
                "region": ma.t_col("region", unknown={UNKNOWN}).t_eq(
                    ma.t_col(f"{CTX_PREFIX}region", unknown={UNKNOWN})
                ),
            },
        )

        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 1
        assert result.best_match["rule_name"][0] == "r1"

    def test_cannot_provide_both_metadata_and_expressions(self):
        with pytest.raises(ValueError, match="not both"):
            ExpressionRulesEngine(
                rules=pl.DataFrame({"rule_name": ["r1"]}),
                dimension_metadata=DimensionsMetadata(dimensions=[
                    Dimension(dimension_name="x", match_strategy=MatchStrategy.EXACT, data_type=str),
                ]),
                dimension_expressions={"x": ma.col("x")},
            )

    def test_must_provide_one_of_metadata_or_expressions(self):
        with pytest.raises(ValueError, match="Must provide"):
            ExpressionRulesEngine(
                rules=pl.DataFrame({"rule_name": ["r1"]}),
            )
