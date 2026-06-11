---
title: "Chapter 6: Expression Engine Results"
description: "The RuleResult class and its accessors for survivors, best match, explanations, and post-evaluation filtering."
generated_by: claude skill chapter-content-generator
date: 2026-06-03
version: 0.08
---

# Chapter 6: Expression Engine Results

## Summary

This chapter covers the RuleResult class that wraps the output of the ExpressionRulesEngine. You will learn how to access survivors, retrieve the best match, inspect active dimensions, generate per-rule explanations, and apply post-evaluation filters including at_least, top_n, and min_specificity. The chapter also covers observability columns for debugging.

---

## Why a Result Wrapper?

The evaluation pipeline (Chapter 5) produces a materialized DataFrame containing survived rules with their ternary columns, specificity scores, and ranks. While this DataFrame could be used directly, the `RuleResult` class provides a clean, backend-agnostic interface for the most common access patterns — retrieving the best match, counting survivors, and explaining individual rule outcomes.

The wrapper also decouples the engine's internal column naming conventions (prefixed with `__`) from the caller's code. Rather than remembering that specificity lives in `__specificity` and ternary values in `__t_region`, callers use named methods that hide these implementation details.

The RuleResult is also designed as a base class. The accumulator engine (Chapter 9) extends it with `AccumulatorResult`, adding aggregate and provenance accessors. This inheritance ensures that any code written against RuleResult also works transparently with AccumulatorResult — the accumulator workflow is a strict superset of the expression workflow.

<!-- concept:49 -->
## RuleResult Class

The `RuleResult` class is constructed by the engine at the end of evaluation. It receives two inputs:

- **`dataframe`**: the materialized result DataFrame (all survivors, ranked)
- **`active_dimensions`**: the list of dimension names that were evaluated

```python
class RuleResult:
    def __init__(self, dataframe, active_dimensions: list[str]) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions
```

All accessors on RuleResult reach the underlying DataFrame through `mountainash.relations.relation()`, maintaining backend agnosticism. The only exception is the `survivors` property, which returns the raw DataFrame directly for callers who want backend-specific operations.

<!-- concept:50 -->
<!-- concept:51 -->
<!-- concept:52 -->
## Survivors Accessor

The `survivors` property returns the complete result DataFrame — all rules that passed the survival filter, sorted by specificity descending, with rank assignments.

```python
result = engine.evaluate(context)
survivors_df = result.survivors
```

The returned DataFrame contains:

- All original rule columns (rule_name, payload fields, dimension columns)
- `__specificity`: integer count of hard-match dimensions
- `__rank`: 1-based rank by specificity
- `__t_{dim_name}` columns (if observability was enabled): per-dimension ternary values

The DataFrame is in the same backend as the input rules. If you passed a Polars DataFrame to the engine constructor, `survivors` returns a Polars DataFrame. This allows callers to chain backend-specific operations (filtering, aggregation, export) on the result.

## Best Match Accessor

The `best_match` property returns just the single most specific surviving rule — the rule at rank 1. It returns a one-row DataFrame (not a scalar), preserving all columns.

```python
best = result.best_match  # Single-row DataFrame
```

Internally, this takes the first row via `relation(self._df).head(1).collect()`. If no rules survived (empty result), `best_match` returns an empty DataFrame rather than raising an error.

This accessor is the most common entry point for callers who need a single definitive answer: "given this context, which rule applies?"

## Count Accessor

The `count` property returns the number of surviving rules as a plain integer:

```python
n = result.count  # int
```

This is useful for validation and branching logic:

- `count == 0`: no rules matched the context (may indicate a gap in the rule set)
- `count == 1`: exactly one rule matched (unambiguous result)
- `count > 1`: multiple rules matched (caller may need to inspect specificity or apply additional filters)

#### Diagram: RuleResult API Overview

<iframe src="../../sims/rule-result-api/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>RuleResult API Overview</summary>
Type: diagram
**sim-id:** rule-result-api<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive class diagram showing all RuleResult properties and methods with their return types and relationships.

**Components:**
- Central node: RuleResult class
- Property nodes: survivors, best_match, count, active_dimensions (with return type labels)
- Method nodes: explain(rule_name), at_least(n) (with parameter and return type labels)
- Edge labels showing data flow from internal _df to each accessor

**Interactions:** Click any accessor node to see a code example and sample output. Hover for a tooltip with the docstring. Click "show internals" to reveal the relation() calls inside each accessor.

