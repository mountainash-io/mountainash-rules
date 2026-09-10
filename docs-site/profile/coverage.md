# Package Profile Coverage Report

Generated: 2026-09-02
Source hash: 7d0e3dcb747949bb2d7172a34135a7872b2ed55f (previous: 41d584652aa29b09762aa1140ae64fb640473940)
Package root: src/mountainash_rules/
Refresh type: **full refresh** — package renamed (`mountainash_utils_rules` → `mountainash_rules`) and
reorganised into `core/` + `engines/{filter,accumulator}/` (commit `23a29d6`), triggering every condition in
`references/update-algorithm.md` ("Full Refresh Triggers"): package roots changed, package structure changed,
and effectively all module paths changed relative to the previous profile.

## Summary

| Metric | Count |
|--------|-------|
| Modules discovered | 21 |
| Modules profiled | 21 |
| Modules ignored | 0 |
| Coverage gaps | 0 |
| Facets written | 5 |
| Orphaned profiles removed | 14 (all pre-reorganisation flat-layout profiles) |

## Module Coverage

| Module | Profile | Doc Priority | Test Coverage |
|--------|---------|--------------|----------------|
| `__init__` | ✅ | essential | tests/test_public_api.py |
| `__version__` | ✅ | skip-with-reason | — |
| `core/__init__` | ✅ | skip-with-reason | — (empty namespace marker) |
| `core/constants` | ✅ | essential | tests/core/test_compiler.py, tests/core/test_dimension.py |
| `core/dimension` | ✅ | essential | tests/core/test_dimension.py, tests/core/test_dimension_serialization.py |
| `core/context` | ✅ | internal-note | tests/core/test_context.py |
| `core/compiler` | ✅ | useful | tests/core/test_compiler.py |
| `core/set_wildcard` | ✅ | useful | tests/core/test_set_wildcard.py |
| `core/result` | ✅ | essential | tests/core/test_result.py, tests/filter/test_explain.py |
| `core/hit_policy` | ✅ | useful | tests/core/test_hit_policy.py |
| `core/batch_result` | ✅ | useful | tests/filter/test_batch_evaluation.py |
| `engines/__init__` | ✅ | skip-with-reason | — (empty namespace marker) |
| `engines/filter/__init__` | ✅ | skip-with-reason | — (empty namespace marker) |
| `engines/filter/engine` | ✅ | essential | tests/filter/test_engine.py, test_batch_evaluation.py, test_explain.py, test_integration.py, test_upstream_regressions.py, test_backend_purity.py |
| `engines/accumulator/__init__` | ✅ | skip-with-reason | — (empty namespace marker) |
| `engines/accumulator/aggregate` | ✅ | useful | tests/accumulator/test_aggregate.py |
| `engines/accumulator/compiler` | ✅ | useful | tests/accumulator/test_compiler.py |
| `engines/accumulator/engine` | ✅ | essential | tests/accumulator/test_engine.py, test_apply.py, test_backends.py, test_correctness.py, test_edge_cases.py, benchmarks/test_accumulator_benchmarks.py |
| `engines/accumulator/result` | ✅ | useful | tests/accumulator/test_result.py |
| `engines/accumulator/lattice` | ✅ | essential | tests/accumulator/test_lattice.py, test_lattice_properties.py |
| `engines/accumulator/primes` | ✅ | internal-note | tests/accumulator/test_primes.py |

## What Changed Since the Prior Profile (hash `41d5846`)

New modules (did not exist before):
- `core/hit_policy` — hit-policy selection layer (collect/unique/first/priority/any/rule_order)
- `core/batch_result` — `BatchRuleResult` for `evaluate_batch()`
- `core/set_wildcard` — shared in-band sentinel helpers for set-dimension wildcards

New capabilities on existing modules:
- `engines/filter/engine`: `evaluate_batch()`, `explain()`
- `engines/accumulator/aggregate`: `min`/`max`/`product` operations (previously sum-only)
- `engines/accumulator/lattice`: `Lattice.save`/`load` snapshot persistence, `LatticeIndex` ternary partition
  routing, `Lattice.is_composed`
