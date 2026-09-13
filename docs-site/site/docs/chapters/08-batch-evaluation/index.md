---
title: "Chapter 8: Batch Evaluation"
description: "Evaluate many contexts against one rule set in a vectorized cross-join, rank results per context, and inspect them through BatchRuleResult accessors."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 8: Batch Evaluation

## Summary

This chapter introduces batch evaluation, a new capability since the June 2026 edition. You will learn how `ExpressionRulesEngine.evaluate_batch()` scores every row in a contexts frame against every rule in one vectorized operation, while preserving a separate ranking for each context. The chapter follows the batch from preparation and backend conformance through cross-join scoring, portable per-context ranking, hit-policy enforcement, optional chunking, and the `BatchRuleResult` accessors that turn a large result into useful per-context views.

Batch evaluation builds directly on Chapter 5's single-context `evaluate()` pipeline. The match expressions, ternary values, survival rule, and specificity score do not change. What changes is the shape of the input: instead of broadcasting one context into the rules frame, the engine prepares many context rows and evaluates their Cartesian product with the rules. The result is still backend-agnostic and still uses the hit-policy semantics introduced in Chapter 6.

---

<!-- concept:112 -->
## The BatchRuleResult Class

`BatchRuleResult` is the per-context wrapper returned by `evaluate_batch()`. Its underlying frame contains the surviving rows from the batch computation. Conceptually, each row represents one `(context, rule)` pair that survived matching and any requested cardinality filters. The wrapper adds accessors for the questions applications usually ask next: Which contexts matched? How many rules matched each one? What is the best match for each context? Can one context be inspected as an ordinary `RuleResult`?

The class stores four pieces of state:

- The evaluated dataframe, exposed by `survivors`.
- The list of `active_dimensions` used for this call.
- The public `context_id_field` label used to identify contexts in the input.
- The `SelectionInfo` needed when a per-context result is wrapped as a `RuleResult`.

The survivor frame keeps `__context_id`, `__rule_index`, `__rank`, and `__specificity`. By default, observability is enabled, so `__t_<dimension>` ternary columns remain as well. The engine always removes temporary columns such as `__ctx_<dimension>`, `__survived`, `__global_idx`, and `__grp_base` before returning the wrapper. If `include_observability=False`, the ternary columns are removed too.

Here is the smallest useful way to inspect a batch result. `relation()` keeps the example portable when the engine is backed by a dataframe implementation other than Polars.

```python
from mountainash.relations import relation

batch = engine.evaluate_batch(contexts)

survivors = relation(batch.survivors).to_polars()
best = relation(batch.best_matches).to_polars()
print(survivors)
print(best)
print(batch.count)
```

`count` is the total number of survivor rows across all contexts, not the number of contexts. A batch with three matched contexts and two rules per context therefore has a count of six. `best_matches` selects, for each context with at least one retained row, the row at that context's minimum *retained* `__rank` — not necessarily `__rank == 1`. With the collecting policy and no truncation, minimum retained rank is rank 1 for every matched context, so this is one row per context that has at least one survivor. But `min_specificity` can remove a context's rank-1 row while keeping lower-ranked ones for that same context; `best_matches` still returns exactly one row per such context — the best of what remains, not necessarily the row that would have been rank 1 before filtering.

The wrapper does not copy the original, unprojected context columns into every survivor row. It retains the normalized `__context_id` so that the rule output can be joined back to the caller's context frame when more context attributes are needed.

<!-- concept:113 -->
## The Evaluate Batch Method

`evaluate_batch()` is the batch counterpart to the single-context `evaluate()` method. Its exact signature is:

```python
def evaluate_batch(
    self,
    contexts: t.Any,
    *,
    context_id_field: str | None = None,
    dimensions: list[str] | None = None,
    hit_policy: HitPolicy | str | None = None,
    priority_field: str | None = None,
    top_n_per_context: int | None = None,
    min_specificity: int | None = None,
    include_observability: bool = True,
    chunk_size: int | None = None,
) -> BatchRuleResult:
```

The required `contexts` argument is a dataframe-like object. Each row is one context to evaluate. The optional `context_id_field` names a column whose values identify those rows. If it is omitted, the engine creates `__context_id` from a zero-based row index. If it is supplied, its values must be non-null and globally unique; a missing field, a null value, or a duplicate identifier raises `ValueError` during context preparation.

