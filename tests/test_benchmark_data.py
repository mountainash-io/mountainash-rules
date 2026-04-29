"""Tests for the synthetic benchmark data generator (tests/benchmark_data.py)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.engine import ExpressionRulesEngine

from tests.benchmark_data import (
    assign_strategies,
    build_engine,
    generate_context,
    generate_rules,
)


# ---------------------------------------------------------------------------
# assign_strategies
# ---------------------------------------------------------------------------


class TestAssignStrategies:
    def test_three_dims_default_mix(self):
        result = assign_strategies(3)
        assert len(result) == 3
        assert all(isinstance(s, MatchStrategy) for s in result)

    def test_seven_dims_default_mix(self):
        result = assign_strategies(7)
        assert len(result) == 7
        assert all(isinstance(s, MatchStrategy) for s in result)
        # Should see at least EXACT and RANGE with default mix
        strategy_set = set(result)
        assert MatchStrategy.EXACT in strategy_set

    def test_single_strategy_override(self):
        result = assign_strategies(5, {MatchStrategy.REGEX: 1.0})
        assert len(result) == 5
        # All should be REGEX (padded with EXACT if proportional count < 5 but
        # since weight is 1.0 and round(5 * 1.0) == 5, all should be REGEX)
        assert all(s == MatchStrategy.REGEX for s in result)

    def test_deterministic_output(self):
        """Same inputs always produce the same strategy list."""
        assert assign_strategies(10) == assign_strategies(10)

    def test_exact_length(self):
        for n in [1, 4, 8, 15, 20]:
            assert len(assign_strategies(n)) == n


# ---------------------------------------------------------------------------
# generate_rules
# ---------------------------------------------------------------------------


class TestGenerateRules:
    def test_correct_shape(self):
        col_dict, metadata = generate_rules(rule_count=20, dim_count=3)
        assert "rule_name" in col_dict
        assert len(col_dict["rule_name"]) == 20
        assert len(metadata.dimensions) == 3

    def test_range_produces_min_max_columns(self):
        # Force a RANGE dimension by using a mix with only RANGE
        col_dict, metadata = generate_rules(
            rule_count=10,
            dim_count=1,
            strategy_mix={MatchStrategy.RANGE: 1.0},
        )
        assert "DIM_0_MIN" in col_dict
        assert "DIM_0_MAX" in col_dict
        assert len(col_dict["DIM_0_MIN"]) == 10
        # All non-sentinel values should satisfy min <= max
        for lo, hi in zip(col_dict["DIM_0_MIN"], col_dict["DIM_0_MAX"]):
            if lo != UNKNOWN_NUMERIC:
                assert lo <= hi

    def test_set_membership_produces_lists(self):
        col_dict, metadata = generate_rules(
            rule_count=10,
            dim_count=1,
            strategy_mix={MatchStrategy.SET_MEMBERSHIP: 1.0},
        )
        assert "DIM_0" in col_dict
        non_null = [v for v in col_dict["DIM_0"] if v is not None]
        assert len(non_null) > 0
        for v in non_null:
            assert isinstance(v, list)
            assert len(v) >= 2

    def test_set_exclusion_produces_lists(self):
        col_dict, metadata = generate_rules(
            rule_count=10,
            dim_count=1,
            strategy_mix={MatchStrategy.SET_EXCLUSION: 1.0},
        )
        assert "DIM_0" in col_dict
        non_null = [v for v in col_dict["DIM_0"] if v is not None]
        assert len(non_null) > 0
        for v in non_null:
            assert isinstance(v, list)

    def test_unknown_density_approx_15_percent(self):
        rule_count = 500
        col_dict, metadata = generate_rules(
            rule_count=rule_count,
            dim_count=3,
            strategy_mix={MatchStrategy.EXACT: 1.0},
            unknown_density=0.15,
            seed=42,
        )
        unknown_count = sum(1 for v in col_dict["DIM_0"] if v == UNKNOWN)
        density = unknown_count / rule_count
        # Allow generous tolerance: 5%–30%
        assert 0.05 <= density <= 0.30

    def test_deterministic_with_same_seed(self):
        a, _ = generate_rules(20, 3, seed=42)
        b, _ = generate_rules(20, 3, seed=42)
        assert a == b

    def test_different_seeds_differ(self):
        a, _ = generate_rules(20, 3, seed=42)
        b, _ = generate_rules(20, 3, seed=99)
        assert a != b

    def test_regex_dimension_has_pattern(self):
        _, metadata = generate_rules(
            rule_count=10,
            dim_count=1,
            strategy_mix={MatchStrategy.REGEX: 1.0},
        )
        dim = metadata.dimensions[0]
        assert dim.match_strategy == MatchStrategy.REGEX
        assert dim.regex_pattern is not None
        assert len(dim.regex_pattern) > 0

    def test_rule_names_are_unique(self):
        col_dict, _ = generate_rules(50, 2)
        assert len(col_dict["rule_name"]) == len(set(col_dict["rule_name"]))

    def test_multi_dim_multi_strategy(self):
        """Smoke test: many dims across multiple strategies don't raise."""
        col_dict, metadata = generate_rules(rule_count=30, dim_count=8)
        assert len(metadata.dimensions) == 8
        for dim in metadata.dimensions:
            assert dim.dimension_name.startswith("DIM_")


