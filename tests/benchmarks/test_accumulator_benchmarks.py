"""Performance benchmarks for AccumulatorEngine build and apply phases.

Run with: pytest tests/test_accumulator_benchmarks.py -v --benchmark-enable
Skip with: pytest tests/test_accumulator_benchmarks.py -v --benchmark-disable

Requires pytest-benchmark (not installed in CI test_github env).
"""

import random

import polars as pl
import pytest
from pydantic import create_model

pytestmark = pytest.mark.benchmark

from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

def _generate_accumulator_rules(
    rule_count: int,
    dim_count: int,
    unknown_density: float = 0.3,
    seed: int = 42,
) -> tuple[pl.DataFrame, DimensionsMetadata]:
    """Generate synthetic rules with EXACT and RANGE dimensions.

    Args:
        rule_count: Number of rules to generate.
        dim_count: Number of constraint dimensions (half EXACT, half RANGE).
        unknown_density: Fraction of rule cells that are wildcards.
        seed: RNG seed for determinism.

    Returns:
        (rules_df, metadata)
    """
    rng = random.Random(seed)
    n_exact = dim_count // 2 or 1
    n_range = dim_count - n_exact

    data: dict[str, list] = {"rule_name": [f"rule_{i}" for i in range(rule_count)]}
    dims: list[Dimension] = []

    # EXACT dimensions
    values_pool = ["A", "B", "C", "D", "E"]
    for d in range(n_exact):
        name = f"exact_{d}"
        col = []
        for _ in range(rule_count):
            if rng.random() < unknown_density:
                col.append(UNKNOWN)
            else:
                col.append(rng.choice(values_pool))
        data[name] = col
        dims.append(Dimension(dimension_name=name, match_strategy=MatchStrategy.EXACT))

    # RANGE dimensions
    for d in range(n_range):
        name = f"range_{d}"
        min_col = []
        max_col = []
        for _ in range(rule_count):
            if rng.random() < unknown_density:
                min_col.append(UNKNOWN_NUMERIC)
                max_col.append(UNKNOWN_NUMERIC)
            else:
                lo = rng.randint(0, 80)
                hi = lo + rng.randint(5, 20)
                min_col.append(lo)
                max_col.append(hi)
        data[f"{name}_min"] = min_col
        data[f"{name}_max"] = max_col
        dims.append(Dimension(
            dimension_name=name,
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field=f"{name}_min",
            range_max_field=f"{name}_max",
        ))

    # Aggregate column
    data["margin"] = [rng.uniform(-1.0, 1.0) for _ in range(rule_count)]

    metadata = DimensionsMetadata(dimensions=dims)
    return pl.DataFrame(data), metadata


def _generate_context(metadata: DimensionsMetadata, seed: int = 99):
    """Generate a context that hits approximately half the dimensions."""
    rng = random.Random(seed)
    fields = {}
    for dim in metadata.dimensions:
        if dim.match_strategy == MatchStrategy.EXACT:
            fields[dim.dimension_name] = (str, rng.choice(["A", "B", "C"]))
        elif dim.match_strategy == MatchStrategy.RANGE:
            fields[dim.dimension_name] = (int, rng.randint(30, 70))
    return create_model("BenchContext", **fields)(**{k: v[1] for k, v in fields.items()})


# ---------------------------------------------------------------------------
# Build phase benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="accumulator-build-rules")
class TestBuildScalingByRuleCount:
    """Build time as rule count increases (fixed 4 dims, 30% unknown density)."""

    @pytest.fixture(params=[5, 10, 15])
    def rule_count(self, request):
        return request.param

    def test_build_scaling(self, rule_count, benchmark):
        rules, metadata = _generate_accumulator_rules(
            rule_count=rule_count, dim_count=4, unknown_density=0.3,
        )
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        result = benchmark.pedantic(
            engine.build, args=(rules,), rounds=3, warmup_rounds=1,
        )
        assert result.count >= 1


@pytest.mark.benchmark(group="accumulator-build-dims")
class TestBuildScalingByDimCount:
    """Build time as dimension count increases (fixed 10 rules, 30% unknown)."""

    @pytest.fixture(params=[2, 4, 6])
    def dim_count(self, request):
        return request.param

    def test_build_dim_scaling(self, dim_count, benchmark):
        rules, metadata = _generate_accumulator_rules(
            rule_count=10, dim_count=dim_count, unknown_density=0.3,
        )
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        result = benchmark.pedantic(
            engine.build, args=(rules,), rounds=3, warmup_rounds=1,
        )
        assert result.count >= 1


@pytest.mark.benchmark(group="accumulator-build-density")
class TestBuildScalingByUnknownDensity:
    """Build time as unknown density increases (more wildcards = more combinations)."""

    @pytest.fixture(params=[0.1, 0.3, 0.5, 0.7])
    def density(self, request):
        return request.param

    def test_build_density_scaling(self, density, benchmark):
        rules, metadata = _generate_accumulator_rules(
            rule_count=10, dim_count=4, unknown_density=density,
        )
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        result = benchmark.pedantic(
            engine.build, args=(rules,), rounds=3, warmup_rounds=1,
        )
        assert result.count >= 1


# ---------------------------------------------------------------------------
# Apply phase benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="accumulator-apply")
class TestApplyScaling:
    """Apply time against pre-built lattices of varying sizes."""

    @pytest.fixture(params=[5, 10, 15])
    def rule_count(self, request):
        return request.param

    def test_apply_scaling(self, rule_count, benchmark):
        rules, metadata = _generate_accumulator_rules(
            rule_count=rule_count, dim_count=4, unknown_density=0.3,
        )
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        context = _generate_context(metadata)

        result = benchmark.pedantic(
            engine.apply, args=(lattice, context), rounds=5, warmup_rounds=1,
        )
        assert result.count >= 0  # may be 0 if context doesn't match


# ---------------------------------------------------------------------------
# End-to-end build+apply benchmark
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="accumulator-e2e")
class TestEndToEndScaling:
    """Full build + apply cycle."""

    @pytest.fixture(params=[5, 10])
    def rule_count(self, request):
        return request.param

    def test_e2e(self, rule_count, benchmark):
        rules, metadata = _generate_accumulator_rules(
            rule_count=rule_count, dim_count=4, unknown_density=0.3,
        )
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        context = _generate_context(metadata)

        def build_and_apply():
            lattice = engine.build(rules)
            return engine.apply(lattice, context)

        result = benchmark.pedantic(
            build_and_apply, rounds=3, warmup_rounds=1,
        )
