# Accumulator Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the accumulator's three verified correctness defects — unchecked prime-product overflow, half-open-range incompatibility, ignored inclusivity flags — plus the min-only NA flags, per `docs/superpowers/specs/2026-07-12-accumulator-correctness-design.md`.

**Architecture:** All range semantics changes live in `AccumulatorCompiler` expression builders (sentinel bound = ±∞, inclusivity chosen at compile time); the overflow guard is a two-tier exact check in `AccumulatorEngine.build()`/`_expand_level` backed by a new exception in `primes.py`. The filter engine is the correctness oracle for an exhaustive enumeration test.

**Tech Stack:** Python 3.12, mountainash expressions/relations, polars test fixtures, pytest via hatch.

## Global Constraints

- Backend-agnostic engine core: touch DataFrames only via `mountainash.relations.relation()` / `mountainash.expressions` (`ma.*`); the single sanctioned materialisation is the overflow verification's two-column `to_polars()`.
- Ternary encoding unchanged: 1 match / 0 unknown / −1 non-match.
- Rule-side don't-care sentinel is `UNKNOWN_NUMERIC` (−999999999) only.
- Test commands: `hatch run test:test-target tests/<file>::<node>` for single tests, `hatch run test:test-quick` for the suite.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `LatticeWidthExceededError` in primes.py

**Files:**
- Modify: `src/mountainash_rules/primes.py`
- Test: `tests/test_primes.py`

**Interfaces:**
- Produces: `class LatticeWidthExceededError(OverflowError)` importable from `mountainash_rules.primes`. Task 5 raises it; `checked_multiply` itself is unchanged (still raises plain `OverflowError`).

- [ ] **Step 1: Write the failing test** (append to `tests/test_primes.py`)

```python
from mountainash_rules.primes import LatticeWidthExceededError


class TestLatticeWidthExceededError:
    def test_is_an_overflow_error(self):
        assert issubclass(LatticeWidthExceededError, OverflowError)

    def test_carries_message(self):
        err = LatticeWidthExceededError("partition ('AU',) level 15")
        assert "partition" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch run test:test-target tests/test_primes.py::TestLatticeWidthExceededError -v`
Expected: FAIL — `ImportError: cannot import name 'LatticeWidthExceededError'`

- [ ] **Step 3: Write minimal implementation** (append to `src/mountainash_rules/primes.py`)

