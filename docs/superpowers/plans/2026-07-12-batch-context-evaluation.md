# Batch Context Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `evaluate_batch(contexts_df)` to the filter engine plus accumulator apply-phase caching (`LatticeIndex`, engine memoisation), per `docs/superpowers/specs/2026-07-12-batch-context-evaluation-design.md`.

**Architecture:** Context prep is a pure projection helper (`_prepare_contexts`); the batch pipeline cross-joins prepared contexts with rules, reuses the compiled `__ctx_*` expressions verbatim, and ranks per context with a portable sort + group-min self-join (no window functions). Results wrap in `BatchRuleResult`. Accumulator caching is `WeakKeyDictionary` + a new `LatticeIndex`.

**Tech Stack:** Python 3.12, mountainash relations (`group_by(...).agg(...)`, `join`, `sort`, `with_row_index`, `unique`, `concat`), polars fixtures.

## Global Constraints

- **Depends on the hit-policies plan having landed** (`__rule_index`, `HitPolicy`, `ordering_keys`, reserved-column guard).
- Core invariant (Task 2's oracle): per-context batch results equal single-context `evaluate()` results row-for-row.
- Reserved batch columns extend the engine namespace: `__context_id`, `__global_idx`, `__grp_base`.
- Missing context columns → typed `NOT_SET` sentinels; nulls in present columns → same sentinel (P0 semantics).
- The internal id column is always `__context_id`; a user-supplied `context_id_field` is validated unique and copied into it.
- Chunking changes memory use only, never results; UNIQUE/ANY violations accumulate across chunks before raising.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Context preparation helper

**Files:**
- Modify: `src/mountainash_rules/engine.py`
- Test: `tests/test_batch_evaluation.py` (create)

**Interfaces:**
- Produces: `ExpressionRulesEngine._prepare_contexts(contexts, active_dims, context_id_field) -> t.Any` — a relation with exactly `["__context_id"] + [f"__ctx_{d}" for d in active_dims]` columns, sentinel-filled. Raises on duplicate ids and reserved-column collisions. Task 2 consumes it.

- [ ] **Step 1: Write the failing tests** (create `tests/test_batch_evaluation.py`)

```python
"""Tests for evaluate_batch and accumulator apply caching."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.constants import (
    NOT_SET,
    NOT_SET_NUMERIC,
    HitPolicy,
    MatchStrategy,
)
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine


def _metadata():
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region"),
        Dimension(
            dimension_name="amount", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="amt_min", range_max_field="amt_max",
        ),
        Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX,
                  context_field="product_code"),
    ])


def _rules():
    return pl.DataFrame({
        "rule_name": ["au_low", "au_high", "nz_any", "prefix_x"],
        "region": ["AU", "AU", "NZ", "<NA>"],
        "amt_min": [0, 100, -999999999, -999999999],
        "amt_max": [99, 999, -999999999, -999999999],
        "code": ["<NA>", "<NA>", "<NA>", "X-"],
        "price": [1.0, 2.0, 3.0, 4.0],
    })


def _engine():
    return ExpressionRulesEngine(rules=_rules(), dimension_metadata=_metadata())


class TestPrepareContexts:
    def test_projects_exactly_id_plus_ctx_columns(self):
        contexts = pl.DataFrame({
            "region": ["AU", "NZ"],
            "amount": [50, 500],
            "product_code": ["X-1", "Y-2"],
            "irrelevant": ["a", "b"],
        })
        engine = _engine()
        prepared = engine._prepare_contexts(
            contexts, ["region", "amount", "code"], None
        )
        assert sorted(prepared.columns) == sorted(
            ["__context_id", "__ctx_region", "__ctx_amount", "__ctx_code"]
        )

    def test_missing_column_gets_typed_sentinel(self):
        contexts = pl.DataFrame({"region": ["AU"]})
        prepared = _engine()._prepare_contexts(
            contexts, ["region", "amount"], None
        )
        row = relation(prepared).to_dict()
        assert row["__ctx_amount"][0] == NOT_SET_NUMERIC

    def test_null_value_gets_typed_sentinel(self):
        contexts = pl.DataFrame({"region": ["AU", None]})
        prepared = _engine()._prepare_contexts(contexts, ["region"], None)
        row = relation(prepared).to_dict()
        assert row["__ctx_region"][1] == NOT_SET

    def test_supplied_id_copied_and_validated(self):
        contexts = pl.DataFrame({"cid": ["a", "b"], "region": ["AU", "NZ"]})
        prepared = _engine()._prepare_contexts(contexts, ["region"], "cid")
        row = relation(prepared).to_dict()
        assert row["__context_id"] == ["a", "b"]

    def test_duplicate_supplied_ids_raise(self):
        contexts = pl.DataFrame({"cid": ["a", "a"], "region": ["AU", "NZ"]})
        with pytest.raises(ValueError, match="unique"):
            _engine()._prepare_contexts(contexts, ["region"], "cid")

    def test_reserved_column_in_contexts_raises(self):
        contexts = pl.DataFrame({"__rank": [1], "region": ["AU"]})
        with pytest.raises(ValueError, match="__rank"):
            _engine()._prepare_contexts(contexts, ["region"], None)
```

- [ ] **Step 2: Run to verify failure**

Run: `hatch run test:test-target tests/test_batch_evaluation.py::TestPrepareContexts -v`
Expected: FAIL — `AttributeError: ... no attribute '_prepare_contexts'`.

- [ ] **Step 3: Implement** (add to `ExpressionRulesEngine`)

```python
    _BATCH_RESERVED = (
        "__context_id", "__global_idx", "__grp_base",
        "__rule_index", "__rank", "__specificity", "__survived",
    )

    def _prepare_contexts(
        self,
        contexts: t.Any,
        active_dims: list[str],
        context_id_field: str | None,
    ) -> t.Any:
        """Project contexts to __context_id + __ctx_<dim> columns with sentinels."""
        rel = relation(contexts)
        colliding = [
            c for c in rel.columns
            if c in self._BATCH_RESERVED or c.startswith(("__t_", CTX_PREFIX))
        ]
        if colliding:
            raise ValueError(
                f"Contexts frame contains reserved engine columns: {colliding}"
            )

        if context_id_field is None:
            rel = rel.with_row_index(name="__context_id")
        else:
            total = rel.count_rows()
            distinct = rel.select(ma.col(context_id_field)).unique().count_rows()
            if distinct != total:
                raise ValueError(
                    f"context_id_field '{context_id_field}' must be unique "
                    f"({total} rows, {distinct} distinct)"
                )
            rel = rel.with_columns(
                ma.col(context_id_field).alias("__context_id")
            )

        available = set(rel.columns)
        ctx_exprs: list[t.Any] = [ma.col("__context_id")]
        for name in active_dims:
            dim = self._metadata.get_dimension(name) if self._metadata else None
            field = dim.resolved_context_field if dim is not None else name
            sentinel = (
                not_set_sentinel_for(dim.data_type) if dim is not None else NOT_SET
            )
            alias = f"{CTX_PREFIX}{name}"
            if field in available:
                ctx_exprs.append(
                    ma.coalesce(ma.col(field), ma.lit(sentinel)).alias(alias)
                )
            else:
                ctx_exprs.append(ma.lit(sentinel).alias(alias))
        return rel.select(*ctx_exprs)
```

Imports: `not_set_sentinel_for` from constants (exists after the metadata plan; if executing before it, substitute the P0-era inline `NOT_SET_NUMERIC if dim.data_type in (int, float) else NOT_SET` and leave a TODO referencing the metadata plan). If `ma.coalesce` cannot mix a column with a literal, use `ma.when(ma.col(field).is_null()).then(ma.lit(sentinel)).otherwise(ma.col(field))`.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engine.py tests/test_batch_evaluation.py
git commit -m "feat: batch context preparation with typed sentinels and reserved-column guard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: evaluate_batch core + agreement oracle

**Files:**
- Modify: `src/mountainash_rules/engine.py`
- Create: `src/mountainash_rules/batch_result.py`
- Test: `tests/test_batch_evaluation.py`

**Interfaces:**
- Consumes: `_prepare_contexts` (Task 1), compiled `self._expressions`, `ordering_keys` from `hit_policy.py`.
- Produces:

```python
def evaluate_batch(self, contexts, *, context_id_field=None, dimensions=None,
                   hit_policy=None, priority_field=None,
                   top_n_per_context=None, min_specificity=None,
                   include_observability=True, chunk_size=None) -> BatchRuleResult

class BatchRuleResult:
    survivors -> t.Any
    best_matches -> t.Any                      # __rank == 1
    count -> int
    active_dimensions -> list[str]
    context_id_field -> str
    counts_per_context -> t.Any
    matched_context_ids -> list
    def unmatched_context_ids(self, contexts) -> list
    def for_context(self, context_id) -> RuleResult
```

(Tasks 3–5 fill accessors, policies, chunking; this task lands COLLECT-only single-pass with `survivors`/`best_matches`/`count`.)

- [ ] **Step 1: Write the failing tests** (append)

```python
class TestEvaluateBatchAgreement:
    """Core invariant: batch == per-context evaluate(), row for row."""

    def _contexts(self):
        return pl.DataFrame({
            "region": ["AU", "AU", "NZ", "XX", None],
            "amount": [50, 500, None, 10, 10],
            "product_code": ["X-1", "Y-9", "X-2", "X-3", None],
        })

    def test_batch_agrees_with_single_context_evaluation(self):
        engine = _engine()
        batch = engine.evaluate_batch(self._contexts())
        surv = pl.DataFrame(relation(batch.survivors).to_polars())

        for i, ctx in enumerate(self._contexts().to_dicts()):
            single = engine.evaluate(
                {k: v for k, v in ctx.items() if v is not None}
            )
            single_rows = relation(single.survivors).to_polars()
            batch_rows = surv.filter(pl.col("__context_id") == i).sort("__rank")
            assert batch_rows["rule_name"].to_list() == \
                single_rows["rule_name"].to_list(), f"context {i}"
            assert batch_rows["__specificity"].to_list() == \
                single_rows["__specificity"].to_list(), f"context {i}"
            assert batch_rows["__rank"].to_list() == \
                single_rows["__rank"].to_list(), f"context {i}"

    def test_best_matches_one_row_per_surviving_context(self):
        engine = _engine()
        batch = engine.evaluate_batch(self._contexts())
        best = relation(batch.best_matches).to_polars()
        assert best["__context_id"].n_unique() == len(best)
        assert set(best["__rank"].to_list()) == {1}
```

- [ ] **Step 2: Run to verify failure** — `AttributeError: ... no attribute 'evaluate_batch'`.

- [ ] **Step 3: Implement**

`engine.py`:

```python
    def evaluate_batch(
        self,
        contexts: t.Any,
        *,
        context_id_field: str | None = None,
        dimensions: list[str] | None = None,
        hit_policy: HitPolicy | None = None,
        priority_field: str | None = None,
        top_n_per_context: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        chunk_size: int | None = None,
    ) -> BatchRuleResult:
        """Evaluate every context row against every rule in one pass."""
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names
        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")
        if hit_policy is None:
            hit_policy = (
                self._metadata.hit_policy if self._metadata else HitPolicy.COLLECT
            )
        info = selection_info_from_metadata(
            self._metadata, priority_field, include_observability
        )

        prepared = self._prepare_contexts(contexts, active_dims, context_id_field)
        result_df = self._evaluate_batch_frame(
            prepared, active_dims, hit_policy, info,
            top_n_per_context, min_specificity, include_observability,
        )
        return BatchRuleResult(
            dataframe=result_df,
            active_dimensions=active_dims,
            context_id_field=context_id_field or "__context_id",
            selection_info=info,
        )

    def _evaluate_batch_frame(
        self,
        prepared: t.Any,
        active_dims: list[str],
        hit_policy: HitPolicy,
        info: "SelectionInfo",
        top_n_per_context: int | None,
        min_specificity: int | None,
        include_observability: bool,
    ) -> t.Any:
        rules_rel = relation(self._rules)
        self._check_reserved(rules_rel)          # same guard as evaluate()
        rules_rel = rules_rel.with_row_index(name="__rule_index")

        joined = rules_rel.join(prepared, how="cross")

        # Ternary, survival, specificity — same expressions as _evaluate
        dim_columns = [
            self._expressions[d].name.alias(f"__t_{d}") for d in active_dims
        ]
        joined = joined.with_columns(*dim_columns)
        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        survived_inner = t_cols[0] if len(t_cols) == 1 else ma.least(*t_cols)
        survived = survived_inner.ge(ma.lit(0)).alias("__survived")
        specificity = functools.reduce(
            lambda a, b: a.add(b),
            [c.eq(ma.lit(1)).cast(int) for c in t_cols],
        ).alias("__specificity")
        joined = joined.with_columns(survived, specificity)
        joined = joined.filter(ma.col("__survived"))

        # Portable per-context rank: sort, global index, group-min join-back
        keys = ordering_keys(hit_policy, info.priority_field)
        sort_cols = ["__context_id"] + [k for k, _ in keys]
        sort_desc = [False] + [d for _, d in keys]
        joined = joined.sort(sort_cols, descending=sort_desc)
        joined = joined.with_row_index(name="__global_idx")
        bases = joined.group_by("__context_id").agg(
            ma.col("__global_idx").min().alias("__grp_base")
        )
        joined = joined.join(bases, on="__context_id", how="inner")
        joined = joined.with_columns(
            ma.col("__global_idx")
            .sub(ma.col("__grp_base"))
            .add(ma.lit(1))
            .alias("__rank")
        )

        # Task 4 inserts assertions/min_specificity/top_n/cardinality here.

        drop_cols = ["__survived", "__global_idx", "__grp_base"] + [
            f"{CTX_PREFIX}{d}" for d in active_dims
        ]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        return joined.drop(*drop_cols).collect()
```

(`self._check_reserved(rel)` — extract the reserved-column guard added by the hit-policies plan into this small method and call it from both `_evaluate` and here. If `ma.col(...).sub(...)` does not exist, use `.add(ma.lit(1)).add(ma.col("__grp_base").mul(ma.lit(-1)))` — check `dir(ma.col("x"))` first.)

`batch_result.py`:

```python
"""BatchRuleResult: backend-agnostic accessors over batch evaluation output."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.hit_policy import SelectionInfo
from mountainash_rules.result import RuleResult


class BatchRuleResult:
    """Wraps the batch survivor frame (one row per context x surviving rule)."""

    def __init__(
        self,
        dataframe: t.Any,
        active_dimensions: list[str],
        context_id_field: str,
        selection_info: SelectionInfo | None = None,
    ) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions
        self._context_id_field = context_id_field
        self._selection_info = selection_info

    @property
    def survivors(self) -> t.Any:
        return self._df

    @property
    def best_matches(self) -> t.Any:
        return (
            relation(self._df).filter(ma.col("__rank").eq(ma.lit(1))).collect()
        )

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def active_dimensions(self) -> list[str]:
        return self._active_dimensions

    @property
    def context_id_field(self) -> str:
        return self._context_id_field
```

- [ ] **Step 4: Run** → agreement oracle PASSES. If ordering disagrees, debug the sort (the single-context path sorts `(__specificity desc, __rule_index asc)`; the batch sort must produce the identical per-context order).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engine.py src/mountainash_rules/batch_result.py tests/test_batch_evaluation.py
git commit -m "feat: evaluate_batch with portable per-context ranking

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: BatchRuleResult accessors

**Files:**
- Modify: `src/mountainash_rules/batch_result.py`
- Test: `tests/test_batch_evaluation.py`

**Interfaces:**
- Produces: `counts_per_context`, `matched_context_ids`, `unmatched_context_ids(contexts)`, `for_context(id) -> RuleResult`.

- [ ] **Step 1: Write the failing tests**

```python
class TestBatchAccessors:
    def _batch(self):
        contexts = pl.DataFrame({
            "region": ["AU", "XX"], "amount": [50, 1],
            "product_code": ["X-1", "Q"],
        })
        return _engine().evaluate_batch(contexts), contexts

    def test_counts_per_context(self):
        batch, _ = self._batch()
        counts = relation(batch.counts_per_context).to_polars()
        assert dict(zip(counts["__context_id"], counts["__n"])) == {0: 3}

    def test_matched_and_unmatched(self):
        batch, contexts = self._batch()
        assert batch.matched_context_ids == [0]
        assert batch.unmatched_context_ids(contexts) == [1]

    def test_for_context_returns_rule_result(self):
        batch, _ = self._batch()
        single = batch.for_context(0)
        assert single.count == 3
        assert single.explain("au_low")["region"] == 1
```

(Context 0 = AU/50/X-1 matches `au_low` (region+range), `prefix_x` (prefix, others unknown), and... verify against `_rules()`: `nz_any` fails region (−1) → 3 survivors are `au_low`, `prefix_x`, and — check `au_high` range 100-999 excludes 50 → non-match. So survivors = `au_low`, `prefix_x` = **2**, not 3. The implementer MUST recount at RED time and fix the expected numbers in the test to the verified truth from single-context `evaluate()` — the oracle from Task 2 is the authority. Context 1 = XX/1/Q: `nz_any` fails region; `au_*` fail region; `prefix_x` fails prefix → 0 survivors, stays unmatched.)

- [ ] **Step 2: Run to verify failure** — missing attributes.

- [ ] **Step 3: Implement** (append to `BatchRuleResult`)

```python
    @property
    def counts_per_context(self) -> t.Any:
        return (
            relation(self._df)
            .group_by("__context_id")
            .agg(ma.col("__rank").count().alias("__n"))
            .collect()
        )

    @property
    def matched_context_ids(self) -> list:
        rows = (
            relation(self._df).select(ma.col("__context_id")).unique().to_dict()
        )
        return sorted(rows["__context_id"])

    def unmatched_context_ids(self, contexts: t.Any) -> list:
        matched = set(self.matched_context_ids)
        ctx_rel = relation(contexts)
        if self._context_id_field in ctx_rel.columns:
            all_ids = ctx_rel.select(
                ma.col(self._context_id_field)
            ).unique().to_dict()[self._context_id_field]
        else:
            all_ids = list(range(ctx_rel.count_rows()))
        return sorted(i for i in all_ids if i not in matched)

    def for_context(self, context_id) -> RuleResult:
        frame = (
            relation(self._df)
            .filter(ma.col("__context_id").eq(ma.lit(context_id)))
            .collect()
        )
        return RuleResult(
            dataframe=frame,
            active_dimensions=self._active_dimensions,
            selection_info=self._selection_info,
        )
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/batch_result.py tests/test_batch_evaluation.py
git commit -m "feat: BatchRuleResult per-context accessors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Hit policies, min_specificity, top_n per context

**Files:**
- Modify: `src/mountainash_rules/engine.py` (`_evaluate_batch_frame`)
- Test: `tests/test_batch_evaluation.py`

**Interfaces:**
- Consumes: `default_output_fields`, `HitPolicyViolationError` from `hit_policy.py`.
- Produces: full policy semantics in batch: FIRST/PRIORITY/ANY keep `__rank == 1`; UNIQUE raises listing offending context ids; order = rank → assertions → min_specificity → top_n_per_context → cardinality.

- [ ] **Step 1: Write the failing tests**

```python
class TestBatchHitPolicies:
    def _contexts(self):
        return pl.DataFrame({
            "region": ["AU", "NZ"], "amount": [50, 500],
            "product_code": ["X-1", "Z"],
        })

    def test_first_keeps_one_row_per_context(self):
        batch = _engine().evaluate_batch(
            self._contexts(), hit_policy=HitPolicy.FIRST
        )
        surv = relation(batch.survivors).to_polars()
        assert surv.group_by("__context_id").len()["len"].to_list() == [1, 1]

    def test_unique_violation_lists_context_ids(self):
        from mountainash_rules.hit_policy import HitPolicyViolationError
        with pytest.raises(HitPolicyViolationError) as exc_info:
            _engine().evaluate_batch(
                self._contexts(), hit_policy=HitPolicy.UNIQUE
            )
        assert "0" in str(exc_info.value)  # context 0 has >1 survivor

    def test_top_n_per_context_truncates_per_context_not_globally(self):
        batch = _engine().evaluate_batch(self._contexts(), top_n_per_context=1)
        surv = relation(batch.survivors).to_polars()
        assert (surv["__rank"] <= 1).all()
        assert surv["__context_id"].n_unique() == 2
```

(As in Task 3, verify survivor counts per context against single-context `evaluate()` at RED time; contexts here are chosen so context 0 has ≥2 survivors and context 1 has ≥1.)

- [ ] **Step 2: Run to verify failure** — policies ignored (all rows returned) / no error raised.

- [ ] **Step 3: Implement** (insert at the marked point in `_evaluate_batch_frame`)

```python
        # Assertions over full per-context survivor sets (pre-truncation)
        if hit_policy == HitPolicy.UNIQUE:
            offenders = (
                joined.group_by("__context_id")
                .agg(ma.col("__rank").count().alias("__n"))
                .filter(ma.col("__n").gt(ma.lit(1)))
            )
            if offenders.count_rows() > 0:
                ids = offenders.to_dict()["__context_id"]
                raise HitPolicyViolationError(
                    hit_policy, offenders.collect(),
                    f"hit_policy=unique violated for context ids "
                    f"{sorted(ids)[:20]}"
                    + (" (truncated)" if len(ids) > 20 else ""),
                )
        elif hit_policy == HitPolicy.ANY:
            outputs = default_output_fields(joined.columns, info)
            if not outputs:
                raise ValueError(
                    "hit_policy=any requires output_fields when no metadata "
                    "is available to infer them"
                )
            disagree = (
                joined.select(
                    ma.col("__context_id"), *[ma.col(c) for c in outputs]
                )
                .unique()
                .group_by("__context_id")
                .agg(ma.col(outputs[0]).count().alias("__n"))
                .filter(ma.col("__n").gt(ma.lit(1)))
            )
            if disagree.count_rows() > 0:
                ids = disagree.to_dict()["__context_id"]
                raise HitPolicyViolationError(
                    hit_policy, disagree.collect(),
                    f"hit_policy=any violated for context ids {sorted(ids)[:20]}",
                )

        if min_specificity is not None:
            joined = joined.filter(
                ma.col("__specificity").ge(ma.lit(min_specificity))
            )
        if top_n_per_context is not None:
            joined = joined.filter(
                ma.col("__rank").le(ma.lit(top_n_per_context))
            )
        if hit_policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY):
            joined = joined.filter(ma.col("__rank").eq(ma.lit(1)))
