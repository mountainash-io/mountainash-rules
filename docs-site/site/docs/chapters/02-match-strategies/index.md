---
title: "Chapter 2: Match Strategies"
description: "Complete reference for all 12 MatchStrategy enum values including equality, numeric comparison, string pattern, context validation, and set operation strategies."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 2: Match Strategies

## Summary

This chapter covers the MatchStrategy enum and all its strategy types. Each strategy defines how a dimension value in a rules table is compared against a value from the evaluation context — from exact equality and inequality through range comparisons, string pattern matching, context validation, and set operations.

---

<!-- concept:11 -->
## The MatchStrategy Enum

The `MatchStrategy` enum is defined in `mountainash_rules.core.constants` and exported from the `mountainash_rules` package. It is a `StrEnum` with 12 members. Each member represents a distinct semantic for comparing a context value against a rule cell value, and each compiled strategy produces a ternary result (1, 0, or -1) according to the logic described in Chapter 1.

The enum values are stable lowercase strings, so they can be used directly in serialized dimension metadata:

```python
from mountainash_rules import MatchStrategy

MatchStrategy.EXACT.value          # "exact"
MatchStrategy.EXACT_KEY.value      # "exact_key"
MatchStrategy.CONTEXT_REGEX.value  # "context_regex"
```

The complete set of members is:

```python
from enum import StrEnum

class MatchStrategy(StrEnum):
    EXACT = "exact"
    EXACT_KEY = "exact_key"
    NOT_EQUAL = "not_equal"
    RANGE = "range"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    REGEX = "regex"
    CONTEXT_REGEX = "context_regex"
    SET_MEMBERSHIP = "set_membership"
    SET_EXCLUSION = "set_exclusion"
```

When you create a `Dimension` object (Chapter 3), you assign one of these strategies to its `match_strategy` field. The `DimensionCompiler` (Chapter 4) then dispatches to the corresponding `_compile_<strategy>` method and generates the backend-agnostic expression for that strategy. This chapter focuses on the *semantics* of each strategy — what it means for a context value to match a rule value under each one.

#### Diagram: Strategy Comparison Matrix

<iframe src="../../sims/strategy-comparison-matrix/main.html" width="100%" height="500px" scrolling="no"></iframe>
<details markdown="1">
<summary>Strategy Comparison Matrix</summary>
Type: infographic
**sim-id:** strategy-comparison-matrix<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive matrix showing all 12 strategies with their applicable data types, ternary output rules, and example inputs/outputs.

**Components:**
- 12 rows (one per strategy), 5 columns: Strategy Name, Data Types, Rule Value Example, Context Value Example, Ternary Output
- Color-coded cells: green for TRUE (1), gray for UNKNOWN (0), red for FALSE (-1)

**Interactions:** Click any strategy row to expand it with 3 additional test cases showing TRUE, UNKNOWN, and FALSE outcomes. Hover over data type cells to see a tooltip explaining the constraint. Filter buttons at top: "All", "Equality", "Numeric", "String", "Set".

**Learning objective:** Differentiate the input requirements and output semantics of each match strategy (Bloom: Analyze)
</details>

## Equality Strategies

The equality strategies compare scalar values directly. EXACT and NOT_EQUAL support the ordinary sentinel-aware ternary comparison, while EXACT_KEY is deliberately asymmetric: it permits a wildcard on the rule side but treats context sentinels as ordinary non-matching values for partition routing.

<!-- concept:12 -->
### EXACT Strategy

The EXACT strategy produces TRUE (1) when the context value equals the rule cell value, and FALSE (-1) when they differ. If either the rule cell or the context value is a typed sentinel, the result is UNKNOWN (0).

This is the default strategy assigned to any dimension that does not explicitly specify one. It covers the most common business rule pattern: "this rule applies when the context field has exactly this value."

The following table shows the ternary outcomes for EXACT with a string dimension:

| Rule Cell | Context Value | Result | Explanation |
|-----------|--------------|--------|-------------|
| `"AU"` | `"AU"` | 1 (TRUE) | Values match |
| `"AU"` | `"US"` | -1 (FALSE) | Values differ |
| `"<NA>"` | `"AU"` | 0 (UNKNOWN) | Rule is wildcard |
| `"AU"` | `"<NOT_SET>"` | 0 (UNKNOWN) | Context missing |
| `"<NA>"` | `"<NOT_SET>"` | 0 (UNKNOWN) | Both are sentinels |

