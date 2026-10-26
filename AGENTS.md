# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mountain Ash Rules (`mountainash_rules`) is a metadata-driven rules package: rules are rows of a table, dimensions declare how each column matches a context, and evaluation is relational, built on `mountainash` relations and expressions. The supported and tested backends are **Polars and Ibis-DuckDB** (`duckdb` is pinned `<2.0`, central backlog #363; see `docs/backend-support.md`). It provides two engines:

- **`ExpressionRulesEngine`** (filter engine, `engines/filter/engine.py`) — evaluates one context, or a batch of contexts, against a rules table or Mountainash `Relation`, ranks survivors by specificity and applies a hit policy. It performs **no** rule validation, context value admission or request-identity checking; invalid inputs are evaluated as given and backend errors propagate natively. `hit_policy`, `priority_field` and `output_fields` are call-time arguments of `evaluate`, `evaluate_batch` and `RuleResult.select`.
- **`DecisionTable`** (`decision_table/`) — rules physicalised into non-overlapping *entries* so that matching is a lookup through the filter engine. Layers: `prevalidate` → `build_decision_table` → `DecisionTable.match(...).fetch()`, plus `DecisionTable.save/load` and `verify_saved_table`. `context_validation.py` provides `check_context_schema` and `validate_contexts`, run by callers before matching.

The package root (`src/mountainash_rules/__init__.py`) is the only public import surface; `tests/test_public_api.py` asserts its exact export set.

## Planning, Specs & Principles (live in mountainash-central)

Design specs, implementation plans, discussion records, the technical-debt backlog, and the
architecture principles live in the ecosystem repo **`mountainash-central`** (sibling checkout:
`../mountainash-central`). Only source, tests, user-facing docs (`docs/user-quickstart.md`,
`docs/backend-support.md`, `docs-site/`), and this `AGENTS.md` live in this repo's tree.

| Artifact | Location in `mountainash-central` |
|----------|-----------------------------------|
| Design specs | `04.planning/mountainash-rules/superpowers/specs/` |
| Implementation plans | `04.planning/mountainash-rules/superpowers/plans/` (DecisionTable: `2026-10-20-decision-table-{0-cutover,1-prevalidation,2-construction,3-context-validation,4-application,5-post-aggregation}.md`) |
| Plan reviews and owner decisions | `04.planning/mountainash-rules/superpowers/reviews/` (`2026-10-21-decision-table-plans-review-{2..6}.md`) |
| Discussions | `04.planning/mountainash-rules/superpowers/discussions/` |
| Technical-debt backlog (+ `prioritisation/`) | `04.planning/mountainash-rules/a.backlog/` |
| ENFORCED architecture principles | `01.principles/mountainash-rules/` |

Principles to read before changing behaviour:

- `a.architecture/accumulator/five-layer-pipeline.md` — the five layers and their boundaries.
- `a.architecture/accumulator/physicalised-rules-panel.md` — how entries are physicalised and what each strategy contributes.
- `b.metadata-contract/explicit-dont-care-sentinels.md` — one declared wildcard per field; null semantics; PRESENCE and OUTCOME.
- `b.metadata-contract/context-availability-and-resolution.md` — context availability.

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

### Orthogonal type, strategy and region ownership

`core/type_families.py` is the private owner of Rules type-dependent behavior: seven shared stateless families, selected by one `DataType` mapping. `ContextField` and `Aggregate` retain declaration-specific configuration; callers pass the declaration to the family rather than configuring a family per field.

Families own declaration checks, descriptors, metadata codecs, role-specific rule/list/context binding and admission, context representability, signature conversion and ordered-value arithmetic. These roles deliberately differ: signature conversion is not engine binding, and DATETIME context truncation is not construction flooring. `core/numeric.py` retains shared physical-source and expression mechanics.

Strategies still own matching; `decision_table/fields.py` owns region interpretation, geometry, contributor coverage and bounded successor/predecessor. It delegates value arithmetic to the type family. Validation layers retain structural schema extraction, ordering and finding aggregation; matching does not invoke validation. No family accepts a match strategy or selects a region class.

### Ternary match logic

Per-dimension match values use signed-integer ternary encoding: **1 = match, 0 = unknown or don't-care, −1 = non-match**. Defined in `core/constants.py`, produced by `core/compiler.py`, consumed by `engines/filter/engine.py` and `core/result.py`. A rule survives when its minimum ternary value is ≥ 0; specificity = count of 1s.

### Null and wildcard model

