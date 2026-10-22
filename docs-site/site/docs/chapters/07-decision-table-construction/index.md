---
title: "Chapter 7: Prevalidating Rules and Building a DecisionTable"
description: "Layers 1 and 2: check authored rules with prevalidate, then build a physicalised DecisionTable of entries."
---

# Chapter 7: Prevalidating Rules and Building a DecisionTable

The expression engine scores every rule for every context. A `DecisionTable` does the combinatorial work once, ahead of time. Its rules are cut into non-overlapping **entries**, each entry carries the folded outputs of every rule that covers it, and matching a context becomes a lookup. This chapter covers the first two layers: `prevalidate` and `build_decision_table`.

## Rules for a DecisionTable

A DecisionTable's rules table has more structure than the expression engine's:

- a **UUID column** (`DimensionsMetadata.uuid_column`) that identifies each rule;
- a **scope column** (`scope_column`) that groups rules; each scope is built, and later matched, on its own;
- the **condition columns** that the dimensions read;
- a **contribution column** for each output, named by an `Aggregate`: the value that a rule adds to the output of any entry it covers.

```python
import polars as pl
from mountainash.relations import relation
from mountainash_rules import (
    Aggregate, ContextField, DataType, Dimension, DimensionsMetadata,
    build_decision_table, prevalidate,
)

metadata = DimensionsMetadata(
    uuid_column="rule_id",
    scope_column="scope",
    fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
    dimensions=(Dimension(dimension_name="region", context_field="region"),),
    outputs=(Aggregate(scope="fees", output_name="fee", contribution_column="fee",
                       data_type=DataType.INT),),
)
rules = pl.DataFrame({
    "rule_id": ["F1", "F2"],
    "scope":   ["fees", "fees"],
    "region":  ["EU", "*"],
    "fee":     [10, 1],
})
```

Rule F1 charges a fee of 10 to EU customers; F2 charges 1 to everybody. An EU customer is covered by both. What fee should the table report for the EU?

## Layer 1: prevalidation

```python
validated = prevalidate(relation(rules), metadata)
print(type(validated).__name__, validated.warnings)
```

```text
ValidatedRules ()
```

`prevalidate(rules, metadata)` takes a Mountainash `Relation` and the metadata. It captures the rules once (so later changes to the source do not affect the build) and takes its own copy of the metadata. It then checks, without executing any user rule logic:

- declared columns exist and have the declared types, and the metadata's fields and dimensions agree with each other;
- rule identity: UUIDs are unique within each scope;
- cells: a null in a cell is invalid unless the field declares null as its wildcard; set cells hold no null elements;
- contributions: every contribution column is present and non-null where it is needed;
- strategy and type pairings, and one consistent interpretation of each context field;
- duplicate and conflicting rules.

It collects **every** breach. If there is any, it raises `DataContractError`; otherwise it returns `ValidatedRules(rules, source_metadata, warnings)`.

```python
from mountainash_rules import DataContractError

duplicate_ids = pl.DataFrame({"rule_id": ["F1", "F1"], "scope": ["fees", "fees"],
                              "region": ["EU", "US"], "fee": [10, 1]})
null_cell = pl.DataFrame({"rule_id": ["F1", "F2"], "scope": ["fees", "fees"],
                          "region": [None, "EU"], "fee": [10, 1]})
for bad in (duplicate_ids, null_cell):
    try:
        prevalidate(relation(bad), metadata)
    except DataContractError as error:
        for finding in error.breaches:
            print(finding.kind, finding.severity, finding.detail, finding.rule_uuids)
```

```text
duplicate_uuid error Duplicate UUID in scope 'fees' ('F1',)
null_rule_cell error 'region' is null without a null wildcard ('F1',)
```

| `Finding` attribute | Meaning |
|---|---|
| `kind` | A short code such as `duplicate_uuid`, `duplicate_rule`, `conflicting_rule`, `null_rule_cell`, `null_contribution`, `unsatisfiable_rule`. |
| `severity` | `"error"` blocks construction; `"warning"` is reported and construction continues. |
| `detail` | A human-readable description. |
| `rule_uuids` | The rules involved. |

