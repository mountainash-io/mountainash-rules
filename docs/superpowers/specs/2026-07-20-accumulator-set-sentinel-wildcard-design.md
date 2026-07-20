# Accumulator Set-Wildcard: In-Band Sentinel Redesign — Design Spec

**Status:** Design (not yet implemented)
**Date:** 2026-07-20
**Supersedes:** the `SET_MEMBERSHIP`/`SET_EXCLUSION` coalescing portion (A1) of `2026-07-19-accumulator-core-extensions-design.md`. The A2 aggregate-ops portion of that spec shipped independently (PR #51 → `develop`) and is unaffected.

## Problem

A1 set-coalescing encoded a set-dimension **wildcard as a null list** (matching mountainash's `t_is_in`, where a null rule list scores ternary 0). The whole-branch review found this breaks the accumulator build's frontier-dominance pruning:

`_frontier_filter` (`engines/accumulator/engine.py`) removes dominated combinations via a self-join on the `co_`/`na_` fingerprint columns, which include the list-typed `co_<field>`. For a set wildcard that column is **null**, and polars inner joins drop null keys (`join_nulls=False`, which mountainash's backend-agnostic `join` API does not expose — null-safe join is a polars-only flag, absent on narwhals and false-by-default in ibis/SQL). So any combination whose set dimension coalesced to a wildcard is **never detected as dominated and survives**.

**Verified:** three all-wildcard `SET_MEMBERSHIP` rules produce combinations `{2,3,5,6,10,15,30}` instead of the single maximal `{30}`; `apply` returns 7 survivors instead of 1, with wrong accumulated aggregates. The identical shape with an EXACT-wildcard (concrete `UNKNOWN` sentinel) dimension correctly collapses to `{30}`.

**Root cause:** using `null` as a *semantic marker*. Null behaves inconsistently across backends in exactly the operations where a marker is compared (join / group / membership). This violates the mountainash-central principle [`null-is-not-a-portable-sentinel`](../../../../mountainash-central/01.principles/mountainash/d.cross-backend/core/null-is-not-a-portable-sentinel.md). The fix is to represent the wildcard with an in-band typed **sentinel**, never a null — consistent with the scalar `UNKNOWN`/`NOT_SET` convention the rest of the engine already uses.

## Goals

- Set-dimension wildcards are represented by a non-null, in-band typed sentinel across **input, filter, and build** (scope B — a unified representation end to end).
- The frontier self-join dedupes wildcard-set combinations correctly; `apply` returns correct survivors and aggregates.
- No new native backend imports, no new `# allow:` tags; no mountainash change required.
- Backward-compatible: rules that express a wildcard as a null list are **normalized**, never rejected.
- The pre-existing set-op ordering nondeterminism (equal sets → distinct fingerprints) is fixed in the same stroke.

## Non-Goals

- Changing the A2 aggregate-ops behaviour (already shipped).
- Any change to `_frontier_filter`, `_check_overflow`, partition routing, or scalar/range/string strategies.
- Upstream mountainash changes (a separate backlog item 58 tracks removing the one `to_polars()` seam; not required here).

## Canonical Representation

A set-dimension **wildcard** is the single-element list `[unknown_sentinel_for(dim.data_type)]`:

| data_type | wildcard list |
|---|---|
| str | `["<NA>"]` |
| int | `[-999999999]` |
| float | `[-999999999.0]`* |
| date | `[date(1,1,1)]` |
| datetime | `[datetime(1,1,1)]` |

\* whatever `unknown_sentinel_for(float)` returns; the spec uses the existing `unknown_sentinel_for` — no new sentinels are introduced.

The sentinel is a reserved, out-of-domain value — the same assumption the scalar `UNKNOWN` wildcard already relies on, so a concrete rule list never contains it. An **empty** concrete list `[]` keeps its concrete meaning (membership `[]` = matches nothing; exclusion `[]` = excludes nothing) and is cleanly distinct from `[sentinel]` — the empty-vs-wildcard ambiguity of the old design disappears.

Feasibility verified: `ma.lit([sentinel])` produces a `List(element)`-typed literal; `when(is_null).then(lit([sentinel])).otherwise(col)` normalizes null → `[sentinel]` and preserves dtype; `col.list.contains(lit(sentinel))` detects it. All backend-pure (route through `mountainash.expressions`), no `# allow:` tag.

## Shared Helpers (`core`)

Three helpers live in a **new shared module `core/set_wildcard.py`** (they build `mountainash.expressions` and need `unknown_sentinel_for` from `core/constants.py`) imported by both `core/compiler.py` (filter) and `engines/accumulator/compiler.py` — so the two engines cannot diverge and the accumulator does not reach into filter-compiler internals:

```python
def canonicalize_set_expr(col):
    """Sort + dedupe a list column so equal sets compare/fingerprint identically."""
    return col.list.unique().list.sort()

def normalize_set_expr(dim, col):
    """Null → [sentinel]; canonicalize concrete lists. The single ingestion normaliser."""
    sent = unknown_sentinel_for(dim.data_type)
    return ma.when(col.is_null()).then(ma.lit([sent])).otherwise(canonicalize_set_expr(col))

def set_wildcard_predicate(dim, col):
    """True when `col` (post-normalization, non-null) is the wildcard. Detection signal."""
    return col.list.contains(ma.lit(unknown_sentinel_for(dim.data_type)))
```

`set_wildcard_predicate` assumes its input is already normalized (non-null); `list.contains` returns null on a null cell, so detection is always evaluated after normalization.

**Normalization sequencing:** `normalize_set_expr` MUST run at ingestion, before any detection/coalesce/fingerprint. This is what makes every downstream predicate see only non-null lists.

## Filter Engine (`core/compiler.py`)

`_compile_set_membership` / `_compile_set_exclusion` wrap the existing ternary call with a wildcard short-circuit, operating on the normalized rule column:

```python
def _compile_set_membership(self, dim):
    rule_col = normalize_set_expr(dim, ma.col(dim.resolved_rule_field))
    ctx_col  = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels_for(dim.data_type))
    is_wild  = set_wildcard_predicate(dim, rule_col)
    return ma.when(is_wild).then(0).otherwise(ctx_col.t_is_in(rule_col))
```

`_compile_set_exclusion` is identical with `t_is_not_in`; a wildcard exclusion rule ("excludes nothing") is likewise ternary 0.

- **Self-normalizing inline** — a standalone `ExpressionRulesEngine` over set rules is correct with no separate rules-frame pass. Null rule lists in input still behave as wildcards (normalized → `[sentinel]` → `is_wild` → 0): **backward-compatible**.
- **No mountainash change** — `t_is_in`'s internal `collection.is_null()` clause simply never fires post-normalization (harmless, always false).
- Ternary contract preserved: wildcard → 0, context-in-set → 1, context-out-of-set → −1, context missing (via `t_col unknown=`) → 0.

## Accumulator Build (`engines/accumulator/`)

The fix is in the **data**, not the join — so `_frontier_filter` is **untouched** (its inputs become null-free and canonical).

1. **Ingestion normalization (materialized).** After `relation(rules).to_polars()`, apply `normalize_set_expr` to each set dimension's rule list column, so every `co_<field>` starts non-null and canonical before the anchor and any coalesce. This is the step that fixes the frontier bug.
2. **Wildcard detection.** The accumulator compiler keeps its strategy-dispatching `wildcard_predicate(dim, col)` helper: the `SET_MEMBERSHIP`/`SET_EXCLUSION` branch delegates to `set_wildcard_predicate` (not `is_null()`), and the scalar/range branches keep the existing sentinel-equality test unchanged. Same set helper the filter engine uses.
3. **`_coalesce_set(dim, op)`:**
   - detection via `set_wildcard_predicate`;
   - both-wildcard branch → `ma.lit([sentinel])` (replaces the old `.then(co)` typed-null);
   - co-wildcard → `rhs`; rhs-wildcard → `co`;
   - both-concrete → `canonicalize_set_expr(set_intersection|set_union)` so equal coalesced sets share a fingerprint.
   Membership uses `set_intersection`; exclusion uses `set_union`. (Wildcard is the identity for both: `∩` with match-anything, `∪` with exclude-nothing.)
4. **`_compatible_set_membership`:** `co_wild OR rhs_wild OR (intersection nonempty)` with `set_wildcard_predicate` for the wildcard tests; concrete∩concrete-nonempty unchanged. `SET_EXCLUSION` remains always-compatible (`ma.lit(True)`).
5. **`co_<field>_na` flag** = the `set_wildcard_predicate` result (emitted for provenance/apply, as today).
6. **`_frontier_filter`: unchanged.** Inputs are now null-free and canonical, so the self-join dedupes wildcard combinations, and equal-but-differently-ordered sets dedupe too.

**Apply consistency (free).** After build, the lattice's `co_` columns *are* the normalized representation. Apply runs the filter compiler (above) over them; `set_wildcard_predicate` short-circuits identically. `normalize_set_expr` is **idempotent** — re-applying it to already-normalized `co_` columns is a no-op (the `is_null` branch never fires; canonicalizing an already-canonical list is stable) — so the filter compiler's inline normalization is safe over lattice columns. One representation, both engines, build → apply.

## Data Flow

```
rule list column (may be null / unordered)
        │  normalize_set_expr  (ingestion — both engines)
        ▼
[sentinel] for wildcard  |  sorted-unique list for concrete   (never null)
        │
   ┌────┴───────────────────────────────┐
   ▼ filter compiler                     ▼ accumulator build
 is_wild? → ternary 0                  co_<field> (non-null, canonical)
 else t_is_in → 1 / −1                   │ coalesce (sentinel-aware) → canonical
                                         │ co_<field>_na = is_wild
                                         ▼ _frontier_filter (UNCHANGED) — dedupes correctly
                                         ▼ apply → filter compiler over co_ columns (same is_wild path)
```

## Testing

The old A1's fatal flaw was tests asserting only dtype / `count >= 1`. The new suite asserts **exact** results so a broken build fails loudly.

- **Shared helpers:** `normalize_set_expr` (null→`[sentinel]`, concrete sorted-unique, dtype preserved); `canonicalize_set_expr` (`["UK","NZ"]`→`["NZ","UK"]`, dupes removed); `set_wildcard_predicate` (True on `[sentinel]`, False on concrete incl. `[]`).
- **Filter compiler:** membership/exclusion ternary — wildcard rule → **0**, context-in-set → **1**, out-of-set → **−1**, **null rule-list input → normalized → 0** (backward-compat), context-missing → 0.
- **Accumulator compiler:** coalesce both-wildcard → `[sentinel]`; wildcard-passthrough → concrete side; both-concrete → canonicalized (asserts sorted-unique) intersection (membership) / union (exclusion); `_compatible_set_membership` empty concrete∩concrete → not compatible.
- **Anchor regression (would have caught the bug):** a 3-rule all-wildcard-set build asserts `__prime_product == {30}` (NOT `{2,3,5,6,10,15,30}`); a mixed build asserts exact survivors + accumulated values.
- **Ordering:** two rules with the same set in different order → identical fingerprint → deduped to one combination.
- **Apply round-trip:** context in the `{R1,R2}` intersection matches prime-product 6; a wildcard combination matches any context — **exact** survivor counts, never `>= 1`.
- **Backend purity:** `tests/test_backend_purity.py` green; no new imports, no new `# allow:` tags.

## Compatibility & Migration

- **Backward-compatible input:** null rule lists are normalized at ingestion; no change to how rules are authored. Documented fast path: supply `[sentinel]` (or any concrete list) at source to skip the fill.
- **Independent of A2** (shipped, PR #51). Small `engine.py` overlap only.
- **Docs:** update `CLAUDE.md` match-strategy table — `set_membership`/`set_exclusion` are accumulator-coalesceable (membership→intersection, exclusion→union) with an **in-band `[sentinel]` wildcard** (never null); note the `null-is-not-a-portable-sentinel` principle.
- **`mountainash#89`** already covers narwhals list ops on the apply side; no new xfail group.
