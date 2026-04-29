# Multi-Lattice Composition — Design Discussion

**Status:** Backlog. Not scoped for implementation.
**Date:** 2026-04-29
**Author:** Nathaniel Ramm (with Claude)
**Predecessor:** `2026-04-29-accumulator-engine-design.md`, `2026-04-29-strategy-composition-layer-vision.md`

---

## 1. The Gap

The accumulator engine (as shipped) builds a single lattice from a single rule set. All rules are authored by a single authority, versioned together, and combined in one build pass. This matches the original PMX_DB pattern, where the recursive CTE operated over one discretion-rule source per authority level.

The strategy composition layer vision describes a world where **multiple authorities independently author rule sets** — risk limits, portfolio manager discretion, regime overlays, model-derived adjustments — and the combined pricing policy is the composition of all of them. The current engine cannot express this: you'd have to flatten all authority rule sets into one DataFrame and rebuild from scratch whenever any authority's rules change.

---

## 2. What Multi-Lattice Composition Enables

### 2.1 Cross-authority composition

Each authority builds its own lattice independently:

```python
risk_lattice = engine.build(risk_rules)
pm_lattice = engine.build(pm_rules)
regime_lattice = engine.build(regime_rules)

combined = risk_lattice.join(pm_lattice).join(regime_lattice)
```

The join operation runs the same coalesce/compatible/frontier-filter machinery, but over outermost rulesets from two lattices rather than over individual rules. The result is a new lattice whose outermost rulesets represent the maximal consistent combinations across all authorities.

### 2.2 Incremental rebuild

When one authority's rules change, only that lattice needs rebuilding. The join with other unchanged lattices is cheaper than a full rebuild from the flattened union.

### 2.3 Multiple price points from composed layers

A single context applied to a composed lattice returns multiple matching outermost rulesets — each representing a distinct valid price point under different rule combinations. The accumulator builds the menu of valid prices; the filter engine picks from it.

Separately, applying the context to each layer independently produces per-layer price components:

```python
base = engine.apply(base_lattice, ctx)       # base rate component
risk = engine.apply(risk_lattice, ctx)        # risk margin
disc = engine.apply(discretion_lattice, ctx)  # discretion adjustment
```

The final price is the sum (or other composition) of selected matches from each layer. Each layer is independently authored, versioned, built, and cached.

### 2.4 Authority dominance becomes mathematical

The Pareto frontier filter naturally handles authority hierarchy. A risk limit that pins more dimensions (more constraints) mathematically dominates a discretion rule that leaves those dimensions as wildcards. The frontier filter preserves the risk limit and prunes any discretion combination that it strictly dominates — without any explicit priority system.

---

## 3. Why Multisets Become Real

The current single-lattice build uses one-rule-at-a-time extension with two guards:
- Guard 1: ascending prime order (`__prime < __prime_rhs`)
- Guard 2: divisibility check (`__prime_product % __prime_rhs != 0`)

These prevent multisets completely — each rule can appear at most once in any combination.

Lattice joins break this guarantee. If the same underlying rule appears in two authority lattices (e.g., a shared base-rate rule included in both the risk and PM rule sets), the join could combine outermost rulesets that both contain it. The prime product correctly tracks this: if rule R3 (prime 5) appears in both sides, the joined combination's prime product includes 5² = 25, and the divisibility test still works. A bitset would silently collapse this to a single occurrence, corrupting the accumulated numeric.

This is the original design rationale for choosing primes over bitsets — the single-lattice build doesn't need it, but multi-lattice composition does.

---

## 4. Implementation Sketch

### 4.1 `Lattice.join(other) → Lattice`

Structurally identical to the build phase's recursive expansion, but:
- LHS is outermost rulesets from `self`, not singleton rules
- RHS is outermost rulesets from `other`, not singleton rules
- The cross-join + compatible filter + coalesce + accumulate pipeline is the same
- The frontier filter runs over the joined result
- Guard 1 (ascending prime) needs rethinking — the LHS already has compound prime products, not single primes. The canonical ordering guard may need to use the prime product itself, or be replaced with a different deduplication strategy.
- Guard 2 (divisibility) still works: `lhs.prime_product % rhs.prime_product != 0` correctly rejects joins where one side is a subset of the other.

### 4.2 Prime allocation across authorities

Each authority's rules must be assigned primes from the **same global prime space** (within a partition). If risk rules get primes 2,3,5 and PM rules independently get primes 2,3,5, the join cannot distinguish which authority contributed which rule. Either:
- A global prime allocator assigns primes across all authorities before any lattice is built
- Each authority gets a disjoint prime range (risk: primes 0-99, PM: primes 100-199)
- Primes are re-allocated at join time (expensive, but correct)

### 4.3 Metadata alignment

Both lattices must share the same `DimensionsMetadata` (same dimensions, same strategies, same sentinels). Joining lattices with different dimension sets is undefined and should raise.

---

## 5. Open Questions

1. **Should join be commutative?** `A.join(B)` and `B.join(A)` should produce the same outermost rulesets (possibly in different order). This is true if the coalesce operation is commutative per dimension type (EXACT: yes, RANGE intersection: yes, GT/LT greatest/least: yes). Verify.

2. **Should join be associative?** `(A.join(B)).join(C)` should equal `A.join(B.join(C))`. This affects whether the user can compose in any order. Likely yes given the commutativity of coalesce, but the frontier filter's interaction with intermediate results needs analysis.

3. **Is the flattened single-build equivalent?** Does `engine.build(concat(risk_rules, pm_rules))` produce the same lattice as `risk_lattice.join(pm_lattice)`? If yes, the join is purely an optimisation (incremental rebuild). If no, the join has different semantics and both paths need to be supported.

4. **How does the PMX_DB authority-level system map?** The SQL had `authoritylevel_id` as a context-key partition field, building separate lattices per authority level. Is multi-lattice join a generalisation of that, or a different pattern?

---

## 6. Relationship to Other Backlog Items

- **Strategy Composition Layer** (`2026-04-29-strategy-composition-layer-vision.md`): Multi-lattice composition is the engine mechanism that makes the vision's "multiple authorities, one policy" story concrete.
- **Temporal Rules Engine** (roadmap candidate 1): Temporal lattice composition — joining a base lattice with a time-varying overlay lattice — is a natural extension.
- **Backend-native recursion** (execution model A): The join operation is another candidate for backend-native execution once `mountainash.expressions` supports it.
