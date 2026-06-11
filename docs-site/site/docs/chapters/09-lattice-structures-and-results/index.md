---
title: Lattice Structures and Results
description: The Lattice data structure, AccumulatorResult accessors, and supporting modules for partition-based rule evaluation
generated_by: claude skill chapter-content-generator
date: 2026-06-03
version: 0.08
---

# Chapter 9: Lattice Structures and Results

## Summary

This chapter covers the Lattice data structure produced by the AccumulatorEngine and the AccumulatorResult class that wraps evaluation output. You will learn about lattice combinations, partition keys, coalesced columns, NA flag columns, combination depth tracking, and the AccumulatorResult accessors for accumulated aggregates, provenance via prime products, and depth information. The chapter concludes with supporting modules: the Aggregate model, partition key filtering, build_all for multi-partition construction, and apply_auto for automatic lattice selection.

---

<!-- concept:77 -->
<!-- concept:78 -->
<!-- concept:79 -->
## What the Lattice Represents

In Chapter 8 you learned how the AccumulatorEngine expands individual rules into multi-rule combinations through level expansion and then filters dominated combinations using the frontier filter. The output of that process is a DataFrame where each row represents a valid combination of rules, annotated with coalesced dimension values, aggregated outputs, prime products for provenance tracking, and depth information. The **Lattice** class wraps this DataFrame along with its associated metadata, making the accumulator's output a first-class object that can be stored, inspected, and applied to contexts.

The term "lattice" comes from the mathematical structure of rule combinations. If you have three rules \( R_1, R_2, R_3 \), the possible combinations form a partial order: single rules at depth 1, pairs at depth 2, and the triple at depth 3. The frontier filter prunes this structure to retain only non-dominated combinations, but the underlying partial-order relationship gives the data structure its name.

Understanding the Lattice class requires you to hold three ideas together: what the combinations contain (coalesced columns and NA flags), how they relate to the original rules (prime products and depth), and how they are partitioned for efficient lookup (partition keys). This chapter addresses each in turn.

#### Diagram: Lattice Structure Overview

<iframe src="../../sims/lattice-structure-overview/main.html" width="100%" height="500px" scrolling="no"></iframe>

<details markdown="1">
<summary>Lattice Structure Overview</summary>
Type: class diagram with data preview | **sim-id:** lattice-structure-overview<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Visualize the Lattice class and its relationship to the underlying DataFrame, metadata, aggregates, and partition key.
**Bloom Level:** Understand
**Interactions:** Click each property node (combinations, partition_key, metadata, aggregates) to see its type, a description, and an example value. Hover over the DataFrame node to see sample columns including co_-prefixed, na_-prefixed, __prime_product, and __level columns.
</details>

<!-- concept:83 -->
## The Lattice Class

A **Lattice** is constructed with four arguments: a DataFrame of combinations, a DimensionsMetadata describing the dimensions used during accumulation, a list of Aggregate models specifying which output columns to aggregate and how, and an optional partition key dictionary that identifies which subset of the rule space this lattice covers.

```python
class Lattice:
    def __init__(
        self,
        dataframe,
        metadata: DimensionsMetadata,
        aggregates: list[Aggregate],
        partition_key: dict | None,
    ) -> None:
        self._df = dataframe
        self._metadata = metadata
        self._aggregates = aggregates
        self._partition_key = partition_key
```

The class exposes four read-only properties. The `combinations` property returns the raw DataFrame. The `count` property returns the number of combination rows, computed via the backend-agnostic `relation()` interface so it works identically on Polars, Pandas, or any other supported backend. The `partition_key` property returns the dictionary identifying this lattice's partition (or `None` for unpartitioned lattices). The `metadata` and `aggregates` properties return the construction-time metadata and aggregate definitions.

The Lattice is intentionally a thin wrapper. It does not add computation or transformation logic -- those responsibilities belong to the AccumulatorEngine. Its purpose is to bundle the DataFrame with its schema context so that downstream code (particularly the `apply()` method) can reconstruct the correct evaluation metadata without the caller needing to pass metadata separately.

<!-- concept:82 -->
## Lattice Combinations

The **lattice combinations** are the rows of the wrapped DataFrame. Each row represents one valid combination of rules that survived the frontier filter. A combination at depth 1 corresponds to a single original rule. A combination at depth 2 represents two rules whose constraint dimensions are compatible (they can be coalesced without contradiction). A combination at depth 3 represents three compatible rules, and so on.

