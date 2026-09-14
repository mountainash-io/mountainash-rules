"""Tests for ExpressionRulesEngine.explain — all-rules ternary detail."""

import polars as pl
import pytest

from mountainash_rules import (
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    ExplainResult,
    MatchStrategy,
    UNKNOWN,
)


@pytest.fixture
def engine():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="region",
                match_strategy=MatchStrategy.EXACT,
                data_type="str",
            ),
            Dimension(
                dimension_name="channel",
                match_strategy=MatchStrategy.EXACT,
                data_type="str",
            ),
        ]
    )
    rules = pl.DataFrame(
        {
            "rule_name": ["both_match", "one_miss", "wildcard"],
            "region": ["AU", "AU", UNKNOWN],
            "channel": ["BROKER", "DIRECT", UNKNOWN],
        }
    )
    return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)


class TestExplain:
    def test_returns_every_rule(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        assert isinstance(result, ExplainResult)
        assert result.count == 3  # non-survivors included

    def test_non_survivor_names_the_failing_dimension(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        rows = {r["rule_name"]: r for r in result.frame.to_dicts()}
        miss = rows["one_miss"]
        assert miss["__survived"] is False
        assert miss["__t_region"] == 1  # matched
        assert miss["__t_channel"] == -1  # the reason it failed

    def test_wildcard_rows_report_zero_ternary(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        rows = {r["rule_name"]: r for r in result.frame.to_dicts()}
        assert rows["wildcard"]["__t_region"] == 0
        assert rows["wildcard"]["__survived"] is True
        assert rows["wildcard"]["__specificity"] == 0

    def test_survivor_accessors_split_correctly(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        survivor_names = {r["rule_name"] for r in result.survivors.to_dicts()}
        non_survivor_names = {r["rule_name"] for r in result.non_survivors.to_dicts()}
        assert survivor_names == {"both_match", "wildcard"}
        assert non_survivor_names == {"one_miss"}

    def test_no_rank_and_no_ctx_columns(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        cols = result.frame.columns
        assert "__rank" not in cols
        assert not any(c.startswith("__ctx_") for c in cols)
        assert "__rule_index" in cols  # kept: stable rule identity

    def test_agrees_with_evaluate_survivor_set(self, engine):
        ctx = {"region": "AU", "channel": "BROKER"}
        eval_names = {r["rule_name"] for r in engine.evaluate(ctx).survivors.to_dicts()}
        explain_names = {
            r["rule_name"] for r in engine.explain(ctx).survivors.to_dicts()
        }
        assert explain_names == eval_names

    def test_unknown_dimension_raises(self, engine):
        with pytest.raises(KeyError):
            engine.explain({"region": "AU"}, dimensions=["nope"])
