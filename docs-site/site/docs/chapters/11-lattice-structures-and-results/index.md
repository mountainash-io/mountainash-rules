---
title: "Chapter 11: Lattice Structures and Results"
description: "The Lattice data structure, persisted snapshots, aggregate results, and ternary routing across partitioned rule lattices."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 11: Lattice Structures and Results

## Summary

This chapter covers the objects produced by the accumulator engine and the routing layer that serves them. You will learn how a `Lattice` wraps maximal rule combinations, how coalesced values, NA flags, aggregate columns, prime products, and depth describe each combination, and how `AccumulatorResult` exposes evaluation output. The chapter also introduces the four aggregate operations, snapshot persistence with `Lattice.save()` and `Lattice.load()`, and `LatticeIndex`, which routes single contexts or batches across `CONTEXT_KEY` partitions using the same ternary and specificity semantics as ordinary rule evaluation.

---

The accumulator build phase turns a rule table into a reusable result structure. Instead of asking which individual rule survives for every request, it first finds compatible combinations of rules, coalesces their constraints, and accumulates their outputs. Applying a context to that precomputed structure is then a filter operation over the lattice.

A **lattice** is the object that keeps this result together with the metadata needed to interpret it. A lattice can be used immediately, saved as a portable snapshot, loaded by a serving process, or selected through a routing index when a rule set is split into context-key partitions.

<!-- concept:77 -->
## The Lattice Class

A `Lattice` is constructed with four arguments: a DataFrame containing combinations, a `DimensionsMetadata` object describing the dimensions, a list of `Aggregate` models, and a partition-key dictionary (or `None` for an unpartitioned lattice). The public package exports these classes from `mountainash_rules`, so callers do not need to depend on private module paths.

```python
from mountainash_rules import Lattice

lattice = Lattice(
    dataframe=combination_frame,
    metadata=metadata,
    aggregates=aggregates,
    partition_key=None,
)
```

The class is intentionally a thin wrapper. Its `combinations` property returns the wrapped frame, `count` reports its row count, `partition_key` identifies the context-key slice, `metadata` returns the dimension metadata, and `aggregates` returns the aggregate definitions. These properties preserve the schema context that the apply phase needs; the accumulator engine remains responsible for building and evaluating combinations.

`combinations` should be treated as read-only by callers. Apply-phase filter engines are cached against lattice identity, so mutating the returned frame can make a cached engine disagree with the data it is meant to evaluate.

#### Diagram: Lattice Structure Overview

<iframe src="../../sims/lattice-structure-overview/main.html" width="100%" height="500px" scrolling="no"></iframe>

<details markdown="1">
<summary>Lattice Structure Overview</summary>
Type: class diagram with data preview | **sim-id:** lattice-structure-overview<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Identify the Lattice object's frame, metadata, aggregate definitions, and partition key (Bloom: Understand).

**Interactions:** Click each property node to see its type, description, and example value. Hover over the combinations node to inspect sample coalesced, NA-flag, aggregate, prime-product, and level columns.
</details>

<!-- concept:124 -->
## Lattice Save Method

A built lattice is often expensive to create because level expansion and frontier filtering examine many compatible combinations. `Lattice.save(dir_path)` persists that result so a later process can use it without rebuilding. The method accepts a string or `pathlib.Path`, creates the destination directory, writes `lattice.parquet`, writes `manifest.yaml`, and returns the destination `Path`.

The manifest records the serialized dimensions metadata, every aggregate model, and the partition key. The snapshot format is a strict superset of `mountainash-rules-babel`'s `LatticeManifest`, so tooling that understands the babel manifest can read the shared fields. The Parquet file carries the actual combinations frame, including computed columns produced during accumulation.

```python
from pathlib import Path

snapshot_dir = lattice.save(Path("snapshots/insurance"))
print(snapshot_dir)  # snapshots/insurance
```

Saving is a build-time operation, not an apply-time transformation. A useful deployment workflow is **build offline, save, then serve applications from the saved snapshot**. This separates the expensive and potentially large build job from the request-serving process while retaining the exact aggregate and provenance columns needed for results.

<!-- concept:125 -->
## Lattice Load Method

`Lattice.load(dir_path)` reverses `save()`: it reads `manifest.yaml`, validates the serialized dimensions and aggregate models, reads `lattice.parquet`, and constructs a new `Lattice`. The computed columns are not recomputed or discarded. In particular, `__agg_*`, `__prime_product`, `__level`, coalesced values, and coalesced NA flags survive the round trip.

