---
title: "Chapter 9: Accumulator Compiler"
description: "The AccumulatorCompiler and its two expression families — compatible and coalesce — that enable lattice construction from rule pairs."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 9: Accumulator Compiler

## Summary

This chapter introduces the AccumulatorCompiler, which produces two families of expressions for the lattice-building process: compatible expressions (determining whether two rules can coexist in a combination) and coalesce expressions (merging dimension values when building lattice nodes). You will learn how each match strategy translates into compatible and coalesce semantics, and how NA flags track sentinel propagation.

---

## The Accumulator Problem

The ExpressionRulesEngine (Chapter 5) answers a point query: "given this context, which rules match?" But in many business scenarios, you need to pre-compute how rules *interact* with each other — which rules can coexist, and what is the combined constraint when two or more compatible rules are merged.

Consider a pricing system where multiple discount rules might apply simultaneously. Rather than evaluating at query time whether rules R1, R2, and R3 are mutually compatible, you can pre-compute all valid combinations into a lattice structure. The AccumulatorCompiler provides the expression primitives that make this lattice construction possible.

The compiler produces two families of expressions for each CONSTRAINT dimension:

- **Compatible expressions**: determine whether two rules can coexist on a dimension (they do not contradict each other)
- **Coalesce expressions**: compute the merged value when two compatible rules are combined (using the strategy's merge operation)
<!-- concept:59 -->
## AccumulatorCompiler

The `AccumulatorCompiler` is a stateless compiler class (similar to the DimensionCompiler from Chapter 4) that produces expressions operating on *pairs* of rules rather than rule-context pairs. Its expressions reference two sets of columns:

- **LHS (left-hand side)**: the current combination, with columns prefixed by `co_` (coalesced values)
- **RHS (right-hand side)**: the candidate rule being considered for inclusion, with columns suffixed by `_rhs` (from a cross-join)

The compiler provides three methods:

```python
class AccumulatorCompiler:
    def compile_compatible(self, dim: Dimension) -> BaseExpressionAPI: ...
    def compile_coalesce(self, dim: Dimension) -> list[BaseExpressionAPI]: ...
    def compile_coalesce_na_flag(self, dim: Dimension) -> BaseExpressionAPI: ...
```

Unlike the DimensionCompiler, which handles all 12 match strategies, the AccumulatorCompiler supports strategies that have defined pairwise compatibility and coalesce semantics: EXACT, RANGE, GREATER_THAN, LESS_THAN, SET_MEMBERSHIP, and SET_EXCLUSION. String pattern strategies remain unsupported because their general intersection semantics are not defined here. Attempting to compile an unsupported strategy raises a `ValueError` with a descriptive message identifying the offending strategy.

Set dimensions therefore participate in the accumulator workflow. Set wildcards use the in-band sentinel list described in Chapter 4; they are not null values. Membership dimensions intersect compatible sets, while exclusion dimensions union excluded values. The filter engine and accumulator use the same set-wildcard helpers, so their wildcard interpretation remains aligned.

| Strategy | Compatible | Coalesce | Supported |
|----------|-----------|----------|-----------|
| EXACT | Values equal or either is sentinel | Take the non-sentinel value | Yes |
| RANGE | Intervals overlap or either is sentinel | Take the intersection interval | Yes |
| GREATER_THAN | Always compatible | Take the maximum (stricter bound) | Yes |
| LESS_THAN | Always compatible | Take the minimum (stricter bound) | Yes |
| SET_MEMBERSHIP | Either wildcard or sets intersect | Set intersection | Yes |
| SET_EXCLUSION | Always compatible | Set union | Yes |
| PREFIX, SUFFIX, CONTAINS, REGEX, CONTEXT_REGEX | N/A | N/A | No |


<!-- concept:60 -->
<!-- concept:61 -->
<!-- concept:63 -->
<!-- concept:64 -->
## Compatible Expression

A compatible expression evaluates to True when two rules can coexist in the same lattice combination for a given dimension. The fundamental question it answers is: "is there any context value that could satisfy both rules on this dimension simultaneously?"

If the answer is yes, the rules are compatible on that dimension. If the answer is no, they contradict each other and cannot be combined.

The key insight is that sentinel values (wildcards) are always compatible with any other value — a wildcard rule places no constraint, so it cannot contradict anything. The compatible expression only needs to check for actual conflicts between non-sentinel values.

#### Diagram: Compatibility Logic Flow

<iframe src="../../sims/compatibility-logic/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>Compatibility Logic Flow</summary>
Type: workflow
**sim-id:** compatibility-logic<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive decision tree showing the compatibility logic for EXACT and RANGE strategies, with configurable LHS/RHS values.

**Components:**
- Two input panels: LHS (coalesced value) and RHS (candidate rule value)
- Decision nodes: "Is LHS sentinel?", "Is RHS sentinel?", "Do values match/overlap?"
- Terminal nodes: "Compatible" (green) or "Incompatible" (red)
- Example traces highlighted along the path

**Interactions:** User enters LHS and RHS values (or selects sentinel). The diagram traces the path through the decision tree, highlighting active nodes. Strategy toggle switches between EXACT and RANGE logic. For RANGE, users can set min/max on both sides.
**Learning objective:** Determine whether two rule values are compatible on a given dimension (Bloom: Apply)
</details>

<!-- concept:62 -->
<!-- concept:65 -->
<!-- concept:66 -->
<!-- concept:67 -->
## Coalesce Expression

A coalesce expression computes the merged dimension value when two compatible rules are combined. The merged value represents the strategy-defined combination of both constraints — typically their intersection, as with ranges and membership sets, while exclusion sets combine by union.

Coalesce returns a list of expressions (not a single expression) because some strategies produce multiple output columns. EXACT produces one coalesced value column; RANGE produces two (coalesced min and coalesced max).

The coalesce logic follows a priority rule: non-sentinel values take precedence over sentinels. If one side is a sentinel (wildcard), the merged value comes from the other side. If both sides have non-sentinel values, the merge operation depends on the strategy:

- **EXACT**: both sides must have the same value (guaranteed by compatibility check), so either value is used
- **RANGE**: the merged interval is the intersection — take the max of the two mins and the min of the two maxes
- **Threshold (GT/LT)**: take the stricter bound — max for GREATER_THAN, min for LESS_THAN
- **SET_MEMBERSHIP**: the concrete lists are intersected to retain values satisfying both rules
- **SET_EXCLUSION**: the concrete lists are unioned to accumulate every excluded value

## Coalesce NA Flag

Alongside the coalesced values, the compiler produces NA flag columns that track whether the merged dimension is still a wildcard. An NA flag is 1 when *both* sides are sentinels (for RANGE, all four bounds must be sentinels), meaning neither rule constrains this dimension, and 0 otherwise.

```python
def compile_coalesce_na_flag(self, dim: Dimension) -> BaseExpressionAPI:
    if dim.match_strategy == MatchStrategy.RANGE:
        co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
        all_sentinel = (
            co_min_s.__and__(rhs_min_s)
            .__and__(co_max_s)
            .__and__(rhs_max_s)
        )
        return all_sentinel.cast(int).alias(f"co_{dim.dimension_name}_na")
    if dim.match_strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
        co_w, rhs_w = self._set_wild_checks(dim)
        field = dim.resolved_rule_field
        return co_w.__and__(rhs_w).cast(int).alias(f"co_{field}_na")
    co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
    field = dim.resolved_rule_field
    return co_sentinel.__and__(rhs_sentinel).cast(int).alias(f"co_{field}_na")
```

The NA flag is critical for the frontier filter (Chapter 10): it enables the engine to determine whether two lattice combinations have the same effective constraint pattern, which is necessary for identifying dominated combinations.

A dimension's NA flag can only be 1 if every rule contributing to the combination has a sentinel for that dimension. The moment any rule with a non-sentinel value is included, the NA flag becomes 0 (the combination now constrains this dimension).

## Compatible Exact

For the EXACT strategy, two rules are compatible on a dimension when at least one of three conditions holds:

1. The LHS (coalesced) value is a sentinel (wildcard)
2. The RHS (candidate) value is a sentinel (wildcard)
3. Both values are non-sentinel and equal

The expression implements this as an OR of three checks:

```python
def _compatible_exact(self, dim: Dimension) -> BaseExpressionAPI:
    co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
    field = dim.resolved_rule_field
    values_match = ma.col(f"co_{field}").eq(ma.col(f"{field}_rhs"))
    return co_sentinel.__or__(rhs_sentinel).__or__(values_match)
```

The intuition is straightforward: if either side does not care (sentinel), they cannot conflict. If both sides care, they must agree on the value.

## Compatible Range

For the RANGE strategy, two rules are compatible when their effective intervals overlap. A sentinel minimum acts as negative infinity and a sentinel maximum as positive infinity; each bound is checked independently. The comparison also honors the dimension's endpoint-inclusivity flags, so touching endpoints overlap only when both relevant sides are inclusive.

```python
def _compatible_range(self, dim: Dimension) -> BaseExpressionAPI:
    """True when the two effective intervals overlap.

    A sentinel min is -inf, a sentinel max is +inf, each bound
    independently. Touching endpoints overlap iff both the min and the
    max side are inclusive (covers all four flag combinations).
    """
    co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
    co_min = ma.col(f"co_{dim.range_min_field}")
    co_max = ma.col(f"co_{dim.range_max_field}")
    rhs_min = ma.col(f"{dim.range_min_field}_rhs")
    rhs_max = ma.col(f"{dim.range_max_field}_rhs")

    touch_overlaps = dim.range_min_inclusive and dim.range_max_inclusive
    if touch_overlaps:
        low = co_min.le(rhs_max)
        high = co_max.ge(rhs_min)
    else:
        low = co_min.lt(rhs_max)
        high = co_max.gt(rhs_min)

    low_ok = co_min_s.__or__(rhs_max_s).__or__(low)
    high_ok = co_max_s.__or__(rhs_min_s).__or__(high)
    return low_ok.__and__(high_ok)
```

If either effective interval has an unbounded sentinel on the relevant side, that side of the overlap test succeeds automatically. Otherwise, the comparison enforces the configured inclusive or exclusive endpoint behavior.

| LHS Range | RHS Range | Compatible? | Reason |
|-----------|-----------|-------------|--------|
| [10, 50] | [30, 70] | Yes | Overlap at [30, 50] |
| [10, 30] | [40, 60] | No | No overlap |
| [sentinel] | [40, 60] | Yes | LHS is wildcard |
| [10, 50] | [sentinel] | Yes | RHS is wildcard |

<!-- concept:120 -->
## Set Membership Compatible

For a `SET_MEMBERSHIP` dimension, `_compatible_set_membership` treats two rule lists as compatible when either list is a wildcard or the lists have a non-empty intersection. The wildcard is the in-band `[sentinel]` list handled by the shared helpers; Chapter 4 explains its representation and normalization mechanics.

```python
def _compatible_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
    co_w, rhs_w = self._set_wild_checks(dim)
    field = dim.resolved_rule_field
    intersection_nonempty = (
        ma.col(f"co_{field}")
        .list.set_intersection(ma.col(f"{field}_rhs"))
        .list.len()
        .gt(ma.lit(0))
    )
    return co_w.__or__(rhs_w).__or__(intersection_nonempty)
```

The first two terms make any wildcard pair compatible with a constrained list. When both lists are concrete, `list.set_intersection(...).list.len().gt(ma.lit(0))` is true exactly when at least one member can satisfy both rules. Empty intersection means the candidate rule cannot join this combination. `SET_EXCLUSION` uses a different compatibility rule: its compiler dispatch returns `ma.lit(True)` because overlapping exclusions do not contradict one another.

<!-- concept:121 -->
## Set Membership Coalesce

The `_coalesce_set` helper merges compatible set values, with the operation selected by the strategy. `compile_coalesce` passes `"intersection"` for `SET_MEMBERSHIP` and `"union"` for `SET_EXCLUSION`:

```python
case MatchStrategy.SET_MEMBERSHIP:
    return self._coalesce_set(dim, "intersection")
case MatchStrategy.SET_EXCLUSION:
    return self._coalesce_set(dim, "union")
```

For either strategy, wildcard handling follows the same four cases as the implementation: two wildcards remain the sentinel list, a wildcard on the LHS yields the RHS list, a wildcard on the RHS yields the LHS list, and two concrete lists use the selected operation. Thus membership coalescing narrows allowed values through intersection, while exclusion coalescing accumulates forbidden values through union.

```python
def _coalesce_set(self, dim: Dimension, op: str) -> list[BaseExpressionAPI]:
    co_w, rhs_w = self._set_wild_checks(dim)
    field = dim.resolved_rule_field
    co = ma.col(f"co_{field}")
    rhs = ma.col(f"{field}_rhs")
    combined = (
        co.list.set_intersection(rhs) if op == "intersection" else co.list.set_union(rhs)
    )
    new_val = (
        ma.when(co_w.__and__(rhs_w)).then(sentinel_list_expr(dim))
        .when(co_w).then(rhs)
        .when(rhs_w).then(co)
        .otherwise(canonicalize_set_expr(combined))
        .alias(f"co_{field}")
    )
    return [new_val]
```

`canonicalize_set_expr` sorts and deduplicates concrete results, giving equivalent lists a stable representation for later lattice comparisons and fingerprints. The accumulator's set wildcard sentinel remains an in-band list value rather than null.

## Coalesce Exact

When two compatible EXACT values are merged, the coalesce logic selects the non-sentinel value. If both are non-sentinel, they must be equal (guaranteed by the compatibility check), so either value works. If both are sentinel, the result remains sentinel.

The implementation uses `ma.coalesce` (which takes the first non-null value) after mapping sentinels to null:

```python
def _coalesce_exact(self, dim: Dimension) -> list[BaseExpressionAPI]:
    co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
    field = dim.resolved_rule_field
    # Map sentinels to None so coalesce skips them
    co_hard = ma.when(co_sentinel).then(None).otherwise(ma.col(f"co_{field}"))
    rhs_hard = ma.when(rhs_sentinel).then(None).otherwise(ma.col(f"{field}_rhs"))
    # Take first non-null, falling back to original (preserves sentinel if both are)
    return [ma.coalesce(co_hard, rhs_hard, ma.col(f"co_{field}")).alias(f"co_{field}")]
```

The three-argument coalesce ensures that if both sides are sentinels (both mapped to None), the fallback to `co_{field}` preserves the original sentinel value. This maintains the wildcard state in the combination.

## Coalesce Range

Range coalescing computes the intersection of two intervals. The merged minimum is the maximum of the two minimums (the tighter lower bound), and the merged maximum is the minimum of the two maximums (the tighter upper bound).

The coalesce handles four cases per bound:

1. Both sides are sentinel: result is sentinel (wildcard bound)
2. Only LHS is sentinel: take RHS value
3. Only RHS is sentinel: take LHS value
4. Neither is sentinel: take the tighter bound (`greatest` for min, `least` for max)

```python
def _coalesce_range(self, dim: Dimension) -> list[BaseExpressionAPI]:
    co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
    sentinel = unknown_sentinel_for(dim.data_type)

    new_min = (
        ma.when(co_min_s.__and__(rhs_min_s))
          .then(ma.lit(sentinel))
        .when(co_min_s)
          .then(ma.col(f"{dim.range_min_field}_rhs"))
        .when(rhs_min_s)
          .then(ma.col(f"co_{dim.range_min_field}"))
        .otherwise(
            ma.greatest(
                ma.col(f"co_{dim.range_min_field}"),
                ma.col(f"{dim.range_min_field}_rhs"),
            )
        )
        .alias(f"co_{dim.range_min_field}")
    )

    new_max = (
        ma.when(co_max_s.__and__(rhs_max_s))
          .then(ma.lit(sentinel))
        .when(co_max_s)
          .then(ma.col(f"{dim.range_max_field}_rhs"))
        .when(rhs_max_s)
          .then(ma.col(f"co_{dim.range_max_field}"))
        .otherwise(
            ma.least(
                ma.col(f"co_{dim.range_max_field}"),
                ma.col(f"{dim.range_max_field}_rhs"),
            )
        )
        .alias(f"co_{dim.range_max_field}")
    )

    return [new_min, new_max]
```

The same pattern applies to the maximum bound, but using `ma.least` for the "both constrained" case (taking the smaller of the two maxes tightens the upper bound).

#### Diagram: Range Coalescing on a Number Line

<iframe src="../../sims/range-coalesce-numberline/main.html" width="100%" height="400px" scrolling="no"></iframe>
<details markdown="1">
<summary>Range Coalescing on a Number Line</summary>
Type: microsim
**sim-id:** range-coalesce-numberline<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive number line showing how two intervals are coalesced into their intersection.

**Controls:**
- Two draggable intervals (LHS and RHS) with movable endpoints
- Sentinel toggle for each interval (makes it a wildcard)
- Reset button

**Visual elements:**
- Number line from 0 to 100
- LHS interval in blue, RHS interval in green
- Coalesced result in purple (the overlap region)
- Sentinel intervals shown as dashed lines spanning the full range
- Incompatibility indicator (red X) when intervals do not overlap

**Behavior:** As the user drags interval endpoints, the coalesced result updates in real time. When intervals overlap, the purple region shows the intersection. When they do not overlap and neither is sentinel, a red "INCOMPATIBLE" indicator appears. Toggling sentinel makes that interval cover the full range.

**Learning objective:** Compute the intersection of two constraint intervals including sentinel/wildcard handling (Bloom: Apply)
</details>

## Coalesce Threshold

GREATER_THAN and LESS_THAN strategies have the simplest coalesce logic because threshold rules are always compatible (the `_compatible_threshold` method returns `ma.lit(True)` unconditionally). Two thresholds can always be combined — the result is simply the stricter bound.

For GREATER_THAN, the coalesced value is the maximum of the two thresholds (a higher threshold is stricter). For LESS_THAN, it is the minimum (a lower threshold is stricter).

```python
def _coalesce_threshold(self, dim: Dimension, combine_fn) -> list[BaseExpressionAPI]:
    co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
    field = dim.resolved_rule_field
    sentinel = unknown_sentinel_for(dim.data_type)
    new_val = (
        ma.when(co_sentinel.__and__(rhs_sentinel))
          .then(ma.lit(sentinel))
        .when(co_sentinel)
          .then(ma.col(f"{field}_rhs"))
        .when(rhs_sentinel)
          .then(ma.col(f"co_{field}"))
        .otherwise(
            combine_fn(ma.col(f"co_{field}"), ma.col(f"{field}_rhs"))
        )
        .alias(f"co_{field}")
    )
    return [new_val]
```

The `combine_fn` parameter is `ma.greatest` for GREATER_THAN and `ma.least` for LESS_THAN. This elegant parameterization reuses the same four-case logic for both threshold directions.

| Strategy | LHS Threshold | RHS Threshold | Coalesced | Why |
|----------|--------------|--------------|-----------|-----|
| GREATER_THAN | 500 | 700 | 700 | Must exceed both -> take max |
| GREATER_THAN | sentinel | 500 | 500 | Only RHS constrains |
| LESS_THAN | 100 | 80 | 80 | Must be below both -> take min |
| LESS_THAN | sentinel | sentinel | sentinel | Neither constrains |

## The LHS/RHS Column Convention

All accumulator expressions reference columns using a naming convention that reflects the cross-join structure of the expansion step. Understanding this convention is essential for reading the compiled expressions correctly.

During level expansion (Chapter 10), the current-level combinations are cross-joined with the candidate rules. The join adds a `_rhs` suffix to all columns from the candidate side. The current-level combinations already have `co_` prefixed columns from the previous coalesce step. This produces two parallel column namespaces:

| Source | Column Pattern | Example |
|--------|---------------|---------|
| Current combination (LHS) | `co_{rule_field}` | `co_region`, `co_age_min` |
| Candidate rule (RHS) | `{rule_field}_rhs` | `region_rhs`, `age_min_rhs` |

The compatible expressions compare `co_*` against `*_rhs` to check for conflicts. The coalesce expressions combine them to produce updated `co_*` values. The NA flag expressions check both sides for sentinels and write to `co_*_na` columns.

This convention means that each dimension actually involves up to four columns in the cross-joined DataFrame: `co_{field}`, `{field}_rhs`, `co_{field}_na`, and (for RANGE) the corresponding min/max pairs on both sides. For a rule set with 5 constraint dimensions, the cross-joined DataFrame can have 20+ accumulator-specific columns.

## Sentinel Detection Helper

The `_sentinel_checks` method is a shared utility used by multiple compile methods. It returns a pair of Boolean expressions that detect whether the LHS and RHS values are sentinels:

```python
def _sentinel_checks(
    self, dim: Dimension,
) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
    field = dim.resolved_rule_field
    sentinel = unknown_sentinel_for(dim.data_type)
    co_is_sentinel = ma.col(f"co_{field}").eq(ma.lit(sentinel))
    rhs_is_sentinel = ma.col(f"{field}_rhs").eq(ma.lit(sentinel))
    return co_is_sentinel, rhs_is_sentinel
```

Note that only the `UNKNOWN` sentinel (not `NOT_SET`) is checked. The current implementation obtains the typed value with `unknown_sentinel_for(dim.data_type)`: the accumulator operates on rule data, which uses the UNKNOWN wildcard, while `NOT_SET` is specific to context values and is not recognised here.

For RANGE dimensions, a separate helper `_range_sentinel_checks` returns four expressions — one for each bound on each side (co_min, co_max, rhs_min, rhs_max).

#### Diagram: Accumulator Column Flow

<iframe src="../../sims/accumulator-column-flow/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>Accumulator Column Flow</summary>
Type: workflow
**sim-id:** accumulator-column-flow<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Animated data flow showing how LHS (co_*) and RHS (*_rhs) columns are consumed by compatible, coalesce, and NA flag expressions during one expansion step.

**Components:**
- Left: current combination row with co_* columns
- Center-top: candidate rule row with original columns (shown with _rhs suffix after join)
- Center: three expression blocks (compatible, coalesce, NA flag) with input arrows from both sides
- Right: updated combination row with new co_* values

**Interactions:** Click each expression block to expand and see the when/then/otherwise logic. Toggle between EXACT and RANGE mode to see different column sets. Editable input values on LHS and RHS to see expression results change dynamically.

**Learning objective:** Trace how LHS and RHS column values flow through the three expression families during one expansion step (Bloom: Analyze)
</details>

## Putting It Together

The AccumulatorCompiler is used by the AccumulatorEngine (Chapter 10) during lattice construction. For each CONSTRAINT dimension, the engine pre-compiles all three expression types at initialization:

```python
# In AccumulatorEngine.__init__:
self._compatible_exprs = {
    dim.dimension_name: self._compiler.compile_compatible(dim)
    for dim in self._constraint_dims
}
self._coalesce_exprs = {
    dim.dimension_name: self._compiler.compile_coalesce(dim)
    for dim in self._constraint_dims
}
self._coalesce_na_exprs = {
    dim.dimension_name: self._compiler.compile_coalesce_na_flag(dim)
    for dim in self._constraint_dims
}
```

During each expansion step, all compatible expressions are combined with AND to determine if a candidate rule can join the combination, all coalesce expressions are applied to compute the merged constraint values, and all NA flag expressions update the wildcard tracking columns.

## Key Takeaways

- The **AccumulatorCompiler** produces expressions for rule-pair analysis (compatibility and merging), complementing the DimensionCompiler's rule-context expressions.
- **Compatible expressions** answer "can these two rules coexist?" — sentinels are always compatible, non-sentinel values must not contradict.
- **Coalesce expressions** apply strategy-specific merges when compatible rules combine — intersections for ranges and membership sets, union for exclusion sets, and stricter bounds for thresholds.
- **NA flags** track whether a dimension remains unconstrained (all contributing rules are wildcards) in a lattice combination.
- **Compatible Exact** requires value equality or sentinel on either side; **Compatible Range** requires interval overlap or sentinel bounds.
- **Coalesce Exact** uses `ma.coalesce` with sentinel-to-null mapping to select the non-wildcard value.
- **Coalesce Range** takes `greatest(mins)` for the lower bound and `least(maxes)` for the upper bound, producing the interval intersection.
- **Coalesce Threshold** is always compatible and takes the stricter bound (max for GT, min for LT) — the simplest coalesce logic.
- **Set dimensions are accumulator-compatible**: membership compatibility requires a wildcard or non-empty intersection, membership coalescing intersects sets, and exclusion coalescing unions them.
- String pattern strategies (PREFIX, SUFFIX, CONTAINS, REGEX, CONTEXT_REGEX) remain unsupported by the accumulator compiler.
