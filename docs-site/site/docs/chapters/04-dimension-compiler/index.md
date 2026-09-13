---
title: "Chapter 4: Dimension Compiler"
description: "How the DimensionCompiler translates dimension metadata into backend-agnostic expression templates for each match strategy."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 4: Dimension Compiler

## Summary

This chapter explains how the DimensionCompiler translates dimension metadata into backend-agnostic expression templates. Each match strategy has a corresponding compile method that produces a mountainash expression capable of evaluating a context value against the rules column. You will also learn about sentinel-aware ternary logic in compiled expressions and context value extraction.

---

## From Metadata to Expressions

Chapters 2 and 3 defined *what* each match strategy means and *how* dimensions are configured. This chapter bridges the gap between configuration and execution by showing how the `DimensionCompiler` transforms dimension metadata into executable expression templates.

An expression template is a lazy computation tree that references two column names: the rule column (from the DataFrame) and the context literal column (injected at evaluation time). The template does not execute immediately — it becomes executable only when the engine applies it to a relation containing both columns. This separation of compilation from evaluation is what enables the engine to compile once and evaluate many times with different contexts.

<!-- concept:31 -->
## DimensionCompiler

The `DimensionCompiler` class is a stateless translator. It takes a `Dimension` object (or an entire `DimensionsMetadata` collection) and produces expression templates — one per dimension. The class has no internal state; it could be a collection of free functions, but is organized as a class for namespacing and future extensibility.

The primary interface consists of two methods:

- **`compile_dimensions(metadata)`**: compiles all dimensions in a `DimensionsMetadata`, returning a dict mapping dimension names to expression templates
- **`compile_dimension(dim)`**: compiles a single `Dimension` to one expression template

Internally, `compile_dimension` dispatches to a private method based on the dimension's `match_strategy` using Python's structural pattern matching (`match/case`):

```python
def compile_dimension(self, dim: Dimension) -> BaseExpressionAPI:
    match dim.match_strategy:
        case MatchStrategy.EXACT:
            return self._compile_exact(dim)
        case MatchStrategy.RANGE:
            return self._compile_range(dim)
        case MatchStrategy.REGEX:
            return self._compile_regex(dim)
        # ... other strategies
```

The output of compilation is a `BaseExpressionAPI` object — the abstract base for all mountainash expressions. This object can be passed to `relation.with_columns()` to produce a new column in the DataFrame.

#### Diagram: Compiler Dispatch Architecture

<iframe src="../../sims/compiler-dispatch/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>Compiler Dispatch Architecture</summary>
Type: diagram
**sim-id:** compiler-dispatch<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive architecture diagram showing how DimensionCompiler routes each MatchStrategy to its corresponding compile method, and what expression tree each produces.

**Components:**
- Input node: Dimension object with strategy highlighted
- Central node: compile_dimension dispatcher
- 12 output branches, one per strategy, each showing the resulting expression tree structure
- Color coding: equality strategies (blue), numeric (green), string (orange), set (purple)

**Interactions:** Click any strategy branch to expand the expression tree it produces, showing the specific `ma.*` calls involved. Hover over expression nodes for documentation tooltips. A dropdown selects different example dimensions to show different compiled outputs.

**Learning objective:** Trace the compilation path from a Dimension's match_strategy to its resulting expression tree (Bloom: Apply)
</details>

<!-- concept:32 -->
## Compile Exact Expression

The EXACT compilation is the simplest and most illustrative. It constructs a ternary equality comparison between the rule column and the context literal column, with sentinel-awareness built into both column references.

The compiled expression for an EXACT dimension with `dimension_name="region"` and `data_type=str` is:

```python
sentinels = {"<NA>", "<NOT_SET>"}
rule_col = ma.t_col("region", unknown=sentinels)
ctx_col = ma.t_col("__ctx_region", unknown=sentinels)
expr = rule_col.t_eq(ctx_col)
```