```

- [ ] **Step 4: Run** → PASS; re-run the Task 2 agreement oracle → still PASS (COLLECT default untouched).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engine.py tests/test_batch_evaluation.py
git commit -m "feat: per-context hit policies, min_specificity, top_n in evaluate_batch

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Chunking

**Files:**
- Modify: `src/mountainash_rules/engine.py`
- Test: `tests/test_batch_evaluation.py`

**Interfaces:**
- Produces: `chunk_size` splits prepared contexts into id-ranges; per-chunk frames concat before a single `BatchRuleResult`; UNIQUE/ANY offenders accumulate across chunks and raise once.

- [ ] **Step 1: Write the failing test**

```python
class TestChunking:
    def test_chunked_equals_unchunked(self):
        contexts = pl.DataFrame({
            "region": ["AU"] * 5 + ["NZ"] * 5,
            "amount": list(range(0, 1000, 100)),
            "product_code": [f"X-{i}" for i in range(10)],
        })
        engine = _engine()
        whole = relation(engine.evaluate_batch(contexts).survivors).to_polars()
        chunked = relation(
            engine.evaluate_batch(contexts, chunk_size=3).survivors
        ).to_polars()
        key = ["__context_id", "__rank"]
        assert whole.sort(key).equals(chunked.sort(key))
