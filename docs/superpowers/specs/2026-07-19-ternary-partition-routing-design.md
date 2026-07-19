# Ternary Partition Routing — Design Spec

> **Status:** APPROVED (design walkthrough 2026-07-19; amended same day after
> Codex adversarial review — see §9 Review adjudication)
> **Source:** `mountainash-central/01.principles/mountainash-rules/h.backlog/ternary-partition-routing.md`
> **Repo:** `mountainash-rules`, branch `feature/ternary-partition-routing` (off `develop` @ `496f84a`)

## Problem

Ternary match logic (1 = match, 0 = wildcard, −1 = non-match; survive on
`min ≥ 0`; rank by specificity) operates only at the CONSTRAINT-dimension
level. CONTEXT_KEY partition routing is the opposite — an exact dict lookup:
`LatticeIndex._map[key]` either hits or raises `KeyError`. Consequences:

1. **No default / overflow partition.** A context whose key was never built
   has nowhere to go but an error.
2. **Unreachable-wildcard footgun.** `build_all` groups rules by raw unique
   CONTEXT_KEY values, so a rule keyed on the UNKNOWN sentinel builds a
   partition that exact routing can never serve — silently dead.
3. **Runtime-only failure surface.** A hand-assembled or evolved suite of
   partitions can contain logically ambiguous overlaps that nothing detects
   until a context happens to hit them.

## Design summary

Generalise `LatticeIndex` routing to the same ternary + specificity semantics
as the constraint layer, implemented **by** the constraint layer: partitions
become rows of an internal meta rules-table evaluated by an embedded
`ExpressionRulesEngine`. An all-wildcard key is the default/overflow
partition. Exact hits keep an O(1) dict fast path. `index()` validates the
partition suite for ambiguity at build/load time via an exhaustive,
vectorised witness-matrix check. `apply_batch` routes through the same shared
resolver.

## 1. Routing semantics

Per CONTEXT_KEY dimension, context value vs partition key value:

| Partition key value | Context value | Ternary |
|---|---|---|
| specific `v` | `v` | **1** |
| specific `v` | anything else (incl. NOT_SET) | **−1** |
| UNKNOWN sentinel (typed, per `data_type`) | anything | **0** (wildcard) |

- A partition **survives** when its minimum ternary over key dims is ≥ 0.
- **Specificity** = count of 1s. Route to the unique top-specificity
  survivor.
- **All-wildcard key = default/overflow partition**: survives every context
  at specificity 0, wins only when nothing more specific survives.
- A context value equal to the dim's UNKNOWN sentinel is treated as unknown:
  it matches wildcard keys (0), never specific keys (−1).
- **These semantics are asymmetric** — only the *rule side* (partition key)
  has a wildcard; a context-side sentinel is a non-match against any specific
  key. The stock EXACT compile does **not** deliver this: `_compile_exact`
  uses `t_eq` with both UNKNOWN and NOT_SET in the sentinel set on *both*
  sides, so a NOT_SET-filled context field would score 0 against every
  specific key — every partition would survive missing-field contexts and
  tie spuriously. Routing therefore uses a dedicated strategy, §2's
  `EXACT_KEY`.
- **Bool key dims:** the wildcard is `null` on the key side (bools have no
  in-band sentinel; `_compile_bool_ternary` already uses null as don't-care).
  A context-side `None` is wildcard-only, mirroring NOT_SET.

**Missing context key fields.** `_extract_partition_key` fills a missing
context field with the typed NOT_SET sentinel
(`not_set_sentinel_for(d.data_type)`; `None` for bool) instead of raising
`KeyError` — mirroring `core/context.py`'s fill for constraint dims. An
**explicitly-null** context value (`region: None`, backend null/NaN) is
treated identically to an absent field. A NOT_SET value matches wildcard
keys only, so a context with no `region` routes to the default partition if
one exists, else raises the no-partition error.

**Partition keys may not contain NOT_SET sentinels.** `index()` rejects any
lattice whose key contains a NOT_SET sentinel (`ValueError`, always-on —
independent of the `validate` flag). Without this, a NOT_SET-filled context
tuple could exact-dict-hit such a key and route a missing-field context to a
specific partition, violating the semantics above. NOT_SET keys cannot arise
from `build_all` (it groups on rule values, and NOT_SET is a context-side
sentinel); this guards hand-assembled suites.

**Outcomes of routing one context:**

| Survivors at top specificity | Result |
|---|---|
| exactly 1 | apply that lattice |
| 0 survivors at all | `KeyError` — message lists the served partition keys |
| ≥ 2 | `AmbiguousPartitionError` — names the tied partitions + the context |

**`AmbiguousPartitionError(KeyError)`** is a new exception exported from the
package root. Subclassing `KeyError` keeps every existing `except KeyError`
consumer working — in particular `mountainash-rules-service`'s partition-miss
handler maps both outcomes to HTTP 422 with **zero service change**. The
no-survivor case deliberately stays a plain `KeyError` (same type as today).

## 2. Architecture — partitions as a meta rules-table

`LatticeIndex.__init__` builds, once:

- **`_map`** — the existing exact dict, unchanged. Fast path.
- **Meta rules-table** — one row per partition; one column per CONTEXT_KEY
  dim holding that partition's key value verbatim (UNKNOWN sentinel already
  means wildcard in-band — no encoding step); plus a `__partition_idx`
  integer payload column mapping a surviving row back to its `Lattice`.