# ---------------------------------------------------------------------------
# generate_context
# ---------------------------------------------------------------------------


class TestGenerateContext:
    def _make_exact_setup(self, rule_count: int = 20):
        col_dict, metadata = generate_rules(
            rule_count=rule_count,
            dim_count=2,
            strategy_mix={MatchStrategy.EXACT: 1.0},
        )
        return col_dict, metadata

    def test_returns_base_model(self):
        col_dict, metadata = self._make_exact_setup()
        ctx = generate_context(metadata, col_dict)
        assert isinstance(ctx, BaseModel)

    def test_correct_string_fields(self):
        col_dict, metadata = self._make_exact_setup()
        ctx = generate_context(metadata, col_dict)
        for dim in metadata.dimensions:
            val = getattr(ctx, dim.dimension_name)
            assert isinstance(val, str)

    def test_range_context_is_int(self):
        col_dict, metadata = generate_rules(
            rule_count=20,
            dim_count=1,
            strategy_mix={MatchStrategy.RANGE: 1.0},
        )
        ctx = generate_context(metadata, col_dict)
        val = getattr(ctx, "DIM_0")
        assert isinstance(val, int)

    def test_gt_context_is_int(self):
        col_dict, metadata = generate_rules(
            rule_count=20,
            dim_count=1,
            strategy_mix={MatchStrategy.GREATER_THAN: 1.0},
        )
        ctx = generate_context(metadata, col_dict)
        val = getattr(ctx, "DIM_0")
        assert isinstance(val, int)

    def test_deterministic_with_same_seed(self):
        col_dict, metadata = self._make_exact_setup()
        ctx_a = generate_context(metadata, col_dict, seed=42)
        ctx_b = generate_context(metadata, col_dict, seed=42)
        assert ctx_a.model_dump() == ctx_b.model_dump()

    def test_different_seeds_may_differ(self):
        col_dict, metadata = self._make_exact_setup(rule_count=50)
        ctx_a = generate_context(metadata, col_dict, seed=1)
        ctx_b = generate_context(metadata, col_dict, seed=2)
        # Not guaranteed to differ, but with 50 rules the pool is large enough
        # that this effectively always holds.
        assert ctx_a.model_dump() != ctx_b.model_dump()

    def test_fields_match_dimension_names(self):
        col_dict, metadata = generate_rules(rule_count=10, dim_count=4)
        ctx = generate_context(metadata, col_dict)
        for dim in metadata.dimensions:
            assert hasattr(ctx, dim.dimension_name)


# ---------------------------------------------------------------------------
# build_engine
# ---------------------------------------------------------------------------


class TestBuildEngine:
    def test_returns_expression_rules_engine(self):
        col_dict, metadata = generate_rules(
            rule_count=10,
            dim_count=2,
            strategy_mix={MatchStrategy.EXACT: 1.0},
        )
        engine = build_engine(col_dict, metadata, "polars")
        assert isinstance(engine, ExpressionRulesEngine)

    def test_roundtrip_evaluate(self):
        col_dict, metadata = generate_rules(
            rule_count=20,
            dim_count=2,
            strategy_mix={MatchStrategy.EXACT: 1.0},
            seed=42,
        )
        engine = build_engine(col_dict, metadata, "polars")
        ctx = generate_context(metadata, col_dict, seed=42)
        result = engine.evaluate(ctx)
        # Should not raise; survivors may be zero or more
        assert result is not None

    def test_pandas_backend(self):
        col_dict, metadata = generate_rules(
            rule_count=10,
            dim_count=1,
            strategy_mix={MatchStrategy.EXACT: 1.0},
        )
        engine = build_engine(col_dict, metadata, "pandas")
        assert isinstance(engine, ExpressionRulesEngine)

    def test_range_roundtrip(self):
        col_dict, metadata = generate_rules(
            rule_count=20,
            dim_count=1,
            strategy_mix={MatchStrategy.RANGE: 1.0},
            seed=42,
        )
        engine = build_engine(col_dict, metadata, "polars")
        ctx = generate_context(metadata, col_dict, seed=42)
        result = engine.evaluate(ctx)
        assert result is not None
