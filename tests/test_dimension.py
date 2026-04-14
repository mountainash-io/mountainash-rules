"""Tests for Dimension model validation."""

import pytest

from mountainash_utils_rules.constants import MatchStrategy
from mountainash_utils_rules.dimension import Dimension


class TestNumericStrategyValidation:
    def test_greater_than_requires_numeric(self):
        with pytest.raises(ValueError, match="GREATER_THAN"):
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
        with pytest.raises(ValueError, match="LESS_THAN"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.LESS_THAN,
                data_type=str,
            )


class TestStringStrategyValidation:
    def test_prefix_requires_string(self):
        with pytest.raises(ValueError, match="PREFIX"):
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
        with pytest.raises(ValueError, match="SUFFIX"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.SUFFIX,
                data_type=int,
            )

    def test_contains_requires_string(self):
        with pytest.raises(ValueError, match="CONTAINS"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.CONTAINS,
                data_type=int,
            )

    def test_regex_requires_string(self):
        with pytest.raises(ValueError, match="REGEX"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.REGEX,
                data_type=int,
            )


class TestRegexPatternValidation:
    def test_regex_with_pattern_ok(self):
        d = Dimension(
            dimension_name="code",
            match_strategy=MatchStrategy.REGEX,
            data_type=str,
            regex_pattern="^foo",
        )
        assert d.regex_pattern == "^foo"

    def test_regex_without_pattern_raises(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="code",
                match_strategy=MatchStrategy.REGEX,
                data_type=str,
            )

    def test_regex_empty_pattern_raises(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="code",
                match_strategy=MatchStrategy.REGEX,
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
