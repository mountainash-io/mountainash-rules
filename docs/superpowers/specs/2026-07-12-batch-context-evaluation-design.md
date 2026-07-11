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

1. **Context prep.** `relation(contexts)`; add `__context_id` via
   `with_row_index` unless `context_id_field` given (then validated unique).
   For each active dimension: rename/alias `dim.resolved_context_field` →
   `__ctx_<dimension_name>`; a **missing column** becomes a typed sentinel
   literal (`NOT_SET` / `NOT_SET_NUMERIC` / temporal sentinel per
   `data_type`), and **null values** in present columns are `fill_null`-ed
   with the same sentinel — exactly the single-context semantics from the P0
   fix, so batch and single results agree row-for-row. Non-dimension context
   columns are dropped before the join (they'd collide with rule columns and
   bloat the cross product).
2. **Combine.** Rules relation (with `__rule_index` from the hit-policies
   spec) cross-joined with prepared contexts. Rules × contexts is the honest
   cost model; `chunk_size` (see §4) bounds memory.
3. **Ternary + survival + specificity.** Identical expressions to
   `_evaluate` Steps 2–3, computed once over the combined frame, then
   `filter(__survived)`.
4. **Per-context ranking — portable, no window functions.** Sort by
   `(__context_id, policy ordering keys…, __rule_index)`, add a global
   `__global_idx` via `with_row_index`, then compute per-group rank by an
   aggregate self-join: `group_by(__context_id).agg(min(__global_idx) as
   __grp_base)` joined back, `__rank = __global_idx - __grp_base + 1`. This
   uses only verbs every backend already supports (sort, group-agg, join) —
   deliberately chosen over `over()`-style windows, which mountainash
   relations do not currently expose. A native window implementation is a
   future optimisation behind the same API, not a semantic change.
5. **Hit policy per context.** COLLECT/RULE_ORDER: ordering only.
   FIRST/PRIORITY: `filter(__rank == 1)`. UNIQUE: group-agg survivor counts;
   any count > 1 → `HitPolicyViolationError` listing offending context ids
   (bounded to first 20 in the message; full frame on the error object).
   ANY: per-context `unique().count` over output-field projection; violation
   handling as UNIQUE. `top_n_per_context` = `filter(__rank <= n)`.
6. **Collect** once into `BatchRuleResult`.

### 3. `BatchRuleResult` (`src/mountainash_rules/batch_result.py`, new)

Backend-agnostic accessors mirroring `RuleResult`:

- `.survivors` — full matched frame (context id + rule columns + `__rank`,
  `__specificity`, optional `__t_*`), native backend.
- `.best_matches` — `filter(__rank == 1)`: exactly one row per surviving
  context (rank ties are impossible — `__rule_index` is a total order).
- `.matched_context_ids` / `.unmatched_context_ids(contexts)` — the second
  takes the original contexts frame and anti-joins, because unmatched
  contexts are absent from the survivor frame by construction.
- `.counts_per_context` — `group_by(context_id).count()`.
- `.for_context(context_id) -> RuleResult` — filtered single-context view,
  giving access to `explain()` and `select()` unchanged.
- `.count`, `.active_dimensions`, `.context_id_field`.

### 4. Chunking

When `chunk_size` is set, contexts are split into id-ranges of that size and
Steps 2–5 run per chunk with results concatenated via `relations.concat`
before a single collect. This is a memory bound, not a semantics change:
per-context ranking never crosses chunk boundaries, so results are identical.
Default `None` (single pass) — chunking is opt-in for very large cross
products. No automatic heuristics (YAGNI; the caller knows their memory
budget).

### 5. Apply-phase caching (accumulator)

Two independent fixes in `accumulator_engine.py`:

1. **`apply()` engine memoisation.** A `weakref.WeakKeyDictionary[Lattice,
   ExpressionRulesEngine]` on the `AccumulatorEngine` caches the filter
   engine built from `_build_apply_metadata()` + lattice combinations.
   `Lattice` uses default identity hashing and is immutable in practice
   (its `_df` is never reassigned), so identity-keyed caching is sound; the
   weak reference means dropping a lattice frees its engine. Repeated
   `apply(lattice, ctx)` calls stop recompiling `DimensionCompiler` output
   per call.
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

   `apply_batch` gives the accumulator the same batch story: partition the
   contexts frame by the CONTEXT_KEY columns, run the lattice's cached filter
   engine's `evaluate_batch` per partition, concat. This is the
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
2. Synthesised vs supplied `context_id_field`; duplicate supplied ids →
   `ValueError`.
3. Missing dimension column in contexts → all-UNKNOWN for that dimension
   (matches single-context typed-sentinel behaviour).
4. `best_matches` has exactly one row per surviving context;
   `unmatched_context_ids` returns the contexts with zero survivors.
5. Hit policies: FIRST/PRIORITY pick per-context winners; UNIQUE with an
   engineered two-survivor context raises listing that context id.
6. `chunk_size=3` over 10 contexts equals unchunked results exactly.
7. `top_n_per_context` truncates per context, not globally.
8. Caching: `apply()` twice on one lattice compiles the filter engine once
   (counter/monkeypatch on `DimensionCompiler.compile_dimensions`);
   `LatticeIndex.apply` routes to the correct partition;
   `LatticeIndex.apply_batch` equals per-context `apply_auto` results.
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
