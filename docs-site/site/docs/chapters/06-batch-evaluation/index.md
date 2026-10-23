---
title: "Chapter 6: Expression Engine Batch Evaluation"
description: "Evaluate a table of requests, read per-context results, and keep large batches manageable."
---

# Chapter 6: Expression Engine Batch Evaluation

Suppose your rules can quote a delivery price for one order, and now a table of orders is waiting for quotes. `evaluate_batch` takes that table. Each row is a **context**: the facts for one decision. The rules stay the same; the context changes from row to row. Every context is scored against every rule in one pass and ranked **per context**.

## Evaluate a table of requests

| Rule | Region | Product code | Price |
|---|---|---|---:|
| `au_standard` | AU | any (`*`) | 10.00 |
| `au_x` | AU | starts with `X-` | 8.00 |
| `nz_standard` | NZ | any (`*`) | 12.00 |

An Australian request with an `X-` code matches both Australian rules; we keep every match first and choose later.

```python
import polars as pl
from mountainash_rules import (
    ContextField, DataType, Dimension, DimensionsMetadata, ExpressionRulesEngine,
    HitPolicyViolationError, MatchStrategy,
)

rules = pl.DataFrame({
    "rule_name": ["au_standard", "au_x", "nz_standard"],
    "region":    ["AU", "AU", "NZ"],
    "code":      ["*", "X-", "*"],
    "price":     [10.0, 8.0, 12.0],
})
metadata = DimensionsMetadata(
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="code", data_type=DataType.STR, wildcard="*"),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="code", context_field="code",
                  match_strategy=MatchStrategy.PREFIX),
    ),
)
engine = ExpressionRulesEngine(rules, metadata)

contexts = pl.DataFrame({
    "request_id": ["r-100", "r-101", "r-102", "r-103"],
    "customer":   ["Ann", "Bo", "Cam", "Dee"],
    "region":     ["AU", "NZ", "XX", "AU"],
    "code":       ["X-1", "Z-9", "Q", "Q"],
})
batch = engine.evaluate_batch(contexts, context_id_field="request_id")
print(batch.survivors.select("__context_id", "rule_name", "price", "__specificity", "__rank"))
```

```text
shape: (4, 5)
┌──────────────┬─────────────┬───────┬───────────────┬────────┐
│ __context_id ┆ rule_name   ┆ price ┆ __specificity ┆ __rank │
│ ---          ┆ ---         ┆ ---   ┆ ---           ┆ ---    │
│ str          ┆ str         ┆ f64   ┆ i64           ┆ u32    │
╞══════════════╪═════════════╪═══════╪═══════════════╪════════╡
│ r-100        ┆ au_x        ┆ 8.0   ┆ 2             ┆ 1      │
│ r-100        ┆ au_standard ┆ 10.0  ┆ 1             ┆ 2      │
│ r-101        ┆ nz_standard ┆ 12.0  ┆ 1             ┆ 1      │
│ r-103        ┆ au_standard ┆ 10.0  ┆ 1             ┆ 1      │
└──────────────┴─────────────┴───────┴───────────────┴────────┘
```

Rows are ordered by context id, then rank. `r-100` matches both Australian rules, with `au_x` ranked first because it is more specific. `r-102` (region `XX`) matches nothing and has no row.

## Read the batch result

`evaluate_batch` returns a `BatchRuleResult`.

```python
print(batch.matched_context_ids)
print(batch.unmatched_context_ids(contexts))
print(batch.count)
print(batch.counts_per_context.sort("__context_id").rows())
print(batch.best_matches.select("__context_id", "rule_name", "price").rows())
print(batch.for_context("r-100").best_match.select("rule_name").rows())
print(batch.context_id_field)
```

```text
['r-100', 'r-101', 'r-103']
['r-102']
4
[('r-100', 2), ('r-101', 1), ('r-103', 1)]
[('r-100', 'au_x', 8.0), ('r-101', 'nz_standard', 12.0), ('r-103', 'au_standard', 10.0)]
[('au_x',)]
request_id
```

| Accessor | Meaning |
|---|---|
| `survivors` | The retained rows for all contexts, ordered by `__context_id` then `__rank`. |
| `best_matches` | The retained row with the lowest rank for each context. |
| `matched_context_ids` | Sorted ids with at least one retained row. |
| `unmatched_context_ids(contexts)` | Submitted ids absent from the retained rows; pass the original contexts. |
| `counts_per_context` | Retained row count (`__n`) per context that has any. |
| `for_context(id)` | A `RuleResult` for one context; an unmatched or never-submitted id gives an empty one. |
| `count`, `active_dimensions`, `context_id_field` | Retained row count, evaluated dimensions, and the input column the ids came from. |

The result always names the id column `__context_id`, whatever your input column is called; `batch.context_id_field` remembers the input name. Other context columns, such as `customer`, are not copied into the result: join `__context_id` back to the contexts if you need them. Accessors describe the *retained* rows, so a result you filtered with `top_n_per_context` omits contexts whose rows were all filtered out.

## Context identity

With `context_id_field`, the named column supplies the ids. The engine does not check them: duplicates or nulls would make two requests look like one, so supply a non-null unique column (`validate_contexts` checks a `request_id` column, see [Chapter 8](../08-applying-decision-tables/index.md)).

