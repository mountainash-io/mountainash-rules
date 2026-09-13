---
title: "Chapter 5: Expression Rules Engine"
description: "The ExpressionRulesEngine class and its single-pass vectorized evaluation pipeline from construction through context binding, survival computation, and rank assignment."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 5: Expression Rules Engine

## Summary

This chapter covers the ExpressionRulesEngine class — the primary entry point for single-pass vectorized rule evaluation. You will learn how the engine is constructed, the distinction between convenience and advanced construction paths, and the complete evaluation pipeline: context binding, dimension expression application, survival computation, specificity scoring, and rank assignment.

---

<!-- concept:40 -->
## The Central Engine Class

The `ExpressionRulesEngine` is the primary API for rule evaluation in mountainash-rules. It accepts a rules DataFrame and dimension configuration at construction time, compiles expressions once, and then evaluates any number of contexts against those rules using a fixed single-pass pipeline. Dimension configuration must come from exactly one non-empty definition; the constructor validates this before any compilation happens.

The engine is designed around three principles from Chapter 1: vectorized evaluation (all rules processed simultaneously), backend-agnostic design (any supported DataFrame type), and ternary logic (wildcards handled algebraically). This chapter shows how those principles manifest in the concrete implementation.

<!-- concept:41 -->
## Engine Construction

The engine constructor accepts three positional-or-keyword parameters plus one keyword-only parameter:

- **`rules`**: the DataFrame containing the rules table (required)
- **`dimension_metadata`**: a `DimensionsMetadata` object for automatic compilation (optional)
- **`dimension_expressions`**: a pre-compiled dict of expressions (optional)
- **`output_fields`**: an explicit list of output field names, keyword-only and accepted only on the expressions-only path (optional)

Exactly one of `dimension_metadata` or `dimension_expressions` must be provided, and it must configure at least one dimension — the constructor raises `ValueError` if both or neither are supplied, or if the supplied definition is empty.

```python
from mountainash_rules import ExpressionRulesEngine, DimensionsMetadata

# Construction with metadata (convenience path)
engine = ExpressionRulesEngine(
    rules=rules_df,
    dimension_metadata=metadata,
)

# Construction with pre-compiled expressions (advanced path)
engine = ExpressionRulesEngine(
    rules=rules_df,
    dimension_expressions=compiled_exprs,
)
```

During construction, the engine stores the rules DataFrame as `self._rules` and the compiled expressions as `self._expressions`. If metadata is provided, it also stores it as `self._metadata`. Evaluation consults metadata for context-field/type resolution, default hit-policy configuration, and set-column validation; the compiled expressions perform the dimension scoring.

<!-- concept:42 -->
## Convenience vs Advanced Path

The two construction paths serve different use cases:

**Convenience path** (provide `dimension_metadata`): the engine instantiates a `DimensionCompiler` internally and compiles all dimensions automatically. This is the typical path for most users who define their dimensions declaratively and let the engine handle compilation.

**Advanced path** (provide `dimension_expressions`): the caller provides pre-compiled expression templates directly. This path enables:

- Custom expressions that go beyond the standard match strategies
- Expressions compiled by a different compiler implementation
- Expressions that have been modified or composed programmatically
- Testing scenarios where specific expression behavior is needed

The advanced path bypasses the compiler entirely — the engine trusts that the provided expressions are valid and produce ternary integer columns when applied to the rules DataFrame.

Only the advanced (expressions-only) path may pass `output_fields` explicitly. These are the rule columns compared for `ANY` output agreement, not a projection of the returned columns. When supplied, the argument must be a nonempty list of unique, nonempty names present in the rules table. Expressions-only `ANY` requires this declaration even for zero or one survivor. The convenience path uses `dimension_metadata.output_fields` instead and rejects a constructor-level `output_fields` argument.

| Path | Input | Compilation | Use Case |
|------|-------|-------------|----------|
| Convenience | DimensionsMetadata | Automatic (DimensionCompiler) | Standard rule sets with declarative config |
| Advanced | dict[str, BaseExpressionAPI] | None (pre-compiled) | Custom expressions, testing, composition |

#### Diagram: Engine Construction Paths

<iframe src="../../sims/engine-construction-paths/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Engine Construction Paths</summary>
Type: workflow
**sim-id:** engine-construction-paths<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Decision flowchart showing the two engine construction paths and their internal processes.

**Components:**
- Left branch: Convenience path (metadata -> compiler -> expressions -> stored)
- Right branch: Advanced path (expressions -> stored directly)
- Shared output: engine ready for evaluate() calls
- Error path: both provided or neither provided -> ValueError

