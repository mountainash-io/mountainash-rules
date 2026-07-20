"""Tests for AccumulatorCompiler — compatible, coalesce, and NA flag expressions."""

import polars as pl
import pytest

import mountainash.expressions as ma

from mountainash_rules.engines.accumulator.compiler import AccumulatorCompiler
from mountainash_rules.core.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.core.constants import DataType
from mountainash_rules.core.dimension import Dimension


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


class TestRangeCompatible:
    def test_overlapping_intervals(self, compiler):
        dim = Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        )
        expr = compiler.compile_compatible(dim)

        df = pl.DataFrame({
            "co_lvr_min": [60, 60, 80],
            "co_lvr_max": [80, 80, 90],
            "lvr_min_rhs": [70, 90, 70],
            "lvr_max_rhs": [90, 100, 75],
        })
        result = df.with_columns(expr.alias("compat").compile(df, booleanizer=None))
        assert result["compat"].to_list() == [True, False, False]

    def test_sentinel_always_compatible(self, compiler):
        dim = Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        )
        expr = compiler.compile_compatible(dim)

        df = pl.DataFrame({
            "co_lvr_min": [UNKNOWN_NUMERIC, 60],
            "co_lvr_max": [UNKNOWN_NUMERIC, 80],
            "lvr_min_rhs": [70, UNKNOWN_NUMERIC],
            "lvr_max_rhs": [90, UNKNOWN_NUMERIC],
        })
        result = df.with_columns(expr.alias("compat").compile(df, booleanizer=None))
        assert result["compat"].to_list() == [True, True]


class TestRangeCoalesce:
    def test_intersection_tightens_bounds(self, compiler):
        dim = Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        )
        exprs = compiler.compile_coalesce(dim)
        assert len(exprs) == 2

        df = pl.DataFrame({
            "co_lvr_min": [60], "co_lvr_max": [80],
            "lvr_min_rhs": [70], "lvr_max_rhs": [90],
        })
        result = df.with_columns(*[e.compile(df, booleanizer=None) for e in exprs])
        assert result["co_lvr_min"].to_list() == [70]
        assert result["co_lvr_max"].to_list() == [80]

    def test_sentinel_lhs_uses_rhs(self, compiler):
        dim = Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        )
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({
            "co_lvr_min": [UNKNOWN_NUMERIC], "co_lvr_max": [UNKNOWN_NUMERIC],
            "lvr_min_rhs": [70], "lvr_max_rhs": [90],
        })
        result = df.with_columns(*[e.compile(df, booleanizer=None) for e in exprs])
        assert result["co_lvr_min"].to_list() == [70]
        assert result["co_lvr_max"].to_list() == [90]

    def test_sentinel_rhs_uses_lhs(self, compiler):
        dim = Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        )
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({
            "co_lvr_min": [60], "co_lvr_max": [80],
            "lvr_min_rhs": [UNKNOWN_NUMERIC], "lvr_max_rhs": [UNKNOWN_NUMERIC],
        })
        result = df.with_columns(*[e.compile(df, booleanizer=None) for e in exprs])
        assert result["co_lvr_min"].to_list() == [60]
        assert result["co_lvr_max"].to_list() == [80]

    def test_both_sentinel_stays_sentinel(self, compiler):
        dim = Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        )
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({
            "co_lvr_min": [UNKNOWN_NUMERIC], "co_lvr_max": [UNKNOWN_NUMERIC],
            "lvr_min_rhs": [UNKNOWN_NUMERIC], "lvr_max_rhs": [UNKNOWN_NUMERIC],
        })
        result = df.with_columns(*[e.compile(df, booleanizer=None) for e in exprs])
        assert result["co_lvr_min"].to_list() == [UNKNOWN_NUMERIC]
        assert result["co_lvr_max"].to_list() == [UNKNOWN_NUMERIC]


class TestGreaterThanCompatible:
    def test_always_compatible(self, compiler):
        dim = Dimension(
            dimension_name="score", match_strategy=MatchStrategy.GREATER_THAN, data_type=int,
        )
        expr = compiler.compile_compatible(dim)

        df = pl.DataFrame({
            "co_score": [10, UNKNOWN_NUMERIC, 10],
            "score_rhs": [20, 30, UNKNOWN_NUMERIC],
        })
        result = df.with_columns(expr.alias("compat").compile(df, booleanizer=None))
        assert result["compat"].to_list() == [True, True, True]