- **Meta-engine** — an internal `ExpressionRulesEngine` over the meta-table.
  Each CONTEXT_KEY dim is recast as an **`EXACT_KEY`**-strategy CONSTRAINT
  dimension: same `dimension_name` and `data_type`, `rule_field` = the
  meta-table column, `context_field` = the original dim's
  `resolved_context_field`. Hit policy COLLECT (selection is done by the
  resolver, not a policy).

**`MatchStrategy.EXACT_KEY`** — a new strategy added via the established
extension path (enum member in `core/constants.py`, validation in
`core/dimension.py`, `_compile_exact_key` in `core/compiler.py`, test class
in `tests/core/test_compiler.py`). Semantics: **rule-side wildcard only** —

```
rule value == UNKNOWN sentinel  → 0
rule value == context value     → 1
otherwise                       → −1
```

i.e. `t_col(rule_field, unknown={unknown_sentinel_for(dt)}).t_eq(col(ctx))`
— the rule side recognises only the UNKNOWN sentinel (not NOT_SET, which is
forbidden in keys anyway), and the context side is a plain column, so
context-side sentinels compare as ordinary non-matching values → −1. Bool
variant mirrors `_compile_bool_ternary` but tests **rule-side null only**.
This is the same one-engine architecture — `EXACT_KEY` is a first-class
strategy any consumer may use, compiled and evaluated by the single
constraint pipeline; routing just happens to be its first consumer.

Routing = `meta_engine.evaluate(context)`; the survivor frame's
`__specificity` column drives winner / tie / miss detection. There is **one**
implementation of ternary matching in the package; partition routing now uses
it instead of paralleling it.

**Backend purity.** The meta-table is constructed from Python dict-of-lists
via `mountainash.relations.relation({...})` — verified 2026-07-19 to accept
plain dicts and produce a `Relation` backend-agnostically. **No new
`# allow:` tag is needed**; the purity count stays at three.

## 3. `apply` flow

```
key = engine._extract_partition_key(context)      # NOT_SET-filled
if key in self._map:                              # exact fast path, O(1)
    return engine.apply(self._map[key], context, dimensions=...)
lattice = self._route(context)                    # meta-engine evaluate
return engine.apply(lattice, context, dimensions=...)
```

The exact fast path is semantics-preserving, including for keys containing
UNKNOWN wildcards. Proof sketch: a dict hit means the context tuple equals
key `K` exactly (keys cannot contain NOT_SET — §1 — so NOT_SET-filled
contexts never false-hit). Partition `K` scores 1 on each of its specific
dims and 0 on its wildcard dims → specificity = |specific dims of K|. Any
competitor must be wildcard on every dim where `K` is wildcard (the context
carries the UNKNOWN sentinel there, which kills specific keys at −1), and
scores ≤ 1 elsewhere — so its specificity ≤ `K`'s, with equality only for an
identical key, which the dict (plus §5 duplicate detection) rules out. `K`
is the unique top-specificity survivor. A wildcard-free index never reaches
`_route`, so today's behaviour is byte-identical for existing users.

`_route(context)` — the shared resolver: evaluate on the meta-engine; apply
the outcome table from §1.

## 4. `apply_batch` flow

Structure unchanged (global `__lattice_ctx_id`, unique key combos,
per-partition `evaluate_batch`, concat). The only change: each unique combo
resolves via `_route()` on a context assembled from the combo's key values
(dict hit first, meta-engine on miss) instead of a bare `self._map[key]`
lookup. Contexts whose combo routes to the same lattice are batched together
exactly as today; a combo that misses or ties raises the same errors as
`apply`. Combos are few relative to contexts, so routing cost is negligible.

