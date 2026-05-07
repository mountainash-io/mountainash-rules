"""Tests for RuleResult — parametrized across all backends."""

from __future__ import annotations

import pytest
from mountainash.relations import relation

from mountainash_rules.result import RuleResult

from tests.conftest import build_backend_df


@pytest.fixture
def sample_result_data() -> dict[str, list]:
    return {
        "rule_name": ["specific", "general", "mid"],
        "rate": [0.05, 0.10, 0.07],
        "__t_region": [1, 0, 1],
        "__t_product": [1, 0, 0],
        "__t_tier": [1, 1, 1],
        "__specificity": [3, 1, 2],
        "__rank": [1, 3, 2],
    }


@pytest.fixture
def result(backend_name, sample_result_data) -> RuleResult:
    df = build_backend_df(backend_name, sample_result_data, table_name="result")
    return RuleResult(dataframe=df, active_dimensions=["region", "product", "tier"])


def _rows(df) -> dict:
    return relation(df).to_dict()


class TestSurvivors:
    def test_survivors_returns_all_rows(self, result):
        assert result.count == 3

    def test_survivors_has_expected_shape(self, result):
        rows = _rows(result.survivors)
        assert len(rows["rule_name"]) == 3


class TestBestMatch:
    def test_best_match_returns_first_row(self, result):
        best = _rows(result.best_match)
        assert len(best["rule_name"]) == 1
        assert best["rule_name"][0] == "specific"
        assert best["__specificity"][0] == 3


class TestExplain:
    def test_explain_returns_per_dimension_values(self, result):
        assert result.explain("specific") == {"region": 1, "product": 1, "tier": 1}

    def test_explain_general_rule(self, result):
        assert result.explain("general") == {"region": 0, "product": 0, "tier": 1}

    def test_explain_missing_rule_raises(self, result):
        with pytest.raises(KeyError):
            result.explain("nonexistent")


class TestAtLeast:
    def test_at_least_filters_by_specificity(self, result):
        filtered = _rows(result.at_least(2))
        assert len(filtered["rule_name"]) == 2
        assert set(filtered["rule_name"]) == {"specific", "mid"}

    def test_at_least_zero_returns_all(self, result):
        assert len(_rows(result.at_least(0))["rule_name"]) == 3

    def test_at_least_high_returns_none(self, result):
        assert len(_rows(result.at_least(10)).get("rule_name", [])) == 0