The sentinel-aware behavior means that a rule with `<NA>` in an EXACT dimension acts as a catch-all: it never rejects any context value, but it also never claims a hard match. This is exactly the wildcard semantics that ternary logic provides.

<!-- concept:91 -->
### EXACT_KEY Strategy

EXACT_KEY is an asymmetric exact comparison for dimensions that act as partition keys. The rule side may contain the typed UNKNOWN value, which produces UNKNOWN (0) and therefore represents a wildcard partition. A specific rule key compares equal to a specific context key for TRUE (1), but a context-side sentinel is not treated as a wildcard: it produces FALSE (-1) against a specific rule key. This prevents a missing or unknown context key from accidentally selecting a specific partition.

The compiler method `_compile_exact_key` implements this distinction with ordinary `ma.col` expressions rather than the two-sided sentinel-aware `ma.t_col` used by EXACT. For non-boolean data, it checks the rule against `unknown_sentinel_for(dim.data_type)`; for boolean data, the rule-side wildcard is null.

| Rule Cell | Context Value | Result | Explanation |
|-----------|--------------|--------|-------------|
| `"AU"` | `"AU"` | 1 (TRUE) | Specific keys are equal |
| `"AU"` | `"US"` | -1 (FALSE) | Specific keys differ |
| `"<NA>"` | `"AU"` | 0 (UNKNOWN) | Rule-side wildcard |
| `"AU"` | `"<NOT_SET>"` | -1 (FALSE) | Context sentinel is not a wildcard |

`EXACT_KEY` powers accumulator partition routing through `LatticeIndex`; Chapter 11 covers that routing workflow in full.

<!-- concept:13 -->
### NOT_EQUAL Strategy

The NOT_EQUAL strategy inverts EXACT: it produces TRUE (1) when the context value differs from the rule cell value, and FALSE (-1) when they match. Sentinel handling remains the same — if either operand is a sentinel, the result is UNKNOWN (0).

This strategy is useful for exclusion rules: "this rule applies to every region except AU."

| Rule Cell | Context Value | Result | Explanation |
|-----------|--------------|--------|-------------|
| `"AU"` | `"US"` | 1 (TRUE) | Values differ |
| `"AU"` | `"AU"` | -1 (FALSE) | Values match (excluded) |
| `"<NA>"` | `"US"` | 0 (UNKNOWN) | Rule is wildcard |
| `"US"` | `"<NOT_SET>"` | 0 (UNKNOWN) | Context missing |

Note that NOT_EQUAL uses the same compiled expression structure as EXACT but invokes `t_ne` instead of `t_eq`. Both share the sentinel detection from `t_col`, so wildcard handling is automatic.

<!-- concept:93 -->
### Bool Ternary Comparison

Boolean dimensions have no typed in-band wildcard sentinel: neither `True` nor `False` can safely mean "don't care." For boolean EXACT and NOT_EQUAL comparisons, mountainash-rules therefore uses `null` as the wildcard/don't-care state. A null on either the rule side or the context side produces UNKNOWN (0); only two non-null booleans are compared as TRUE (1) or FALSE (-1).

`DimensionCompiler._compile_bool_ternary(dim, op_name)` implements this rule. `_compile_exact` calls it with `"__eq__"` and `_compile_not_equal` calls it with `"__ne__"`. The method checks `rule_col.is_null()` and `ctx_col.is_null()` before applying the native boolean operator.

| Rule Cell | Context Value | Result | Explanation |
|-----------|--------------|--------|-------------|
| `True` | `True` | 1 (TRUE) | Non-null booleans are equal |
| `True` | `False` | -1 (FALSE) | Non-null booleans differ |
| `null` | `True` | 0 (UNKNOWN) | Null rule is the wildcard |
| `False` | `null` | 0 (UNKNOWN) | Null context is don't-care |

This null-based behavior is specific to boolean ternary comparison. Other scalar data types use the typed sentinels from `mountainash_rules.core.constants`.

## Numeric Comparison Strategies

Three strategies handle numeric comparisons: RANGE for interval containment, GREATER_THAN for lower-bound thresholds, and LESS_THAN for upper-bound thresholds. All three require an orderable `data_type` (`int`, `float`, `date`, or `datetime`) — the Pydantic validator on the `Dimension` class rejects non-orderable dimensions that attempt to use these strategies.

