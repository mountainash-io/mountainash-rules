# Package Layout Reorganisation: core/ + engines/, Public-API Discipline, Test Mirror

**Date:** 2026-07-13
**Status:** Approved (backlog card: `mountainash-central/01.principles/mountainash-rules/h.backlog/package-layout-reorganisation.md`)
**Repos:** mountainash-rules (primary), mountainash-rules-babel (downstream)

## 1. Problem

`src/mountainash_rules/` is a flat directory of 16 modules. Two engines —
the filter engine and the accumulator engine — sit side by side in the
root, distinguished only by an `accumulator_` filename prefix. Shared
infrastructure is interleaved with engine-specific code. The
composition-engine gap list guarantees the accumulator cluster keeps
growing. Downstream, babel deep-imports six private module paths, so any
file move breaks it.

The measured dependency graph is cleanly layered (no cycles), so this is a
file move plus import rewrites, not a refactor. No behaviour changes.

## 2. Target layout

```
src/mountainash_rules/
├── __init__.py                  # sole public surface — exported names unchanged
├── __version__.py
├── core/
│   ├── __init__.py
│   ├── constants.py             # enums, sentinels, ternary values
│   ├── dimension.py             # Dimension / DimensionsMetadata (+ YAML)
│   ├── context.py               # context extraction + sentinel fill
│   ├── compiler.py              # DimensionCompiler (ternary expressions)
│   ├── result.py                # RuleResult
│   ├── hit_policy.py            # SelectionInfo, ordering, assertions, cardinality
│   └── batch_result.py          # BatchRuleResult
└── engines/
    ├── __init__.py
    ├── filter/
    │   ├── __init__.py
    │   └── engine.py            # ExpressionRulesEngine
    └── accumulator/
        ├── __init__.py
        ├── engine.py            # was accumulator_engine.py
        ├── compiler.py          # was accumulator_compiler.py
        ├── result.py            # was accumulator_result.py
        ├── lattice.py           # Lattice, LatticeIndex
        ├── aggregate.py         # Aggregate
        └── primes.py            # prime table, LatticeWidthExceededError
```

Placement decisions (resolved during review, with dependency evidence):

- **Selection stack (`result`, `hit_policy`, `batch_result`) → core.**
  `result.py` imports `hit_policy.py`, and `AccumulatorResult` inherits
  `RuleResult.select()`. Placing these in `engines/filter/` would force
  core→filter imports. They are shared, not filter-only.
- **`compiler.py` (DimensionCompiler) → core.** Only the filter engine
  imports it directly, but it is the ternary-encoding compiler both
  engines depend on transitively.
- **`aggregate`, `lattice`, `primes` → engines/accumulator.** Nothing
  outside the accumulator cluster imports them internally; external
  consumers get `Aggregate`/`Lattice`/`LatticeIndex` via the public API.
- **`accumulator_` prefixes dropped.** The module path carries the
  information. Duplicate basenames across subpackages (`engine.py`,
  `compiler.py`, `result.py`) are intentional.
- **`engines/` level kept** (vs flat `filter/` + `accumulator/`): future
  engines (DRG orchestration, scorecard — see
  complementary-engine-architectures backlog card) slot in as
  `engines/<name>/`.

Allowed dependency direction: `engines/accumulator` → `engines/filter` →
`core`; never the reverse. `core` imports nothing from `engines/`.

## 3. Public-API discipline (downstream contract)

Module paths under `mountainash_rules.` are **private**. Consumers import
only from the package root. Current gap: babel (src + tests) deep-imports
`mountainash_rules.{lattice,constants,dimension,aggregate,accumulator_engine}`.

Names babel needs that are missing from the package root today (all in
`core/constants.py`):

```
UNKNOWN, NOT_SET, UNKNOWN_NUMERIC, NOT_SET_NUMERIC,
UNKNOWN_DATE, NOT_SET_DATE, UNKNOWN_DATETIME, NOT_SET_DATETIME,
sentinels_for, unknown_sentinel_for, not_set_sentinel_for
```

These are promoted into `mountainash_rules.__init__.__all__`. Babel then
switches every rules import to the package root — code AND docs: babel's
`CLAUDE.md` and `docs/lattice-schema.md` currently cite
`mountainash_rules.constants.sentinels_for` and must cite the package root
instead (sweep with `grep -rn "mountainash_rules\.[a-z_]*\."` over babel,
excluding `rules_babel` matches). This ships **before** the move,
independently, so the move itself is invisible to babel.

## 4. Deprecation shims

Every old top-level module (14: `constants`, `dimension`, `context`,
`compiler`, `result`, `hit_policy`, `batch_result`, `engine`,
`accumulator_engine`, `accumulator_compiler`, `accumulator_result`,
`lattice`, `aggregate`, `primes`) is replaced by a shim that:

1. emits `DeprecationWarning` naming the new path on import, and
2. re-exports the new module's public names (`from <new> import *` plus an
   explicit `from <new> import <ClassNames>` for names `import *` would
   miss — `*` honours module `__all__` only where defined; where the moved
   module has no `__all__`, the shim lists the public names explicitly).

Shims are removed in the production release **after** the next one
(CalVer: shipped in `YYYY.MM.0/.1`, removed in the following month's
release). A dated removal note lives in each shim's docstring.

Identity guarantee: `mountainash_rules.engine.ExpressionRulesEngine is
mountainash_rules.engines.filter.engine.ExpressionRulesEngine` — shims
re-export, never redefine. The shim test asserts **exactly one**
DeprecationWarning per (re)import and identity for **every** public name of
the new module, not a single representative symbol.

## 5. Backend-purity expansion

`tests/test_backend_purity.py` currently pins
`PURE_FILES = ("engine.py", "result.py", "compiler.py")` by basename in the
package root. After the move the pure set becomes (paths relative to
`src/mountainash_rules/`):

```
core/compiler.py, core/result.py, core/hit_policy.py, core/batch_result.py,
core/context.py, engines/filter/engine.py,
engines/accumulator/engine.py, engines/accumulator/compiler.py,
engines/accumulator/result.py, engines/accumulator/lattice.py
```

(`core/constants.py`, `core/dimension.py`, `engines/accumulator/primes.py`,
`engines/accumulator/aggregate.py` have no DataFrame code and are included
for free — the whole package minus `__init__`/shims is scanned; simpler
rule: **every non-shim module must be pure**.)

Known impurity to allow-tag (audited 2026-07-13): the current
`accumulator_engine.py:9` `import polars as pl` used only by the
empty-build schema seed (`pl.Series("__prime", [], dtype=pl.Int64)`).
Tag: `# allow: empty-build schema seed pending backend-agnostic empty-frame support`.
The existing tagged fallback in `core/compiler.py` (per-row REGEX) carries over.

Shim files are excluded from the purity scan (they import nothing but the
new modules).

## 6. Test directory mirror

`tests/` (27 flat files) is reorganised to mirror the package:

```
tests/
├── conftest.py                    # stays at root (fixtures, xfail registry)
├── __init__.py
├── core/        → test_dimension.py, test_dimension_serialization.py,
│                  test_context.py, test_compiler.py, test_result.py,
│                  test_hit_policy.py
├── filter/      → test_engine.py, test_batch_evaluation.py,
│                  test_integration.py, test_upstream_regressions.py
├── accumulator/ → test_accumulator_*.py (renamed test_engine.py,
│                  test_compiler.py, ... prefixes dropped to mirror src),
│                  test_lattice.py, test_lattice_properties.py, test_primes.py
├── benchmarks/  → test_benchmarks.py, test_accumulator_benchmarks.py,
│                  benchmark_data.py, test_benchmark_data.py
└── test_backend_purity.py         # stays at root (whole-package scan)
```

CI path filters (audited 2026-07-13): all three workflows
(`python-run-pytest.yml`, `python-run-ruff.yml`, `python-run-radon.yml`)
trigger only on `src/mountainash_rules/**` — a pre-existing gap this plan
would trip over, since Task 5 is tests-only. The filters gain `tests/**`,
`pyproject.toml`, and `hatch.toml`.

Safety: the conftest `_UPSTREAM_XFAILS` registry matches on
`ClassName::test_name` substrings of `item.nodeid`, not file paths —
verified 2026-07-13 — so moves don't break xfails. Test files renamed to
drop `accumulator_` prefixes only where the directory disambiguates;
class names inside are untouched.

## 7. Out of scope

- Any behaviour change, signature change, or refactor of module contents.
- Splitting `accumulator_engine.py` build/apply phases (future card if it
  keeps growing).
- Removing the shims (follow-up release).

## 8. Acceptance criteria

1. `from mountainash_rules import X` works for every current `__all__`
   name plus the §3 promotions; no other public surface change.
2. Both suites green: rules (731 passed / 36 skipped / 31 xfailed baseline)
   and babel (79 passed baseline) — babel green **without babel source
   changes** once Task 1's root-import switch has landed.
3. Old module paths import successfully with exactly one
   `DeprecationWarning` each, and re-exported objects are identical (`is`)
   to their new-path counterparts.
4. Backend purity enforced across every non-shim module in the package.
5. File history preserved (`git mv`; `git log --follow` works).
6. CLAUDE.md/README structure trees updated in both repos, and no
   `mountainash_rules.<module>` paths remain in babel code or docs.
7. Installed-wheel smoke test: build the wheel, install into a scratch
   venv, and import the package root, one new canonical path, and one shim
   (asserting its DeprecationWarning) — proves packaging picks up the
   nested subpackages.
8. CI workflows trigger on `tests/**`, `pyproject.toml`, and `hatch.toml`
   in addition to `src/mountainash_rules/**`.
9. The public-API test covers `__version__` and asserts every name in
   `__all__` actually resolves.