**Interactions:** Click either path to see it animate step by step. Hover over the error conditions to see the exact ValueError messages. Toggle between the two paths to compare the number of internal steps.

**Learning objective:** Choose the appropriate construction path based on the use case requirements (Bloom: Evaluate)
</details>

## Engine Immutability

Once constructed, the engine's internal state is effectively immutable. The compiled expressions dictionary, the rules DataFrame reference, and the metadata are all set during `__init__` and never modified afterward. This immutability provides several benefits:

- **Thread safety**: multiple threads can call `evaluate()` concurrently on the same engine without synchronization, because evaluation only reads the compiled expressions and produces new DataFrames without mutating shared state
- **Predictability**: the same engine with the same context always produces the same result — there is no hidden state that evolves between evaluations
- **Cacheability**: engines can be stored in application-level caches, keyed by rule set version, and reused for the lifetime of that version

The only mutable activity happens during evaluation, where temporary columns are added to *copies* of the rules DataFrame (the relation API does not mutate the original). The original `self._rules` is never modified.

<!-- concept:43 -->
## Single-Pass Evaluation

The `evaluate()` method is the engine's primary interface. It accepts a context and returns a `RuleResult` containing surviving rules ordered according to the active hit policy. The entire evaluation happens in a single pass through the rules DataFrame — there is no iteration, no recursion, and no multi-stage filtering.

The method signature:

```python
def evaluate(
    self,
    context: BaseModel | dict,
    dimensions: list[str] | None = None,
    top_n: int | None = None,
    min_specificity: int | None = None,
    include_observability: bool = True,
    hit_policy: HitPolicy | str | None = None,
    priority_field: str | None = None,
) -> RuleResult:
```

The optional parameters provide control over the evaluation:

- **`dimensions`**: evaluate only a subset of dimensions (useful for partial evaluation or debugging). `None` means all configured dimensions; an empty list, duplicate names, or non-list/non-string values raise `ValueError` — an empty list is no longer an alias for "all dimensions". A well-formed but unknown dimension name still raises `KeyError`.
- **`top_n`**: keep at most N remaining rows in applied-policy order after the specificity filter; must be a non-Boolean Python `int >= 0` or `None`, otherwise `ValueError`
- **`min_specificity`**: exclude survivors with fewer than N hard matches; must be a non-Boolean Python `int >= 0` or `None`, otherwise `ValueError`
- **`include_observability`**: retain or drop the per-dimension ternary columns in the result
- **`hit_policy`**: select survivor cardinality and ordering, given as a `HitPolicy` value or its lowercase string value (such as `"collect"`); `None` uses the policy from metadata, or `collect` when using pre-compiled expressions
- **`priority_field`**: identify the rule column used to order survivors for the `priority` hit policy, overriding the metadata value when supplied

These two selection parameters control which survivors are returned and how they are ordered; their complete semantics are deferred to [Chapter 6: Hit Policies](../06-hit-policies/).

Internally, `evaluate()` performs three preparatory steps before delegating to the pipeline:

1. Determine and validate the active dimensions (`None` means all dimensions; an empty list, duplicate names, or non-list/non-string values raise `ValueError`, and a well-formed but unknown name raises `KeyError`)
2. Validate the `top_n` and `min_specificity` limits, then normalize and validate the `hit_policy`
3. Extract context values using `extract_context_values()`

It then calls `_evaluate()` which executes the six-step pipeline and returns the materialized result DataFrame, which is wrapped in a `RuleResult` object.

<!-- concept:44 -->
## Context Binding Phase

The first step of the evaluation pipeline binds context values to the rules DataFrame as literal columns. Each context value is broadcast to every row, creating a uniform reference for column-to-column comparisons. A missing Boolean context value is bound as a typed null rather than a concrete `True`/`False` literal, so a backend whose Boolean cast would otherwise turn null into `False` still preserves "absent" as absent.

```python
from mountainash_rules.core.context import _context_literal

# Step 1: Bind context values as typed literal columns
rel = relation(self._rules)
ctx_columns = []
for name, value in context_values.items():
    dim = self._metadata.get_dimension(name) if self._metadata is not None else None
    literal = _context_literal(value, dim.data_type if dim is not None else None)
    ctx_columns.append(literal.alias(f"__ctx_{name}"))
rel = rel.with_columns(*ctx_columns)
```