```python
from mountainash_rules import Lattice

loaded = Lattice.load("snapshots/insurance")
print(loaded.count)
print(loaded.is_composed)
```

The loader raises `FileNotFoundError` when the snapshot directory has no `manifest.yaml`. The current implementation uses a direct `polars.read_parquet` call for the snapshot read. This is the package's documented backend-purity `# allow:` exemption: backend-agnostic `mountainash` parquet file reading is not yet available, so the native read is isolated and explicitly tagged rather than silently treated as a general engine dependency.

The persistence workflow is therefore concrete: construct `AccumulatorEngine` and build lattices in an offline job, call `save()` for each partition, and load those directories in the service that calls `apply()` or an index. Loading preserves whether the stored frame is a composed build result; it does not turn a flat imported frame into a newly built lattice.

<!-- concept:126 -->
## Lattice Is Composed

The `is_composed` property distinguishes a lattice produced by `AccumulatorEngine.build()` from a flat or imported lattice. It returns `True` when the combinations frame contains the `__prime_product` tracking column. The build phase creates that column while assigning each source rule a prime and multiplying those primes for a combination. Importers commonly strip tracking columns, so a flat/imported lattice normally returns `False`.

```python
built = engine.build(rules, partition_key={"product_type": "insurance"})
print(built.is_composed)  # True when build output has __prime_product

restored = Lattice.load("snapshots/insurance")
print(restored.is_composed)  # Preserves the saved frame's identity
```

This property is a structural signal, not a claim that the lattice can be merged with another lattice. It answers whether this frame carries the accumulator's composition provenance. A `LatticeIndex` can route across multiple lattices, but routing is not composition: it chooses one lattice for a context rather than merging their combinations.

<!-- concept:78 -->
## Lattice Combinations

The **lattice combinations** are the rows of the wrapped DataFrame. Each row represents one compatible combination of original rules that survived the frontier filter. A depth-1 row corresponds to one source rule; a depth-2 row combines two compatible rules; deeper rows contain progressively larger compatible sets.

Each combination carries several categories of columns:

- **Coalesced values** store the merged constraint for each dimension.
- **Coalesced NA flags** use names such as `co_region_na` and indicate that a dimension remains unconstrained.
- **Aggregate values** use the `__agg_` prefix, such as `__agg_premium`.
- **Prime products** use `__prime_product` to encode source-rule provenance.
- **Levels** use `__level` to record combination depth.

The exact field used for a coalesced value follows the dimension's resolved rule field. RANGE dimensions have coalesced minimum and maximum fields; other dimensions have a coalesced rule field plus its `_na` flag. During apply, the engine remaps constraint metadata to these coalesced fields and evaluates the caller's context against them.

| Column category | Example | Meaning |
|---|---|---|
| Coalesced value | `co_region` | Merged constraint value |
| Coalesced NA flag | `co_region_na` | `1` when the combination leaves the dimension unconstrained |
| Aggregate | `__agg_premium` | Value folded across contributing rules |
| Prime product | `__prime_product` | Source-rule provenance identity |
| Level | `__level` | Number of contributing rules |

The combinations frame is the main artifact of the accumulator pipeline. It is built once and can then serve many apply calls.

<!-- concept:79 -->
## Lattice Partition Key

A **partition key** identifies the slice of the rule space covered by one lattice. It is a dictionary whose names are `CONTEXT_KEY` dimensions. Context-key dimensions segment rules before combination building; they are not ordinary constraint dimensions that can be coalesced with one another.

For example, a `product_type` context key can produce one lattice with `{"product_type": "insurance"}` and another with `{"product_type": "lending"}`. The insurance rules cannot combine with lending rules because they are built in separate partitions. A lattice without context-key dimensions uses `partition_key=None`.

```python
lattice = engine.build(
    rules,
    partition_key={"product_type": "insurance"},
)
print(lattice.partition_key)
# {'product_type': 'insurance'}
```

Partitioning reduces the number of combinations each apply operation must inspect and prevents unrelated products, tenants, or policy domains from entering the same lattice. The partition key is also the input to the routing mechanisms introduced later in this chapter.

<!-- concept:80 -->
## Coalesced Columns

