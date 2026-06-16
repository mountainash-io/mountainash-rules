---
title: "Chapter 8: Accumulator Engine"
description: "The AccumulatorEngine's lattice-building algorithm including prime encoding, anchor creation, level expansion, canonical ordering, and frontier filtering."
generated_by: claude skill chapter-content-generator
date: 2026-06-03
version: 0.08
---

# Chapter 8: Accumulator Engine

## Summary

This chapter covers the AccumulatorEngine class and its lattice-building algorithm. You will learn how prime number encoding provides unique combination identity and subset detection, the sieve-based prime table, the five-phase build process (partition, prime assignment, anchor creation, level expansion with canonical ordering, and frontier filter for removing dominated combinations), and how checked multiplication guards against overflow.

---

## From Expression Engine to Accumulator

The ExpressionRulesEngine (Chapter 5) answers point queries at evaluation time: "given this context, which rules match right now?" The AccumulatorEngine answers a fundamentally different question at *build* time: "what are all the maximal consistent combinations of rules, and what are their merged constraints?"

The result — a lattice of combinations — can then be queried at runtime just like a rules table. But because the lattice pre-encodes all valid rule interactions, the runtime query is simpler and can aggregate values across contributing rules (e.g., sum discounts from multiple applicable rules).

The AccumulatorEngine uses the compatible and coalesce expressions from Chapter 7 as building blocks, orchestrating them through a five-phase lattice construction algorithm.

<!-- concept:68 -->
## AccumulatorEngine

The `AccumulatorEngine` class is constructed with dimension metadata and optional aggregate definitions:

```python
from mountainash_rules import AccumulatorEngine, DimensionsMetadata, Aggregate

engine = AccumulatorEngine(
    dimension_metadata=metadata,
    aggregates=[Aggregate(column_name="discount", operation="sum")],
)
```

At construction, the engine:

1. Separates dimensions into CONSTRAINT and CONTEXT_KEY lists
2. Pre-compiles compatible, coalesce, and NA flag expressions for all CONSTRAINT dimensions using the AccumulatorCompiler
3. Stores aggregate definitions for use during level expansion

The primary method is `build(rules, partition_key=None)`, which constructs a `Lattice` object representing all maximal consistent combinations for the given rule set (or partition thereof).

The engine also provides three higher-level methods built on top of `build()`:

- **`build_all(rules)`**: automatically discovers all unique CONTEXT_KEY partitions and builds a lattice for each one
- **`apply(lattice, context)`**: evaluates a context against a pre-built lattice, returning an `AccumulatorResult`
- **`apply_auto(lattices, context)`**: selects the correct lattice from a list by partition key and evaluates

These convenience methods are covered in detail in Chapter 9. This chapter focuses on the build algorithm itself.

<!-- concept:69 -->
<!-- concept:70 -->
<!-- concept:71 -->
## Prime Number Encoding

The core algorithmic insight of the AccumulatorEngine is using prime numbers to represent combinations. Each rule is assigned a unique prime number. A combination of rules is represented by the *product* of their primes.

This encoding provides two powerful properties via the Fundamental Theorem of Arithmetic:

1. **Unique identity**: every combination has a unique prime product (because prime factorizations are unique)
2. **Subset detection**: combination A is a subset of combination B if and only if B's prime product is divisible by A's prime product

For example, if rules R1, R2, R3 are assigned primes 2, 3, 5:

| Combination | Prime Product | Contains R1? | Contains R2? |
|-------------|--------------|-------------|-------------|
| {R1} | 2 | Yes (2 % 2 = 0) | No (2 % 3 != 0) |
| {R2} | 3 | No | Yes |
| {R1, R2} | 6 | Yes (6 % 2 = 0) | Yes (6 % 3 = 0) |
| {R1, R3} | 10 | Yes (10 % 2 = 0) | No (10 % 3 != 0) |
| {R1, R2, R3} | 30 | Yes | Yes |

The subset check \( B \mod A = 0 \) operates in constant time on fixed-width integers, enabling efficient domination detection in the frontier filter.

#### Diagram: Prime Product Combination Lattice

<iframe src="../../sims/prime-product-lattice/main.html" width="100%" height="550px" scrolling="no"></iframe>
<details markdown="1">
<summary>Prime Product Combination Lattice</summary>
Type: diagram
**sim-id:** prime-product-lattice<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive Hasse diagram showing how prime products encode subset relationships in a 4-rule lattice.

**Components:**
- 4 base nodes (primes: 2, 3, 5, 7) at level 0
- Pairwise combinations at level 1 (products: 6, 10, 14, 15, 21, 35)
- Triple combinations at level 2 (products: 30, 42, 70, 105)
- Edges showing subset relationships (divisibility)