Without `context_id_field`, the ids are the zero-based row positions of the input, assigned once, before any chunking. Unmatched rows leave gaps, and the ids are only as stable as the input's order:

```python
generated = engine.evaluate_batch(contexts.drop("request_id"))
print(generated.matched_context_ids, generated.unmatched_context_ids(contexts.drop("request_id")))
```

```text
[0, 1, 3] [2]
```

Keep the original row order and count if you rely on generated ids.

## Hit policies and limits per context

The policies of [Chapter 5](../05-expression-results-and-policies/index.md) apply within each context, and `top_n_per_context` and `min_specificity` limit each context's rows.

```python
quotes = engine.evaluate_batch(
    contexts, context_id_field="request_id", hit_policy="first", top_n_per_context=1,
)
print(quotes.survivors.select("__context_id", "rule_name", "price").rows())

try:
    engine.evaluate_batch(contexts, context_id_field="request_id", hit_policy="unique")
except HitPolicyViolationError as error:
    print(error)
    print(error.offending.rows())
```

```text
[('r-100', 'au_standard', 10.0), ('r-101', 'nz_standard', 12.0), ('r-103', 'au_standard', 10.0)]
hit_policy=unique violated for context ids ['r-100']
[('r-100', 2)]
```

All offending contexts are reported before the exception is raised; no partial result is returned. `error.offending` is a summary with `__context_id` and `__n` (the survivor count for `unique`, the number of distinct output tuples for `any`). The `first` policy returned `au_standard` for `r-100` because it is first in rule order.

A one-row batch agrees with the single-context `evaluate`.

## Missing columns are unknown

A contexts table need not carry every field. A field with no column is missing (unknown, 0) for every row:

```python
partial = pl.DataFrame({"request_id": ["a"], "region": ["AU"]})
rows = engine.evaluate_batch(partial, context_id_field="request_id").survivors
print(rows.select("__context_id", "rule_name", "__t_region", "__t_code").rows())
```

```text
[('a', 'au_standard', 1, 0), ('a', 'au_x', 1, 0)]
```

A column that is present is supplied for every row of it, including its null cells, and null scores 0 like a missing value ([Chapter 3](../03-matching-concepts/index.md)).

## Reserved names

The engine adds internal columns to the contexts. Do not give a context column a name that begins with `__ctx_`, `__t_` or `__rule_`, or one of the reserved result names (such as `__context_id`, `__rank`, `__specificity` or `__rule_index`); the engine raises `ValueError` naming the reserved columns.

## Chunking

A batch is evaluated as a cross join of contexts and rules. `chunk_size` evaluates consecutive slices of the contexts so that each cross join stays small.

```python
chunked = engine.evaluate_batch(contexts, context_id_field="request_id", chunk_size=2)
print(chunked.survivors.equals(batch.survivors))
```

```text
True
```

Chunking changes the work per step, not the result: ranking and policy checks run inside each chunk, and the chunks are then combined; requests are never ranked against one another. It bounds the cross product, not total memory, because the accumulated results are still held. `chunk_size` is `None` or a positive integer.

## Empty inputs

An empty contexts table, or empty rules with a valid schema, produce the normal typed empty result; the engine does not fabricate a row.

```python
print(engine.evaluate_batch(contexts.head(0), context_id_field="request_id").count)
```

```text
0
```

## Backends

The rules decide where the batch runs. Contexts in Polars, on the same connection, or on another connection are joined on the rules' side: a context table is copied to the rules' backend, and the rules never move.

```python
import ibis

duck = ExpressionRulesEngine(ibis.duckdb.connect().create_table("rules", rules), metadata)
on_duck = duck.evaluate_batch(contexts, context_id_field="request_id")
print(type(on_duck.survivors).__name__, on_duck.matched_context_ids)
```

```text
Table ['r-100', 'r-101', 'r-103']
```

Polars and Ibis-DuckDB are the supported and tested backends.

## Reference

`evaluate_batch(contexts, *, context_id_field=None, dimensions=None, hit_policy=None, priority_field=None, output_fields=None, top_n_per_context=None, min_specificity=None, include_observability=True, chunk_size=None)`.

| Argument | Meaning |
|---|---|
| `contexts` | A table or Relation, one row per context. |
| `context_id_field` | `None` for generated ids, or the nonempty name of the id column. |
| `dimensions` | A subset of dimensions to evaluate, as for `evaluate`. |
| `hit_policy`, `priority_field`, `output_fields` | Per-context selection ([Chapter 5](../05-expression-results-and-policies/index.md)). |
| `top_n_per_context`, `min_specificity` | Non-negative limits applied per context. |
| `include_observability` | Keep the `__t_<dimension>` columns. |
| `chunk_size` | `None` or a positive integer. |

## Next steps

[Chapter 7](../07-decision-table-construction/index.md) turns a rules table into a DecisionTable.

## Source references

- `src/mountainash_rules/engines/filter/engine.py` — `evaluate_batch` and its chunked path.
- `src/mountainash_rules/core/batch_result.py` — `BatchRuleResult`.
- `src/mountainash_rules/core/context.py` — context binding.
