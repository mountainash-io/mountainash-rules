---
title: "Chapter 2: Match Strategies"
description: "Complete reference for all MatchStrategy enum values including equality, numeric comparison, string pattern, and set operation strategies."
generated_by: claude skill chapter-content-generator
date: 2026-06-03
version: 0.08
---

# Chapter 2: Match Strategies

## Summary

This chapter covers the MatchStrategy enum and all its strategy types. Each strategy defines how a dimension value in a rules table is compared against a value from the evaluation context — from exact equality and inequality through range comparisons, string pattern matching, and set operations.

---

<!-- concept:11 -->
<!-- concept:12 -->
<!-- concept:13 -->
<!-- concept:14 -->
<!-- concept:15 -->
<!-- concept:16 -->
<!-- concept:17 -->
<!-- concept:18 -->
<!-- concept:19 -->
<!-- concept:20 -->
<!-- concept:21 -->
<!-- concept:22 -->
## The MatchStrategy Enum

The `MatchStrategy` enum is defined in `mountainash_rules.constants` and enumerates every comparison operation the rules engine supports. Each member represents a distinct semantic for comparing a context value against a rule cell value, and each produces a ternary result (1, 0, or -1) according to the logic described in Chapter 1.

The enum uses Python's `auto()` for value assignment, meaning the integer values of the members are implementation details — only the names matter when configuring dimensions.

```python
from enum import Enum, auto

class MatchStrategy(Enum):
    EXACT = auto()
    NOT_EQUAL = auto()
    RANGE = auto()
    GREATER_THAN = auto()
    LESS_THAN = auto()
    PREFIX = auto()
    SUFFIX = auto()
    CONTAINS = auto()
    REGEX = auto()
    SET_MEMBERSHIP = auto()
    SET_EXCLUSION = auto()
```

When you create a `Dimension` object (Chapter 3), you assign one of these strategies to its `match_strategy` field. The DimensionCompiler (Chapter 4) then generates the appropriate backend-agnostic expression for that strategy. This chapter focuses on the *semantics* of each strategy — what it means for a context value to match a rule value under each one.

#### Diagram: Strategy Comparison Matrix

<iframe src="../../sims/strategy-comparison-matrix/main.html" width="100%" height="500px" scrolling="no"></iframe>
<details markdown="1">
<summary>Strategy Comparison Matrix</summary>
Type: infographic
**sim-id:** strategy-comparison-matrix<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive matrix showing all 11 strategies with their applicable data types, ternary output rules, and example inputs/outputs.

**Components:**
- 11 rows (one per strategy), 5 columns: Strategy Name, Data Types, Rule Value Example, Context Value Example, Ternary Output
- Color-coded cells: green for TRUE (1), gray for UNKNOWN (0), red for FALSE (-1)

**Interactions:** Click any strategy row to expand it with 3 additional test cases showing TRUE, UNKNOWN, and FALSE outcomes. Hover over data type cells to see a tooltip explaining the constraint. Filter buttons at top: "All", "Equality", "Numeric", "String", "Set".

**Learning objective:** Differentiate the input requirements and output semantics of each match strategy (Bloom: Analyze)
</details>

## Equality Strategies

The two equality strategies — EXACT and NOT_EQUAL — form the most basic comparison operations. They work with any data type (strings or numerics) and compare the context value directly against the rule cell value.

### EXACT Strategy

The EXACT strategy produces TRUE (1) when the context value equals the rule cell value, and FALSE (-1) when they differ. If either the rule cell or the context value is a sentinel, the result is UNKNOWN (0).

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

## Numeric Comparison Strategies

Three strategies handle numeric comparisons: RANGE for interval containment, GREATER_THAN for lower-bound thresholds, and LESS_THAN for upper-bound thresholds. All three require `data_type` to be `int` or `float` — the Pydantic validator on the `Dimension` class rejects string-typed dimensions that attempt to use these strategies.

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

If either bound column contains the numeric sentinel (-999999999), that bound is treated as UNKNOWN. The compiled RANGE expression combines the lower and upper bound checks via `t_and`, so if one bound is a wildcard (producing 0), the result depends entirely on the other bound.

### GREATER_THAN Strategy

The GREATER_THAN strategy produces TRUE when the context value is strictly greater than the rule cell value. It uses the ternary operator `ctx.t_gt(rule)`, which respects sentinel values on both sides.

