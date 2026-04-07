# Additive Rules Engine — Architecture Analysis

**Status:** Analysis only. Not an implementation spec.
**Date:** 2026-04-07
**Author:** Nathaniel Ramm (with Claude)

---

## 1. Purpose & scope

This document analyses a second rules-engine pattern that the codebase needs but does not yet have, by reverse-engineering the SQL function `pmx.sp_productpricingmatrix_discretion_combos` (`sp_productpricingmatrix_discretion_combos.sql`, 1105 lines) and comparing its execution model against the existing engine family.

The output of this document is **understanding**, not code:

- A deep read of the SQL, sufficient that an implementer who has never seen it can reason about every mechanism it employs.
- A formal architectural comparison of the two engine patterns — what makes them different, where they share machinery, and how they relate.
- A minimum gap list against the current shared metadata layer, identifying what the new engine would need that does not yet exist. No API design.
- A set of open questions that only production data or implementation-phase work can resolve.

A separate spec, plan, and implementation will follow if and when the team commits to building the new engine. This document exists to make that decision well-informed.

The two patterns are named throughout as:

- **Filter engine** — the existing engine family (`RulesEngine`, `HybridRulesEngine`, `VectorizedRulesEngine`). Answers *"which rules apply to this context?"*.
- **Accumulator engine** — the new pattern under analysis. Answers *"what is the maximal consistent combination of rules, and what numeric does it accumulate?"*.

---

## 2. The two patterns at a glance

The two engines differ not in *what they evaluate* but in *what a rule is*.

In the **filter engine**, a rule is a *proposition about a context*: given context C, does rule R fire? Rules are evaluated independently. Output cardinality equals input cardinality (one decision per rule). There is no inter-rule state. The context is the protagonist; rules are predicates over it.

In the **accumulator engine**, a rule is a *partial constraint that composes with other partial constraints*. The engine traverses the rule registry and emits every *maximally consistent combination* of rules — each combination carrying (a) a coalesced attribute fingerprint that tells you which contexts it would later apply to, (b) one or more accumulated numerics, and (c) an identity DNA that lets a final pass discard non-maximal combinations. This first phase has **no context**. A second phase matches a context against the precomputed lattice of combinations to retrieve the applicable result(s).

The shared metadata layer (`Dimension`, `MatchStrategy`, ternary match encoding) describes the *vocabulary of constraints*. The filter engine consumes that vocabulary to ask *"does this rule speak about this context?"*. The accumulator consumes the same vocabulary to ask *"do these two rules speak compatibly about the same hypothetical context, and what is the joint thing they say?"*.

A summary table appears at the end of section 5, after both engines have been described in detail.

---

## 3. Deep read of the SQL

The SQL builds a recursive Common Table Expression (CTE) over a discretion-rule source (`pmx.sp_productpricingmatrix_discretion(@floor_type)`), then applies a final filter that uses prime-factor divisibility to discard non-maximal combinations. The function takes one parameter (`@floor_type`) and returns a table — there is no per-context input.

The CTE has three structural parts: an **anchor member** (lines 269–516), a **recursive member** (lines 517–1023), and a **final filter** wrapped around the CTE result (lines 1025–1097).

### 3.1 Anchor member — rules as singleton rulesets

Each row from the discretion-rule source becomes a level-0 ruleset of itself. The anchor SELECT does three things:

1. **Bootstraps the coalesced state** by aliasing each rule attribute as its `co_*` ("coalesced") counterpart (lines 397–420). At level 0, a singleton ruleset's coalesced state is identical to the rule's own state.
2. **Computes the initial fingerprint hashes** (lines 423–482) — three of them: `ruleset_nonbanded`, `ruleset_banded`, `ruleset_bandingsystem`. Only `ruleset_nonbanded` is used downstream by the superset filter (line 1070); the others exist for the SQL's binning subsystem and are addressed in section 3.4.
3. **Initialises the recursion control fields** (lines 499–504): `level = 0`, `combination = pricingmarginshapecell_id` cast to a string (this is the comma-separated provenance trail that grows in the recursive member), and `combination_primeproduct = a.primevalue` — each rule carries a globally-allocated prime which becomes the seed of the combination DNA.

