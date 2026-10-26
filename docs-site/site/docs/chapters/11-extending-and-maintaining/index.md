---
title: "Chapter 11: Extending and Maintaining the Package"
description: "Public boundaries, the change map for the expression engine and DecisionTable, tests, and a review checklist."
---

# Chapter 11: Extending and Maintaining the Package

The package has one public import surface (the package root), two engines that share one rule model, and a handful of invariants that keep them honest. This chapter says what may change where, and what must change together.

## Keep the public boundary small

- Import public names from the package root only: `from mountainash_rules import DecisionTable`. Module paths under `mountainash_rules.core`, `mountainash_rules.engines` and `mountainash_rules.decision_table` are private and may move.
- `tests/test_public_api.py` asserts the **exact** export set. Adding or removing a root export is a deliberate change to that test, to the user documentation and to the AGENTS guidance.
- `core` never imports from `engines` or `decision_table`; `decision_table` evaluates through `engines/filter`.
- **Backend purity** is enforced: no module under `src/mountainash_rules/` imports `polars`, `ibis`, `narwhals` or `pyarrow`. Use `mountainash.relations` and `mountainash.expressions`. `tests/test_backend_purity.py` parametrises every non-dunder module with no exemptions; an unavoidable native escape needs a `# allow: <reason>` tag on the import line, and there are none today.
- Context identity and the three validation capabilities are the caller's to run. The engines do not validate rules or contexts.

## What changes where

| Change | Where | Evidence that must stay valid |
|---|---|---|
| A new match strategy | enum value in `core/constants.py`; validation in `core/dimension.py`; its `match` arm in `core/compiler.py`; a strategy class in `decision_table/strategies.py` (and a field-family interpretation if it needs a new region); tests in `tests/core` and `tests/decision_table` | Engine scoring, prevalidation, construction and the finite-completion oracle all agree on both backends. |
| A field type or `ContextField` attribute | Declaration in `core/dimension.py` and `core/constants.py` (`DataType`); type behavior in `core/type_families.py` (checks, codecs, binding, admission, signatures and arithmetic); flat-table shape in `core/metadata_tables.py`. Context and region orchestration remain in `core/context.py` and `decision_table/fields.py`. | Metadata survives `to_tables` / `from_tables`; saved tables reload equal; binding converts and reduces as declared. |
| A hit policy or selection rule | `core/constants.py`, `core/hit_policy.py`, `core/result.py` | Single and batch evaluation agree; re-selection restrictions remain correct; policies stay call-time arguments. |
| An aggregate operation | `core/aggregate.py` (`AggregateOp`), `decision_table/folds.py`, `core/metadata_tables.py` | Folded outputs, persistence and `build_id` of the metadata. |
| Persistence format | `decision_table/persistence.py` and `DecisionTable.load` together | `verify_saved_table` and `load` agree on the file list; `build_id` and metadata round trip. |
| Context validation | Structural extraction and reporting in `context_validation.py`; type-specific admission and representability in `core/type_families.py` | `check_context_schema` and `validate_contexts` see what `match` will see (`contexts_to_relation`). |
| Result accessors | `core/result.py`, `core/batch_result.py`, `decision_table/apply.py` | `MatchResult.matches` columns stay documented. |

### Adding a strategy, step by step

1. Add the member to `MatchStrategy` and any type rules to `DimensionsMetadata` validation.
2. Add its comparison to `DimensionCompiler.compile_dimension` so the engine scores it. Wildcard and missing-context handling come from the shared `_ternary` wrapper; keep them.
3. Add a strategy class in `decision_table/strategies.py` that lowers a rule cell into a region, so `prevalidate` and `build_decision_table` can interpret it. If the comparison is not a region the existing families can cut, make it an int8 outcome column the way patterns are.
4. Test the strategy on Polars and Ibis-DuckDB: every rule-by-context cell of its scoring table in `tests/core/test_compiler.py`, and the equivalence of the built table with the engine in `tests/decision_table`.
5. Update the user quickstart, the book's strategy tables (Chapter 3) and `AGENTS.md`.

## Null and wildcard invariants

Changes to matching must preserve these:

- A field declares exactly one wildcard marker; every dimension on the field uses it, and the marker round-trips through the `fields` table as canonical text.
- Null is never an implicit wildcard. A rule-side null is a don't-care only where the field declares null as its wildcard.
- A context null or missing value is unknown (0) on a value dimension unless a truthy mapping maps null. On `PRESENCE`, an explicit null is a known fact; an omitted field is unknown.
- A −1 in any dimension eliminates a rule; unknowns never rescue it.

The worked null example of [Chapter 3](../03-matching-concepts/index.md) is a regression suite in `tests/filter/test_decision_table_semantics.py`.

## `execute_on`

Every join between contexts and rules or entries states `execute_on` on the rules or entries side. Contexts are small and often host-originated; the join runs where the rules live and Mountainash copies the contexts there. Do not re-host rules onto the contexts' backend.

## Tests

The release gate:

```bash
POLARS_MAX_THREADS=2 hatch run test:test-target tests/core tests/filter tests/decision_table \
    tests/context_validation tests/test_public_api.py tests/test_backend_purity.py -q --no-cov
```

Backend-dependent tests run on `polars` and `ibis-duckdb` by default; choose with `--backends`. Persistence tests against remote storage run only when `MOUNTAINASH_RULES_SCRATCH_PROFILE` and `MOUNTAINASH_RULES_SCRATCH_PATH` are set. Built tables are shared across tests through `tests/decision_table/table_fixtures.py`; a test that spies on, mutates or tests the build builds its own.

The finite-completion oracle (`tests/decision_table/oracles/`) is the reference for construction. It never touches construction code: for each context on a finite grid it asks the expression engine which authored rules survive, asks the same engine which entries match, and checks that the matched entries' sources are exactly those survivors (soundness and completeness) and that each entry's outputs are the fold of its sources' contributions.

## Known upstream workarounds

Two Mountainash behaviours are worked around in code, each commented at its call site: #366 (integer division on DuckDB; `masks.py`) and #376 (null ordering in windows; `stages.py`). `duckdb` is pinned `<2.0` (backlog #363). Remove each workaround when the upstream item closes.

## Review checklist

- Is every new public name exported from the package root and covered by the exact-export test?
- Does package code import only Mountainash relations and expressions?
- Are rule cells interpreted only through declared wildcards, never inferred from null?
- Do context joins state `execute_on` on the rules or entries side?
- Do the engine and the built table agree for the new behaviour, on both backends?
- Do `to_tables` / `from_tables` and `save` / `load` round-trip any new metadata?
- Are the user quickstart, this book and `AGENTS.md` updated for the change?

## Source references

- `AGENTS.md` — repository guidance, the architecture summary and commands.
- `tests/test_public_api.py`, `tests/test_backend_purity.py` — the two structural gates.
- `docs/backend-support.md` — the backend matrix and upstream items.