```

- [ ] **Step 2: Run to verify failure** — `chunk_size` accepted but currently ignored? If Task 2's signature already accepts it and the single-pass result is identical, this test may PASS vacuously. Guard against that: implement chunking so it actually partitions (assert in a debug counter test if needed), and verify failure by temporarily raising `NotImplementedError` for `chunk_size is not None` in Task 2 — Task 2's Step 3 code MUST include:

```python
        if chunk_size is not None:
            raise NotImplementedError("chunk_size lands in a later task")
```

so this step observably fails RED with `NotImplementedError`.

- [ ] **Step 3: Implement** (replace the `NotImplementedError` in `evaluate_batch`)

```python
        prepared = self._prepare_contexts(contexts, active_dims, context_id_field)
        if chunk_size is None:
            result_df = self._evaluate_batch_frame(
                prepared, active_dims, hit_policy, info,
                top_n_per_context, min_specificity, include_observability,
            )
        else:
            from mountainash.relations import concat
            prepared_pl = relation(prepared).to_polars()
            frames = []
            violations: list[t.Any] = []
            for start in range(0, len(prepared_pl), chunk_size):
                chunk = prepared_pl.slice(start, chunk_size)
                try:
                    frames.append(self._evaluate_batch_frame(
                        relation(chunk), active_dims, hit_policy, info,
                        top_n_per_context, min_specificity,
                        include_observability,
                    ))
                except HitPolicyViolationError as exc:
                    violations.append(exc)
            if violations:
                combined = concat([relation(v.offending) for v in violations])
                raise HitPolicyViolationError(
                    hit_policy, combined.collect(),
                    "; ".join(str(v) for v in violations),
                )
            result_df = concat(
                [relation(f) for f in frames]
            ).collect()
