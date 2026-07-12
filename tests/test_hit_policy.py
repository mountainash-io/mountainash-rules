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