- Each `ContextField` (`core/dimension.py`) declares **exactly one** wildcard marker: a typed value of the field (for example `"*"`), or null when the author declares it (`wildcard_declared=True`, `wildcard=None`). Every dimension on the field uses that marker. Markers round-trip through the flat `fields` table as canonical text parsed by field type.
- **Null is never an implicit wildcard.** A rule-side null without a declared null wildcard is invalid (prevalidation rejects it). A context null, or a missing context value (omitted key, unset `model_fields_set` member, absent column), scores 0 on a value dimension unless the field declares a truthy mapping that maps null.
- **PRESENCE** asks "is the field null?": rule cells `IS_NULL` / `IS_NOT_NULL` / wildcard; context explicit null scores 1, supplied value −1, not supplied 0. "Not applicable" is a PRESENCE dimension, never a meaning of null in a value dimension.
- **OUTCOME** dimensions compare a required int8 ternary (rule column) with an already computed context ternary: rule 0 or context 0 scores 0, equal non-zero 1, unequal non-zero −1. In a `DecisionTable`, PRESENCE, authored TRUTHY and pattern dimensions are stored in `entries` as int8 OUTCOME columns.
- `UNKNOWN*` constants and `unknown_sentinel_for` are conventional marker values only; the engine gives them meaning solely where a field declares them as its wildcard.

### Filter engine pipeline (`engines/filter/engine.py`)

1. **Compile** — `DimensionCompiler` (`core/compiler.py`) turns each `Dimension` into a backend-agnostic ternary expression template at construction.
2. **Bind** — context values become `__ctx_*` columns through `context_binding_expressions` (`core/context.py`). DECIMAL values are reduced using the field's declared rounding and DATETIME values truncated to its unit at binding.
3. **Evaluate** — all `__t_<dim>` ternary columns computed in one pass; survival filter `least(*t) >= 0`; `__specificity` = sum of matches.
4. **Select** — sort by hit-policy ordering keys, compute `__rank`, run hit-policy assertions, apply cardinality (`core/hit_policy.py`).

`explain(context, dimensions=None)` → `ExplainResult`: every rule scored (ternaries, `__survived`, `__specificity`), no survival filter and no hit-policy interaction.

`evaluate_batch(contexts, ...)` cross-joins contexts against the rules, computes the same ternaries and ranks per context. Returns `BatchRuleResult` (`core/batch_result.py`). A one-row batch agrees with the multi-row result.

**`execute_on` rule.** Every join between contexts and rules or entries states `execute_on` on the **rules/entries** side, so a small context relation is copied to the rules' backend (a temporary table) and the rules/entries never leave their backend. Relation rules join prepared contexts directly (Polars, same-connection or other-connection contexts); there is no re-hosting of rules onto the contexts' backend. Never invert this.

### Hit policies (`core/hit_policy.py`, `constants.HitPolicy`)

`collect` (default), `unique` (raises `HitPolicyViolationError` on >1 survivor), `first`, `priority` (needs `priority_field`), `any` (survivors must agree on `output_fields`, used for agreement, not projection; raises on conflict), `rule_order`. All three selection arguments are call-time; `RuleResult.select(policy, ...)` re-selects post-hoc unless the result was truncated.

### DecisionTable layers (`decision_table/`)

| # | Layer | Module(s) | Contract |
|---|---|---|---|
| 1 | Prevalidation | `prevalidate.py`, `strategies.py`, `fields.py` | `prevalidate(rules: Relation, metadata) -> ValidatedRules(rules, source_metadata, warnings)`; blocking breaches raise `DataContractError(breaches, warnings)`; `Finding(kind, severity, detail, rule_uuids)` |
| 2 | Construction | `build.py`, `stages.py`, `masks.py`, `folds.py`, `table.py` | `build_decision_table(validated, *, checkpoint=...) -> DecisionTable`; staged a–f relational construction under a Mountainash `materialization_scope`; contributor bitmasks in dynamic 63-bit words; `entry_sources(scope, entry_sources_id, source_uuid)` stores membership; SUM/MIN/MAX folds per output |
| 3 | Post-validation | — | deferred |
| 4 | Application | `apply.py` | `DecisionTable.match(contexts, scopes=None) -> MatchOperation`; `.fetch() -> MatchResult(matches)` with one record per matching entry per request (`request_id`, `scope`, `entry_id`, `entry_sources_id`, `specificity`, condition columns, outputs); no validation, rank filtering or folding; backend errors re-raised as `DecisionTableError` |
| 5 | Business post-aggregation | user code | documented worked example only (`docs/user-quickstart.md`); never part of this package's API |

