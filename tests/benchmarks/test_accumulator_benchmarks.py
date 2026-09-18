"""Performance benchmarks for AccumulatorEngine build and apply phases.

Run with: pytest tests/test_accumulator_benchmarks.py -v --benchmark-enable
Skip with: pytest tests/test_accumulator_benchmarks.py -v --benchmark-disable

Requires pytest-benchmark (not installed in CI test_github env).
"""

import random

import mountainash_rules as rules
import pytest

from tests.accumulator.exact_runtime_fixtures import declarations, gate, uuid


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

def _aggregate():
    return rules.Aggregate(
        column_name="margin",
        output_name="pricing.margin",
        data_type="float",
        numeric_semantics="numeric-1",
    )


def _profile(dimensions):
    return rules.ResolutionProfile(
        profile_id="benchmark",
        mode="candidates",
        output_fields=("pricing.margin",),
        provenance="none",
        dimensions=tuple(sorted(d.dimension_name for d in dimensions)),
        allow_dont_care=(),
        promise="candidate_only",
    )


def _expected_overlap_decisions(rows, dimensions):
    """Author the finite synthetic overlap decisions without reading findings."""
    def wildcard(row, dimension):
        sentinel = rules.unknown_sentinel_for(dimension.data_type)
        if dimension.match_strategy == rules.MatchStrategy.RANGE:
            return (
                row[dimension.range_min_field] == sentinel
                and row[dimension.range_max_field] == sentinel
            )
        return row[dimension.dimension_name] == sentinel

    def overlap(left, right, dimension):
        if wildcard(left, dimension) or wildcard(right, dimension):
            return True
        if dimension.match_strategy == rules.MatchStrategy.RANGE:
            return (
                left[dimension.range_min_field] <= right[dimension.range_max_field]
                and right[dimension.range_min_field] <= left[dimension.range_max_field]
            )
        return left[dimension.dimension_name] == right[dimension.dimension_name]

    def duplicate(left, right):
        for dimension in dimensions:
            if dimension.match_strategy == rules.MatchStrategy.RANGE:
                fields = (dimension.range_min_field, dimension.range_max_field)
            else:
                fields = (dimension.dimension_name,)
            if any(left[field] != right[field] for field in fields):
                return False
        return True

    decisions = []
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1:]:
            identities = tuple(sorted((left["id"], right["id"])))
            if all(overlap(left, right, dimension) for dimension in dimensions):
                decisions.append(("source_overlap", identities))
                if duplicate(left, right):
                    decisions.append(("duplicate_source", identities))
    return decisions


def _generate_accumulator_rules(
    rule_count: int,
    dim_count: int,
    unknown_density: float = 0.3,
    seed: int = 42,
):
    """Generate deterministic exact sources and their reviewed validation gate."""
    rng = random.Random(seed)
    n_exact = dim_count // 2 or 1
    n_range = dim_count - n_exact
    data: dict[str, list] = {
        "id": [uuid(index + 1) for index in range(rule_count)],
        "rule_name": [f"rule_{index}" for index in range(rule_count)],
    }
    dimensions = []

    values_pool = ["A", "B", "C", "D", "E"]
    for index in range(n_exact):
        name = f"exact_{index}"
        data[name] = [
            rules.UNKNOWN if rng.random() < unknown_density else rng.choice(values_pool)
            for _ in range(rule_count)
        ]
        dimensions.append(
            rules.Dimension(
                dimension_name=name,
                match_strategy=rules.MatchStrategy.EXACT,
            )
        )

    for index in range(n_range):
        name = f"range_{index}"
        minimums = []
        maximums = []
        for _ in range(rule_count):
            if rng.random() < unknown_density:
                minimums.append(rules.UNKNOWN_NUMERIC)
                maximums.append(rules.UNKNOWN_NUMERIC)
            else:
                bucket = rng.randrange(5)
                minimum = bucket * 100
                minimums.append(minimum)
                maximums.append(minimum + 20)
        data[f"{name}_min"] = minimums
        data[f"{name}_max"] = maximums
        dimensions.append(
            rules.Dimension(
                dimension_name=name,
                match_strategy=rules.MatchStrategy.RANGE,
                data_type="int",
                range_min_field=f"{name}_min",
                range_max_field=f"{name}_max",
            )
        )

    data["margin"] = [rng.uniform(-1.0, 1.0) for _ in range(rule_count)]
    profile = _profile(dimensions)
    contract = rules.ContextContract(
        schema_version=1,
        contract_id="benchmark",
        domain_ref="D",
        fields=tuple(
            rules.ContextField(
                name=dimension.resolved_context_field,
                data_type=dimension.data_type,
                required=True,
            )
            for dimension in sorted(dimensions, key=lambda item: item.dimension_name)
        ),
        profiles=(profile,),
    )
    source_rows = [
        {field: values[index] for field, values in data.items()}
        for index in range(rule_count)
    ]
    kwargs = declarations(
        tuple(dimensions),
        (_aggregate(),),
        contracts=(contract,),
    )
    validation = gate(
        source_rows,
        kwargs,
        decisions=_expected_overlap_decisions(source_rows, dimensions),
    )
    return source_rows, kwargs, validation