<!-- concept:14 -->
### RANGE Strategy

The RANGE strategy checks whether a context value falls within an interval defined by two rule columns — a minimum bound and a maximum bound. Unlike other strategies that use a single rule column, RANGE requires two: `range_min_field` and `range_max_field`.

The interval boundary behavior is configurable per dimension via `range_min_inclusive` and `range_max_inclusive` flags (both default to `True`). With inclusive bounds, the comparison is:

\[
\text{min} \leq \text{context} \leq \text{max}
\]

With exclusive bounds on either side, the corresponding comparison becomes strict (\(<\) or \(>\)).

Consider a pricing rules table where discount tiers are defined by order quantity ranges:

| Rule | qty_min | qty_max | discount |
|------|---------|---------|----------|
| R1 | 1 | 10 | 5% |
| R2 | 11 | 50 | 10% |
| R3 | 51 | 100 | 15% |

With context value `qty = 25`, the RANGE strategy evaluates:

- R1: \( 1 \leq 25 \leq 10 \) is FALSE — context exceeds max
- R2: \( 11 \leq 25 \leq 50 \) is TRUE — context within range
- R3: \( 51 \leq 25 \leq 100 \) is FALSE — context below min

If either bound column contains the typed unknown sentinel, that bound is treated as UNKNOWN. The compiled RANGE expression combines the lower and upper bound checks via `t_and`, so if one bound is a wildcard (producing 0), the result depends entirely on the other bound.

<!-- concept:15 -->
### GREATER_THAN Strategy

The GREATER_THAN strategy produces TRUE when the context value is strictly greater than the rule cell value. It uses the ternary operator `ctx.t_gt(rule)`, which respects typed sentinel values on both sides.

This strategy is appropriate for threshold-based rules: "this rule applies when the customer's spending exceeds $500."

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| 500 | 750 | 1 (TRUE) |
| 500 | 500 | -1 (FALSE) |
| 500 | 200 | -1 (FALSE) |
| -999999999 | 750 | 0 (UNKNOWN) |

Note that equality at the boundary produces FALSE, not TRUE. GREATER_THAN is a strict comparison. If you need "greater than or equal to" semantics, use RANGE with only a lower bound (set `range_max_field` to a sentinel value).

<!-- concept:16 -->
### LESS_THAN Strategy

The LESS_THAN strategy is the mirror of GREATER_THAN: it produces TRUE when the context value is strictly less than the rule cell value. It uses `ctx_col.t_lt(rule_col)`.

This is useful for upper-ceiling rules: "this rule applies when the item weight is under 5kg."

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| 5 | 3 | 1 (TRUE) |
| 5 | 5 | -1 (FALSE) |
| 5 | 8 | -1 (FALSE) |
| -999999999 | 3 | 0 (UNKNOWN) |

Like GREATER_THAN, LESS_THAN is strict — equality at the boundary produces FALSE.

#### Diagram: Numeric Strategy Number Line

<iframe src="../../sims/numeric-strategy-numberline/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Numeric Strategy Number Line</summary>
Type: microsim
**sim-id:** numeric-strategy-numberline<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive number line visualization showing how RANGE, GREATER_THAN, and LESS_THAN strategies partition the numeric domain into TRUE, FALSE, and UNKNOWN regions.

**Controls:**
- Strategy selector (radio buttons): RANGE, GREATER_THAN, LESS_THAN
- Draggable threshold markers on the number line (min/max for RANGE, single threshold for GT/LT)
- Context value slider that shows the ternary result in real time
- Toggle for inclusive/exclusive bounds (RANGE only)

**Visual elements:**
- Horizontal number line from 0 to 100
- Green region = TRUE zone, red region = FALSE zone
- Context value shown as a vertical marker with color indicating current result
- Boundary markers as draggable circles

**Behavior:** As the user drags the context value slider or adjusts boundaries, the ternary result updates instantly with color feedback. Switching strategies rearranges the TRUE/FALSE regions.

**Learning objective:** Predict the ternary outcome for numeric strategies given arbitrary bounds and context values (Bloom: Apply)
</details>

## String Pattern Strategies

Four row-pattern strategies operate on string data: PREFIX, SUFFIX, CONTAINS, and REGEX. These strategies read a pattern from each rule cell and use a sentinel-detection wrapper before performing the string operation. CONTEXT_REGEX is the fifth string strategy, but it reads one literal pattern from the `Dimension` metadata and validates the context globally.

