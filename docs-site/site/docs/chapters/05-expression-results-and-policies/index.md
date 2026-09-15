---
title: "Chapter 5: Expression Engine Hit Policies, Results and Explanations"
description: "Choose hit policies, inspect and filter RuleResult values, and use complete engine-level explanations."
---

# Chapter 5: Expression Engine Hit Policies, Results and Explanations

An expression evaluation can have several surviving rules. Matching establishes which rows are applicable; a **hit policy** establishes how an application handles those survivors. The policy may keep them all, assert that the table is unambiguous, choose a winner by a stated order, or require the rows to agree on their outputs.

[Chapter 4](../04-expression-rules-engine/index.md#read-the-result-contract) introduced the default survivor ordering and result interface. This chapter develops the choices governing those results and distinguishes:

- **matching** decides which candidates survive;
- **ordering** decides the meaning of `__rank`;
- **assertions** detect ambiguity or conflicting outputs;
- **cardinality** may retain every candidate or one representative; and
- **diagnosis** may need the complete scored table, including candidates that did not survive.

The code below is a fresh Python session. `generic` is a wildcard fallback, `specific` is an Australian rule, and `nz_only` deliberately does not survive an Australian context. `salience` is a business precedence column; `price` is the output that an application would consume.

```python
import polars as pl
from mountainash.relations import relation
from mountainash_rules import (
    Dimension,
    DimensionCompiler,
    DimensionsMetadata,
    ExpressionRulesEngine,
    HitPolicy,
    HitPolicyViolationError,
    UNKNOWN,
)

policy_rules = pl.DataFrame({
    "rule_name": ["generic", "specific", "nz_only"],
    "region": [UNKNOWN, "AU", "NZ"],
    "salience": [10, 1, 0],
    "price": [5.0, 7.0, 9.0],
})
policy_metadata = DimensionsMetadata(
    dimensions=[Dimension(dimension_name="region")],
    priority_field="salience",
    output_fields=["price"],
)
policy_engine = ExpressionRulesEngine(
    policy_rules,
    dimension_metadata=policy_metadata,
)

def summary(result):
    return relation(result.survivors).to_polars().select(
        "rule_name", "price", "__specificity", "__rank",
    ).rows()
```

<!-- concept:100 -->
## Choose and reapply a hit policy {#choose-and-reapply-a-hit-policy}

`HitPolicy` is a string enum with six values. The selected policy determines survivor ordering before ranks are assigned, then applies any assertion and cardinality rule. The table gives the contract before optional result filters.

| Policy | Ordering | Assertion | Returned cardinality |
|---|---|---|---|
| `COLLECT` | Specificity descending, then original rule order | None | All survivors |
| `UNIQUE` | Same as `COLLECT` | At most one survivor | All survivors if assertion passes |
| `FIRST` | Original rule order | None | At most one survivor |
| `PRIORITY` | Priority descending, specificity descending, then original rule order | Priorities of survivors must be non-null | At most one survivor |
| `ANY` | Same as `COLLECT` | Multiple survivors must agree on outputs | At most one survivor |
| `RULE_ORDER` | Original rule order | None | All survivors |

Enum members and their serialized forms are both accepted. An unknown string is rejected with `ValueError`; it does not silently become `COLLECT`.

```python
print(HitPolicy.COLLECT.value)
print(HitPolicy("rule_order") is HitPolicy.RULE_ORDER)
```

```text
collect
True
```

The two Australian survivors make the difference between policies visible. `specific` has specificity one because it matches `AU`; `generic` has specificity zero because its region is a wildcard. `nz_only` is not a candidate at all for this context.

<!-- concept:97 -->
### Table-level hit-policy fields {#table-level-hit-policy-fields}

`DimensionsMetadata` stores a table-level `hit_policy`, `priority_field`, and `output_fields`. The metadata policy is a default: an explicit `evaluate(..., hit_policy=...)` wins for that one call. A call-level `priority_field` likewise overrides the metadata priority field when `PRIORITY` is selected.

`priority_field` is required when metadata declares `PRIORITY`; configuration fails before an engine exists if it is omitted. `output_fields` declares which payload columns `ANY` must compare. The engine validates declared output names against the actual rules-table schema during construction, so a misspelled output is not quietly ignored.

The following engine chooses `FIRST` by default, then the second call overrides it with `collect`:

```python
default_first = ExpressionRulesEngine(
    policy_rules,
    dimension_metadata=DimensionsMetadata(
        dimensions=[Dimension(dimension_name="region")],
        hit_policy=HitPolicy.FIRST,
    ),
)
print(default_first.evaluate({"region": "AU"}).count)
print(default_first.evaluate({"region": "AU"}, hit_policy="collect").count)
```

```text
1
2
```

For a metadata-backed engine without explicit `output_fields`, `ANY` can infer outputs from the rule schema by excluding condition fields, `rule_name`, the priority field, and internal `__` columns. An expressions-only engine has no such metadata. It must receive explicit `output_fields` at construction for `ANY`, even if a later context would have zero or one survivor. This makes the agreement assertion a declared schema contract rather than an accident of a particular request.

<!-- concept:101 -->
### Collect the default, unfiltered ranking {#collect-the-default-unfiltered-ranking}

`COLLECT` is the default. It preserves every survivor and orders them by decreasing specificity, breaking ties by their original rules-table position. It does not make ambiguity an error and it does not reduce the survivor set.

```python
collected = policy_engine.evaluate({"region": "AU"})
print(summary(collected))
```

```text
[('specific', 7.0, 1, 1), ('generic', 5.0, 0, 2)]
```

`COLLECT` is the useful starting point when a caller needs to inspect alternatives or plans to choose a policy after the initial evaluation. It preserves the complete survivor basis, provided no result limit was also requested.

<!-- concept:102 -->
### Unique: assert at most one survivor {#unique-assert-at-most-one-survivor}

`UNIQUE` states that zero or one survivor is acceptable and that two or more survivors are an ambiguity in the rule library. It retains all survivors when valid, but it checks the assertion before any cardinality operation could hide a conflict.

```python
try:
    policy_engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
except HitPolicyViolationError as exc:
    offending = relation(exc.offending).to_polars().select("rule_name", "__rank").rows()
    print(exc.policy.value, offending)
```

```text
unique [('specific', 1), ('generic', 2)]
```

Zero survivors pass the assertion: "no rule applies" is not the same as "several rules apply." Catch `HitPolicyViolationError` when the application needs to distinguish a failed `UNIQUE` or `ANY` assertion from other configuration `ValueError` instances.

<!-- concept:103 -->
### First and priority: pick a single winner {#first-and-priority-pick-a-single-winner}

`FIRST` and `PRIORITY` both return at most one row, but they define precedence differently. `FIRST` uses original rule order and deliberately ignores specificity. `PRIORITY` uses the declared priority field first, then specificity, then original rule order.

```python
first = policy_engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.FIRST)
priority = policy_engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.PRIORITY)
print("first", summary(first))
print("priority", summary(priority))
```

```text
first [('generic', 5.0, 0, 1)]
priority [('generic', 5.0, 0, 1)]
```

Both choose `generic`, but for different stated reasons: it is the first matching row and it has `salience=10`, greater than `specific`'s one. A priority tie would let specificity decide; a remaining tie would use original rule order. `PRIORITY` rejects a missing priority field in configuration and rejects a null priority among survivors, because neither establishes a complete ordering.

<!-- concept:104 -->
### Any: survivors that must agree {#any-survivors-that-must-agree}

`ANY` is an assertion plus cardinality policy. It first compares all configured output fields across the complete survivor set. If multiple survivors disagree, it raises; if they agree, it returns the rank-one representative under the ordinary specificity ordering.

The `price` values in the running table conflict, so the agreement assertion fails:

```python
try:
    policy_engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY)
except HitPolicyViolationError as exc:
    offending = relation(exc.offending).to_polars().select("rule_name", "price").rows()
    print(exc.policy.value, offending)
```

```text
any [('specific', 7.0), ('generic', 5.0)]
```

When the same two survivors agree on the declared output, `ANY` keeps the specificity-ranked representative. The other survivor describes the same configured answer for this context.

```python
agreeing_rules = policy_rules.with_columns(pl.lit(5.0).alias("price"))
agreeing_engine = ExpressionRulesEngine(
    agreeing_rules,
    dimension_metadata=policy_metadata,
)
print(summary(agreeing_engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY)))
```

```text
[('specific', 5.0, 1, 1)]
```

<!-- concept:105 -->
### Rule order: preserve declaration order {#rule_order-preserve-declaration-order}

`RULE_ORDER` retains every survivor but orders them by their original rules-table position. It shares `FIRST`'s ordering but not its one-row cardinality. Specificity is still computed and remains available for inspection or filtering; it simply does not determine the presentation order.

```python
ordered = policy_engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.RULE_ORDER)
print(summary(ordered))
```

```text
[('generic', 5.0, 0, 1), ('specific', 7.0, 1, 2)]
```

Use this policy when the authored row sequence is the intended precedence and downstream code needs every applicable row. Changing from `COLLECT` to `RULE_ORDER` does not change survival; it changes only ordering, which matters whenever a consumer takes the first row.

<!-- concept:107 -->
### HitPolicyViolationError {#hitpolicyviolationerror}

`HitPolicyViolationError` subclasses `ValueError` and carries two additional attributes:

| Attribute | Meaning |
|---|---|
| `policy` | The failed `HitPolicy` member, `UNIQUE` or `ANY`. |
| `offending` | For scalar evaluation or `RuleResult.select()`, the materialized, complete ranked survivor frame. For batch evaluation, a grouped violation summary containing `__context_id` and `__n`. |

The `UNIQUE` and `ANY` assertions run before cardinality and before `top_n` or `min_specificity` filters. The scalar exception therefore preserves the candidate set responsible for the problem; [Chapter 6](../06-batch-evaluation/index.md#work-in-smaller-chunks) explains the batch summary and chunked failures. A missing priority field, invalid policy name, or invalid output schema is instead a normal `ValueError` because it is configuration rather than a policy assertion over survivors.

## Explain a decision and refine a result

<!-- concept:54 -->
### Explain a retained rule {#explain-a-decision-and-refine-the-result}

`RuleResult.explain(rule_name)` reads observability columns from the **retained** result frame and returns one ternary value per active dimension. It can explain `specific`, which remains in `collected`, but cannot explain `nz_only`, because evaluation removed that non-survivor.

```python
print(collected.explain("specific"))
try:
    collected.explain("nz_only")
except KeyError as exc:
    print(str(exc))
```

```text
{'region': 1}
"Rule 'nz_only' not found in survivors"
```

This method requires observability columns. A result created with `include_observability=False` still has its payload, rank, and count, but lacks the `__t_` columns the method reads. It answers "how did this kept rule score?" rather than "why did a rule disappear?"

<!-- concept:55 -->
### At least: filtering returned survivors {#at_least-filtering-the-returned-survivors}

`result.at_least(n)` returns a native DataFrame containing only retained rows with `__specificity >= n`. It is a post-evaluation view, not a new `RuleResult`, so it cannot recover discarded candidates or provide a new selection basis.

```python
print(relation(collected.at_least(1)).to_polars().select(
    "rule_name", "__rank",
).rows())
```

```text
[('specific', 1)]
```

This is useful for a consumer that wishes to suppress wildcard-only fallbacks without changing the engine's original result. It can only narrow the rows `collected` already retained.

<!-- concept:56 -->
### Top N: limiting returned survivors {#top_n-limiting-how-many-survivors-return}

`top_n` is an `evaluate()` argument. It keeps at most N rows after the policy ordering and after a possible `min_specificity` filter. `top_n=0` is valid and returns no rows; a non-integer, Boolean, or negative value is rejected.

Because `top_n` loses candidates, its result is conservatively marked as possibly truncated even when N happens to exceed the actual survivor count. That provenance matters for later `select()` calls.

<!-- concept:57 -->
### Minimum specificity: excluding weak matches {#min_specificity-excluding-weak-matches}

`min_specificity` is also an `evaluate()` argument. It removes already-ranked survivors below a hard-match threshold. Rank is assigned **before** this filter, and the remaining frame is sorted by that retained rank. This preserves the fact that a row's rank is its position in the complete policy ordering, not a new count after filtering.

With `FIRST`, `generic` initially has rank one. The specificity floor removes it, leaving `specific` at rank two. `best_match` deliberately finds the lowest retained rank; it does not assume that rank one survived or that a caller's frame order is trustworthy.

```python
first_filtered = policy_engine.evaluate(
    {"region": "AU"},
    hit_policy="first",
    min_specificity=1,
)
print(summary(first_filtered))
print(relation(first_filtered.best_match).to_polars().select(
    "rule_name", "__rank",
).rows())
```

```text
[('specific', 7.0, 1, 2)]
[('specific', 2)]
```

A `min_specificity` or `top_n` limit makes the survivor set unsuitable for later policy re-selection. Re-evaluate with no limiting filters when the complete candidate set is required.

<!-- concept:109 -->
### The ExplainResult class {#the-explainresult-class}

`ExplainResult` is a separate result type for a complete scoring record. Its `frame` contains **every rule** with original rule columns, `__rule_index`, per-dimension `__t_` columns, `__survived`, and `__specificity`. It has no `__rank`, no hit-policy selection, no `best_match`, and no `select()` method.

| `ExplainResult` accessor | Meaning |
|---|---|
| `frame` | All scored rules, both survivors and non-survivors. |
| `survivors` | The `frame` rows with `__survived=True`. |
| `non_survivors` | The `frame` rows with `__survived=False`. |
| `count` | Number of rows in the complete frame. |
| `active_dimensions` | Dimensions used for scoring. |

It is intentionally not a `RuleResult` subclass. A selection API would imply that this unfiltered, unranked diagnostic table has a policy-selected winner, which it does not.

<!-- concept:110 -->
### Engine-level explain: scoring every rule {#engine-level-explain-scoring-every-rule}

`engine.explain(context)` uses the same context binding, ternary expressions, survival rule, and specificity calculation as `evaluate()`. It stops before survival filtering, ranking, policies, and cardinality. It is the right interface for the question, "why did this table row not apply?"

```python
explained = policy_engine.explain({"region": "AU"})
print(relation(explained.frame).to_polars().select(
    "rule_name", "__t_region", "__survived", "__specificity", "__rule_index",
).rows())
print(
    explained.count,
    relation(explained.survivors).to_polars().select("rule_name").rows(),
    relation(explained.non_survivors).to_polars().select("rule_name").rows(),
)
```

```text
[('generic', 0, True, 0, 0), ('specific', 1, True, 1, 1), ('nz_only', -1, False, 0, 2)]
3 [('generic',), ('specific',)] [('nz_only',)]
```

`nz_only` visibly fails because its region ternary is `-1`. `RuleResult.explain()` could not report that row because it never reaches a retained result. `ExplainResult` preserves it specifically for diagnosis and keeps no ranking that might misrepresent a policy choice.

<!-- concept:111 -->
### Reapplying a policy with RuleResult.select {#reapplying-a-policy-with-ruleresultselect}

`RuleResult.select(policy, priority_field=None)` applies a new policy to an already evaluated survivor frame without re-running matching. It validates the policy and priority, re-sorts by the new ordering, recomputes one-based ranks, checks assertions, and then applies cardinality. A complete `COLLECT`, `UNIQUE`, or `RULE_ORDER` result with no `top_n` or `min_specificity` limit can be reselected.

```python
priority_selected = collected.select("priority", priority_field="salience")
print(summary(priority_selected))
```

```text
[('generic', 5.0, 0, 1)]
```

The gate is candidate completeness, not the name of the earlier policy. `select()` refuses a result marked as possibly truncated: a limit may have removed rows, and `FIRST`, `PRIORITY`, and `ANY` are conservatively marked truncated after they apply one-row cardinality, even for a singleton where no row happened to be lost. The conservative marker prevents a second selection from silently treating an incomplete set as complete.

```python
limited = policy_engine.evaluate({"region": "AU"}, top_n=1)
try:
    limited.select("unique")
except ValueError as exc:
    print(str(exc))

try:
    priority_selected.select("rule_order")
except ValueError as exc:
    print(str(exc))
```

```text
select() on a possibly truncated result (filters or cardinality were applied); re-evaluate the complete candidate set instead
select() on a possibly truncated result (filters or cardinality were applied); re-evaluate the complete candidate set instead
```

Re-evaluation is necessary because `select()` cannot reconstruct rows it no longer holds. This is also why a DataFrame manually wrapped as a `RuleResult` is not a substitute for engine evaluation: it lacks the selection provenance needed to establish that its candidates are complete.

## Next steps

A hit policy is an explicit decision contract layered on top of matching. Use `COLLECT` to preserve alternatives, `UNIQUE` to expose ambiguity, `FIRST` or `PRIORITY` for declared precedence, `ANY` for agreeing outputs, and `RULE_ORDER` to keep authored sequence. Keep a complete result when a later policy may be needed, and use engine-level explanation when a discarded row must be diagnosed.

[Chapter 6](../06-batch-evaluation/index.md#evaluate-a-table-of-requests) applies the same matching, ranking, policy, and candidate-completeness principles separately for every context in a table. [Chapter 9](../09-expression-engine-internals/index.md#dimensioncompiler) traces the compiled expressions and selection machinery behind these public contracts.

## Source references

- [`HitPolicy` and selection helpers](https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/core/hit_policy.py) — policy ordering, assertions, output-field resolution, cardinality, and truncation provenance.
- [`RuleResult` and `ExplainResult`](https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/core/result.py) — result accessors, safe re-selection, and complete diagnostic frames.
- [`ExpressionRulesEngine`](https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/engines/filter/engine.py) — metadata precedence, filters, ranking, and engine-level explanation.