**Interactions:** Click any node to highlight all its ancestors (subsets) and descendants (supersets). Hover to see the prime factorization and contributing rules. Toggle "compatibility filter" to show only nodes where all pairs are compatible (simulating the engine's behavior). Drag nodes to rearrange layout.

**Learning objective:** Verify subset relationships using prime product divisibility (Bloom: Apply)
</details>

## Prime Table Sieve

The engine needs a pre-computed table of prime numbers to assign to rules. The `primes.py` module generates this table at import time using the Sieve of Eratosthenes — a classical algorithm that efficiently finds all primes up to a given limit.

```python
def _sieve(limit: int) -> list[int]:
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, limit + 1, i):
                is_prime[j] = False
    return [i for i, v in enumerate(is_prime) if v]

PRIME_TABLE: list[int] = _sieve(3572)
```

The sieve runs up to 3572, producing 500 primes (the 500th prime is 3571). This means the engine supports partitions with up to 500 rules. The sieve executes once at module import time, so there is no runtime cost for prime generation.

## Get Prime Function

The `get_prime(index)` function provides bounds-checked access to the prime table:

```python
def get_prime(index: int) -> int:
    if index < 0:
        raise IndexError("Prime index must be non-negative")
    if index >= len(PRIME_TABLE):
        raise IndexError(
            f"Prime index {index} exceeds table size {len(PRIME_TABLE)}. "
            f"Partition has too many rules."
        )
    return PRIME_TABLE[index]
```

The function uses 0-based indexing: `get_prime(0)` returns 2, `get_prime(1)` returns 3, and so on. The bounds check provides a clear error message when a partition exceeds the supported rule count, rather than failing silently or with a cryptic index error.

<!-- concept:72 -->
## Checked Multiply

As combinations grow larger, their prime products can exceed the int64 range (\(2^{63} - 1\)). The `checked_multiply` function guards against this:

```python
_INT64_MAX = (2**63) - 1

def checked_multiply(a: int, b: int) -> int:
    result = a * b
    if result > _INT64_MAX:
        raise OverflowError(
            f"Prime product {a} * {b} = {result} exceeds int64 max. "
            f"Partition has too many mutually compatible rules."
        )
    return result
```

Int64 overflow is a practical concern because DataFrame backends (Polars, Arrow) use fixed-width 64-bit integers for the `__prime_product` column. Python's arbitrary-precision integers would silently exceed this range, producing incorrect results when the value is stored in the DataFrame.

The overflow check triggers when a partition has many mutually compatible rules — typically more than 15-20 rules that are all pairwise compatible. In practice, this indicates the partition should be subdivided further (adding more CONTEXT_KEY dimensions to reduce partition size).

!!! warning "Overflow in Practice"
    The first 15 primes multiply to approximately \(6.1 \times 10^{17}\), which is within int64 range. Adding the 16th prime (53) pushes the product to \(3.3 \times 10^{19}\), exceeding int64 max. Partitions with more than 15 mutually compatible rules will trigger the overflow guard.

<!-- concept:73 -->
## Anchor Creation

The lattice build begins with **anchor creation** — constructing level-0 singleton combinations where each rule stands alone. The anchor DataFrame adds several column families to the original rules:

- **Coalesced columns** (`co_*`): copies of the rule's constraint values, prefixed with `co_`. At level 0, these are identical to the original values.
- **NA flag columns** (`co_*_na`): 1 if the dimension value is a sentinel, 0 otherwise
- **Tracking columns**: `__prime_product` (equals `__prime` at level 0), `__level` (0)
- **Aggregate columns** (`__agg_*`): initial values from the aggregate source column

```python
# Conceptual anchor for rule with region="AU", age_min=18, age_max=65, discount=0.10
# Assigned prime: 2

# Original: region="AU", age_min=18, age_max=65, discount=0.10, __prime=2
# Added:    co_region="AU", co_age_min=18, co_age_max=65
#           co_region_na=0, co_age_na=0
<!-- concept:74 -->
#           __prime_product=2, __level=0
#           __agg_discount=0.10
```

The anchor represents each rule as a standalone combination of depth 1. The level expansion phase will attempt to merge these anchors with other rules to build deeper combinations.

## Level Expansion

Level expansion is the heart of the lattice-building algorithm. Starting from the anchor (level 0), the engine iteratively constructs deeper combinations by cross-joining the current level with the full rule set and filtering for compatible pairs.

Each expansion iteration:

1. **Cross-join**: current level combinations x all rules (producing every possible extension)
2. **Guard: canonical ordering**: the new rule's prime must be greater than the last-added prime in the combination (prevents generating the same combination in multiple orderings)
3. **Guard: not already included**: the candidate's prime must not divide the combination's prime product (prevents adding a rule that is already part of the combination)
4. **Compatibility filter**: all dimension compatible expressions must be True
5. **Coalesce**: apply all coalesce expressions to compute merged constraint values
6. **Update tracking**: multiply prime products, increment level, accumulate aggregates

```python
<!-- concept:75 -->
# Canonical ordering guard
guard1 = ma.col("__prime").lt(ma.col("__prime_rhs"))

# Already-included guard
guard2 = ma.col("__prime_product").mod(ma.col("__prime_rhs")).ne(ma.lit(0))

# Combined with all compatibility expressions
all_guards = guard1.__and__(guard2)
for expr in compat_exprs:
    all_guards = all_guards.__and__(expr)
```

Expansion terminates when an iteration produces zero new combinations (no further compatible extensions exist). In the worst case, this takes N iterations for N rules, but in practice it terminates much sooner because most rule pairs are incompatible.

#### Diagram: Level Expansion Animation

<iframe src="../../sims/level-expansion-anim/main.html" width="100%" height="550px" scrolling="no"></iframe>
<details markdown="1">
<summary>Level Expansion Animation</summary>
Type: microsim
**sim-id:** level-expansion-anim<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Step-by-step animation of the level expansion process for a 4-rule example, showing cross-join, filtering, coalescing, and accumulation at each level.

**Controls:**
- Level selector (0, 1, 2, 3)
- Auto-play / step-through toggle
- Speed control
- "Show guards" toggle to highlight which pairs are eliminated by each guard

**Visual elements:**
- Left: current level combinations as colored cards with prime products
- Center: cross-join matrix showing all pairs with compatibility indicators (green check / red X)
- Right: resulting new combinations with coalesced values
- Bottom: running count of total combinations and eliminated pairs per guard

**Behavior:** At each level, the cross-join matrix appears, guards eliminate incompatible/duplicate pairs (animated), survivors are coalesced, and new combination cards appear at the next level. Final step shows the frontier filter removing dominated combinations.

**Learning objective:** Trace the breadth-first lattice expansion algorithm through multiple levels (Bloom: Analyze)
</details>

## Canonical Ordering Guard

Without the canonical ordering guard, the expansion algorithm would generate the same combination multiple times in different orderings. For example, combination {R1, R2} could be produced by extending {R1} with R2 *and* by extending {R2} with R1.

The canonical ordering guard prevents this by requiring that the new rule's prime is strictly greater than the last-added prime in the combination:

```python
guard1 = ma.col("__prime").lt(ma.col("__prime_rhs"))
```

Here, `__prime` is the last-added rule's prime (stored on the current level row), and `__prime_rhs` is the candidate rule's prime. Only pairs where the candidate has a higher prime are kept.

This is equivalent to requiring that rules in a combination are always ordered by their prime assignment. Since primes are assigned in order of the rule's position in the input DataFrame, the canonical ordering effectively means "only extend forward in the rule list."

The result: each combination is generated exactly once, in its canonical (sorted) form. This reduces the expansion space from \( O(n!) \) orderings per combination to \( O(1) \).

<!-- concept:76 -->
## Frontier Filter

After all levels have been expanded and combined, the engine applies a **frontier filter** to remove dominated combinations. A combination A is dominated by combination B if:

1. A and B have the same fingerprint (identical coalesced values and NA flags on all dimensions)
2. B's prime product is a strict superset of A's (B contains all of A's rules plus more)

The fingerprint condition ensures that both combinations represent the same constraint pattern. The superset condition means B was built from a strictly larger set of rules than A — making A redundant (anything A contributes, B contributes with additional rules included).

The filter implementation uses a self-join on fingerprint columns followed by an anti-join:

```python
# Find dominated: B.prime_product % A.prime_product == 0 AND B != A
dominated = joined.filter(
    ma.col("__pp_super").mod(ma.col("__pp_sub")).eq(ma.lit(0))
    .__and__(ma.col("__pp_super").ne(ma.col("__pp_sub")))
).select(ma.col("__pp_sub").alias("__prime_product")).unique()

# Keep only non-dominated combinations
result = all_combos.join(dominated, on="__prime_product", how="anti")
```

The frontier filter ensures that the final lattice contains only *outermost* (maximal) combinations — those that cannot be extended with any additional compatible rule. This minimizes lattice size while preserving all useful constraint patterns.

#### Diagram: Frontier Filter Domination Removal

<iframe src="../../sims/frontier-filter-demo/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>Frontier Filter Domination Removal</summary>
Type: microsim
**sim-id:** frontier-filter-demo<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive visualization showing how the frontier filter identifies and removes dominated combinations from the lattice.

**Controls:**
- Toggle "show dominated" to reveal/hide eliminated nodes
- Click any node to see its fingerprint and prime factorization
- "Run filter" button to animate the removal process step by step

**Visual elements:**
- Lattice nodes as circles with prime products inside
- Fingerprint groups highlighted with shared background colors
- Dominated nodes shown with strikethrough and red border
- Edges showing subset relationships (divisibility)
- Counter showing combinations before/after frontier filter

**Behavior:** Initially shows all combinations (pre-filter). Clicking "Run filter" highlights fingerprint groups, identifies domination pairs, and animates the removal of dominated nodes. Final state shows only frontier (maximal) combinations.

**Learning objective:** Identify dominated combinations using fingerprint matching and prime product divisibility (Bloom: Evaluate)
</details>

## The Five-Phase Build Process

Summarizing the complete build algorithm:

1. **Partition**: if CONTEXT_KEY dimensions exist, filter rules by the given partition key values
2. **Prime assignment**: assign each rule in the partition a unique prime via `get_prime(index)`
3. **Anchor creation**: construct level-0 singleton combinations with coalesced columns, NA flags, and tracking
4. **Level expansion**: iteratively cross-join, filter (canonical ordering + already-included + compatibility), coalesce, and accumulate until no new combinations are produced
5. **Frontier filter**: remove dominated combinations by fingerprint-based self-join, retaining only outermost nodes

The output is a `Lattice` object wrapping the final DataFrame of maximal consistent combinations. This lattice can then be queried using the `apply()` method (Chapter 9), which internally uses an ExpressionRulesEngine pointed at the lattice's coalesced columns.

## Complexity and Scalability

The worst-case complexity of the lattice build is exponential in the number of rules within a partition — in theory, \( n \) rules can produce up to \( 2^n \) combinations. However, real-world rule sets exhibit much lower combinatorial explosion because most rule pairs are incompatible (they contradict each other on at least one dimension).

Several design decisions keep the build tractable:

- **Partitioning**: CONTEXT_KEY dimensions split the rule set into smaller independent partitions, each of which is built separately. A rule set of 100 rules with a 10-valued CONTEXT_KEY produces 10 partitions of ~10 rules each — far more manageable than one partition of 100.
- **Canonical ordering**: eliminates redundant combination orderings, reducing the search space by a factor of \( k! \) for combinations of size \( k \).
- **Early termination**: expansion stops as soon as a level produces zero new combinations. If all pairwise combinations are found at level 1 but no triples are compatible, the algorithm terminates after level 1.
- **Frontier filter**: reduces the final lattice to only maximal combinations, discarding intermediate subsets that are dominated by larger ones.

In practice, partitions of up to 15-20 rules with moderate compatibility (most pairs incompatible) build in under a second. The int64 overflow guard (checked multiply) serves as a natural safety valve — it halts the build before the combinatorial explosion consumes excessive memory or produces unpresentable results.

## When to Use the Accumulator vs Expression Engine

The AccumulatorEngine is not a replacement for the ExpressionRulesEngine — they solve different problems:

| Question | Use |
|----------|-----|
| "Which single rule best matches this context?" | ExpressionRulesEngine |
| "What is the combined effect of all compatible rules?" | AccumulatorEngine |
| "What discount does the customer get from stacking all applicable offers?" | AccumulatorEngine |
| "Which pricing tier applies to this order?" | ExpressionRulesEngine |

The accumulator workflow involves a build phase (expensive, done once) and an apply phase (cheap, done per query). If your rules change frequently and you only need the best single match, the ExpressionRulesEngine is the simpler and more efficient choice. If your rules are relatively stable and you need multi-rule aggregation, the AccumulatorEngine provides pre-computed answers that would otherwise require complex runtime logic.

## Key Takeaways

- The **AccumulatorEngine** pre-computes all valid rule combinations into a lattice structure, enabling multi-rule aggregation that would be expensive to compute at query time.
- **Prime number encoding** gives each combination a unique identity (prime product) and enables O(1) subset detection via modulo division.
- The **Prime Table Sieve** pre-computes 500 primes at module import time, supporting partitions of up to 500 rules.
- **Get Prime** provides bounds-checked access; **Checked Multiply** guards against int64 overflow when products grow too large.
- **Anchor creation** establishes level-0 singletons with all tracking columns (coalesced values, NA flags, prime products, aggregates).
- **Level expansion** is a breadth-first algorithm that cross-joins the current level with all rules, applies three guards (canonical ordering, already-included, compatibility), then coalesces and accumulates.
- The **Canonical Ordering Guard** ensures each combination is generated exactly once by requiring increasing prime order.
- The **Frontier Filter** removes dominated combinations via fingerprint matching and prime product divisibility, producing a minimal lattice of only outermost nodes.