The key elements are:

1. **Sentinel set selection**: the compiler calls `_sentinels_for_type(dim.data_type)` to choose string or numeric sentinels
2. **Rule column reference**: `ma.t_col(dim.resolved_rule_field, unknown=sentinels)` — uses the resolved rule field name (which may differ from dimension_name)
3. **Context column reference**: `ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)` — always uses `__ctx_` prefix with the dimension name
4. **Ternary equality**: `.t_eq()` produces 1 when values are equal, -1 when different, and 0 when either operand is a sentinel

The NOT_EQUAL compilation is identical except it uses `.t_ne()` instead of `.t_eq()`.

The sibling `_compile_exact_key` variant is used when only the rule side may wildcard (partition-key routing); context sentinels remain ordinary non-matches. Chapter 2 explains this strategy-level distinction.

<!-- concept:33 -->
## Compile Range Expression

RANGE compilation produces a compound expression that tests both the lower and upper bounds and combines them by taking their row-wise ternary minimum, which is equivalent to a ternary AND. This is the only strategy that references two rule columns rather than one.

For a dimension with `range_min_field="age_min"`, `range_max_field="age_max"`, and inclusive bounds:

```python
sentinels = {-999999999, -999999998}
ctx_col = ma.t_col("__ctx_age", unknown=sentinels)
min_col = ma.t_col("age_min", unknown=sentinels)
max_col = ma.t_col("age_max", unknown=sentinels)

# Lower bound: min <= context
lower = min_col.t_le(ctx_col)
# Upper bound: max >= context
upper = max_col.t_ge(ctx_col)
# Combined: row-wise ternary minimum
expr = ma.when(lower.le(upper)).then(lower).otherwise(upper)
```

The explicit row-wise minimum follows ternary AND semantics: if either operand is -1 (FALSE), the result is -1. If both are 1 (TRUE), the result is 1. If one is 0 (UNKNOWN) and the other is non-negative, the result is 0. With one sentinel bound, a contradiction on the other side still produces -1; otherwise the result is 0, not a hard match. The explicit `when` expression avoids the horizontal-minimum row-shape collapse noted in the compiler for Polars 1.44.

When bounds are exclusive, the compiler substitutes `.t_lt()` for `.t_le()` or `.t_gt()` for `.t_ge()`:

| Configuration | Lower Bound Expression | Upper Bound Expression |
|---------------|----------------------|----------------------|
| Both inclusive (default) | `min_col.t_le(ctx_col)` | `max_col.t_ge(ctx_col)` |
| Min exclusive | `min_col.t_lt(ctx_col)` | `max_col.t_ge(ctx_col)` |
| Max exclusive | `min_col.t_le(ctx_col)` | `max_col.t_gt(ctx_col)` |
| Both exclusive | `min_col.t_lt(ctx_col)` | `max_col.t_gt(ctx_col)` |

<!-- concept:34 -->
## Compile String Match

PREFIX, SUFFIX, and CONTAINS share a common compilation pattern encapsulated in `_compile_string_match`. These strategies cannot use the `t_col` / `t_eq` approach because the underlying string operations (starts_with, ends_with, contains) return Boolean values, not ternary integers.

The compiler builds a three-branch conditional expression that explicitly handles both a sentinel rule cell and a non-concrete context value — via `_context_is_nonconcrete` — before falling back to `otherwise`:

```python
rule_col = ma.col(dim.resolved_rule_field)
ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)

# Branch 1: sentinel rule or non-concrete context -> UNKNOWN
rule_is_sentinel = rule_col.eq(ma.lit("<NA>")) | rule_col.eq(ma.lit("<NOT_SET>"))
context_is_nonconcrete = (
    ctx_col.is_null()
    | ctx_col.eq(ma.lit("<NA>"))
    | ctx_col.eq(ma.lit("<NOT_SET>"))
)
unknown = rule_is_sentinel | context_is_nonconcrete

# Branch 2: string operation matches -> TRUE
match = ctx_col.str.starts_with(rule_col)  # or ends_with / contains

# Assemble: when unknown -> 0, when match -> 1, otherwise -> -1
expr = ma.when(unknown).then(0).when(match).then(1).otherwise(-1)
```

