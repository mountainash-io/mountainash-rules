# Ternary Partition Routing — Design Spec

> **Status:** APPROVED (design walkthrough 2026-07-19)
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
  it matches wildcard keys (0), never specific keys (−1) — consistent with
  the expression layer's in-band sentinel handling.

**Missing context key fields.** `_extract_partition_key` fills a missing
context field with the typed NOT_SET sentinel
(`not_set_sentinel_for(d.data_type)`) instead of raising `KeyError` —
mirroring `core/context.py`'s fill for constraint dims. A NOT_SET value
matches wildcard keys only, so a context with no `region` routes to the
default partition if one exists, else raises the no-partition error.

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
  Each CONTEXT_KEY dim is recast as an EXACT-strategy CONSTRAINT dimension:
  same `dimension_name` and `data_type`, `rule_field` = the meta-table
  column, `context_field` = the original dim's `resolved_context_field`.
  Hit policy COLLECT (selection is done by the resolver, not a policy).

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

The exact fast path is semantics-preserving: an exact hit matches every key
dim at 1, which is provably maximal specificity, and duplicate exact keys
cannot coexist (validation §5 / dict construction). A wildcard-free index
never reaches `_route`, so today's behaviour is byte-identical for existing
users.

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

Naïve pairwise overlap detection would false-positive the legitimate
"more specific partition covers the crossing" pattern: `(AU, *)` vs
`(*, BROKER)` is genuinely ambiguous **unless** `(AU, BROKER)` also exists,
in which case it wins their entire overlap and no runtime tie is reachable.
The check is therefore exact, built on a finite abstraction:

1. Per key dim, reachable context values fall into finitely many
   equivalence classes: each specific value appearing in any partition key,
   plus one OTHER representative (a fresh value matching no specific key —
   derived per `data_type`). Routing behaviour depends only on the class.
2. The cross-product of classes forms a **witness-context matrix** that
   provably exercises every distinguishable routing case.
3. Route the whole matrix through the meta-engine as one `evaluate_batch` —
   the validator *is* the router; no second logic to drift.
4. Any witness with ≥ 2 top-specificity survivors →
   `AmbiguousPartitionError` at `index()` time, naming the tied partitions
   and one witness context (e.g. `{region: 'AU', channel: 'BROKER'}`).
   Witnesses with zero survivors are fine (legitimate runtime miss).

Properties:

- **Duplicate keys** are the degenerate tie — caught by the same mechanism
  (dict construction also collapses them; validation makes it loud).
- Matrix size = `∏(distinct specific values per dim + 1)`; vectorised in one
  batch, trivial at realistic sizes. `validate=False` is the opt-out for
  pathological indexes; the runtime tie check in `_route` remains as a
  backstop reachable only via that opt-out.
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
| `AccumulatorEngine.index` | gains `validate: bool = True` |
| `AccumulatorEngine._extract_partition_key` | missing context field → typed NOT_SET fill (was `KeyError`) |
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

**Validation (`index()`):**
- crossing pair, no cover → `AmbiguousPartitionError` at `index()`, message
  carries a witness context;
- crossing pair + covering `(AU, BROKER)` partition → validates clean AND
  routes correctly at runtime (the false-positive guard);
- duplicate partition keys → caught;
- `validate=False` defers the crossing pair to the runtime error.

**Persistence:** `save` → `load` a wildcard-keyed partition suite →
`index()` → default routing works (sentinel round-trips through parquet +
manifest).

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
