"""Integration tests: end-to-end scenarios with real-world rule patterns."""

import polars as pl
import pytest

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine


class TestPricingCarveOut:
    """Pricing hierarchy: general rate -> client-specific -> product-specific override."""

    @pytest.fixture
    def pricing_engine(self):
        rules_df = pl.DataFrame({
            "rule_name": ["base_rate", "client_au", "client_au_premium"],
            "rate": [0.10, 0.08, 0.05],
            "client_region": [UNKNOWN, "AU", "AU"],
            "product": [UNKNOWN, UNKNOWN, "premium"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="client_region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)

    def test_specific_override_wins(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        best = result.best_match
        assert best["rule_name"][0] == "client_au_premium"
        assert best["rate"][0] == 0.05

    def test_fallback_to_client_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "standard"})
        best = result.best_match
        assert best["rule_name"][0] == "client_au"
        assert best["rate"][0] == 0.08

    def test_fallback_to_base_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "UK", "product": "standard"})
        best = result.best_match
        assert best["rule_name"][0] == "base_rate"
        assert best["rate"][0] == 0.10

    def test_hierarchy_preserved_in_ranking(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        names = result.survivors.sort("__rank")["rule_name"].to_list()
        assert names == ["client_au_premium", "client_au", "base_rate"]


class TestEntityPool:
    """Entity pool with range-based and regex rules for increasing specificity."""

    @pytest.fixture
    def pool_engine(self):
        rules_df = pl.DataFrame({
            "rule_name": ["catch_all", "mid_tier", "high_value_au"],
            "pool": ["default", "tier_b", "tier_a"],
            "region": [UNKNOWN, UNKNOWN, "AU"],
            "value_min": [UNKNOWN_NUMERIC, 1000, 5000],
            "value_max": [UNKNOWN_NUMERIC, 9999, 99999],
            "code_pattern": [UNKNOWN, "^T.*", "^T.*"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(
                dimension_name="value",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field="value_min",
                range_max_field="value_max",
            ),
            Dimension(dimension_name="code_pattern", match_strategy=MatchStrategy.REGEX, data_type=str),
        ])
        return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)

    def test_most_specific_wins(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "AU", "value": 7500, "code_pattern": "TXN-001"})
        assert result.best_match["rule_name"][0] == "high_value_au"

    def test_mid_tier_fallback(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "UK", "value": 5000, "code_pattern": "TXN-001"})
        assert result.best_match["rule_name"][0] == "mid_tier"

    def test_catch_all_fallback(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "UK", "value": 500, "code_pattern": "ABC-001"})
        assert result.best_match["rule_name"][0] == "catch_all"


class TestNoMatch:
    def test_all_rules_eliminated(self):
        rules_df = pl.DataFrame({
            "rule_name": ["au_only", "us_only"],
            "region": ["AU", "US"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "UK"})
        assert result.count == 0


class TestTieHandling:
    def test_same_specificity_both_survive(self):
        rules_df = pl.DataFrame({
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
            "product": ["premium", "standard"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})
        # rule_a matches both, rule_b fails on product
        assert result.count == 1
        assert result.best_match["rule_name"][0] == "rule_a"

    def test_equal_specificity_both_returned(self):
        rules_df = pl.DataFrame({
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 2


class TestExplainIntegration:
    def test_explain_shows_dimension_breakdown(self):
        rules_df = pl.DataFrame({
            "rule_name": ["specific", "general"],
            "region": ["AU", UNKNOWN],
            "product": ["premium", UNKNOWN],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})

        assert result.explain("specific") == {"region": 1, "product": 1}
        assert result.explain("general") == {"region": 0, "product": 0}


class TestMixedStrategyFraudDetection:
    """Exercises EXACT, SET_MEMBERSHIP, GREATER_THAN, and PREFIX together."""

    @pytest.fixture
    def fraud_engine(self):
        rules_df = pl.DataFrame({
            "rule_name": ["catch_all", "high_value", "blacklist_merchant", "specific_txn"],
            "action": ["allow", "review", "block", "block"],
            "merchant_type": [UNKNOWN, UNKNOWN, "CASINO", "RETAIL"],
            "allowed_countries": pl.Series(
                "allowed_countries",
                [
                    ["AU", "NZ", "US", "UK"],
                    ["AU", "NZ", "US", "UK"],
                    ["AU", "NZ", "US", "UK"],
                    ["AU"],
                ],
                dtype=pl.List(pl.Utf8),
            ),
            "amount_threshold": [UNKNOWN_NUMERIC, 10000, UNKNOWN_NUMERIC, 500],
            "code_prefix": [UNKNOWN, UNKNOWN, UNKNOWN, "TXN-"],
        })
        metadata = DimensionsMetadata(dimensions=[
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
        return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)

    def test_high_value_review(self, fraud_engine):
        """High-value US transaction → high_value rule triggers review."""
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "US",
            "amount": 15000,
            "code": "TXN-999",
        })
        assert result.best_match["rule_name"][0] == "high_value"
        assert result.best_match["action"][0] == "review"

    def test_blacklist_merchant_blocks(self, fraud_engine):
        """Casino merchant in allowed country → blacklist blocks."""
        result = fraud_engine.evaluate(context={
            "merchant_type": "CASINO",
            "country": "AU",
            "amount": 100,
            "code": "TXN-001",
        })
        assert result.best_match["rule_name"][0] == "blacklist_merchant"
        assert result.best_match["action"][0] == "block"

    def test_specific_txn_most_specific(self, fraud_engine):
        """Retail, AU, 1000, TXN-001 matches specific_txn (highest specificity)."""
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "AU",
            "amount": 1000,
            "code": "TXN-001",
        })
        assert result.best_match["rule_name"][0] == "specific_txn"
        assert result.best_match["__specificity"][0] == 4