`dimensions` selects the dimensions to evaluate. Only `None` means all compiled dimensions are active; an empty list is no longer an all-dimensions alias. `dimensions=[]`, a non-list value, a duplicate name, or any non-string entry all raise `ValueError` before any expression is evaluated. Every remaining requested name is checked against the compiled expressions, and an unknown-but-well-formed name raises `KeyError` rather than being silently ignored.

The remaining arguments control selection and output:

| Argument | Effect |
|---|---|
| `hit_policy` | Uses this call's policy (a `HitPolicy` member or its lowercase string value — an unrecognized string raises `ValueError`); otherwise uses metadata's policy, or `HitPolicy.COLLECT` when metadata is unavailable. |
| `priority_field` | Supplies the descending priority column required by `HitPolicy.PRIORITY`. |
| `top_n_per_context` | Keeps at most this many ranked survivors inside each context group; must be a non-Boolean Python `int >= 0`, or `None`. |
| `min_specificity` | Removes per-context survivors below the hard-match threshold; same `int >= 0` or `None` constraint as `top_n_per_context`. |
| `include_observability` | Retains or drops the `__t_<dimension>` ternary columns. |
| `chunk_size` | Switches from one whole-batch cross-join to opt-in chunks of prepared contexts; must be a non-Boolean Python `int >= 1`, or `None`. |

The method prepares the contexts, then either evaluates one prepared frame or evaluates several chunks. In both modes it returns a `BatchRuleResult` with the active dimensions, the effective context-id field, and selection information attached. It does not call Python once for every context; the normal path scores the complete batch through relation operations.

A typical call requests one best rule per context while retaining no ternary diagnostics:

```python
batch = engine.evaluate_batch(
    contexts,
    context_id_field="request_id",
    hit_policy=HitPolicy.FIRST,
    top_n_per_context=1,
    include_observability=False,
)
```

`top_n_per_context` and `min_specificity` are applied after the complete survivor set for each context has been ranked. In particular, hit-policy assertions are checked before those truncation filters, so `UNIQUE` and `ANY` can detect violations that would otherwise be hidden by `top_n_per_context=1`.

<!-- concept:114 -->
## Batch Context Preparation

Single-context evaluation binds one context value to a literal `__ctx_<dimension>` column that is broadcast across all rule rows. Batch evaluation mirrors that binding, but first projects many context rows into a compact, normalized frame. `_prepare_contexts()` returns exactly one identifier column and one context-binding column per active dimension:

```text
__context_id | __ctx_region | __ctx_amount | __ctx_code
```

All unrelated input columns are deliberately discarded. This prevents accidental collisions with rule columns during the join and keeps the cross-join payload limited to values that the compiled expressions actually read.

For each active dimension, the method resolves the context field from metadata. A dimension may use a different `context_field` from its dimension name, as the `code` dimension does in the following grounded example. For a present, non-Boolean field, null values are replaced with the typed `NOT_SET` sentinel using `coalesce`. If the field is absent, the method creates the entire `__ctx_` column from that sentinel; with metadata, the sentinel is selected from the dimension's declared `data_type`, and without metadata the generic `NOT_SET` constant is used.

A `BOOL` dimension is the one exception to sentinel substitution, because Boolean has no in-band "not set" value that cannot also be a real answer. When a Boolean field is present, its nulls are preserved rather than coalesced to a sentinel — the projection casts through `_nullable_bool()`, which keeps a null a null instead of letting a backend's Boolean cast silently turn it into `False`. When a Boolean field is absent entirely, the generated `__ctx_` column preserves absence as a nullable Boolean rather than being filled with `NOT_SET`, for the same reason.

Consider the dimensions and context rows below. `amount` is a numeric range dimension, while the `code` dimension reads `product_code` from each context row.

```python
import polars as pl
from mountainash_rules import Dimension, DimensionsMetadata, MatchStrategy

metadata = DimensionsMetadata(dimensions=[
    Dimension(dimension_name="region"),
    Dimension(
        dimension_name="amount",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="amt_min",
        range_max_field="amt_max",
    ),
    Dimension(
        dimension_name="code",
        match_strategy=MatchStrategy.PREFIX,
        context_field="product_code",
    ),
])

contexts = pl.DataFrame({
    "request_id": ["r-100", "r-101", "r-102"],
    "region": ["AU", "NZ", None],
    "amount": [50, 500, None],
    "product_code": ["X-1", "Z-9", None],
    "caller_note": ["morning", "evening", "retry"],
})
```

