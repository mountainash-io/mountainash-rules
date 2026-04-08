"""Integration tests: end-to-end scenarios with real-world rule patterns."""

from __future__ import annotations

import pytest
from mountainash.relations import relation

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine

from tests.conftest import (
    LIST_CAPABLE_BACKENDS,
    build_backend_df,
)


def _rows(df) -> dict:
    return relation(df).to_dict()


# ---------------------------------------------------------------------------
# Pricing carve-out
# ---------------------------------------------------------------------------

class TestPricingCarveOut:
    """Pricing hierarchy: general rate -> client-specific -> product-specific override."""

    @pytest.fixture
    def pricing_engine(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["base_rate", "client_au", "client_au_premium"],
            "rate": [0.10, 0.08, 0.05],
            "client_region": [UNKNOWN, "AU", "AU"],
            "product": [UNKNOWN, UNKNOWN, "premium"],
        }, table_name="pricing")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="client_region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)

    def test_specific_override_wins(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "client_au_premium"
        assert best["rate"][0] == 0.05

    def test_fallback_to_client_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "standard"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "client_au"
        assert best["rate"][0] == 0.08

    def test_fallback_to_base_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "UK", "product": "standard"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "base_rate"
        assert best["rate"][0] == 0.10

    def test_hierarchy_preserved_in_ranking(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        rows = _rows(result.survivors)
        pairs = sorted(zip(rows["__rank"], rows["rule_name"]))
        assert [name for _, name in pairs] == ["client_au_premium", "client_au", "base_rate"]


# ---------------------------------------------------------------------------
# Entity pool
# ---------------------------------------------------------------------------

class TestEntityPool:
    """Entity pool with range-based and regex rules for increasing specificity.

    REGEX uses literal pattern on Dimension metadata (not a per-rule column).
    """

    @pytest.fixture
    def pool_engine(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["catch_all", "mid_tier", "high_value_au"],
            "pool": ["default", "tier_b", "tier_a"],
            "region": [UNKNOWN, UNKNOWN, "AU"],
            "value_min": [UNKNOWN_NUMERIC, 1000, 5000],
            "value_max": [UNKNOWN_NUMERIC, 9999, 99999],
        }, table_name="pool")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(
                dimension_name="value",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field="value_min",
                range_max_field="value_max",
            ),
            Dimension(
                dimension_name="code_pattern",
                match_strategy=MatchStrategy.REGEX,
                data_type=str,
                regex_pattern="^T.*",
            ),
        ])
        return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)

    def test_most_specific_wins(self, pool_engine):
        # code_pattern "TXN-001" matches ^T.* → +1 for all rules
        # catch_all: 0+0+1=1, mid_tier: 0+1+1=2, high_value_au: 1+1+1=3
        result = pool_engine.evaluate(context={"region": "AU", "value": 7500, "code_pattern": "TXN-001"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "high_value_au"
        assert best["__specificity"][0] == 3

    def test_mid_tier_fallback(self, pool_engine):
        # high_value_au eliminated on region; mid_tier survives with specificity 2
        result = pool_engine.evaluate(context={"region": "UK", "value": 5000, "code_pattern": "TXN-001"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "mid_tier"
        assert best["__specificity"][0] == 2

    def test_no_match_when_regex_fails(self, pool_engine):
        # code_pattern "ABC-001" fails ^T.* → -1 for every rule → all eliminated
        result = pool_engine.evaluate(context={"region": "UK", "value": 500, "code_pattern": "ABC-001"})
        assert result.count == 0


# ---------------------------------------------------------------------------
# No match
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_all_rules_eliminated(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["au_only", "us_only"],
            "region": ["AU", "US"],
        }, table_name="no_match_rules")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "UK"})
        assert result.count == 0


# ---------------------------------------------------------------------------
# Tie handling
# ---------------------------------------------------------------------------

class TestTieHandling:
    def test_same_specificity_both_survive(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
            "product": ["premium", "standard"],
        }, table_name="tie_rules_a")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})
        assert result.count == 1
        assert _rows(result.best_match)["rule_name"][0] == "rule_a"

    def test_equal_specificity_both_returned(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
        }, table_name="tie_rules_b")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 2


# ---------------------------------------------------------------------------
# Explain integration
# ---------------------------------------------------------------------------

class TestExplainIntegration:
    def test_explain_shows_dimension_breakdown(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["specific", "general"],
            "region": ["AU", UNKNOWN],
            "product": ["premium", UNKNOWN],
        }, table_name="explain_rules")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})

        assert result.explain("specific") == {"region": 1, "product": 1}
        assert result.explain("general") == {"region": 0, "product": 0}


# ---------------------------------------------------------------------------
# Mixed strategy (includes SET_MEMBERSHIP) — requires list-capable backends
# ---------------------------------------------------------------------------

def _fraud_rules_data() -> dict:
    return {
        "rule_name": ["catch_all", "high_value", "blacklist_merchant", "specific_txn"],
        "action": ["allow", "review", "block", "block"],
        "merchant_type": [UNKNOWN, UNKNOWN, "CASINO", "RETAIL"],
        "allowed_countries": [
            ["AU", "NZ", "US", "UK"],
            ["AU", "NZ", "US", "UK"],
            ["AU", "NZ", "US", "UK"],
            ["AU"],
        ],
        "amount_threshold": [UNKNOWN_NUMERIC, 10000, UNKNOWN_NUMERIC, 500],
        "code_prefix": [UNKNOWN, UNKNOWN, UNKNOWN, "TXN-"],
    }


def _fraud_metadata() -> DimensionsMetadata:
    return DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="merchant_type",
            match_strategy=MatchStrategy.EXACT,
            data_type=str,
        ),
        Dimension(
            dimension_name="country",
            context_field="country",
            rule_field="allowed_countries",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        ),
        Dimension(
            dimension_name="amount",
            context_field="amount",
            rule_field="amount_threshold",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        ),
        Dimension(
            dimension_name="code",
            context_field="code",
            rule_field="code_prefix",
            match_strategy=MatchStrategy.PREFIX,
            data_type=str,
        ),
    ])


@pytest.mark.parametrize("list_backend", LIST_CAPABLE_BACKENDS)
class TestMixedStrategyFraudDetection:
    """Exercises EXACT, SET_MEMBERSHIP, GREATER_THAN, and PREFIX together.

    SET_MEMBERSHIP now compiles cleanly on every list-capable backend via
    mountainash.expressions `t_is_in` / `t_is_not_in`, which accept list
    column references polymorphically (mountainash-expressions#75).
    """

    @pytest.fixture
    def fraud_engine(self, list_backend):
        rules = build_backend_df(
            list_backend, _fraud_rules_data(), table_name="fraud_rules"
        )
        return ExpressionRulesEngine(rules=rules, dimension_metadata=_fraud_metadata())

    def test_high_value_review(self, fraud_engine):
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "US",
            "amount": 15000,
            "code": "TXN-999",
        })
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "high_value"
        assert best["action"][0] == "review"

    def test_blacklist_merchant_blocks(self, fraud_engine):
        result = fraud_engine.evaluate(context={
            "merchant_type": "CASINO",
            "country": "AU",
            "amount": 100,
            "code": "TXN-001",
        })
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "blacklist_merchant"
        assert best["action"][0] == "block"

    def test_specific_txn_most_specific(self, fraud_engine):
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "AU",
            "amount": 1000,
            "code": "TXN-001",
        })
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "specific_txn"
        assert best["__specificity"][0] == 4
