"""Tests for DimensionCompiler."""

import polars as pl
import pytest

import mountainash.expressions as ma

from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import CTX_PREFIX, UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension


@pytest.fixture
def compiler():
    return DimensionCompiler()


class TestExactCompilation:
    def test_exact_match_produces_true(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US", "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [1, -1, -1]

    def test_exact_unknown_rule_value_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", UNKNOWN, "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values[0] == 1   # hard match
        assert values[1] == 0   # unknown (wildcard)
        assert values[2] == -1  # non-match

    def test_exact_unknown_context_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US"],
            f"{CTX_PREFIX}region": [UNKNOWN, UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [0, 0]  # all unknown when context is unknown

    def test_exact_numeric(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.EXACT, data_type=int)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "tier": [1, 2, UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}tier": [1, 1, 1],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values[0] == 1   # match
        assert values[1] == -1  # non-match
        assert values[2] == 0   # unknown


class TestRangeCompilation:
    def test_range_within_bounds_produces_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=float,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [0.0, 100.0, 200.0],
            "amount_max": [99.0, 199.0, 299.0],
            f"{CTX_PREFIX}amount": [50.0, 50.0, 50.0],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, -1, -1]

    def test_range_boundary_inclusive(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
            range_min_inclusive=True,
            range_max_inclusive=True,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [10, 10],
            "amount_max": [20, 20],
            f"{CTX_PREFIX}amount": [10, 20],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, 1]  # both boundaries inclusive

    def test_range_boundary_exclusive(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
            range_min_inclusive=False,
            range_max_inclusive=False,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [10, 10],
            "amount_max": [20, 20],
            f"{CTX_PREFIX}amount": [10, 20],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1]  # both boundaries exclusive

    def test_range_unknown_min_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [0, UNKNOWN_NUMERIC],
            "amount_max": [100, 100],
            f"{CTX_PREFIX}amount": [50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values[0] == 1  # known range, match
        assert values[1] == 0  # unknown min → unknown result


class TestRegexCompilation:
    def test_regex_match_produces_true(self, compiler):
        dim = Dimension(dimension_name="pattern", match_strategy=MatchStrategy.REGEX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "pattern": ["^AU.*", "^US.*", "^UK.*"],
            f"{CTX_PREFIX}pattern": ["AU-123", "AU-123", "AU-123"],
        })
        result = df.with_columns(expr.name.alias("__t_pattern").compile(df, booleanizer=None))
        values = result["__t_pattern"].to_list()
        assert values[0] == 1   # match
        assert values[1] == -1  # no match
        assert values[2] == -1  # no match

    def test_regex_search_semantics(self, compiler):
        """regex_contains uses search semantics (match anywhere, not anchored)."""
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.REGEX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["123", "xyz"],
            f"{CTX_PREFIX}code": ["abc-123-def", "abc-123-def"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values[0] == 1   # "123" found within "abc-123-def"
        assert values[1] == -1  # "xyz" not found

    def test_regex_unknown_pattern_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="pattern", match_strategy=MatchStrategy.REGEX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "pattern": ["^AU.*", UNKNOWN],
            f"{CTX_PREFIX}pattern": ["AU-123", "AU-123"],
        })
        result = df.with_columns(expr.name.alias("__t_pattern").compile(df, booleanizer=None))
        values = result["__t_pattern"].to_list()
        assert values[0] == 1  # match
        assert values[1] == 0  # unknown pattern → unknown result


class TestNotEqualCompilation:
    def test_not_equal_mismatch_produces_true(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.NOT_EQUAL, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US", "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [-1, 1, 1]

    def test_not_equal_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.NOT_EQUAL, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [UNKNOWN, "US"],
            f"{CTX_PREFIX}region": ["AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values[0] == 0
        assert values[1] == 1


class TestGreaterThanCompilation:
    def test_greater_than_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [1500, 1500, 1500],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, 1, 1]

    def test_greater_than_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [50, 50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1, -1]

    def test_greater_than_equal_is_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1]

    def test_greater_than_unknown_rule(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [0]


class TestLessThanCompilation:
    def test_less_than_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [50, 50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, 1, 1]

    def test_less_than_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100, 500],
            f"{CTX_PREFIX}amount": [1500, 1500],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1]

    def test_less_than_equal_is_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [100],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1]

    def test_less_than_unknown_rule(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "amount": [UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [0]


class TestPrefixCompilation:
    def test_prefix_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-", "POST-", "MID-"],
            f"{CTX_PREFIX}code": ["PRE-001", "PRE-001", "PRE-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [1, -1, -1]

    def test_prefix_no_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-"],
            f"{CTX_PREFIX}code": ["XYZ-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [-1]

    def test_prefix_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-", UNKNOWN],
            f"{CTX_PREFIX}code": ["PRE-001", "PRE-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_prefix_per_row_different_patterns(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)
        df = pl.DataFrame({
            "code": ["PRE-", "POST-", "MID-"],
            f"{CTX_PREFIX}code": ["PRE-001", "POST-002", "MID-003"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        assert result["__t_code"].to_list() == [1, 1, 1]