Every combination row contains several categories of columns:

- **Coalesced columns** (prefixed with `co_`) -- the tightened dimension values that result from merging the constraints of the contributing rules.
- **NA flag columns** (prefixed with `na_`) -- boolean indicators showing which dimensions were not constrained by any contributing rule.
- **Aggregate columns** (prefixed with `__agg_`) -- the accumulated aggregate values (sums, counts, etc.) across contributing rules.
- **The prime product** (`__prime_product`) -- a unique integer identifying which rules contributed to this combination.
- **The level** (`__level`) -- the number of rules in this combination (its depth in the lattice).

The combination DataFrame is the central data artifact of the accumulator pipeline. It is computed once during the build phase and then queried many times during the apply phase, where contexts are matched against combinations using the coalesced columns as filter criteria.

| Column Category | Prefix | Example | Purpose |
|---|---|---|---|
| Coalesced values | `co_` | `co_region`, `co_tier` | Tightened constraint bounds |
| NA flags | `na_` | `na_region`, `na_tier` | Indicates unconstrained dimensions |
| Aggregates | `__agg_` | `__agg_premium`, `__agg_discount` | Accumulated output values |
| Prime product | `__prime_product` | 30 (= 2 * 3 * 5) | Provenance tracking |
| Level | `__level` | 3 | Combination depth |

<!-- concept:88 -->
## Lattice Partition Key

A **partition key** is a dictionary that identifies which slice of the rule space a particular Lattice covers. Partition keys correspond to dimensions with the `CONTEXT_KEY` role (introduced in Chapter 3). These dimensions are not used for constraint matching -- instead, they segment the rule table so that each segment can be built into its own independent lattice.

For example, if a rule table has a `product_type` dimension with the `CONTEXT_KEY` role, and the rule table contains rows for `product_type="insurance"` and `product_type="lending"`, the engine produces two lattices: one with `partition_key={"product_type": "insurance"}` and one with `partition_key={"product_type": "lending"}`. Each lattice contains only the combinations relevant to its product type.

Partitioning is important for two reasons. First, it reduces the size of each lattice, which makes the apply phase faster because fewer combinations need to be evaluated. Second, it ensures that rules from different partitions do not accidentally combine during level expansion -- an insurance premium rule should never coalesce with a lending discount rule.

```python
# A lattice built for insurance products
lattice = engine.build(rules, partition_key={"product_type": "insurance"})
print(lattice.partition_key)  # {"product_type": "insurance"}
print(lattice.count)          # Number of combinations for insurance only
```

When no `CONTEXT_KEY` dimensions exist, the entire rule table is built into a single lattice with `partition_key=None`.

<!-- concept:80 -->
<!-- concept:81 -->
## Coalesced Columns

**Coalesced columns** store the tightened constraint values for each combination. When two rules are combined, their constraints on each dimension are merged. For an EXACT match dimension, if both rules specify the same value, the coalesced value is that value. If one rule specifies a value and the other leaves the dimension unconstrained (sentinel), the coalesced value takes the constrained rule's value. For a RANGE dimension, the coalesced range is the intersection of the two rules' ranges -- the tighter of the two minimum bounds and the tighter of the two maximum bounds.

The name "coalesced" comes from the SQL COALESCE function, which returns the first non-null value from a list. The accumulator compiler's coalesce expressions (Chapter 7) work similarly: they pick the most specific value from the contributing rules.

Coalesced columns are named with a `co_` prefix followed by the original rule field name. If the original dimension used a `rule_field` of `region`, the coalesced column is `co_region`. For RANGE dimensions that use `range_min_field` and `range_max_field`, the coalesced columns are `co_range_min_field` and `co_range_max_field`.

During the apply phase, the engine builds a remapped DimensionsMetadata that redirects dimension lookups to the `co_`-prefixed columns rather than the original rule columns. This remapping is transparent to the caller -- they provide a context with the same field names as always, and the engine handles the column redirection internally.

#### Diagram: Coalesced Column Derivation

<iframe src="../../sims/coalesced-column-derivation/main.html" width="100%" height="500px" scrolling="no"></iframe>

<details markdown="1">
<summary>Coalesced Column Derivation</summary>
Type: data flow diagram | **sim-id:** coalesced-column-derivation<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Trace how original rule column values are merged into coalesced columns during level expansion.
**Bloom Level:** Apply
**Interactions:** Select two rules from a sample rule table and see their constraint values merged step by step. Toggle between EXACT and RANGE dimensions to see the different coalescing strategies (value selection vs. range intersection).
</details>

