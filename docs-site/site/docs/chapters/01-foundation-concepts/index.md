---
title: "Chapter 1: Foundation Concepts"
description: "Core abstractions underpinning the mountainash-rules engine including ternary logic, sentinel values, match strategies, vectorized evaluation, and backend-agnostic design."
generated_by: claude skill chapter-content-generator
date: 2026-06-03
version: 0.08
---

# Chapter 1: Foundation Concepts

## Summary

This chapter introduces the foundational abstractions that underpin the mountainash-rules engine. You will learn about ternary logic and its role in wildcard matching, sentinel values for representing missing or inapplicable data, match strategy patterns, Pydantic model validation, vectorized evaluation principles, backend-agnostic design, and the core mountainash expression and relation libraries.

## Concepts Covered

- Ternary Logic
- Sentinel Values
- Match Strategy Patterns
- Pydantic Model Validation
- Vectorized Evaluation
- Backend-Agnostic Design
- DataFrame as Rule Store
- Mountainash Expressions
- Mountainash Relations
- Context Object

## Prerequisites

None — this is the introductory chapter.

---

## Why a Rules Engine?

Business logic in production systems often takes the form of nested conditional statements: "if the customer is in region A and the product category is X, apply discount Y." As the number of conditions and outcomes grows, maintaining these conditionals in application code becomes fragile and error-prone. A rules engine externalizes that logic into a data structure — typically a table — where each row represents one rule and each column represents either a condition or an outcome.

The mountainash-rules package takes this concept further by evaluating all rules simultaneously against a given context, using vectorized operations rather than row-by-row iteration. Before diving into the engine itself, you need to understand ten foundational concepts that make this approach work.

<!-- concept:1 -->
## Ternary Logic

Classical Boolean logic recognizes two states: true and false. When evaluating business rules, however, a third state is essential — *unknown*. A rule might not specify a value for a particular dimension, meaning that dimension should be treated as a wildcard: the rule neither matches nor fails on that dimension.

Mountainash-rules represents these three states as integers:

| Value | Meaning | Interpretation |
|-------|---------|----------------|
| 1 | TRUE | Hard match — the rule's value matches the context |
| 0 | UNKNOWN | Wildcard — the rule does not constrain this dimension |
| -1 | FALSE | Non-match — the rule's value conflicts with the context |

This encoding has a useful algebraic property: combining multiple dimensions requires only taking the minimum value across all dimensions. If any dimension returns -1 (FALSE), the minimum is -1, and the rule is eliminated. If all dimensions return 0 or 1, the rule survives. The number of 1-valued dimensions gives the rule's *specificity* — how precisely it targets the given context.

!!! note "Why Not Null?"
    Many systems use null or NaN for missing values, but these propagate unpredictably through arithmetic. By mapping unknowns to the integer 0, the rules engine can use standard numeric operations (minimum, summation, comparison) without special null-handling branches.

<!-- concept:2 -->
## Sentinel Values

Ternary logic requires a mechanism for detecting when a rule cell or context field is "empty" — that is, when it carries no meaningful constraint. The engine uses **sentinel values**: reserved constants that can never appear as legitimate business data. When the engine encounters a sentinel in a rule column or context value, it maps the comparison result to the UNKNOWN (0) state in ternary logic.

The rules engine defines four sentinel constants, separated by data type:

- **String sentinels**: `"<NA>"` for unknown/wildcard rule values and `"<NOT_SET>"` for missing context values
- **Numeric sentinels**: `-999999999` for unknown numeric rule values and `-999999998` for missing numeric context values

The distinction between `<NA>` and `<NOT_SET>` matters during compilation. A rule cell containing `<NA>` means "this rule does not constrain this dimension" (wildcard). A context field containing `<NOT_SET>` means "the caller did not provide this value" (missing input). Both result in UNKNOWN (0) in the ternary output, but they originate from different sources and serve different conceptual roles.

```python
from mountainash_rules.constants import UNKNOWN, NOT_SET, UNKNOWN_NUMERIC, NOT_SET_NUMERIC

# String sentinels
UNKNOWN         # "<NA>"      — wildcard in rule table
NOT_SET         # "<NOT_SET>" — missing from context

# Numeric sentinels
UNKNOWN_NUMERIC  # -999999999  — wildcard in numeric rule column
NOT_SET_NUMERIC  # -999999998  — missing numeric context value
```

