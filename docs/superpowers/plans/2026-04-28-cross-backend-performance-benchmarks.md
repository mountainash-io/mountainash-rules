# Cross-Backend Performance Benchmarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pytest-benchmark performance benchmarks that measure `engine.evaluate()` latency across the 6 benchmarkable backends (all except `ibis-polars`, blocked by mountainash-io/mountainash#78), with a 3×3 scaling matrix (rules × dimensions) and per-strategy isolation tests.

**Architecture:** A synthetic data generator (`tests/benchmark_data.py`) builds deterministic rule sets and contexts from seeded RNG. Benchmark tests (`tests/test_benchmarks.py`) parametrize over `(rule_count, dim_count, backend_name)` for the scaling matrix and `(strategy, backend_name)` for strategy isolation. pytest-benchmark handles warmup, round statistics, and JSON persistence. Engine construction is excluded from the timed loop via pedantic mode — only `engine.evaluate(context)` is benchmarked.

**Tech Stack:** pytest-benchmark (already in test env), pydantic (context models), mountainash_utils_rules (engine + compiler + constants), existing conftest.py (`ALL_BACKENDS`, `LIST_CAPABLE_BACKENDS`, `build_backend_df`). Benchmarks define `BENCH_BACKENDS = [b for b in ALL_BACKENDS if b != "ibis-polars"]` to exclude the broken backend.

**Spec:** `docs/superpowers/specs/2026-04-28-cross-backend-performance-benchmarks-design.md`

**Test command:** `hatch run test:test-perf` (all benchmarks) or `hatch run test:test-perf-target tests/test_benchmarks.py -v` (verbose)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `tests/benchmark_data.py` | Create | Synthetic data generation: `generate_rules()`, `generate_context()`, `build_engine()` |
| `tests/test_benchmarks.py` | Create | pytest-benchmark test functions: scaling matrix + strategy isolation |
| `hatch.toml` | Modify | Add `test-perf-save` command |

No changes to existing test files or conftest.py.

---

### Task 1: Synthetic Data Generator — Dimension Assignment

**Files:**
- Create: `tests/benchmark_data.py`

- [ ] **Step 1: Write a test for dimension assignment**

Create `tests/test_benchmark_data.py`:

```python
"""Tests for the synthetic benchmark data generator."""

from mountainash_utils_rules.constants import MatchStrategy
from benchmark_data import assign_strategies


def test_assign_strategies_default_mix_3_dims():
    """3 dims with default mix should assign the top-weighted strategies."""
    result = assign_strategies(dim_count=3)
    assert len(result) == 3
    assert all(isinstance(s, MatchStrategy) for s in result)
    # With default weights, top 3 are EXACT(0.35), RANGE(0.25), REGEX(0.10)
    assert result[0] == MatchStrategy.EXACT
    assert result[1] == MatchStrategy.RANGE


def test_assign_strategies_default_mix_7_dims():
    """7 dims should spread across more strategies."""
    result = assign_strategies(dim_count=7)
    assert len(result) == 7
    strategies_used = set(result)
    # At least 4 distinct strategies with 7 dims
    assert len(strategies_used) >= 4


def test_assign_strategies_single_strategy_override():
    """Override to 100% EXACT should assign all dims as EXACT."""
    result = assign_strategies(dim_count=5, strategy_mix={"EXACT": 1.0})
    assert all(s == MatchStrategy.EXACT for s in result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'benchmark_data'`

- [ ] **Step 3: Implement `assign_strategies`**

Create `tests/benchmark_data.py`:

```python
"""Synthetic data generator for performance benchmarks.

Generates deterministic rule sets and contexts using seeded RNG.
All public functions accept a `seed` parameter for reproducibility.
"""

from __future__ import annotations

import math

from mountainash_utils_rules.constants import MatchStrategy


DEFAULT_STRATEGY_MIX: dict[str, float] = {
    "EXACT": 0.35,
    "RANGE": 0.25,
    "REGEX": 0.10,
    "PREFIX": 0.10,
    "GREATER_THAN": 0.05,
    "LESS_THAN": 0.05,
    "SET_MEMBERSHIP": 0.05,
    "CONTAINS": 0.05,
}


def assign_strategies(
    dim_count: int,
    strategy_mix: dict[str, float] | None = None,
) -> list[MatchStrategy]:
    """Assign a MatchStrategy to each of `dim_count` dimensions.

    Distributes strategies proportionally to weights. Deterministic
    given (dim_count, strategy_mix).
    """
    mix = strategy_mix or DEFAULT_STRATEGY_MIX
    sorted_entries = sorted(mix.items(), key=lambda kv: kv[1], reverse=True)

    assignments: list[MatchStrategy] = []
    remaining = dim_count

    for i, (name, weight) in enumerate(sorted_entries):
        if remaining <= 0:
            break
        if i == len(sorted_entries) - 1:
            count = remaining
        else:
            count = max(1, round(weight * dim_count)) if remaining > 0 else 0
            count = min(count, remaining)
        assignments.extend([MatchStrategy[name]] * count)
        remaining -= count

    return assignments[:dim_count]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/benchmark_data.py tests/test_benchmark_data.py
git commit -m "feat(benchmarks): add dimension strategy assignment"
```

---

### Task 2: Synthetic Data Generator — Rule Value Generation

**Files:**
- Modify: `tests/benchmark_data.py`
- Modify: `tests/test_benchmark_data.py`

- [ ] **Step 1: Write tests for rule generation**

Append to `tests/test_benchmark_data.py`:

```python
from benchmark_data import generate_rules


def test_generate_rules_returns_correct_shape():
    """100 rules, 5 dims should produce a dict with rule_name + dim columns."""
    rules_dict, metadata = generate_rules(rule_count=100, dim_count=5)
    assert len(rules_dict["rule_name"]) == 100
    assert len(metadata.dimensions) == 5
    # Every column in the dict should have exactly rule_count entries
    for col_values in rules_dict.values():
        assert len(col_values) == 100


def test_generate_rules_range_produces_min_max_columns():
    """RANGE strategy should produce DIM_k_MIN and DIM_k_MAX columns."""
    rules_dict, metadata = generate_rules(
        rule_count=10, dim_count=2, strategy_mix={"RANGE": 1.0},
    )
    assert "DIM_0_MIN" in rules_dict
    assert "DIM_0_MAX" in rules_dict
    for i in range(10):
        assert rules_dict["DIM_0_MIN"][i] <= rules_dict["DIM_0_MAX"][i]


def test_generate_rules_set_membership_produces_lists():
    """SET_MEMBERSHIP should produce list values."""
    rules_dict, metadata = generate_rules(
        rule_count=10, dim_count=1, strategy_mix={"SET_MEMBERSHIP": 1.0},
    )
    non_unknown = [v for v in rules_dict["DIM_0"] if isinstance(v, list)]
    assert len(non_unknown) > 0
    for v in non_unknown:
        assert 2 <= len(v) <= 5


def test_generate_rules_unknown_density():
    """~15% of values should be sentinels."""
    rules_dict, metadata = generate_rules(rule_count=200, dim_count=3, seed=42)
    from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC
    sentinels = {UNKNOWN, UNKNOWN_NUMERIC}
    total = 0
    unknown_count = 0
    for dim in metadata.dimensions:
        col = dim.resolved_rule_field
        if col in rules_dict:
            for v in rules_dict[col]:
                total += 1
                if v in sentinels:
                    unknown_count += 1
    density = unknown_count / total
    assert 0.05 < density < 0.30  # loose bounds for RNG


def test_generate_rules_deterministic():
    """Same seed should produce identical output."""
    r1, m1 = generate_rules(rule_count=50, dim_count=3, seed=99)
    r2, m2 = generate_rules(rule_count=50, dim_count=3, seed=99)
    assert r1 == r2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v -k "test_generate_rules"`
Expected: FAIL with `ImportError: cannot import name 'generate_rules'`

- [ ] **Step 3: Implement `generate_rules`**

Add to `tests/benchmark_data.py`:

```python
import random
import typing as t

from mountainash_utils_rules.constants import (
    UNKNOWN,
    UNKNOWN_NUMERIC,
    MatchStrategy,
)
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata


# --- Regex patterns safe from catastrophic backtracking ---
_REGEX_PATTERNS = [
    "^[A-Z]{2}_\\d+",
    "^ITEM_\\d{3}$",
    "^[A-Z]+_[a-z]+$",
    "^CODE_[0-9A-F]{4}",
    "^PRE_\\d{2,4}",
]


def generate_rules(
    rule_count: int,
    dim_count: int,
    strategy_mix: dict[str, float] | None = None,
    unknown_density: float = 0.15,
    seed: int = 42,
) -> tuple[dict[str, list], DimensionsMetadata]:
    """Return (column_dict, metadata) for a synthetic rule set."""
    rng = random.Random(seed)
    strategies = assign_strategies(dim_count, strategy_mix)

    columns: dict[str, list] = {"rule_name": [f"rule_{i}" for i in range(rule_count)]}
    dimensions: list[Dimension] = []

    for dim_idx, strategy in enumerate(strategies):
        dim_name = f"DIM_{dim_idx}"
        _add_dimension(
            columns=columns,
            dimensions=dimensions,
            dim_name=dim_name,
            strategy=strategy,
            rule_count=rule_count,
            unknown_density=unknown_density,
            rng=rng,
        )

    metadata = DimensionsMetadata(dimensions=dimensions)
    return columns, metadata


def _add_dimension(
    columns: dict[str, list],
    dimensions: list[Dimension],
    dim_name: str,
    strategy: MatchStrategy,
    rule_count: int,
    unknown_density: float,
    rng: random.Random,
) -> None:
    """Generate column data and Dimension metadata for one dimension."""
    is_numeric = strategy in (
        MatchStrategy.RANGE,
        MatchStrategy.GREATER_THAN,
        MatchStrategy.LESS_THAN,
    )
    sentinel = UNKNOWN_NUMERIC if is_numeric else UNKNOWN

    if strategy == MatchStrategy.RANGE:
        mins: list[t.Any] = []
        maxs: list[t.Any] = []
        for _ in range(rule_count):
            if rng.random() < unknown_density:
                mins.append(UNKNOWN_NUMERIC)
                maxs.append(UNKNOWN_NUMERIC)
            else:
                lo = rng.randint(0, 900)
                hi = lo + rng.randint(10, 100)
                mins.append(lo)
                maxs.append(hi)
        columns[f"{dim_name}_MIN"] = mins
        columns[f"{dim_name}_MAX"] = maxs
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field=f"{dim_name}_MIN",
            range_max_field=f"{dim_name}_MAX",
        ))

    elif strategy in (MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL):
        pool_size = max(5, rule_count // 3)
        pool = [f"VAL_{i:04d}" for i in range(pool_size)]
        values = [
            sentinel if rng.random() < unknown_density else rng.choice(pool)
            for _ in range(rule_count)
        ]
        columns[dim_name] = values
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=strategy,
            data_type=str,
        ))

    elif strategy in (MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN):
        values = [
            sentinel if rng.random() < unknown_density else rng.randint(100, 900)
            for _ in range(rule_count)
        ]
        columns[dim_name] = values
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=strategy,
            data_type=int,
        ))

    elif strategy == MatchStrategy.PREFIX:
        prefixes = [f"PFX_{i:03d}" for i in range(max(3, rule_count // 5))]
        values = [
            sentinel if rng.random() < unknown_density
            else rng.choice(prefixes) + f"_{rng.randint(0, 999):03d}"
            for _ in range(rule_count)
        ]
        columns[dim_name] = values
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=MatchStrategy.PREFIX,
            data_type=str,
        ))

    elif strategy == MatchStrategy.SUFFIX:
        suffixes = [f"SFX_{i:03d}" for i in range(max(3, rule_count // 5))]
        values = [
            sentinel if rng.random() < unknown_density
            else f"{rng.randint(0, 999):03d}_" + rng.choice(suffixes)
            for _ in range(rule_count)
        ]
        columns[dim_name] = values
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=MatchStrategy.SUFFIX,
            data_type=str,
        ))

    elif strategy == MatchStrategy.CONTAINS:
        substrings = [f"SUB{i:02d}" for i in range(max(3, rule_count // 5))]
        values = [
            sentinel if rng.random() < unknown_density
            else f"x{rng.choice(substrings)}y"
            for _ in range(rule_count)
        ]
        columns[dim_name] = values
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=MatchStrategy.CONTAINS,
            data_type=str,
        ))

    elif strategy == MatchStrategy.REGEX:
        pattern = rng.choice(_REGEX_PATTERNS)
        values = [
            sentinel if rng.random() < unknown_density else pattern
            for _ in range(rule_count)
        ]
        columns[dim_name] = values
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=MatchStrategy.REGEX,
            data_type=str,
            regex_pattern=pattern,
        ))

    elif strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
        pool = [f"SET_{i:03d}" for i in range(max(5, rule_count // 3))]
        values: list[t.Any] = []
        for _ in range(rule_count):
            if rng.random() < unknown_density:
                values.append(sentinel)
            else:
                size = rng.randint(2, 5)
                values.append(rng.sample(pool, min(size, len(pool))))
        columns[dim_name] = values
        dimensions.append(Dimension(
            dimension_name=dim_name,
            match_strategy=strategy,
            data_type=str,
        ))

    else:
        raise ValueError(f"Unsupported strategy: {strategy}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v`