class TestGreaterThanCoalesce:
    def test_tighter_bound_wins(self, compiler):
        dim = Dimension(
            dimension_name="score", match_strategy=MatchStrategy.GREATER_THAN, data_type=int,
        )
        exprs = compiler.compile_coalesce(dim)
        assert len(exprs) == 1

        df = pl.DataFrame({"co_score": [10], "score_rhs": [20]})
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_score"].to_list() == [20]

    def test_sentinel_lhs_uses_rhs(self, compiler):
        dim = Dimension(
            dimension_name="score", match_strategy=MatchStrategy.GREATER_THAN, data_type=int,
        )
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({"co_score": [UNKNOWN_NUMERIC], "score_rhs": [20]})
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_score"].to_list() == [20]

    def test_both_sentinel_stays_sentinel(self, compiler):
        dim = Dimension(
            dimension_name="score", match_strategy=MatchStrategy.GREATER_THAN, data_type=int,
        )
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({"co_score": [UNKNOWN_NUMERIC], "score_rhs": [UNKNOWN_NUMERIC]})
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_score"].to_list() == [UNKNOWN_NUMERIC]


class TestLessThanCompatible:
    def test_always_compatible(self, compiler):
        dim = Dimension(
            dimension_name="cap", match_strategy=MatchStrategy.LESS_THAN, data_type=int,
        )
        expr = compiler.compile_compatible(dim)

        df = pl.DataFrame({
            "co_cap": [100, UNKNOWN_NUMERIC],
            "cap_rhs": [50, 80],
        })
        result = df.with_columns(expr.alias("compat").compile(df, booleanizer=None))
        assert result["compat"].to_list() == [True, True]


class TestLessThanCoalesce:
    def test_tighter_bound_wins(self, compiler):
        dim = Dimension(
            dimension_name="cap", match_strategy=MatchStrategy.LESS_THAN, data_type=int,
        )
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({"co_cap": [100], "cap_rhs": [50]})
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_cap"].to_list() == [50]

    def test_sentinel_rhs_uses_lhs(self, compiler):
        dim = Dimension(
            dimension_name="cap", match_strategy=MatchStrategy.LESS_THAN, data_type=int,
        )
        exprs = compiler.compile_coalesce(dim)

        df = pl.DataFrame({"co_cap": [100], "cap_rhs": [UNKNOWN_NUMERIC]})
        result = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert result["co_cap"].to_list() == [100]


class TestSetMembershipCompatible:
    def _dim(self):
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)

    def test_non_empty_intersection_compatible(self, compiler):
        expr = compiler.compile_compatible(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU", "NZ"], ["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["NZ", "UK"], ["US", "CA"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.alias("c").compile(df, booleanizer=None))
        assert out["c"].to_list() == [True, False]

    def test_wildcard_either_side_compatible(self, compiler):
        expr = compiler.compile_compatible(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"], ["AU"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["US"], ["<NA>"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.alias("c").compile(df, booleanizer=None))
        assert out["c"].to_list() == [True, True]


class TestSetMembershipCoalesce:
    def _dim(self):
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)

    def test_intersection_canonicalized(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        assert len(exprs) == 1
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU", "NZ", "UK"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["UK", "NZ", "US"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["NZ", "UK"]]  # sorted-unique

    def test_wildcard_passthrough(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"], ["AU"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["AU"], ["<NA>"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["AU"], ["AU"]]

    def test_both_wildcard_stays_sentinel(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["<NA>"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["<NA>"]]
        assert out["co_region"].dtype == pl.List(pl.Utf8)


class TestSetExclusionCoalesce:
    def _dim(self):
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_EXCLUSION, data_type=DataType.STR)

    def test_union_canonicalized(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["NZ", "US"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["AU", "NZ", "US"]]

    def test_always_compatible(self, compiler):
        expr = compiler.compile_compatible(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU"], ["<NA>"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["NZ"], ["US"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.alias("c").compile(df, booleanizer=None))
        assert out["c"].to_list() == [True, True]


class TestSetNaFlag:
    def _dim(self, strategy):
        return Dimension(dimension_name="region", match_strategy=strategy, data_type=DataType.STR)

    def test_na_flag_from_final_coalesced_value(self, compiler):
        # wildcard+wildcard -> 1 ; wildcard+concrete -> 0 ; concrete+concrete -> 0
        expr = compiler.compile_coalesce_na_flag(self._dim(MatchStrategy.SET_MEMBERSHIP))
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"], ["<NA>"], ["AU"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["<NA>"], ["AU"], ["NZ"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.compile(df, booleanizer=None))
        assert out["co_region_na"].to_list() == [1, 0, 0]