This strategy is appropriate for threshold-based rules: "this rule applies when the customer's spending exceeds $500."

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| 500 | 750 | 1 (TRUE) |
| 500 | 500 | -1 (FALSE) |
| 500 | 200 | -1 (FALSE) |
| -999999999 | 750 | 0 (UNKNOWN) |

Note that equality at the boundary produces FALSE, not TRUE. GREATER_THAN is a strict comparison. If you need "greater than or equal to" semantics, use RANGE with only a lower bound (set `range_max_field` to a sentinel value).

### LESS_THAN Strategy

The LESS_THAN strategy is the mirror of GREATER_THAN: it produces TRUE when the context value is strictly less than the rule cell value. It uses `ctx.t_lt(rule)`.

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

Four strategies operate on string data: PREFIX, SUFFIX, CONTAINS, and REGEX. These strategies check whether the context string matches a pattern stored in the rule cell. Unlike equality strategies that use the ternary-aware `t_col` system directly, string strategies use a sentinel-detection wrapper that explicitly checks for sentinels before performing the string operation.

The compiled expression for string strategies follows this three-branch pattern:

1. Check if the rule cell is a sentinel (`<NA>` or `<NOT_SET>`) — if yes, return 0 (UNKNOWN)
2. Perform the string operation (starts_with, ends_with, contains, or regex match)
3. If the operation returns true, return 1 (TRUE); otherwise return -1 (FALSE)

This `when/then/otherwise` structure ensures that wildcard rules still produce the UNKNOWN state rather than attempting a string operation against a sentinel literal.

### PREFIX Strategy

The PREFIX strategy checks whether the context value starts with the string stored in the rule cell. The underlying operation is `ctx_col.str.starts_with(rule_col)`.

Example use case: matching URL paths or product codes by their leading segment.

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| `"ELEC"` | `"ELECTRONICS"` | 1 (TRUE) |
| `"ELEC"` | `"FURNITURE"` | -1 (FALSE) |
| `"<NA>"` | `"ELECTRONICS"` | 0 (UNKNOWN) |

The PREFIX strategy requires `data_type=str` on the dimension. Attempting to use it with a numeric dimension raises a `ValueError` during Pydantic validation.

### SUFFIX Strategy

The SUFFIX strategy checks whether the context value ends with the rule cell string. The operation is `ctx_col.str.ends_with(rule_col)`.

Example use case: matching file extensions or domain suffixes.

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| `".com.au"` | `"example.com.au"` | 1 (TRUE) |
| `".com.au"` | `"example.co.uk"` | -1 (FALSE) |
| `"<NA>"` | `"example.com"` | 0 (UNKNOWN) |

### CONTAINS Strategy

The CONTAINS strategy checks whether the context value contains the rule cell string as a substring anywhere within it. The operation is `ctx_col.str.contains(rule_col)`.

This is the most permissive string strategy — it matches regardless of position.

| Rule Cell | Context Value | Result |
|-----------|--------------|--------|
| `"premium"` | `"super_premium_gold"` | 1 (TRUE) |
| `"premium"` | `"standard_basic"` | -1 (FALSE) |
| `"<NA>"` | `"premium_gold"` | 0 (UNKNOWN) |

### REGEX Strategy

The REGEX strategy is architecturally different from the other three string strategies. Rather than storing a pattern per rule row, the regex pattern is defined once on the `Dimension` metadata via the `regex_pattern` field. All rules in the engine share the same regex test — it acts as a global context validator rather than a per-rule filter.

Because the pattern is fixed at dimension-definition time (not per-rule), there is no UNKNOWN state from the rule side. The result is always TRUE (1) if the context value matches the regex, or FALSE (-1) if it does not.

```python
from mountainash_rules import Dimension, MatchStrategy

# A REGEX dimension validates that context values match a pattern
email_dim = Dimension(
    dimension_name="email",
    match_strategy=MatchStrategy.REGEX,
    data_type=str,
    regex_pattern=r"^[^@]+@[^@]+\.[^@]+$",
)
```

The Pydantic validator enforces two constraints on REGEX dimensions:

- `regex_pattern` must be a non-empty string when strategy is REGEX
- `regex_pattern` must *not* be set for any other strategy

This mutual exclusivity prevents accidental misconfiguration where a pattern is attached to an EXACT dimension (where it would be silently ignored).

#### Diagram: String Strategy Matching Visualization

<iframe src="../../sims/string-strategy-matcher/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>String Strategy Matching Visualization</summary>
Type: microsim
**sim-id:** string-strategy-matcher<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive text visualization showing how PREFIX, SUFFIX, CONTAINS, and REGEX strategies evaluate against a context string.