**Coalesced columns** hold the most specific compatible constraint obtained by merging source rules. For an EXACT dimension, equal values remain that value; a constrained value wins over a wildcard. For a RANGE dimension, compatible bounds are intersected: the larger lower bound and smaller upper bound describe the tighter interval. Set dimensions use the accumulator compiler's set intersection or union semantics for membership and exclusion, respectively.

Coalesced fields are prefixed with `co_`. If a dimension resolves to the rule field `region`, its coalesced value is `co_region`. A range with resolved fields `age_min` and `age_max` uses `co_age_min` and `co_age_max`. The corresponding unconstrained indicators are named `co_region_na` or `co_age_na`.

The apply phase constructs metadata that points constraint dimensions at these `co_` fields while retaining the original context field names. A caller still supplies `{"region": "NSW"}`; the engine compares that context value against `co_region` internally.

#### Diagram: Coalesced Column Derivation

<iframe src="../../sims/coalesced-column-derivation/main.html" width="100%" height="500px" scrolling="no"></iframe>

<details markdown="1">
<summary>Coalesced Column Derivation</summary>
Type: data flow diagram | **sim-id:** coalesced-column-derivation<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Apply EXACT and RANGE coalescing rules to two compatible source rows (Bloom: Apply).

**Interactions:** Select two sample rules and inspect their merged values step by step. Toggle EXACT, RANGE, and set dimensions to compare value selection, interval intersection, and set combination.
</details>

<!-- concept:81 -->
## Coalesced NA Flag Columns

A coalesced NA flag is an integer flag named `co_<field>_na`. It is `1` when every contributing source rule leaves that dimension unconstrained, and `0` as soon as any contributing rule constrains it. The flag lets the apply phase recognize a wildcard combination without treating a null value as a portable sentinel.

For example, a row with `co_region="NSW"` and `co_tier_na=1` is constrained to NSW but unconstrained on tier. A row with `co_region_na=1` and `co_tier="gold"` is unconstrained on region and constrained to gold. These flags also make a lattice easier to inspect: many `1` flags indicate a broad combination, while few flags indicate a narrow one.

```text
co_region | co_region_na | co_tier | co_tier_na | __agg_premium | __level
----------|--------------|---------|------------|---------------|--------
NSW       | 0            | <NA>    | 1          | 120.0         | 2
a wildcard | 1          | gold    | 0          | 85.0          | 1
```

The displayed `<NA>` is the typed unknown sentinel, not a database null. The `_na` flag is the reliable indication that the dimension was left unconstrained.

<!-- concept:82 -->
## Combination Depth

The `__level` column records **combination depth**: the number of source rules contributing to a row. The accumulator starts with singleton anchor combinations and expands compatible rows one level at a time. A level of 1 therefore represents one rule, level 2 represents two rules, and so on.

Depth often correlates with specificity because a deeper combination contains more constraints, but the values should not be treated as interchangeable concepts. Specificity is computed by ternary evaluation during apply; `__level` is a build-time provenance measure. The `depths` result accessor exposes the latter so callers can implement their own reporting or ranking logic.

#### Diagram: Lattice Depth Hierarchy

<iframe src="../../sims/lattice-depth-hierarchy/main.html" width="100%" height="450px" scrolling="no"></iframe>

<details markdown="1">
<summary>Lattice Depth Hierarchy</summary>
Type: hierarchical tree | **sim-id:** lattice-depth-hierarchy<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Analyze how singleton, pair, and deeper combinations relate to their contributing rules (Bloom: Analyze).

**Interactions:** Click a combination to inspect its prime-product provenance and level. Toggle pruned combinations and use color intensity to compare aggregate magnitudes.
</details>

<!-- concept:83 -->
## The AccumulatorResult Class

`AccumulatorResult` extends `RuleResult` for an apply against a lattice. It wraps the surviving combinations and adds the accumulator's aggregate definitions and source lattice. Its constructor receives the matching DataFrame, active dimension names, the list of `Aggregate` models, and the lattice that supplied the combinations.

```python
result = engine.apply(lattice, context)

print(result.survivors)       # matching combination rows
print(result.best_match)      # inherited RuleResult selection
print(result.count)           # number of matches
print(result.active_dimensions)
```

The result inherits `RuleResult` behavior such as `survivors`, `best_match`, `count`, `active_dimensions`, and `explain()`. Accumulator-specific accessors make the computed output easier to consume without requiring callers to know every internal column name.

<!-- concept:84 -->
## Accumulated Aggregates