The engine also provides convenience sets `STRING_SENTINELS` and `NUMERIC_SENTINELS` that group each pair together, making it straightforward to test whether any value is a sentinel regardless of its source.

<!-- concept:3 -->
## Match Strategy Patterns

A match strategy defines the comparison operation used to evaluate a context value against a rule value. Rather than hardcoding a single equality check, mountainash-rules supports a catalogue of strategies that handle different data shapes: exact equality, inequality, numeric ranges, string patterns, and set operations.

Each strategy answers the same fundamental question — "does the context value satisfy this rule cell?" — but does so with different semantics. For example, an EXACT strategy checks equality, while a RANGE strategy checks whether a numeric context value falls between a minimum and maximum bound stored in the rule row.

The following table provides an overview of all available strategies, grouped by category:

| Category | Strategies | Applies To |
|----------|-----------|------------|
| Equality | EXACT, NOT_EQUAL | Strings, numerics |
| Numeric comparison | RANGE, GREATER_THAN, LESS_THAN | int, float |
| String pattern | PREFIX, SUFFIX, CONTAINS, REGEX | str |
| Set operations | SET_MEMBERSHIP, SET_EXCLUSION | Any (list columns) |

Each strategy is examined in detail in Chapter 2. The key design insight is that strategy selection is metadata-driven: you declare which strategy a dimension uses in the dimension model, and the engine compiles the appropriate comparison logic automatically.

#### Diagram: Match Strategy Decision Tree

<iframe src="../../sims/match-strategy-tree/main.html" width="100%" height="500px" scrolling="no"></iframe>
<details markdown="1">
<summary>Match Strategy Decision Tree</summary>
Type: diagram
**sim-id:** match-strategy-tree<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive decision tree showing how to select the appropriate match strategy based on data type and comparison semantics.

**Components:**
- Root node: "What data type?" branching to String, Numeric, List
- String branch: EXACT, NOT_EQUAL, PREFIX, SUFFIX, CONTAINS, REGEX
- Numeric branch: EXACT, NOT_EQUAL, RANGE, GREATER_THAN, LESS_THAN
- List branch: SET_MEMBERSHIP, SET_EXCLUSION

**Interactions:** Click a strategy node to see a tooltip with a one-line description and example. Hover over edges to see the decision criterion. Nodes are color-coded by category (equality=blue, numeric=green, string=orange, set=purple).

**Learning objective:** Classify match strategies by data type and comparison semantics (Bloom: Analyze)
</details>

<!-- concept:4 -->
## Pydantic Model Validation

The mountainash-rules package uses Pydantic `BaseModel` classes to define and validate its configuration objects. Pydantic provides automatic type checking, default value handling, and custom validation logic through model validators — all of which are critical for catching configuration errors at construction time rather than at evaluation time.

Two primary models make use of Pydantic validation in the rules engine:

1. **Dimension** — represents a single column's evaluation metadata (match strategy, data type, field mappings)
2. **DimensionsMetadata** — a validated collection of Dimension objects with uniqueness constraints

When you create a `Dimension`, Pydantic's `model_validator` runs after field assignment to enforce strategy-specific constraints. For instance, a RANGE dimension must specify `range_min_field` and `range_max_field`, and its `data_type` must be `int` or `float`. A PREFIX dimension must have `data_type` of `str`. If any constraint is violated, the validator raises a `ValueError` with a descriptive message before the engine is ever constructed.

```python
from mountainash_rules import Dimension, MatchStrategy

# This is valid — RANGE with numeric type and both range fields
dim = Dimension(
    dimension_name="age",
    match_strategy=MatchStrategy.RANGE,
    data_type=int,
    range_min_field="age_min",
    range_max_field="age_max",
)

# This would raise ValueError — RANGE requires int or float
# dim = Dimension(
#     dimension_name="age",
#     match_strategy=MatchStrategy.RANGE,
#     data_type=str,
#     range_min_field="age_min",
#     range_max_field="age_max",
# )
```

This early-validation approach ensures that by the time the engine compiles expressions, every dimension is known to be internally consistent.

<!-- concept:5 -->
## Vectorized Evaluation