After this step, the DataFrame has one new column per active dimension, all prefixed with `__ctx_`. For example, if evaluating dimensions `["region", "tier"]` with context `{"region": "AU", "tier": "gold"}`, the DataFrame gains columns `__ctx_region` (every row = "AU") and `__ctx_tier` (every row = "gold").

The literal broadcast is computationally cheap — modern DataFrame backends represent literal columns as a single scalar with a length, not by physically replicating the value per row. This means context binding adds negligible memory overhead regardless of rule count.

<!-- concept:45 -->
## Dimension Expression Phase

The second step applies each compiled dimension expression, producing a ternary column per dimension:

```python
# Step 2: Apply dimension expressions as named ternary columns
dim_columns = [
    self._expressions[dim_name].alias(f"__t_{dim_name}")
    for dim_name in active_dims
]
rel = rel.with_columns(*dim_columns)
```

After this step, the DataFrame has columns `__t_region`, `__t_tier`, etc., each containing integer values 1, 0, or -1 for every rule row. These columns represent the ternary evaluation of each dimension's compiled expression.

This is where the vectorized power manifests: all rules are evaluated for all dimensions in a single `with_columns` call. The backend processes each expression across the entire column using SIMD instructions, with no Python-level loops.

<!-- concept:46 -->
## Survival Computation

The third step determines which rules survive evaluation and how specific they are. A rule survives if no dimension produced FALSE (-1) — equivalently, if every dimension's ternary value is >= 0. The implementation computes this survival flag as a conjunction (logical AND) of each dimension's `ternary >= 0` check rather than a horizontal minimum, because a horizontal-minimum reduction over identical inputs can collapse to a single element on some backend versions; the conjunction preserves the same survival rule and row shape.

```python
import functools

# Step 3: Compute survival and specificity
t_cols = [ma.col(f"__t_{d}") for d in active_dims]

# Survival: every ternary value must be non-negative
survived = functools.reduce(
    lambda a, b: a.and_(b),
    (c.ge(ma.lit(0)) for c in t_cols),
).alias("__survived")

# Specificity: count of dimensions with TRUE (1)
specificity = sum(c.eq(ma.lit(1)).cast(int) for c in t_cols).alias("__specificity")

rel = rel.with_columns(survived, specificity)
```

The survival computation first turns each ternary column into a Boolean `>= 0` check, then combines the checks with `.and_()`. A -1 makes the row fail; 0 and 1 both pass. The nonempty dimension requirement gives the reduction at least one input.

The specificity score counts how many dimensions produced a hard match (TRUE = 1) by casting each equality check to integer (0 or 1) and summing. A rule that matches 3 out of 5 dimensions with hard matches has specificity 3.

!!! note "Why Specificity Matters"
    When multiple rules survive, specificity determines which is the "best" match. A rule that explicitly matches the context on 4 dimensions is more specific than one that matches on 2 and wildcards the rest. Specificity provides a natural, parameter-free ranking of survivors.

<!-- concept:47 -->
## Specificity Scoring

The specificity score is the count of dimensions where the ternary value is exactly 1 (TRUE). It does *not* count UNKNOWN (0) values — those represent wildcards, not matches. This distinction is crucial: two rules might both survive (neither has FALSE), but the one with more TRUE dimensions is more specific to the given context.

Consider a rule set with three dimensions (region, tier, category) and two surviving rules:

| Rule | __t_region | __t_tier | __t_category | __specificity |
|------|-----------|---------|-------------|---------------|
| R1 | 1 | 1 | 1 | 3 |
| R2 | 1 | 0 | 0 | 1 |

Both survive (no -1 values), but R1 is specificity 3 (matches on all three) while R2 is specificity 1 (matches only on region, wildcards the rest). The engine ranks R1 higher because it is a more precise match for the context.

Specificity scoring is computed vectorially — no sorting or comparison between rows is needed to determine each row's specificity. It is a purely per-row calculation.

<!-- concept:48 -->
## Rank Assignment

After computing survival and specificity, the pipeline filters out non-survivors, orders the survivors using the active hit policy (specificity descending by default), and assigns a 1-based rank:

```python
# Step 4: Filter, order, and rank
keys = ordering_keys(hit_policy, info.priority_field)
rel = (
    rel
    .filter(ma.col("__survived"))
    .sort(*[k for k, _ in keys], descending=[d for _, d in keys])
    .with_row_index(name="__rank")
    .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
)
```

The rank is 1-based (not 0-based) for user convenience — the best match under the selected ordering is rank 1. With the default `collect` policy, specificity determines the primary ordering; other policies may use rule order or a priority column.

