# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mountain Ash Rules (`mountainash_rules`) is a metadata-driven rules engine: rules are rows in a DataFrame, dimensions declare how each column matches a context, and evaluation is a single vectorised pass built on `mountainash` expressions. It is backend-agnostic (Polars, Pandas, Ibis, Narwhals) and provides two engines:

- **ExpressionRulesEngine** (filter engine, `engines/filter/engine.py`) — evaluates one context (or a batch of contexts) against the rules, ranks survivors by specificity, and applies a hit policy.
- **AccumulatorEngine** (exact accumulator, `engines/accumulator/engine.py`) — builds immutable exact cells (a `Lattice`) from validated rules via backend-native predicate geometry, then resolves contexts against a contract/profile directly (no filter engine involved). **Pending replacement** — see the Accumulator engine subsection under Architecture before working on it.

## Planning, Specs & Principles (live in mountainash-central)

Design specs, implementation plans, discussion records, the technical-debt backlog, and the
architecture principles live in the ecosystem repo **`mountainash-central`** (sibling checkout:
`../mountainash-central`). Only source, tests, user-facing docs (`docs/user-quickstart.md`),
and this `AGENTS.md` live in this repo's tree.

| Artifact | Location in `mountainash-central` |
|----------|-----------------------------------|
| Design specs | `04.planning/mountainash-rules/superpowers/specs/` |
| Implementation plans | `04.planning/mountainash-rules/superpowers/plans/` |
| Discussions | `04.planning/mountainash-rules/superpowers/discussions/` |
| Technical-debt backlog (+ `prioritisation/`) | `04.planning/mountainash-rules/a.backlog/` |
| ENFORCED architecture principles | `01.principles/mountainash-rules/` |

**When running the superpowers flow (brainstorming → writing-plans → SDD) for this repo,
write the spec/plan to `mountainash-central/04.planning/mountainash-rules/superpowers/{specs,plans}/`,
not to a local `docs/superpowers/` directory.** (The superpowers skills default to a local
`docs/superpowers/` path — override that default to the central location above.)

### Central Documentation Workflow (MANDATORY)

- Work on central documentation only in the primary local `../mountainash-central` checkout, on its `main` branch.
- Edit and commit documentation directly to `main`. Do not create or use documentation branches, additional worktrees, or docs PRs.
- A code implementation branch/worktree does not change the documentation location: specs, plans, principles, backlog records and other central docs still go to the primary `mountainash-central` checkout.
- Stage and commit only the documentation changes owned by the current task. Preserve unrelated local commits and uncommitted work; do not stash, reset or overwrite another thread's work to synchronize the checkout.
- This directive overrides generic branching, worktree and PR workflows for central documentation. Do not move docs into another checkout to avoid a synchronization issue.

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

Set-dimension wildcards use the in-band `[sentinel]` list (not null), normalized at ingestion (reservation check in both engines; element-null check in the accumulator build).

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

### Accumulator engine (exact accumulator — PENDING REPLACEMENT)

> **Do not extend this implementation.** The table below describes what is in
> `src/` today, but the owner has authorized a full replacement: a fresh
> backend-native "disjoint lattice" implementation is being designed to
> replace this one outright (new module/package, not an in-place rewrite),
> with the existing code retained only as integration/failure evidence — "not
> the architecture or correctness oracle" — until it is deleted. Current
> status, exit gates and the coexistence-then-removal plan:
> `mountainash-central/04.planning/mountainash-rules/superpowers/plans/2026-09-20-accumulator-design-meta-plan.md`.
> The replacement design itself:
> `mountainash-central/04.planning/mountainash-rules/superpowers/specs/2026-09-20-accumulator-replacement-design.md`,
> with construction/serving implementation plans in
> `.../superpowers/plans/2026-09-22-accumulator-backend-construction.md` and
> `.../2026-09-22-accumulator-serving-validation.md`. **Check the meta-plan's
> status line before doing any accumulator work** — building on the table
> below may mean building on code that is about to be removed.