Traditional rule engines evaluate each rule one at a time, iterating through the rule set for each incoming context. Mountainash-rules takes a fundamentally different approach: it evaluates all rules simultaneously using **vectorized operations** — column-wise computations that process the entire rules DataFrame in a single pass.

The core idea is that a rules table with \( n \) rows and \( d \) dimensions can be evaluated as \( d \) column operations rather than \( n \times d \) cell comparisons. Each dimension's comparison logic is compiled into an expression that operates on the entire column at once. The engine then combines the per-dimension ternary columns (one per dimension) into a survival flag and a specificity score using vectorized minimum and summation operations.

This design yields two major benefits:

- **Performance**: vectorized operations in modern DataFrame libraries (Polars, PyArrow) leverage SIMD instructions and cache-friendly memory layouts, processing millions of rules per second
- **Simplicity**: the evaluation pipeline is a fixed sequence of column transformations — no conditional branching, no early exits, no rule ordering dependencies

#### Diagram: Row-by-Row vs Vectorized Evaluation

<iframe src="../../sims/vectorized-vs-iterative/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>Row-by-Row vs Vectorized Evaluation</summary>
Type: microsim
**sim-id:** vectorized-vs-iterative<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Animated comparison of row-by-row iteration (left panel) versus vectorized column-wise evaluation (right panel) on a sample 8-row, 3-dimension rules table.

**Controls:**
- Play/Pause button to control animation
- Speed slider (1x to 5x)
- Reset button

**Visual elements:**
- Left panel: grid of cells with a cursor moving row-by-row, left-to-right, highlighting each cell as it is evaluated. Counter shows total cell evaluations.
- Right panel: same grid, but entire columns highlight simultaneously. Counter shows total column operations.
- Color coding: green = TRUE (1), gray = UNKNOWN (0), red = FALSE (-1)

**Behavior:** On play, both panels animate simultaneously. Left panel evaluates cell by cell (8 rows x 3 dims = 24 steps). Right panel evaluates column by column (3 steps). Final step shows survival filter applied to both, reaching the same result.

**Learning objective:** Compare the efficiency of vectorized versus iterative rule evaluation (Bloom: Evaluate)
</details>

<!-- concept:6 -->
## Backend-Agnostic Design

A key architectural decision in mountainash-rules is that the engine does not depend on any specific DataFrame library. The expression and relation APIs provided by the `mountainash` package abstract over multiple backends — Polars, Pandas (via Narwhals), PyArrow, and Ibis — so the same compiled expressions work regardless of which library the caller uses for their rules DataFrame.

This is achieved through two abstraction layers:

- **`mountainash.expressions`** (`ma`): provides column references (`ma.col`, `ma.t_col`), literals (`ma.lit`), and operators (`.t_eq`, `.t_gt`, `.t_and`, etc.) that produce backend-neutral expression trees
- **`mountainash.relations`** (`relation()`): wraps any supported DataFrame in a uniform API with methods like `.with_columns()`, `.filter()`, `.sort()`, `.join()`, `.collect()`

When the engine calls `relation(rules)`, the library inspects the type of the input and selects the appropriate backend adapter. All subsequent operations — adding context literal columns, applying dimension expressions, filtering survivors — go through this adapter. The final `.collect()` call materializes the result back into the caller's native DataFrame type.

```python
import polars as pl
from mountainash_rules import ExpressionRulesEngine, DimensionsMetadata, Dimension

# The same engine works with Polars DataFrames...
rules_polars = pl.DataFrame({...})
engine = ExpressionRulesEngine(rules=rules_polars, dimension_metadata=metadata)
result = engine.evaluate(context)  # Returns Polars DataFrame

# ...and would also work with Pandas, PyArrow, or Ibis inputs
```

The backend-agnostic design means that teams can adopt mountainash-rules without changing their existing data pipeline infrastructure.

<!-- concept:7 -->
## DataFrame as Rule Store

In mountainash-rules, the rules table is stored as a DataFrame — not a database table, not an in-memory tree, and not a list of dictionaries. Each row represents one rule, and each column represents either a dimension (a condition the rule constrains) or a payload field (an outcome value the rule carries).

This representation has several advantages over alternative data structures:

- **Columnar storage** enables vectorized evaluation natively
- **Schema enforcement** ensures all rules have the same structure
- **Interoperability** with data pipelines, analytics tools, and file formats (Parquet, CSV, Arrow)
- **Immutability** (in Polars) prevents accidental mutation during evaluation

