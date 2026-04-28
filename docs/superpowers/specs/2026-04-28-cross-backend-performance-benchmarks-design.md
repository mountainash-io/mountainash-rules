# Cross-Backend Performance Benchmarks — Design Spec

**Status:** Approved design. Not yet implemented.
**Date:** 2026-04-28
**Author:** Nathaniel Ramm (with Claude)

---

## 1. Purpose

Add performance benchmarks to `mountainash-utils-rules` that answer two questions:

1. **Comparative backend ranking** — how do the 7 supported backends compare for the same workload?
2. **Scaling characterisation** — how does each backend behave as rule count and dimension count grow?

This is local-only tooling (no CI gating). Results are printed to the terminal via pytest-benchmark and optionally saved as JSON to `.benchmarks/` for later analysis.

---

## 2. Scope

**In scope:**
- Scaling matrix: 3 rule-count tiers × 3 dimension-count tiers × 7 backends (63 benchmarks)
- Per-strategy isolation: 11 strategies × 7 backends at fixed medium size (77 benchmarks, minus skips for non-list backends)
- Synthetic data generator with seeded RNG for reproducibility
- Hatch command for saving JSON baselines

**Out of scope:**
- CI integration / regression gating
- Markdown or HTML report generation
- Benchmarking engine construction time (only `apply` is timed)
- Benchmarking the accumulator engine (not yet implemented)

---

## 3. Files

| File | Action | Responsibility |
|------|--------|----------------|
| `tests/benchmark_data.py` | Create | Synthetic data generation and engine factory |
| `tests/test_benchmarks.py` | Create | pytest-benchmark test functions |
| `hatch.toml` | Modify | Add `test-perf-save` command |

No changes to existing test files or conftest.py.

---

## 4. Synthetic Data Generator (`tests/benchmark_data.py`)

### 4.1 Public API

```python
def generate_rules(
    rule_count: int,
    dim_count: int,
    strategy_mix: dict[str, float] | None = None,
    unknown_density: float = 0.15,
    seed: int = 42,
) -> tuple[dict[str, list], DimensionsMetadata]:
    """Return (column_dict, metadata) for a synthetic rule set."""

def generate_context(
    metadata: DimensionsMetadata,
    rules_dict: dict[str, list],
    hit_rate: float = 0.15,
    seed: int = 42,
) -> BaseModel:
    """Return a pydantic context that matches ~hit_rate of the rules."""

def build_engine(
    rules_dict: dict[str, list],
    metadata: DimensionsMetadata,
    backend_name: str,
) -> ExpressionRulesEngine:
    """Build a DataFrame in the given backend and return an engine."""
```

### 4.2 Strategy Mix

Default realistic mix (weights are approximate — rounded to fit dim_count):

| Strategy | Weight |
|----------|--------|
| EXACT | 0.35 |
| RANGE | 0.25 |
| REGEX | 0.10 |
| PREFIX | 0.10 |
| GREATER_THAN | 0.05 |
| LESS_THAN | 0.05 |
| SET_MEMBERSHIP | 0.05 |
| CONTAINS | 0.05 |

For per-strategy isolation benchmarks, the mix is overridden to 100% of one strategy.

### 4.3 Dimension Assignment

Dimensions are named `DIM_0` through `DIM_{N-1}`. Each is assigned a strategy from the mix by distributing weights across the dimension count. Assignment is deterministic given `(dim_count, strategy_mix)`.

RANGE dimensions generate two columns: `DIM_k_MIN` and `DIM_k_MAX`.

### 4.4 Value Generation

All values generated from `random.Random(seed)` for reproducibility.

| Strategy | Rule values | Context values |
|----------|-------------|----------------|
| EXACT | Choice from pool of `max(5, rule_count // 3)` string values | Sampled from the rule value pool |
| NOT_EQUAL | Same as EXACT | Sampled from pool |
| RANGE | `(min, max)` pairs from `[0, 1000)`, gap 10–100 | Integer sampled to hit ~hit_rate of ranges |
| GREATER_THAN | Thresholds from `[100, 900)` | Integer near the median threshold |
| LESS_THAN | Thresholds from `[100, 900)` | Integer near the median threshold |
| PREFIX | Strings like `"PFX_001"`, `"PFX_002"` with shared prefixes | String starting with a common prefix |
| SUFFIX | Strings with shared suffixes | String ending with a common suffix |
| CONTAINS | Strings with embedded substrings | String containing a common substring |
| REGEX | Simple patterns: `^[A-Z]{2}_\d+`, `^ITEM_\d{3}$`, etc. | String matching ~hit_rate of patterns |
| SET_MEMBERSHIP | Lists of 2–5 values from a value pool | Value from the pool |
| SET_EXCLUSION | Lists of 2–5 values from a value pool | Value from the pool |

Unknown values (`UNKNOWN` / `UNKNOWN_NUMERIC`) are inserted at ~15% density across all rule columns.