```

Note: chunking materialises prepared contexts to polars for slicing — that is the contexts frame only (small relative to the cross product), and it is the documented purpose of the parameter. `_evaluate_batch_frame` must continue collecting violations rather than short-circuiting other chunks; the try/except achieves this because each chunk raises independently.

- [ ] **Step 4: Run** → PASS; add a violation-accumulation test if UNIQUE offenders span chunks (two AU-duplicate contexts placed in different chunks; assert both ids appear in the message).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engine.py tests/test_batch_evaluation.py
git commit -m "feat: opt-in chunked batch evaluation with cross-chunk violation accumulation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Accumulator caching — memoised apply + LatticeIndex

**Files:**
- Modify: `src/mountainash_rules/accumulator_engine.py`, `src/mountainash_rules/lattice.py`, `src/mountainash_rules/__init__.py`
- Test: `tests/test_batch_evaluation.py`

**Interfaces:**
- Produces: `AccumulatorEngine.apply()` memoises its filter engine per Lattice (`weakref.WeakKeyDictionary`); `AccumulatorEngine.index(lattices) -> LatticeIndex`; `LatticeIndex.apply(context)`, `LatticeIndex.apply_batch(contexts, **evaluate_batch_kwargs)`; `apply_auto` reimplemented as `self.index(lattices).apply(context)`.

- [ ] **Step 1: Write the failing tests**

```python
class TestApplyCaching:
    def _setup(self):
        from mountainash_rules.accumulator_engine import AccumulatorEngine
        from mountainash_rules.constants import DimensionRole
        md = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="segment", role=DimensionRole.CONTEXT_KEY),
            Dimension(dimension_name="region"),
        ])
        rules = pl.DataFrame({
            "rule_name": ["a", "b", "c"],
            "segment": ["retail", "retail", "corp"],
            "region": ["AU", "<NA>", "AU"],
        })
        engine = AccumulatorEngine(dimension_metadata=md)
        return engine, engine.build_all(rules)

    def test_apply_compiles_engine_once_per_lattice(self, monkeypatch):
        import mountainash_rules.engine as eng_mod
        engine, lattices = self._setup()
        calls = []
        original = eng_mod.DimensionCompiler.compile_dimensions
        monkeypatch.setattr(
            eng_mod.DimensionCompiler, "compile_dimensions",
            lambda self, md: calls.append(1) or original(self, md),
        )
        target = lattices[0]
        engine.apply(target, {"segment": target.partition_key["segment"],
                              "region": "AU"})
        engine.apply(target, {"segment": target.partition_key["segment"],
                              "region": "NZ"})
        assert len(calls) == 1

    def test_lattice_index_routes_by_partition(self):
        engine, lattices = self._setup()
        index = engine.index(lattices)
        result = index.apply({"segment": "corp", "region": "AU"})
        rows = relation(result.survivors).to_polars()
        assert set(rows["rule_name"]) <= {"c"}

    def test_lattice_index_apply_batch(self):
        engine, lattices = self._setup()
        index = engine.index(lattices)
        contexts = pl.DataFrame({
            "segment": ["retail", "corp"], "region": ["AU", "AU"],
        })
        batch = index.apply_batch(contexts)
        surv = relation(batch.survivors).to_polars()
        assert surv["__context_id"].n_unique() == 2
