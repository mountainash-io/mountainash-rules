"""Tests for Dimension model validation."""

import pytest

from mountainash_rules.core.constants import DimensionRole, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


class TestNumericStrategyValidation:
    def test_greater_than_requires_numeric(self):
        with pytest.raises(ValueError, match="greater_than"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.GREATER_THAN,
                data_type=str,
            )

    def test_greater_than_accepts_int(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        assert d.match_strategy == MatchStrategy.GREATER_THAN

    def test_greater_than_accepts_float(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=float,
        )
        assert d.match_strategy == MatchStrategy.GREATER_THAN

    def test_less_than_requires_numeric(self):
        with pytest.raises(ValueError, match="less_than"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.LESS_THAN,
                data_type=str,
            )


class TestStringStrategyValidation:
    def test_prefix_requires_string(self):
        with pytest.raises(ValueError, match="prefix"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.PREFIX,
                data_type=int,
            )

    def test_prefix_accepts_string(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.PREFIX,
            data_type=str,
        )
        assert d.match_strategy == MatchStrategy.PREFIX

    def test_suffix_requires_string(self):
        with pytest.raises(ValueError, match="suffix"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.SUFFIX,
                data_type=int,
            )

    def test_contains_requires_string(self):
        with pytest.raises(ValueError, match="contains"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.CONTAINS,
                data_type=int,
            )

    def test_regex_requires_string(self):
        with pytest.raises(ValueError, match="regex"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.REGEX,
                data_type=int,
            )


class TestRegexPatternValidation:
    def test_regex_with_pattern_ok(self):
        d = Dimension(
            dimension_name="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            data_type=str,
            regex_pattern="^foo",
        )
        assert d.regex_pattern == "^foo"

    def test_regex_without_pattern_raises(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="code",
                match_strategy=MatchStrategy.CONTEXT_REGEX,
                data_type=str,
            )

    def test_regex_empty_pattern_raises(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="code",
                match_strategy=MatchStrategy.CONTEXT_REGEX,
                data_type=str,
                regex_pattern="",
            )

    def test_regex_pattern_forbidden_on_non_regex(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.EXACT,
                data_type=str,
                regex_pattern="^foo",
            )


class TestSetStrategyValidation:
    def test_set_membership_accepts_any_type(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        )
        assert d.match_strategy == MatchStrategy.SET_MEMBERSHIP

    def test_set_exclusion_accepts_any_type(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.SET_EXCLUSION,
            data_type=int,
        )
        assert d.match_strategy == MatchStrategy.SET_EXCLUSION


class TestExistingValidationUnchanged:
    def test_exact_unchanged(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.EXACT,
            data_type=str,
        )
        assert d.match_strategy == MatchStrategy.EXACT

    def test_range_still_requires_numeric(self):
        with pytest.raises(ValueError):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.RANGE,
                data_type=str,
                range_min_field="min",
                range_max_field="max",
            )

    def test_range_still_requires_min_max_fields(self):
        with pytest.raises(ValueError):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
            )


class TestDimensionRole:
    def test_default_role_is_constraint(self):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT)
        assert dim.role == DimensionRole.CONSTRAINT

    def test_explicit_context_key_role(self):
        dim = Dimension(
            dimension_name="product_id",
            match_strategy=MatchStrategy.EXACT,
            role=DimensionRole.CONTEXT_KEY,
        )
        assert dim.role == DimensionRole.CONTEXT_KEY

    def test_existing_dimensions_unaffected(self):
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT),
            Dimension(
                dimension_name="amount",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field="amount_min",
                range_max_field="amount_max",
            ),
        ])
        assert all(d.role == DimensionRole.CONSTRAINT for d in metadata.dimensions)
