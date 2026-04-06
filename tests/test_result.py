"""Tests for RuleResult."""

import polars as pl
import pytest

from mountainash_utils_rules.result import RuleResult


@pytest.fixture
def sample_result_df():
    """A pre-evaluated result DataFrame as the engine would produce."""
    return pl.DataFrame({
        "rule_name": ["specific", "general", "mid"],
        "rate": [0.05, 0.10, 0.07],
        "__t_region": [1, 0, 1],
        "__t_product": [1, 0, 0],
        "__t_tier": [1, 1, 1],
        "__specificity": [3, 1, 2],
        "__rank": [1, 3, 2],
    })


@pytest.fixture
def result(sample_result_df):
    return RuleResult(
        dataframe=sample_result_df,
        active_dimensions=["region", "product", "tier"],
    )


class TestSurvivors:
    def test_survivors_returns_all_rows(self, result):
        assert result.count == 3

    def test_survivors_is_the_dataframe(self, result):
        assert result.survivors.shape[0] == 3


class TestBestMatch:
    def test_best_match_returns_first_row(self, result):
        best = result.best_match
        assert best.shape[0] == 1
        assert best["rule_name"][0] == "specific"
        assert best["__specificity"][0] == 3


class TestExplain:
    def test_explain_returns_per_dimension_values(self, result):
        explanation = result.explain("specific")
        assert explanation == {"region": 1, "product": 1, "tier": 1}

    def test_explain_general_rule(self, result):
        explanation = result.explain("general")
        assert explanation == {"region": 0, "product": 0, "tier": 1}

    def test_explain_missing_rule_raises(self, result):
        with pytest.raises(KeyError):
            result.explain("nonexistent")


class TestAtLeast:
    def test_at_least_filters_by_specificity(self, result):
        filtered = result.at_least(2)
        assert filtered.shape[0] == 2
        assert set(filtered["rule_name"].to_list()) == {"specific", "mid"}

    def test_at_least_zero_returns_all(self, result):
        assert result.at_least(0).shape[0] == 3

    def test_at_least_high_returns_none(self, result):
        assert result.at_least(10).shape[0] == 0