```

- [ ] **Step 2: Run to verify failure** — `compile_dimensions` called twice; no `index` attribute.

- [ ] **Step 3: Implement**

`accumulator_engine.py`:

```python
import weakref

    def __init__(self, ...):
        ...
        self._apply_engines: "weakref.WeakKeyDictionary[Lattice, ExpressionRulesEngine]" = (
            weakref.WeakKeyDictionary()
        )

    def _filter_engine_for(self, lattice: Lattice) -> ExpressionRulesEngine:
        engine = self._apply_engines.get(lattice)
        if engine is None:
            engine = ExpressionRulesEngine(
                rules=lattice.combinations,
                dimension_metadata=self._build_apply_metadata(),
            )
            self._apply_engines[lattice] = engine
        return engine

    def apply(self, lattice, context, dimensions=None) -> AccumulatorResult:
        filter_engine = self._filter_engine_for(lattice)
        filter_result = filter_engine.evaluate(context, dimensions=dimensions)
        return AccumulatorResult(...)  # unchanged tail

    def index(self, lattices: list[Lattice]) -> "LatticeIndex":
        from mountainash_rules.lattice import LatticeIndex
        return LatticeIndex(self, lattices, self._context_key_dims)

    def apply_auto(self, lattices, context, dimensions=None) -> AccumulatorResult:
        """Convenience wrapper; hot paths should hold a LatticeIndex."""
        return self.index(lattices).apply(context, dimensions=dimensions)