Two rules with the same normalised conditions are **duplicates** if their outputs are equal and **conflicts** if they differ. Both are errors, because the table could not say what the entry's value should be. `DataContractError.breaches` lists the errors; `DataContractError.warnings` lists warnings discovered at the same time.

A rule that can never match, such as an empty interval or one that requires a field to be both null and equal to a value, is a *warning*: it appears in `validated.warnings`.

Prevalidation is separate from the expression engine on purpose. The engine evaluates what it is given; prevalidation is where the rules table is held to account.

## Layer 2: construction

```python
table = build_decision_table(validated)
print(table.entries.to_polars().sort("entry_id"))
```

```text
shape: (2, 5)
┌──────────┬───────┬────────┬──────────────────┬─────┐
│ entry_id ┆ scope ┆ region ┆ entry_sources_id ┆ fee │
│ ---      ┆ ---   ┆ ---    ┆ ---              ┆ --- │
│ i64      ┆ str   ┆ str    ┆ i64              ┆ i64 │
╞══════════╪═══════╪════════╪══════════════════╪═════╡
│ 0        ┆ fees  ┆ *      ┆ 0                ┆ 1   │
│ 1        ┆ fees  ┆ EU     ┆ 1                ┆ 11  │
└──────────┴───────┴────────┴──────────────────┴─────┘
```

The table has two entries. The EU entry carries `10 + 1 = 11`, because both F1 and F2 cover it; the wildcard entry carries F2's `1` alone. Construction receives no context: it folds contributions once, so matching never has to.

### What an entry is

An **entry** is one covered cell of a scope: a set of context values that are treated alike, with one `entry_id` and the folded outputs. Its columns depend on the fields' types:

| Field family | Entry columns | Cells |
|---|---|---|
| `STR`, `BOOL` (unordered) | the field's own column | each value that the scope's rules name, plus one wildcard cell for "any other value" |
| `INT`, `DECIMAL`, `DATE`, `DATETIME` (ordered) | `<field>__lo`, `<field>__hi` | closed intervals cut at the rules' boundary values |
| `TRUTHY`, `PRESENCE`, `OUTCOME`, pattern dimensions | an int8 column | the required ternary outcome |

Ordered fields show the cutting most clearly. Rule T1 covers terms 1 to 5 and adds 2; rule T2 covers terms of 3 or more and adds 5.

```python
term_metadata = DimensionsMetadata(
    uuid_column="rule_id",
    scope_column="scope",
    fields=(ContextField(name="term", data_type=DataType.INT, wildcard_declared=True),),
    dimensions=(
        Dimension(dimension_name="t_range", context_field="term",
                  match_strategy="range", range_min_field="lo", range_max_field="hi"),
        Dimension(dimension_name="t_min", context_field="term", match_strategy="min"),
    ),
    outputs=(Aggregate(scope="rates", output_name="rate", contribution_column="rate",
                       data_type=DataType.INT),),
)
term_rules = pl.DataFrame({
    "rule_id": ["T1", "T2"],
    "scope":   ["rates", "rates"],
    "lo":      pl.Series([1, None], dtype=pl.Int64),
    "hi":      pl.Series([5, None], dtype=pl.Int64),
    "t_min":   pl.Series([None, 3], dtype=pl.Int64),
    "rate":    [2, 5],
})
term_table = build_decision_table(prevalidate(relation(term_rules), term_metadata))
print(term_table.entries.to_polars().sort("entry_id"))
```

```text
shape: (3, 6)
┌──────────┬───────┬──────────┬────────────┬──────────────────┬──────┐
│ entry_id ┆ scope ┆ term__lo ┆ term__hi   ┆ entry_sources_id ┆ rate │
│ ---      ┆ ---   ┆ ---      ┆ ---        ┆ ---              ┆ ---  │
│ i64      ┆ str   ┆ i64      ┆ i64        ┆ i64              ┆ i64  │
╞══════════╪═══════╪══════════╪════════════╪══════════════════╪══════╡
│ 0        ┆ rates ┆ 1        ┆ 2          ┆ 0                ┆ 2    │
│ 1        ┆ rates ┆ 3        ┆ 5          ┆ 2                ┆ 7    │
│ 2        ┆ rates ┆ 6        ┆ 2147483647 ┆ 1                ┆ 5    │
└──────────┴───────┴──────────┴────────────┴──────────────────┴──────┘
```