**Learning objective:** Navigate the RuleResult API to select the appropriate accessor for a given use case (Bloom: Apply)
</details>

<!-- concept:53 -->
## Active Dimensions

The `active_dimensions` property returns the list of dimension names that were evaluated. This may be a subset of all configured dimensions if the caller passed a `dimensions` parameter to `evaluate()`.

```python
dims = result.active_dimensions  # ["region", "tier"]
```

This property is primarily used by the `explain()` method to know which ternary columns to inspect, but it is also useful for logging and debugging — confirming which dimensions contributed to the evaluation.

<!-- concept:54 -->
## Explain Method

The `explain()` method provides per-dimension ternary values for a specific rule, identified by its `rule_name` column value. This is the primary debugging tool for understanding *why* a rule received its specificity score.

```python
explanation = result.explain("au_premium")
# {"region": 1, "tier": 1}  — both dimensions matched

explanation = result.explain("base")
# {"region": 0, "tier": 0}  — both dimensions were wildcards
```

The method:

1. Filters the result DataFrame for rows where `rule_name` equals the argument
2. Extracts the `__t_{dim_name}` column values for each active dimension
3. Returns a dictionary mapping dimension names to ternary integers (1, 0, or -1)
4. Raises `KeyError` if the rule name is not found in the survivors

The explain output provides immediate diagnostic value:

| Ternary Value | Meaning | Implication |
|--------------|---------|-------------|
| 1 | Hard match | Dimension explicitly matched context — contributed to specificity |
| 0 | Unknown/wildcard | Dimension did not constrain — neither helped nor hurt |
| -1 | Non-match | Should not appear (rule would not have survived) |

A -1 value in an explain result indicates an internal inconsistency — it should never appear because survival filtering removes all rules with any -1 dimension. If you see it, the evaluation pipeline has a bug.

<!-- concept:55 -->
<!-- concept:57 -->
## At Least Filter

The `at_least(n)` method returns a filtered DataFrame containing only survivors with specificity >= n. This is a post-evaluation convenience that applies a floor to the specificity score.

```python
# Only rules with at least 2 hard matches
high_specificity = result.at_least(2)
```

The method constructs a relation, filters on `__specificity >= n`, and collects. The returned DataFrame is in the same backend as the original result.

Use cases for `at_least`:

- Excluding catch-all rules (specificity 0) that survived only because all their dimensions are wildcards
- Requiring a minimum level of context matching before accepting a rule
- Implementing tiered fallback: try `at_least(3)`, fall back to `at_least(2)`, then `at_least(1)`

<!-- concept:56 -->
## Top N Filtering

The `top_n` parameter on `evaluate()` limits the result to the N most specific survivors. Unlike `at_least`, this is applied during evaluation (after ranking, before result construction) rather than as a post-evaluation filter.

```python
# Get only the top 3 matches
result = engine.evaluate(context, top_n=3)
assert result.count <= 3
```

The filtering happens via `rel.head(top_n)` after sorting by specificity descending. This means:

- Rank 1-3 are retained; rank 4+ are discarded
- The `__rank` values in the result are contiguous (1, 2, 3)
- If fewer than N rules survive, all survivors are returned

`top_n` is useful when the caller needs a fixed-size result regardless of how many rules match — for example, displaying the "top 5 applicable offers" to a user.

## Min Specificity Filter

The `min_specificity` parameter on `evaluate()` excludes survivors whose specificity falls below a threshold. Like `top_n`, it is applied during evaluation after rank assignment.

```python
# Only rules that match on at least 2 dimensions
result = engine.evaluate(context, min_specificity=2)
```

The filtering happens via `rel.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))` after ranking but before `top_n`. This ordering means:

1. All rules are ranked by specificity
2. Rules below `min_specificity` are removed
3. Then `top_n` is applied to the remaining rules

This allows combining both filters: "give me the top 5 rules that match on at least 3 dimensions."

The key difference from `at_least()` is timing:

| Filter | Applied During | Affects __rank | Method |
|--------|---------------|----------------|--------|
| min_specificity | Evaluation | Yes (gaps in ranks) | evaluate() parameter |
| top_n | Evaluation | Yes (truncated) | evaluate() parameter |
| at_least | Post-evaluation | No (original ranks preserved) | RuleResult method |

<!-- concept:58 -->
## Observability Columns

