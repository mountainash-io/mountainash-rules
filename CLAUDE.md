# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mountain Ash Rules (`mountainash_rules`) is a metadata-driven rules engine: rules are rows in a DataFrame, dimensions declare how each column matches a context, and evaluation is a single vectorised pass built on `mountainash` expressions. It is backend-agnostic (Polars, Pandas, Ibis, Narwhals) and provides two engines:

- **ExpressionRulesEngine** (filter engine, `engine.py`) — evaluates one context (or a batch of contexts) against the rules, ranks survivors by specificity, and applies a hit policy.
- **AccumulatorEngine** (build/apply engine, `accumulator_engine.py`) — precomputes all *maximal consistent combinations* of rules (a `Lattice`) with accumulated numerics, then applies contexts against the lattice with a filter engine.

## Architecture

### Ternary match logic

Per-dimension match values use signed-integer ternary encoding: **1 = match, 0 = unknown/wildcard, −1 = non-match**. Defined in `constants.py`, produced by `compiler.py`, consumed by `engine.py`/`result.py`. A rule survives when its minimum ternary value is ≥ 0; specificity = count of 1s; UNKNOWN counts as wildcard (survives, ranks lower).

Wildcards are in-band typed sentinels (see `constants.sentinels_for(data_type)`):

| data_type | UNKNOWN | NOT_SET |
|---|---|---|
| str | `"<NA>"` | `"<NOT_SET>"` |
| int/float | `-999999999` | `-999999998` |
| date | `date(1,1,1)` | `date(1,1,2)` |
| datetime | `datetime(1,1,1)` | `datetime(1,1,2)` |

### Filter engine pipeline (`engine.py`)

1. **Compile** — `DimensionCompiler` (`compiler.py`) turns each `Dimension` into a backend-agnostic ternary expression template at construction.
2. **Bind** — context values become `__ctx_<dim>` literal columns (missing values become NOT_SET sentinels; `context.py`).
3. **Evaluate** — all `__t_<dim>` ternary columns computed in one pass; survival filter `least(*t) >= 0`; `__specificity` = sum of matches.
4. **Select** — sort by hit-policy ordering keys, compute `__rank`, run hit-policy assertions, apply cardinality (`hit_policy.py`).

`explain(context, dimensions=None)` → `ExplainResult`: every rule scored
(ternaries + `__survived` + `__specificity`), no survival filter, no rank,
no hit-policy interaction. Shares Steps 1–3 with `evaluate` via
`_scored_relation`.

`evaluate_batch(contexts, ...)` cross-joins a contexts frame against the rules, computes the same ternary columns, and ranks **per context id** portably (no window functions). Contexts are conformed to the rules' backend before the join (`_conform_to_rules_backend`). Returns `BatchRuleResult` (`batch_result.py`).

### Hit policies (`hit_policy.py`, `constants.HitPolicy`)

`collect` (default), `unique` (raises `HitPolicyViolationError` on >1 survivor), `first`, `priority` (needs `priority_field`), `any` (raises on conflicting outputs), `rule_order`. Policy lives on `DimensionsMetadata` or per-call; `RuleResult.select(policy)` re-selects post-hoc unless the result was truncated.

### Accumulator engine

- `build(rules)` → `Lattice`: level-wise expansion of compatible rule combinations, coalescing dimension values (`co_<field>` columns + `co_*_na` flags, `accumulator_compiler.py`), accumulating `Aggregate` numerics (`__agg_<name>`), identifying combinations by prime products (`primes.py`; overflow → `LatticeWidthExceededError` with remediation), and pruning dominated combinations (frontier filter). Each level is **materialised** via `relation(...).collect()` — do not re-chain lazily (exponential plan blow-up).
- `apply(lattice, context)` → `AccumulatorResult`. Apply-phase filter engines are memoised per lattice (WeakKeyDictionary).
- `DimensionRole.CONTEXT_KEY` dimensions partition the rule space; `build_all` + `index(lattices)` → `LatticeIndex` routes contexts (single or batch) to the right lattice.
- `Lattice.is_composed` distinguishes build output (has `__prime_product`) from flat/imported lattices.

### Backend purity (ENFORCED)

No module under `src/mountainash_rules/` may import polars/ibis/narwhals directly — only `mountainash.relations` / `mountainash.expressions`. `tests/test_backend_purity.py` enforces this across the whole package (every non-dunder module is parametrised, no exemptions). A genuinely unavoidable native escape must be tagged `# allow: <reason>` on the import line — currently two: the per-row REGEX fallback in `core/compiler.py` (pending upstream column-pattern `regex_contains`) and the empty-build schema seed in `engines/accumulator/engine.py` (pending backend-agnostic empty-frame support).

## Match Strategies

`MatchStrategy` is a lowercase `StrEnum` (12 members), compiled in `compiler.py`:

| Strategy | Rule column format | Notes |
|---|---|---|
| `exact` / `not_equal` | scalar | any data_type |
| `range` | two columns (`range_min_field`/`range_max_field`) | numeric/temporal; `range_min_inclusive`/`range_max_inclusive` flags |
| `greater_than` / `less_than` | threshold | numeric/temporal |
| `prefix` / `suffix` / `contains` | string | |
| `regex` | per-row pattern column | Polars-native fallback (`# allow:` tagged) |
| `context_regex` | literal `regex_pattern` on the Dimension | global context validator |
| `set_membership` / `set_exclusion` | list column | Polars-native fallback |

**Adding a strategy:** add enum value in `core/constants.py`, validation in `core/dimension.py`, `_compile_<strategy>` in `core/compiler.py`, test class in `tests/core/test_compiler.py`.

## Package Structure

```
src/mountainash_rules/
├── __init__.py                  # Public API (see __all__) — the only public import surface
├── core/                        # backend-agnostic building blocks (never imports from engines/)
│   ├── constants.py             # MatchStrategy, DimensionRole, HitPolicy, DataType, sentinels
│   ├── dimension.py             # Dimension / DimensionsMetadata (+ YAML round-trip)
│   ├── context.py               # context extraction + sentinel fill
│   ├── compiler.py              # DimensionCompiler (ternary expressions)
│   ├── result.py                # RuleResult (+ select())
│   ├── hit_policy.py            # SelectionInfo, ordering, assertions, cardinality
│   └── batch_result.py          # BatchRuleResult
├── engines/
│   ├── filter/
│   │   └── engine.py            # ExpressionRulesEngine (evaluate / evaluate_batch)
│   └── accumulator/             # build/apply engine (depends on filter + core)
│       ├── engine.py            # AccumulatorEngine build/apply
│       ├── compiler.py          # coalesce / compatible / NA-flag expressions
│       ├── result.py            # AccumulatorResult (extends RuleResult)
│       ├── lattice.py           # Lattice, LatticeIndex
│       ├── aggregate.py         # Aggregate model (sum/min/max/product monoids)
│       └── primes.py            # prime table, checked_multiply, LatticeWidthExceededError
```

Dependency direction is one-way: `engines/accumulator` → `engines/filter` → `core`; `core` never imports from `engines/`. The pre-reorganisation top-level module paths (and their deprecation shims) were removed 2026-07-16 — babel, the only downstream consumer, already imports from the package root only.

`Dimension.data_type` is a `DataType` StrEnum (`str/int/float/bool/date/datetime`); passing a Python type still works but emits a `DeprecationWarning`. `DimensionsMetadata` serialises via `to_yaml/from_yaml/to_yaml_file/from_yaml_file`.

## Build/Test/Lint Commands

- **Tests (quick)**: `hatch run test:test-quick`
- **Tests (coverage)**: `hatch run test:test`
- **Single test**: `hatch run test:test-target tests/test_file.py::TestClass::test_name`
- **Benchmarks**: `hatch run test:test-perf`
- **Lint**: `hatch run ruff:check` / `hatch run ruff:fix`
- **Type check**: `hatch run mypy:check`
- **Build**: `hatch build`

Known upstream issues are registered as xfails in `tests/conftest.py` (`_UPSTREAM_XFAILS`): ibis-polars `with_row_index` (mountainash#78); string-match strategies broken on pandas/narwhals backends (mountainash#89).

## Dependencies

Core: `mountainash` (expressions/relations — the only DataFrame API the engine core may use), `polars`, `pandas`, `ibis-framework`, `pydantic`, `pyyaml`. Sibling checkouts of `mountainash`, `mountainash-data`, `mountainash-settings` are required for dev (see `hatch.toml`).

## Branch Strategy & Releases

- `main`: production (only `release/*` and `hotfix/*` merge in); `develop`: development/RC; `feature/* | bugfix/* | hotfix/*` branch from develop.
- CalVer: `YYYY.MM.MICRO` (RC = `.0`, production = `.1`, patches increment).
- CI: pytest + coverage, ruff, radon on PRs; `build-and-release-package.yml` builds wheels + SBOMs.

## Code Style

- ruff formatting/linting; `import typing as t`; Google-style docstrings; CamelCase classes, snake_case functions, UPPER_CASE constants.
- ValueError for validation errors; custom exceptions (`HitPolicyViolationError`, `LatticeWidthExceededError`) where callers need to catch.
- TDD: failing test first. Test markers: unit, integration, performance, benchmark.
- Keep engine core backend-pure (see above); use ternary encoding for match values.
- `mountainash_rules` module paths are private — import public names from the package root only (`from mountainash_rules import Lattice`).

## Related Documentation

- `docs/superpowers/specs/` and `docs/superpowers/plans/` — the 2026-07 design specs/plans for accumulator correctness, serialisable metadata, hit policies, batch evaluation.
- `mountainash-central/01.principles/mountainash-rules/` — architectural review, principles, backlog.
- `mountainash-rules-babel` — the format interchange layer (CSV/DMN import/export of lattices); its `docs/lattice-schema.md` defines the export schema contract.

## License

MIT
