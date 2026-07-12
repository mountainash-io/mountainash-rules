"""Tests for hit policies, priority, and deterministic tie-breaking."""

import polars as pl
import pytest

from mountainash_rules.constants import HitPolicy, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine


class TestHitPolicyEnum:
    def test_values(self):
        assert HitPolicy("rule_order") is HitPolicy.RULE_ORDER
        assert HitPolicy.COLLECT.value == "collect"


class TestMetadataFields:
    def test_defaults(self):
        md = DimensionsMetadata(dimensions=[Dimension(dimension_name="x")])
        assert md.hit_policy is HitPolicy.COLLECT
        assert md.priority_field is None
        assert md.output_fields == []

    def test_priority_requires_field(self):
        with pytest.raises(ValueError, match="priority_field"):
            DimensionsMetadata(
                dimensions=[Dimension(dimension_name="x")],
                hit_policy=HitPolicy.PRIORITY,
            )


from mountainash_rules.hit_policy import (
    HitPolicyViolationError,
    SelectionInfo,
    default_output_fields,
    ordering_keys,
)


class TestOrderingKeys:
    def test_collect(self):
        assert ordering_keys(HitPolicy.COLLECT, None) == [
            ("__specificity", True), ("__rule_index", False),
        ]

    def test_first_and_rule_order_ignore_specificity(self):
        assert ordering_keys(HitPolicy.FIRST, None) == [("__rule_index", False)]
        assert ordering_keys(HitPolicy.RULE_ORDER, None) == [("__rule_index", False)]

    def test_priority(self):
        assert ordering_keys(HitPolicy.PRIORITY, "salience") == [
            ("salience", True), ("__specificity", True), ("__rule_index", False),
        ]


class TestDefaultOutputFields:
    def test_excludes_rule_condition_and_internal_columns(self):
        info = SelectionInfo(
            dimension_rule_fields=("region", "amt_min", "amt_max"),
            priority_field="salience",
            output_fields=(),
            truncated=False,
            observability=True,
        )
        cols = ["rule_name", "region", "amt_min", "amt_max", "salience",
                "price", "code", "__rank", "__specificity", "__rule_index",
                "__t_region"]
        assert default_output_fields(cols, info) == ["price", "code"]

    def test_explicit_output_fields_win(self):
        info = SelectionInfo((), None, ("price",), False, True)
        assert default_output_fields(["price", "code"], info) == ["price"]


def _region_md(**kwargs):
    return DimensionsMetadata(
        dimensions=[Dimension(dimension_name="region")], **kwargs
    )


class TestDeterministicTieBreak:
    def test_collect_ties_broken_by_rule_order(self):
        rules = pl.DataFrame({
            "rule_name": ["r_late", "r_early"],
            "region": ["AU", "AU"],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        result = engine.evaluate({"region": "AU"})
        rows = result.survivors.to_dicts()
        assert [r["rule_name"] for r in rows] == ["r_late", "r_early"]
        assert [r["__rule_index"] for r in rows] == [0, 1]
        assert [r["__rank"] for r in rows] == [1, 2]


class TestReservedColumns:
    def test_reserved_column_in_rules_raises(self):
        rules = pl.DataFrame({
            "rule_name": ["r"], "region": ["AU"], "__rank": [9],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        with pytest.raises(ValueError, match="__rank"):
            engine.evaluate({"region": "AU"})


class TestPolicySemantics:
    def _rules(self):
        return pl.DataFrame({
            "rule_name": ["generic", "specific"],
            "region": ["<NA>", "AU"],      # generic is a wildcard
            "salience": [10, 1],
            "price": [1.0, 2.0],
        })

    def test_first_ignores_specificity(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        result = engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.FIRST)
        assert result.count == 1
        assert result.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_priority_beats_specificity(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        result = engine.evaluate(
            {"region": "AU"},
            hit_policy=HitPolicy.PRIORITY, priority_field="salience",
        )
        assert result.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_unique_violation(self):
        from mountainash_rules.hit_policy import HitPolicyViolationError
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        with pytest.raises(HitPolicyViolationError) as exc_info:
            engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
        assert exc_info.value.policy is HitPolicy.UNIQUE
        assert len(exc_info.value.offending) == 2

    def test_unique_zero_survivors_passes(self):
        engine = ExpressionRulesEngine(
            rules=pl.DataFrame({"rule_name": ["r"], "region": ["NZ"]}),
            dimension_metadata=_region_md(),
        )
        result = engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
        assert result.count == 0

    def test_any_agreeing_outputs_returns_one(self):
        rules = pl.DataFrame({
            "rule_name": ["a", "b"], "region": ["AU", "<NA>"], "price": [5.0, 5.0],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        result = engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY)
        assert result.count == 1

    def test_any_disagreeing_outputs_raises(self):
        from mountainash_rules.hit_policy import HitPolicyViolationError
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        with pytest.raises(HitPolicyViolationError):
            engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY)

    def test_metadata_policy_applies_and_override_wins(self):
        md = _region_md(hit_policy=HitPolicy.FIRST)
        engine = ExpressionRulesEngine(rules=self._rules(), dimension_metadata=md)
        assert engine.evaluate({"region": "AU"}).count == 1          # FIRST from metadata
        assert engine.evaluate(
            {"region": "AU"}, hit_policy=HitPolicy.COLLECT
        ).count == 2                                                  # override


class TestResultSelect:
    def _collect_result(self, **eval_kwargs):
        rules = pl.DataFrame({
            "rule_name": ["generic", "specific"],
            "region": ["<NA>", "AU"],
            "salience": [10, 1],
            "price": [1.0, 2.0],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        return engine.evaluate({"region": "AU"}, **eval_kwargs)

    def test_select_first_from_collect(self):
        result = self._collect_result()
        first = result.select(HitPolicy.FIRST)
        assert first.count == 1
        assert first.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_select_priority_from_collect(self):
        result = self._collect_result()
        best = result.select(HitPolicy.PRIORITY, priority_field="salience")
        assert best.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_select_unique_raises_on_two_survivors(self):
        from mountainash_rules.hit_policy import HitPolicyViolationError
        with pytest.raises(HitPolicyViolationError):
            self._collect_result().select(HitPolicy.UNIQUE)

    def test_select_refuses_truncated_result(self):
        result = self._collect_result(top_n=1)
        with pytest.raises(ValueError, match="truncated"):
            result.select(HitPolicy.UNIQUE)


class TestAccumulatorCollectPin:
    def test_apply_metadata_pins_collect(self):
        from mountainash_rules.accumulator_engine import AccumulatorEngine
        md = DimensionsMetadata(
            dimensions=[Dimension(dimension_name="x")],
            hit_policy=HitPolicy.FIRST,
        )
        engine = AccumulatorEngine(dimension_metadata=md)
        assert engine._build_apply_metadata().hit_policy is HitPolicy.COLLECT


def test_hit_policy_yaml_round_trip():
    md = DimensionsMetadata(
        dimensions=[Dimension(dimension_name="x")],
        hit_policy=HitPolicy.PRIORITY,
        priority_field="salience",
        output_fields=["price"],
    )
    assert DimensionsMetadata.from_yaml(md.to_yaml()) == md


def test_package_exports():
    from mountainash_rules import HitPolicy as HP
    from mountainash_rules import HitPolicyViolationError, SelectionInfo  # noqa: F401
    assert HP("first") is HP.FIRST