During evaluation, the engine adds temporary columns to the DataFrame — context literal columns (`__ctx_*`), ternary result columns (`__t_*`), survival flags, specificity scores, and ranks. These are computed in-place as new columns (without mutating existing data) and optionally dropped before returning the result.

The prefix `__ctx_` is a reserved namespace. Context values from the caller are broadcast as literal columns so that each row can be compared against the same context value using vectorized column-to-column operations rather than scalar comparisons.

<!-- concept:8 -->
<!-- concept:9 -->
## Mountainash Expressions

The `mountainash.expressions` module (imported as `ma`) provides the expression-building API that the rules engine uses to construct comparison logic. An expression is a lazy computation tree — it describes what to compute, not when to compute it. Expressions are only materialized when the relation's `.collect()` method is called.

The expression API includes several key building blocks:

- **`ma.col(name)`** — references an existing column by name
- **`ma.t_col(name, unknown={...})`** — a ternary-aware column reference that maps specified sentinel values to the UNKNOWN state (0)
- **`ma.lit(value)`** — a literal constant broadcast across all rows
- **`ma.when(cond).then(val).otherwise(val)`** — conditional expression (similar to SQL CASE)
- **Ternary operators** — `.t_eq()`, `.t_ne()`, `.t_gt()`, `.t_lt()`, `.t_le()`, `.t_ge()`, `.t_and()` produce ternary-valued (1/0/-1) results
- **Aggregation functions** — `ma.least()`, `ma.greatest()`, `ma.coalesce()` for combining values

The `t_col` constructor is particularly important. When you write `ma.t_col("region", unknown={"<NA>", "<NOT_SET>"})`, any row where the `region` column contains `"<NA>"` or `"<NOT_SET>"` will automatically produce 0 (UNKNOWN) when compared with a ternary operator, regardless of the other operand's value. This is how sentinel values integrate seamlessly with ternary logic.

#### Diagram: Expression Tree Anatomy

<iframe src="../../sims/expression-tree-anatomy/main.html" width="100%" height="500px" scrolling="no"></iframe>
<details markdown="1">
<summary>Expression Tree Anatomy</summary>
Type: diagram
**sim-id:** expression-tree-anatomy<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive visualization of how a compiled EXACT dimension expression decomposes into an expression tree.

**Components:**
- Tree nodes representing: `t_eq` (root), `t_col("region", unknown=sentinels)` (left child), `t_col("__ctx_region", unknown=sentinels)` (right child)
- Leaf nodes showing sentinel set contents
- Data flow arrows showing how a sample rule row and context value propagate through the tree to produce a ternary result

**Interactions:** Click any node to expand its documentation tooltip. Click a sample-data button to run a trace: the user selects a rule value and context value from dropdowns, and the diagram highlights the evaluation path, showing intermediate ternary values at each node.

**Learning objective:** Trace the evaluation of a mountainash expression from inputs to ternary output (Bloom: Apply)
</details>

## Mountainash Relations

While expressions define *what* to compute, relations define *where* and *how* the computation executes. The `mountainash.relations` module provides the `relation()` function, which wraps a DataFrame in a backend-agnostic relational API.

A relation supports the standard relational operations needed by the rules engine:

- `.with_columns(*exprs)` — add or replace columns using expression results
- `.filter(expr)` — retain only rows where the expression is truthy
- `.sort(col, descending=True)` — order rows by a column
- `.join(other, on=..., how=...)` — join two relations (inner, cross, anti)
- `.head(n)` — take the first n rows
- `.with_row_index(name=...)` — add a 0-based row index column
- `.drop(*cols)` — remove columns by name
- `.collect()` — materialize the lazy computation into a native DataFrame
- `.count_rows()` — return the row count as an integer

Relations are lazy by default: calling `.with_columns()` or `.filter()` builds a computation plan without executing it. The actual work happens when `.collect()` is called. This allows the backend to optimize the entire pipeline (predicate pushdown, projection pruning) before executing.

The rules engine pipeline is expressed entirely as a chain of relation operations. The `ExpressionRulesEngine._evaluate()` method constructs a relation from the input DataFrame, chains context binding, dimension expression, survival computation, filtering, sorting, and ranking operations, then collects the result.

<!-- concept:10 -->
## Context Object