```

`lattice.py` — document immutability on `combinations` ("Callers must not mutate the returned frame; apply-phase engines are cached against this object's identity.") and add:

```python
class LatticeIndex:
    """Partition-key routing over a set of built lattices, built once."""

    def __init__(self, engine, lattices: list["Lattice"], context_key_dims) -> None:
        self._engine = engine
        self._context_key_dims = list(context_key_dims)
        self._map: dict[tuple, Lattice] = {}
        for lattice in lattices:
            if lattice.partition_key is not None:
                key = tuple(
                    lattice.partition_key[d.dimension_name]
                    for d in self._context_key_dims
                )
            else:
                key = ()
            self._map[key] = lattice

    def apply(self, context, dimensions=None):
        key = self._engine._extract_partition_key(context)
        if key not in self._map:
            raise KeyError(f"No lattice for partition key {key!r}")
        return self._engine.apply(self._map[key], context, dimensions=dimensions)

    def apply_batch(self, contexts, **kwargs):
        """Partition contexts by CONTEXT_KEY fields; evaluate_batch per lattice."""
        import mountainash.expressions as ma
        from mountainash.relations import concat, relation

        rel = relation(contexts)
        key_fields = [d.resolved_context_field for d in self._context_key_dims]
        if not key_fields:
            (single,) = self._map.values()
            return self._engine._filter_engine_for(single).evaluate_batch(
                contexts, **kwargs
            )
        combos = rel.select(*[ma.col(f) for f in key_fields]).unique().to_dict()
        frames = []
        n = len(combos[key_fields[0]])
        for i in range(n):
            key = tuple(combos[f][i] for f in key_fields)
            if key not in self._map:
                raise KeyError(f"No lattice for partition key {key!r}")
            part = rel
            for f, v in zip(key_fields, key):
                part = part.filter(ma.col(f).eq(ma.lit(v)))
            engine = self._engine._filter_engine_for(self._map[key])
            frames.append(relation(engine.evaluate_batch(
                part.collect(), **kwargs
            ).survivors))
        merged = concat(frames).collect()
        first = self._engine._filter_engine_for(next(iter(self._map.values())))
        # Re-wrap: all partitions share dimensions and selection info
        from mountainash_rules.batch_result import BatchRuleResult
        return BatchRuleResult(
            dataframe=merged,
            active_dimensions=[
                d.dimension_name
                for d in self._engine._constraint_dims
            ],
            context_id_field=kwargs.get("context_id_field") or "__context_id",
        )
