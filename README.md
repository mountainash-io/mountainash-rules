# mountainash-rules

![Python](https://img.shields.io/badge/python-3.12-blue) ![License](https://img.shields.io/badge/license-Apache--2.0-green) ![Backends](https://img.shields.io/badge/backends-Polars%20%7C%20Ibis--DuckDB-purple)

Business rules that live in code are slow to change: every update needs a code review, a deploy and a release cycle. This package moves the logic into **metadata**. Rules are rows in a table, dimensions declare how each column matches a context, and evaluation is relational, built on [mountainash](https://github.com/mountainash-io/mountainash) relations and expressions. Change a rule, reload the table, and the new behaviour applies. No redeploy, no branching logic to maintain.

It provides two engines over one rule model:

- **`ExpressionRulesEngine`** scores every rule against a context (or a batch of contexts) in one vectorised pass, keeps the rules no dimension contradicts, ranks them by specificity and applies a hit policy. Every decision can be explained, dimension by dimension.
- **`DecisionTable`** cuts rules into non-overlapping *entries* once, folds the contributions (`SUM`, `MIN`, `MAX`) of every rule that covers each entry, and answers contexts by lookup, returning every matching entry. A built table can be saved, loaded and verified.

Polars and Ibis-DuckDB are the supported and tested backends ([backend support](docs/backend-support.md)).

### Why metadata, not code?

- **Rules change faster than code.** Pricing tiers, eligibility criteria and fraud thresholds shift weekly. When rules are data, a product owner updates a table and the engine picks it up.
- **Rules are auditable.** Every rule is a row. You can diff two rule sets, version them, and explain why a context matched, because each dimension reports a ternary outcome (match, unknown, non-match).
- **No per-rule Python branching.** Evaluation is expressed over the rules relation; the work still depends on rule count, strategies and backend.

## Installation

```bash
git clone https://github.com/mountainash-io/mountainash-rules.git
cd mountainash-rules
hatch env create
```

The package is pure Python. Resolve the configured `mountainash` dependency at delivered merge `b9c0ab4ab6380712c89f27c8d083f658278d4593` or a descendant, together with `mountainash-files`, `mountainash-transport`, `mountainash-data` and `mountainash-settings` (see `hatch.toml`). This requirement names a delivered dependency revision, not an invented release version or checkout layout. Keep resolved dependency revisions and installed packages aligned when comparing local results with CI; an existing Hatch environment can contain older non-editable dependency copies.

## Quick Start

```python
import polars as pl
from mountainash_rules import (
    ContextField, DataType, Dimension, DimensionsMetadata, ExpressionRulesEngine,
    MatchStrategy,
)

# 1. Rules are rows. "*" is the wildcard of the string fields; null is the wildcard of spend.
rules = pl.DataFrame({
    "rule_name": ["premium_au", "standard_au", "global_fallback"],
    "region":    ["AU", "AU", "*"],
    "tier":      ["premium", "*", "*"],
    "spend_min": pl.Series([1000, 0, None], dtype=pl.Int64),
    "spend_max": pl.Series([9999, 9999, None], dtype=pl.Int64),
    "discount":  [15, 10, 0],
})

# 2. Declare the context fields and how each dimension compares them with a rule column.
metadata = DimensionsMetadata(
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="tier", data_type=DataType.STR, wildcard="*"),
        ContextField(name="spend", data_type=DataType.INT, wildcard_declared=True),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="tier", context_field="tier"),
        Dimension(dimension_name="spend", context_field="spend",
                  match_strategy=MatchStrategy.RANGE,
                  range_min_field="spend_min", range_max_field="spend_max"),
    ),
)

# 3. Build the engine (compiles expressions once).
engine = ExpressionRulesEngine(rules, metadata)

# 4. Evaluate a context.
result = engine.evaluate({"region": "AU", "tier": "premium", "spend": 1500})
print(result.survivors.select("rule_name", "discount", "__specificity", "__rank"))
# premium_au 15 3 1 / standard_au 10 2 2 / global_fallback 0 0 3

print(result.explain("standard_au"))   # {"region": 1, "tier": 0, "spend": 1}
```

All three rules survive; `premium_au` matched three dimensions, `standard_au` two (its tier is a wildcard) and `global_fallback` none. A rule is eliminated only by a −1; a missing or null context value scores 0 (unknown), so it never eliminates a rule and never earns specificity.

## Wildcards and null

- A `ContextField` declares **one** wildcard marker, a typed value such as `"*"` or null (`wildcard_declared=True`). Every dimension on the field uses it.
- **Null is never an implicit wildcard.** A rule-side null is a don't-care only where the field declares null as its wildcard; `prevalidate` rejects it elsewhere.
- A `PRESENCE` dimension asks whether a field is null: rule cells `IS_NULL`, `IS_NOT_NULL` or the wildcard.
- `UNKNOWN`, `UNKNOWN_NUMERIC`, `UNKNOWN_DATE` and `UNKNOWN_DATETIME` are conventional marker values; they mean nothing unless a field declares them.

## Match Strategies

| Strategy | Rule value | Notes |
|---|---|---|
| `exact`, `not_equal` | a value | any compatible field type |
| `range` | two columns | closed interval; a marker endpoint is unbounded |
| `min`, `max` | a threshold | inclusive; numeric or temporal fields |
| `prefix`, `suffix`, `contains` | a string | STR fields |
| `regex` | a pattern per rule | Mountainash `str.regex_contains` |
| `context_regex` | `regex_pattern` on the Dimension | 0 for a missing context value |
| `set_membership`, `set_exclusion` | a list | typed elements; null elements are invalid |
| `truthy` | a value mapped through the field's `truthy` mapping | rule and context outcomes compared as ternaries |
| `presence` | `IS_NULL`, `IS_NOT_NULL`, wildcard | see above |
| `outcome` | an int8 ternary | the context supplies the computed ternary |

Field types are `STR`, `INT`, `FLOAT`, `DECIMAL` (precision, scale and rounding declared), `BOOL`, `DATE` and `DATETIME` (unit and timezone declared). `FLOAT` cannot be ordered; use `DECIMAL`. `BooleanCoercion` flags widen which original values a `BOOL` field reads as true or false.

## Hit Policies

`HitPolicy` is a **call-time** argument of `evaluate`, `evaluate_batch` and `RuleResult.select`, together with `priority_field` and `output_fields`:

| Policy | Result |
|---|---|
| `collect` (default) | every survivor, by specificity |
| `rule_order` | every survivor, in rule order |
| `first` | one survivor, rule order wins |
| `priority` | one survivor, by `priority_field` |
| `unique` | at most one; more raises `HitPolicyViolationError` |
| `any` | survivors must agree on `output_fields`; one is returned |

## Batch Evaluation

```python
contexts = pl.DataFrame({
    "request_id": ["a", "b"],
    "region": ["AU", "XX"], "tier": ["premium", None], "spend": [1500, None],
})
batch = engine.evaluate_batch(contexts, context_id_field="request_id")
batch.survivors            # __context_id, rule columns, __rank, __specificity, ...
batch.matched_context_ids  # ['a', 'b']
```

Every context is scored against every rule in one pass and ranked per context. `chunk_size` bounds the cross join without changing results. Contexts are copied to the rules' backend; the rules never move. The engine does not validate contexts or request ids.

## DecisionTable

```python
from mountainash.relations import relation
from mountainash_rules import (
    Aggregate, build_decision_table, check_context_schema, prevalidate, validate_contexts,
)

# metadata also names uuid_column, scope_column and the outputs (Aggregate declarations);
# rules carry a UUID, a scope and a contribution per output.
validated = prevalidate(relation(rules_table), table_metadata)    # layer 1: ValidatedRules or DataContractError
table = build_decision_table(validated)                           # layer 2: DecisionTable

check_context_schema(ContextModel, table_metadata)                # context validation, run by the caller
validate_contexts(contexts, table_metadata)

result = table.match(contexts).fetch()                            # layer 4: every matching entry
result.matches                                                    # request_id, scope, entry_id, specificity, outputs, ...

table.save("/data/rates/home_loans")                              # eight Parquet files + manifest
loaded = DecisionTable.load("/data/rates/home_loans")             # or load(..., connection=duckdb_connection)
verify_saved_table("/data/rates/home_loans")                      # VerificationReport
```

The five layers are prevalidation, construction, post-validation (deferred), application and business post-aggregation. Application returns every matching entry; combining several of them (for example, adding a base rate and two margins) is your domain's job. [The user quickstart](docs/user-quickstart.md) walks through a complete home-loan example, including that layer 5 calculation.

## Flat Metadata Tables

`DimensionsMetadata.to_tables()` and `from_tables()` serialise metadata as five flat relations: `ruleset`, `fields`, `dimensions`, `outputs` and `truthy`. Typed wildcard markers are stored as canonical text and parsed with the declared field type.

## Backend Support

Polars and Ibis-DuckDB are the supported and tested backends; `duckdb` is pinned `<2.0` (central backlog #363). Package code imports no DataFrame library directly (`tests/test_backend_purity.py` enforces this, including `pyarrow`). See [`docs/backend-support.md`](docs/backend-support.md) for the matrix, where work executes and the known upstream items.

## Textbook

The [production textbook](https://docs.mountainash.io/mountainash-rules/) is built from `main`; the [development textbook](https://docs.mountainash.io/mountainash-rules/dev/) is built from `develop`. Each push to either branch builds both snapshots and publishes them together. A failed build leaves the previous paired site live.

For initial activation, merge the textbook changes into both branches and configure Pages, its environment, and the shared custom domain first. Then set the repository Actions variable `TEXTBOOK_PUBLISHING_ENABLED` to `true` and manually dispatch `deploy-textbook.yml`. Until enabled, publishing runs are skipped; a one-sided bootstrap cannot deploy an incomplete site.

The source lives in `docs-site/`: `chapter-plan.md` (the book plan and writing rules) and `site/` (MkDocs configuration and the textbook Markdown). Preview locally without installing the source package or sibling repositories:

```bash
uv run --no-project --with-requirements docs-site/requirements.txt \
  python -m mkdocs serve --config-file docs-site/site/mkdocs.yml
```

## Development

| Command | Description |
|---------|-------------|
| `hatch run test:test-quick` | Run tests (no coverage) |
| `hatch run test:test` | Run tests with coverage reports |
| `hatch run test:test-target-quick tests/path.py -v` | Run specific tests |
| `hatch run test:test-perf` | Run performance benchmarks |
| `hatch run ruff:check` | Lint |
| `hatch run ruff:fix` | Lint + auto-fix |
| `hatch run mypy:check` | Type check |
| `hatch run radon:radon-cc` | Cyclomatic complexity |

The release gate:

```bash
POLARS_MAX_THREADS=2 hatch run test:test-target tests/core tests/filter tests/decision_table \
    tests/context_validation tests/test_public_api.py tests/test_backend_purity.py -q --no-cov
```

Backend-dependent tests run on Polars and Ibis-DuckDB by default; choose with `--backends polars`, `--backends ibis-duckdb` or `--backends all`. Persistence tests against remote storage run only when `MOUNTAINASH_RULES_SCRATCH_PROFILE` and `MOUNTAINASH_RULES_SCRATCH_PATH` are set.

## Architecture

The engine uses **signed-integer ternary logic** (−1 = non-match, 0 = unknown or don't-care, 1 = match) to evaluate each dimension independently, then combines the results in a single vectorised pass:

1. **Compile**: `DimensionCompiler` converts dimension metadata into backend-agnostic expression templates at construction time.
2. **Bind**: context values are bound to typed columns (DECIMAL reduced and DATETIME truncated as declared).
3. **Evaluate**: all dimension expressions run in one pass, producing a ternary per dimension per rule.
4. **Rank and select**: rules with any −1 are eliminated; survivors are ordered by the hit policy and ranked.

A `DecisionTable` evaluates its entries through the same engine. Package code uses only `mountainash.relations` and `mountainash.expressions`. See [AGENTS.md](AGENTS.md) for the full architecture.

## Mountain Ash Ecosystem

This package is part of the [Mountain Ash](https://github.com/mountainash-io) data framework ecosystem.

## License

Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.