The **context** represents the set of values that the rules should be evaluated against. When a caller asks "which rules match this situation?", the situation is described by a context object — typically a Python dictionary or a Pydantic `BaseModel` instance.

The `extract_context_values()` function handles context normalization. It accepts either a Pydantic model (calling `.model_dump()` to convert it to a dict) or a plain dictionary. For each dimension that the engine evaluates, it looks up the corresponding field name in the context dict. If the field is missing or `None`, the function substitutes the `NOT_SET` sentinel, ensuring that the downstream ternary logic produces the UNKNOWN state.

```python
from mountainash_rules.context import extract_context_values

# Dictionary context
context = {"region": "AU", "product_category": "electronics"}
values = extract_context_values(context, ["region", "product_category", "tier"])
# {"region": "AU", "product_category": "electronics", "tier": "<NOT_SET>"}

# Pydantic model context
from pydantic import BaseModel

class OrderContext(BaseModel):
    region: str
    product_category: str
    tier: str | None = None

ctx = OrderContext(region="AU", product_category="electronics")
values = extract_context_values(ctx, ["region", "product_category", "tier"])
# Same result — tier is None, so it maps to "<NOT_SET>"
```

The context object is the entry point for every evaluation. At runtime, the engine extracts context values, broadcasts them as literal columns in the rules DataFrame, and then evaluates each dimension expression by comparing the rule column against the corresponding context literal column.

#### Diagram: Context to Evaluation Pipeline

<iframe src="../../sims/context-pipeline-flow/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Context to Evaluation Pipeline</summary>
Type: workflow
**sim-id:** context-pipeline-flow<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Animated workflow showing how a context object flows through the evaluation pipeline: extraction, literal column broadcast, dimension expression application, survival computation, ranking, and result return.

**Components:**
- Left: context object (dict/Pydantic model) with labeled fields
- Center: rules DataFrame with temporary columns being added step by step
- Right: final RuleResult with survivors, specificity, and rank

**Interactions:** Step-through buttons (Next/Previous) advance through each pipeline phase. At each step, newly added columns highlight in yellow. Eliminated rows (FALSE survivors) fade to red.

**Learning objective:** Sequence the evaluation pipeline phases from context input to ranked result (Bloom: Understand)
</details>

## Putting It All Together

The ten foundation concepts form a layered architecture. At the base, **ternary logic** and **sentinel values** provide the mathematical framework for handling wildcards and missing data. **Match strategy patterns** define the vocabulary of comparison operations. **Pydantic model validation** ensures configuration correctness at construction time. **Vectorized evaluation** and the **DataFrame as rule store** establish the performance model. **Mountainash expressions** and **mountainash relations** provide the backend-agnostic computation layer. Finally, the **context object** is the runtime input that triggers the entire evaluation pipeline.

Each subsequent chapter builds on these concepts. Chapter 2 examines every match strategy in detail. Chapters 3 and 4 show how dimensions are modeled and compiled into expressions. Chapter 5 assembles the full expression rules engine. Chapter 6 covers result inspection and filtering. Chapters 7 through 9 introduce the accumulator engine, which uses these same foundations to build lattice structures for multi-rule aggregation.

## Key Takeaways

- **Ternary logic** (1 / 0 / -1) provides a clean algebraic framework for rule evaluation where wildcards are first-class citizens, not exceptional null-handling cases.
- **Sentinel values** (`<NA>`, `<NOT_SET>`, -999999999, -999999998) bridge the gap between missing data and ternary logic by marking cells that should map to the UNKNOWN state.
- **Match strategies** are a pluggable vocabulary of comparison operations — the engine does not hardcode equality; it delegates to strategy-specific compiled expressions.
- **Pydantic validation** catches configuration errors (wrong data types, missing range fields, incompatible strategies) at construction time, preventing subtle runtime failures.
- **Vectorized evaluation** processes all rules in parallel via column-wise operations, achieving orders-of-magnitude speedups over row-by-row iteration.
- **Backend-agnostic design** means the same compiled rules work across Polars, Pandas, PyArrow, and Ibis without code changes.
- **DataFrames** serve as the rule store, leveraging columnar storage for both evaluation performance and data pipeline interoperability.
- **The context object** normalizes caller input into a dictionary of dimension values, substituting sentinels for missing fields so the ternary logic handles them uniformly.