Persistence (`persistence.py`): `DecisionTable.save(path, *, storage_profile=None)` writes eight Parquet files (`entries`, `entry_sources`, `pattern_conditions`, `ruleset`, `fields`, `dimensions`, `outputs`, `truthy`) through `mountainash-files`/`mountainash-transport`, then `manifest.json` (build id, size, SHA-256) last. `DecisionTable.load(path, *, connection=None, storage_profile=None)` reads without checking; `verify_saved_table(path, *, storage_profile=None) -> VerificationReport` checks bytes against the manifest. `DimensionsMetadata.to_tables()/from_tables()` serialize metadata as five flat relations (`core/metadata_tables.py`).

Context validation (`context_validation.py`) is a caller-run capability, separate from the table and the engine: `check_context_schema(schema_or_class, metadata)` compares a pydantic class, Relation schema or mapping with field declarations without reading rows; `validate_contexts(contexts, metadata)` checks a batch relationally (non-null unique `request_id`, non-finite numbers, unmapped truthy values, misused markers, null list elements) and raises one `ValueError` listing every invalid value.

Context identity is a protocol: a `request_id` plus attributes (or columns) named after the context fields. Python objects become a small in-process relation (`contexts_to_relation` keeps an unset field distinct from an explicit `None` through `model_fields_set`).

Mountainash workarounds still in code, each marked at its call site: #366 (integer `//`/`%` on DuckDB, safe because ordinals stay far below 2^53; `masks.py`), #367 (multi-column `explode`; `masks.py`), #368 (null ordering in windows; `stages.py`). Remove them when the upstream item closes.

### Backend purity (ENFORCED)

No module under `src/mountainash_rules/` may import `polars`, `ibis`, `narwhals` or `pyarrow` directly — only `mountainash.relations` / `mountainash.expressions` (and `mountainash-files`/`mountainash-transport` for storage, which exchange Arrow tables as values). `tests/test_backend_purity.py` enforces this across the whole package: every non-dunder module is parametrised, with no exemptions, and `pyarrow` is in `PROHIBITED_PACKAGES`. A genuinely unavoidable native escape must be tagged `# allow: <reason>` on the import line; none exist today. Tests may use Polars and Ibis to build fixtures.

## Match Strategies

`MatchStrategy` is a lowercase `StrEnum`, compiled in `core/compiler.py` (DecisionTable construction lowers each rule cell through `decision_table/strategies.py`):

| Strategy | Rule column format | Notes |
|---|---|---|
| `exact` / `not_equal` | scalar | any compatible field datatype |
| `range` | two columns (`range_min_field`/`range_max_field`) | closed numeric/temporal interval; a marker endpoint is unbounded |
| `min` / `max` | threshold | inclusive (`context >= rule` / `context <= rule`); numeric/temporal fields; FLOAT ordered dimensions require DECIMAL |
| `prefix` / `suffix` / `contains` | string | STR fields |
| `regex` | per-row pattern column | Mountainash `str.regex_contains` |
| `context_regex` | literal `regex_pattern` on the Dimension | 0 for a missing context value |
| `set_membership` / `set_exclusion` | list column | typed elements; null elements are invalid |
| `truthy` | value mapped through the field's `truthy` | rule and context outcomes compared as ternary equality |
| `presence` | `IS_NULL` / `IS_NOT_NULL` / wildcard | see the null model |
| `outcome` | int8 ternary | context supplies the computed ternary |

**Adding a strategy:** add the enum value in `core/constants.py`, validation in `core/dimension.py`, its `match` arm in `core/compiler.py`, a strategy class in `decision_table/strategies.py` (prevalidation, construction), and tests in `tests/core/test_compiler.py` and `tests/decision_table/`.

## Package Structure

