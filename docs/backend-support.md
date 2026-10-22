# Backend support

`mountainash_rules` never imports a DataFrame library in package code. Relations are
Mountainash `Relation` objects and row logic is built with `mountainash.expressions`;
Mountainash compiles both to the backend that holds the data. `tests/test_backend_purity.py`
enforces this for every module under `src/mountainash_rules/`, and `pyarrow` is as prohibited as
Polars, Ibis and Narwhals. Arrow tables cross the persistence boundary only as values passed
between Mountainash and `mountainash-files`.

## Supported and tested backends

| Backend | Relation / connection | Status |
|---|---|---|
| Polars | in-process Polars | Supported and tested |
| Ibis-DuckDB | Ibis table on a DuckDB connection | Supported and tested |

Every behaviour test in `tests/core`, `tests/filter`, `tests/decision_table` and
`tests/context_validation` that depends on the backend runs on both (`--backends` defaults to
`polars,ibis-duckdb`; see `tests/conftest.py`). No other backend is part of the supported
surface; Pandas and Narwhals inputs are not supported.

## What each backend does

| Capability | Polars | Ibis-DuckDB |
|---|---|---|
| `ExpressionRulesEngine` over a rules table or Mountainash `Relation` | yes | yes |
| Match strategies: EXACT, NOT_EQUAL, RANGE, MIN, MAX, PREFIX, SUFFIX, CONTAINS, REGEX, CONTEXT_REGEX, SET_MEMBERSHIP, SET_EXCLUSION, TRUTHY, OUTCOME, PRESENCE | yes | yes |
| `prevalidate` and `build_decision_table` (staged construction with checkpoints) | yes | yes |
| `DecisionTable.match` and `MatchOperation.fetch` | yes | yes |
| `check_context_schema`, `validate_contexts` | yes | yes |
| `DecisionTable.save`, `DecisionTable.load`, `verify_saved_table` | yes (default load target) | yes (`DecisionTable.load(path, connection=...)`) |

The decision table's `entries`, `entry_sources` and `pattern_conditions` live on the backend that
built them. `DecisionTable.load` puts them in in-process Polars unless an Ibis-DuckDB
`connection` is given; the five metadata tables always load host-side. The caller keeps the
connection open while the table is in use.

## Where work executes

Contexts supplied as Python objects become a small in-process relation. Every join between
contexts and the entries or rules states `execute_on` on the entries' (rules') side, so
Mountainash copies the small context relation to the entries' backend as a temporary table and
the entries never leave their backend. `ExpressionRulesEngine` joins a context relation to a rules
`Relation` the same way, whether the contexts are Polars, on the same connection, or on another.
Backend errors propagate as the backend's own errors from the engine; `DecisionTable.match`
re-raises them as `DecisionTableError`.

Construction and application log the backend executions they issue at stage boundaries
(`mountainash_rules.decision_table.build` and `.apply` loggers).

## DuckDB pin

`duckdb` is pinned `<2.0` in `pyproject.toml`. Ibis 12 emits deprecated `->` list lambdas that
DuckDB 2.0 rejects (central backlog #363). Remove the pin when that item closes.

## Known upstream items

Mountainash behaviours the package still works around, each with a comment at the call site:

| Item | Behaviour | Where it is handled |
|---|---|---|
| Mountainash backlog #366 | Integer `//` and `%` are inexact on Ibis-DuckDB for very large operands | `decision_table/masks.py`: contributor ordinals stay far below 2^53, where the arithmetic is exact |
| Mountainash backlog #367 | A two-column `explode` zips on Polars but cross-joins on Ibis | `decision_table/masks.py`: one list column is exploded and the bit index is recovered as the value's rank |
| Mountainash backlog #368 | Null ordering in window and sort keys differs by backend | `decision_table/stages.py`: each key is preceded by a presence flag that sorts nulls first on every backend |

## Testing

```bash
POLARS_MAX_THREADS=2 hatch run test:test-target tests/core tests/filter tests/decision_table \
    tests/context_validation tests/test_public_api.py tests/test_backend_purity.py -q --no-cov
```

Choose backends with `--backends polars`, `--backends ibis-duckdb` or `--backends all`.
Persistence tests against remote storage are skipped unless both
`MOUNTAINASH_RULES_SCRATCH_PROFILE` (a `mountainash-transport` storage profile name) and
`MOUNTAINASH_RULES_SCRATCH_PATH` (a writable directory on that storage, for example
`s3://bucket/tmp`) are set.
