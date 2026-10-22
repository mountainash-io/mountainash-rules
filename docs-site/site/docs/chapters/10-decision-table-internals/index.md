---
title: "Chapter 10: Inside the DecisionTable"
description: "Column plans, regions, the six construction stages, contributor masks, derived entry metadata, application and persistence."
---

# Chapter 10: Inside the DecisionTable

This chapter explains how rules become entries and how entries are matched. It is for maintainers and for readers who need to reason about what a table contains. Module paths are private; import public names from the package root.

## Construction in one picture

```text
ValidatedRules
   │  a  bind rules into column plans; collect cut values / named values; derive patterns
   │  b  turn cut values into closed pieces
   │  c  compute each rule's coverage per column
   │  d  turn covered rows into contributor words; give each distinct set an entry_sources_id
   │  e  decode words into entry_sources; fold each declared output
   ▼  f  emit entries, entry_sources, pattern_conditions
DecisionTable
```

Every stage is a function over Mountainash relations, so all of it runs on the backend that holds the rules. Reusable intermediates are copied under a Mountainash `materialization_scope`, governed by the `checkpoint` policy of `build_decision_table`; the three terminal relations are always snapshotted so they outlive the scope. Backend execution counts are logged at each stage boundary.

## Regions and field families

Strategies and field families split the work between *what a rule cell means* and *how a field is cut*.

A **region** is the semantic description of what one rule cell covers on one field: a `point`, an `interval`, `everything`, a union of `members`, a `negation`, a pattern `matches`, or an `outcome`. A **strategy class** (`decision_table/strategies.py`, one per `MatchStrategy`) lowers a rule cell into a region; prevalidation uses the same lowering and reports `Unsatisfiable` regions (for example an interval whose minimum exceeds its maximum).

A **field family** (`decision_table/fields.py`) interprets regions for one field type:

| Family | Types | Cutting |
|---|---|---|
| `OrderedField` | `INT`, `DECIMAL`, `DATE`, `DATETIME` | cuts the field into closed intervals at the rules' boundary values; equality is a one-point interval |
| `UnorderedField` | `STR`, `BOOL` | one piece per value that a scope's rules name, plus one wildcard piece |
| `TruthyField` | patterns, `TRUTHY`, `PRESENCE`, `OUTCOME` | an int8 required ternary outcome per condition |

Region operands are either Python values (for semantic tests) or Mountainash expressions over the rules relation (for construction). Families never inspect values beyond validity checks, so both share one code path.

## Column plans

Stage a binds the rules into one **column plan** per column group of `entries`:

- a *positive* column per field: `<field>__lo` and `<field>__hi` for ordered fields, `<field>` for unordered ones. `EXACT`, `SET_MEMBERSHIP`, `RANGE`, `MIN` and `MAX` dimensions that share a field are conjoined into this one plan;
- a *negation* column per `NOT_EQUAL` or `SET_EXCLUSION` dimension, holding its authored value or list, or the field's wildcard;
- an *int8 required-outcome* column per `TRUTHY`, `PRESENCE`, `OUTCOME` and `CONTEXT_REGEX` dimension, and per *distinct pattern* of a `REGEX`, `PREFIX`, `SUFFIX` or `CONTAINS` dimension, named `<dimension>__pattern_<k>`, where `k` is the pattern's sorted ordinal.

A prefix dimension shows the effect. Three rules, with a negation on `region`:

```python
import polars as pl
from mountainash.relations import relation
from mountainash_rules import (
    Aggregate, ContextField, DataType, Dimension, DimensionsMetadata, MatchStrategy,
    build_decision_table, prevalidate,
)

metadata = DimensionsMetadata(
    uuid_column="rule_id", scope_column="scope",
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="code", data_type=DataType.STR, wildcard="*"),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="code", context_field="code", match_strategy=MatchStrategy.PREFIX),
        Dimension(dimension_name="region_ne", context_field="region",
                  rule_field="not_region", match_strategy=MatchStrategy.NOT_EQUAL),
    ),
    outputs=(Aggregate(scope="s", output_name="fee", contribution_column="fee",
                       data_type=DataType.INT),),
)
rules = pl.DataFrame({
    "rule_id": ["P1", "P2", "P3"], "scope": ["s", "s", "s"],
    "region": ["EU", "*", "*"], "code": ["X-", "*", "Y-"],
    "not_region": ["*", "*", "US"], "fee": [1, 2, 4],
})
table = build_decision_table(prevalidate(relation(rules), metadata))
print(table.entries.columns)
print(table.pattern_conditions.to_polars().sort("column").rows())
```

```text
['entry_id', 'scope', 'region', 'code__pattern_0', 'code__pattern_1', 'region_ne', 'entry_sources_id', 'fee']
[('code__pattern_0', 'code', 'prefix', 'X-'), ('code__pattern_1', 'code', 'prefix', 'Y-')]
```

The two prefixes become two int8 columns, and `pattern_conditions` records what each column means: pattern `X-` applied with the `prefix` strategy to context field `code`. An entry's `code__pattern_0` of 1 requires the context to start with `X-`; 0 does not constrain it.

## Cells, cut values and pieces