The no-key-dims early path (single flat lattice) is unchanged.

## 5. Load-time ambiguity validation

`engine.index(lattices, validate=True)` — **on by default**.

**Structural checks — always on, independent of `validate` (O(n), no
matrix):** run over the input *list* before dict construction, because the
dict silently collapses duplicates and can never see them afterwards:

- `index([])` → `ValueError` (no lattices; also removes any ambiguity about
  the meta-table's schema seed).
- Duplicate partition keys across the input list → `ValueError` naming the
  key (previously one lattice was silently discarded by the dict).
- Any key containing a NOT_SET sentinel → `ValueError` (§1).

**Ambiguity check (`validate=True`):**

Naïve pairwise overlap detection would false-positive the legitimate
"more specific partition covers the crossing" pattern: `(AU, *)` vs
`(*, BROKER)` is genuinely ambiguous **unless** `(AU, BROKER)` also exists,
in which case it wins their entire overlap and no runtime tie is reachable.
The check is therefore exact, built on a finite abstraction:

1. Per key dim, reachable context values fall into finitely many
   equivalence classes: each specific value appearing in any partition key,
   plus one OTHER representative — **the dim's typed NOT_SET sentinel
   (`None` for bool)**. This is always constructible (no "fresh value"
   derivation, no finite-domain problem for bool) and provably matches no
   specific key: NOT_SET is forbidden in keys (§1) and under `EXACT_KEY` a
   context-side sentinel scores −1 against every specific value and 0
   against wildcards — exactly the OTHER class's behaviour. It also *is* a
   reachable context (a missing field), so every witness is a realisable
   input. Routing behaviour depends only on the class.
2. The cross-product of classes forms a **witness-context matrix** that
   provably exercises every distinguishable routing case.
3. Route the whole matrix through the meta-engine as one `evaluate_batch` —
   the validator *is* the router; no second logic to drift.
4. Any witness with ≥ 2 top-specificity survivors →
   `AmbiguousPartitionError` at `index()` time, naming the tied partitions
   and one witness context (e.g. `{region: 'AU', channel: 'BROKER'}`).
   Witnesses with zero survivors are fine (legitimate runtime miss).

Properties:

- **Duplicate keys** are caught by the structural pre-check above, *not* by
  the matrix — dict construction collapses them before the meta-table
  exists, so the matrix could never see a duplicate.
- Matrix size = `∏(distinct specific values per dim + 1)` — bounded by
  `(P+1)^D` for `P` partitions over `D` key dims, so it can explode for
  many-dimensional suites. Two guards: (a) the matrix is **evaluated in
  fixed-size chunks** (default 100 000 witnesses per `evaluate_batch`), so
  memory stays bounded regardless of total size; (b) a **witness-count cap**
  (default 1 000 000, exposed as `max_witnesses` on `index()`) above which
  `index()` raises `ValueError` telling the caller to pass `validate=False`
  (accepting the runtime backstop) or restructure the key dims. No silent
  sampling — validation is exact or explicitly declined.
- `validate=False` is the opt-out; the runtime tie check in `_route` remains
  as a backstop reachable only via that opt-out.
- **Service inherits load-time detection with no change:**
  `_load_partitioned` calls `engine.index(lattices)` inside its per-slug
  try/except, so an ambiguous suite is skipped with a warning at startup.

## 6. `build_all` — no change

A rule with UNKNOWN in a key field already produces a sentinel-keyed
partition; under the new routing that partition **is** the wildcard/default
instead of dead weight. The footgun is resolved by routing, not building.
`partition_key=None` / flat single-lattice cases are unchanged.

## 7. API surface changes

| Surface | Change |
|---|---|
| `LatticeIndex.apply` | wildcard + specificity routing; `KeyError` message now lists served partitions; may raise `AmbiguousPartitionError` |
| `LatticeIndex.apply_batch` | same routing per unique combo |
| `AccumulatorEngine.index` | gains `validate: bool = True`, `max_witnesses: int = 1_000_000`; raises `ValueError` on empty list, duplicate keys, NOT_SET-bearing keys, or witness-cap overflow |
| `AccumulatorEngine._extract_partition_key` | missing **or explicitly-null** context field → typed NOT_SET fill (`None` for bool) (was `KeyError`) |
| `MatchStrategy.EXACT_KEY` | new enum member — rule-side-wildcard-only exact match (§2); first-class strategy, usable by any consumer |
| `AmbiguousPartitionError` | new, subclasses `KeyError`, exported from package root `__all__` |
| `apply_auto` | inherits all of the above (delegates to `index().apply`) |

Not changed: `Lattice`, `Lattice.save/load`, `build`/`build_all`, all
constraint-layer behaviour, service code.

## 8. Testing

In `tests/accumulator/test_lattice.py` (new `TestTernaryRouting` /
`TestIndexValidation` classes):

**Routing (`apply`):**
- default (all-wildcard) partition catches an otherwise-unmatched context;
- specific partition beats the default for its key;
- multi-dim: `(AU, *)` vs `(*, BROKER)` with `validate=False`, context
  `(AU, BROKER)` → `AmbiguousPartitionError` at runtime;
- missing context key field → routes to default; with no default →
  `KeyError`;
- context carrying the UNKNOWN sentinel value → wildcard-only;
- wildcard-free index: identical behaviour to today (exact hit + exact miss
  `KeyError`) — fast-path regression guard.

**Batch (`apply_batch`):** the same matrix — mixed contexts routing to
specific + default partitions in one batch with correct per-context groups;
a batch containing an unroutable combo raises.

**Compiler (`tests/core/test_compiler.py`, new `TestExactKey` class):**
- rule UNKNOWN sentinel → 0 against any context value;
- rule specific vs equal context → 1; vs different → −1;
- rule specific vs context NOT_SET sentinel → **−1** (the asymmetry that
  distinguishes `EXACT_KEY` from `EXACT`);
- rule specific vs context UNKNOWN sentinel → −1;
- bool: rule null → 0; rule specific vs context null → −1.

**Validation (`index()`):**
- crossing pair, no cover → `AmbiguousPartitionError` at `index()`, message
  carries a witness context;
- crossing pair + covering `(AU, BROKER)` partition → validates clean AND
  routes correctly at runtime (the false-positive guard);
- duplicate partition keys in the input list → `ValueError` (even with
  `validate=False` — structural check);
- key containing a NOT_SET sentinel → `ValueError`;
- `index([])` → `ValueError`;
- witness count over `max_witnesses` → `ValueError` naming the opt-outs;
- bool key dim with both `True` and `False` as specific keys + a wildcard →
  validates clean; context `{flag: None}` routes to the wildcard (OTHER
  representative exists for finite domains);
- `validate=False` defers the crossing pair to the runtime error.

**Persistence:** `save` → `load` a wildcard-keyed partition suite →
`index()` → default routing works (sentinel round-trips through parquet +
manifest).

## 9. Review adjudication (Codex adversarial review, 2026-07-19)

Nine findings; eight accepted and folded into the sections above:

| Finding | Disposition |
|---|---|
| NOT_SET partition key can false-hit the fast path (critical) | Accepted — NOT_SET forbidden in keys, always-on check (§1, §5) |
| Duplicate keys collapse in the dict before validation | Accepted — structural pre-check over the input list (§5) |
| Wildcard routing via stock EXACT is unspecified | Accepted & confirmed against code (`_compile_exact` t_eq scores context NOT_SET as 0, not −1) — new `EXACT_KEY` strategy (§1, §2) |
| Witness matrix unbounded | Accepted — chunked evaluation + `max_witnesses` cap (§5) |
| OTHER representative undefined for bool | Accepted — OTHER = typed NOT_SET sentinel / `None` (§5) |
| Null context values unspecified | Accepted — explicit null ≡ missing (§1) |
| `index([])` undefined | Accepted — `ValueError` (§5) |
| Exact-hit specificity claim too broad | Accepted — proof rewritten to cover UNKNOWN-bearing keys (§3) |
| `AmbiguousPartitionError(KeyError)` → HTTP 422 misclassifies a config defect; startup skip hides it | **Rejected — deliberate tradeoff.** With `validate=True` default, ambiguity surfaces at `index()`/startup inside the service's existing per-slug skip-and-warn envelope (its standing failure mode for any bad suite). Runtime ambiguity is reachable only via explicit `validate=False`, at which point 422-on-tie is the same contract as today's partition miss. Zero-service-change wins; revisit if the service ever grows a config-health endpoint. |

## Non-goals

- Range/regex/other match strategies at the partition level — CONTEXT_KEY
  routing stays exact-or-wildcard (ternary over EXACT), matching what
  `build_all` can produce.
- Priority/ordering-based tie resolution — ties are an error by design
  (silent-wrong-results class); a modeller resolves them with a more
  specific partition.
- `LatticeIndex` save/load (standing non-goal; rehydrate via per-snapshot
  `Lattice.load` + `engine.index`).
- Service changes — the service is a pure delegator and inherits everything.