## NA Flag Columns

**NA flag columns** are boolean columns that indicate whether a dimension was left unconstrained (NA / sentinel) in a particular combination. They are named with an `na_` prefix followed by the rule field name. An NA flag of `true` means that none of the rules contributing to this combination specified a value for that dimension, so the combination matches any context value on that dimension.

NA flags serve two purposes. First, they make the apply phase efficient: if `na_region` is `true`, the engine can skip the region constraint evaluation entirely for that combination, because it matches regardless of the context's region value. Second, they enable observability: an analyst inspecting a lattice can quickly identify which combinations are broad (many NA flags set to `true`) versus narrow (few or no NA flags).

The NA flag columns are computed by the accumulator compiler's coalesce NA flag expressions (Chapter 7). When two rules are combined, the NA flag for a dimension is `true` only if both contributing rules had sentinel values for that dimension. If either rule constrained the dimension, the NA flag is `false` for the combination, even if one of them was unconstrained.

```python
# In the lattice DataFrame, NA flags appear alongside coalesced values:
# | co_region | na_region | co_tier | na_tier | __agg_premium | __level |
# |-----------|-----------|---------|---------|---------------|---------|
# | "NSW"     | false     | null    | true    | 120.0         | 2       |
# | null      | true      | "gold"  | false   | 85.0          | 1       |
```

The example above shows two combinations. The first has `co_region="NSW"` (constrained) and `na_tier=true` (unconstrained on tier). The second is unconstrained on region but constrained to `tier="gold"`.

## Combination Depth

The **combination depth** (stored in the `__level` column) records how many original rules contributed to a combination. A depth of 1 means the combination is a single rule. A depth of 2 means two rules were combined. The maximum possible depth equals the number of rules in the partition, though the frontier filter typically prunes the lattice well before that limit.

Depth is significant because deeper combinations are more specific -- they incorporate constraints from more rules, which generally means a narrower match window and a larger accumulated aggregate. When the apply phase finds multiple matching combinations, the one with the greatest depth is typically the most relevant, as it represents the most rules that simultaneously apply to the given context.

The depth value is set during level expansion (Chapter 8). At each expansion step, the engine creates new combinations at depth \( d+1 \) by extending frontier combinations at depth \( d \) with one additional compatible rule. The level column is simply incremented by 1 at each expansion step.

#### Diagram: Lattice Depth Hierarchy

<iframe src="../../sims/lattice-depth-hierarchy/main.html" width="100%" height="450px" scrolling="no"></iframe>

<details markdown="1">
<summary>Lattice Depth Hierarchy</summary>
Type: hierarchical tree | **sim-id:** lattice-depth-hierarchy<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Visualize how combinations at different depths relate to their contributing rules.
**Bloom Level:** Analyze
**Interactions:** Click a combination node to see its contributing rules (decoded from the prime product). Toggle "show pruned" to see combinations that the frontier filter removed. Color intensity indicates accumulated aggregate magnitude.
</details>

## The AccumulatorResult Class

The **AccumulatorResult** extends the `RuleResult` class (Chapter 6) with three accumulator-specific accessors. It wraps the output of the apply phase -- the set of lattice combinations that match a given context -- and provides convenient methods for extracting aggregates, provenance, and depth information.

AccumulatorResult inherits all of RuleResult's capabilities: `survivors` for the full matching DataFrame, `best_match` for the top-ranked combination, `count` for the number of matches, `active_dimensions` for which dimensions participated in the evaluation, and `explain()` for debugging. On top of these, it adds:

```python
class AccumulatorResult(RuleResult):
    def __init__(self, dataframe, active_dimensions, aggregates, lattice):
        super().__init__(dataframe=dataframe, active_dimensions=active_dimensions)
        self._aggregates = aggregates
        self._lattice = lattice
```

The constructor takes the matching combinations DataFrame, the list of active dimension names, the Aggregate model list, and a reference to the source Lattice. The lattice reference enables downstream code to access the full combination space if needed, not just the matches.

<!-- concept:84 -->
<!-- concept:87 -->
## Accumulated Aggregates

The **accumulated aggregates** accessor retrieves the aggregated output values for matching combinations. Each aggregate is identified by name (corresponding to the `column_name` field of the Aggregate model). The `accumulated()` method selects the `__agg_`-prefixed column from the result DataFrame and returns it as a collected value.

