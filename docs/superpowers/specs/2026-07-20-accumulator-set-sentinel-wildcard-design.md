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
| float | `[-999999999]` cast to `List(Float64)` (see typing note) |
| date | `[date(1,1,1)]` |
| datetime | `[datetime(1,1,1)]` |
| **bool** | **rejected — see below** |

An **empty** concrete list `[]` keeps its concrete meaning (membership `[]` = matches nothing; exclusion `[]` = excludes nothing) and is cleanly distinct from `[sentinel]` — the empty-vs-wildcard ambiguity of the old design disappears.

Feasibility verified: `ma.lit([sentinel])` produces a `List(element)`-typed literal; `when(is_null).then(lit([sentinel])).otherwise(col)` normalizes null → `[sentinel]` and preserves dtype; `col.list.contains(lit(sentinel))` detects it. All backend-pure (route through `mountainash.expressions`), no `# allow:` tag.

### Domain constraints (enforced, not assumed)

The wildcard sentinel is a **reserved** value, and the reservation is **validated**, not merely assumed (the scalar convention leaves it implicit; sets add a mixed-list failure mode that scalars don't have, so it must be enforced):

1. **`bool` set dimensions are rejected.** `unknown_sentinel_for(DataType.BOOL)` falls through to the string `"<NA>"` (no typed bool wildcard exists), and a set over `{true, false}` is degenerate. `Dimension` validation (`core/dimension.py`) raises `ValueError` when `match_strategy ∈ {SET_MEMBERSHIP, SET_EXCLUSION}` and `data_type == BOOL`. Fail-loud, never silently mis-typed.
2. **Concrete lists containing the sentinel are rejected at ingestion.** `normalize_set_expr` guards that a non-wildcard rule list does not contain `unknown_sentinel_for(dim.data_type)`. A list is the wildcard **iff it is exactly `[sentinel]`**; any *other* list containing the sentinel (e.g. `["AU", "<NA>"]`) is a data error and raises, so `list.contains(sentinel)` unambiguously means "wildcard". (The guard is a validation pass over the materialized rule frame at build ingestion and at filter-engine rule intake — see Failure Handling.)
3. **Element-level nulls in a concrete list are rejected at ingestion.** `[null]` / `["AU", null]` are data errors (sort/unique/membership/set-op semantics on element-nulls diverge across backends). Only the *whole-list* null (→ normalized to `[sentinel]`) is a valid wildcard.

### Typed sentinel literal

`unknown_sentinel_for(float)` currently returns the **int** `-999999999`. To avoid a `List(Int)` literal meeting a `List(Float)` column, the sentinel-list literal coerces its **element** to the dimension's Python type — **not** a native list-dtype cast. (A list-dtype cast is a non-goal: mountainash exposes no backend-agnostic list-dtype constructor, and casting a list literal requires a native `pl.List(...)`, which would violate backend purity — the same trap as the old null-cast. Verified: `ma.lit([-999999999.0])` with a Python-float element infers `List(Float64)` directly, and `float(-999999999) == -999999999` exactly.)

```python
def _typed_sentinel(dim):
    sent = unknown_sentinel_for(dim.data_type)
    return float(sent) if dim.data_type == DataType.FLOAT else sent

def sentinel_list_expr(dim):
    """A [sentinel] list literal whose element already carries the dim's Python
    type, so mountainash infers List(<element>) with no native cast (backend-pure)."""
    return ma.lit([_typed_sentinel(dim)])
```

All shared helpers below use `sentinel_list_expr(dim)`, never a bare `ma.lit([sent])`.

## Shared Helpers (`core`)

Three helpers live in a **new shared module `core/set_wildcard.py`** (they build `mountainash.expressions` and need `unknown_sentinel_for` from `core/constants.py`) imported by both `core/compiler.py` (filter) and `engines/accumulator/compiler.py` — so the two engines cannot diverge and the accumulator does not reach into filter-compiler internals:

```python
def canonicalize_set_expr(col):
    """Sort + dedupe a list column so equal sets compare/fingerprint identically."""
    return col.list.unique().list.sort()

def normalize_set_expr(dim, col):
    """Null → [sentinel]; canonicalize concrete lists. The single ingestion normaliser.

    Element-coerced sentinel via sentinel_list_expr(dim); no native cast.
    """
    return ma.when(col.is_null()).then(sentinel_list_expr(dim)).otherwise(canonicalize_set_expr(col))

def set_wildcard_predicate(dim, col):
    """True when `col` (post-normalization + validation) is the wildcard.

    Because ingestion validation rejects any concrete list containing the
    sentinel, `list.contains(sentinel)` is true iff the list is exactly
    [sentinel]. Evaluated only after normalization (contains() is null on a
    null cell)."""
    return col.list.contains(ma.lit(_typed_sentinel(dim)))   # typed scalar sentinel
```

**Ingestion validation (guards the reservation, F2/F6/F7).** A separate validation pass over the materialized rule frame — run once at ingestion in *both* engines — raises `ValueError` when, for any set dimension, a **non-null** rule list either (a) contains the sentinel but is not exactly `[sentinel]` (mixed/embedded sentinel), or (b) contains a null element. This makes "reserved, out-of-domain" an enforced contract, not an assumption, so `set_wildcard_predicate` is unambiguous. Whole-list null is *not* an error — it is the valid wildcard, normalized to `[sentinel]`.

**Normalization sequencing:** validation → `normalize_set_expr` MUST run at ingestion, before any detection/coalesce/fingerprint. This is what makes every downstream predicate see only non-null, validated lists.

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
- **Backend scope, stated honestly (F3).** This does **not** widen backend support. `t_is_in`/`list.contains` on a list *column* still hits the documented Narwhals limitation (Narwhals rejects an expression argument to `list.contains`), the same gap already tracked by `mountainash#89`. The accumulator build is polars-internal so build is unaffected; the filter/apply path over set list columns remains **polars/ibis only**, with Narwhals under the existing `#89` xfail — no new xfail group, and the spec makes **no unified-all-backends claim** for set list evaluation.

## Accumulator Build (`engines/accumulator/`)

The fix is in the **data**, not the join — so `_frontier_filter` is **untouched** (its inputs become null-free and canonical). The safety of "no change" depends entirely on normalization reaching *every* anchor path, so it is a named, mandatory stage:

1. **`_normalize_set_columns(rules_pl)` — a named build stage (F5).** Immediately after `relation(rules).to_polars()` and **before** the empty-frame branch, the partition output, and `_create_anchor` — for *every* build path (empty input, partition-filtered input, and any imported/flat lattice path that reaches the anchor) — run ingestion validation (reservation + null-element guards) then apply `normalize_set_expr` to each set dimension's rule list column. Every `co_<field>` for a set dim is therefore non-null and canonical before the anchor and any coalesce. A build-time assertion confirms no set `co_` column is null before `_frontier_filter`. This stage is what makes the frontier "no change" claim sound.
2. **Wildcard detection.** The accumulator compiler keeps its strategy-dispatching `wildcard_predicate(dim, col)` helper: the `SET_MEMBERSHIP`/`SET_EXCLUSION` branch delegates to `set_wildcard_predicate` (not `is_null()`), and the scalar/range branches keep the existing sentinel-equality test unchanged. Same set helper the filter engine uses.
3. **`_coalesce_set(dim, op)`:**
   - detection via `set_wildcard_predicate`;
   - both-wildcard branch → `sentinel_list_expr(dim)` (typed `[sentinel]` literal; replaces the old `.then(co)` typed-null);
   - co-wildcard → `rhs`; rhs-wildcard → `co`;
   - both-concrete → `canonicalize_set_expr(set_intersection|set_union)` so equal coalesced sets share a fingerprint.
   Membership uses `set_intersection`; exclusion uses `set_union`. (Wildcard is the identity for both: `∩` with match-anything, `∪` with exclude-nothing.)
4. **`_compatible_set_membership`:** `co_wild OR rhs_wild OR (intersection nonempty)` with `set_wildcard_predicate` for the wildcard tests; concrete∩concrete-nonempty unchanged. `SET_EXCLUSION` remains always-compatible (`ma.lit(True)`).
5. **`co_<field>_na` flag = `set_wildcard_predicate` applied to the FINAL coalesced value (F8)**, not to the LHS/RHS inputs. So wildcard+concrete → concrete coalesced value → flag **false**; wildcard+wildcard → `[sentinel]` → flag **true**; concrete+concrete → concrete → false. The flag tracks whether the *combination* leaves the dimension unconstrained, which is the semantics the frontier fingerprint and apply/provenance need.
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

- **Shared helpers:** `sentinel_list_expr` (element carries dim Python type; float dim → `List(Float64)` literal, verified); `normalize_set_expr` (null→`[sentinel]`, concrete sorted-unique, dtype preserved); `canonicalize_set_expr` (`["UK","NZ"]`→`["NZ","UK"]`, dupes removed); `set_wildcard_predicate` (True on `[sentinel]`, False on concrete incl. `[]`).
- **Domain validation (F1/F2/F6/F7):** `Dimension(SET_MEMBERSHIP|SET_EXCLUSION, data_type=bool)` raises `ValueError`; ingestion rejects a concrete list containing the sentinel (`["AU","<NA>"]` → raise) and rejects element-level nulls (`["AU", null]` → raise); whole-list null is accepted (→ wildcard).
- **Float typing (F4):** a `float` set dimension normalizes and coalesces to `List(Float64)` (element `-999999999.0`), asserted on polars; the `sentinel_list_expr` element-type is checked per dtype (str/int/float/date/datetime).
- **Filter compiler:** membership/exclusion ternary — wildcard rule → **0**, context-in-set → **1**, out-of-set → **−1**, **null rule-list input → normalized → 0** (backward-compat), context-missing → 0.
- **Accumulator compiler:** coalesce both-wildcard → `[sentinel]`; wildcard-passthrough → concrete side; both-concrete → canonicalized (asserts sorted-unique) intersection (membership) / union (exclusion); `_compatible_set_membership` empty concrete∩concrete → not compatible.
- **`co_<field>_na` post-coalesce (F8):** assert the flag on the final coalesced value for all three cases — wildcard+wildcard → **1**, wildcard+concrete → **0**, concrete+concrete → **0**.
- **Anchor regression (would have caught the bug):** a 3-rule all-wildcard-set build asserts `__prime_product == {30}` (NOT `{2,3,5,6,10,15,30}`); a mixed build asserts exact survivors + accumulated values. Plus a **pre-frontier assertion** that no set `co_` column is null on every path (empty, partitioned, multi-level).
- **Ordering:** two rules with the same set in different order → identical fingerprint → deduped to one combination.
- **Apply round-trip:** context in the `{R1,R2}` intersection matches prime-product 6; a wildcard combination matches any context — **exact** survivor counts, never `>= 1`.
- **Idempotence (F9):** `normalize(normalize(x))` equals `normalize(x)` at collected-value level, across dtypes (float/date included) on the polars build path.
- **Backend purity:** `tests/test_backend_purity.py` green; no new imports, no new `# allow:` tags.

## Compatibility & Migration

- **Backward-compatible input:** null rule lists are normalized at ingestion; no change to how rules are authored. Documented fast path: supply `[sentinel]` (or any concrete list) at source to skip the fill.
- **New validations (fail-loud, not silent):** `bool` set dimensions, sentinel-in-concrete-list, and element-level nulls now raise `ValueError`. These reject only inputs that were already degenerate or ambiguous — no previously-correct rule set is affected (bool set dims and sentinel-bearing lists had no well-defined meaning before).
- **Independent of A2** (shipped, PR #51). Small `engine.py` overlap only.
- **Docs:** update `CLAUDE.md` match-strategy table — `set_membership`/`set_exclusion` are accumulator-coalesceable (membership→intersection, exclusion→union) with an **in-band `[sentinel]` wildcard** (never null; `bool` unsupported); note the `null-is-not-a-portable-sentinel` principle.
- **`mountainash#89`** already covers narwhals list ops on the apply side; no new xfail group; no unified-all-backends claim for set list evaluation.