```
src/mountainash_rules/
├── __init__.py                  # Public API (see __all__) — the only public import surface
├── context_validation.py        # check_context_schema, validate_contexts
├── core/                        # backend-agnostic building blocks (never imports from engines/)
│   ├── constants.py             # MatchStrategy, HitPolicy, DataType, BooleanCoercion, conventional markers
│   ├── dimension.py             # ContextField, Dimension, DimensionsMetadata
│   ├── type_families.py         # seven stateless type owners; declaration/configuration passed per call
│   ├── aggregate.py             # Aggregate, AggregateOp (SUM/MIN/MAX output folds)
│   ├── metadata_tables.py       # five flat metadata relations and canonical markers
│   ├── compiler.py              # DimensionCompiler (ternary expressions, filter engine)
│   ├── context.py               # context value extraction and binding expressions
│   ├── numeric.py               # shared physical-source, decimal, marker and set-canonicalization leaves
│   ├── set_wildcard.py          # set-dimension marker predicates
│   ├── result.py                # RuleResult (+ select()), ExplainResult
│   ├── hit_policy.py            # SelectionInfo, ordering, assertions, cardinality
│   └── batch_result.py          # BatchRuleResult
├── engines/
│   └── filter/
│       └── engine.py            # ExpressionRulesEngine (evaluate / explain / evaluate_batch)
└── decision_table/              # depends on core and engines/filter
    ├── prevalidate.py           # layer 1: prevalidate, ValidatedRules, Finding, DataContractError
    ├── fields.py                # field families and the shared region vocabulary
    ├── strategies.py            # one class per strategy, lowering cells into regions
    ├── stages.py                # construction stages a–f
    ├── masks.py                 # contributor membership as 63-bit words
    ├── folds.py                 # contribution folds
    ├── helpers.py               # scope selection and source checks
    ├── build.py                 # layer 2: build_decision_table
    ├── table.py                 # DecisionTable, derived entry metadata
    ├── apply.py                 # layer 4: MatchOperation, MatchResult
    ├── persistence.py           # save/load/verify through mountainash-files
    └── errors.py                # DecisionTableError
```

Dependency direction is one-way: `core` never imports from `engines/` or `decision_table/`; `decision_table` evaluates through `engines/filter`. Package module paths are private — import public names from the package root only (`from mountainash_rules import DecisionTable`).

## Build/Test/Lint Commands

- **Release gate (~11 min):** `POLARS_MAX_THREADS=2 hatch run test:test-target tests/core tests/filter tests/decision_table tests/context_validation tests/test_public_api.py tests/test_backend_purity.py -q --no-cov`
- **Tests (quick):** `hatch run test:test-quick`
- **Tests (coverage):** `hatch run test:test`
- **Single test:** `hatch run test:test-target tests/test_file.py::TestClass::test_name`
- **Backends:** `--backends polars`, `--backends ibis-duckdb` or `--backends all` (default `polars,ibis-duckdb`, see `tests/conftest.py`)
- **Remote persistence tests** run only when `MOUNTAINASH_RULES_SCRATCH_PROFILE` (a transport storage profile name) and `MOUNTAINASH_RULES_SCRATCH_PATH` (a writable directory on it, e.g. `s3://bucket/tmp`) are both set.
- **Benchmarks:** `hatch run test:test-perf`
- **Lint:** `hatch run ruff:check` / `hatch run ruff:fix`
- **Type check:** `hatch run mypy:check`
- **Build:** `hatch build`

## Dependencies

Core: `mountainash` (expressions/relations — the only DataFrame API package code may use), `mountainash-files` and `mountainash-transport` (persistence), `polars`, `ibis-framework[polars,duckdb]`, `duckdb<2.0`, `narwhals`, `pydantic`. Sibling checkouts of `mountainash`, `mountainash-files`, `mountainash-transport`, `mountainash-data`, `mountainash-settings`, and `mountainash-auth-client` are required for dev and CI (see `hatch.toml` and `.github/config/mountainash_dependencies.yml`). The package is pure Python.

## Branch Strategy & Releases

- `main`: production (only `release/*` and `hotfix/*` merge in); `develop`: development/RC; `feature/* | bugfix/* | hotfix/*` branch from develop.
- CalVer: `YYYY.MM.MICRO` (RC = `.0`, production = `.1`, patches increment).
- CI: pytest + coverage, ruff, radon on PRs; `build-and-release-package.yml` builds wheels + SBOMs.

## Code Style

- ruff formatting/linting; `import typing as t`; Google-style docstrings; CamelCase classes, snake_case functions, UPPER_CASE constants.
- `ValueError` for validation errors; custom exceptions (`HitPolicyViolationError`, `DataContractError`, `DecisionTableError`) where callers need to catch.
- TDD: failing test first. Test markers: unit, integration, performance, benchmark.
- Keep package code backend-pure (see above); use ternary encoding for match values.
- Wildcards are declared per `ContextField`; never infer null as a wildcard.

## Related Documentation

- `docs/user-quickstart.md` — user guide, including the layer 5 worked example.
- `docs/backend-support.md` — backend matrix, DuckDB pin and known upstream items.
- `docs-site/` — the published documentation site (mkdocs).
- `mountainash-central/04.planning/mountainash-rules/superpowers/{specs,plans}/` and `mountainash-central/01.principles/mountainash-rules/` — design, plans and principles (see the location map above).

## License

Apache-2.0
