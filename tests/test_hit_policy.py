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