def _generate_context(metadata: rules.DimensionsMetadata, seed: int = 99):
    """Generate a complete named-profile request."""
    rng = random.Random(seed)
    context = {}
    for dimension in metadata.dimensions:
        if dimension.match_strategy == rules.MatchStrategy.EXACT:
            context[dimension.resolved_context_field] = rng.choice(["A", "B", "C"])
        elif dimension.match_strategy == rules.MatchStrategy.RANGE:
            context[dimension.resolved_context_field] = rng.randint(30, 70)
    return context


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
        source_rows, kwargs, validation = _generate_accumulator_rules(
            rule_count=rule_count, dim_count=4, unknown_density=0.3,
        )
        engine = rules.AccumulatorEngine(
            kwargs["metadata"],
            kwargs["aggregates"],
            limits=kwargs["limits"],
        )
        result = benchmark.pedantic(
            engine.build,
            args=(source_rows,),
            kwargs={"validation": validation},
            rounds=3,
            warmup_rounds=1,
        )
        assert result.count >= 1


@pytest.mark.benchmark(group="accumulator-build-dims")
class TestBuildScalingByDimCount:
    """Build time as dimension count increases (fixed 10 rules, 30% unknown)."""

    @pytest.fixture(params=[2, 4, 6])
    def dim_count(self, request):
        return request.param

    def test_build_dim_scaling(self, dim_count, benchmark):
        source_rows, kwargs, validation = _generate_accumulator_rules(
            rule_count=10, dim_count=dim_count, unknown_density=0.3,
        )
        engine = rules.AccumulatorEngine(
            kwargs["metadata"],
            kwargs["aggregates"],
            limits=kwargs["limits"],
        )
        result = benchmark.pedantic(
            engine.build,
            args=(source_rows,),
            kwargs={"validation": validation},
            rounds=3,
            warmup_rounds=1,
        )
        assert result.count >= 1


@pytest.mark.benchmark(group="accumulator-build-density")
class TestBuildScalingByUnknownDensity:
    """Build time as unknown density increases (more wildcards = more combinations)."""

    @pytest.fixture(params=[0.1, 0.3, 0.5, 0.7])
    def density(self, request):
        return request.param

    def test_build_density_scaling(self, density, benchmark):
        source_rows, kwargs, validation = _generate_accumulator_rules(
            rule_count=10, dim_count=4, unknown_density=density,
        )
        engine = rules.AccumulatorEngine(
            kwargs["metadata"],
            kwargs["aggregates"],
            limits=kwargs["limits"],
        )
        result = benchmark.pedantic(
            engine.build,
            args=(source_rows,),
            kwargs={"validation": validation},
            rounds=3,
            warmup_rounds=1,
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
        source_rows, kwargs, validation = _generate_accumulator_rules(
            rule_count=rule_count, dim_count=4, unknown_density=0.3,
        )
        engine = rules.AccumulatorEngine(
            kwargs["metadata"],
            kwargs["aggregates"],
            limits=kwargs["limits"],
        )
        lattice = engine.build(source_rows, validation=validation)
        context = _generate_context(kwargs["metadata"])

        result = benchmark.pedantic(
            engine.apply,
            args=(lattice, context),
            kwargs={"contract_id": "benchmark", "profile_id": "benchmark"},
            rounds=5,
            warmup_rounds=1,
        )
        assert result.status in {"candidates", "no_match"}


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
        source_rows, kwargs, validation = _generate_accumulator_rules(
            rule_count=rule_count, dim_count=4, unknown_density=0.3,
        )
        engine = rules.AccumulatorEngine(
            kwargs["metadata"],
            kwargs["aggregates"],
            limits=kwargs["limits"],
        )
        context = _generate_context(kwargs["metadata"])

        def build_and_apply():
            lattice = engine.build(source_rows, validation=validation)
            return engine.apply(
                lattice,
                context,
                contract_id="benchmark",
                profile_id="benchmark",
            )

        result = benchmark.pedantic(
            build_and_apply, rounds=3, warmup_rounds=1,
        )
        assert result.status in {"candidates", "no_match"}
