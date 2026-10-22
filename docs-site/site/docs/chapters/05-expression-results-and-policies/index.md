---
title: "Chapter 5: Expression Engine Hit Policies, Results and Explanations"
description: "Choose among surviving rules with a hit policy, re-select results, and read violations."
---

# Chapter 5: Expression Engine Hit Policies, Results and Explanations

When several rules survive, something must decide which ones the caller sees. That decision is a **hit policy**. It is a call-time choice: the policy, the priority column and the output columns are arguments of `evaluate`, `evaluate_batch` and `RuleResult.select`, not part of the metadata. The same engine can serve a "first match" caller and a "collect everything" caller.

## The example

```python
import polars as pl
from mountainash_rules import (
    ContextField, DataType, Dimension, DimensionsMetadata, ExpressionRulesEngine,
    HitPolicy, HitPolicyViolationError,
)

rules = pl.DataFrame({
    "rule_name": ["generic", "specific", "twin"],
    "region":    ["*", "AU", "AU"],
    "price":     [10, 8, 8],
    "salience":  [1, 10, 5],
})
metadata = DimensionsMetadata(
    fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
    dimensions=(Dimension(dimension_name="region", context_field="region"),),
)
engine = ExpressionRulesEngine(rules, metadata)
context = {"region": "AU"}
```

An Australian context matches all three rules: `specific` and `twin` with specificity 1, and `generic` with 0.

## The six policies

| Policy | Result |
|---|---|
| `collect` (default) | Every survivor, ordered by specificity (highest first), ties by rule order. |
| `rule_order` | Every survivor, in the order of the rules table. |
| `first` | One survivor: the first in rule order. |
| `priority` | One survivor: the highest `priority_field`, then specificity, then rule order. Requires `priority_field`. |
| `unique` | At most one survivor; more raises `HitPolicyViolationError`. |
| `any` | The survivors must agree on `output_fields`; one is returned, or `HitPolicyViolationError`. |

```python
for policy in ("collect", "rule_order", "first"):
    print(policy, engine.evaluate(context, hit_policy=policy).survivors.select("rule_name", "__rank").rows())
print("priority", engine.evaluate(context, hit_policy="priority", priority_field="salience")
      .survivors.select("rule_name").rows())
```

```text
collect [('specific', 1), ('twin', 2), ('generic', 3)]
rule_order [('generic', 1), ('specific', 2), ('twin', 3)]
first [('generic', 1)]
priority [('specific',)]
```

`first` returned `generic` because it is first in rule order, not because it is the most specific; if you want the most specific, use `collect` and take `best_match`, or `priority` with a priority column. A policy is a `HitPolicy` member or its lowercase string value; an unknown string raises `ValueError` and never silently becomes `collect`.

## Violations

`unique` and `any` are *assertions*. They fail loudly when the rule table does not meet the expectation behind them.

```python
try:
    engine.evaluate(context, hit_policy="unique")
except HitPolicyViolationError as error:
    print(error)
    print(error.policy, error.offending.select("rule_name").rows())
```

```text
hit_policy=unique but 3 rules survived
unique [('specific',), ('twin',), ('generic',)]
```

`error.offending` holds the complete ranked survivor frame for a single evaluation, and a grouped summary with `__context_id` and `__n` for a batch ([Chapter 6](../06-batch-evaluation/index.md)).

`any` compares the named output columns. It is an agreement check, not a projection.

```python
try:
    engine.evaluate(context, hit_policy="any", output_fields=["price"])
except HitPolicyViolationError as error:
    print(error)

agreeing = ExpressionRulesEngine(rules.filter(pl.col("rule_name") != "generic"), metadata)
print(agreeing.evaluate(context, hit_policy="any", output_fields=["price"])
      .survivors.select("rule_name", "price").rows())
```

```text
hit_policy=any but survivors disagree on outputs ['price']
[('specific', 8)]
```

`specific` and `twin` both price at 8, so `any` is satisfied once `generic` (priced 10) is out. `priority` without a `priority_field` raises `ValueError`.

## Re-select a result

`RuleResult.select(policy, priority_field=None, output_fields=None)` applies another policy to an evaluated result without re-running the match.

```python
result = engine.evaluate(context)                    # collect: the complete candidate set
print(result.select("first").survivors.select("rule_name").rows())
print(result.select("priority", priority_field="salience").survivors.select("rule_name").rows())
```

```text
[('generic',)]
[('specific',)]
```

Re-selection needs the **complete candidate basis**. A result that was limited (`top_n`, `min_specificity`) or reduced by a single-row policy (`first`, `priority`, `any`) may be missing rows, so `select` refuses it:

```python
limited = engine.evaluate(context, top_n=2)
try:
    limited.select("first")
except ValueError as error:
    print(error)
```

```text
select() on a possibly truncated result (filters or cardinality were applied); re-evaluate the complete candidate set instead
```

Unfiltered `collect`, `rule_order` and a successful `unique` result can be re-selected. Anything else: re-evaluate.

## Ordering, assertions, cardinality

Internally a policy is three steps, applied in this order: **order** the survivors (the policy's sort keys, then rule order as the final tiebreak), **assert** (`unique` and `any` raise here, before any limit, so a conflict still raises with `top_n=0`), then **cardinality** (`first`, `priority` and `any` keep one row; `top_n` keeps *n*).

`__rank` is 1-based and assigned after ordering and before caller filters. A filter does not renumber it: if `min_specificity` removes the rank-1 row, rank 2 is the first retained row, and `best_match` returns the lowest retained rank rather than assuming rank 1.

## Output fields

`output_fields` names the rule columns the `any` policy compares; it is an agreement check, not a projection. Each name must be a column of the rules table, without duplicates or empty names.

When you omit it on a metadata-backed engine, the engine infers the outputs: every rule column that is not a dimension's condition column (including both `RANGE` endpoints), not `rule_name`, not the priority field and not an internal `__` column. State them per call when that inference is not what you mean. On the expressions-only path there is no metadata to infer from, so `any` requires explicit `output_fields` (the constructor also accepts `output_fields` for that case).

## Explanations

Two views explain a decision:

- `RuleResult.explain(rule_name)` returns `{dimension: ternary}` for a retained rule, from the observability columns.
- `engine.explain(context)` scores *all* rules, including the ones that did not survive ([Chapter 4](../04-expression-rules-engine/index.md)).

## Next steps

[Chapter 6](../06-batch-evaluation/index.md) applies the same policies to a table of contexts.

## Source references

- `src/mountainash_rules/core/hit_policy.py` — `SelectionInfo`, ordering keys, assertions, cardinality, `HitPolicyViolationError`.
- `src/mountainash_rules/core/result.py` — `RuleResult.select`.
- `src/mountainash_rules/core/constants.py` — `HitPolicy`.