```python
class LatticeWidthExceededError(OverflowError):
    """A partition contains a compatible rule clique too large for int64 prime products.

    Raised by the accumulator build phase when combining one more rule would
    overflow the int64 ``__prime_product`` combination identity. Remediation:
    split the partition with a CONTEXT_KEY dimension, or reduce the size of
    the mutually compatible rule clique.
    """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch run test:test-target tests/test_primes.py::TestLatticeWidthExceededError -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/primes.py tests/test_primes.py
git commit -m "feat(accumulator): add LatticeWidthExceededError

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Sentinel-aware, inclusivity-aware `_compatible_range`

**Files:**
- Modify: `src/mountainash_rules/accumulator_compiler.py` (`_compatible_range`, module docstring)
- Test: `tests/test_accumulator_correctness.py` (create)

**Interfaces:**
- Consumes: existing `_range_sentinel_checks(dim)` returning `(co_min_s, co_max_s, rhs_min_s, rhs_max_s)`.
- Produces: `compile_compatible(dim)` for RANGE now honours sentinel-as-±∞ per bound and `range_min_inclusive`/`range_max_inclusive`. Tasks 3–6 build on this semantics.

- [ ] **Step 1: Write the failing tests** (create `tests/test_accumulator_correctness.py`)

```python
"""Correctness tests for accumulator range semantics, NA flags, and overflow."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.accumulator_engine import AccumulatorEngine
from mountainash_rules.constants import UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata

S = UNKNOWN_NUMERIC


def _range_metadata(min_inc=True, max_inc=True):
    return DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="x", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="x_min", range_max_field="x_max",
            range_min_inclusive=min_inc, range_max_inclusive=max_inc,
        ),
    ])


def _build_pair(a, b, min_inc=True, max_inc=True):
    """Build a 2-rule lattice; the pair combined iff __prime_product 6 exists."""
    engine = AccumulatorEngine(dimension_metadata=_range_metadata(min_inc, max_inc))
    rules = pl.DataFrame({
        "rule_name": ["A", "B"],
        "x_min": [a[0], b[0]],
        "x_max": [a[1], b[1]],
    })
    lattice = engine.build(rules)
    rows = relation(lattice.combinations).to_dict()
    return set(rows["__prime_product"]), rows


class TestHalfOpenRangeCompatibility:
    def test_two_lower_bounded_ranges_combine(self):
        # [50, +inf) and [60, +inf) overlap on [60, +inf)
        products, _ = _build_pair((50, S), (60, S))
        assert 6 in products

    def test_lower_bounded_vs_disjoint_upper_bounded_do_not_combine(self):
        # [50, +inf) and (-inf, 40] are disjoint
        products, _ = _build_pair((50, S), (S, 40))
        assert 6 not in products

    def test_two_upper_bounded_ranges_combine_to_tighter_max(self):
        products, rows = _build_pair((S, 10), (S, 20))
        assert 6 in products
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        i = by_product[6]
        assert rows["co_x_min"][i] == S
        assert rows["co_x_max"][i] == 10

    def test_sentinel_min_does_not_mean_whole_dimension_wildcard(self):
        # (-inf, 5] vs [7, 9]: disjoint even though one min is sentinel
        products, _ = _build_pair((S, 5), (7, 9))
        assert 6 not in products


class TestInclusivityFlags:
    def test_touching_inclusive_ranges_combine_to_point(self):
        products, rows = _build_pair((0, 10), (10, 20))
        assert 6 in products
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        i = by_product[6]
        assert rows["co_x_min"][i] == 10
        assert rows["co_x_max"][i] == 10

    def test_touching_exclusive_max_ranges_do_not_combine(self):
        products, _ = _build_pair((0, 10), (10, 20), max_inc=False)
        assert 6 not in products
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py -v`
Expected: `test_two_lower_bounded_ranges_combine` FAILS (6 not built — sentinel max compared raw); `test_sentinel_min_does_not_mean_whole_dimension_wildcard` FAILS (6 wrongly built); `test_touching_inclusive_ranges_combine_to_point` FAILS (strict lt/gt). `test_two_upper_bounded_ranges_combine_to_tighter_max` may partially pass — record which assertions fail.

- [ ] **Step 3: Implement** (replace `_compatible_range` in `src/mountainash_rules/accumulator_compiler.py`)

```python
    def _compatible_range(self, dim: Dimension) -> BaseExpressionAPI:
        """True when the two effective intervals overlap.

        A sentinel min is -inf, a sentinel max is +inf, each bound
        independently. Touching endpoints overlap iff both the min and the
        max side are inclusive (covers all four flag combinations).
        """
        co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
        co_min = ma.col(f"co_{dim.range_min_field}")
        co_max = ma.col(f"co_{dim.range_max_field}")
        rhs_min = ma.col(f"{dim.range_min_field}_rhs")
        rhs_max = ma.col(f"{dim.range_max_field}_rhs")

        touch_overlaps = dim.range_min_inclusive and dim.range_max_inclusive
        if touch_overlaps:
            low = co_min.le(rhs_max)
            high = co_max.ge(rhs_min)
        else:
            low = co_min.lt(rhs_max)
            high = co_max.gt(rhs_min)

        low_ok = co_min_s.__or__(rhs_max_s).__or__(low)
        high_ok = co_max_s.__or__(rhs_min_s).__or__(high)
        return low_ok.__and__(high_ok)
```

Also extend the class docstring of `AccumulatorCompiler` with:

```python
    Sentinel semantics: a rule-side don't-care bound is UNKNOWN_NUMERIC only
    (sentinel min = -inf, sentinel max = +inf). NOT_SET_NUMERIC is a
    context-side sentinel; the filter engine currently also tolerates it
    rule-side (NUMERIC_SENTINELS contains both), but the accumulator does
    not recognise it — rule tables fed to build() must use UNKNOWN_NUMERIC.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Run the accumulator suite for regressions**

Run: `hatch run test:test-target tests/test_accumulator_engine.py tests/test_accumulator_compiler.py tests/test_accumulator_edge_cases.py tests/test_lattice_properties.py -v`
Expected: PASS (the worked example uses overlapping closed ranges; if any existing test asserted the old buggy behaviour, fix the *test* only after confirming against the spec that the old expectation was wrong, and say so in the commit message).

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/accumulator_compiler.py tests/test_accumulator_correctness.py
git commit -m "fix(accumulator): sentinel-as-infinity and inclusivity-aware range compatibility

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: RANGE NA flag checks all four sentinels (compiler)

**Files:**
- Modify: `src/mountainash_rules/accumulator_compiler.py` (`compile_coalesce_na_flag`)
- Test: `tests/test_accumulator_correctness.py`

**Interfaces:**
- Produces: `co_<dim>_na = (co_min_s AND rhs_min_s) AND (co_max_s AND rhs_max_s)` for RANGE dims. Task 4 applies the matching fix at the anchor.

- [ ] **Step 1: Write the failing test** (append to `tests/test_accumulator_correctness.py`)

```python
class TestRangeNaFlags:
    def test_combined_upper_bounded_ranges_not_flagged_na(self):
        # (-inf, 10] + (-inf, 20]: bounded above, so NOT fully don't-care
        products, rows = _build_pair((S, 10), (S, 20))
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        assert rows["co_x_na"][by_product[6]] == 0

    def test_combined_all_sentinel_ranges_flagged_na(self):
        products, rows = _build_pair((S, S), (S, S))
        by_product = {p: i for i, p in enumerate(rows["__prime_product"])}
        assert rows["co_x_na"][by_product[6]] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py::TestRangeNaFlags -v`
Expected: `test_combined_upper_bounded_ranges_not_flagged_na` FAILS with `co_x_na == 1` (min-only check).

- [ ] **Step 3: Implement** (replace the RANGE branch of `compile_coalesce_na_flag`)

```python
    def compile_coalesce_na_flag(self, dim: Dimension) -> BaseExpressionAPI:
        """Expression for the coalesced NA flag (1 = both sides don't-care)."""
        if dim.match_strategy == MatchStrategy.RANGE:
            co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
            all_sentinel = (
                co_min_s.__and__(rhs_min_s)
                .__and__(co_max_s)
                .__and__(rhs_max_s)
            )
            return all_sentinel.cast(int).alias(f"co_{dim.dimension_name}_na")
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        return co_sentinel.__and__(rhs_sentinel).cast(int).alias(f"co_{field}_na")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py::TestRangeNaFlags -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/accumulator_compiler.py tests/test_accumulator_correctness.py
git commit -m "fix(accumulator): RANGE NA flag inspects all four sentinel bounds

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: RANGE NA flag at the anchor (level 0)

**Files:**
- Modify: `src/mountainash_rules/accumulator_engine.py` (`_create_anchor`, RANGE branch of the NA-flag loop, currently ~lines 214-221)
- Test: `tests/test_accumulator_correctness.py`

**Interfaces:**
- Produces: singleton rows carry `co_<dim>_na = 1` only when BOTH min and max are sentinels. NA columns feed the frontier fingerprint, so this fixes dominance grouping too.

- [ ] **Step 1: Write the failing test** (append to `tests/test_accumulator_correctness.py`)

```python
class TestAnchorNaFlags:
    def test_half_open_singleton_not_flagged_na(self):
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        rules = pl.DataFrame({"rule_name": ["A"], "x_min": [S], "x_max": [10]})
        lattice = engine.build(rules)
        rows = relation(lattice.combinations).to_dict()
        assert rows["co_x_na"][0] == 0

    def test_all_sentinel_singleton_flagged_na(self):
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        rules = pl.DataFrame({"rule_name": ["A"], "x_min": [S], "x_max": [S]})
        lattice = engine.build(rules)
        rows = relation(lattice.combinations).to_dict()
        assert rows["co_x_na"][0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py::TestAnchorNaFlags -v`
Expected: `test_half_open_singleton_not_flagged_na` FAILS (`co_x_na == 1` from the min-only anchor check).

- [ ] **Step 3: Implement** (in `_create_anchor`, replace the RANGE branch of the NA-flag loop)

```python
        for dim in self._constraint_dims:
            if dim.match_strategy == MatchStrategy.RANGE:
                sentinel = UNKNOWN_NUMERIC
                na_exprs.append(
                    ma.col(dim.range_min_field).eq(ma.lit(sentinel))
                    .__and__(ma.col(dim.range_max_field).eq(ma.lit(sentinel)))
                    .cast(int)
                    .alias(f"co_{dim.dimension_name}_na")
                )
            else:
                # (non-RANGE branch unchanged)
```

- [ ] **Step 4: Run tests, then the accumulator suite**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py -v` → PASS
Run: `hatch run test:test-target tests/test_accumulator_engine.py tests/test_accumulator_apply.py tests/test_lattice_properties.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/accumulator_engine.py tests/test_accumulator_correctness.py
git commit -m "fix(accumulator): anchor RANGE NA flag requires both bounds sentinel

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Two-tier overflow guard

**Files:**
- Modify: `src/mountainash_rules/accumulator_engine.py` (`build`, `_expand_level`, new `_check_overflow`; imports)
- Test: `tests/test_accumulator_correctness.py`

**Interfaces:**
- Consumes: `LatticeWidthExceededError`, `checked_multiply`, `_INT64_MAX` from `mountainash_rules.primes` (Task 1).
- Produces: `build()` raises `LatticeWidthExceededError` before any wrapped product can be materialised; partitions whose full prime product fits int64 never pay a per-level cost.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_accumulator_correctness.py`)

```python
import mountainash_rules.accumulator_engine as acc_mod
from mountainash_rules.primes import LatticeWidthExceededError


def _all_wildcard_rules(n):
    """n mutually compatible rules (every bound sentinel)."""
    return pl.DataFrame({
        "rule_name": [f"R{i}" for i in range(n)],
        "x_min": [S] * n,
        "x_max": [S] * n,
    })


class TestOverflowGuard:
    def test_sixteen_rule_clique_raises(self):
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        with pytest.raises(LatticeWidthExceededError) as exc_info:
            engine.build(_all_wildcard_rules(16))
        msg = str(exc_info.value)
        assert "level" in msg
        assert "CONTEXT_KEY" in msg

    def test_fifteen_rule_clique_builds_with_exact_product(self):
        import math
        from mountainash_rules.primes import get_prime
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        lattice = engine.build(_all_wildcard_rules(15))
        rows = relation(lattice.combinations).to_dict()
        expected = math.prod(get_prime(i) for i in range(15))
        assert expected in set(rows["__prime_product"])

    def test_safe_partition_skips_verification(self, monkeypatch):
        calls = []
        real = acc_mod.checked_multiply
        monkeypatch.setattr(
            acc_mod, "checked_multiply",
            lambda a, b: calls.append((a, b)) or real(a, b),
        )
        engine = AccumulatorEngine(dimension_metadata=_range_metadata())
        engine.build(_all_wildcard_rules(5))  # prod(2..11) = 2310, tier-1 safe
        assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py::TestOverflowGuard -v`
Expected: `test_sixteen_rule_clique_raises` FAILS (no error raised — silent wrap); `test_safe_partition_skips_verification` FAILS with `AttributeError: ... has no attribute 'checked_multiply'` (not yet imported into the engine module). Note: the 16-rule build cross-joins heavily; if it is slow, that is acceptable for this one test (~65k max rows at the widest level).

- [ ] **Step 3: Implement**

In `src/mountainash_rules/accumulator_engine.py`, change the primes import and add `math`:

```python
import math

from mountainash_rules.primes import (
    LatticeWidthExceededError,
    _INT64_MAX,
    checked_multiply,
    get_prime,
)
```

In `build()`, after `primes = [get_prime(i) for i in range(n_rules)]`:

```python
        # Tier 1: if the product of ALL assigned primes fits int64, no
        # combination can ever overflow — skip per-level verification.
        overflow_possible = math.prod(primes) > _INT64_MAX
```

Thread it through the loop:

```python
        for level_num in range(1, n_rules):
            new_combos = self._expand_level(
                current_level, rhs_rules, level_num,
                overflow_possible=overflow_possible,
                partition_key=partition_key,
            )
```

In `_expand_level`, change the signature and add the guard immediately after the `count == 0` early return (the level is already materialised by `count_rows()` at that point):

```python
    def _expand_level(
        self,
        current_level: t.Any,
        rhs_rules: t.Any,
        level_num: int,
        overflow_possible: bool = False,
        partition_key: dict[str, t.Any] | None = None,
    ) -> t.Any | None:
        ...
        count = filtered.count_rows()
        if count == 0:
            return None

        if overflow_possible:
            self._check_overflow(filtered, level_num, partition_key)
```

Add the new method after `_expand_level`:

```python
    def _check_overflow(
        self,
        filtered: t.Any,
        level_num: int,
        partition_key: dict[str, t.Any] | None,
    ) -> None:
        """Raise LatticeWidthExceededError if any pending multiply overflows int64.

        Screen with two aggregates (exact Python-int arithmetic on the maxima
        is conservative); only a suspect level pays the exact per-row check,
        which materialises just the two tracking columns.
        """
        maxima = filtered.select(
            ma.col("__prime_product").max().alias("__max_pp"),
            ma.col("__prime_rhs").max().alias("__max_prhs"),
        ).to_dict()
        if maxima["__max_pp"][0] * maxima["__max_prhs"][0] <= _INT64_MAX:
            return
        pairs = filtered.select(
            ma.col("__prime_product"), ma.col("__prime_rhs")
        ).to_polars()
        for pp, prhs in zip(pairs["__prime_product"], pairs["__prime_rhs"]):
            try:
                checked_multiply(pp, prhs)
            except OverflowError as exc:
                raise LatticeWidthExceededError(
                    f"Prime-product overflow at level {level_num} "
                    f"(clique size {level_num + 1}) for partition "
                    f"{partition_key!r}: {exc} "
                    f"Split the partition with a CONTEXT_KEY dimension or "
                    f"reduce the mutually compatible rule clique."
                ) from exc
```

Implementation note: if the relations API does not expose `.max()` on column expressions (check with `python -c "import mountainash.expressions as ma; print(hasattr(ma.col('x'), 'max'))"`), fall back to materialising the two columns unconditionally on unsafe partitions — correctness is identical, only the screen optimisation is lost; leave a `# TODO(mountainash): restore aggregate screen` comment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the quick suite**

Run: `hatch run test:test-quick`
Expected: PASS with the same pre-existing skips/xfails as before this task (30 skipped / 30 xfailed at time of writing).

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/accumulator_engine.py tests/test_accumulator_correctness.py
git commit -m "feat(accumulator): two-tier exact overflow guard on prime products

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Exhaustive enumeration oracle

**Files:**
- Test: `tests/test_accumulator_correctness.py`

**Interfaces:**
- Consumes: everything from Tasks 2–4; `ExpressionRulesEngine` as the apply-phase oracle.
- Produces: the three-way equivalence guarantee — `compatible ⟺ coalesce non-empty ⟺ joint filter-engine match` — over all interval pairs with bounds in `{sentinel, 0, 5, 10}` × all four inclusivity combinations.

- [ ] **Step 1: Write the test** (append to `tests/test_accumulator_correctness.py`)

```python
import itertools

from mountainash_rules.engine import ExpressionRulesEngine

BOUNDS = [S, 0, 5, 10]
PROBES = [-1, 0, 2, 5, 7, 10, 11]
FLAG_COMBOS = [(True, True), (True, False), (False, True), (False, False)]


def _valid_interval(lo, hi):
    return lo == S or hi == S or lo <= hi


def _coalesced_nonempty(a, b, min_inc, max_inc):
    """Python-side oracle: intersection of a and b is non-empty."""
    finite_los = [v for v in (a[0], b[0]) if v != S]
    finite_his = [v for v in (a[1], b[1]) if v != S]
    if not finite_los or not finite_his:
        return True
    lo, hi = max(finite_los), min(finite_his)
    return lo <= hi if (min_inc and max_inc) else lo < hi


class TestThreeWayEquivalence:
    @pytest.mark.parametrize("min_inc,max_inc", FLAG_COMBOS)
    def test_compatible_iff_nonempty_iff_joint_match(self, min_inc, max_inc):
        metadata = _range_metadata(min_inc, max_inc)
        intervals = [
            (lo, hi)
            for lo, hi in itertools.product(BOUNDS, BOUNDS)
            if _valid_interval(lo, hi)
        ]
        for a, b in itertools.combinations_with_replacement(intervals, 2):
            products, _ = _build_pair(a, b, min_inc, max_inc)
            combined = 6 in products

            nonempty = _coalesced_nonempty(a, b, min_inc, max_inc)

            rules = pl.DataFrame({
                "rule_name": ["A", "B"],
                "x_min": [a[0], b[0]],
                "x_max": [a[1], b[1]],
            })
            filter_engine = ExpressionRulesEngine(
                rules=rules, dimension_metadata=metadata
            )
            joint = any(
                filter_engine.evaluate({"x": v}).count == 2 for v in PROBES
            )

            label = f"A={a} B={b} min_inc={min_inc} max_inc={max_inc}"
            assert combined == nonempty, f"lattice vs interval oracle: {label}"
            assert combined == joint, f"lattice vs filter-engine oracle: {label}"
```

Notes for the implementer:
- Degenerate intervals with finite `min > max` are excluded by `_valid_interval` — they are invalid rules, out of scope per the spec.
- The probe set covers every gap in the bound lattice: interiors 2 and 7, endpoints 0/5/10, and the unbounded tails −1 and 11.
- This test is O(4 × ~120 pairs × small builds) — expect a few seconds, not minutes. If it exceeds ~60s, mark it `@pytest.mark.slow` per the repo's marker conventions rather than shrinking the domain.

- [ ] **Step 2: Run the test**

Run: `hatch run test:test-target tests/test_accumulator_correctness.py::TestThreeWayEquivalence -v`
Expected: PASS (this is a verification test over already-implemented behaviour; if any pair fails, that is a real semantics bug in Tasks 2–4 — debug the expression, do not weaken the oracle).

- [ ] **Step 3: Run the full quick suite one final time**

Run: `hatch run test:test-quick`
Expected: PASS; note final counts in the commit message.

- [ ] **Step 4: Commit**

```bash
git add tests/test_accumulator_correctness.py
git commit -m "test(accumulator): exhaustive three-way equivalence oracle for range semantics

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