The `accumulated(aggregate_name)` method selects the `__agg_<aggregate_name>` column for matching combinations and returns the collected frame. The name is the `column_name` configured in the corresponding `Aggregate` model.

```python
premiums = result.accumulated("premium")
print(premiums)  # values from __agg_premium
```

There can be several aggregate columns in one lattice. For example, `accumulated("premium")` and `accumulated("discount")` read separate computed columns from the same result. The values represent the configured fold across the rules in each combination, not necessarily a sum; the supported operations are described below.

<!-- concept:85 -->
## Provenance Accessor

The `provenance` property returns the `__prime_product` column. During build, each source rule receives a unique prime. Multiplying the primes for a combination gives one integer whose factorization identifies the contributing rules.

```python
primes = result.provenance
# Example values: 30, 6, 5
# 30 = 2 * 3 * 5; 6 = 2 * 3; 5 = 5
```

This deterministic identity supports audit questions such as “which source rules produced this aggregate?” The checked multiplication used for combination identity raises `LatticeWidthExceededError` when the product would exceed the supported int64 range. That identity guard is separate from aggregate-value arithmetic: aggregate overflow is backend-defined and unguarded.

<!-- concept:86 -->
## Depths Accessor

The `depths` property returns the `__level` column from the matching combinations. It reports how many source rules contributed to each result row.

```python
depths = result.depths
# Example: [3, 2, 1]
```

Depths are useful for diagnostics and custom policies. A service may want to display whether an output came from a singleton rule or a deeper intersection, while an analyst may compare depth with specificity to understand why several combinations survived.

<!-- concept:87 -->
## The Aggregate Model

`Aggregate` is a Pydantic model with a required `column_name` and an `operation` that defaults to `AggregateOp.SUM`. It describes what the accumulator folds; it does not perform the fold itself. The engine reads these models during build and emits a `__agg_` column for each one.

```python
from mountainash_rules import Aggregate, AggregateOp

premium_total = Aggregate(
    column_name="premium",
    operation=AggregateOp.SUM,
)
```

The operation is represented by the `AggregateOp` string enum. Pydantic accepts the enum values (`sum`, `min`, `max`, and `product`) and validates the model shape before build. Multiple aggregates can be configured together, and each operates independently over its own source column.

<!-- concept:127 -->
## Aggregate Min, Max, and Product

The accumulator now supports four commutative and associative operations: `sum`, `min`, `max`, and `product`. The three operations beyond sum are useful when the business meaning is not “add every contribution.” For example, if rules contribute possible discounts and the desired result is the strongest discount available in a combination, configure `max` rather than summing discounts.

```python
aggregates = [
    Aggregate(column_name="premium", operation=AggregateOp.SUM),
    Aggregate(column_name="discount", operation=AggregateOp.MAX),
    Aggregate(column_name="risk_factor", operation=AggregateOp.PRODUCT),
]
engine = AccumulatorEngine(
    dimension_metadata=metadata,
    aggregates=aggregates,
)
```

`min` chooses the smallest orderable value, `max` chooses the largest orderable value, and `product` multiplies numeric values. Their commutativity and associativity mean the accumulated value does not depend on source-rule order within a combination. `sum` and `product` require numeric columns; `min` and `max` require orderable columns such as numeric or temporal values. The backend reports an error for incompatible dtypes rather than the model performing a separate dtype check.

Aggregate values have an important limitation: overflow is backend-defined and unguarded. The engine guards only the int64 `__prime_product` combination identity. Product aggregates reach a numeric ceiling faster than sums, so callers must select suitable backend dtypes and monitor the resulting values.

| Operation | Example meaning | Value requirement |
|---|---|---|
| `sum` | Total premium across contributing rules | Numeric |
| `min` | Lowest threshold or earliest orderable bound | Numeric or temporal/orderable |
| `max` | Largest discount available | Numeric or temporal/orderable |
| `product` | Combined multiplicative risk factor | Numeric |

<!-- concept:88 -->
## Partition Key Filtering

**Partition key filtering** selects the rows belonging to one context-key partition before the accumulator builds combinations. `AccumulatorEngine.build()` filters each context-key rule field against the supplied `partition_key`; `build_all()` discovers the unique combinations first and calls `build()` once for each one.

Suppose the rule table contains `product_type` values `insurance` and `lending`. Filtering produces independent inputs:

- `{"product_type": "insurance"}` contains only insurance rows.
- `{"product_type": "lending"}` contains only lending rows.

The resulting lattices therefore cannot combine rules across those product types. This is a build-time separation, not an apply-time preference. If there are no `CONTEXT_KEY` dimensions, the engine builds one unpartitioned lattice.

<!-- concept:89 -->
## Build All Partitions

`AccumulatorEngine.build_all(rules)` is the multi-partition entry point. It returns a list of `Lattice` objects, one for every unique combination of `CONTEXT_KEY` values found in the rule frame. With no context-key dimensions it returns a one-element list containing `build(rules)`.

```python
from mountainash_rules import AccumulatorEngine, Aggregate

engine = AccumulatorEngine(
    dimension_metadata=metadata,
    aggregates=[Aggregate(column_name="premium")],
)
lattices = engine.build_all(rules)
print(len(lattices))
```

The method reads the resolved rule fields for context-key dimensions, obtains unique key rows, converts each row into a partition-key dictionary keyed by dimension name, and builds that partition. Building all partitions up front amortizes the expensive expansion and frontier-filter work across later applications. The returned list can be passed to `apply_auto()` or, for reusable routing, to `index()`.

<!-- concept:90 -->
## Apply Auto Selection

`apply_auto(lattices, context, dimensions=None)` is the simple convenience path for applying a context to a list returned by `build_all()`. It extracts and normalizes the context-key tuple, chooses the exact dictionary entry when one exists, and otherwise routes through the same ternary matching logic used by the index before delegating to `apply()`.

```python
context = {
    "product_type": "insurance",
    "region": "NSW",
    "tier": "gold",
}
result = engine.apply_auto(lattices, context)
print(result.accumulated("premium"))
```

`apply_auto()` constructs an index with `validate=False` for the call. That makes it convenient for a small number of applications, but it skips the load-time exhaustive ambiguity check and repeats index construction. For a serving process that will route many contexts, build a `LatticeIndex` once and retain it.

<!-- concept:128 -->
## The LatticeIndex Router

`LatticeIndex` is the reusable ternary-partition-routing layer over several lattices. Create it through `AccumulatorEngine.index(lattices, validate=True, max_witnesses=1_000_000)`. It accepts lattices from `build_all()` or `Lattice.load()`, builds a meta rules table with one row per partition, and evaluates that table with an embedded `ExpressionRulesEngine` over `EXACT_KEY` dimensions.

This is deliberately not a second ad hoc dispatch language. The meta-engine uses the same ternary values and specificity ordering as ordinary rule evaluation. A partition key can be exact on a context-key dimension or wildcard it; the most specific surviving partition wins. An exact tuple hit is first checked in an O(1) dictionary fast path. Keys that need wildcard/default behavior go through the meta-engine.

```python
index = engine.index(
    lattices,
    validate=True,
    max_witnesses=1_000_000,
)

result = index.apply(context)
batch_result = index.apply_batch(contexts)
```

`index.apply()` routes one context and then evaluates it against the selected lattice. `index.apply_batch()` normalizes context-key fields, groups rows by normalized key, routes each group, evaluates each group with the lattice's filter engine, and merges the survivors while preserving a global context ID. This gives single and batch serving the same partition semantics.