`AccumulatorEngine` builds and serves **immutable exact cells** through backend-native
predicate geometry. This replaced the earlier prime-product/frontier-pruning `Lattice`
combinatorics — there is no more `co_<field>` coalescing, `__prime_product` identity, or
`Lattice.is_composed`/`save`/`load`, and the accumulator no longer depends on
`engines/filter` at all (see "Dependency direction" below). This implementation's own
design rationale and phased delivery history (retired execution plans, not a queue to
resume) live in `mountainash-central/04.planning/mountainash-rules/superpowers/specs/2026-09-13-exact-predicates-identity-native-snapshot-design.md`
and the `exact-accumulator-phase-{1..6}` plans alongside it.

| Component | Role | File(s) |
|---|---|---|
| `AccumulatorEngine` | `build`/`build_all` (validated rules → `Lattice`, one or all `CONTEXT_KEY` partitions), `apply`/`apply_auto` (context + `contract_id`/`profile_id` → result), all under an `ExactLimits` resource budget | `engines/accumulator/engine.py` |
| Contracts | Immutable, dataframe-free data contracts: `ContextContract`, `DomainDefinition`, `ResolutionProfile`, `ValidationBundle`/`ValidationPolicy`/`ValidationReport`, `ExactLimits`/`OperationBudget` | `core/contracts.py` |
| Predicate graph & reasoning | Backend-independent predicate-1 lowering (`PredicateGraph`) and exact Boolean decision search over discrete scalar ranks (`Reasoner`) | `core/predicates.py`, `core/reasoner.py` |
| Normalization | normalization-2 box/interval intersection over the predicate graph (`Fragment`/`Overlay`/`CellRegion`) | `core/normalization.py` |
| String language | `StringLanguage` — exact Unicode-scalar regex/DFA matching, backed by the Rust `mountainash_rules._native` PyO3 extension (`pyproject.toml` builds it per-platform) | `core/language.py` |
| Codec & scalars | Canonical JSON / hash-envelope codec for exact records; exact scalar recognition, transport, and discrete ordered universes | `core/codec.py`, `core/scalar.py` |
| Validation proofs | Provider-independent structural/analysis proofs (`StructuralSource`, `StructuralCell`, `AnalysisGeometry`, `SourceProof`, `ProfileProof`, `RoutingEvidence`, …) and portable permission checks over them | `core/validation.py` |
| Build pipeline | Phase-3 source-evidence orchestration, scoped dense-word cell layout (`ExactLayout`), source→cell analysis/compilation, bounded host/relational state boundary | `engines/accumulator/{analysis,layout,compiler,state,tables}.py` |
| Apply pipeline | `EvaluationSession` — contract+profile-scoped request resolution against a compiled `Lattice` | `engines/accumulator/runtime.py` |
| Persistence | Module-level `save(lattice, dir_path, *, budget)` / `load(dir_path, *, budget)`; bounded, descriptor-safe native snapshot wire transport (parquet + YAML manifest) is separate from semantic restoration | `engines/accumulator/persistence.py`, `engines/accumulator/snapshot_io.py` |
| Partition routing | `LatticeIndex` routes `CONTEXT_KEY`-partitioned contexts (single via `apply_auto`, or precomputed via `index(lattices)`) using ternary + specificity semantics over `EXACT_KEY` dimensions; `AmbiguousPartitionError` (a `KeyError` subclass, exported from the package root) on ties | `engines/accumulator/lattice.py` |

Partition-routing semantics (ternary + specificity over `EXACT_KEY` dims, witness-matrix
ambiguity checks) are unaffected by the exact-accumulator rewrite; see
`mountainash-central/04.planning/mountainash-rules/superpowers/specs/2026-07-19-ternary-partition-routing-design.md`.

### Backend purity (ENFORCED)

No module under `src/mountainash_rules/` may import polars/ibis/narwhals directly — only `mountainash.relations` / `mountainash.expressions`. `tests/test_backend_purity.py` enforces this across the whole package (every non-dunder module is parametrised, no exemptions). A genuinely unavoidable native escape must be tagged `# allow: <reason>` on the import line. The retained `engines/accumulator/snapshot_io.py` has four: two `pyarrow`/`pyarrow.parquet` imports (snapshot parquet metadata/schema codec seam) and two `polars` imports (snapshot parquet relation bridge, pending backend-agnostic file IO). Per-row REGEX now uses public expression-valued `str.regex_contains`; its former native escape is removed. This is separate from the Rust `mountainash_rules._native` PyO3 extension backing `core/language.py`'s `StringLanguage` — that is a sanctioned native component for exact regex/DFA matching, not a DataFrame-backend escape, and isn't `# allow:`-tagged.