Expected: 8 PASSED (3 from Task 1 + 5 new)

- [ ] **Step 5: Commit**

```bash
git add tests/benchmark_data.py tests/test_benchmark_data.py
git commit -m "feat(benchmarks): add synthetic rule generation"
```

---

### Task 3: Synthetic Data Generator — Context Generation

**Files:**
- Modify: `tests/benchmark_data.py`
- Modify: `tests/test_benchmark_data.py`

- [ ] **Step 1: Write tests for context generation**

Append to `tests/test_benchmark_data.py`:

```python
from benchmark_data import generate_context
from pydantic import BaseModel


def test_generate_context_returns_base_model():
    """Context should be a pydantic BaseModel with fields matching dimensions."""
    rules_dict, metadata = generate_rules(rule_count=50, dim_count=3, seed=42)
    ctx = generate_context(metadata, rules_dict, seed=42)
    assert isinstance(ctx, BaseModel)
    for dim in metadata.dimensions:
        assert hasattr(ctx, dim.dimension_name)


def test_generate_context_values_are_correct_types():
    """Numeric dims should have int/float values, string dims should have str."""
    rules_dict, metadata = generate_rules(
        rule_count=50, dim_count=5, seed=42,
    )
    ctx = generate_context(metadata, rules_dict, seed=42)
    for dim in metadata.dimensions:
        val = getattr(ctx, dim.dimension_name)
        if dim.data_type == int:
            assert isinstance(val, int)
        elif dim.data_type == float:
            assert isinstance(val, (int, float))
        else:
            assert isinstance(val, str)


def test_generate_context_deterministic():
    """Same seed should produce identical context."""
    rules_dict, metadata = generate_rules(rule_count=50, dim_count=3, seed=42)
    c1 = generate_context(metadata, rules_dict, seed=99)
    c2 = generate_context(metadata, rules_dict, seed=99)
    assert c1.model_dump() == c2.model_dump()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v -k "test_generate_context"`
Expected: FAIL with `ImportError: cannot import name 'generate_context'`

- [ ] **Step 3: Implement `generate_context`**

Add to `tests/benchmark_data.py`:

```python
from pydantic import BaseModel, create_model


def generate_context(
    metadata: DimensionsMetadata,
    rules_dict: dict[str, list],
    hit_rate: float = 0.15,
    seed: int = 42,
) -> BaseModel:
    """Return a pydantic context that matches ~hit_rate of the rules."""
    rng = random.Random(seed)

    fields: dict[str, t.Any] = {}
    values: dict[str, t.Any] = {}

    for dim in metadata.dimensions:
        val = _generate_context_value(dim, rules_dict, rng, hit_rate)
        py_type = dim.data_type
        fields[dim.dimension_name] = (py_type, ...)
        values[dim.dimension_name] = val

    ContextModel = create_model("BenchmarkContext", **fields)
    return ContextModel(**values)


def _generate_context_value(
    dim: Dimension,
    rules_dict: dict[str, list],
    rng: random.Random,
    hit_rate: float,
) -> t.Any:
    """Pick a context value for one dimension, biased toward matching rules."""
    strategy = dim.match_strategy
    col = dim.resolved_rule_field

    if strategy == MatchStrategy.RANGE:
        min_col = dim.range_min_field
        max_col = dim.range_max_field
        assert min_col and max_col
        valid_ranges = [
            (lo, hi) for lo, hi in zip(rules_dict[min_col], rules_dict[max_col])
            if lo != UNKNOWN_NUMERIC
        ]
        if valid_ranges:
            lo, hi = rng.choice(valid_ranges)
            return rng.randint(lo, hi)
        return 500

    if strategy in (MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN):
        non_sentinel = [v for v in rules_dict[col] if v != UNKNOWN_NUMERIC]
        if non_sentinel:
            median = sorted(non_sentinel)[len(non_sentinel) // 2]
            return median + rng.randint(-50, 50)
        return 500

    if strategy == MatchStrategy.REGEX:
        return "AB_12345"

    if strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
        all_values: list[str] = []
        for v in rules_dict[col]:
            if isinstance(v, list):
                all_values.extend(v)
        if all_values:
            return rng.choice(all_values)
        return "SET_000"

    # String strategies: EXACT, NOT_EQUAL, PREFIX, SUFFIX, CONTAINS
    non_sentinel = [v for v in rules_dict[col] if v != UNKNOWN]
    if non_sentinel:
        sample = rng.choice(non_sentinel)
        if strategy == MatchStrategy.PREFIX:
            return sample
        if strategy == MatchStrategy.SUFFIX:
            return sample
        if strategy == MatchStrategy.CONTAINS:
            return sample
        return sample
    return "FALLBACK"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v`
