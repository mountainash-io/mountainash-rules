# Batch Context Evaluation and Apply-Phase Caching

> **Status:** APPROVED FOR PLANNING
> **Date:** 2026-07-12
> **Source:** Architectural review 2026-07-12 §3; backlog card
> `mountainash-central/01.principles/mountainash-rules/h.backlog/batch-context-evaluation.md`
> **Principles:** backend-agnostic engine core (ENFORCED) — the batch path uses
> only `mountainash.relations` verbs (join, filter, group aggregates, sort);
> no window functions are required (see §3).
> **Depends on:** hit-policies spec (`__rule_index`, `HitPolicy`), P0 typed
> sentinels in `context.py`.
>
> **Correctness follow-up:** This historical feature design is amended by the
> approved [filter boundary/integration specification](../../../../mountainash-central/04.planning/mountainash-rules/superpowers/specs/2026-09-12-filter-batch-boundaries-design.md)
> and [policy/selection specification](../../../../mountainash-central/04.planning/mountainash-rules/superpowers/specs/2026-09-12-filter-policy-selection-design.md).
> The guidance below reflects canonical IDs, retained-rank selection and typed
> empty batches; original feature delivery remains historical.

## Problem

`evaluate()` handles one context per call: N contexts cost N full pipeline
runs (N relation builds, N sorts, N collects). Scoring a portfolio of 100k
contexts against 1k rules is the dominant real workload and the single
biggest efficiency lever identified in the review. Separately, the
accumulator's apply phase recompiles an `ExpressionRulesEngine` on every
`apply()` call and rebuilds the lattice lookup map on every `apply_auto()`
call.

## Design

### 1. API

```python
def evaluate_batch(
    self,
    contexts: t.Any,                       # DataFrame, any relation()-accepted backend
    *,
    context_id_field: str | None = None,   # default: synthesised __context_id
    dimensions: list[str] | None = None,
    hit_policy: HitPolicy | None = None,   # per-context application
    priority_field: str | None = None,
    top_n_per_context: int | None = None,
    min_specificity: int | None = None,
    include_observability: bool = True,
    chunk_size: int | None = None,         # contexts per chunk; None = single pass
) -> BatchRuleResult
```

on `ExpressionRulesEngine`. The compiled dimension expressions are reused
verbatim — they already reference `__ctx_<dim>` placeholder columns; the only
difference is that those columns now come from a join instead of literals.

### 2. Pipeline

1. **Context prep.** `relation(contexts)`; the internal id column is
   **always** `__context_id`: synthesised via `with_row_index` when
   `context_id_field` is absent, otherwise the supplied field is validated
   non-null and globally unique and *copied* into `__context_id`. The source
   field is recorded in `BatchRuleResult.context_id_field`, not echoed from
   contexts. The prepared frame uses `select(__context_id, [ctx exprs...])`, where each
   active dimension contributes
   `ma.col(dim.resolved_context_field).alias(f"__ctx_{dimension_name}")` —
   projection (a) guarantees nothing but reserved-prefix columns enters the
   join, eliminating collision-by-construction, and (b) handles two
   dimensions sharing one `resolved_context_field` (legal — metadata only
   enforces unique dimension names), which a rename could not. A **missing
   column** becomes a typed sentinel literal (`NOT_SET` / `NOT_SET_NUMERIC`
   / temporal sentinel per `data_type`); **null values** in present columns
   use the same typed absence representation. Boolean absence remains typed
   null rather than becoming a string sentinel.
   **Reserved-column validation**: before joining, both frames are checked —
   neither contexts nor rules may already contain `__context_id`,
   `__rule_index`, `__global_idx`, `__grp_base`, `__rank`, `__specificity`,
   `__survived`, or `__t_*`/`__ctx_*` columns (`ValueError` naming
   offenders; same reserved-namespace rule as the hit-policies spec).
2. **Combine.** Rules relation (with `__rule_index` from the hit-policies
   spec) cross-joined with prepared contexts. Rules × contexts is the honest
   cost model; `chunk_size` (see §4) bounds each chunk's cross-product work.
3. **Ternary + survival + specificity.** Identical expressions to
   `_evaluate` Steps 2–3, computed once over the combined frame, then
   `filter(__survived)`.