Stage a also produces, per plan, either the **cut values** of an ordered field or the **named values** of each scope for an unordered field, so that cells never mix scopes. Stage b turns cut values into closed pieces (the intervals of [Chapter 7](../07-decision-table-construction/index.md)); for unordered fields, a piece is a named value or the wildcard. Stage c computes, for each rule and each column, which pieces the rule covers, and emits the covered `(scope, source_uuid, cell columns)` rows.

Natural keys are always scope, plan, sorted cut or value, and rule UUID; never row order. That is what makes `entry_id` and `entry_sources_id` reproducible.

## Contributor masks and folds

Each rule in a scope gets one **contributor bit**. A cell's contributors are a set of such bits, held in dynamically sized words of 63 bits each: ⌈N/63⌉ `int64` words for N rules in a scope. Using 63 of the 64 bits keeps every word a non-negative `int64`. A 63-row lookup relation maps a bit index to its value; ordinals come from the rules' sorted natural UUIDs.

Stage d ORs coverage into words, then gives each distinct word tuple within a scope one `entry_sources_id`, in sorted word order. Stage e decodes each word tuple into `entry_sources(scope, entry_sources_id, source_uuid)` rows and folds each declared output once for each contributing rule of each contributor set (`SUM`, `MIN` or `MAX`; see `decision_table/folds.py`). Each rule contributes once to every cell it covers, however many pieces cover it.

Stage f numbers the entries (`entry_id` is the position of the cell when sorted by scope and cell columns) and emits the three terminal relations. Null keys sort differently on different backends, so `entry_id` numbering prefixes each key with a presence flag that sorts nulls first everywhere; this is one of the Mountainash workarounds recorded in `docs/backend-support.md`.

## How the engine reads entries

`DecisionTable.entry_metadata` is `derive_entry_metadata(source_metadata, pattern_conditions)`. It mirrors the column plans:

- the positive dimensions that share a field become one ordered-interval or value dimension on that field's own interpretation;
- each `NOT_EQUAL` or `SET_EXCLUSION` dimension keeps its own negation column;
- each authored `TRUTHY`, `PRESENCE` or `OUTCOME` dimension and every `pattern_conditions` column becomes a dimension with `MatchStrategy.OUTCOME`, reading a context field named `outcome__<column>`.

```python
for dimension in table.entry_metadata.dimensions:
    print(dimension.dimension_name, "<-", dimension.context_field, dimension.match_strategy.value)
```

```text
region <- region exact
region_ne <- region not_equal
code__pattern_0 <- outcome__code__pattern_0 outcome
code__pattern_1 <- outcome__code__pattern_1 outcome
```

Because `entry_metadata` is a function of the saved parts alone, a table loaded from disk reads exactly as the table that was built.

## Application

`MatchOperation.fetch()` runs these steps:

1. Filter `entries` to the selected scopes.
2. Restrict the derived pattern columns to those the selected entries reference.
3. Compute the context's ternary for each int8 outcome column (patterns, TRUTHY, PRESENCE) as an `outcome__<column>` value: pattern conditions through Mountainash `str.regex_contains`, PRESENCE from the context's presence companion column, TRUTHY through the field's mapping.
4. Call `ExpressionRulesEngine.evaluate_batch` once over the selected entries with `entry_metadata`; the engine scores every other dimension itself and handles the outcome columns through `MatchStrategy.OUTCOME`.
5. Return every matching entry with its specificity. Nothing is rank-filtered or folded.

`contexts_to_relation` converts a list of pydantic objects into a relation that has `request_id`, a column for each context field set on at least one object, and for each such field a presence companion column (unset 0, explicit `None` 1, a value −1), so that an unset field stays distinct from an explicit null. A field set on no object has neither column and reads as unset everywhere. Context joins state `execute_on` on the entries' side; the no-engine path (nothing selected, or nothing left to score) follows the same rule.

Backend errors raised during application are wrapped as `DecisionTableError`, unless they already are Mountainash Rules errors.

## Persistence

`persistence.py` saves and loads through `mountainash-files` and `mountainash-transport`. Arrow tables pass between Mountainash and Files as values, so this module imports neither Polars, Ibis nor PyArrow. The manifest, written last, records the `build_id` and each file's size and SHA-256; `verify_saved_table` streams each file and compares. `load` reads the manifest and the eight fixed file names without checking them, which keeps loading cheap and verification explicit.

## Invariants worth protecting

- Construction receives no context; application receives no rules.
- No module imports Polars, Ibis, Narwhals or PyArrow directly (`tests/test_backend_purity.py`).
- A change to a strategy must change `core/compiler.py` (engine), `decision_table/strategies.py` (lowering) and their tests together; the finite-completion oracle in `tests/decision_table/oracles/` judges built tables against the expression engine on a finite grid.
- `entry_metadata` must remain a function of `source_metadata` and `pattern_conditions` only.

## Source references

- `src/mountainash_rules/decision_table/stages.py` — construction stages a–f.
- `src/mountainash_rules/decision_table/fields.py`, `strategies.py` — regions, field families, strategy lowering.
- `src/mountainash_rules/decision_table/masks.py`, `folds.py` — contributor words and folds.
- `src/mountainash_rules/decision_table/table.py` — `DecisionTable`, `derive_entry_metadata`.
- `src/mountainash_rules/decision_table/apply.py` — application.
- `src/mountainash_rules/decision_table/persistence.py` — save, load, verify.