- `engines/accumulator/primes`: `MAX_RULES_PER_PARTITION` raised to an explicit 10,000 (was an implicit ~500)
- `core/constants`: `MatchStrategy` grew from 11 to 12 members (`EXACT_KEY` added; `REGEX` split from
  `CONTEXT_REGEX`); `DataType` enum added with temporal (date/datetime) sentinels; bool dimensions with
  null-as-don't-care added

Structural change:
- Package renamed `mountainash_utils_rules` → `mountainash_rules`; flat module layout reorganised into
  `core/` (backend-agnostic building blocks) + `engines/filter/` + `engines/accumulator/`. Deprecation shims
  for the pre-reorganisation paths were added then removed (2026-07-16) — no downstream consumer other than
  `mountainash-rules-babel` imports below the package root, and it already used root-level imports only.

## Open Questions Resolved Since the Prior Profile

The prior profile (`41d5846`) recorded seven open questions. Current status:

1. ~~Aggregate.operation: only 'sum' implemented~~ — **Resolved.** `min`/`max`/`product` all implemented.
2. REGEX pattern scope — **Unchanged, now clarified.** `REGEX` (per-row pattern column) is now distinct from
   `CONTEXT_REGEX` (metadata-level literal pattern); both exist as separate, correctly-scoped strategies.
3. SET_MEMBERSHIP / SET_EXCLUSION backend parity — **Still a caveat.** Narwhals list-op support tracked
   upstream as `mountainash#89`; documented in CLAUDE.md and xfailed in `tests/conftest.py`.
4. ~~AccumulatorEngine: string/set strategies unsupported~~ — **Resolved.** Set membership/exclusion now
   supported in the accumulator via `core/set_wildcard` + `engines/accumulator/compiler`.
5. ~~Prime table limit ~500 rows~~ — **Resolved/raised.** `MAX_RULES_PER_PARTITION = 10_000`, an explicit
   documented constant distinct from the intrinsic ~15-prime combination-width bound.
6. ~~Lattice persistence: none~~ — **Resolved.** `Lattice.save`/`load` (parquet + manifest.yaml).
7. Accumulator multi-lattice composition — **Superseded.** `LatticeIndex` (ternary partition routing,
   PR #48/#49) addresses the practical case (routing a context to the right one of several lattices); true
   lattice *composition* (merging two lattices' combinations) remains undesigned.

## New Open Questions

1. **Aggregate overflow is unguarded.** Only `__prime_product` combination identity is int64-guarded;
   `product` aggregates can silently overflow the backend's numeric type. Needs explicit documentation before
   the Accumulator Results chapter covers `AggregateOp.PRODUCT`.
2. **Three permanent backend-purity `# allow:` exemptions** (per-row REGEX, empty-build schema seed, snapshot
   parquet read) are each pending a specific upstream `mountainash` capability with no committed timeline —
   document as current, not temporary, limitations.
3. **`LatticeIndex` ambiguity validation cost.** `index(lattices, validate=True, max_witnesses=1_000_000)` runs
   an exhaustive witness-matrix check at load time; the cost/completeness tradeoff of `max_witnesses` needs a
   worked example for the docs.

## Missing Profiles

None.

## Orphaned Profiles

None remaining — 14 pre-reorganisation flat-layout profile files (`init.json`, `version.json`, `constants.json`,
`dimension.json`, `compiler.json`, `engine.json`, `result.json`, `context.json`, `accumulator_engine.json`,
`accumulator_compiler.json`, `accumulator_result.json`, `aggregate.json`, `lattice.json`, `primes.json`) were
removed as part of this full refresh; their content is superseded by the 21 current module profiles.

## Stale Profiles

None — this is a full refresh; all 21 profiles are current at hash `7d0e3dcb747949bb2d7172a34135a7872b2ed55f`.

## Facets Without User-Facing Module

None.