```python
result = engine.apply(lattice, context)

# Get the accumulated premium for all matching combinations
premiums = result.accumulated("premium")
print(premiums)  # Returns the __agg_premium column values
```

The method name follows the pattern `accumulated(aggregate_name)` rather than a property, because there can be multiple aggregates per engine and the caller needs to specify which one they want. If the engine was configured with two aggregates -- say "premium" and "discount" -- the caller makes two separate calls.

The accumulated values represent the sum (or other configured operation) of the original rule values across all rules in each combination. A combination at depth 3 with three rules contributing premium values of 50, 30, and 20 would have an `__agg_premium` of 100 (assuming sum aggregation).

<!-- concept:85 -->
<!-- concept:86 -->
## Provenance Accessor

The **provenance accessor** returns the prime product column from the result DataFrame. The prime product is a single integer that uniquely encodes which original rules contributed to each matching combination. By factoring this integer into its prime components (each rule is assigned a unique prime number during engine construction), you can recover the exact set of contributing rules.

```python
result = engine.apply(lattice, context)

# Get prime products for matching combinations
primes = result.provenance
# Returns __prime_product column, e.g., [30, 6, 5]
# 30 = 2 * 3 * 5 (rules 1, 2, 3)
# 6  = 2 * 3     (rules 1, 2)
# 5  = 5         (rule 3 alone)
```

Provenance is essential for auditability. When a business analyst asks "which rules produced this premium of 150?", the prime product provides a deterministic answer without requiring the engine to re-derive the combination. The checked multiply mechanism (Chapter 8) ensures that prime products never overflow within the supported rule count, maintaining uniqueness.

## Depths Accessor

The **depths accessor** returns the `__level` column from the result DataFrame. This tells you how many rules contributed to each matching combination.

```python
result = engine.apply(lattice, context)
depths = result.depths
# Returns __level column, e.g., [3, 2, 1]
```

Depth information is useful for ranking. When multiple combinations match a context, a common strategy is to prefer deeper combinations because they represent more specific rule intersections. The `best_combination` property (inherited as `best_match` from RuleResult) already applies specificity-based ranking, but the depths accessor lets callers implement custom ranking logic.

## The Aggregate Model

The **Aggregate model** is a Pydantic BaseModel with two fields: `column_name` (the name of the rule table column to aggregate) and `operation` (the aggregation function, defaulting to `"sum"`). It is the configuration object that tells the AccumulatorEngine which columns to accumulate and how.

```python
from mountainash_rules.aggregate import Aggregate

# Sum the "premium" column across contributing rules
agg = Aggregate(column_name="premium", operation="sum")
```

The Aggregate model is deliberately minimal. It does not contain evaluation logic -- it is purely a configuration descriptor. The engine reads the `column_name` and `operation` fields during the build phase to generate the appropriate accumulation expressions. Pydantic validation ensures that both fields are present and correctly typed.

Multiple aggregates can be passed to the engine, producing multiple `__agg_`-prefixed columns in the lattice. Each aggregate operates independently, so you can sum premiums while counting applicable rules in a single build pass.

| Aggregate Field | Type | Default | Purpose |
|---|---|---|---|
| column_name | str | (required) | Rule table column to aggregate |
| operation | str | "sum" | Aggregation function (sum, count, etc.) |

## Partition Key Filtering

**Partition key filtering** is the process of selecting the subset of rules that belongs to a specific partition before building a lattice. When the AccumulatorEngine encounters dimensions with the `CONTEXT_KEY` role, it uses those dimensions' values to segment the rule table.

The filtering works by converting the rules DataFrame to Polars (via the `relation()` interface), selecting the unique combinations of context key fields, and then iterating over those combinations. For each unique key combination, the engine builds a separate lattice containing only the rules that match those key values.

```python
# If product_type is a CONTEXT_KEY dimension:
# Rules DataFrame:
# | product_type | region | premium |
# |-------------|--------|---------|
# | insurance   | NSW    | 100     |
# | insurance   | VIC    | 90      |
# | lending     | NSW    | 50      |
#
# Partition filtering produces:
# Lattice 1: partition_key={"product_type": "insurance"}, 2 rules
# Lattice 2: partition_key={"product_type": "lending"},   1 rule
```

Partition key filtering happens inside `build_all()` and is invisible to callers who use `apply_auto()`. However, understanding the mechanism helps when debugging unexpected empty results -- if the context's product type does not match any partition, a `KeyError` is raised.