Note that this pattern uses `ma.col()` (not `ma.t_col()`) because the sentinel detection is handled explicitly in the `when` branches rather than by the column reference itself. The three strategies differ only in which string method is invoked:

- PREFIX: `ctx_col.str.starts_with(rule_col)`
- SUFFIX: `ctx_col.str.ends_with(rule_col)`
- CONTAINS: `ctx_col.str.contains(rule_col)`

<!-- concept:35 -->
## Compile Regex Expression

Regex compilation has two sibling paths. For `REGEX`, `_compile_regex_per_row` reads a pattern from each rule row and maps sentinel patterns to UNKNOWN; for `CONTEXT_REGEX`, `_compile_context_regex` applies the metadata's literal `regex_pattern` uniformly as a global context validator. Chapter 2 explains the strategy-level distinction; this section focuses on the resulting expression shapes.

For `CONTEXT_REGEX`, the compiled expression is:

```python
ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
match = ctx_col.str.regex_contains(dim.regex_pattern)
expr = (
    ma.when(self._context_is_nonconcrete(dim))
    .then(-1)
    .when(match)
    .then(1)
    .otherwise(-1)
)
```

There is no UNKNOWN branch because the pattern is always defined (enforced by the Pydantic validator). A non-concrete context — `None`, or the reserved `<NA>`/`<NOT_SET>` markers — forces FALSE (-1), taking precedence over the regex outcome even if the pattern could match the marker's spelling; otherwise the result is binary: the context value either matches the regex (1) or does not (-1). This makes `CONTEXT_REGEX` dimensions behave as global filters — they eliminate all rules simultaneously if the context fails the pattern check or is non-concrete.

<!-- concept:36 -->
## Compile Set Expression

`SET_MEMBERSHIP` and `SET_EXCLUSION` use the ternary-aware `list.t_contains` operator (with `.t_not()` negating the result for `SET_EXCLUSION`). The operator handles sentinel detection on the context side — if the context value is a sentinel, the result is UNKNOWN (0). The rule side is normalized first so that its in-band wildcard can be detected before the membership operation:

```python
rule_col = normalize_set_expr(dim, ma.col(dim.resolved_rule_field))
ctx_col = ma.t_col(
    CTX_PREFIX + dim.dimension_name,
    unknown=sentinels_for(dim.data_type),
)
is_wild = set_wildcard_predicate(dim, rule_col)

# SET_MEMBERSHIP
expr = ma.when(is_wild).then(0).otherwise(rule_col.list.t_contains(ctx_col))

# SET_EXCLUSION
expr = ma.when(is_wild).then(0).otherwise(rule_col.list.t_contains(ctx_col).t_not())
```

The rule column starts as a list-valued `ma.col()` reference because set wildcards are represented in-band rather than by scalar `t_col` handling. `normalize_set_expr` canonicalizes concrete lists and converts a null list to the wildcard representation; `set_wildcard_predicate` then turns that wildcard into ternary UNKNOWN (0), while concrete lists use the appropriate membership operator.

<!-- concept:98 -->
## Set Wildcard Sentinel

A set-typed dimension cannot use a null list as a wildcard portably across backends. The shared `core/set_wildcard.py` protocol therefore represents a wildcard in-band as a single-element list, `[unknown_sentinel_for(dim.data_type)]`, and never as null. This keeps the list's type stable while giving every backend the same value to recognize.

`sentinel_list_expr(dim)` constructs the typed literal with `ma.lit([_typed_sentinel(dim)])`. The helper preserves the dimension's element type; in particular, float sentinels are coerced to Python `float` before creating the list literal rather than relying on a backend-native list cast.