After ranking, optional post-filters are applied:

- `min_specificity`: removes rows where `__specificity < min_specificity`
- `top_n`: keeps only the first N rows (by rank)

These filters run after rank assignment, so the `__rank` column reflects the full ranking before any filtering. This means if you request `top_n=3` and `min_specificity=2`, a rule might have rank 5 in the filtered result (if ranks 2-4 were eliminated by min_specificity).

Finally, temporary columns are dropped:

```python
# Step 6: Clean up
drop_cols = ["__survived"] + [f"__ctx_{d}" for d in active_dims]
if not include_observability:
    drop_cols += [f"__t_{d}" for d in active_dims]
rel = rel.drop(*drop_cols)
```

The `__survived` flag and all `__ctx_*` literal columns are always dropped. The ternary columns (`__t_*`) are retained by default for observability but can be excluded via `include_observability=False`.

#### Diagram: Six-Step Evaluation Pipeline

<iframe src="../../sims/evaluation-pipeline-steps/main.html" width="100%" height="550px" scrolling="no"></iframe>
<details markdown="1">
<summary>Six-Step Evaluation Pipeline</summary>
Type: microsim
**sim-id:** evaluation-pipeline-steps<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive step-through visualization of the complete evaluation pipeline, showing the DataFrame state after each of the six steps.

**Controls:**
- Step forward/backward buttons
- Speed slider for auto-play mode
- Reset button
- Dimension count selector (2-4 dims)

**Visual elements:**
- DataFrame grid showing columns and rows
- New columns highlight in yellow on the step they are added
- Eliminated rows fade to red on filter step
- Rank numbers animate in on rank step
- Column headers color-coded: original (white), context (blue), ternary (green/gray/red), computed (purple)

**Behavior:** Starting with a sample 6-rule, 3-dimension table, each step adds/removes/transforms columns. Step 1: __ctx_ columns appear. Step 2: __t_ columns appear with color. Step 3: __survived and __specificity appear. Step 4: non-survivors fade out, rows reorder by specificity, __rank appears. Step 5: optional filters dim additional rows. Step 6: temporary columns disappear.

**Learning objective:** Sequence and explain each step of the single-pass evaluation pipeline (Bloom: Understand)
</details>

## Partial Dimension Evaluation

The `dimensions` parameter on `evaluate()` allows evaluating a subset of the configured dimensions. This is useful for several scenarios:

- **Debugging**: evaluate one dimension at a time to isolate which dimension is causing unexpected rule elimination
- **Progressive filtering**: evaluate high-selectivity dimensions first, then add more for fine-grained ranking
- **Performance profiling**: measure which dimensions contribute most to evaluation cost

When `dimensions` is provided, only those dimensions produce ternary columns. Dimensions not in the list are excluded from survival computation and specificity scoring entirely — they do not default to UNKNOWN, they simply do not participate.

```python
# Full evaluation
result_full = engine.evaluate(context)

# Partial — only evaluate region
result_region = engine.evaluate(context, dimensions=["region"])
# All rules survive except those that explicitly fail on region
# Specificity is 0 or 1 (only one dimension)

# Partial — evaluate region and tier
result_both = engine.evaluate(context, dimensions=["region", "tier"])
```

The engine validates that all requested dimension names exist in the compiled expressions dictionary, raising `KeyError` for unknown names. This prevents silent misconfiguration where a misspelled dimension name causes it to be silently ignored.

## Filter Interaction and Ordering

When `top_n` and `min_specificity` are both specified, they interact in a specific order that affects the final result. Understanding this ordering is critical for correct filter composition.

The pipeline applies filters in this sequence:

1. All survivors are ranked by specificity descending (ranks 1, 2, 3, ...)
2. `min_specificity` filter removes rows below the threshold
3. `top_n` filter takes the first N remaining rows

This means `min_specificity` takes priority over `top_n`. If 10 rules survive but only 3 have specificity >= 2, then `evaluate(context, min_specificity=2, top_n=5)` returns 3 rules (not 5), because the specificity filter runs first.

The reverse composition would also be possible (take top N first, then filter by specificity), but that would risk returning fewer than N results even when high-specificity rules exist at lower ranks. The current ordering ensures that specificity acts as a quality floor while top_n acts as a quantity ceiling.

#### Diagram: Filter Ordering Pipeline

<iframe src="../../sims/filter-ordering-pipeline/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Filter Ordering Pipeline</summary>
Type: microsim
**sim-id:** filter-ordering-pipeline<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive visualization demonstrating the filter ordering by showing how min_specificity and top_n interact on a sample result set.