For the four row-pattern strategies, the compiled expression follows this three-branch pattern:

1. Check if the rule cell is a sentinel (`<NA>` or `<NOT_SET>`) — if yes, return 0 (UNKNOWN)
2. Perform the string operation (starts_with, ends_with, contains, or regex match)
3. If the operation returns true, return 1 (TRUE); otherwise return -1 (FALSE)

This `when/then/otherwise` structure ensures that wildcard rules still produce the UNKNOWN state rather than attempting a string operation against a sentinel literal. `CONTEXT_REGEX` has a separate compiler path because its pattern is not a rule-column value.

<!-- concept:17 -->
### PREFIX Strategy

The PREFIX strategy checks whether the context value starts with the string stored in the rule cell. The underlying operation is `ctx_col.str.starts_with(rule_col)`.

Example use case: matching URL paths or product codes by their leading segment.

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| `"ELEC"` | `"ELECTRONICS"` | 1 (TRUE) |
| `"ELEC"` | `"FURNITURE"` | -1 (FALSE) |
| `"<NA>"` | `"ELECTRONICS"` | 0 (UNKNOWN) |

The PREFIX strategy requires `data_type=str` on the dimension. Attempting to use it with a numeric dimension raises a `ValueError` during Pydantic validation.

<!-- concept:18 -->
### SUFFIX Strategy

The SUFFIX strategy checks whether the context value ends with the rule cell string. The operation is `ctx_col.str.ends_with(rule_col)`.

Example use case: matching file extensions or domain suffixes.

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| `".com.au"` | `"example.com.au"` | 1 (TRUE) |
| `".com.au"` | `"example.co.uk"` | -1 (FALSE) |
| `"<NA>"` | `"example.com"` | 0 (UNKNOWN) |

<!-- concept:19 -->
### CONTAINS Strategy

The CONTAINS strategy checks whether the context value contains the rule cell string as a substring anywhere within it. The operation is `ctx_col.str.contains(rule_col)`.

This is the most permissive string strategy — it matches regardless of position.

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| `"premium"` | `"super_premium_gold"` | 1 (TRUE) |
| `"premium"` | `"standard_basic"` | -1 (FALSE) |
| `"<NA>"` | `"premium_gold"` | 0 (UNKNOWN) |

<!-- concept:20 -->
### REGEX Strategy

REGEX is the per-row regex strategy: each rule row supplies its own pattern in the dimension's rule column. The context string is tested against that row's pattern, so different rules can accept different textual shapes. A sentinel pattern (`<NA>` or `<NOT_SET>`) produces UNKNOWN (0), while a non-sentinel pattern produces TRUE (1) on a match and FALSE (-1) otherwise.

`DimensionCompiler._compile_regex_per_row` implements this behavior. Because `mountainash`'s `regex_contains` currently accepts only a literal pattern, the method uses a Polars-native `str.contains` expression with the context and rule columns. This is the compiler's explicitly tagged backend fallback; non-Polars backends do not have a portable column-valued regex operation yet.

```python
from mountainash_rules import DataType, Dimension, MatchStrategy

email_dim = Dimension(
    dimension_name="email",
    rule_field="email_pattern",
    match_strategy=MatchStrategy.REGEX,
    data_type=DataType.STR,
)
```

For REGEX dimensions, `regex_pattern` must not be set: the pattern belongs in `email_pattern` (or the resolved rule field) on each rule row. To validate every context email against one shared literal pattern instead, use CONTEXT_REGEX.

<!-- concept:92 -->
### CONTEXT_REGEX Strategy

CONTEXT_REGEX is a global context validator. Its single literal pattern is stored on the `Dimension`'s `regex_pattern` field, not in the rules table. `DimensionCompiler._compile_context_regex` applies `ctx_col.str.regex_contains(dim.regex_pattern)` and maps a match to TRUE (1) and a non-match to FALSE (-1). Every rule receives the same ternary result for this dimension, so there is no rule-side wildcard and no UNKNOWN branch.

The `Dimension` validator requires a non-empty `regex_pattern` for CONTEXT_REGEX and rejects `regex_pattern` for other strategies, including per-row REGEX:

```python
from mountainash_rules import DataType, Dimension, MatchStrategy

email_dim = Dimension(
    dimension_name="email",
    match_strategy=MatchStrategy.CONTEXT_REGEX,
    data_type=DataType.STR,
    regex_pattern=r"^[^@]+@[^@]+\.[^@]+$",
)
```

