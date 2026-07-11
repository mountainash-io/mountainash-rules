# Accumulator Correctness: Overflow Guard, Half-Open Ranges, Inclusivity

> **Status:** APPROVED FOR PLANNING
> **Date:** 2026-07-12
> **Source:** Architectural review 2026-07-12 §1.4, §1.5; backlog card
> `mountainash-central/01.principles/mountainash-rules/h.backlog/accumulator-correctness-gaps.md`
> **Principles:** `multiset-safety-as-default` (prime products are the combination
> identity — silent corruption of them breaks dominance filtering and provenance),
> `build-scoped-primes`.

## Problem

Three verified correctness defects in the accumulator build phase
(`accumulator_engine.py`, `accumulator_compiler.py`):

1. **Unchecked prime-product overflow.** `_expand_level` computes
   `__prime_product.mul(__prime_rhs)` with no guard. `primes.checked_multiply`
   exists but is never called. The product of the first 15 primes is
   614,889,782,588,491,410 (fits int64); the first 16 overflow. Any partition
   containing a mutually-compatible clique of ~16 rules (fewer when primes are
   larger) silently wraps the int64 product. A wrapped product corrupts guard 2
   (`__prime_product % __prime_rhs`), the frontier dominance filter, and all
   provenance decoding — with no error raised.

2. **Half-open ranges are wrongly incompatible.** `_compatible_range` computes
   `either_sentinel = co_min_s | rhs_min_s` — it inspects only the *min*
   columns. A rule with `[50, <sentinel>]` (meaning "50 or more") has a real
   min and a sentinel max, so `either_sentinel` is False and the overlap test
   compares against the raw sentinel value −999999999, declaring the pair
   incompatible with almost everything. Additionally, a sentinel *min* is
   treated as "whole dimension is don't-care", which wrongly accepts pairs
   whose real max bounds don't overlap. Note `_coalesce_range` already handles
   each bound independently and correctly — only `_compatible_range` and the
   NA flag are wrong.

3. **Inclusivity flags ignored (A3).** `_compatible_range` uses strict
   `lt`/`gt`. With the default `range_min_inclusive=True, range_max_inclusive=True`,
   the rules `[0, 10]` and `[10, 20]` share the point 10 and should be
   compatible (coalescing to the point interval `[10, 10]`), but strict
   comparison declares them incompatible.

4. **RANGE NA flag inspects only min columns.** `compile_coalesce_na_flag`
   sets `co_<dim>_na = (co_min sentinel) AND (rhs_min sentinel)` — a
   combination of two half-open ranges `[<sentinel>, 10]` and `[<sentinel>, 20]`
   is flagged fully-don't-care when it is actually bounded above.

## Design

### Semantics: sentinel bounds are ±∞

A sentinel (`UNKNOWN_NUMERIC`) in `range_min_field` means −∞; in
`range_max_field` means +∞. Each bound is independent. This matches the
filter engine's behaviour, where `t_col(..., unknown=sentinels)` yields
UNKNOWN (0) for the sentinel bound and the rule survives — i.e. the filter
engine already treats a sentinel bound as unconstrained. The accumulator must
agree, because the filter engine is the apply-phase oracle: **compatible(A, B)
must hold iff some context value satisfies both A and B under filter-engine
semantics, and coalesce(A, B) must be exactly the set of contexts matching
both.**

Rule-side don't-care is expressed only with `UNKNOWN` / `UNKNOWN_NUMERIC`;
`NOT_SET*` sentinels remain context-side only (unchanged, but now documented
in the module docstring).

### Fix 1: `_compatible_range` — sentinel-aware, inclusivity-aware overlap

Two effective intervals overlap iff each one's lower bound is below the
other's upper bound, where sentinel bounds auto-satisfy their side:

```
low_ok  = co_min_s  OR rhs_max_s OR cmp_low(co_min,  rhs_max)
high_ok = co_max_s  OR rhs_min_s OR cmp_high(co_max, rhs_min)
compatible = low_ok AND high_ok
```

`cmp_low`/`cmp_high` are chosen at compile time from the dimension's
inclusivity flags (both rows share the dimension, so flags are symmetric):
touching endpoints count as overlap iff **both** the max side and the min
side are inclusive:

- `cmp_low = le` if `range_min_inclusive and range_max_inclusive`, else `lt`
- `cmp_high = ge` if the same condition, else `gt`

(Mixed inclusivity, e.g. `[a, b)` vs `[b, c)`: the shared point b is excluded
by the first interval's max, so strict comparison is correct — the single
`and` condition captures all four flag combinations.)

### Fix 2: NA flag — all four sentinel checks

```
co_<dim>_na = (co_min_s AND rhs_min_s) AND (co_max_s AND rhs_max_s)
```

i.e. the coalesced interval is fully don't-care only when both mins and both
maxes are sentinels. (Equivalent to computing it from the coalesced columns,
but computed pre-coalesce to keep the single `with_columns` pass.)

`_coalesce_range` is already correct under the ±∞ reading (each bound
handled independently with per-side sentinel propagation) — no change, but it
gains the enumeration tests below.