The third row's null values become typed sentinels in the projected frame. The `caller_note` column is not projected because it is not used by an active dimension. If no custom identifier is supplied, the same frame would receive context IDs `0`, `1`, and `2`. With `context_id_field="request_id"`, the IDs are the unique strings `r-100`, `r-101`, and `r-102`.

Before any of this projection or later chunking happens, a caller-supplied `context_id_field` is validated: the engine aggregates the total row count, the non-null count, and the distinct count for that column in one pass and raises `ValueError` if any row's ID is null or if two rows share an ID, without ever collecting the full input merely to check identity. This runs before context IDs are converted into `__context_id` and before the frame can be sliced into chunks, so an identity problem is caught once, at the earliest point, regardless of which evaluation path follows.

The projection also protects engine-owned names. `_check_reserved()` scans a caller-supplied frame and raises `ValueError` if any column is one of the batch-generated names or starts with `__t_` or `__ctx_`. The reserved tuple is:

```python
(
    "__context_id", "__global_idx", "__grp_base",
    "__rule_index", "__rank", "__specificity", "__survived",
)
```

Thus a context frame containing a user column named `__rank` is rejected before evaluation. The same check is applied to the rules frame. This is important because a collision could make a user column indistinguishable from a generated rank, survival flag, or ternary value.

<!-- concept:115 -->
## Cross-Join Evaluation

Once contexts have been prepared, the engine evaluates the Cartesian product of contexts and rules. A cross-join pairs every prepared context row with every rule row. If the prepared frame contains `m` contexts and the rules frame contains `n` rules, the intermediate relation has `m × n` candidate pairs before survival filtering. The candidate pair is the unit of evaluation: every rule receives the context values belonging to that pair.

`_evaluate_batch_frame()` first adds `__rule_index` to the rules relation. It then cross-joins the rules relation with the prepared context relation, after backend conformance has taken place. The compiled expressions are applied to the joined relation in one `with_columns` call, producing one `__t_<dimension>` column for each active dimension.

The rest is the ternary machinery from Chapter 5, now evaluated per pair:

- `1` means the rule matches on that dimension.
- `0` means the rule is unknown or wildcarded on that dimension.
- `-1` means the rule does not match on that dimension.
- A pair survives when the least ternary value across active dimensions is at least zero.
- `__specificity` counts the dimensions whose ternary value is exactly `1`.

The scoring equations are unchanged. A wildcard can preserve a pair, but it does not increase specificity. Therefore a context can have a specific rule and a catch-all rule in the same batch result, with the specific rule ranked first for that context.

```python
from mountainash_rules import ExpressionRulesEngine

rules = pl.DataFrame({
    "rule_name": ["au_low", "au_high", "nz_any", "prefix_x"],
    "region": ["AU", "AU", "NZ", "<NA>"],
    "amt_min": [0, 100, -999999999, -999999999],
    "amt_max": [99, 999, -999999999, -999999999],
    "code": ["<NA>", "<NA>", "<NA>", "X-"],
    "price": [1.0, 2.0, 3.0, 4.0],
})

engine = ExpressionRulesEngine(
    rules=rules,
    dimension_metadata=metadata,
)

batch = engine.evaluate_batch(
    contexts,
    context_id_field="request_id",
    include_observability=True,
)
```

For `r-100` (`AU`, amount `50`, and product code `X-1`), `au_low` has hard matches on region and amount, while `prefix_x` has a hard match on code and wildcards the other dimensions. Both survive, but their specificity scores differ. The result frame records those scores and their ranks separately from the rows for `r-101` and `r-102`.

This is the central batch invariant: for each context ID, the rows and scores agree with evaluating that context alone through `engine.evaluate()`, assuming the same active dimensions, policy, and thresholds. Batch evaluation changes execution shape, not matching semantics.

<!-- concept:116 -->
## Per-Context Ranking

A single global sort would be wrong for a batch. A rule with specificity `2` for context A must not receive a better rank merely because context B has only specificity-`1` survivors. The engine therefore ranks inside each `__context_id` group.

The implementation deliberately avoids window functions. Not every supported backend provides the same window-function support, and using a backend-specific ranking expression would undermine the engine's portability. Instead, `_evaluate_batch_frame()` computes a portable equivalent with ordinary relation operations:

1. Sort by `__context_id`, followed by the active policy's ordering keys.
2. Add a global zero-based `__global_idx`.
3. Group by `__context_id` and find each group's minimum global index, called `__grp_base`.
4. Join those group bases back to the sorted relation.
5. Compute `__rank = __global_idx - __grp_base + 1`.

The rank is consequently one-based within every context group. `ordering_keys()` supplies the policy-specific ordering. `FIRST` and `RULE_ORDER` use `__rule_index` ascending; `PRIORITY` uses the priority field descending, then specificity descending, then `__rule_index`; `COLLECT`, `UNIQUE`, and `ANY` use specificity descending followed by rule order.

The active hit policy is selected in this order:

1. The `hit_policy` argument on this `evaluate_batch()` call — a `HitPolicy` member or its lowercase string value; an unrecognized string raises `ValueError` rather than falling through to `COLLECT`.
2. `DimensionsMetadata.hit_policy`, when metadata is attached.
3. `HitPolicy.COLLECT` when neither is available.

Configuration errors are checked before scoring, the same way they are for single-context `evaluate()`: `HitPolicy.PRIORITY` requires an existing `priority_field`, and an expressions-only `ANY` (no metadata) requires explicit `output_fields`, both checked up front regardless of how many rows any context will end up with.

Assertions themselves run over the full survivor set for each context before result truncation. Under `HitPolicy.UNIQUE`, a context with more than one survivor raises `HitPolicyViolationError`. Under `HitPolicy.ANY`, the engine determines output fields (explicit, or inferred when metadata is attached) and raises when multiple survivors in one context disagree on their output values. A metadata-backed inferred output set that happens to be empty is only an error for a context where more than one row survives to be compared — a context with zero or one survivor is unaffected by an empty inferred set.

After assertions, `min_specificity` removes low-specificity rows. `top_n_per_context` then keeps only the first N *retained* positions per group, counted after the `min_specificity` filter rather than against the original `__rank` values — so `__rank` itself is never renumbered by either filter, and a context's lowest surviving `__rank` after `min_specificity` can be greater than 1. Finally, `FIRST`, `PRIORITY`, and `ANY` keep the minimum retained rank in each context, which is rank 1 only when nothing earlier removed it. `COLLECT` and `UNIQUE` retain their remaining rows, subject to the requested truncation.

The following table summarizes the batch-specific consequence of each policy. The policy meanings themselves are developed in Chapter 6; this table focuses on group cardinality.

| Hit policy | Ranking basis per context | Batch consequence |
|---|---|---|
| `COLLECT` | Specificity, then rule order | Keeps all surviving rows unless truncated. |
| `UNIQUE` | Specificity, then rule order | Asserts at most one survivor in each context group. |
| `FIRST` | Rule order | Keeps the minimum retained rank independently for every context. |
| `PRIORITY` | Priority, specificity, then rule order | Requires `priority_field` and selects the minimum retained rank per context. |
| `ANY` | Specificity, then rule order | Asserts output agreement within each context, then keeps the minimum retained rank. |
| `RULE_ORDER` | Rule order | Preserves rule order; it does not itself reduce cardinality. |

<!-- concept:117 -->
## Backend Conforming

A cross-join requires both input relations to live in the same dataframe backend. Callers often construct contexts as Polars dataframes even when the engine's rules were loaded through Ibis, Pandas, or Narwhals. `_conform_to_rules_backend()` rehosts the prepared context relation in the backend used by `self._rules` before `_evaluate_batch_frame()` performs the join.

Backend detection and conversion go through `mountainash`; the filter engine does not import backend libraries directly. The conversion choices are:

| Rules backend detected | Prepared contexts are rehosted as |
|---|---|
| Ibis | Ibis via `prepared.to_ibis()` |
| Narwhals or Pandas | Pandas when the rules object identifies Pandas; otherwise Polars |
| Other supported case | Polars |

This conversion happens after projection and sentinel filling. Consequently, backend conformance does not change which columns are active or how missing context values are represented. It only puts the already-prepared relation on the same execution engine as the rules.

The design matters for two reasons. First, users can submit a familiar dataframe type without manually converting it to match the rules. Second, the engine preserves backend purity: the core pipeline composes `mountainash.relations` and `mountainash.expressions`, while backend detection is isolated to the conversion boundary.

<!-- concept:118 -->
## Chunked Batch Evaluation