Expected: 11 PASSED (8 from Tasks 1-2 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add tests/benchmark_data.py tests/test_benchmark_data.py
git commit -m "feat(benchmarks): add synthetic context generation"
```

---

### Task 4: Synthetic Data Generator — Engine Factory

**Files:**
- Modify: `tests/benchmark_data.py`
- Modify: `tests/test_benchmark_data.py`

- [ ] **Step 1: Write test for engine factory**

Append to `tests/test_benchmark_data.py`:

```python
from benchmark_data import build_engine
from mountainash_utils_rules.engine import ExpressionRulesEngine


def test_build_engine_polars():
    """build_engine should return a working ExpressionRulesEngine."""
    rules_dict, metadata = generate_rules(rule_count=10, dim_count=3, seed=42)
    engine = build_engine(rules_dict, metadata, "polars")
    assert isinstance(engine, ExpressionRulesEngine)


def test_build_engine_roundtrip():
    """Engine should evaluate without error."""
    rules_dict, metadata = generate_rules(rule_count=10, dim_count=3, seed=42)
    engine = build_engine(rules_dict, metadata, "polars")
    ctx = generate_context(metadata, rules_dict, seed=42)
    result = engine.evaluate(ctx)
    # Should not raise; survivor count is non-negative
    assert result.count >= 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v -k "test_build_engine"`
Expected: FAIL with `ImportError: cannot import name 'build_engine'`

- [ ] **Step 3: Implement `build_engine`**

Add to `tests/benchmark_data.py`:

```python
from mountainash_utils_rules.engine import ExpressionRulesEngine
from conftest import build_backend_df


def build_engine(
    rules_dict: dict[str, list],
    metadata: DimensionsMetadata,
    backend_name: str,
) -> ExpressionRulesEngine:
    """Build a DataFrame in the given backend and return an engine."""
    df = build_backend_df(backend_name, rules_dict, table_name="bench_rules")
    return ExpressionRulesEngine(rules=df, dimension_metadata=metadata)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target-quick tests/test_benchmark_data.py -v`
Expected: 13 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/benchmark_data.py tests/test_benchmark_data.py
git commit -m "feat(benchmarks): add engine factory using conftest backend builder"
```

---

### Task 5: Scaling Matrix Benchmark

**Files:**
- Create: `tests/test_benchmarks.py`

- [ ] **Step 1: Write the scaling matrix benchmark test**

Create `tests/test_benchmarks.py`:

```python
"""Performance benchmarks for ExpressionRulesEngine across backends.

Run with:
    hatch run test:test-perf                          # terminal output
    hatch run test:test-perf-save                     # terminal + JSON
    hatch run test:test-perf-target tests/test_benchmarks.py -v  # verbose
"""

from __future__ import annotations

import pytest

from conftest import ALL_BACKENDS, LIST_CAPABLE_BACKENDS
from benchmark_data import build_engine, generate_context, generate_rules

# ibis-polars excluded: upstream bug mountainash-io/mountainash#78
# breaks with_row_index in the engine pipeline.
BENCH_BACKENDS = [b for b in ALL_BACKENDS if b != "ibis-polars"]

RULE_COUNTS = [10, 100, 1000]
DIM_COUNTS = [3, 5, 7]


class TestScalingMatrix:
    """3×3×6 scaling matrix: rule_count × dim_count × backend."""

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

        # SET strategies require list-capable backends — skip if
        # the generated rules include them and the backend can't handle it.
        has_set_strategy = any(
            d.match_strategy in (
                __import__("mountainash_utils_rules.constants", fromlist=["MatchStrategy"]).MatchStrategy.SET_MEMBERSHIP,
                __import__("mountainash_utils_rules.constants", fromlist=["MatchStrategy"]).MatchStrategy.SET_EXCLUSION,
            )
            for d in metadata.dimensions
        )
        if has_set_strategy and backend_name not in LIST_CAPABLE_BACKENDS:
            pytest.skip(f"{backend_name} does not support list columns")

        engine = build_engine(rules_dict, metadata, backend_name)
        ctx = generate_context(metadata, rules_dict, seed=42)

        benchmark.pedantic(
            engine.evaluate,
            args=(ctx,),
            rounds=5,
            warmup_rounds=1,
        )
```

- [ ] **Step 2: Run with a single backend to verify it works**

Run: `hatch run test:test-target-quick tests/test_benchmarks.py -v -k "polars and 10r and 3d" --benchmark-only`
Expected: 1 PASSED with benchmark timing output

- [ ] **Step 3: Commit**

```bash
git add tests/test_benchmarks.py
git commit -m "feat(benchmarks): add scaling matrix benchmark (3×3×6)"
```

---

### Task 6: Refactor Scaling Matrix — Clean Up Import

**Files:**
- Modify: `tests/test_benchmarks.py`

The `__import__` hack in Task 5 is ugly. Clean it up now that the file exists.

- [ ] **Step 1: Replace the inline import with a top-level import**

Replace the `has_set_strategy` block in `tests/test_benchmarks.py`:

```python
from mountainash_utils_rules.constants import MatchStrategy

# ... inside the test method, replace the has_set_strategy block with:

        _SET_STRATEGIES = {MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION}
        has_set_strategy = any(
            d.match_strategy in _SET_STRATEGIES
            for d in metadata.dimensions
        )
        if has_set_strategy and backend_name not in LIST_CAPABLE_BACKENDS:
            pytest.skip(f"{backend_name} does not support list columns")
```

Move `_SET_STRATEGIES` to module level and import `MatchStrategy` at the top. The full file header should be:

```python
from __future__ import annotations

import pytest

from conftest import ALL_BACKENDS, LIST_CAPABLE_BACKENDS
from benchmark_data import build_engine, generate_context, generate_rules
from mountainash_utils_rules.constants import MatchStrategy

# ibis-polars excluded: upstream bug mountainash-io/mountainash#78
# breaks with_row_index in the engine pipeline.
BENCH_BACKENDS = [b for b in ALL_BACKENDS if b != "ibis-polars"]

_SET_STRATEGIES = {MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION}
```

And the skip check in the test becomes:

```python
        has_set_strategy = any(
            d.match_strategy in _SET_STRATEGIES for d in metadata.dimensions
        )
        if has_set_strategy and backend_name not in LIST_CAPABLE_BACKENDS:
            pytest.skip(f"{backend_name} does not support list columns")
```

- [ ] **Step 2: Run test to verify it still works**

Run: `hatch run test:test-target-quick tests/test_benchmarks.py -v -k "polars and 10r and 3d" --benchmark-only`
Expected: 1 PASSED

- [ ] **Step 3: Commit**

```bash
git add tests/test_benchmarks.py
git commit -m "refactor(benchmarks): clean up MatchStrategy import"
```

---

### Task 7: Per-Strategy Isolation Benchmark

**Files:**
- Modify: `tests/test_benchmarks.py`

- [ ] **Step 1: Add the strategy isolation test**

Append to `tests/test_benchmarks.py`:

```python
STRATEGY_RULE_COUNT = 100
STRATEGY_DIM_COUNT = 5


class TestStrategyIsolation:
    """Per-strategy benchmarks at fixed medium size (100r × 5d)."""

    @pytest.mark.benchmark(group="strategy")
    @pytest.mark.parametrize("strategy", list(MatchStrategy), ids=lambda s: s.name)
    @pytest.mark.parametrize("backend_name", BENCH_BACKENDS)
    def test_strategy(self, benchmark, strategy, backend_name):
        if (
            strategy in _SET_STRATEGIES
            and backend_name not in LIST_CAPABLE_BACKENDS
        ):
            pytest.skip(f"{backend_name} does not support list columns")

        rules_dict, metadata = generate_rules(
            rule_count=STRATEGY_RULE_COUNT,
            dim_count=STRATEGY_DIM_COUNT,
            strategy_mix={strategy.name: 1.0},
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
```

- [ ] **Step 2: Run with one strategy and one backend to verify**

Run: `hatch run test:test-target-quick tests/test_benchmarks.py::TestStrategyIsolation -v -k "EXACT and polars" --benchmark-only`
Expected: 1 PASSED with benchmark timing

- [ ] **Step 3: Commit**

```bash
git add tests/test_benchmarks.py
git commit -m "feat(benchmarks): add per-strategy isolation benchmarks"
```

---

### Task 8: Hatch Command — `test-perf-save`

**Files:**
- Modify: `hatch.toml`

- [ ] **Step 1: Add `test-perf-save` to hatch.toml**

In `hatch.toml`, in the `[envs.test.scripts]` section, after the `test-perf-target` line (line 156), add:

```toml
test-perf-save = "pytest --benchmark-only --benchmark-save=baseline {args}"
```

- [ ] **Step 2: Verify the command is recognized**

Run: `hatch run test:test-perf-save tests/test_benchmarks.py -k "polars and 10r and 3d" 2>&1 | head -20`
Expected: pytest runs with `--benchmark-save=baseline` and a JSON file is created in `.benchmarks/`

- [ ] **Step 3: Verify JSON file was created**

Run: `ls .benchmarks/`
Expected: A directory containing a JSON file with "baseline" in the name

- [ ] **Step 4: Commit**

```bash
git add hatch.toml
git commit -m "feat(benchmarks): add test-perf-save hatch command for JSON baselines"
```

---

### Task 9: Smoke-Test Full Benchmark Suite

This task runs the full benchmark suite to verify everything works end-to-end. No code changes — purely validation.

- [ ] **Step 1: Run scaling matrix benchmarks (polars only, to save time)**

Run: `hatch run test:test-perf-target "tests/test_benchmarks.py::TestScalingMatrix -k polars" -v`
Expected: 9 PASSED (3 rule counts × 3 dim counts)

- [ ] **Step 2: Run strategy isolation benchmarks (polars only)**

Run: `hatch run test:test-perf-target "tests/test_benchmarks.py::TestStrategyIsolation -k polars" -v`
Expected: 11 PASSED (one per strategy)

- [ ] **Step 3: Run the full suite across all backends**

Run: `hatch run test:test-perf-target tests/test_benchmarks.py -v`
Expected: ~100+ benchmarks pass (some skipped for SET on non-list backends). Review the terminal output to confirm grouping looks correct.

- [ ] **Step 4: Verify existing tests are not affected**

Run: `hatch run test:test-quick`
Expected: All existing tests pass. Benchmark tests are skipped (no `--benchmark-only` flag means pytest-benchmark skips them by default, or they run with `--benchmark-disable`).

- [ ] **Step 5: Commit any .benchmarks/ gitignore if needed**

If `.benchmarks/` is not already in `.gitignore`, add it:

```bash
echo ".benchmarks/" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore .benchmarks/ directory"
```

---

Plan complete and saved to `docs/superpowers/plans/2026-04-28-cross-backend-performance-benchmarks.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