4. **Per-context ranking — portable, no window functions.** Sort by
   `(__context_id, policy ordering keys…, __rule_index)`, add a global
   `__global_idx` via `with_row_index`, then compute per-group rank via
   `Relation.group_by("__context_id")` — which returns a grouped relation
   whose `.agg(...)` supports aggregate expressions —
   `.agg(min(__global_idx) as __grp_base)`, joined back:
   `__rank = __global_idx - __grp_base + 1`. This uses only verbs the
   relations API exposes today (sort, group_by/agg, join, with_row_index) —
   deliberately chosen over `over()`-style windows, which mountainash
   relations do not currently expose. A native window implementation is a
   future optimisation behind the same API, not a semantic change.
5. **Hit policy per context** (order mirrors the single-context pipeline:
   rank → assertions → `min_specificity` → `top_n_per_context` →
   cardinality). COLLECT/RULE_ORDER: ordering only. UNIQUE: group-agg
   survivor counts; any count > 1 → `HitPolicyViolationError` listing
   offending context ids (bounded to first 20 in the message; full
   offending frame on the error object). ANY: per-context `unique().count`
   over the output-field projection; violation handling as UNIQUE. Then
   `filter(__specificity >= min_specificity)` if set, then
   `top_n_per_context` selects the first N remaining positions, and finally
   FIRST/PRIORITY/ANY selects the minimum retained rank per context.
   Stored pre-filter ranks are not rewritten; rank 2 can be the first survivor.
6. **Collect** once into `BatchRuleResult`.

### 3. `BatchRuleResult` (`src/mountainash_rules/batch_result.py`, new)

Backend-agnostic accessors mirroring `RuleResult`:

- `.survivors` — full matched frame (context id + rule columns + `__rank`,
  `__specificity`, optional `__t_*`), native backend.
- `.best_matches` — exactly one minimum-retained-rank row per context with
  retained matches, explicitly ordered by context ID and rank.
- `.matched_context_ids` / `.unmatched_context_ids(contexts)` — retained-match
  IDs and the sorted difference from the original submitted input. Custom source
  IDs must remain non-null and unique; missing source fields raise `ValueError`.
  Generated IDs require the original row order and count.
- `.counts_per_context` — retained-row counts, after filters/cardinality.
- `.for_context(context_id) -> RuleResult` — rank-ordered single-context view
  carrying the batch's conservative completeness state. Unknown and unmatched
  IDs produce typed empty views; potentially truncated views cannot `select()`.
- `.count`, `.active_dimensions`, `.context_id_field`.

### 4. Chunking