## Match Strategies

`MatchStrategy` is a lowercase `StrEnum` (12 members), compiled in `compiler.py`:

| Strategy | Rule column format | Notes |
|---|---|---|
| `exact` / `not_equal` | scalar | any data_type |
| `exact_key` | scalar | rule-side wildcard only (UNKNOWN → 0; context sentinel vs specific → −1); powers partition routing |
| `range` | two columns (`range_min_field`/`range_max_field`) | numeric/temporal; `range_min_inclusive`/`range_max_inclusive` flags |
| `greater_than` / `less_than` | threshold | numeric/temporal |
| `prefix` / `suffix` / `contains` | string | |
| `regex` | per-row pattern column | Public expression-valued `str.regex_contains`; qualified on Polars/Ibis-DuckDB |
| `context_regex` | literal `regex_pattern` on the Dimension | global context validator |
| `set_membership` / `set_exclusion` | list column | Filter via `t_is_in`/`t_is_not_in` (polars/ibis; narwhals list ops under `mountainash#89`). Accumulator-coalesceable (membership → list intersection, exclusion → list union). Wildcard = in-band `[unknown_sentinel_for(dtype)]` (never null; bool unsupported); see `null-is-not-a-portable-sentinel` principle. |

**Adding a strategy:** add enum value in `core/constants.py`, validation in `core/dimension.py`, `_compile_<strategy>` in `core/compiler.py`, test class in `tests/core/test_compiler.py`.

## Package Structure

```
src/mountainash_rules/
├── __init__.py                  # Public API (see __all__) — the only public import surface
├── _native.pyi                  # Type stubs for the mountainash_rules._native Rust/PyO3 extension
├── core/                        # backend-agnostic building blocks (never imports from engines/)
│   ├── constants.py             # MatchStrategy, DimensionRole, HitPolicy, DataType, sentinels
│   ├── dimension.py             # Dimension / DimensionsMetadata (+ YAML round-trip)
│   ├── context.py               # context extraction + sentinel fill
│   ├── compiler.py              # DimensionCompiler (ternary expressions, filter engine)
│   ├── result.py                # RuleResult (+ select())
│   ├── hit_policy.py            # SelectionInfo, ordering, assertions, cardinality
│   ├── batch_result.py          # BatchRuleResult
│   ├── set_wildcard.py          # set-dimension sentinel/wildcard predicates
│   ├── contracts.py             # ContextContract, DomainDefinition, ResolutionProfile, ExactLimits, ValidationBundle
│   ├── predicates.py            # PredicateGraph — backend-independent predicate-1 lowering
│   ├── reasoner.py              # Reasoner — exact Boolean decision search
│   ├── normalization.py         # normalization-2: Fragment/Overlay/CellRegion box intersection
│   ├── language.py              # StringLanguage — exact regex/DFA matching (native-backed)
│   ├── codec.py                 # canonical JSON / hash-envelope codec for exact records
│   ├── scalar.py                # exact scalar recognition, transport, ordered universes
│   └── validation.py            # provider-independent analysis proofs + permission checks
├── engines/
│   ├── filter/
│   │   └── engine.py            # ExpressionRulesEngine (evaluate / explain / evaluate_batch)
│   └── accumulator/             # exact accumulator — depends on core only, not engines/filter
│       ├── engine.py            # AccumulatorEngine — build/build_all, apply/apply_auto
│       ├── lattice.py           # Lattice, LatticeIndex, AmbiguousPartitionError
│       ├── aggregate.py         # Aggregate model + AggregateOp (sum/min/max/product folds)
│       ├── analysis.py          # Phase 3 source-evidence orchestration
│       ├── layout.py            # ExactLayout — scoped dense-word cell layout
│       ├── compiler.py          # source→cell analysis/compilation
│       ├── state.py             # CompiledState — immutable relational state
│       ├── tables.py            # host/relational boundary + byte accounting
│       ├── runtime.py           # EvaluationSession — apply-phase request resolution
│       ├── persistence.py       # save/load — semantic native restoration
│       ├── snapshot_io.py       # bounded native snapshot wire transport (parquet + YAML)
│       └── result.py            # AccumulatorResult, AccumulatorBatchResult
```