The two rules' boundaries cut the term axis into the cells `[1, 2]`, `[3, 5]` and `[6, ∞)`. Terms 3 to 5 are covered by both rules and fold to `2 + 5 = 7`. The upper bound of an unbounded interval is the largest value of the 32-bit integer range. Terms below 1 are covered by no entry, so a match there returns nothing from this scope. For an ordered field, intervals never overlap; a context falls into at most one entry per scope.

### Folding

Each output is declared by an `Aggregate` and folded with its `operation`: `SUM` (the default), `MIN` or `MAX`. A rule contributes **once** to each cell it covers. A `DECIMAL` output carries its declared precision, scale and rounding.

### Provenance: `entry_sources`

`entry_sources_id` identifies the *set* of rules behind an entry. Entries with the same contributors within a scope share an id. The set itself is stored relationally:

```python
print(term_table.entry_sources.to_polars().sort("scope", "entry_sources_id", "source_uuid").rows())
```

```text
[('rates', 0, 'T1'), ('rates', 1, 'T2'), ('rates', 2, 'T1'), ('rates', 2, 'T2')]
```

Entry 1 has `entry_sources_id` 2, whose sources are `T1` and `T2`. Join `entry_sources` on `(scope, entry_sources_id)` whenever a match must be explained in terms of the authored rules. Ids and entry numbers are local to one built table: `entry_id` numbers the entries by the sorted `(scope, cell columns)`, never by row order.

### The other parts of a table

| Attribute | Content |
|---|---|
| `entries` | One row per entry: `entry_id`, `scope`, the cell columns, `entry_sources_id` and the folded outputs. |
| `entry_sources` | `(scope, entry_sources_id, source_uuid)`. |
| `pattern_conditions` | For regex, prefix, suffix, contains and context-regex dimensions: one row per derived int8 column, with `column`, `context_field`, `strategy` and `pattern`. Empty when the rules use no patterns. |
| `source_metadata` | The metadata of the authored rules. |
| `build_id` | A SHA-256 of the rules' content and the metadata. Equal inputs give equal ids. |

`table.entry_metadata` is derived from `source_metadata` and `pattern_conditions` alone, so a table rebuilt from its saved parts is read the same way ([Chapter 10](../10-decision-table-internals/index.md)).

### Build options

`build_decision_table(validated, *, checkpoint=CheckpointPolicy.EVERY_STAGE)`. The Mountainash `CheckpointPolicy` decides which reusable intermediates are copied during construction (`EVERY_STAGE`, `NEVER`, or `CheckpointPolicy.every(k)`). The table is identical whichever you choose; only the cost differs. Construction logs the backend executions it issues at its stage boundaries on the `mountainash_rules.decision_table.build` logger.

## Scopes

Each scope is built independently, so rules of different scopes never combine. A home-loan table might have one scope for the base rate, one for an advertised margin and one for a discretionary margin; each produces its own entries and its own output. [Chapter 8](../08-applying-decision-tables/index.md) matches contexts against them and [Chapter 8's last section](../08-applying-decision-tables/index.md#layer-5-business-post-aggregation) combines the outputs.

## Next steps

[Chapter 8](../08-applying-decision-tables/index.md) validates contexts, matches them, and saves and loads a table.

## Source references

- `src/mountainash_rules/decision_table/prevalidate.py` — `prevalidate`, `ValidatedRules`, `Finding`, `DataContractError`.
- `src/mountainash_rules/decision_table/build.py` — `build_decision_table`.
- `src/mountainash_rules/decision_table/table.py` — `DecisionTable`.