### 3.2 Recursive member — coalesce, three-valued match, accumulation

The recursive member is the heart of the mechanism. It is an `INNER JOIN` of a fresh row from the source (`b`, the new rule being added) against the CTE itself (`a`, the partial ruleset accumulated so far). It has three concurrent mechanisms.

**(a) The coalesce rule.** For each dimension, the join's SELECT computes a new coalesced value via the pattern (lines 677–720):

```sql
isnull(coalesce(
    CASE WHEN a.co_X_naflag = 1 then null else a.co_disc_X_id END,
    CASE WHEN b.X_naflag    = 1 then null else b.disc_X_id    END
), a.co_disc_X_id) as co_disc_X_id
```

This reads as: take the LHS's coalesced value if it is a hard value; otherwise take the RHS's value if hard; otherwise fall back to the LHS's prior coalesced value. Hard values from either side win. Don't-care (NA) tunnels through to the fallback.

Alongside, the coalesced NA flag is computed using the pattern at lines 615–668:

```sql
isnull(coalesce(
    CASE WHEN a.co_X_naflag = 1 then null else 0 END,
    CASE WHEN b.X_naflag    = 1 then null else 0 END
), 1) as co_X_naflag
```

Which is logically `co_X_naflag = a.co_X_naflag AND b.X_naflag`. A dimension remains NA only if **both** sides are NA on it; the moment either side pins the dimension, the joint state is pinned.

**(b) The three-valued match condition.** The join predicate is built per dimension as (lines 888–1016):

```sql
( a.co_disc_X_id = b.disc_X_id ) OR a.co_X_naflag = 1 OR b.X_naflag = 1
```

