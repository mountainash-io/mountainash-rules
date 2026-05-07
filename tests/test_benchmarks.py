"""Performance benchmarks for ExpressionRulesEngine across backends.

Run with:
    hatch run test:test-perf                          # terminal output
    hatch run test:test-perf-save                     # terminal + JSON
    hatch run test:test-perf-target tests/test_benchmarks.py -v  # verbose
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.benchmark

from tests.conftest import ALL_BACKENDS, LIST_CAPABLE_BACKENDS
from tests.benchmark_data import build_engine, generate_context, generate_rules
from mountainash_rules.constants import MatchStrategy

# ibis-polars excluded: upstream bug mountainash-io/mountainash#78
# breaks with_row_index in the engine pipeline.
BENCH_BACKENDS = [b for b in ALL_BACKENDS if b != "ibis-polars"]

_SET_STRATEGIES = {MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION}

# String-match strategies broken on pandas/narwhals backends (upstream).
# pandas: PREFIX, SUFFIX, CONTAINS fail — str accessor receives Expr not str.
# narwhals-polars: PREFIX, SUFFIX fail — cannot create literal for Expr.
# Tracked as mountainash-io/mountainash#89.
_STRING_MATCH_STRATEGIES = {MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS}
_STRING_MATCH_BROKEN_BACKENDS = {"pandas", "narwhals-pandas", "narwhals-polars"}

RULE_COUNTS = [10, 100, 1000]
DIM_COUNTS = [3, 5, 7]
STRATEGY_RULE_COUNT = 100
STRATEGY_DIM_COUNT = 5


class TestScalingMatrix:
    """3x3x6 scaling matrix: rule_count x dim_count x backend."""

    @pytest.mark.benchmark(group="scaling")
    @pytest.mark.parametrize("rule_count", RULE_COUNTS, ids=["10r", "100r", "1000r"])
    @pytest.mark.parametrize("dim_count", DIM_COUNTS, ids=["3d", "5d", "7d"])
    @pytest.mark.parametrize("backend_name", BENCH_BACKENDS)
    def test_scaling(self, benchmark, rule_count, dim_count, backend_name):
        rules_dict, metadata = generate_rules(
            rule_count=rule_count,
            dim_count=dim_count,
            seed=42,
        )

        has_set_strategy = any(
            d.match_strategy in _SET_STRATEGIES for d in metadata.dimensions
        )
        if has_set_strategy and backend_name not in LIST_CAPABLE_BACKENDS:
            pytest.skip(f"{backend_name} does not support list columns")

        has_string_match = any(
            d.match_strategy in _STRING_MATCH_STRATEGIES for d in metadata.dimensions
        )
        if has_string_match and backend_name in _STRING_MATCH_BROKEN_BACKENDS:
            pytest.skip(f"{backend_name} does not support per-row string match strategies")

        engine = build_engine(rules_dict, metadata, backend_name)
        ctx = generate_context(metadata, rules_dict, seed=42)

        benchmark.pedantic(
            engine.evaluate,
            args=(ctx,),
            rounds=5,
            warmup_rounds=1,
        )


class TestStrategyIsolation:
    """Per-strategy benchmarks at fixed medium size (100r x 5d)."""

    @pytest.mark.benchmark(group="strategy")
    @pytest.mark.parametrize("strategy", list(MatchStrategy), ids=lambda s: s.name)
    @pytest.mark.parametrize("backend_name", BENCH_BACKENDS)
    def test_strategy(self, benchmark, strategy, backend_name):
        if (
            strategy in _SET_STRATEGIES
            and backend_name not in LIST_CAPABLE_BACKENDS
        ):
            pytest.skip(f"{backend_name} does not support list columns")

        if (
            strategy in _STRING_MATCH_STRATEGIES
            and backend_name in _STRING_MATCH_BROKEN_BACKENDS
        ):
            pytest.skip(f"{backend_name} does not support per-row string match strategies")

        rules_dict, metadata = generate_rules(
            rule_count=STRATEGY_RULE_COUNT,
            dim_count=STRATEGY_DIM_COUNT,
            strategy_mix={strategy: 1.0},
            seed=42,
        )
        engine = build_engine(rules_dict, metadata, backend_name)
        ctx = generate_context(metadata, rules_dict, seed=42)

        benchmark.pedantic(
            engine.evaluate,
            args=(ctx,),
            rounds=5,
            warmup_rounds=1,
        )
