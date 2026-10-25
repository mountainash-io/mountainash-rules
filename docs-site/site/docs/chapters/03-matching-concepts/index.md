---
title: "Chapter 3: Matching Concepts: Strategies, Unknowns and Wildcards"
description: "How a dimension scores a rule: ternary outcomes, wildcards, null, missing values, PRESENCE and the match strategies."
---

# Chapter 3: Matching Concepts: Strategies, Unknowns and Wildcards

Every dimension compares one context field with one rule column and produces a **ternary** outcome. The rest of the package is built on three facts about those outcomes.

## Ternary logic: match, unknown and non-match

| Outcome | Meaning |
|---|---|
| **1** | The context definitely satisfies the rule's condition. |
| **0** | Nothing is known. Either the rule places no constraint (**don't-care**), or the context did not supply the value (**unknown**). |
| **−1** | The context definitely violates the condition. |

A rule **survives** when none of its dimensions scores −1. Its **specificity** is the number of dimensions that scored 1. A rule with a −1 is out, however many other dimensions matched; an unknown never rescues a −1.

```python
import polars as pl
from mountainash_rules import (
    ContextField, DataType, Dimension, DimensionsMetadata, ExpressionRulesEngine,
)

rules = pl.DataFrame({"rule_name": ["eu", "any"], "region": ["EU", "*"]})
metadata = DimensionsMetadata(
    fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
    dimensions=(Dimension(dimension_name="region", context_field="region"),),
)
engine = ExpressionRulesEngine(rules, metadata)

for context in ({"region": "EU"}, {"region": "US"}, {"region": None}, {}):
    frame = engine.explain(context).frame.sort("rule_name")
    print(context, frame.select("rule_name", "__t_region", "__survived", "__specificity").rows())
```

```text
{'region': 'EU'} [('any', 0, True, 0), ('eu', 1, True, 1)]
{'region': 'US'} [('any', 0, True, 0), ('eu', -1, False, 0)]
{'region': None} [('any', 0, True, 0), ('eu', 0, True, 0)]
{} [('any', 0, True, 0), ('eu', 0, True, 0)]
```

`engine.explain(context)` scores every rule without filtering, which is why `eu` appears with a −1 for a US customer.

## Wildcards are declared, never inferred

A rule cell that places no constraint holds the **wildcard marker** that its `ContextField` declares. In the example above `"*"` is the wildcard of `region`, so the `any` rule scores 0 whatever the context says. It survives, but earns no specificity, so it ranks below a rule that matched.

The marker is a typed value (`"*"`, or a number or date), or null when the author says so:

```python
ContextField(name="region", data_type=DataType.STR, wildcard="*")
ContextField(name="spend", data_type=DataType.INT, wildcard_declared=True)   # null is the wildcard
```

A field has exactly one marker, and every dimension on the field uses it. **Null is never an implicit wildcard**: a rule-side null is a don't-care only on a field that declares null as its wildcard. On any other field it is invalid data, which `prevalidate` reports for a DecisionTable.

For a `RANGE`, a marker endpoint is unbounded on that side. For a set strategy, a list that holds the marker is a don't-care for that rule.

## Missing and null context values

A context can fail to supply a value in three ways:

| Context | On a value dimension |
|---|---|
| the key is absent, the pydantic field is unset, or the table has no such column | unknown, **0** |
| the value is `None` (a null cell) | unknown, **0** |
| a `TRUTHY` field maps `None` to an outcome | the mapped outcome |

So a missing value never eliminates a rule and never earns specificity. A rule that requires `region = EU` stays *possible* when the region is missing; the caller decides what a possible, unproven rule means. The engine does not convert missing data into a mismatch, and it never converts it into a match.

## PRESENCE: asking whether a field is null

Sometimes the fact you need is that a field is empty. A `PRESENCE` dimension asks exactly that. Rule cells hold `IS_NULL`, `IS_NOT_NULL` or the wildcard.

| Context | Presence value |
|---|---|
| explicit null (`None`) | **known**: scores 1 against `IS_NULL`, −1 against `IS_NOT_NULL` |
| a supplied value | scores −1 against `IS_NULL`, 1 against `IS_NOT_NULL` |
| not supplied | unknown, 0 against both |

A value dimension and a `PRESENCE` dimension on one field ask different questions: presence asks *is the field null?*; a value dimension asks about the value, with null unknown. One explicit null can score 1 on presence and 0 on the value without contradiction. Only a rule that requires `IS_NULL` and a concrete value on the same field contradicts itself; `prevalidate` warns that it is unsatisfiable.

The worked example: `region` has wildcard `"*"` and `vip` is a Boolean whose declared wildcard is null.

| Rule | `region` | `region_presence` | `vip` |
|---|---|---|---|
| R1, EU customers | `EU` | `*` | null (wildcard) |
| R2, no region recorded | `*` | `IS_NULL` | null (wildcard) |
| R3, VIP with a region | `*` | `IS_NOT_NULL` | `True` |

```python
rules = pl.DataFrame({
    "rule_name":       ["R1", "R2", "R3"],
    "region":          ["EU", "*", "*"],
    "region_presence": ["*", "IS_NULL", "IS_NOT_NULL"],
    "vip":             [None, None, True],
})
metadata = DimensionsMetadata(
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="vip", data_type=DataType.BOOL, wildcard_declared=True),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="region_presence", context_field="region",
                  match_strategy="presence"),
        Dimension(dimension_name="vip", context_field="vip"),
    ),
)
engine = ExpressionRulesEngine(rules, metadata)
for context in ({"region": "EU", "vip": True}, {"region": None, "vip": False},
                {}, {"region": "US", "vip": True}):
    print(context, engine.evaluate(context).survivors.select("rule_name", "__specificity").rows())
```

```text
{'region': 'EU', 'vip': True} [('R3', 2), ('R1', 1)]
{'region': None, 'vip': False} [('R2', 1), ('R1', 0)]
{} [('R1', 0), ('R2', 0), ('R3', 0)]
{'region': 'US', 'vip': True} [('R3', 2)]
```

The explicit null in the second context says "no region": R2 matches that fact. R1 stays possible because, on the value dimension, null is unknown. R3 fails on presence and on `vip`. The empty context leaves every dimension unknown, so every rule is possible at specificity 0. The only difference between the second context and the empty one is the presence dimension.

"Not applicable" is therefore always a `PRESENCE` rule, never a meaning of null in a value dimension.

## Match strategies

`MatchStrategy` is a lowercase string enum. Each strategy reads a rule column in a particular shape.

| Strategy | Rule value | 1 when | −1 when |
|---|---|---|---|
| `exact` | one value | the context equals it | it differs |
| `not_equal` | one value | the context differs | it equals |
| `range` | two columns (`range_min_field`, `range_max_field`) | the context lies in the closed interval | outside it |
| `min` | a threshold | `context >= threshold` | below it |
| `max` | a threshold | `context <= threshold` | above it |
| `prefix`, `suffix`, `contains` | a string | the context string starts with, ends with, contains it | it does not |
| `regex` | a pattern per rule | the context string matches the rule's pattern | it does not |
| `context_regex` | none (`regex_pattern` on the dimension) | the context string matches that one pattern | it does not |
| `set_membership` | a list | the context is in the list | it is not |
| `set_exclusion` | a list | the context is not in the list | it is |
| `truthy` | a value mapped through the field's `truthy` | rule and context outcomes are equal and non-zero | they are opposite |
| `presence` | `IS_NULL`, `IS_NOT_NULL`, wildcard | see above | see above |
| `outcome` | an int8 ternary | the rule's and the context's outcomes are equal and non-zero | they are opposite non-zero |

In every case a wildcard rule cell and an unknown context score 0. `context_regex` also scores 0 for a missing context value. `min`, `max` and `range` need a numeric or temporal field; the string strategies need a `STR` field; list elements must not be null.

```python
rules = pl.DataFrame({
    "rule_name": ["band", "atleast", "atmost"],
    "lo": pl.Series([10, None, None], dtype=pl.Int64),
    "hi": pl.Series([20, None, None], dtype=pl.Int64),
    "floor": pl.Series([None, 50, None], dtype=pl.Int64),
    "cap": pl.Series([None, None, 5], dtype=pl.Int64),
})
metadata = DimensionsMetadata(
    fields=(ContextField(name="n", data_type=DataType.INT, wildcard_declared=True),),
    dimensions=(
        Dimension(dimension_name="band", context_field="n", match_strategy="range",
                  range_min_field="lo", range_max_field="hi"),
        Dimension(dimension_name="atleast", context_field="n", match_strategy="min", rule_field="floor"),
        Dimension(dimension_name="atmost", context_field="n", match_strategy="max", rule_field="cap"),
    ),
)
engine = ExpressionRulesEngine(rules, metadata)
for context in ({"n": 20}, {"n": 50}, {"n": 3}):
    frame = engine.explain(context).frame.sort("rule_name")
    print(context, frame.select("rule_name", "__t_band", "__t_atleast", "__t_atmost").rows())
```

```text
{'n': 20} [('atleast', 0, -1, 0), ('atmost', 0, 0, -1), ('band', 1, 0, 0)]
{'n': 50} [('atleast', 0, 1, 0), ('atmost', 0, 0, -1), ('band', -1, 0, 0)]
{'n': 3} [('atleast', 0, -1, 0), ('atmost', 0, 0, 1), ('band', -1, 0, 0)]
```

Each rule uses only the dimensions whose cells it fills; the others hold null, the field's wildcard, and score 0.

## Truthy mappings and outcomes

A `TRUTHY` field maps raw values, including `None`, to outcomes. Both the rule's value and the context's value pass through the same mapping, and the dimension scores 1 when the two outcomes are equal and non-zero.

```python
rules = pl.DataFrame({"rule_name": ["yes", "no"], "required": ["yes", "no"]})
metadata = DimensionsMetadata(
    fields=(ContextField(name="vip", data_type=DataType.STR,
                         truthy={"yes": 1, "no": -1, None: -1}),),
    dimensions=(Dimension(dimension_name="vip", context_field="vip",
                          rule_field="required", match_strategy="truthy"),),
)
engine = ExpressionRulesEngine(rules, metadata)
for context in ({"vip": "yes"}, {"vip": None}, {}):
    frame = engine.explain(context).frame.sort("rule_name")
    print(context, frame.select("rule_name", "__t_vip").rows())
```

```text
{'vip': 'yes'} [('no', -1), ('yes', 1)]
{'vip': None} [('no', 1), ('yes', -1)]
{} [('no', 0), ('yes', 0)]
```

Here the mapping says a null `vip` counts like "no". Without the `None` entry, a null would be unknown (0) for both rules. `OUTCOME` is the lower-level form: the rule column already holds an int8 ternary, and the context supplies an already computed ternary. A DecisionTable uses it internally for the columns that hold PRESENCE, TRUTHY and pattern conditions ([Chapter 10](../10-decision-table-internals/index.md)).

A mapping for zero does not make a missing DECIMAL value match zero. Only available context values participate in non-null mappings; omission and declared wildcards remain unknown. An explicit null uses the `None` mapping regardless of its position in the mapping, or remains unknown when there is no such entry. These rules also apply when matching a DecisionTable.

## Don't-care versus unknown

Consider rule A `region = *` (a don't-care) and rule B `region = EU`.

- Context EU matches A (0) and B (1).
- Context US matches A only: B scores −1.
- No region supplied: A is a don't-care (0) and B is *unknown* (0). Both survive at specificity 0, and the downstream layer decides. The engine does not drop B's constraint because the value was missing.

## Next steps

[Chapter 4](../04-expression-rules-engine/index.md) builds an `ExpressionRulesEngine` and reads its results.

## Source references

- `src/mountainash_rules/core/constants.py` — `MatchStrategy`, `DataType`, `HitPolicy`.
- `src/mountainash_rules/core/compiler.py` — ternary compilation per strategy.
- `src/mountainash_rules/core/context.py` — context binding: null and missing values.