When `chunk_size` is a positive Python integer (excluding bool), prepared contexts
are split by row position without reassigning IDs. Steps 2–5 run per chunk;
results are concatenated via `relations.concat` and sorted by context ID/rank.
This bounds per-chunk cross-product work, not total staging, output or diagnostics.
IDs are non-null and globally unique (validated before conversion/chunking).
UNIQUE/ANY violation detection **accumulates across all chunks**
before raising (the error reports every offending context id, not just the
first violating chunk's). Default `None` (single pass) — chunking is opt-in
for very large cross products. No automatic heuristics (YAGNI; the caller
knows their memory budget).

After validation, typed empty contexts follow the normal unchunked evaluation
path once, including when chunking was requested. No empty concat, fake context
or native schema seed is used. Empty rules remain valid with nonempty dimensions.
Zero/negative/non-integer chunk sizes, invalid limits, empty/duplicate projections
and zero-dimension filter definitions fail explicitly; shared metadata stays valid.

### 5. Apply-phase caching (accumulator)

Two independent fixes in `accumulator_engine.py`:

1. **`apply()` engine memoisation.** A `weakref.WeakKeyDictionary[Lattice,
   ExpressionRulesEngine]` on the `AccumulatorEngine` caches the filter
   engine built from `_build_apply_metadata()` + lattice combinations.
   `Lattice` uses default identity hashing and is weakref-able, so
   identity-keyed caching is sound; the weak reference means dropping a
   lattice frees its engine. The cache assumes lattice contents are not
   mutated after construction — there is no public setter, but
   `Lattice.combinations` exposes the underlying frame, so this assumption
   is documented on the property. Repeated `apply(lattice, ctx)` calls stop
   recompiling `DimensionCompiler` output per call.
2. **`LatticeIndex`** (new class, `lattice.py`): wraps `list[Lattice]` +
   the context-key dimension list, building the partition-key → lattice map
   once:

   ```python
   index = engine.index(lattices)      # AccumulatorEngine.index(...)
   result = index.apply(context)       # key extraction + cached-engine apply
   result = index.apply_batch(contexts)  # groups contexts by partition key,
                                          # evaluate_batch per partition, concat
   ```

   `apply_auto(lattices, context)` remains as a convenience and is
   reimplemented as `self.index(lattices).apply(context)` — correct but
   rebuilding the index per call; its docstring points hot paths at
   `LatticeIndex`. (Plain lists are not weak-referenceable, so caching the
   map inside `apply_auto` keyed on the list is not an option; an explicit
   index object is the honest API.)

   `apply_batch` gives the accumulator the same batch story. Mechanics
   (relations expose no grouped-subframe iterator, so partitioning is
   explicit): collect the distinct combinations of each CONTEXT_KEY
   dimension's `resolved_context_field` from the contexts frame via
   `unique()`; for each combination, map the values to
   `partition_key[dimension_name]` to select the lattice, `filter` the
   contexts to that partition, run the lattice's cached filter engine's
   `evaluate_batch`, and `concat` the results. This is the
   `two-engines-two-stages` Stage-2 at dataset scale with no new engine
   machinery.

### Approaches considered

- **Python loop over `evaluate()` with shared relation** — rejected: keeps
  N sorts/collects; no backend gets to vectorise across contexts.
- **Window-function ranking (`rank() over (partition by ctx)`)** — rejected
  for now: not available through `mountainash.relations`; the group-min
  self-join is O(survivors) with a small join and fully portable. Revisit
  when mountainash grows window verbs.
- **Join on context-key columns instead of cross join** — rejected as the
  general mechanism (dimensions like RANGE/PREFIX cannot be equi-join keys);
  noted as a future *optimisation* for EXACT dimensions (pre-filtering the
  cross product by equi-joinable dimensions), out of scope here.

## Testing (TDD)

`tests/test_batch_evaluation.py` (new), RED first:

1. **Agreement oracle**: for a 20-context frame over mixed strategies
   (EXACT, RANGE, PREFIX, missing columns, null values), `evaluate_batch`
   survivors per context equal `evaluate()` run per context — same rules,
   same ranks, same specificity. This is the core invariant.
2. Synthesised vs supplied `context_id_field`; null or globally duplicate supplied
   IDs raise `ValueError`, including no-hit contexts and duplicates across chunks.
3. Missing dimension column in contexts → all-UNKNOWN for that dimension
   (matches single-context typed-sentinel behaviour).
4. `best_matches` selects the minimum remaining rank per retained context;
   `unmatched_context_ids` returns submitted IDs absent after caller reductions.
5. Hit policies: FIRST/PRIORITY pick per-context winners; UNIQUE with an
   engineered two-survivor context raises listing that context id.
6. `chunk_size=3` over 10 contexts equals unchunked results exactly.
7. `top_n_per_context` truncates per context, not globally.
8. Repeated `apply()` retains correct alias-remapped aggregate values;
   `LatticeIndex.apply` routes to the correct partition, and
   `LatticeIndex.apply_batch` preserves per-context identity and aggregate values.
9. Backend sweep: the agreement oracle runs on the polars, pandas and
   duckdb fixtures (reusing the existing backend parametrisation; expected
   xfails under mountainash#78 documented, not silently skipped).

## Files touched

- `src/mountainash_rules/engine.py` (`evaluate_batch`, context-prep helper),
  `batch_result.py` (new), `lattice.py` (`LatticeIndex`),
  `accumulator_engine.py` (memoisation, `index()`, `apply_auto` reimpl),
  `__init__.py` (exports).
- tests: `test_batch_evaluation.py` new.

## Out of scope

- Equi-join pre-filtering optimisation for EXACT dimensions (future perf).
- Native window-function ranking (blocked on mountainash).
- Automatic chunk-size heuristics.
- Batch build (the accumulator build phase is offline and already
  set-oriented).
