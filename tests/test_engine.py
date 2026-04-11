"""Tests for ExpressionRulesEngine — parametrized across all backends."""

from __future__ import annotations

import mountainash.expressions as ma
import pytest
from mountainash.relations import relation

from mountainash_utils_rules.constants import CTX_PREFIX, UNKNOWN, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine

from .conftest import build_backend_df


def _rows(df) -> dict:
    """Backend-agnostic read — returns column -> list[values]."""
    return relation(df).to_dict()


# ---------------------------------------------------------------------------
# Survival + matching
# ---------------------------------------------------------------------------

class TestSurvival:
    def test_non_matching_rules_eliminated(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = _rows(result.survivors)["rule_name"]
        assert "no_match" not in names

    def test_matching_rules_survive(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = _rows(result.survivors)["rule_name"]
        assert "specific" in names
        assert "general" in names
        assert "mid" in names


# ---------------------------------------------------------------------------
# Specificity
# ---------------------------------------------------------------------------

class TestSpecificity:
    def test_specific_rule_ranks_first(self, basic_engine):
        # With REGEX as a context validator, "specific" and "mid" tie at
        # specificity 3 — either is acceptable as the top match.
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] in ("specific", "mid")

    def test_specificity_values(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        rows = _rows(result.survivors)
        name_to_spec = dict(zip(rows["rule_name"], rows["__specificity"]))
        # REGEX dimension (code, pattern "^PRE.*") is context-driven: with
        # context code="PRE-001" it contributes +1 to every surviving rule.
        assert name_to_spec["specific"] == 3  # region + amount + code
        assert name_to_spec["general"] == 1   # code only (region/amount unknown)
        assert name_to_spec["mid"] == 3       # region + amount + code


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def test_rank_order(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        rows = _rows(result.survivors)
        pairs = sorted(zip(rows["__rank"], rows["rule_name"]))
        names_in_order = [name for _, name in pairs]
        # "specific" and "mid" both have specificity 3 — order between them
        # is not guaranteed. "general" (specificity 1) must come last.
        assert set(names_in_order[:2]) == {"specific", "mid"}
        assert names_in_order[2] == "general"


# ---------------------------------------------------------------------------
# Empty result
# ---------------------------------------------------------------------------

class TestEmptyResult:
    def test_no_survivors(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["only_us"],
            "region": ["US"],
        }, table_name="empty_rules")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 0


# ---------------------------------------------------------------------------
# top_n / min_specificity / subset
# ---------------------------------------------------------------------------

class TestTopN:
    def test_top_n_limits_results(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=2,
        )
        assert result.count == 2
        rows = _rows(result.survivors)
        pairs = sorted(zip(rows["__rank"], rows["rule_name"]))
        # Top 2 are the spec-3 ties: specific and mid (in either order).
        assert {pairs[0][1], pairs[1][1]} == {"specific", "mid"}

    def test_top_n_larger_than_survivors(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=100,
        )
        assert result.count == 3


class TestMinSpecificity:
    def test_min_specificity_filters(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            min_specificity=2,
        )
        names = _rows(result.survivors)["rule_name"]
        assert "specific" in names
        assert "mid" in names
        assert "general" not in names


class TestDimensionsSubset:
    def test_subset_dimensions(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            dimensions=["region"],
        )
        assert result.count == 3
        assert "no_match" not in _rows(result.survivors)["rule_name"]

    def test_invalid_dimension_raises(self, basic_engine):
        with pytest.raises(KeyError, match="nonexistent"):
            basic_engine.evaluate(
                context={"region": "AU"},
                dimensions=["nonexistent"],
            )


# ---------------------------------------------------------------------------
# Observability toggle
# ---------------------------------------------------------------------------

class TestObservability:
    def test_observability_columns_present_by_default(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        cols = set(_rows(result.survivors).keys())
        assert "__t_region" in cols
        assert "__t_amount" in cols
        assert "__t_code" in cols

    def test_observability_columns_absent_when_disabled(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            include_observability=False,
        )
        cols = set(_rows(result.survivors).keys())
        assert "__t_region" not in cols
        assert "__t_amount" not in cols
        assert "__t_code" not in cols
        assert "__specificity" in cols
        assert "__rank" in cols


# ---------------------------------------------------------------------------
# Custom expressions
# ---------------------------------------------------------------------------

class TestCustomExpressions:
    def test_custom_expression_exact(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["r1", "r2"],
            "region": ["AU", "US"],
        }, table_name="custom_rules")

        engine = ExpressionRulesEngine(
            rules=rules,
            dimension_expressions={
                "region": ma.t_col("region", unknown={UNKNOWN}).t_eq(
                    ma.t_col(f"{CTX_PREFIX}region", unknown={UNKNOWN})
                ),
            },
        )

        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 1
        assert _rows(result.best_match)["rule_name"][0] == "r1"

    def test_cannot_provide_both_metadata_and_expressions(self, backend_name):
        rules = build_backend_df(backend_name, {"rule_name": ["r1"]}, table_name="two_ways")
        with pytest.raises(ValueError, match="not both"):
            ExpressionRulesEngine(
                rules=rules,
                dimension_metadata=DimensionsMetadata(dimensions=[
                    Dimension(dimension_name="x", match_strategy=MatchStrategy.EXACT, data_type=str),
                ]),
                dimension_expressions={"x": ma.col("x")},
            )

    def test_must_provide_one_of_metadata_or_expressions(self, backend_name):
        rules = build_backend_df(backend_name, {"rule_name": ["r1"]}, table_name="neither")
        with pytest.raises(ValueError, match="Must provide"):
            ExpressionRulesEngine(rules=rules)
