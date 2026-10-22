---
title: "Chapter 4: Using the Expression Rules Engine"
description: "Construct an ExpressionRulesEngine, evaluate one context, and read ranked rule results."
---

# Chapter 4: Using the Expression Rules Engine

`ExpressionRulesEngine` evaluates one context against every row of a rules table and returns the rows that no dimension contradicts, ranked by specificity. Construct the engine once from a table and `DimensionsMetadata`, then evaluate as many contexts as you like.

This chapter follows one single-context workflow. [Chapter 2](../02-shared-rule-model/index.md) defines the metadata and [Chapter 3](../03-matching-concepts/index.md) defines the ternary outcomes. Here those outcomes become a result you can read.

## Construct an engine and evaluate a context

A discount library has a broad fallback, a regional rule and a regional-and-tier rule. `"*"` is the wildcard of the string fields and null is the wildcard of `spend`. `discount` is an output column: it passes through the engine and is never compared.

```python
import polars as pl
from mountainash_rules import (
    ContextField, DataType, Dimension, DimensionsMetadata, ExpressionRulesEngine,
    MatchStrategy,
)

rules = pl.DataFrame({
    "rule_name": ["premium_au", "standard_au", "global_fallback"],
    "region":    ["AU", "AU", "*"],
    "tier":      ["premium", "*", "*"],
    "spend_min": pl.Series([1000, 0, None], dtype=pl.Int64),
    "spend_max": pl.Series([9999, 9999, None], dtype=pl.Int64),
    "discount":  [15, 10, 0],
})
metadata = DimensionsMetadata(
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="tier", data_type=DataType.STR, wildcard="*"),
        ContextField(name="spend", data_type=DataType.INT, wildcard_declared=True),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="tier", context_field="tier"),
        Dimension(
            dimension_name="spend", context_field="spend",
            match_strategy=MatchStrategy.RANGE,
            range_min_field="spend_min", range_max_field="spend_max",
        ),
    ),
)
engine = ExpressionRulesEngine(rules, metadata)

result = engine.evaluate({"region": "AU", "tier": "premium", "spend": 1500})
print(result.survivors.select("rule_name", "discount", "__specificity", "__rank"))
```

```text
shape: (3, 4)
┌─────────────────┬──────────┬───────────────┬────────┐
│ rule_name       ┆ discount ┆ __specificity ┆ __rank │
│ ---             ┆ ---      ┆ ---           ┆ ---    │
│ str             ┆ i64      ┆ i64           ┆ u32    │
╞═════════════════╪══════════╪═══════════════╪════════╡
│ premium_au      ┆ 15       ┆ 3             ┆ 1      │
│ standard_au     ┆ 10       ┆ 2             ┆ 2      │
│ global_fallback ┆ 0        ┆ 0             ┆ 3      │
└─────────────────┴──────────┴───────────────┴────────┘
```

All three rules survive: the context contradicts none of them. `premium_au` matched three dimensions, `standard_au` two (its `tier` is a wildcard) and `global_fallback` none. With the default `COLLECT` policy the survivors are ordered by specificity, highest first. Which of them *wins* is a separate decision, made by a [hit policy](../05-expression-results-and-policies/index.md).

### What the constructor takes

The constructor requires the rules and exactly one of `dimension_metadata` or `dimension_expressions`. Supplying neither or both raises `ValueError`, as does an empty definition. The constructor compiles each dimension into an expression template once; build the engine at startup and reuse it.

The rules may be a native table (Polars, Ibis on DuckDB) or a Mountainash `Relation`. The backend of the rules decides where evaluation runs, and survivor frames come back in that backend.

```python
import ibis
from mountainash.relations import relation

connection = ibis.duckdb.connect()
duck_rules = connection.create_table("rules", rules)
duck_engine = ExpressionRulesEngine(duck_rules, metadata)
print(duck_engine.evaluate({"region": "AU", "tier": "premium", "spend": 1500}).count)
```

```text
3
```

The engine does **not** validate its inputs. Rules are evaluated as given, a context is bound without being admitted or rejected, and a backend error propagates as that backend's own error. To check the rules, run `prevalidate` ([Chapter 7](../07-decision-table-construction/index.md)); to check contexts, run `check_context_schema` and `validate_contexts` ([Chapter 8](../08-applying-decision-tables/index.md)).

## The context

`evaluate` accepts a dictionary or a pydantic model. For a model, only fields that were set are supplied; the others are missing, which means unknown ([Chapter 3](../03-matching-concepts/index.md)).

```python
from pydantic import BaseModel

class Customer(BaseModel):
    region: str | None = None
    tier: str | None = None
    spend: int | None = None

result = engine.evaluate(Customer(region="AU", spend=1500))     # tier was never set
print(result.survivors.select("rule_name", "__specificity").rows())
```

```text
[('premium_au', 2), ('standard_au', 2), ('global_fallback', 0)]
```

`premium_au` stays possible because the unset `tier` scores 0, but it earns no specificity for that dimension. Each dimension reads the context field named by its `context_field`, so a context key is the *field* name, not the dimension name.

## Read the result

`evaluate()` returns a `RuleResult`.

```python
result = engine.evaluate({"region": "AU", "tier": "premium", "spend": 1500})
print(result.count)
print(result.best_match.select("rule_name", "discount").rows())
print(result.explain("standard_au"))
print(result.at_least(3).select("rule_name").rows())
print(result.active_dimensions)
```

```text
3
[('premium_au', 15)]
{'region': 1, 'tier': 0, 'spend': 1}
[('premium_au',)]
['region', 'tier', 'spend']
```