**Controls:**
- Strategy selector (dropdown): PREFIX, SUFFIX, CONTAINS, REGEX
- Rule value text input (editable for PREFIX/SUFFIX/CONTAINS; shows fixed pattern for REGEX)
- Context value text input (editable)

**Visual elements:**
- Large context string displayed character by character
- Matching region highlighted in green; non-matching characters in default color
- If no match: entire string outlined in red
- Sentinel detection shown as a gray overlay with "UNKNOWN" label when rule value is `<NA>`

**Behavior:** On each keystroke in either input, the visualization updates to show the match region. For REGEX, the regex pattern text field becomes the dimension-level pattern. Result badge shows 1/0/-1 with color.

**Learning objective:** Demonstrate how each string strategy identifies matching substrings within context values (Bloom: Apply)
</details>

## Set Operation Strategies

The final two strategies handle list-valued rule columns: SET_MEMBERSHIP and SET_EXCLUSION. These are designed for dimensions where a rule defines a collection of acceptable (or excluded) values, and the context provides a single value to test against that collection.

### SET_MEMBERSHIP Strategy

SET_MEMBERSHIP produces TRUE (1) when the context value appears in the rule cell's list, and FALSE (-1) when it does not. The underlying operation is `ctx_col.t_is_in(rule_col)`, which is ternary-aware: if the context value is a sentinel, the result is UNKNOWN (0).

Example use case: a rule that applies to a specific set of countries stored as a list in the rule row.

| Rule Cell (list) | Context Value | Result |
|-----------------|--------------|--------|
| `["AU", "NZ", "SG"]` | `"AU"` | 1 (TRUE) |
| `["AU", "NZ", "SG"]` | `"US"` | -1 (FALSE) |
| `["AU", "NZ", "SG"]` | `"<NOT_SET>"` | 0 (UNKNOWN) |

The list column format depends on the DataFrame backend. In Polars, this is a column of type `List(Utf8)` or `List(Int64)`. The `t_is_in` operator handles the backend-specific membership test transparently.

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

1. **Is the rule column a list?** Use SET_MEMBERSHIP or SET_EXCLUSION
2. **Is the comparison numeric with two bounds?** Use RANGE
3. **Is it a single numeric threshold?** Use GREATER_THAN or LESS_THAN
4. **Is the comparison a string pattern match?** Choose PREFIX, SUFFIX, CONTAINS, or REGEX based on where in the string the pattern appears
5. **Is it simple value equality?** Use EXACT (default)
6. **Is it exclusion of a single value?** Use NOT_EQUAL

The following summary connects each strategy to its data type constraints, as enforced by the Pydantic validator:

| Strategy | Allowed data_type | Additional Required Fields |
|----------|------------------|---------------------------|
| EXACT | str, int, float | None |
| NOT_EQUAL | str, int, float | None |
| RANGE | int, float | range_min_field, range_max_field |
| GREATER_THAN | int, float | None |
| LESS_THAN | int, float | None |
| PREFIX | str | None |
| SUFFIX | str | None |
| CONTAINS | str | None |
| REGEX | str | regex_pattern |
| SET_MEMBERSHIP | any | None (rule column must be list-typed) |
| SET_EXCLUSION | any | None (rule column must be list-typed) |

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

- The **MatchStrategy enum** defines 11 comparison operations, each producing a ternary result (1/0/-1) that integrates with the engine's wildcard handling.
- **EXACT** is the default and most common strategy — it handles simple value equality with full sentinel awareness.
- **NOT_EQUAL** inverts EXACT semantics for exclusion patterns while preserving wildcard behavior through sentinels.
- **RANGE** uniquely requires two rule columns (min and max) and supports configurable inclusive/exclusive bounds.
- **GREATER_THAN and LESS_THAN** are strict threshold comparisons — equality at the boundary returns FALSE, not TRUE.
- **String strategies** (PREFIX, SUFFIX, CONTAINS) use a sentinel-detection wrapper before performing the string operation, ensuring wildcards produce UNKNOWN rather than runtime errors.
- **REGEX** is the only strategy where the pattern lives on the dimension metadata rather than in rule rows — it acts as a global context validator applied uniformly to all rules.
- **SET_MEMBERSHIP and SET_EXCLUSION** operate on list-typed rule columns, testing whether a single context value is present in or absent from the set.
