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