After normalization, `set_wildcard_predicate(dim, col)` checks whether the list contains the typed sentinel. `validate_set_columns` reserves that value for the wildcard, so a concrete list cannot contain it; consequently, the predicate is unambiguous: a sentinel-containing normalized list is exactly the one-element wildcard list.

<!-- concept:99 -->
## Set Value Normalization

Set values must have one canonical representation before matching, fingerprinting, or accumulator coalescing. `normalize_set_expr(dim, col)` is the single normalizer: a null list becomes `sentinel_list_expr(dim)`, while a concrete list becomes `canonicalize_set_expr(col)`, which applies `col.list.unique().list.sort()`. The operation is idempotent, so applying it to an already normalized column does not change the result.

```python
def canonicalize_set_expr(col: BaseExpressionAPI) -> BaseExpressionAPI:
    return col.list.unique().list.sort()

def normalize_set_expr(dim: Dimension, col: BaseExpressionAPI) -> BaseExpressionAPI:
    return ma.when(col.is_null()).then(sentinel_list_expr(dim)).otherwise(
        canonicalize_set_expr(col)
    )
```

Sorting and deduplicating means lists such as `["gold", "silver", "gold"]` and `["silver", "gold"]` compare and fingerprint identically. Validation enforces the reserved-value contract: `validate_set_columns(rules_rel, set_dims)` rejects a concrete rule list that embeds the reserved sentinel, while `validate_set_no_null_elements(rules_rel, set_dims)` rejects a list containing an element-level null. A whole-list null (or an omitted cell) remains valid wildcard input and is normalized to `[sentinel]`; element-level nulls are not.

#### Diagram: Compiled Expression Gallery

<iframe src="../../sims/compiled-expression-gallery/main.html" width="100%" height="500px" scrolling="no"></iframe>
<details markdown="1">
<summary>Compiled Expression Gallery</summary>
Type: diagram
**sim-id:** compiled-expression-gallery<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Side-by-side comparison of the expression trees produced by each compile method, showing structural differences between strategy families.

**Components:**
- 6 panels: Exact/NotEqual, Range, String (PREFIX/SUFFIX/CONTAINS), Regex, Set, Threshold
- Each panel shows the expression tree with labeled nodes (ma.t_col, t_eq, t_and, when/then, etc.)
- Shared legend for node types (column ref, operator, literal, conditional)

**Interactions:** Click any panel to expand it to full width with a step-by-step trace. Toggle between "structure view" (abstract tree) and "concrete view" (with example values filled in). Dropdown to select a specific dimension example for each panel.

**Learning objective:** Compare the structural complexity and node types across different compiled expressions (Bloom: Analyze)
</details>

<!-- concept:37 -->
## Compile Threshold Expression

The GREATER_THAN and LESS_THAN strategies produce straightforward ternary comparisons between the context value and the rule cell value. Unlike RANGE (which tests two bounds), threshold expressions test a single comparison.

The key subtlety is directionality — the comparison tests whether the *context* exceeds or falls below the *rule* value, not the other way around:

```python
# GREATER_THAN: context > rule
sentinels = {-999999999, -999999998}
rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
expr = ctx_col.t_gt(rule_col)

# LESS_THAN: context < rule
expr = ctx_col.t_lt(rule_col)
```

Note the operand order: `ctx_col.t_gt(rule_col)` reads as "context is greater than rule." This matters for the accumulator engine (Chapter 7), where threshold coalescing uses `ma.greatest` for GREATER_THAN (taking the stricter lower bound) and `ma.least` for LESS_THAN (taking the stricter upper bound).

<!-- concept:38 -->
## Sentinel-Aware Ternary

The sentinel-aware ternary system is the mechanism that makes wildcard handling automatic and correct. It operates at two levels:

1. **Column-level**: `ma.t_col(name, unknown={...})` declares which values in a column should be treated as UNKNOWN. The expression library's backend adapter intercepts comparisons involving these columns and returns 0 whenever either operand contains a value from the `unknown` set.

2. **Expression-level**: the `when/then/otherwise` pattern in string match compilation explicitly checks for sentinels before performing string operations, producing 0 for sentinel rows.

The column-level approach (used by EXACT, NOT_EQUAL, RANGE, GREATER_THAN, LESS_THAN, SET_MEMBERSHIP, SET_EXCLUSION) is preferred because the sentinel awareness is embedded in the column reference and propagates automatically through any ternary operator applied to it. RANGE and the SET strategies do combine their column-level comparisons with an explicit `when/then/otherwise` — a row-wise minimum for RANGE's two bounds, a wildcard check for SET_MEMBERSHIP/SET_EXCLUSION — RANGE uses the bounds' ternary outcomes, while each SET strategy explicitly recognizes its rule-side wildcard list and relies on the context-side `t_col` for scalar sentinel handling.

The expression-level approach (used by PREFIX, SUFFIX, CONTAINS) is necessary when the underlying operation is Boolean (not ternary) and cannot be wrapped in the `t_col` mechanism. The compiler explicitly converts the Boolean result into a ternary integer using the `when/then/otherwise` pattern.

Both approaches produce the same ternary semantics — they differ only in implementation mechanism. The consumer of the compiled expression (the engine) does not need to know which approach was used; it simply applies the expression as a column transformation and receives a ternary integer column.

<!-- concept:39 -->
## Context Value Extraction

Before any dimension expression can be evaluated, the context values must be available as columns in the rules DataFrame. The `extract_context_values()` function handles the conversion from a user-provided context (dict or Pydantic model) to a dictionary of values ready for column injection.

The extraction process for each dimension:

1. Determine the context field name using `dim.resolved_context_field`
2. Look up that field in the context dict (or model dump)
3. If the value is `None` or the field is missing, substitute the dimension's typed absent value: the declared type's `NOT_SET` sentinel for most types, but `None` for a `DataType.BOOL` dimension, which has no in-band sentinel (or, when no metadata is supplied, the string `NOT_SET`)
4. Return the value mapped to the dimension name (not the context field name)

The result is a dictionary keyed by dimension name, containing the actual context value, a typed sentinel, or — for an absent Boolean — `None`. The engine then broadcasts each value as a literal column with the `__ctx_` prefix, binding a `None` Boolean through a nullable-Boolean literal so a backend's cast does not turn the absence into `False`:

```python
# Context extraction
context = {"region": "AU", "tier": None}
values = extract_context_values(context, ["region", "tier"])
# {"region": "AU", "tier": "<NOT_SET>"}

# Engine broadcasts as literal columns:
# __ctx_region = "AU" (every row)
# __ctx_tier = "<NOT_SET>" (every row)
```

For ordinary constraint strategies, typed `NOT_SET` values (or a null Boolean) preserve missing context as UNKNOWN (0). This differs from strict `CONTEXT_REGEX`, which returns -1 for non-concrete context, and `EXACT_KEY` routing, where a concrete rule key rejects absent context while a rule-side wildcard still returns 0. Context absence and a rule-side wildcard are separate inputs; their outcomes depend on the strategy.

#### Diagram: Context Extraction and Column Binding

<iframe src="../../sims/context-extraction-binding/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Context Extraction and Column Binding</summary>
Type: workflow
**sim-id:** context-extraction-binding<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Step-by-step animation showing how context values flow from a user-provided dict/model through extraction, sentinel substitution, and literal column binding.

**Components:**
- Left panel: context object with fields and values
- Center: extraction function with arrows showing field lookup and sentinel substitution
- Right: rules DataFrame with new __ctx_ columns being added