When `include_observability=True` (the default), the result DataFrame retains the per-dimension ternary columns (`__t_region`, `__t_tier`, etc.). These columns are the foundation for debugging and monitoring rule behavior in production.

The observability columns enable several workflows:

- **Explain**: the `explain()` method reads these columns directly
- **Monitoring**: aggregate ternary distributions across evaluations to detect dimension drift
- **Debugging**: inspect which dimensions produced UNKNOWN vs TRUE for a rule set, identifying overly broad wildcard rules
- **Analytics**: compute per-dimension match rates across a batch of contexts

When observability is disabled (`include_observability=False`), these columns are dropped before the result is returned, reducing memory usage and network transfer size for production deployments where debugging is not needed.

```python
# Production: minimal result
result = engine.evaluate(context, include_observability=False)
# Result has: original columns + __specificity + __rank (no __t_* columns)

# Development: full observability
result = engine.evaluate(context, include_observability=True)
# Result has: original columns + __t_* + __specificity + __rank
```

#### Diagram: Result DataFrame Column Layout

<iframe src="../../sims/result-column-layout/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Result DataFrame Column Layout</summary>
Type: infographic
**sim-id:** result-column-layout<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Visual representation of the result DataFrame's column groups, showing which columns are always present, which are optional (observability), and which were dropped during evaluation.

**Components:**
- Column header bar divided into colored sections: Original Columns (white), Ternary Columns (green, optional), Computed Columns (purple, always present)
- Toggle switch for observability on/off showing columns appearing/disappearing
- Sample data rows below the headers

**Interactions:** Toggle observability to show/hide __t_* columns. Hover over column headers for tooltips explaining purpose and value range. Click a section to see the list of columns in that group for a sample 3-dimension evaluation.

**Learning objective:** Identify which columns appear in a RuleResult DataFrame under different observability settings (Bloom: Remember)
</details>

## Understanding Specificity in Results

The `__specificity` column in the result DataFrame is the foundation for ranking and filtering. To fully leverage the result API, it helps to understand how specificity values distribute across different rule set designs.

Consider a 4-dimension rule set (region, tier, category, channel) evaluated against a context that provides values for all four dimensions:

| Rule | region | tier | category | channel | __specificity | Interpretation |
|------|--------|------|----------|---------|---------------|---------------|
| R1 | AU | premium | electronics | online | 4 | Fully specific — matches all 4 dimensions |
| R2 | AU | premium | \<NA\> | \<NA\> | 2 | Matches region and tier, wildcards the rest |
| R3 | AU | \<NA\> | \<NA\> | \<NA\> | 1 | Matches only region |
| R4 | \<NA\> | \<NA\> | \<NA\> | \<NA\> | 0 | Global fallback — all wildcards |

All four rules survive (no FALSE values), but their specificity scores create a clear hierarchy. Using `result.at_least(2)` would return only R1 and R2. Using `result.best_match` returns R1.

A specificity of 0 is valid and common — it represents fallback rules that apply universally but should be overridden by more specific rules.

#### Diagram: Specificity Distribution Dashboard

<iframe src="../../sims/specificity-distribution/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Specificity Distribution Dashboard</summary>
Type: chart
**sim-id:** specificity-distribution<br/>
**Library:** Chart.js<br/>
**Status:** Specified

**Purpose:** Interactive histogram showing the distribution of specificity scores across survivors for a configurable rule set and context.

**Components:**
- Bar chart with specificity values (0 to max dimensions) on x-axis, rule count on y-axis
- Overlay lines showing at_least and min_specificity thresholds as vertical markers
- Summary statistics: total survivors, mean specificity, best match specificity

**Interactions:** Draggable threshold markers for at_least and min_specificity that dynamically gray out bars below the threshold. Dropdown to select different sample rule sets (small/medium/large). Hover over bars for a list of rule names at that specificity level.

**Learning objective:** Analyze specificity distributions to choose appropriate filtering thresholds (Bloom: Evaluate)
</details>

## Chaining Result Operations

The RuleResult API is designed for sequential use — accessors return DataFrames or values that can be further processed. Several common chaining patterns arise in practice:

**Pattern 1: Best-match payload extraction.** Retrieve the best match and extract a payload column:

```python
best_df = result.best_match
# Extract the discount value from the single-row DataFrame
discount = best_df["discount"][0]
```

**Pattern 2: Tiered fallback.** Try progressively lower specificity thresholds:

```python
for min_spec in [3, 2, 1, 0]:
    filtered = result.at_least(min_spec)
    if len(filtered) > 0:
        return filtered
return None  # No rules matched at all
```

**Pattern 3: Explain all survivors.** Generate a diagnostic report:

```python
report = {}
for rule_name in result.survivors["rule_name"]:
    try:
        report[rule_name] = result.explain(rule_name)
    except KeyError:
        pass  # Should not happen for survivors
```

## Working with Results in Practice

A typical production workflow combines multiple result accessors:

```python
result = engine.evaluate(context)

if result.count == 0:
    # No matching rules — use system default
    return default_configuration

if result.count == 1:
    # Unambiguous match
    return extract_payload(result.best_match)

# Multiple matches — use the most specific
best = result.best_match
# Optionally log the runner-up for monitoring
if result.count > 1:
    all_survivors = result.survivors
    log_ambiguity(context, all_survivors)

return extract_payload(best)
```

For debugging, the explain method provides immediate insight:

```python
result = engine.evaluate(context)
for rule_name in ["premium_au", "standard_au", "fallback"]:
    try:
        dims = result.explain(rule_name)
        print(f"{rule_name}: {dims}")
    except KeyError:
        print(f"{rule_name}: did not survive")
```

## Empty Results and Edge Cases

Several edge cases merit attention when working with RuleResult:

**No survivors.** When no rules match the context (all are eliminated by FALSE dimensions), `result.count` is 0, `result.survivors` returns an empty DataFrame with the correct schema, and `result.best_match` returns an empty single-row-attempt that is also empty. Callers should always check `count` before assuming `best_match` contains data.

**All wildcards.** When a rule has sentinels in every dimension, it always survives with specificity 0. This is a valid and common pattern for fallback rules, but it means `result.count` is never 0 as long as a catch-all rule exists. If you need to distinguish "a specific rule matched" from "only the fallback matched," check specificity rather than count.

**Tied specificity.** When multiple survivors have the same specificity score, they receive consecutive ranks in the order they appeared in the original DataFrame. The engine does not break ties — it preserves insertion order. This deterministic behavior means results are reproducible across runs with the same input.

**Single dimension.** When only one dimension is evaluated (via the `dimensions` parameter), specificity can only be 0 or 1, and survival is equivalent to "the dimension did not return FALSE." This degenerates the ranking to a binary classifier on that dimension.

#### Diagram: Result Edge Case Explorer

<iframe src="../../sims/result-edge-cases/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Result Edge Case Explorer</summary>
Type: microsim
**sim-id:** result-edge-cases<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive explorer showing how RuleResult behaves under various edge cases: empty results, all-wildcard rules, tied specificities, and single-dimension evaluation.

**Controls:**
- Edge case selector (dropdown): "No survivors", "All wildcards", "Tied specificity", "Single dimension"
- Each case pre-loads a sample rule set and context

**Visual elements:**
- Rules table with ternary evaluation results
- Result accessor outputs displayed as labeled cards (count, best_match, survivors)
- Warning badges for potential gotchas (e.g., "count > 0 but only fallbacks survived")
- Color-coded rows: eliminated (red), survived (green), tied (yellow border)

**Behavior:** Selecting an edge case loads the scenario, runs evaluation visually, and shows all accessor outputs. Callout boxes highlight the specific edge case behavior.

**Learning objective:** Handle edge cases in RuleResult correctly by testing accessor behavior under boundary conditions (Bloom: Apply)
</details>

## Key Takeaways

- **RuleResult** wraps the evaluation output DataFrame with a backend-agnostic accessor API, hiding internal column naming conventions from callers.
- The **survivors** property returns the full result DataFrame for backend-specific operations; **best_match** returns just the rank-1 rule.
- **count** gives a quick integer for branching logic (0 = no match, 1 = unambiguous, >1 = ambiguous).
- **active_dimensions** records which dimensions participated in evaluation, supporting partial-evaluation scenarios.
- **explain()** returns per-dimension ternary values for a named rule, providing immediate diagnostic insight into why a rule has its specificity score.
- **at_least(n)** is a post-evaluation filter on specificity; **top_n** and **min_specificity** are evaluation-time filters that affect rank assignment.
- **Observability columns** (`__t_*`) are retained by default for debugging but can be excluded in production for reduced footprint.
- The filter application order is: rank all survivors, apply min_specificity, apply top_n — enabling combined filtering with clear semantics.