**Controls:**
- Slider for min_specificity (0 to max dimensions)
- Slider for top_n (1 to rule count)
- Sample rule set selector (small/medium/large)

**Visual elements:**
- Table of ranked survivors with specificity scores
- Step 1 highlight: rows below min_specificity fade to gray
- Step 2 highlight: rows beyond top_n boundary marked with scissors icon
- Final result count displayed prominently
- Counter showing how many rules were eliminated by each filter

**Behavior:** Adjusting either slider immediately shows the filtering effect. Specificity filter always applies first, then top_n. Tooltips explain why a rule was eliminated and by which filter.

**Learning objective:** Predict the number of results when combining min_specificity and top_n filters (Bloom: Apply)
</details>

## Performance Characteristics

The evaluation pipeline has predictable performance characteristics because it follows a fixed sequence of column operations regardless of data content. The primary cost drivers are:

- **Rule count (n)**: linear — each step processes all n rows via vectorized column operations
- **Dimension count (d)**: linear — d expressions are applied, d ternary columns are created, and survival aggregation operates across d columns
- **Rule elimination**: the filter step removes non-survivors, so subsequent steps (sorting, ranking) operate on a smaller DataFrame. Rule sets with high selectivity (many rules eliminated) benefit from this reduction.

The engine performs no data-dependent branching within the pipeline. Whether 10% or 90% of rules survive, the cost of steps 1-3 (context binding, expression application, survival computation) is identical. Only steps 4-5 (sort, rank, optional filters) benefit from fewer survivors.

For large rule sets (100,000+ rules), the bottleneck is typically the sort step. Modern DataFrame backends use optimized parallel sorting algorithms, but the sort is inherently \( O(n \log n) \) — the only non-linear step in the pipeline.

## Complete Evaluation Example

To tie together all pipeline steps, here is a complete example from engine construction through result inspection:

```python
import polars as pl
from mountainash_rules import (
    ExpressionRulesEngine, DimensionsMetadata, Dimension, MatchStrategy
)

# Define rules
rules = pl.DataFrame({
    "rule_name": ["base", "au_specific", "au_premium", "catch_all"],
    "region": ["<NA>", "AU", "AU", "<NA>"],
    "tier": ["<NA>", "<NA>", "premium", "<NA>"],
    "discount": [0.0, 0.05, 0.15, 0.02],
})

# Define metadata
metadata = DimensionsMetadata(dimensions=[
    Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT),
    Dimension(dimension_name="tier", match_strategy=MatchStrategy.EXACT),
])

# Construct engine (compilation happens here)
engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)

# Evaluate
result = engine.evaluate({"region": "AU", "tier": "premium"})

# All four rules survive (none has FALSE for both dimensions)
# But specificity differs:
#   au_premium: region=1, tier=1 -> specificity=2, rank=1
#   au_specific: region=1, tier=0 -> specificity=1, rank=2
#   base: region=0, tier=0 -> specificity=0, rank=3
#   catch_all: region=0, tier=0 -> specificity=0, rank=4
```

The engine also exposes `evaluate_batch()` for evaluating many contexts at once (see [Chapter 8: Batch Evaluation](../08-batch-evaluation/)) and `explain()` for scoring every rule without filtering (see [Chapter 7: Expression Engine Results](../07-expression-engine-results/)). Their internals are covered in those chapters.

## Key Takeaways

- The **ExpressionRulesEngine** is the primary entry point — it accepts rules and metadata at construction time, compiles once, and evaluates many contexts against the same compiled expressions.
- Two **construction paths** exist: convenience (auto-compile from metadata) and advanced (provide pre-compiled expressions). They are mutually exclusive.
- **Single-pass evaluation** processes the entire rules DataFrame in one pipeline — no iteration, no recursion, and predictable performance regardless of rule complexity.
- **Context binding** broadcasts context values as literal columns, enabling column-to-column comparison with zero per-row overhead.
- **Dimension expression application** evaluates all dimensions simultaneously via a single `with_columns` call, producing ternary columns.
- **Survival computation** requires every dimension's ternary value to be >= 0, computed as a conjunction of per-dimension checks — any FALSE eliminates the rule, any UNKNOWN preserves it without claiming a match.
- **Specificity scoring** counts hard matches (TRUE = 1) per rule, providing a natural ranking criterion without explicit configuration.
- **Rank assignment** orders survivors according to the active hit policy (specificity descending by default) and assigns 1-based ranks, with optional post-filters for top_n and min_specificity.