Use CONTEXT_REGEX when a context must satisfy a global format or eligibility check before any rule row can survive. Use REGEX when each rule row needs a distinct pattern.

#### Diagram: String Strategy Matching Visualization

<iframe src="../../sims/string-strategy-matcher/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>String Strategy Matching Visualization</summary>
Type: microsim
**sim-id:** string-strategy-matcher<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive text visualization showing how PREFIX, SUFFIX, CONTAINS, per-row REGEX, and global CONTEXT_REGEX evaluate against a context string.

**Controls:**
- Strategy selector (dropdown): PREFIX, SUFFIX, CONTAINS, REGEX, CONTEXT_REGEX
- Rule value text input (editable for PREFIX/SUFFIX/CONTAINS; per-row pattern for REGEX; disabled for CONTEXT_REGEX)
- Context value text input (editable)

**Visual elements:**
- Large context string displayed character by character
- Matching region highlighted in green; non-matching characters in default color
- If no match: entire string outlined in red
- Sentinel detection shown as a gray overlay with "UNKNOWN" label for a wildcard rule pattern

**Behavior:** On each keystroke in either input, the visualization updates to show the match region. REGEX reads the pattern from the rule row; CONTEXT_REGEX uses the dimension-level pattern. Result badge shows 1/0/-1 with color.

**Learning objective:** Demonstrate how row-specific and global string strategies identify matching or validating context text (Bloom: Apply)
</details>

<!-- concept:21 -->
### SET_MEMBERSHIP Strategy

SET_MEMBERSHIP produces TRUE (1) when the context value appears in the rule cell's list, and FALSE (-1) when it does not. The underlying operation is `ctx_col.t_is_in(rule_col)`, which is ternary-aware: if the context value is a sentinel, the result is UNKNOWN (0).

Example use case: a rule that applies to a specific set of countries stored as a list in the rule row.

| Rule Cell (list) | Context Value | Result |
|-----------------|--------------|--------|
| `["AU", "NZ", "SG"]` | `"AU"` | 1 (TRUE) |
| `["AU", "NZ", "SG"]` | `"US"` | -1 (FALSE) |
| `["AU", "NZ", "SG"]` | `"<NOT_SET>"` | 0 (UNKNOWN) |

The list column format depends on the DataFrame backend. In Polars, this is a column of type `List(Utf8)` or `List(Int64)`. The `t_is_in` operator handles the backend-specific membership test transparently.

<!-- concept:22 -->
### SET_EXCLUSION Strategy

SET_EXCLUSION is the complement of SET_MEMBERSHIP: it produces TRUE (1) when the context value does *not* appear in the rule cell's list. The underlying operation is `ctx_col.t_is_not_in(rule_col)`.

Example use case: a rule that applies to all countries *except* those in a sanctions list.

| Rule Cell (list) | Context Value | Result |
|-----------------|--------------|--------|
| `["XX", "YY"]` | `"AU"` | 1 (TRUE) |
| `["XX", "YY"]` | `"XX"` | -1 (FALSE) |
| `["XX", "YY"]` | `"<NOT_SET>"` | 0 (UNKNOWN) |

Both SET strategies share the same sentinel handling: the ternary awareness comes from the context-side `t_col` reference. The rule-side column (the list) does not use sentinels — an empty list is semantically different from a wildcard. If the dimension should act as a wildcard (no constraint), the entire rule cell should contain the list sentinel value rather than an empty list.

## Strategy Selection Guidelines

Choosing the right strategy for a dimension depends on the shape of your data and the business semantics you need to express. The following decision process helps identify the appropriate strategy:

1. **Is the rule column a list?** Use SET_MEMBERSHIP or SET_EXCLUSION (except for boolean dimensions, which have no typed list wildcard).
2. **Is the comparison numeric or temporal with two bounds?** Use RANGE.
3. **Is it a single numeric or temporal threshold?** Use GREATER_THAN or LESS_THAN.
4. **Is the comparison a string pattern match?** Choose PREFIX, SUFFIX, CONTAINS, or per-row REGEX based on where in the string the pattern appears.
5. **Should one literal string pattern validate every context?** Use CONTEXT_REGEX.
6. **Is this a context-key dimension used for partition routing?** Use EXACT_KEY.
7. **Is it simple value equality?** Use EXACT (default).
8. **Is it exclusion of a single value?** Use NOT_EQUAL.