Dependency direction is one-way: both `engines/filter` and `engines/accumulator` depend
on `core`; `core` never imports from `engines/`. Unlike the pre-2026-09 prime-product
`Lattice` design, `engines/accumulator` no longer depends on `engines/filter` at all —
the exact accumulator resolves contexts through its own predicate-graph/reasoner
pipeline, not a memoised filter engine. The pre-reorganisation top-level module paths
(and their deprecation shims) were removed 2026-07-16 — babel, the only downstream
consumer, already imports from the package root only.

`Dimension.data_type` is a `DataType` StrEnum (`str/int/float/bool/date/datetime`); passing a Python type still works but emits a `DeprecationWarning`. `DimensionsMetadata` serialises via `to_yaml/from_yaml/to_yaml_file/from_yaml_file`.

## Build/Test/Lint Commands

- **Tests (quick)**: `hatch run test:test-quick`
- **Tests (coverage)**: `hatch run test:test`
- **Single test**: `hatch run test:test-target tests/test_file.py::TestClass::test_name`
- **Benchmarks**: `hatch run test:test-perf`
- **Lint**: `hatch run ruff:check` / `hatch run ruff:fix`
- **Type check**: `hatch run mypy:check`
- **Build**: `hatch build`

The owner retired the old-engine test gates during the clean replacement.
`tests/conftest.py` excludes `tests/accumulator/` and
`tests/benchmarks/test_accumulator_benchmarks.py` from collection. Do not re-enable
them to preserve legacy compatibility. Replacement `tests/accumulate/`, shared
core/filter tests and filter benchmarks remain active. Old fixtures remain
importable for shared tests until source removal.

Known upstream issues are registered as xfails in `tests/conftest.py` (`_UPSTREAM_XFAILS`): ibis-polars `with_row_index` (mountainash#78); string-match strategies broken on pandas/narwhals backends (mountainash#89).

## Dependencies

Core: `mountainash` (expressions/relations — the only DataFrame API the engine core may use), `polars`, `pandas`, `ibis-framework`, `pydantic`, `pyyaml`. Sibling checkouts of `mountainash`, `mountainash-data`, `mountainash-settings`, and `mountainash-auth-client` are required for dev and CI (see `hatch.toml` and `.github/config/mountainash_dependencies.yml`). Building from source (`hatch build`, editable installs) also requires a **Rust toolchain** (`cargo`) — `hatch_build.py` compiles the `mountainash_rules._native` PyO3 extension per interpreter/platform; there is no pure-Python fallback.

## Branch Strategy & Releases

- `main`: production (only `release/*` and `hotfix/*` merge in); `develop`: development/RC; `feature/* | bugfix/* | hotfix/*` branch from develop.
- CalVer: `YYYY.MM.MICRO` (RC = `.0`, production = `.1`, patches increment).
- CI: pytest + coverage, ruff, radon on PRs; `build-and-release-package.yml` builds wheels + SBOMs.

## Code Style

- ruff formatting/linting; `import typing as t`; Google-style docstrings; CamelCase classes, snake_case functions, UPPER_CASE constants.
- ValueError for validation errors; custom exceptions (`HitPolicyViolationError`, `AmbiguousPartitionError`, `InvalidContextError`, `UnresolvedContextError`, `ExactCapabilityError`, `ExactResourceError`) where callers need to catch.
- TDD: failing test first. Test markers: unit, integration, performance, benchmark.
- Keep engine core backend-pure (see above); use ternary encoding for match values.
- `mountainash_rules` module paths are private — import public names from the package root only (`from mountainash_rules import Lattice`).

## Related Documentation

- `mountainash-central/04.planning/mountainash-rules/superpowers/{specs,plans}/` — the 2026-07 design specs/plans for accumulator correctness, serialisable metadata, hit policies, batch evaluation (see the Planning, Specs & Principles table above for the full location map).
- `mountainash-central/01.principles/mountainash-rules/` — architectural review, principles, backlog.
- `mountainash-rules-babel` — the format interchange layer (CSV/DMN import/export of lattices); its `docs/lattice-schema.md` defines the export schema contract.

## License

Apache-2.0