#### Diagram: Partition Key Routing

<iframe src="../../sims/partition-key-routing/main.html" width="100%" height="500px" scrolling="no"></iframe>

<details markdown="1">
<summary>Partition Key Routing</summary>
Type: routing diagram | **sim-id:** partition-key-routing<br/> | **Library:** vis-network<br/> | **Status:** Specified

**Learning Objective:** Understand how context key values route a context to the correct lattice partition.
**Bloom Level:** Apply
**Interactions:** Enter a context with different product_type values and see which lattice partition is selected. View the rules in each partition and the resulting combinations. See the KeyError path when no partition matches.
</details>

<!-- concept:89 -->
## Build All Partitions

The **build_all** method is the multi-partition entry point on AccumulatorEngine. It takes a rules DataFrame and returns a list of Lattice objects, one per unique partition key combination found in the rules. If no `CONTEXT_KEY` dimensions are defined, it falls back to building a single unpartitioned lattice.

```python
engine = AccumulatorEngine(
    dimension_metadata=metadata,
    aggregates=[Aggregate(column_name="premium")],
)

# Build lattices for all product types at once
lattices = engine.build_all(rules)
print(len(lattices))  # Number of unique partition key combinations
```

The method works by extracting the unique values of all context key fields, iterating over them, and calling `build()` with each partition key. The result is a list that can be passed directly to `apply_auto()` for context-driven lattice selection.

Build-all is designed for batch scenarios where you want to pre-compute all lattices once and then apply many contexts against them. Building is the expensive operation (it involves level expansion and frontier filtering); applying is comparatively cheap (it is a single-pass filter through the lattice). Pre-building all partitions amortizes the build cost across many apply calls.

<!-- concept:90 -->
## Apply Auto Selection

The **apply_auto** method completes the partition workflow. It takes a list of lattices (typically from `build_all()`), a context object, and an optional dimension subset. It extracts the partition key from the context by reading the context key dimension values, looks up the corresponding lattice from a dictionary keyed by partition tuples, and delegates to the standard `apply()` method.

```python
# Pre-build all lattices
lattices = engine.build_all(rules)

# Apply a specific context -- auto-selects the correct lattice
context = {"product_type": "insurance", "region": "NSW", "tier": "gold"}
result = engine.apply_auto(lattices, context)

# Access results as usual
print(result.accumulated("premium"))
print(result.provenance)
print(result.depths)
```

The code above demonstrates the complete partition workflow. The engine builds lattices for all product types, then when a context arrives with `product_type="insurance"`, `apply_auto` selects the insurance lattice and applies the remaining constraint dimensions against it.

If the context's partition key does not match any lattice, `apply_auto` raises a `KeyError` with a message showing the unmatched key tuple. This explicit failure is preferable to silently returning empty results, because an unmatched partition key usually indicates a configuration error (a missing partition in the rules or a misspelled context key value) rather than a legitimate "no rules match" scenario.

The internal lookup uses a dictionary mapping tuples of partition key values to Lattice objects. The tuple ordering is determined by the engine's `_context_key_dims` list, ensuring consistent key construction between the build and apply phases.

## Key Takeaways

- The **Lattice class** wraps a DataFrame of rule combinations with metadata, aggregate definitions, and an optional partition key, providing a self-contained object for the accumulator's output.
- **Lattice combinations** are rows representing valid multi-rule intersections, each carrying coalesced values, NA flags, aggregates, prime products, and depth.
- **Partition keys** segment the rule space by `CONTEXT_KEY` dimension values, producing independent lattices that prevent cross-partition rule combination.
- **Coalesced columns** (prefixed `co_`) store the tightened constraint values after merging contributing rules; the apply phase uses remapped metadata to filter against these columns.
- **NA flag columns** (prefixed `na_`) indicate unconstrained dimensions, enabling efficient short-circuit evaluation and observability.
- **Combination depth** (`__level`) records the number of contributing rules, with deeper combinations representing more specific rule intersections.
- The **AccumulatorResult** extends RuleResult with `accumulated()`, `provenance`, and `depths` accessors for aggregate values, prime product traceability, and depth information.
- The **Aggregate model** is a Pydantic configuration object specifying which rule column to aggregate and which operation to use.
- **Build all partitions** pre-computes lattices for every unique partition key, and **apply auto** routes a context to the correct lattice by extracting its partition key values.