The `validate` option matters at construction time. With validation enabled, the index exhaustively checks a finite witness matrix of routing-equivalence classes. Each dimension contributes every specific key value plus an “other” class represented by the typed `NOT_SET` sentinel (for boolean dimensions, the null don't-care representation). The cross-product is evaluated in chunks. If the number of witnesses exceeds `max_witnesses`, `index()` raises `ValueError` instead of silently sampling. Increase the ceiling, restructure the key dimensions, or explicitly choose `validate=False` when accepting runtime tie detection is appropriate.

Use `LatticeIndex` instead of `apply_auto()` when the same lattice set will serve many requests, when partition ambiguity should be rejected at load time rather than discovered on a request, or when batch routing is needed. Use `apply_auto()` as a one-call convenience wrapper for a small or simple workflow.

#### Diagram: LatticeIndex Partition Routing

<iframe src="../../sims/partition-key-routing/main.html" width="100%" height="500px" scrolling="no"></iframe>

<details markdown="1">
<summary>LatticeIndex Partition Routing</summary>
Type: routing diagram | **sim-id:** partition-key-routing<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Apply exact and wildcard context-key matching to route a context to the most specific lattice (Bloom: Apply).

**Interactions:** Enter context-key values and inspect the selected partition, its combinations, and the fallback path. Toggle overlapping partition definitions to see the ambiguity error and compare a direct exact-key lookup with meta-engine routing.
</details>

<!-- concept:129 -->
## AmbiguousPartitionError

`AmbiguousPartitionError` is raised when two or more partitions survive routing at the same top specificity. Its message includes the witness context or normalized key and the tied partition keys, making an overlapping partition definition diagnosable.

```python
from mountainash_rules import AmbiguousPartitionError

try:
    result = index.apply(context)
except AmbiguousPartitionError as error:
    print(error)
```

The exception subclasses `KeyError`. A partition routing failure has the same practical shape as a missing-key failure to callers: the requested context cannot be assigned to one unambiguous lattice. Existing code that catches `KeyError` for partition lookup therefore continues to catch routing failures, while callers that need to distinguish an overlap can catch `AmbiguousPartitionError` specifically.

With the default `validate=True`, a reachable ambiguity is normally found while constructing the index. The exception can still arise during `apply()` when validation was opted out, when the runtime context reaches a case not represented by a permitted validation matrix, or when an index was assembled under a deliberately deferred validation policy.

<!-- concept:130 -->
## EXACT_KEY Partition Routing

`EXACT_KEY` is the routing dimension strategy used by the index's embedded meta-engine. Its ordinary strategy definition belongs in Chapter 2; here the important point is how the strategy turns partition keys into routing rules. Every `CONTEXT_KEY` dimension becomes an `EXACT_KEY` dimension in the meta rules table, so wildcard and exact values participate in normal ternary survival and specificity scoring.

An all-wildcard key is the default or overflow partition. It matches contexts that have no more-specific partition, while a key with exact values wins whenever its exact matches give it greater specificity. Exact tuple keys are checked through the O(1) map before falling back to meta-engine evaluation, so the common exact-partition case remains inexpensive.

Missing and null context-key fields are normalized to typed `NOT_SET` values (boolean dimensions retain the null don't-care representation). `NOT_SET` is a context-side missing marker: it can route to a wildcard partition, but it is not a legal literal in a stored partition key because that would collide with missing-field routing.

Load-time structural validation rejects malformed routing tables before requests are served. Empty keys, duplicate keys, keys containing `NOT_SET`, and a mixture of unpartitioned lattices with `CONTEXT_KEY` dimensions are rejected. This prevents a missing key from being confused with a default partition and prevents two entries from claiming the same route. A valid default is represented by the dimension-appropriate wildcard values, not by an empty or `NOT_SET`-bearing key.

The result is one coherent rule: partition selection is itself a small rules-engine evaluation. The same ternary behavior that treats an unconstrained business dimension as a wildcard also lets a default partition catch an otherwise unmatched context, while specificity ensures exact partition definitions take precedence.

## Key Takeaways

- A **Lattice** bundles a combinations frame with dimensions metadata, aggregate definitions, and an optional partition key.
- Combination rows contain coalesced constraints, `co_<field>_na` flags, `__agg_*` values, `__prime_product` provenance, and `__level` depth.
- `Lattice.save()` writes `lattice.parquet` and `manifest.yaml`; `Lattice.load()` restores them and preserves computed columns for offline-build/online-apply workflows.
- `Lattice.is_composed` is `True` when `__prime_product` proves that the frame came from accumulator composition; it does not mean that separate lattices can be merged.
- `AccumulatorResult` extends `RuleResult` with `accumulated()`, `provenance`, and `depths` accessors.
- `AggregateOp` supports commutative and associative `sum`, `min`, `max`, and `product`; aggregate overflow is backend-defined and unguarded.
- `build_all()` creates one lattice per unique `CONTEXT_KEY` combination, while `apply_auto()` provides a convenient one-call selection path.
- `LatticeIndex` is the reusable router for single contexts and batches, using an embedded meta-engine with ordinary ternary and specificity semantics.
- `index(validate=True, max_witnesses=1_000_000)` performs exhaustive witness-matrix ambiguity validation; `AmbiguousPartitionError` identifies top-specificity ties and remains catchable as a `KeyError`.
- `EXACT_KEY` dimensions power routing: exact keys use the fast path, wildcard keys provide the default partition, and missing values become `NOT_SET` for wildcard-only fallback.