The following summary connects each strategy to its data type constraints and required fields, as enforced by the `Dimension` validator:

| Strategy | Allowed data_type | Additional Required Fields |
|----------|------------------|---------------------------|
| EXACT | str, int, float, bool, date, datetime | None |
| EXACT_KEY | str, int, float, bool, date, datetime | None |
| NOT_EQUAL | str, int, float, bool, date, datetime | None |
| RANGE | int, float, date, datetime | range_min_field, range_max_field |
| GREATER_THAN | int, float, date, datetime | None |
| LESS_THAN | int, float, date, datetime | None |
| PREFIX | str | None |
| SUFFIX | str | None |
| CONTAINS | str | None |
| REGEX | str | Per-row pattern in the rule field; no `regex_pattern` |
| CONTEXT_REGEX | str | Non-empty `regex_pattern` on the Dimension |
| SET_MEMBERSHIP | any non-bool | Rule column must be list-typed |
| SET_EXCLUSION | any non-bool | Rule column must be list-typed |

#### Diagram: Strategy Usage Frequency Chart

<iframe src="../../sims/strategy-usage-chart/main.html" width="100%" height="350px" scrolling="no"></iframe>
<details markdown="1">
<summary>Strategy Usage Frequency Chart</summary>
Type: chart
**sim-id:** strategy-usage-chart<br/>
**Library:** Chart.js<br/>
**Status:** Specified

**Purpose:** Bar chart showing typical relative usage frequency of each strategy in production rule sets, helping learners understand which strategies are most commonly needed.

**Components:**
- Horizontal bar chart with one bar per strategy
- Bars sorted by frequency (EXACT most common, REGEX least common)
- Percentage labels on each bar
- Color-coded by category (equality=blue, numeric=green, string=orange, set=purple)

**Interactions:** Hover over any bar to see a tooltip with a one-sentence description of the most common use case for that strategy. Click a bar to highlight all strategies in the same category.

**Learning objective:** Prioritize learning effort by understanding which strategies appear most frequently in real-world rule sets (Bloom: Evaluate)
</details>

## Combining Strategies in a Single Rule Set

A rules table can use multiple strategies simultaneously — each dimension has its own strategy, and they evaluate independently. The ternary results from all dimensions are combined using the minimum operation during evaluation (Chapter 5). This means a rule can simultaneously constrain a string dimension with EXACT, a numeric dimension with RANGE, and a list dimension with SET_MEMBERSHIP.

For example, a shipping rules table might define:

- `region` dimension: EXACT strategy (string)
- `weight_min` / `weight_max` dimension: RANGE strategy (float)
- `allowed_carriers` dimension: SET_MEMBERSHIP strategy (list)

Each rule row specifies values for all three dimensions. During evaluation, the engine produces three ternary columns and takes their minimum to determine survival. A rule survives only if no dimension returns FALSE (-1).

## Key Takeaways

- The **MatchStrategy enum** defines 12 comparison operations, each producing a ternary result (1/0/-1) that integrates with the engine's wildcard handling.
- **EXACT** is the default and most common strategy — it handles simple value equality with full sentinel awareness.
- **EXACT_KEY** is rule-wildcard-only equality for context-key dimensions; a context sentinel does not match a specific key and the strategy powers `LatticeIndex` partition routing.
- **NOT_EQUAL** inverts EXACT semantics for exclusion patterns while preserving wildcard behavior through sentinels.
- Boolean EXACT and NOT_EQUAL comparisons use `null` on either side as the don't-care state because booleans have no typed wildcard sentinel.
- **RANGE** uniquely requires two rule columns (min and max) and supports configurable inclusive/exclusive bounds.
- **GREATER_THAN and LESS_THAN** are strict threshold comparisons — equality at the boundary returns FALSE, not TRUE.
- **String strategies** (PREFIX, SUFFIX, CONTAINS, and per-row REGEX) use a sentinel-detection wrapper before performing the string operation, ensuring wildcard patterns produce UNKNOWN rather than runtime errors.
- **CONTEXT_REGEX** stores one literal pattern on the Dimension and validates the context uniformly across all rules, producing only TRUE or FALSE.
- **SET_MEMBERSHIP and SET_EXCLUSION** operate on list-typed rule columns, testing whether a single context value is present in or absent from the set.