```

Caveat the implementer must handle: with a synthesised `__context_id`, per-partition batches restart ids at 0 and collide after concat. Fix inside `apply_batch`: require or synthesise a **global** id before partitioning — add `__lattice_ctx_id` via `rel.with_row_index` up front and pass `context_id_field="__lattice_ctx_id"` down to `evaluate_batch` when the caller did not supply one. Write the test for two partitions asserting 2 distinct context ids (already in Step 1) — it fails if ids collide.

- [ ] **Step 4: Run** → PASS; also `hatch run test:test-target tests/test_accumulator_apply.py -v` → PASS (apply behaviour unchanged, only cached).

- [ ] **Step 5: Export `BatchRuleResult`, `LatticeIndex` from `__init__.py`; run quick suite; commit**

```bash
git add src/mountainash_rules/ tests/test_batch_evaluation.py
git commit -m "feat: apply-phase caching - memoised filter engines and LatticeIndex

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Backend sweep

**Files:**
- Test: `tests/test_batch_evaluation.py`

**Interfaces:**
- Consumes: the repo's existing backend parametrisation (see `tests/test_accumulator_backends.py` / `tests/test_backend_purity.py` for the fixture idiom — reuse it, do not invent a new one).

- [ ] **Step 1: Write the test** — parametrise the Task 2 agreement oracle over the polars, pandas(narwhals), and ibis-duckdb fixtures used elsewhere in the suite:

```python
# Follow the exact fixture/parametrisation pattern from
# tests/test_accumulator_backends.py — construct the rules frame in each
# backend, run evaluate_batch on a 5-context polars frame, and assert
# per-context agreement with evaluate() as in TestEvaluateBatchAgreement.
# Mark ibis-polars variants xfail(reason="mountainash#78 with_row_index")
# only where the existing suite already does.
```

(The implementer copies `TestEvaluateBatchAgreement.test_batch_agrees_with_single_context_evaluation` into the parametrised harness; the assertion body is identical, only rule-frame construction varies per backend.)

- [ ] **Step 2: Run** → PASS with the same xfail set as the rest of the suite. Any *new* backend failure is a portability bug in the rank join — fix in `_evaluate_batch_frame`, not by widening xfails.

- [ ] **Step 3: Full suite + push**

Run: `hatch run test:test-quick` → PASS.

```bash
git add tests/test_batch_evaluation.py
git commit -m "test: backend sweep for evaluate_batch agreement oracle

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin develop
```
