---
title: "Chapter 9: Inside the Expression Rules Engine"
description: "Dimension compilation, context binding, ternary scoring, survival, ranking and selection."
---

# Chapter 9: Inside the Expression Rules Engine

This chapter follows one evaluation through the engine. Nothing here is needed to use the engine; it explains why the results look the way they do and where to look when you extend it. Module paths below are private: import public names from the package root.

## The pipeline

```text
construct:  metadata ──► DimensionCompiler ──► one ternary expression template per dimension
evaluate:   rules ──► bind rules ──► bind context ──► __t_<dim> ternaries ──► survival and specificity
                                                                   │
                       policy ordering ──► __rank ──► assertions ──► limits and cardinality
```

A single evaluation is one chain of Mountainash relation operations that collects once at the end. The engine never loops over rules in Python.

## 1. Compile: dimensions become templates

At construction, `DimensionCompiler.compile_dimensions(metadata)` turns each `Dimension` into a ternary expression over *named columns* that do not exist yet: the bound rule columns and the bound context columns.

```python
import polars as pl
from mountainash_rules import (
    ContextField, DataType, Dimension, DimensionCompiler, DimensionsMetadata,
    ExpressionRulesEngine, MatchStrategy,
)

metadata = DimensionsMetadata(
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="spend", data_type=DataType.INT, wildcard_declared=True),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="spend", context_field="spend",
                  match_strategy=MatchStrategy.RANGE,
                  range_min_field="spend_min", range_max_field="spend_max"),
    ),
)
templates = DimensionCompiler().compile_dimensions(metadata)
print(list(templates))
```

```text
['region', 'spend']
```

Every template has the same shape: *if the rule cell is a wildcard, or the context value is not present, the result is 0; otherwise 1 when the comparison holds and −1 when it does not*. The comparison is the strategy: `rule == context` for `EXACT`, `context >= rule` for `MIN`, `context.str.starts_with(rule)` for `PREFIX`, `rule.list.contains(context)` for `SET_MEMBERSHIP`, and so on. `RANGE` builds two ternaries, one per endpoint, and takes the smaller of the two, so either endpoint can reject. `PRESENCE`, `OUTCOME`, `TRUTHY` and `CONTEXT_REGEX` have their own shapes, described below.

Because the templates are compiled once, an evaluation only binds values and runs expressions.

## 2. Bind the rules

Before scoring, each rule column a dimension reads is normalised into a *value* column and a *wildcard-state* column. The state column is `true` where the cell holds the field's declared marker (compared with the original type, so `"*"` is never confused with another type's value) and the value column holds the cell with the marker neutralised. A null cell is a wildcard only on a field whose declared marker is null. `RANGE` binds both endpoints separately, and set strategies bind the list and its marker state.

The engine performs no validation here. A null cell on a field with no declared null wildcard is simply a null operand, and a backend error propagates natively; catching such data is `prevalidate`'s job.

## 3. Bind the context

A context becomes four columns per dimension, produced by `context_binding_expressions`:

| Column | Content |
|---|---|
| `__ctx_value_<dim>` | The value converted to the field's type: `DECIMAL` values are reduced to the declared scale with the declared rounding, `DATETIME` values truncated to the declared unit, `BOOL` values read with the engine's `BooleanCoercion`. |
| `__ctx_present_<dim>` | Whether a usable value was supplied. |
| `__ctx_supplied_<dim>` | Whether the field was supplied at all. |
| `__ctx_origin_<dim>` | Why: `omitted`, `dont_care`, `null`, `native_missing`, `invalid` or `concrete`. |

The *origin* is what lets `PRESENCE` tell an explicit null (`null`) from an omitted field (`omitted`). For a single context the values are literals; for a batch, the columns are computed from the contexts table. Absence is decided here and nowhere else: the ternary templates only read `__ctx_present_<dim>`, never the business value, to decide whether a dimension is unknown.

No admission step runs before scoring. What an unreadable value does depends on its type: a Boolean or decimal value that cannot be read binds as unknown (0), while a failed cast for another type, such as text in an `INT` field, propagates as the backend's own error. Check contexts beforehand with `check_context_schema` and `validate_contexts` ([Chapter 8](../08-applying-decision-tables/index.md)).

## 4. Score

The engine adds one `__t_<dim>` column per active dimension, then:

- `__survived` is true when every `__t` is at least 0;
- `__specificity` is the sum of `(__t == 1)` over the active dimensions.

`engine.explain()` stops here (the internal `_scored_relation`): no filter, no rank, no policy. `evaluate` continues with the survivors. The `dimensions` argument selects the *active* dimensions before any expression is built; an inactive dimension contributes nothing.

### The special shapes

- **`TRUTHY`** maps the rule value and the context value through the field's `truthy` mapping into outcomes, then compares them with the outcome rule below. A rule wildcard maps to 0.
- **`OUTCOME`** compares two outcomes directly: 0 if the rule's or the context's is 0, 1 if they are equal, −1 otherwise. An omitted or null context outcome is unknown.
- **`PRESENCE`** returns 0 for a wildcard rule or an omitted context, then compares the rule's `IS_NULL` / `IS_NOT_NULL` with the context's origin being `null`.
- **`CONTEXT_REGEX`** has no rule column: 0 when the context value is not present, else `str.regex_contains(pattern)`.
- **`REGEX`** uses the rule row's own pattern through Mountainash's `str.regex_contains`; every pattern strategy goes through public Mountainash string expressions, never a backend-specific method.

## 5. Order, rank, assert, limit

Survivors are sorted by the policy's ordering keys ([Chapter 5](../05-expression-results-and-policies/index.md)); `__rule_index` is always the last, ascending, so the order is deterministic. A 1-based `__rank` is assigned. Assertions (`unique`, `any`) run over the full survivor set before any limit. `min_specificity`, `top_n` and the policy's cardinality then reduce the rows, which is why `__rank` can start above 1.

## 6. Batch evaluation

`evaluate_batch` binds the contexts table, then **cross-joins** it with the rules and runs the same scoring on the joined rows. The join states `execute_on` on the rules' side, so a context table that lives elsewhere is copied to the rules' backend and the rules never move.

Ranking per context is built from portable operations rather than window functions: sort by `__context_id` then the policy's keys, number all rows globally, take each context's minimum row number, and subtract it from every row's number. The same trick applies to `top_n_per_context`. Chunking slices the contexts, runs this pipeline per slice, and concatenates the results; each slice ranks independently, which is correct because ranking is per context.

## Source references

- `src/mountainash_rules/core/compiler.py` — `DimensionCompiler` and the rule-binding helpers.
- `src/mountainash_rules/core/context.py` — `context_binding_expressions`, `extract_context_values`.
- `src/mountainash_rules/engines/filter/engine.py` — `_scored_relation`, `_evaluate`, `evaluate_batch`.
- `src/mountainash_rules/core/hit_policy.py` — ordering, assertions, cardinality.
