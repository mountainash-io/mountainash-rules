"""Tests for AccumulatorCompiler — compatible, coalesce, and NA flag expressions."""

import polars as pl
import pytest

import mountainash.expressions as ma

from mountainash_utils_rules.accumulator_compiler import AccumulatorCompiler
from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension


@pytest.fixture
def compiler():
    return AccumulatorCompiler()


class TestExactCompatible:
    def test_both_hard_same_value(self, compiler):
        dim = Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT)
        expr = compiler.compile_compatible(dim)

        df = pl.DataFrame({
            "co_channel": ["BROKER", "BROKER", "BROKER"],
            "channel_rhs": ["BROKER", "DIRECT", UNKNOWN],
        })
        result = df.with_columns(expr.alias("compat").compile(df, booleanizer=None))
        assert result["compat"].to_list() == [True, False, True]

    def test_lhs_sentinel(self, compiler):
        dim = Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT)
        expr = compiler.compile_compatible(dim)

        df = pl.DataFrame({
            "co_channel": [UNKNOWN, UNKNOWN],
            "channel_rhs": ["BROKER", UNKNOWN],
        })
        result = df.with_columns(expr.alias("compat").compile(df, booleanizer=None))
        assert result["compat"].to_list() == [True, True]

    def test_numeric_exact_compatible(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.EXACT, data_type=int)
        expr = compiler.compile_compatible(dim)

        df = pl.DataFrame({
            "co_tier": [1, 1, UNKNOWN_NUMERIC],
            "tier_rhs": [1, 2, 3],
        })
        result = df.with_columns(expr.alias("compat").compile(df, booleanizer=None))
        assert result["compat"].to_list() == [True, False, True]


class TestExactCoalesce:
    def test_hard_wins_over_sentinel(self, compiler):
        dim = Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT)
        exprs = compiler.compile_coalesce(dim)
        assert len(exprs) == 1

        df = pl.DataFrame({
            "co_channel": [UNKNOWN, "BROKER", "BROKER"],
            "channel_rhs": ["DIRECT", UNKNOWN, "BROKER"],
        })
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_channel"].to_list() == ["DIRECT", "BROKER", "BROKER"]

    def test_both_sentinel_stays_sentinel(self, compiler):
        dim = Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT)
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({
            "co_channel": [UNKNOWN],
            "channel_rhs": [UNKNOWN],
        })
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_channel"].to_list() == [UNKNOWN]

    def test_numeric_coalesce(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.EXACT, data_type=int)
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({
            "co_tier": [UNKNOWN_NUMERIC, 1, 2],
            "tier_rhs": [3, UNKNOWN_NUMERIC, 2],
        })
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_tier"].to_list() == [3, 1, 2]


class TestExactCoalesceNaFlag:
    def test_na_only_when_both_sentinel(self, compiler):
        dim = Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT)
        expr = compiler.compile_coalesce_na_flag(dim)

        df = pl.DataFrame({
            "co_channel": [UNKNOWN, UNKNOWN, "BROKER", "BROKER"],
            "channel_rhs": [UNKNOWN, "DIRECT", UNKNOWN, "BROKER"],
        })
        result = df.with_columns(expr.compile(df, booleanizer=None))
        assert result["co_channel_na"].to_list() == [1, 0, 0, 0]