### 4.5 Context Hit Rate

The context is constructed to match approximately 10–20% of rules. This is achieved by sampling context values from the rule value distribution (not uniformly random), biased toward values that appear in multiple rules.

---

## 5. Benchmark Tests (`tests/test_benchmarks.py`)

### 5.1 Scaling Matrix

```python
RULE_COUNTS = [10, 100, 1000]
DIM_COUNTS = [3, 5, 7]

@pytest.mark.benchmark(group="scaling")
@pytest.mark.parametrize("rule_count", RULE_COUNTS, ids=["10r", "100r", "1000r"])
@pytest.mark.parametrize("dim_count", DIM_COUNTS, ids=["3d", "5d", "7d"])
@pytest.mark.parametrize("backend_name", ALL_BACKENDS)
def test_scaling_matrix(benchmark, rule_count, dim_count, backend_name):
```

**Timed operation:** `engine.apply(context)` only. Engine construction happens in the benchmark setup (pedantic mode).

**63 combinations** = 3 rule counts × 3 dim counts × 7 backends.

### 5.2 Per-Strategy Isolation

```python
@pytest.mark.benchmark(group="strategy")
@pytest.mark.parametrize("strategy", list(MatchStrategy))
@pytest.mark.parametrize("backend_name", ALL_BACKENDS)
def test_strategy_isolation(benchmark, strategy, backend_name):
```

Fixed at 100 rules, 5 dimensions, all dimensions use the same strategy.

SET_MEMBERSHIP and SET_EXCLUSION skip non-list-capable backends via `pytest.skip()`. SUFFIX and NOT_EQUAL are included (all backends support them).

**Up to 77 combinations** = 11 strategies × 7 backends, minus skips.

### 5.3 pytest-benchmark Configuration

- `--benchmark-warmup=on`
- `--benchmark-min-rounds=5`
- `--benchmark-group-by=group,param:backend_name`
- `--benchmark-columns=min,max,mean,stddev,rounds`

All benchmarks are marked with `@pytest.mark.benchmark` so `--benchmark-only` (the existing `test-perf` command) collects only these.

### 5.4 Backend Import

`ALL_BACKENDS` is imported from the existing `conftest.py`. The `build_engine` factory in `benchmark_data.py` reuses the same backend-construction logic as the existing test fixtures (polars, pandas, narwhals wrapping, ibis table creation).

---

## 6. Hatch Commands

Add to the `[envs.test.scripts]` section in `hatch.toml`:

```toml
test-perf-save = "pytest --benchmark-only --benchmark-save=baseline {args}"
```

Existing commands unchanged:
- `test-perf` = `pytest --benchmark-only` (terminal output only)
- `test-perf-target` = `pytest --benchmark-only {args}`

---

## 7. Terminal Output

With `--benchmark-group-by=group,param:backend_name`, the terminal shows:

```
------------------------ benchmark: scaling / polars -------------------------
Name                    Min        Max       Mean     StdDev   Rounds
test_scaling[10r-3d]    0.12ms     0.18ms    0.14ms   0.02ms   5
test_scaling[10r-5d]    0.18ms     0.25ms    0.20ms   0.03ms   5
test_scaling[100r-3d]   1.10ms     1.30ms    1.18ms   0.07ms   5
...

--------------------- benchmark: scaling / ibis-duckdb ----------------------
Name                    Min        Max       Mean     StdDev   Rounds
test_scaling[10r-3d]    0.45ms     0.60ms    0.51ms   0.05ms   5
...

--------------------- benchmark: strategy / polars --------------------------
Name                       Min        Max       Mean     StdDev   Rounds
test_strategy[EXACT]       0.90ms     1.10ms    0.98ms   0.06ms   5
test_strategy[RANGE]       1.20ms     1.50ms    1.32ms   0.10ms   5
test_strategy[REGEX]       3.10ms     3.80ms    3.40ms   0.25ms   5
...
```

---

## 8. Design Decisions

**Why exclude engine construction from the timed loop?** Construction is a one-time cost per rule-set version. The hot path in production is `apply(context)` called many times against a pre-built engine. Benchmarking construction separately would be useful but is a different question.

**Why 1,000 max rules instead of 10,000+?** The 3×3 matrix already produces 63 benchmarks across backends. Adding a 10k tier would increase runtime significantly for backends like ibis-sqlite. 1,000 rules is enough to show scaling trends; larger tiers can be added later via the `{args}` passthrough.

**Why seeded RNG instead of fixed data files?** Reproducibility without storing large fixtures in git. The seed ensures identical data across runs; changing the seed lets you test sensitivity to data distribution.

**Why skip SET strategies on non-list backends instead of xfail?** These aren't failures — the backend genuinely doesn't support list columns. `pytest.skip()` is the correct semantic, and it keeps the benchmark output clean.