### Fix 3: Overflow guard — exact, cheap, two-tier

**Tier 1 — free pass for safe partitions.** In `build()`, immediately after
assigning primes, compute `math.prod(primes)` in Python arbitrary-precision
ints. If the product of *all* assigned primes ≤ `_INT64_MAX`, no combination
can ever overflow: skip all per-level guards. This covers the common case
(small partitions) at zero per-level cost.

**Tier 2 — per-level screen, exact verification only when suspect.** For
partitions that fail tier 1, at each `_expand_level` after the compatibility
filter (the level already materialises `count_rows()`, so an extra aggregate
is marginal):

1. Screen: fetch `max(__prime_product)` and `max(__prime_rhs)` from the
   filtered frame (one aggregate select). If
   `max_pp * max_prhs <= _INT64_MAX` in Python ints, the level is safe —
   proceed.
2. Verify: otherwise, materialise just the two columns
   (`select(__prime_product, __prime_rhs).to_polars()`) and run
   `checked_multiply` per row. If any row overflows, raise
   `LatticeWidthExceededError`.

The screen is conservative-but-cheap; the verification is exact, so a level
where only non-maximal rows combine never raises spuriously. This finally
wires in `primes.checked_multiply`.

**New exception**, in `primes.py`:

```python
class LatticeWidthExceededError(OverflowError):
    """A partition contains a compatible clique too large for int64 prime products."""
```

Raised with a message reporting the partition key, the level (= clique size
`level + 1`, since level 0 holds singletons), and remediation: split the partition with a CONTEXT_KEY
dimension, or reduce the mutually-compatible rule clique. Failing fast and
loud is the designed behaviour — per `multiset-safety-as-default`, we do not
fall back to a lossy encoding silently. (Alternative encodings for deep
lattices — e.g. sorted-rule-index arrays — were considered and deferred: they
change the dominance-filter join and the provenance API, and no current
workload needs cliques > 15.)

### Approaches considered for the overflow guard

- **A. Per-row expression guard in the backend** (`pp > INT64_MAX // p_rhs`
  as a filter): rejected — requires integer floordiv in `ma`, and a float
  `div().cast(int)` approximation is unsound within ~512 of the boundary
  (float64 cannot represent `_INT64_MAX` exactly).
- **B. Static depth cap from sorted primes**: rejected as the primary guard —
  a cap derived from the smallest assigned primes is conservative and would
  reject valid builds whose deep combinations use only small primes mixed
  with early termination. Kept as documentation (worst-case clique ≈ 15).
- **C. Two-tier exact check (chosen)**: exact, backend-agnostic (aggregates +
  a two-column materialisation only on suspect levels), zero cost for safe
  partitions.

## Testing (TDD)

All tests in `tests/test_accumulator_correctness.py`, written RED first.

1. **Overflow**: build a partition of 16 rules that are all mutually
   compatible (all-sentinel constraint dimensions) → assert
   `LatticeWidthExceededError` raised, message contains level and partition
   info. 15 mutually-compatible rules → builds successfully and the deepest
   combination's `__prime_product` equals `math.prod(primes[:15])`.
2. **Tier-1 fast path**: partition whose total prime product fits int64 →
   spy/flag that no per-level verification ran (assert via a counter on the
   engine or by monkeypatching `checked_multiply`).
3. **Half-open ranges**: `[50, s]` vs `[60, s]` compatible, coalesce
   `[60, s]`; `[50, s]` vs `[s, 40]` incompatible; `[s, 10]` vs `[s, 20]`
   coalesce `[s, 10]` with `na=0`.
4. **Inclusivity**: `[0,10]` + `[10,20]` inclusive → compatible, coalesce
   `[10,10]`; same with `range_max_inclusive=False` → incompatible.
5. **Exhaustive enumeration oracle** (no new dependency; deterministic
   parametrisation instead of hypothesis): for every pair of intervals with
   bounds drawn from `{sentinel, 0, 5, 10}` × both inclusivity settings,
   assert the three-way equivalence:
   `compatible(A, B)` ⟺ coalesced interval non-empty ⟺ ∃ context value in
   `{-1, 0, 2, 5, 7, 10, 11}` matching both A and B through
   `ExpressionRulesEngine` (the apply-phase oracle).
6. **NA flag**: combinations of half-open ranges assert `co_<dim>_na`
   reflects all four bounds.

## Files touched

- `src/mountainash_rules/accumulator_compiler.py` — `_compatible_range`,
  `compile_coalesce_na_flag`.
- `src/mountainash_rules/accumulator_engine.py` — tier-1 check in `build()`,
  tier-2 screen/verify in `_expand_level` (which gains the level's
  safe/unsafe flag as a parameter or engine attribute).
- `src/mountainash_rules/primes.py` — `LatticeWidthExceededError`;
  `checked_multiply` unchanged but now exercised.
- `tests/test_accumulator_correctness.py` — new.

## Out of scope

- Alternative combination encodings for cliques > 15 (documented deferral).
- Accumulator support for strategies beyond EXACT/RANGE/GT/LT.
- `NOT_SET_NUMERIC` appearing rule-side (remains undefined behaviour,
  documented).
