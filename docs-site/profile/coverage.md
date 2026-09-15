# Package Profile Coverage Report

Generated: 2026-09-02 (full refresh); revalidated 2026-09-12 (candidate revalidation, no source change)
Source hash: 94659bb0c096485c87d329f09e944577427f129f (previous: 7d0e3dcb747949bb2d7172a34135a7872b2ed55f)
Package root: src/mountainash_rules/
Refresh type (2026-09-02): **full refresh** — package renamed (`mountainash_utils_rules` → `mountainash_rules`) and
reorganised into `core/` + `engines/{filter,accumulator}/` (commit `23a29d6`), triggering every condition in
`references/update-algorithm.md` ("Full Refresh Triggers"): package roots changed, package structure changed,
and effectively all module paths changed relative to the previous profile.

## Candidate Revalidation (2026-09-12)

This pass established the pilot candidate source basis for `PILOT/docs-site/profile/`. `git diff --stat
7d0e3dcb747949bb2d7172a34135a7872b2ed55f..94659bb0c096485c87d329f09e944577427f129f -- src/mountainash_rules`
is empty, and the `src/mountainash_rules` git tree object (`9723c20ae6b9933ad8c2259cc0ffb8cf79fc3364`) is
identical at both revisions: no package source changed between the last full-refresh hash and the current
candidate HEAD. No module was reclassified, re-scanned, or had its `source_hash` advanced as a result.

What this pass actually changed, backed by the unchanged source above (no new source evidence invented, no
`manual` field touched):

- Added the required `documentation_plan` array to all five facets.
- Added `recommended_docs`/`evidence` to every existing concept in `users`, `maintainers`, `contributors`, and
  `backend-architecture` (32 concepts), citing already-established module evidence, README.md/CLAUDE.md
  sections, `docs/user-quickstart.md` sections, and `docs/superpowers/specs/*.md` design docs.
- Added a `concepts` array to `facets/broader-hype.json` (previously absent — a required-field gap, not a
  missing-`documentation_plan`-only gap), covering all 7 `what_it_makes_possible` capabilities against the
  facet's existing 7 featured modules.
- Corrected audience-membership gaps so every featured/concept module in a facet actually declares that
  audience: `backend-architecture` was missing membership on `core/result` (used by "Backend-agnostic
  protocol via mountainash.relations") and `core/dimension` (used by "Shared dimension model");
  `broader-hype` had **no** module carrying `broader-hype` membership at all, so all 7 of its featured
  modules gained it; `contributors` was missing membership on `engines/accumulator/aggregate` (used by
  "Adding a new aggregate operation"). Each addition is backed by that module's own pre-existing
  evidence/lifecycle_notes and the fact that a facet already referenced it without matching membership.


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

All 21 module profiles carry `source_hash: 7d0e3dcb747949bb2d7172a34135a7872b2ed55f`, which now differs from
the manifest's `current_hash` (`94659bb0c096485c87d329f09e944577427f129f`) recorded on 2026-09-12. This is a
**verified-unchanged stale marker, not a drift risk**: see "Candidate Revalidation (2026-09-12)" above — the
`src/mountainash_rules` git tree is byte-identical between the two revisions, so no module's classification,
evidence, or `manual` object is out of date. A future refresh that advances past `94659bb` on a revision that
actually touches `src/mountainash_rules` should re-run discovery rather than assume this precedent still
holds.


## Facets Without User-Facing Module

None.