A whole-batch cross-join is efficient when the intermediate relation fits comfortably in memory. For a very large batch, however, materializing all `m × n` candidate pairs at once may be too expensive. Passing `chunk_size` opts into chunked evaluation; it must be a non-Boolean Python `int >= 1`, validated the same way as `top_n_per_context` and `min_specificity`. The engine first prepares the complete context projection, converts it to Polars for slicing, and evaluates consecutive slices of at most `chunk_size` rows. An empty prepared projection (zero contexts) is handled directly through the ordinary single-frame evaluation path rather than being sliced into zero chunks.

Each chunk runs the same `_evaluate_batch_frame()` pipeline: backend conformance, cross-join, ternary scoring, survival filtering, per-context ranking, assertions, and truncation. Successful chunk frames are concatenated and collected into the final `BatchRuleResult`.

Chunk boundaries do not change ordinary results. Context IDs are carried through the prepared rows, so each context still has its own rank. The following call evaluates ten contexts in chunks of three:

```python
batch = engine.evaluate_batch(
    contexts,
    context_id_field="request_id",
    chunk_size=3,
)
```

Chunking has an important correctness detail for assertion policies. A `UNIQUE` or `ANY` violation may occur in one chunk and another violation may occur in a different chunk. The method catches each `HitPolicyViolationError`, stores its offending frame, and continues evaluating the remaining chunks. If any violations occurred, it concatenates all offending frames and raises one `HitPolicyViolationError` whose message includes the accumulated context IDs. A violation is therefore not lost merely because its context was in a different chunk from another violation.

This accumulation is especially important when context IDs are automatically generated. For example, with `chunk_size=2`, input rows `0` and `3` can land in separate chunks; if both have multiple survivors under `UNIQUE`, the final error reports both IDs, sorted, and — for a very large violation set — bounded to the first 20 with a truncation note rather than growing the message without limit. Chunking limits peak materialization for the successful path, but it does not weaken hit-policy assertions or silently discard offending contexts.

`chunk_size` bounds the size of a single cross-join, not the batch's total memory footprint: the engine still stages the complete prepared context projection up front, and it still holds every successful chunk's result frame (and any violation frames) in memory until they are concatenated into the final answer. This is chunked evaluation, not a streaming pipeline — it trades one large intermediate relation for several smaller ones, not for bounded total memory.

Use chunking when the batch is too large for a single cross-join, and choose a chunk size that balances relation overhead against the size of each cross-product. The semantic contract remains the same as unchunked evaluation: for a given context and rule set, the same survivors, ranks, policy checks, and accessors are returned.

<!-- concept:119 -->
## The For Context Accessor

`BatchRuleResult.for_context(context_id)` extracts one context's rows and wraps them as an ordinary `RuleResult`. It filters the stored frame where `__context_id` equals the supplied value, collects that frame, and passes through the active dimensions and the batch's own `SelectionInfo` unchanged. This makes a batch result compatible with the single-context result API without rerunning the rule engine. An unmatched or never-submitted ID produces a typed empty `RuleResult` rather than an error, but it still carries that same `SelectionInfo` — emptiness does not make a batch that was truncated (by `top_n_per_context`, `min_specificity`, or a `FIRST`/`PRIORITY`/`ANY` policy) any more re-selectable than a nonempty per-context view would be.

The other accessors answer batch-level questions:

- **`best_matches`** returns the row at each matched context's minimum retained `__rank` — rank one whenever nothing removed it, but not guaranteed to be rank one once per-context truncation has run.
- **`counts_per_context`** groups by `__context_id` and returns a frame with `__context_id` and `__n`, the number of returned survivor rows in that group.
- **`matched_context_ids`** returns sorted unique IDs present in the survivor frame.
- **`unmatched_context_ids(contexts)`** compares those matched IDs with the supplied input contexts. When `context_id_field` was supplied, it first re-validates that column on the given `contexts` — present, non-null, and globally unique — the same check `evaluate_batch()` ran originally, rather than silently reading or deduplicating a column that may since have changed; otherwise it compares against positional IDs `0` through `count_rows() - 1`.
- **`active_dimensions`** returns the dimensions used in this batch call.
- **`context_id_field`** reports the source field name, or `"__context_id"` for generated IDs.

Here is a complete accessor example using the custom IDs from the earlier context frame:

```python
from mountainash.relations import relation

print(batch.context_id_field)       # "request_id"
print(batch.active_dimensions)      # ["region", "amount", "code"]
print(batch.matched_context_ids)

counts = relation(batch.counts_per_context).to_polars()
best = relation(batch.best_matches).to_polars()
missing = batch.unmatched_context_ids(contexts)

# Return an ordinary RuleResult for one context.
one = batch.for_context("r-100")
one_rows = relation(one.survivors).to_polars()
print(one.count)
print(one.best_match)
print(one.explain("au_low"))
```

If `r-100` has two survivors, `one.count` is two and `one.explain("au_low")` returns a dictionary mapping each active dimension to its ternary value, such as `{"region": 1, "amount": 1, "code": 0}`. The `for_context()` result does not include the original `request_id` value as a separate source column; use `__context_id` or join the result back to `contexts` when the full input row is needed.

A context with no survivors has no row in `BatchRuleResult.survivors`, so it does not appear in `matched_context_ids` or `counts_per_context`. It does appear in `unmatched_context_ids(contexts)` when that method receives the original contexts frame. This distinction lets an application process matches and misses without confusing “zero matching rules” with “the context was not evaluated.”

## A Complete Batch Workflow

The following example assembles the full workflow with the actual public classes and arguments. It evaluates several context rows, keeps at most two survivors per context, and retains ternary observability for diagnostics.

```python
import polars as pl
from mountainash.relations import relation
from mountainash_rules import (
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    HitPolicy,
    MatchStrategy,
)

rules = pl.DataFrame({
    "rule_name": ["au_low", "au_high", "nz_any", "prefix_x"],
    "region": ["AU", "AU", "NZ", "<NA>"],
    "amt_min": [0, 100, -999999999, -999999999],
    "amt_max": [99, 999, -999999999, -999999999],
    "code": ["<NA>", "<NA>", "<NA>", "X-"],
    "price": [1.0, 2.0, 3.0, 4.0],
})

metadata = DimensionsMetadata(dimensions=[
    Dimension(dimension_name="region"),
    Dimension(
        dimension_name="amount",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        range_min_field="amt_min",
        range_max_field="amt_max",
    ),
    Dimension(
        dimension_name="code",
        match_strategy=MatchStrategy.PREFIX,
        context_field="product_code",
    ),
])

engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
contexts = pl.DataFrame({
    "request_id": ["r-100", "r-101", "r-102"],
    "region": ["AU", "NZ", "XX"],
    "amount": [50, 500, 10],
    "product_code": ["X-1", "Z-9", "X-3"],
})

batch = engine.evaluate_batch(
    contexts,
    context_id_field="request_id",
    hit_policy=HitPolicy.COLLECT,
    top_n_per_context=2,
    min_specificity=1,
)

for context_id in batch.matched_context_ids:
    result = batch.for_context(context_id)
    print(context_id, result.count)

print("unmatched:", batch.unmatched_context_ids(contexts))
```

Read this workflow in pipeline order: the engine projects `contexts` to its IDs and three `__ctx_` columns, rehosts that projection if necessary, cross-joins it with `rules`, computes ternaries and specificity, ranks independently by `request_id`, applies the collect policy and per-context filters, and exposes the result through `BatchRuleResult`. No context loop is required in application code, yet an application can still recover a familiar `RuleResult` for any individual ID.

## Key Takeaways

- **`BatchRuleResult`** wraps the survivor frame produced by batch evaluation and provides batch-level and per-context accessors.
- **`evaluate_batch()`** accepts a contexts dataframe and scores every context against every rule in one vectorized pass, with exact controls for dimensions, policy, ranking, observability, and chunking.
- **Batch context preparation** projects each input row to `__context_id` and typed `__ctx_<dimension>` columns, filling missing or null values with `NOT_SET` sentinels.
- **Cross-join evaluation** applies the same ternary survival and specificity machinery as single-context `evaluate()` to every context–rule pair.
- **Per-context ranking** uses sorting, group minima, a join-back, and arithmetic instead of window functions, preserving portability across dataframe backends.
- **Hit policies** and assertions apply independently inside each context group; `UNIQUE` and `ANY` are checked before per-context truncation.
- **Backend conforming** rehosts prepared contexts in the rules relation's backend before the cross-join, keeping the engine backend-agnostic at its public boundary.
- **Chunked evaluation** bounds each cross-join's context slice and accumulates assertion violations across chunks rather than hiding violations at chunk boundaries.
- **`for_context()`** turns one context's batch rows into an ordinary `RuleResult`, while `best_matches`, `counts_per_context`, and matched/unmatched ID accessors support batch-oriented application logic.