**Interactions:** Editable context input fields (user can type values or leave blank). Step buttons advance through: field lookup, None detection, sentinel substitution, column broadcast. Missing fields animate the NOT_SET substitution path in red-to-gray transition.

**Learning objective:** Trace the path of a context value from user input to DataFrame literal column (Bloom: Apply)
</details>

## The Sentinel Selection Helper

A small but important internal method drives sentinel selection for all compiled expressions. The `_sentinels_for_type` method examines the dimension's `data_type` and returns the appropriate sentinel set:

```python
def _sentinels_for_type(self, data_type: type) -> set:
    if data_type in (int, float):
        return NUMERIC_SENTINELS  # {-999999999, -999999998}
    return STRING_SENTINELS       # {"<NA>", "<NOT_SET>"}
```

This method is called at the start of every strategy-specific compile method (except REGEX and string matches, which handle sentinels differently). It ensures that ternary-aware column references (`t_col`) are constructed with the correct sentinel set for the dimension's data type, preventing the mismatch where string sentinels are applied to numeric columns or vice versa.

The sentinel set always contains *both* the unknown sentinel (from rule data) and the not-set sentinel (from context extraction). This is because the compiled expression must handle both sources of missing data — the `t_col` reference applies the same UNKNOWN mapping regardless of which sentinel triggered it.

## The CTX_PREFIX Convention

All compiled expressions reference a context literal column using the naming convention `CTX_PREFIX + dimension_name`, where `CTX_PREFIX` is the string `"__ctx_"`. This prefix serves as a namespace separator, preventing collisions between context literal columns and any original columns in the rules DataFrame.

The context column always uses the `dimension_name` (not the `resolved_context_field`), because the engine normalizes all context values to dimension-name keys during extraction. The rule column, by contrast, uses the `resolved_rule_field`, which may differ from the dimension name.

This asymmetry is intentional:

| Column | Named By | Reason |
|--------|---------|--------|
| Rule column | `dim.resolved_rule_field` | Must match actual DataFrame column |
| Context literal | `__ctx_ + dim.dimension_name` | Internal convention, not user-visible |

The context literal column is always temporary — it is dropped from the result DataFrame at the end of evaluation.

## Compilation in Practice

The typical compilation workflow occurs once during engine construction:

1. User creates `DimensionsMetadata` with all dimension definitions
2. User passes metadata to `ExpressionRulesEngine` constructor
3. Constructor instantiates `DimensionCompiler` internally
4. Compiler iterates through dimensions, calling `compile_dimension` on each
5. Results are stored as `self._expressions: dict[str, BaseExpressionAPI]`
6. On each `evaluate()` call, the pre-compiled expressions are applied to the rules DataFrame with fresh context literal columns

This compile-once-evaluate-many pattern means the cost of expression construction is amortized across all evaluations. The compiled expressions are stateless templates — they reference column names, not column data — so they can be reused with any DataFrame that has the expected schema.

```python
from mountainash_rules import ExpressionRulesEngine, DimensionsMetadata

# Compilation happens at construction time
engine = ExpressionRulesEngine(
    rules=rules_df,
    dimension_metadata=metadata,  # Triggers compilation internally
)

# Each evaluate() reuses the same compiled expressions
result1 = engine.evaluate(context_1)
result2 = engine.evaluate(context_2)
result3 = engine.evaluate(context_3)
```

## Compile Output Summary

The following table summarizes the expression structure each compile method produces, showing the key `ma.*` calls and sentinel handling approach for each strategy family:

| Strategy | Expression Root | Sentinel Handling | Column References | Output Range |
|----------|----------------|-------------------|-------------------|-------------|
| EXACT | `t_eq` | Column-level (`t_col`) | 1 rule, 1 context | 1/0/-1 |
| NOT_EQUAL | `t_ne` | Column-level (`t_col`) | 1 rule, 1 context | 1/0/-1 |
| RANGE | row-wise minimum (`when/then/otherwise`) | Column-level (`t_col`) | 2 rule (min,max), 1 context | 1/0/-1 |
| GREATER_THAN | `t_gt` | Column-level (`t_col`) | 1 rule, 1 context | 1/0/-1 |
| LESS_THAN | `t_lt` | Column-level (`t_col`) | 1 rule, 1 context | 1/0/-1 |
| PREFIX/SUFFIX/CONTAINS | `when/then/otherwise` | Expression-level | 1 rule, 1 context | 1/0/-1 |
| REGEX | `when/then/otherwise` | Rule-side sentinels + non-concrete context | 1 rule (pattern), 1 context | 1/0/-1 |
| CONTEXT_REGEX | `when/then/otherwise` | Non-concrete context → -1 (hard fail) | 0 rule, 1 context | 1/-1 only |
| SET_MEMBERSHIP | `when/then/otherwise` | Rule-side `[sentinel]` + context-side (`t_col`) | 1 rule (list), 1 context | 1/0/-1 |
| SET_EXCLUSION | `when/then/otherwise` | Rule-side `[sentinel]` + context-side (`t_col`) | 1 rule (list), 1 context | 1/0/-1 |

#### Diagram: Compilation Pipeline End-to-End

<iframe src="../../sims/compilation-pipeline-e2e/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>Compilation Pipeline End-to-End</summary>
Type: workflow
**sim-id:** compilation-pipeline-e2e<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** End-to-end animation showing the complete lifecycle of a compiled expression from metadata definition through compilation, storage, and evaluation-time application.

**Components:**
- Stage 1: Dimension metadata with highlighted fields (strategy, data_type, field names)
- Stage 2: Compiler dispatch selecting the correct compile method
- Stage 3: Expression tree being constructed with labeled nodes
- Stage 4: Expression stored in engine's _expressions dict
- Stage 5: At evaluation time, expression applied to DataFrame with context literals

**Interactions:** Step buttons advance through each stage. At stage 2, the user can toggle between different strategies to see different dispatch paths. At stage 5, sample data fills the DataFrame and the expression evaluates to show ternary results. Reset button returns to stage 1.

**Learning objective:** Trace a dimension definition through compilation, storage, and runtime evaluation (Bloom: Analyze)
</details>

## Key Takeaways

- The **DimensionCompiler** is a stateless translator that converts Dimension metadata into backend-agnostic expression templates, dispatching to strategy-specific methods via pattern matching.
- **Compile Exact** uses `ma.t_col` with sentinel sets and `.t_eq()` to produce a ternary equality comparison with automatic wildcard handling; `_compile_exact_key` is the rule-side-wildcard-only sibling used for partition routing.
- **Compile Range** produces a compound expression that combines lower and upper bound checks via a row-wise ternary minimum (equivalent to `t_and`), supporting configurable inclusive/exclusive boundaries.
- **Compile String Match** uses an explicit `when/then/otherwise` pattern to convert Boolean string operations into ternary integers, with sentinel detection as the first branch.
- **Compile Regex** separates per-row `REGEX` patterns (`_compile_regex_per_row`) from metadata-literal `CONTEXT_REGEX` validation (`_compile_context_regex`).
- **Compile Set** normalizes list columns, recognizes the in-band `[sentinel]` wildcard, and applies `list.t_contains` (negated with `.t_not()` for SET_EXCLUSION) for concrete rules.
- **Set Value Normalization** sorts and deduplicates concrete lists so equal sets compare and fingerprint identically, while validation rejects reserved sentinels and element-level nulls.
- **Sentinel-Aware Ternary** operates at two levels: column-level (via `t_col`) for most strategies, and expression-level (via `when/then`) for string operations.
- **Context Value Extraction** maps metadata-backed dimensions to their typed `NOT_SET` sentinel when missing or `None`, preserving Boolean absence as `None`; without metadata, it uses the generic string `NOT_SET` before column binding.