| Accessor | Meaning |
|---|---|
| `survivors` | The retained rows in policy order, with the rule's own columns plus `__rank`, `__rule_index`, `__specificity` and one `__t_<dimension>` column per dimension. |
| `best_match` | The retained row with the lowest rank. |
| `count` | The number of retained rows. |
| `explain(rule_name)` | `{dimension_name: ternary}` for one retained rule. |
| `at_least(n)` | The retained rows with at least `n` hard matches. |
| `active_dimensions` | The dimensions that took part in this evaluation. |
| `select(policy, ...)` | Re-applies a hit policy ([Chapter 5](../05-expression-results-and-policies/index.md)). |

The `__t_<dimension>` columns are **observability columns**. They make each survivor's ternary outcomes visible and power `RuleResult.explain`. Switch them off with `include_observability=False` when you only need the rows; rank, specificity and rule identity remain.

## Limit and project

```python
context = {"region": "AU", "tier": "premium", "spend": 1500}
top = engine.evaluate(context, top_n=1)                    # at most one row
sturdy = engine.evaluate(context, min_specificity=2)       # at least two hard matches
partial = engine.evaluate(context, dimensions=["region", "spend"])   # evaluate a subset
print(top.count, sturdy.count, partial.active_dimensions)
```

```text
1 2 ['region', 'spend']
```

`top_n` keeps at most *n* rows after ordering and after any `min_specificity` filter. `top_n` and `min_specificity` must be non-negative integers or `None`. `dimensions=None` evaluates every dimension in configured order; an explicit list must be non-empty and name distinct configured dimensions (an unknown name raises `KeyError`, a malformed list `ValueError`). Dimensions outside the list do not contribute to survival or specificity.

## Explain every rule

`engine.explain(context)` scores every rule with no survival filter, no ranking and no hit policy. It answers *why was this rule not chosen*.

```python
explanation = engine.explain({"region": "AU", "spend": 5})
print(explanation.frame.select(
    "rule_name", "__t_region", "__t_tier", "__t_spend", "__survived", "__specificity"
).rows())
print(explanation.count, explanation.survivors.shape[0], explanation.non_survivors.shape[0])
```

```text
[('premium_au', 1, 0, -1, False, 1), ('standard_au', 1, 0, 1, True, 2), ('global_fallback', 0, 0, 0, True, 0)]
3 2 1
```

`premium_au` is out because 5 is below its minimum spend. The `ExplainResult` also exposes `survivors`, `non_survivors`, `active_dimensions` and `count`.

## Boolean inputs

A `BOOL` field reads actual Booleans. `BooleanCoercion` widens the original values the engine reads as true or false. Combine flags with `|` and pass them to the constructor.

```python
from mountainash_rules import BooleanCoercion

member_rules = pl.DataFrame({"rule_name": ["members", "guests"], "is_member": [True, False]})
member_metadata = DimensionsMetadata(
    fields=(ContextField(name="is_member", data_type=DataType.BOOL, wildcard_declared=True),),
    dimensions=(Dimension(dimension_name="is_member", context_field="is_member"),),
)
strict = ExpressionRulesEngine(member_rules, member_metadata)
lenient = ExpressionRulesEngine(
    member_rules, member_metadata,
    boolean_coercion=BooleanCoercion.BOOLEAN_TEXT | BooleanCoercion.BINARY_NUMBERS,
)
for value in (True, "true", 1, "maybe"):
    print(repr(value), strict.evaluate({"is_member": value}).count, lenient.evaluate({"is_member": value}).count)
```

```text
True 1 1
'true' 2 1
1 2 1
'maybe' 2 2
```

| Flag | Additional values read as Boolean |
|---|---|
| `BINARY_NUMBERS` | numeric `0` and `1` |
| `BOOLEAN_TEXT` | `"true"` and `"false"` in any ASCII case, `"1"`, `"0"` |
| `NUMERIC_TRUTHINESS` | any finite number (0 is false, anything else true) |

A value that is not read as a Boolean is *unknown* (0), so both rules survive: the engine does not reject it. `"maybe"` stays unknown under either setting. Classification never chains through another domain: numeric `2` is true under `NUMERIC_TRUTHINESS`, but the text `"2"` is not a Boolean. Use `validate_contexts` to catch data you do not want evaluated.

## The expressions-only path

For a comparison the strategies cannot express, supply `dimension_expressions` instead of metadata: a dictionary from a dimension name to a ternary Mountainash expression. Context values are bound as `__ctx_<name>` columns, and with no metadata there is no type information, so a missing context key binds as null and the expression owns its interpretation. Because there is no metadata to infer outputs from, supply `output_fields` when you want the `ANY` hit policy.

```python
import mountainash.expressions as ma
from mountainash_rules import UNKNOWN
from mountainash_rules.core.constants import CTX_PREFIX

simple_rules = pl.DataFrame({"rule_name": ["r1", "r2"], "region": ["AU", "US"]})
custom = ExpressionRulesEngine(
    rules=simple_rules,
    dimension_expressions={
        "region": ma.t_col("region", unknown={UNKNOWN}).t_eq(
            ma.t_col(f"{CTX_PREFIX}region", unknown={UNKNOWN})
        ),
    },
)
print(custom.evaluate({"region": "AU"}).survivors.select("rule_name").rows())
```

```text
[('r1',)]
```

Provide metadata or expressions, never both.

## Next steps

[Chapter 5](../05-expression-results-and-policies/index.md) covers hit policies and re-selection; [Chapter 6](../06-batch-evaluation/index.md) evaluates a table of contexts.

## Source references

- `src/mountainash_rules/engines/filter/engine.py` — `ExpressionRulesEngine`: construction, scoring, ranking, result assembly.
- `src/mountainash_rules/core/result.py` — `RuleResult`, `ExplainResult`.
- `src/mountainash_rules/core/dimension.py` — the metadata consumed by the constructor.
