"""Tests for serialisable dimension metadata: StrEnums, DataType, YAML."""

import warnings

import pytest

from mountainash_rules.constants import DimensionRole, MatchStrategy


class TestStrEnums:
    def test_match_strategy_constructs_from_string(self):
        assert MatchStrategy("range") is MatchStrategy.RANGE

    def test_match_strategy_value_is_stable_string(self):
        assert MatchStrategy.GREATER_THAN.value == "greater_than"

    def test_match_strategy_is_str(self):
        assert isinstance(MatchStrategy.EXACT, str)

    def test_context_regex_member_exists(self):
        assert MatchStrategy("context_regex") is MatchStrategy.CONTEXT_REGEX

    def test_dimension_role_constructs_from_string(self):
        assert DimensionRole("context_key") is DimensionRole.CONTEXT_KEY