Hard values must agree; if either side is NA on this dimension, the pair is accepted. This is the SQL realisation of three-valued logic over (hard-value, hard-value, don't-care).

Note the asymmetry: the LHS uses the *coalesced* state (`a.co_*`), the RHS uses the rule's raw state (`b.*`). LHS represents "everything we've accumulated so far"; RHS represents "the new rule we are trying to add".

**(c) Accumulation.** Three things accumulate across each recursive step (lines 850–863):

| Field | Operation | Lines |
|---|---|---|
| `aggregate_margin` | `a.aggregate_margin + b.margin_value` | 851 |
| `aggregate_margin_desk` | `a.aggregate_margin_desk + b.margin_value_desk` | 854 |
| `combination_primeproduct` | `a.combination_primeproduct * b.primevalue` | 863 |
| `combination` | `a.combination + ',' + b.cell_id` (provenance) | 858 |
| `level` | `a.level + 1` | 857 |

The first two are user-meaningful numerics being summed monoidally. The third is the **combination DNA** — a multiplicative product of primes that uniquely identifies the *set* (or multiset; see section 3.3) of rules contributing to this combination, and which the final filter uses for divisibility testing. The fourth is a human-readable provenance trail. The fifth is depth, used for nothing but observability.

**(d) Anti-duplication guards** (lines 1019–1020):

```sql
AND ( a.pricingmarginshapecell_id < b.pricingmarginshapecell_id )
AND ( a.pricingmarginshape_id     <> b.pricingmarginshape_id     )
```

The first guarantees a canonical ordering: `{R1,R2}` and `{R2,R1}` cannot both be emitted, because only the strict-less-than direction passes. The second prevents combining two cells that belong to the same `pricingmarginshape`, where a shape is a mutually-exclusive group of rule cells (combining two cells from the same shape would have no semantic meaning, since exactly one applies).

### 3.3 Final filter — prime quotient as subset test

After the CTE materialises every reachable rule combination at every level, the outer query (lines 1025–1097) applies the *outermost-frontier* filter. For each row:

1. **Group by namespace.** Rows are partitioned by `(product_id, loanpurpose_id, ruleset_nonbanded)` — i.e. by the natural-key fields and the coalesced-attribute fingerprint. Within a namespace, multiple distinct combinations may resolve to the same fingerprint (different rules, same final shape of constraints).
2. **Find any dominating combination.** For each row, look for any *other* row in the same namespace whose `superset_combination_primeproduct % own_combination_primeproduct = 0` (line 1078). Integer divisibility under prime arithmetic is exactly the test for *prime-factor inclusion*: if the superset's product divides cleanly by mine, then every prime factor I have is also in the superset. Since each rule contributes a unique prime, this is the same as asking *"does the superset contain every rule that I contain?"*.
3. **Drop dominated rows.** If any such dominating combination exists, the row is marked `has_superset = 1` and the final WHERE clause (line 1096) discards it. Only **outermost rulesets** survive — combinations that no other combination strictly dominates within their fingerprint namespace.

Formally, this is a **Pareto frontier under prime-factor dominance**: each row is a point in a partial order where dominance means "is a strict superset of, by prime factorisation", and the filter retains only the non-dominated points. The pyramid metaphor: each fingerprint namespace is a pyramid, the *outermost* surface is what survives the filter, and the *inner scaffolding* of intermediate combinations is discarded. Across namespaces, combinations are non-comparable — they describe different constraint shapes that would apply to different contexts.

> **Why primes, and a deferred optimisation.**
>
> Primes are used because the SQL needs a representation of "set membership" that supports both (a) cheap pairwise composition (multiplication) and (b) cheap subset testing (divisibility), in a language without bitsets. Allocating one prime per rule and multiplying them is a clever workaround.
>
> Crucially, **prime products preserve multiset semantics**. If a combination contains a rule twice (i.e. its prime appears with multiplicity 2 in the factorisation), the divisibility test still correctly identifies subset relationships across multisets. A bitset, by contrast, can only represent sets — it cannot distinguish "rule R appears once" from "rule R appears twice".
>
> Whether the new engine needs multiset support is **open** (see section 7, Q1). If empirical analysis confirms the lattice produces only true sets, a bitset DNA (`(super & sub) == sub` instead of modulo) becomes a viable optimisation. Until that analysis is done, primes are the only safe representation.
>
> **Independent of multiset semantics, the SQL's choice of *globally static* primes is an artefact of its execution model.** The SQL must allocate primes once across the entire rule registry because it materialises one big lattice per `@floor_type`. The Python engine, by contrast, can build *per partition* (see section 3.5) and allocate primes **locally per build**, starting from 2. This keeps the smallest primes on the rules most likely to combine deeply, dramatically improves overflow headroom, and means the same prime `2` is reused for unrelated rules across parallel builds. Rules need a stable identity for deduplication and provenance; the prime is a build-phase concern, not a rule registry concern.

**Note on the prime-vs-ternary terminology.** The combination DNA's prime arithmetic is unrelated to any "prime ternary" encoding mentioned in older planning documents. The actual per-dimension match encoding in this codebase is the signed-integer ternary scheme (`1` match, `0` unknown, `−1` non-match) defined in `constants.py` and used throughout `compiler.py` and `result.py`. Any reference to `PRIME_TRUE=2 / PRIME_FALSE=3 / PRIME_UNKNOWN=5` in the historical docs is a deprecated design that was never implemented. The accumulator engine's primes identify *combinations of rules*, not match outcomes — they are two completely separate uses of the word "prime".

### 3.4 Sidebar — three ruleset hashes collapse to one

The SQL computes three fingerprint hashes per row:

- `ruleset_nonbanded` — hash of the coalesced non-banded discretion attribute IDs (lines 423–450).
- `ruleset_banded` — hash of the coalesced banded attribute IDs (LVR band, agg-limit band, etc.) (lines 459–466).
- `ruleset_bandingsystem` — hash of the coalesced banding-system IDs (lines 475–482).

Only `ruleset_nonbanded` is used by the final superset filter (line 1070). The other two exist because the SQL had to model continuous variables (LVR, aggregate limits, net utilisation, risk weight) as **pre-categorised discrete bin IDs** in a separate namespace, so that overlap between bins wouldn't fragment the namespace of "rules that share the same non-bin attribute fingerprint".

**This entire layer is unnecessary in the Python engine.** A `RANGE` `MatchStrategy` natively expresses "this rule applies to LVR ∈ \[60, 80)" without a pre-binning step. Intersection of two RANGE constraints (`[60, 80) ∩ [70, 90) = [70, 80)`) is the natural coalesce operation for that dimension type. The three fingerprint hashes collapse to **one fingerprint** computed over all dimensions including ranges. The Python engine inherits the conceptual cleanliness that the SQL had to fake.

### 3.5 Natural keys vs dimensions — a hidden lattice partition

The SQL's join predicate begins with (lines 889–891):

```sql
ON  a.authoritylevel_id = b.authoritylevel_id
AND a.product_id        = b.product_id
AND a.loanpurpose_id    = b.loanpurpose_id
```

These three fields are not behaving like coalesced dimensions. They never NA out, they never participate in the coalesce machinery, and they appear *both* in the recursive join predicate *and* in the superset filter's namespace grouping (lines 1065–1066). They are the **address of the lattice**: the SQL is implicitly building one independent lattice per `(authoritylevel, product, loanpurpose)` tuple, and the result table is the concatenation of all of them.

This is structurally important and the Python engine should make it explicit. There are two distinct roles a `Dimension` can play:

- **Context-key field.** Outer partition. The lattice is built once per distinct value (or per distinct value-class). Rules are pre-filtered to a partition before the build phase begins. These dimensions never enter the coalesce, never appear in the fingerprint, and never participate in the three-valued match.
- **Dimension field.** Participates in coalesce, three-valued match, and the fingerprint hash. This is what the existing `Dimension` type already models.

Currently `DimensionsMetadata` does not distinguish these roles. Adding the distinction is a gap-list item (section 6).

The pre-filter has a second benefit beyond clarity: it dramatically shrinks the rule set entering the build phase, which interacts with the prime-allocation strategy from section 3.3. A partition with 50 surviving rules can use primes 2..229; a global registry of 5000 rules cannot.

---

## 4. Worked trace

A small lattice exercising EXACT, RANGE, and don't-care, end-to-end through Build and Apply.

**Setup.** Context-key partition: `product_id = 1`. Three rules in the registry for this partition:

| Rule | channel (EXACT) | lvr (RANGE) | foreign_resident (EXACT) | margin |
|---|---|---|---|---|
| R₁ | BROKER | [60, 80) | * (don't care) | −0.10 |
| R₂ | * (don't care) | [70, 90) | false | −0.05 |
| R₃ | BROKER | * (don't care) | false | −0.15 |

The Build phase for partition `product_id=1` allocates primes locally to the surviving rules: R₁ → 2, R₂ → 3, R₃ → 5.

### Anchor (level 0)

Three singleton rulesets, each with its own state as the coalesced state:

| combo | channel | lvr | foreign | margin | prime_product | fingerprint |
|---|---|---|---|---|---|---|
| {R₁} | BROKER | [60, 80) | * | −0.10 | 2 | h(BROKER, [60, 80), *) |
| {R₂} | * | [70, 90) | false | −0.05 | 3 | h(*, [70, 90), false) |
| {R₃} | BROKER | * | false | −0.15 | 5 | h(BROKER, *, false) |

### Recursive iteration 1

Try every (LHS, RHS) pair where `LHS.id < RHS.id` (the canonical-ordering guard) and the three-valued match conditions hold on every dimension:

- **R₁ + R₂.** channel: BROKER vs * → compatible, coalesce = BROKER. lvr: [60, 80) ∩ [70, 90) → compatible, coalesce = [70, 80). foreign: * vs false → compatible, coalesce = false. ✅ Emit `{R₁,R₂}`, margin = −0.15, prime = 6, fingerprint = h(BROKER, [70, 80), false).
- **R₁ + R₃.** channel: BROKER = BROKER → ✅. lvr: [60, 80) vs * → coalesce = [60, 80). foreign: * vs false → coalesce = false. ✅ Emit `{R₁,R₃}`, margin = −0.25, prime = 10, fingerprint = h(BROKER, [60, 80), false).
- **R₂ + R₃.** channel: * vs BROKER → coalesce = BROKER. lvr: [70, 90) vs * → coalesce = [70, 90). foreign: false = false → ✅. ✅ Emit `{R₂,R₃}`, margin = −0.20, prime = 15, fingerprint = h(BROKER, [70, 90), false).

### Recursive iteration 2

Extend each level-1 combination by one more rule:

- `{R₁,R₂}` + R₃. LHS coalesced state is (BROKER, [70, 80), false). R₃ is (BROKER, *, false). All three dimensions compatible. Emit `{R₁,R₂,R₃}`, margin = −0.30, prime = 30, fingerprint = h(BROKER, [70, 80), false).
- `{R₁,R₃}` + R₂ and `{R₂,R₃}` + R₁ would converge on the same `{R₁,R₂,R₃}` combination, but the canonical-ordering guard suppresses them — only one path through the lattice produces each combination.

### All emitted rows (levels 0 + 1 + 2)

| combo | fingerprint | prime | margin |
|---|---|---|---|
| {R₁} | h(BROKER, [60, 80), *) | 2 | −0.10 |
| {R₂} | h(*, [70, 90), false) | 3 | −0.05 |
| {R₃} | h(BROKER, *, false) | 5 | −0.15 |
| {R₁,R₂} | h(BROKER, [70, 80), false) | 6 | −0.15 |
| {R₁,R₃} | h(BROKER, [60, 80), false) | 10 | −0.25 |
| {R₂,R₃} | h(BROKER, [70, 90), false) | 15 | −0.20 |
| {R₁,R₂,R₃} | h(BROKER, [70, 80), false) | 30 | −0.30 |

### Outermost-frontier filter

Group by fingerprint, keep only combinations not dominated by another (under prime divisibility):

- `h(BROKER, [60, 80), *)` — only `{R₁}`. Outermost.
- `h(*, [70, 90), false)` — only `{R₂}`. Outermost.
- `h(BROKER, *, false)` — only `{R₃}`. Outermost.
- `h(BROKER, [70, 80), false)` — `{R₁,R₂}` (prime 6) and `{R₁,R₂,R₃}` (prime 30). 30 % 6 = 0, so `{R₁,R₂}` is dominated and dropped. `{R₁,R₂,R₃}` survives.
- `h(BROKER, [60, 80), false)` — only `{R₁,R₃}`. Outermost.
- `h(BROKER, [70, 90), false)` — only `{R₂,R₃}`. Outermost.

### Final lattice (six outermost rulesets)

| combo | fingerprint | margin |
|---|---|---|
| {R₁} | h(BROKER, [60, 80), *) | −0.10 |
| {R₂} | h(*, [70, 90), false) | −0.05 |
| {R₃} | h(BROKER, *, false) | −0.15 |
| {R₁,R₂,R₃} | h(BROKER, [70, 80), false) | −0.30 |
| {R₁,R₃} | h(BROKER, [60, 80), false) | −0.25 |
| {R₂,R₃} | h(BROKER, [70, 90), false) | −0.20 |

This is the artefact of the Build phase. It is partition-scoped (built for `product_id=1`) and context-free.

### Apply phase

A context arrives: `product_id=1, channel=BROKER, lvr=75, foreign_resident=false`. The Apply phase matches it against each fingerprint in the lattice (this is filter-engine-style matching, where each fingerprint is structurally a degenerate rule):

- `{R₁}` — channel BROKER ✓, lvr 75 ∈ [60, 80) ✓, foreign * ✓. **Match.**
- `{R₂}` — channel * ✓, lvr 75 ∈ [70, 90) ✓, foreign false ✓. **Match.**
- `{R₃}` — channel BROKER ✓, lvr * ✓, foreign false ✓. **Match.**
- `{R₁,R₂,R₃}` — channel BROKER ✓, lvr 75 ∈ [70, 80) ✓, foreign false ✓. **Match.**
- `{R₁,R₃}` — channel BROKER ✓, lvr 75 ∈ [60, 80) ✓, foreign false ✓. **Match.**
- `{R₂,R₃}` — channel BROKER ✓, lvr 75 ∈ [70, 90) ✓, foreign false ✓. **Match.**

**The Apply phase returns *all* matching outermost rulesets.** It does not select among them. Selection is a downstream concern — see section 5 on engine composition. For this trace, that downstream selection (using the filter engine over the matched outermost set as input) would presumably pick `{R₁,R₂,R₃}` with margin −0.30 as the deepest applicable accumulated combination, but the choice is not the accumulator's responsibility.

---

## 5. Architectural comparison

The accumulator engine has two phases. The filter engine has one. This is the spine of the comparison.

- **Build (accumulator only).** Context-free. Traverses the rule registry for a partition, emits the lattice of outermost combinations. Cacheable. Amortised across many context queries.
- **Apply (accumulator only).** Context-bound. Matches a context against the fingerprints of the precomputed lattice and returns matching outermost rulesets.
- **Evaluate (filter engine).** Context-bound. Walks every rule against the context every time. No precomputation.

The accumulator's two-phase split is what enables its efficiency story: the expensive combinatorial work happens once per partition, then every per-context query is a cheap fingerprint lookup. The filter engine, by contrast, must re-walk all rules for every context. For small rule sets and one-shot queries the filter engine wins on simplicity; for large rule sets and repeated queries against the same partition the accumulator wins on amortised cost.

### Comparison table

| Axis | Filter engine (existing) | Accumulator engine (new) |
|---|---|---|
| **What a rule is** | A proposition: "given context C, does rule R fire?" | A partial constraint that composes with other partial constraints |
| **Phases** | Single phase: Evaluate | Two phases: **Build** (context-free) → **Apply** (context-bound) |
| **Where the context lives** | Input to the only phase | Absent in Build; input to Apply |
| **Output unit** | Match decision per rule (or filtered subset) | Outermost ruleset(s) per fingerprint, each carrying coalesced constraints + accumulated numerics + provenance |
| **Inter-rule state** | None — rules evaluated independently | Coalesced attribute fingerprint accumulates across recursive joins |
| **Composition model** | None at evaluation time | Power-set traversal pruned by mutual compatibility; canonical ordering prevents duplicates |
| **Cardinality** | O(N) outputs for N rules | Build: up to O(2ᴺ) intermediate, pruned to O(distinct fingerprints) outermost. Apply: O(matching outermost) |
| **Per-context cost** | O(N · D) every call | O(lookup) against precomputed lattice |
| **Cacheability** | None — must re-evaluate per context | Lattice cached per partition, amortised across many contexts |
| **Lattice partition** | N/A | Natural-key fields (e.g. `product_id`) partition the lattice; one build per partition |
| **Identity / DNA** | N/A | Prime product (multiset-safe; bitset deferred — see §3.3 and §7) |
| **Outermost-frontier filter** | N/A | Pareto frontier under subset dominance — keeps maximal combinations per fingerprint namespace |
| **Per-dimension operations needed** | `match(rule_dim, context_value)` | `coalesce(dim_a, dim_b)`, `compatible(dim_a, dim_b)`, `fingerprint_value(coalesced_dim)`. Apply phase additionally needs `match(fingerprint_dim, context_value)` — **same op the filter engine already implements** |
| **Aggregation** | None | Carries one or more named numerics per combination, summed monoidally across the recursive join |
| **Relationship to the other engine** | Stage 2 of the pipeline (rank/select among outermost rulesets) | Stage 1 of the pipeline (produce the outermost rulesets) |

### The engines compose — the accumulator builds, the filter ranks

The two engines reconverge in two places:

1. **The Apply phase reuses filter-engine machinery.** Matching a context against a fingerprint is structurally identical to the filter engine matching a context against a rule. Fingerprints are degenerate rules: a flat tuple of per-dimension constraints with no inter-dimension state. The shared metadata layer already supports this; the gap list (section 6) only needs an explicit don't-care sentinel that survives serialisation through the lattice.
2. **Selection among multiple Apply matches is itself a filter-engine problem.** When the Apply phase returns several outermost rulesets (as in the worked trace, where six fingerprints match the example context), the user-defined ranking — "pick the deepest", "pick the largest absolute margin", "pick the one whose fingerprint matches the most non-don't-care dimensions", or any business rule the team wants — is exactly what the filter engine is for. Each outermost ruleset becomes a row in a small temporary "rule" table, with its computed metadata (combination size, depth, accumulated margin, fingerprint specificity) as dimensions, and the filter engine picks among them via user-defined rules.

The accumulator and filter engines are not alternatives. **The accumulator builds; the filter ranks.** They are the two stages of a pricing pipeline, and the shared metadata layer is the wire between them.

---

## 6. Shared metadata contract — current state and gaps

Prescriptive-minimal: what already exists, what is needed, no API design.

### 6.1 Already shared (no work needed)

- `Dimension`, `DimensionsMetadata`. Describe the vocabulary of constraints. Both engines consume as-is.
- `MatchStrategy` enum (`EXACT`, `RANGE`, `REGEX`, `PREFIX`, `SUFFIX`, `CONTAINS`, `NOT_EQUAL`, `GREATER_THAN`, `LESS_THAN`). Accumulator needs the same set.
- **Per-dimension ternary match encoding** (`1` match, `0` unknown, `−1` non-match). Defined in `constants.py`, consumed by `compiler.py` and `result.py`. The accumulator reuses this encoding inside its three-valued recursive-join compatibility check.

### 6.2 Gaps the accumulator engine needs

1. **Stable rule identity** (not a globally-allocated prime). Rules must be hashable/keyable so the build phase can deduplicate, the provenance trail can refer back, and the build phase can allocate **primes locally per partition** on top of identity. Rules already have an identifier in practice; this gap is about formalising it as part of the contract rather than introducing a new field.
2. **Explicit don't-care sentinel per rule dimension.** The accumulator must distinguish "rule says nothing about this dimension" (don't-care) from "rule says null for this dimension" (a hard null match). The filter engine treats missing-as-wildcard implicitly. The accumulator's coalesce, three-valued match, fingerprint hashing, and Apply-phase context matching all need an **explicit sentinel** that survives serialisation through the lattice rows. Both engines benefit: the filter engine's wildcard semantics become explicit instead of implicit.
3. **`coalesce(dim_a, dim_b)` per dimension type.** The dimension-by-dimension operation that produces a new constraint from two compatible constraints. EXACT: pick the non-don't-care; error on conflicting hard values. RANGE: interval intersection. REGEX/PREFIX/SUFFIX/CONTAINS: pattern AND (precise semantics deferred to the implementation phase). NOT_EQUAL/GREATER_THAN/LESS_THAN: range-like coalesce. New op; the filter engine never needs to combine rules.
4. **`compatible(dim_a, dim_b)` per dimension type.** Used by the recursive-join match condition. EXACT: equal-or-either-don't-care. RANGE: intervals overlap or either don't-care. Etc. The filter engine asks "does this rule speak about this context"; the accumulator asks "do these two rules speak compatibly about the same hypothetical context". Genuinely new op.
5. **Fingerprint hash function.** A stable hash over the coalesced dimension state of a combination, used to namespace the outermost-frontier filter. Each `MatchStrategy` type needs to expose a canonical hashable representation of a coalesced value, including the don't-care sentinel.
6. **Aggregation accessor on rules.** Rules need to expose zero-or-more named aggregatable numerics (the SQL has `margin_value` and `margin_value_desk`, generalisable). The build phase sums them monoidally across the recursive join. The filter engine doesn't care about rule numerics today; this is additive on the rule contract.
7. **Context-key vs dimension role on `Dimension`.** Per section 3.5, `Dimension` needs to declare whether it participates in coalesce/fingerprint (a true dimension) or partitions the lattice outer-loop (a context-key field). Currently `DimensionsMetadata` has no such distinction. The filter engine can ignore the role; the accumulator needs it.

### 6.3 Reconvergence — not a gap, an opportunity

The Apply phase of the accumulator matches contexts against fingerprints, which is structurally identical to the filter engine matching contexts against rules. The same `match(dim, context_value)` operation services both. **The Apply phase should reuse the filter engine, not reimplement it.** This is already supported by the shared metadata layer once gap #2 (explicit don't-care sentinel) is closed.

---

## 7. Open questions for the implementation phase

1. **Are multiset combinations possible in the lattice?** The SQL's `cell_id < cell_id` ordering prevents the same cell from being added twice within one recursive step, but it is not obvious whether different recursive paths through the lattice can converge on a state where the same rule contributes more than once. Resolving this empirically against a real production rule corpus determines whether the bitset DNA optimisation (section 3.3) is available. Until resolved, primes are the only safe representation.

2. **Prime overflow strategy and the fallback ladder.** The combination prime product can grow large for deep combinations. The implementation phase should adopt a tiered representation:
    - **Tier 1 — int64 backend.** If the build-phase pre-estimate `sum(log2(p_i) × max_multiplicity_i)` over surviving rules is < 62 bits, stay in polars/ibis with `Int64`. Vectorised, fast.
    - **Tier 2 — int128 backend.** If 62–126 bits, use DuckDB's `HUGEINT` via ibis. Still vectorised, larger headroom.
    - **Tier 3 — Python arbitrary precision.** Numpy `object` dtype arrays hold native Python ints, which are unbounded. Slower per-element dispatch but correct for any rule count. The escape hatch when even int128 is insufficient.

   The choice should be made automatically by a pre-build heuristic based on rule count and estimated max combination depth, with a manual override for users who want to force a particular tier.

3. **Apply-phase tie-breaking is undefined and out of scope.** The accumulator's Apply phase returns *all* matching outermost rulesets. Selection among them is the user's responsibility, and the recommended pattern is to use the filter engine as a second stage with user-defined ranking rules over the outermost set. The accumulator should not attempt to rank, score, or pick a winner. This is a design commitment, not just an open question.

4. **Coalesce and compatible semantics for pattern-style strategies.** EXACT and RANGE have obvious coalesce/compatible operations. REGEX, PREFIX, SUFFIX, and CONTAINS are harder: pattern AND is straightforward in principle but the canonical representation of the AND of two regexes is non-trivial, and overlap testing for two arbitrary regexes is undecidable in the general case. The implementation phase should decide whether to (a) restrict the accumulator to a subset of `MatchStrategy` values, (b) implement conservative approximations for pattern strategies, or (c) require pattern-typed dimensions to be context-key fields rather than coalesced dimensions.

5. **Build-phase materialisation strategy.** The SQL materialises the entire lattice into a table-valued function result. The Python engine has more options: lazy polars frames, materialised dataframes, on-disk cache (parquet) keyed by partition + rule registry hash, in-process LRU. The right default is unclear without performance data on realistic rule corpora.

6. **Rule registry change invalidation.** Once a partition's lattice is built and cached, what invalidates it? Any rule add/remove/edit affecting that partition's rule set, presumably — but the filter engine has no concept of "partition affecting" because it has no cache. The implementation phase should design an invalidation protocol that does not require the filter engine to know about it.

---

## 8. Closing note

This document deliberately stops at analysis. The next step, if and when the team commits, is a design spec that picks specific representations for each gap-list item, an implementation plan that sequences the work, and a benchmark suite that validates the amortised-cost claim against a realistic rule corpus.

The single most important takeaway is the composition story: **the accumulator builds; the filter ranks.** Whatever shape the accumulator takes in code, it should be designed so that its Apply-phase output flows naturally into a filter-engine query, because the two engines together describe the entire pricing pipeline — and neither makes complete sense without the other.
