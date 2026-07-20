# Accumulator Set-Wildcard In-Band Sentinel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Represent a set-dimension wildcard as the in-band list `[unknown_sentinel_for(dtype)]` (never null) across the filter engine and the accumulator build, so `_frontier_filter` dedupes correctly and `apply` returns correct survivors/aggregates.

**Architecture:** A new shared module `core/set_wildcard.py` holds the sentinel representation (construct / normalize / detect / validate). The filter compiler short-circuits set ternaries on the wildcard; the accumulator normalizes set columns at a named ingestion stage so `_frontier_filter` needs no change. Bool set dims, sentinel-embedding lists, and element-nulls are rejected fail-loud.

**Tech Stack:** Python 3.10+, `mountainash.expressions` / `mountainash.relations` (backend-agnostic), pydantic, polars (build materialisation), pytest.

**Design spec:** `docs/superpowers/specs/2026-07-20-accumulator-set-sentinel-wildcard-design.md`

## Global Constraints

- **Backend purity (ENFORCED).** No module under `src/mountainash_rules/` may import polars/ibis/narwhals except the three existing `# allow:`-tagged lines. This work adds **zero** new native imports and **zero** new `# allow:` tags — `ma.lit([...])`, `list.contains`, `list.unique`, `list.sort`, `list.len`, `list.drop_nulls`, `list.set_intersection`, `list.set_union` route through `mountainash.expressions`; `validate_set_columns` uses `relation(...).filter(...).collect()` + builtin `len()`. `tests/test_backend_purity.py` must stay green.
- **Wildcard = exactly `[unknown_sentinel_for(dim.data_type)]`**, element coerced to the dim's Python type (float sentinel → `-999999999.0`). Never a bare `ma.lit([sent])` — always `sentinel_list_expr(dim)`. Never a native list-dtype cast (no `pl.List(...)` — purity + it is refused).
- **Null-list = wildcard** (normalized to `[sentinel]`); **empty list `[]`** keeps concrete meaning (membership = matches nothing); **bool set dims / sentinel-embedding lists / element-nulls** are rejected with `ValueError`.
- **`_frontier_filter`, `_check_overflow`, partition routing, scalar/range/string strategies: DO NOT TOUCH.** The fix is normalization at ingestion, not the join.
- **Build is polars-internal** (`engine.py:121` `to_polars()`); set-ops/normalization run on polars. No new backend xfails; the only backend gap is the pre-existing apply-phase `mountainash#89` (narwhals list ops).
- **TDD**: failing test first, one concern at a time. `ValueError` for validation.
- Fast suite: `hatch run test:test-quick`; single: `hatch run test:test-target <nodeid>`; lint: `hatch run ruff:check <path>`.

---

### Task 1: Reject `bool` set dimensions (F1)

**Files:**
- Modify: `src/mountainash_rules/core/dimension.py` (the `_validate_strategy_fields` model validator)
- Test: `tests/core/test_dimension.py` (new test class)

**Interfaces:**
- Produces: `Dimension(match_strategy=SET_MEMBERSHIP|SET_EXCLUSION, data_type=bool)` raises `ValueError`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_dimension.py`:
```python
class TestSetDimensionBoolRejected:
    def test_bool_set_membership_rejected(self):
        import pytest
        from pydantic import ValidationError
        from mountainash_rules import Dimension
        from mountainash_rules.core.constants import MatchStrategy, DataType
        with pytest.raises(ValidationError, match="bool"):
            Dimension(dimension_name="flags", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.BOOL)

    def test_bool_set_exclusion_rejected(self):
        import pytest
        from pydantic import ValidationError
        from mountainash_rules import Dimension
        from mountainash_rules.core.constants import MatchStrategy, DataType
        with pytest.raises(ValidationError, match="bool"):
            Dimension(dimension_name="flags", match_strategy=MatchStrategy.SET_EXCLUSION, data_type=DataType.BOOL)

    def test_str_set_membership_allowed(self):
        from mountainash_rules import Dimension
        from mountainash_rules.core.constants import MatchStrategy, DataType
        d = Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)
        assert d.data_type is DataType.STR

    def test_int_set_membership_allowed(self):
        from mountainash_rules import Dimension
        from mountainash_rules.core.constants import MatchStrategy, DataType
        d = Dimension(dimension_name="tiers", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.INT)
        assert d.data_type is DataType.INT
```

- [ ] **Step 2: Run it to verify it fails**

Run: `hatch run test:test-target tests/core/test_dimension.py::TestSetDimensionBoolRejected -v`
Expected: the two `_rejected` tests FAIL (no error raised); the two `_allowed` tests PASS.

- [ ] **Step 3: Add the rejection to `_validate_strategy_fields`**

In `src/mountainash_rules/core/dimension.py`, inside `_validate_strategy_fields`, immediately before the final `return self`, add:
```python
        if self.match_strategy in (
            MatchStrategy.SET_MEMBERSHIP,
            MatchStrategy.SET_EXCLUSION,
        ):
            if self.data_type is DataType.BOOL:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses "
                    f"{self.match_strategy.value} with data_type bool; boolean "
                    f"set dimensions are not supported (no typed wildcard sentinel "
                    f"exists and a set over {{true, false}} is degenerate)"
                )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `hatch run test:test-target tests/core/test_dimension.py::TestSetDimensionBoolRejected -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the core dimension suite for regressions**

Run: `hatch run test:test-target tests/core/test_dimension.py -q`
Expected: PASS (no existing test regressed).

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/core/dimension.py tests/core/test_dimension.py
git commit -m "feat(dimension): reject bool set dimensions (no typed wildcard sentinel)"
```

---

### Task 2: Shared `core/set_wildcard.py` module (F2, F4, F6, F7)

**Files:**
- Create: `src/mountainash_rules/core/set_wildcard.py`
- Test: `tests/core/test_set_wildcard.py` (new file)

**Interfaces:**
- Produces (all imported by Tasks 3–5):
  - `sentinel_list_expr(dim: Dimension) -> BaseExpressionAPI` — a `[sentinel]` list literal, element typed to the dim.
  - `canonicalize_set_expr(col: BaseExpressionAPI) -> BaseExpressionAPI` — sort + unique.
  - `normalize_set_expr(dim, col) -> BaseExpressionAPI` — null→`[sentinel]`, concrete→sorted-unique.
  - `set_wildcard_predicate(dim, col) -> BaseExpressionAPI` — `col.list.contains(sentinel)` (post-normalization).
  - `validate_set_columns(rules_rel, set_dims: list[Dimension]) -> None` — **portable** (both engines); raises `ValueError` on embedded-sentinel lists. Uses only `list.contains`/`list.len`.
  - `validate_set_no_null_elements(rules_rel, set_dims: list[Dimension]) -> None` — **accumulator build only** (uses `list.drop_nulls`, Ibis-unsupported); raises `ValueError` on element-null lists.
- Consumes: `unknown_sentinel_for`, `DataType` from `core/constants.py`; `Dimension` from `core/dimension.py`.

- [ ] **Step 1: Write the failing helper tests**

Create `tests/core/test_set_wildcard.py`:
```python
"""Tests for the shared set-wildcard sentinel helpers."""

import polars as pl
import pytest

from mountainash_rules.core.constants import MatchStrategy, DataType
from mountainash_rules.core.dimension import Dimension
from mountainash_rules.core.set_wildcard import (
    sentinel_list_expr,
    canonicalize_set_expr,
    normalize_set_expr,
    set_wildcard_predicate,
    validate_set_columns,
    validate_set_no_null_elements,
)
from mountainash.relations import relation


def _str_dim():
    return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)


def _float_dim():
    return Dimension(dimension_name="scores", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.FLOAT)


class TestNormalizeAndDetect:
    def test_null_becomes_sentinel_list(self):
        dim = _str_dim()
        df = pl.DataFrame({"region": pl.Series("region", [None, ["AU"]], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(normalize_set_expr(dim, __import__("mountainash").col("region")).alias("n").compile(df, booleanizer=None))
        assert out["n"].to_list() == [["<NA>"], ["AU"]]
        assert out["n"].dtype == pl.List(pl.Utf8)

    def test_concrete_list_sorted_and_deduped(self):
        dim = _str_dim()
        import mountainash as ma
        df = pl.DataFrame({"region": pl.Series("region", [["UK", "NZ", "NZ"]], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(normalize_set_expr(dim, ma.col("region")).alias("n").compile(df, booleanizer=None))
        assert out["n"].to_list() == [["NZ", "UK"]]

    def test_wildcard_predicate_true_on_sentinel_false_on_concrete(self):
        dim = _str_dim()
        import mountainash as ma
        df = pl.DataFrame({"region": pl.Series("region", [["<NA>"], ["AU"], []], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(set_wildcard_predicate(dim, ma.col("region")).alias("w").compile(df, booleanizer=None))
        assert out["w"].to_list() == [True, False, False]

    def test_float_dim_sentinel_list_is_float_typed(self):
        dim = _float_dim()
        import mountainash as ma
        df = pl.DataFrame({"scores": pl.Series("scores", [None], dtype=pl.List(pl.Float64))})
        out = df.with_columns(normalize_set_expr(dim, ma.col("scores")).alias("n").compile(df, booleanizer=None))
        assert out["n"].dtype == pl.List(pl.Float64)
        assert out["n"].to_list() == [[-999999999.0]]

    def test_normalize_idempotent(self):
        dim = _str_dim()
        import mountainash as ma
        df = pl.DataFrame({"region": pl.Series("region", [None, ["UK", "NZ"]], dtype=pl.List(pl.Utf8))})
        once = df.with_columns(normalize_set_expr(dim, ma.col("region")).alias("region").compile(df, booleanizer=None))
        twice = once.with_columns(normalize_set_expr(dim, ma.col("region")).alias("region").compile(once, booleanizer=None))
        assert once["region"].to_list() == twice["region"].to_list()

    def test_normalize_idempotent_float_and_date(self):
        # F9: idempotence must hold across dtypes, not just str.
        import datetime as _dt
        import mountainash as ma
        from mountainash_rules.core.constants import DataType
        float_dim = _float_dim()
        fdf = pl.DataFrame({"scores": pl.Series("scores", [None, [2.5, 1.5]], dtype=pl.List(pl.Float64))})
        f1 = fdf.with_columns(normalize_set_expr(float_dim, ma.col("scores")).alias("scores").compile(fdf, booleanizer=None))
        f2 = f1.with_columns(normalize_set_expr(float_dim, ma.col("scores")).alias("scores").compile(f1, booleanizer=None))
        assert f1["scores"].to_list() == f2["scores"].to_list()
        assert f1["scores"].to_list() == [[-999999999.0], [1.5, 2.5]]

        date_dim = Dimension(dimension_name="days", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.DATE)
        d = [_dt.date(2020, 1, 2), _dt.date(2020, 1, 1)]
        ddf = pl.DataFrame({"days": pl.Series("days", [None, d], dtype=pl.List(pl.Date))})
        d1 = ddf.with_columns(normalize_set_expr(date_dim, ma.col("days")).alias("days").compile(ddf, booleanizer=None))
        d2 = d1.with_columns(normalize_set_expr(date_dim, ma.col("days")).alias("days").compile(d1, booleanizer=None))
        assert d1["days"].to_list() == d2["days"].to_list()
        assert d1["days"].dtype == pl.List(pl.Date)


class TestCanonicalize:
    def test_sort_and_dedupe(self):
        import mountainash as ma
        df = pl.DataFrame({"c": pl.Series("c", [["UK", "NZ", "UK"]], dtype=pl.List(pl.Utf8))})
        out = df.with_columns(canonicalize_set_expr(ma.col("c")).alias("c2").compile(df, booleanizer=None))
        assert out["c2"].to_list() == [["NZ", "UK"]]


class TestValidateSetColumns:
    def test_embedded_sentinel_rejected(self):
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [["AU", "<NA>"]], dtype=pl.List(pl.Utf8))})
        with pytest.raises(ValueError, match="sentinel"):
            validate_set_columns(relation(rules), [dim])

    def test_embedded_sentinel_passes_null_element_check(self):
        # validate_set_columns is reservation-only; whole-list null + concrete OK.
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [None, ["AU"], ["<NA>"]], dtype=pl.List(pl.Utf8))})
        validate_set_columns(relation(rules), [dim])  # no raise

    def test_null_element_rejected_by_dedicated_check(self):
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [["AU", None]], dtype=pl.List(pl.Utf8))})
        with pytest.raises(ValueError, match="null element"):
            validate_set_no_null_elements(relation(rules), [dim])

    def test_null_element_check_allows_whole_list_null(self):
        dim = _str_dim()
        rules = pl.DataFrame({"region": pl.Series("region", [None, ["AU"]], dtype=pl.List(pl.Utf8))})
        validate_set_no_null_elements(relation(rules), [dim])  # no raise

    def test_no_set_dims_is_noop(self):
        validate_set_columns(relation(pl.DataFrame({"x": [1]})), [])
        validate_set_no_null_elements(relation(pl.DataFrame({"x": [1]})), [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `hatch run test:test-target tests/core/test_set_wildcard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mountainash_rules.core.set_wildcard'`.

- [ ] **Step 3: Create the module**

Create `src/mountainash_rules/core/set_wildcard.py`:
```python
"""Shared in-band sentinel representation for set-dimension wildcards.

A SET_MEMBERSHIP/SET_EXCLUSION wildcard is the single-element list
``[unknown_sentinel_for(dim.data_type)]`` — never a null. These helpers are the
single source of truth for both the filter engine (``core/compiler.py``) and the
accumulator engine (``engines/accumulator/``), so the two cannot diverge.

The sentinel is a reserved, out-of-domain value; ``validate_set_columns``
enforces that a concrete rule list never embeds it, so ``set_wildcard_predicate``
is unambiguous.
"""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import DataType, unknown_sentinel_for
from mountainash_rules.core.dimension import Dimension


def _typed_sentinel(dim: Dimension) -> t.Any:
    """The dimension's wildcard sentinel, coerced to the dim's Python element type."""
    sent = unknown_sentinel_for(dim.data_type)
    return float(sent) if dim.data_type == DataType.FLOAT else sent


def sentinel_list_expr(dim: Dimension) -> BaseExpressionAPI:
    """A ``[sentinel]`` list literal whose element carries the dim's Python type.

    Element coercion (not a native list-dtype cast) keeps this backend-pure: a
    Python-float element yields a ``List(Float64)`` literal with no cast.
    """
    return ma.lit([_typed_sentinel(dim)])


def canonicalize_set_expr(col: BaseExpressionAPI) -> BaseExpressionAPI:
    """Sort + dedupe a list column so equal sets compare/fingerprint identically."""
    return col.list.unique().list.sort()


def normalize_set_expr(dim: Dimension, col: BaseExpressionAPI) -> BaseExpressionAPI:
    """Null list -> ``[sentinel]``; concrete list -> sorted-unique. The one normaliser.

    Idempotent: re-applying to an already-normalized column is a no-op.
    """
    return ma.when(col.is_null()).then(sentinel_list_expr(dim)).otherwise(
        canonicalize_set_expr(col)
    )


def set_wildcard_predicate(dim: Dimension, col: BaseExpressionAPI) -> BaseExpressionAPI:
    """True when ``col`` (post-normalization) is the wildcard.

    Because ``validate_set_columns`` rejects any concrete list embedding the
    sentinel, ``list.contains(sentinel)`` is true iff the list is exactly
    ``[sentinel]``. Must be evaluated after normalization (``contains`` returns
    null on a null cell).
    """
    return col.list.contains(ma.lit(_typed_sentinel(dim)))


def _embedded_sentinel_predicate(dim: Dimension, field: str) -> BaseExpressionAPI:
    """True for a non-null list that contains the sentinel but is not ``[sentinel]``."""
    col = ma.col(field)
    has_sentinel = col.list.contains(ma.lit(_typed_sentinel(dim)))
    length = col.list.len()
    return col.is_not_null().__and__(has_sentinel.__and__(length.ne(ma.lit(1))))


def _null_element_predicate(field: str) -> BaseExpressionAPI:
    """True for a non-null list that contains a null element.

    Uses ``list.drop_nulls`` — mountainash's IBIS backend raises
    ``BackendCapabilityError`` for this op, so callers must only run this on the
    polars-internal accumulator build path (see ``validate_set_no_null_elements``).
    """
    col = ma.col(field)
    length = col.list.len()
    non_null_length = col.list.drop_nulls().list.len()
    return col.is_not_null().__and__(length.ne(non_null_length))


def validate_set_columns(rules_rel: t.Any, set_dims: list[Dimension]) -> None:
    """Raise ``ValueError`` if a concrete set-rule list embeds the reserved sentinel.

    Portable — uses only ``list.contains`` + ``list.len`` (Ibis/Narwhals-safe), so
    it runs in BOTH engines on any backend. A whole-list null is valid (the
    wildcard). ``rules_rel`` is a ``mountainash`` relation; ``collect`` returns a
    native frame that supports builtin ``len`` (backend-pure — no native import).
    """
    for dim in set_dims:
        field = dim.resolved_rule_field
        embedded = rules_rel.filter(_embedded_sentinel_predicate(dim, field)).collect()
        if len(embedded) > 0:
            raise ValueError(
                f"Dimension '{dim.dimension_name}': a concrete rule list embeds the "
                f"reserved wildcard sentinel {unknown_sentinel_for(dim.data_type)!r}. "
                f"The sentinel is only valid as the sole element (the wildcard)."
            )


def validate_set_no_null_elements(rules_rel: t.Any, set_dims: list[Dimension]) -> None:
    """Raise ``ValueError`` if a set-rule list contains a null element.

    Uses ``list.drop_nulls`` (Ibis-unsupported), so this is called ONLY on the
    polars-internal accumulator build path. The filter engine does not call it:
    ``t_is_in`` tolerates a null element (it matches nothing), so a standalone
    filter engine over Ibis set rules is unaffected.
    """
    for dim in set_dims:
        field = dim.resolved_rule_field
        null_elem = rules_rel.filter(_null_element_predicate(field)).collect()
        if len(null_elem) > 0:
            raise ValueError(
                f"Dimension '{dim.dimension_name}': a rule list contains a null "
                f"element. Element-level nulls are not allowed; use a whole-list "
                f"null (or omit the cell) for a wildcard."
            )
```

- [ ] **Step 4: Run the helper tests to verify they pass**

Run: `hatch run test:test-target tests/core/test_set_wildcard.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run backend purity**

Run: `hatch run test:test-target tests/test_backend_purity.py -q`
Expected: PASS — the new module imports only `mountainash.expressions` (no polars/ibis/narwhals).

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/core/set_wildcard.py tests/core/test_set_wildcard.py
git commit -m "feat(core): shared in-band sentinel set-wildcard helpers + validation"
```

---

### Task 3: Filter compiler + filter-engine validation (F3)

**Files:**
- Modify: `src/mountainash_rules/core/compiler.py` (`_compile_set_membership`, `_compile_set_exclusion`)
- Modify: `src/mountainash_rules/engines/filter/engine.py` (validate set columns once — both single and batch paths)
- Test: `tests/core/test_compiler.py` (filter compiler ternary tests); `tests/filter/test_engine.py` (engine validation, single + batch)

**Interfaces:**
- Consumes: `normalize_set_expr`, `set_wildcard_predicate`, `validate_set_columns` (Task 2).
- Produces: filter set ternaries that short-circuit the wildcard to 0 and normalize null input; the filter engine rejects embedded-sentinel set rule lists on **both** `evaluate`/`explain` (via `_scored_relation`) and `evaluate_batch` (via `_evaluate_batch_frame`).

- [ ] **Step 1: Write the failing filter compiler tests**

Add to `tests/core/test_compiler.py` (top-of-file imports already include `pl`, `DimensionCompiler`, `Dimension`, `MatchStrategy`; add `from mountainash_rules.core.constants import CTX_PREFIX` if not present):
```python
class TestSetMembershipTernary:
    def _compile(self, dim):
        from mountainash_rules.core.compiler import DimensionCompiler
        return DimensionCompiler().compile_dimension(dim)

    def _dim(self):
        from mountainash_rules.core.constants import DataType
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)

    def test_wildcard_rule_is_ternary_zero(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["<NA>"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [0]

    def test_context_in_set_is_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [1]

    def test_context_out_of_set_is_minus_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["US"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [-1]

    def test_null_rule_list_normalizes_to_wildcard(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [None], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [0]


class TestSetExclusionTernary:
    def _compile(self, dim):
        from mountainash_rules.core.compiler import DimensionCompiler
        return DimensionCompiler().compile_dimension(dim)

    def _dim(self):
        from mountainash_rules.core.constants import DataType
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_EXCLUSION, data_type=DataType.STR)

    def test_wildcard_rule_is_zero(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["<NA>"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [0]

    def test_context_in_excluded_set_is_minus_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["AU"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [-1]

    def test_context_not_in_excluded_set_is_one(self):
        from mountainash_rules.core.constants import CTX_PREFIX
        expr = self._compile(self._dim())
        df = pl.DataFrame({
            "region": pl.Series("region", [["AU"]], dtype=pl.List(pl.Utf8)),
            CTX_PREFIX + "region": ["NZ"],
        })
        out = df.with_columns(expr.alias("t").compile(df, booleanizer=None))
        assert out["t"].to_list() == [1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `hatch run test:test-target tests/core/test_compiler.py::TestSetMembershipTernary tests/core/test_compiler.py::TestSetExclusionTernary -v`
Expected: `test_wildcard_rule_is_ternary_zero` / `test_wildcard_rule_is_zero` FAIL — current code returns `-1` for a `["<NA>"]` rule (no short-circuit); `test_null_rule_list_normalizes_to_wildcard` passes today via `t_is_in`'s null handling but must keep passing after the change.

- [ ] **Step 3: Rewrite the two filter set methods**

In `src/mountainash_rules/core/compiler.py`, add to the imports block (next to the other `mountainash_rules.core` imports):
```python
from mountainash_rules.core.set_wildcard import normalize_set_expr, set_wildcard_predicate
```
Replace `_compile_set_membership` and `_compile_set_exclusion` with:
```python
    def _compile_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
        """SET_MEMBERSHIP: context value in the rule list; wildcard rule -> ternary 0."""
        rule_col = normalize_set_expr(dim, ma.col(dim.resolved_rule_field))
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels_for(dim.data_type))
        is_wild = set_wildcard_predicate(dim, rule_col)
        return ma.when(is_wild).then(0).otherwise(ctx_col.t_is_in(rule_col))

    def _compile_set_exclusion(self, dim: Dimension) -> BaseExpressionAPI:
        """SET_EXCLUSION: context value NOT in the rule list; wildcard rule -> ternary 0."""
        rule_col = normalize_set_expr(dim, ma.col(dim.resolved_rule_field))
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels_for(dim.data_type))
        is_wild = set_wildcard_predicate(dim, rule_col)
        return ma.when(is_wild).then(0).otherwise(ctx_col.t_is_not_in(rule_col))
```

- [ ] **Step 4: Run the compiler tests to verify they pass**

Run: `hatch run test:test-target tests/core/test_compiler.py::TestSetMembershipTernary tests/core/test_compiler.py::TestSetExclusionTernary -v`
Expected: PASS.

- [ ] **Step 5: Write the failing filter-engine validation tests (single AND batch)**

Add to `tests/filter/test_engine.py` (the filter-engine suite; uses the public `ExpressionRulesEngine`):
```python
class TestFilterEngineRejectsInvalidSetRules:
    def _meta(self):
        from mountainash_rules import Dimension, DimensionsMetadata
        from mountainash_rules.core.constants import MatchStrategy, DataType
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR),
        ])

    def _rules(self):
        import polars as pl
        return pl.DataFrame({
            "rule_name": ["R1"],
            "region": pl.Series("region", [["AU", "<NA>"]], dtype=pl.List(pl.Utf8)),
        })

    def test_embedded_sentinel_rejected_on_evaluate(self):
        import pytest
        from mountainash_rules import ExpressionRulesEngine
        engine = ExpressionRulesEngine(rules=self._rules(), dimension_metadata=self._meta())
        with pytest.raises(ValueError, match="sentinel"):
            engine.evaluate({"region": "AU"})

    def test_embedded_sentinel_rejected_on_evaluate_batch(self):
        # evaluate_batch does NOT route through _scored_relation — this covers the
        # batch path explicitly (a batch-first call must still validate).
        import polars as pl
        import pytest
        from mountainash_rules import ExpressionRulesEngine
        engine = ExpressionRulesEngine(rules=self._rules(), dimension_metadata=self._meta())
        contexts = pl.DataFrame({"region": ["AU"]})
        with pytest.raises(ValueError, match="sentinel"):
            engine.evaluate_batch(contexts)
```
> Verified: `ExpressionRulesEngine(rules=..., dimension_metadata=...)` (`engine.py:55`); `evaluate` accepts a dict; `evaluate_batch(contexts)` accepts a frame. All three names are public root imports. Check `tests/filter/test_engine.py`'s existing top-of-file imports and reuse them where present.

- [ ] **Step 6: Run to verify it fails**

Run: `hatch run test:test-target tests/filter/test_engine.py::TestFilterEngineRejectsInvalidSetRules -v`
Expected: BOTH FAIL — no `ValueError` raised (invalid list silently treated as wildcard) on either the single or batch path.

- [ ] **Step 7: Add memoized validation to the filter engine**

In `src/mountainash_rules/engines/filter/engine.py`, add the import near the other `mountainash_rules` imports:
```python
from mountainash_rules.core.set_wildcard import validate_set_columns
from mountainash_rules.core.constants import MatchStrategy
```
In `__init__` (near `self._rules = rules`, line 74), add the memo flag:
```python
        self._set_dims_validated = False
```
Add a shared one-time validation method:
```python
    def _validate_set_rules_once(self) -> None:
        """Reject set rule lists that embed the reserved sentinel — once, portably.

        Called from BOTH scoring entry points (single and batch) because
        evaluate_batch does not route through _scored_relation. metadata is None
        when the engine was built from dimension_expressions (no Dimension objects
        to inspect), so skip that case.
        """
        if self._metadata is None or self._set_dims_validated:
            return
        set_dims = [
            d for d in self._metadata.dimensions
            if d.match_strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION)
        ]
        validate_set_columns(relation(self._rules), set_dims)
        self._set_dims_validated = True
```
Call it at the top of **`_scored_relation`** (line 407) and at the top of **`_evaluate_batch_frame`** (line 303) — both, because `evaluate_batch` → `_evaluate_batch_frame` (line 248/259) builds its own `rules_rel = relation(self._rules)` at line 313 and never calls `_scored_relation`. In each, add as the first statement:
```python
        self._validate_set_rules_once()
```
> Verified: metadata attribute is `self._metadata` (`engine.py:69`), `None` on the `dimension_expressions` path. `evaluate`/`explain` go through `_scored_relation`; `evaluate_batch` goes through `_evaluate_batch_frame`. Calling `_validate_set_rules_once` in both covers every entry point; the memo flag makes repeat calls cheap. `relation` is already imported in this module.

- [ ] **Step 8: Run the validation tests + the filter suite**

Run: `hatch run test:test-target tests/filter/test_engine.py::TestFilterEngineRejectsInvalidSetRules -v`
Expected: PASS (both single and batch).
Run: `hatch run test:test-target tests/core/test_compiler.py tests/filter/ -q`
Expected: PASS — existing filter/set tests unaffected (null-list rules still normalize to wildcard; concrete lists unchanged). If a pre-existing set test used a null rule list and asserted wildcard behaviour, it still passes.

- [ ] **Step 9: Ruff + commit**

```bash
hatch run ruff:check src/mountainash_rules/core/compiler.py src/mountainash_rules/engines/filter/engine.py
git add src/mountainash_rules/core/compiler.py src/mountainash_rules/engines/filter/engine.py tests/core/test_compiler.py
git commit -m "feat(filter): set-wildcard ternary short-circuit + reject invalid set rule lists"
```

---

### Task 4: Accumulator compiler set branches (coalesce / compatible / NA flag)

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/compiler.py`
- Test: `tests/accumulator/test_compiler.py`

**Interfaces:**
- Consumes: `set_wildcard_predicate`, `canonicalize_set_expr`, `sentinel_list_expr` (Task 2). Assumes co_/rhs columns are already normalized (Task 5 guarantees this at ingestion).
- Produces: `SET_MEMBERSHIP`/`SET_EXCLUSION` support in `compile_compatible`, `compile_coalesce`, `compile_coalesce_na_flag`.

- [ ] **Step 1: Write the failing compiler tests**

Add to `tests/accumulator/test_compiler.py` (imports `AccumulatorCompiler`, `Dimension`, `MatchStrategy`, `pl` already present; add `from mountainash_rules.core.constants import DataType`):
```python
class TestSetMembershipCompatible:
    def _dim(self):
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)

    def test_non_empty_intersection_compatible(self, compiler):
        expr = compiler.compile_compatible(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU", "NZ"], ["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["NZ", "UK"], ["US", "CA"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.alias("c").compile(df, booleanizer=None))
        assert out["c"].to_list() == [True, False]

    def test_wildcard_either_side_compatible(self, compiler):
        expr = compiler.compile_compatible(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"], ["AU"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["US"], ["<NA>"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.alias("c").compile(df, booleanizer=None))
        assert out["c"].to_list() == [True, True]


class TestSetMembershipCoalesce:
    def _dim(self):
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR)

    def test_intersection_canonicalized(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        assert len(exprs) == 1
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU", "NZ", "UK"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["UK", "NZ", "US"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["NZ", "UK"]]  # sorted-unique

    def test_wildcard_passthrough(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"], ["AU"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["AU"], ["<NA>"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["AU"], ["AU"]]

    def test_both_wildcard_stays_sentinel(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["<NA>"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["<NA>"]]
        assert out["co_region"].dtype == pl.List(pl.Utf8)


class TestSetExclusionCoalesce:
    def _dim(self):
        return Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_EXCLUSION, data_type=DataType.STR)

    def test_union_canonicalized(self, compiler):
        exprs = compiler.compile_coalesce(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU", "NZ"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["NZ", "US"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(exprs[0].compile(df, booleanizer=None))
        assert out["co_region"].to_list() == [["AU", "NZ", "US"]]

    def test_always_compatible(self, compiler):
        expr = compiler.compile_compatible(self._dim())
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["AU"], ["<NA>"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["NZ"], ["US"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.alias("c").compile(df, booleanizer=None))
        assert out["c"].to_list() == [True, True]


class TestSetNaFlag:
    def _dim(self, strategy):
        return Dimension(dimension_name="region", match_strategy=strategy, data_type=DataType.STR)

    def test_na_flag_from_final_coalesced_value(self, compiler):
        # wildcard+wildcard -> 1 ; wildcard+concrete -> 0 ; concrete+concrete -> 0
        expr = compiler.compile_coalesce_na_flag(self._dim(MatchStrategy.SET_MEMBERSHIP))
        df = pl.DataFrame({
            "co_region": pl.Series("co_region", [["<NA>"], ["<NA>"], ["AU"]], dtype=pl.List(pl.Utf8)),
            "region_rhs": pl.Series("region_rhs", [["<NA>"], ["AU"], ["NZ"]], dtype=pl.List(pl.Utf8)),
        })
        out = df.with_columns(expr.compile(df, booleanizer=None))
        assert out["co_region_na"].to_list() == [1, 0, 0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `hatch run test:test-target tests/accumulator/test_compiler.py::TestSetMembershipCompatible tests/accumulator/test_compiler.py::TestSetMembershipCoalesce tests/accumulator/test_compiler.py::TestSetExclusionCoalesce tests/accumulator/test_compiler.py::TestSetNaFlag -v`
Expected: FAIL — `ValueError: Strategy SET_MEMBERSHIP not supported by accumulator` (compatible/coalesce), and the NA-flag `else` branch mis-computes for lists.

- [ ] **Step 3: Add the imports and the set branches**

In `src/mountainash_rules/engines/accumulator/compiler.py`, add to the imports:
```python
from mountainash_rules.core.set_wildcard import (
    set_wildcard_predicate,
    canonicalize_set_expr,
    sentinel_list_expr,
)
```
Add a `case` to `compile_compatible`'s `match` (before `case _:`):
```python
            case MatchStrategy.SET_MEMBERSHIP:
                return self._compatible_set_membership(dim)
            case MatchStrategy.SET_EXCLUSION:
                return ma.lit(True)
```
Add a `case` to `compile_coalesce`'s `match` (before `case _:`):
```python
            case MatchStrategy.SET_MEMBERSHIP:
                return self._coalesce_set(dim, "intersection")
            case MatchStrategy.SET_EXCLUSION:
                return self._coalesce_set(dim, "union")
```
Change `compile_coalesce_na_flag` to insert a set branch. Replace its body with:
```python
    def compile_coalesce_na_flag(self, dim: Dimension) -> BaseExpressionAPI:
        """Expression for the coalesced NA flag (1 = combination leaves dim unconstrained)."""
        if dim.match_strategy == MatchStrategy.RANGE:
            co_min_s, co_max_s, rhs_min_s, rhs_max_s = self._range_sentinel_checks(dim)
            all_sentinel = (
                co_min_s.__and__(rhs_min_s)
                .__and__(co_max_s)
                .__and__(rhs_max_s)
            )
            return all_sentinel.cast(int).alias(f"co_{dim.dimension_name}_na")
        if dim.match_strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
            co_w, rhs_w = self._set_wild_checks(dim)
            field = dim.resolved_rule_field
            return co_w.__and__(rhs_w).cast(int).alias(f"co_{field}_na")
        co_sentinel, rhs_sentinel = self._sentinel_checks(dim)
        field = dim.resolved_rule_field
        return co_sentinel.__and__(rhs_sentinel).cast(int).alias(f"co_{field}_na")
```
> The set NA flag is `co_wild AND rhs_wild`, which equals the wildcard status of the FINAL coalesced value: coalesce yields `[sentinel]` iff both inputs are wildcard (wildcard+concrete → the concrete side; concrete+concrete → intersection/union). This satisfies spec F8 while remaining computable from the LHS/RHS inputs in the same `with_columns` pass.

Add the two helper methods at the end of the class:
```python
    def _set_wild_checks(self, dim: Dimension) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        field = dim.resolved_rule_field
        co_w = set_wildcard_predicate(dim, ma.col(f"co_{field}"))
        rhs_w = set_wildcard_predicate(dim, ma.col(f"{field}_rhs"))
        return co_w, rhs_w

    def _compatible_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
        co_w, rhs_w = self._set_wild_checks(dim)
        field = dim.resolved_rule_field
        intersection_nonempty = (
            ma.col(f"co_{field}")
            .list.set_intersection(ma.col(f"{field}_rhs"))
            .list.len()
            .gt(ma.lit(0))
        )
        return co_w.__or__(rhs_w).__or__(intersection_nonempty)

    def _coalesce_set(self, dim: Dimension, op: str) -> list[BaseExpressionAPI]:
        co_w, rhs_w = self._set_wild_checks(dim)
        field = dim.resolved_rule_field
        co = ma.col(f"co_{field}")
        rhs = ma.col(f"{field}_rhs")
        combined = (
            co.list.set_intersection(rhs) if op == "intersection" else co.list.set_union(rhs)
        )
        new_val = (
            ma.when(co_w.__and__(rhs_w)).then(sentinel_list_expr(dim))
            .when(co_w).then(rhs)
            .when(rhs_w).then(co)
            .otherwise(canonicalize_set_expr(combined))
            .alias(f"co_{field}")
        )
        return [new_val]
```

- [ ] **Step 4: Run the compiler tests to verify they pass**

Run: `hatch run test:test-target tests/accumulator/test_compiler.py::TestSetMembershipCompatible tests/accumulator/test_compiler.py::TestSetMembershipCoalesce tests/accumulator/test_compiler.py::TestSetExclusionCoalesce tests/accumulator/test_compiler.py::TestSetNaFlag -v`
Expected: PASS.

- [ ] **Step 5: Run accumulator compiler suite + purity + ruff**

Run: `hatch run test:test-target tests/accumulator/test_compiler.py tests/test_backend_purity.py -q`
Expected: PASS.
Run: `hatch run ruff:check src/mountainash_rules/engines/accumulator/compiler.py`
Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/compiler.py tests/accumulator/test_compiler.py
git commit -m "feat(accumulator): set coalescing/compatible/NA-flag on in-band sentinel"
```

---

### Task 5: Accumulator engine — normalization stage + seed NA + pre-frontier assertion (F5)

**Files:**
- Modify: `src/mountainash_rules/engines/accumulator/engine.py` (`build`, new `_normalize_set_columns`, `_create_anchor` NA loop, pre-frontier assertion)
- Test: `tests/accumulator/test_engine.py`

**Interfaces:**
- Consumes: `validate_set_columns`, `validate_set_no_null_elements`, `normalize_set_expr`, `set_wildcard_predicate` (Task 2); the accumulator compiler set branches (Task 4).
- Produces: `build(rules)` with any set dimension produces a lattice whose set `co_` columns are non-null and canonical; the frontier dedupes wildcard combinations.

- [ ] **Step 1: Write the failing build regression tests**

Add to `tests/accumulator/test_engine.py` (helpers `_rows`, `relation` already imported; add `from mountainash_rules.core.constants import DataType` if absent):
```python
class TestSetMembershipBuildFrontier:
    def _metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR),
        ])

    def test_three_wildcard_rules_collapse_to_single_maximal(self):
        # THE anchor regression: 3 wildcard-set rules must dedupe to pp=30, NOT 7 combos.
        rules = pl.DataFrame({
            "rule_name": ["R1", "R2", "R3"],
            "region": pl.Series("region", [None, None, None], dtype=pl.List(pl.Utf8)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        rows = _rows(engine.build(rules).combinations)
        assert set(rows["__prime_product"]) == {30}

    def test_two_membership_rules_coalesce_to_intersection(self):
        rules = pl.DataFrame({
            "rule_name": ["R1", "R2"],
            "region": pl.Series("region", [["AU", "NZ", "UK"], ["NZ", "UK", "US"]], dtype=pl.List(pl.Utf8)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        rows = _rows(engine.build(rules).combinations)
        by_pp = dict(zip(rows["__prime_product"], rows["co_region"]))
        assert sorted(by_pp[6]) == ["NZ", "UK"]

    def test_same_set_different_order_dedupes(self):
        # Ordering: two rules whose sets are equal up to order must dedupe to the
        # single maximal combination — assert the EXACT surviving prime-product set.
        rules = pl.DataFrame({
            "rule_name": ["R1", "R2"],
            "region": pl.Series("region", [["UK", "NZ"], ["NZ", "UK"]], dtype=pl.List(pl.Utf8)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        rows = _rows(engine.build(rules).combinations)
        # R1 and R2 have equal (canonicalized) sets, are compatible (non-empty
        # intersection), so {R1,R2} (pp=6) dominates both singletons {2},{3}.
        assert set(rows["__prime_product"]) == {6}
        by_pp = dict(zip(rows["__prime_product"], rows["co_region"]))
        assert by_pp[6] == ["NZ", "UK"]  # canonical (sorted-unique)


class TestSetExclusionBuild:
    def _metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_EXCLUSION, data_type=DataType.STR),
        ])

    def test_two_exclusion_rules_coalesce_to_union(self):
        rules = pl.DataFrame({
            "rule_name": ["R1", "R2"],
            "region": pl.Series("region", [["AU", "NZ"], ["NZ", "US"]], dtype=pl.List(pl.Utf8)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        rows = _rows(engine.build(rules).combinations)
        by_pp = dict(zip(rows["__prime_product"], rows["co_region"]))
        assert by_pp[6] == ["AU", "NZ", "US"]


class TestSetBuildValidation:
    def _metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR),
        ])

    def test_embedded_sentinel_rejected(self):
        import pytest
        rules = pl.DataFrame({
            "rule_name": ["R1"],
            "region": pl.Series("region", [["AU", "<NA>"]], dtype=pl.List(pl.Utf8)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        with pytest.raises(ValueError, match="sentinel"):
            engine.build(rules)
```

- [ ] **Step 2: Run to verify it fails**

Run: `hatch run test:test-target tests/accumulator/test_engine.py::TestSetMembershipBuildFrontier tests/accumulator/test_engine.py::TestSetExclusionBuild tests/accumulator/test_engine.py::TestSetBuildValidation -v`
Expected: FAIL — the accumulator does not yet normalize set columns. With Task 4's compiler but no normalization, wildcard `co_region` is null, so `set_wildcard_predicate` (a `list.contains` on a null list) yields **null**, compatibility becomes null (not true), and `_expand_level`'s filter drops those expansions — `test_three_wildcard_rules_collapse_to_single_maximal` returns **too few** combinations (e.g. only singletons `{2,3,5}`), which is `!= {30}`, so it fails. `test_two_membership_rules_coalesce_to_intersection` (concrete, non-null lists) may already pass — it is an integration check, not a fail-first unit. `TestSetBuildValidation` fails because no validation runs yet. (The point of the anchor test is the post-normalization assertion `== {30}` in Step 7, not the exact pre-normalization value.)

- [ ] **Step 3: Add the normalization stage import**

In `src/mountainash_rules/engines/accumulator/engine.py`, add near the other `mountainash_rules.core` imports:
```python
from mountainash_rules.core.set_wildcard import (
    validate_set_columns,
    validate_set_no_null_elements,
    normalize_set_expr,
    set_wildcard_predicate,
)
```

- [ ] **Step 4: Add the `_normalize_set_columns` method and call it in `build`**

Add this method to `AccumulatorEngine` (near `_create_anchor`):
```python
    def _set_dims(self) -> list[Dimension]:
        return [
            d for d in self._constraint_dims
            if d.match_strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION)
        ]

    def _normalize_set_columns(self, rules_pl: t.Any) -> t.Any:
        """Validate + normalize every set-dimension rule column to the non-null,
        canonical in-band-sentinel form. Runs for EVERY build path before the
        empty-frame branch and the anchor, so set co_ columns are never null."""
        set_dims = self._set_dims()
        if not set_dims:
            return rules_pl
        rel = relation(rules_pl)
        validate_set_columns(rel, set_dims)          # reservation (portable)
        validate_set_no_null_elements(rel, set_dims)  # element-nulls (polars build only)
        rel = rel.with_columns(*[
            normalize_set_expr(dim, ma.col(dim.resolved_rule_field)).alias(dim.resolved_rule_field)
            for dim in set_dims
        ])
        return rel.to_polars()
```
In `build`, right after `rules_pl = rel.to_polars()` (currently `engine.py:122`) and BEFORE `n_rules = len(rules_pl)`:
```python
        rules_pl = self._normalize_set_columns(rules_pl)
```

- [ ] **Step 5: Fix the seed NA loop for set dimensions**

In `_create_anchor`, the `# Add NA flag columns` loop currently does `ma.col(field).eq(ma.lit(sentinel))` for the non-range branch — wrong for list columns. Replace the non-range `else` branch of that loop so set dims use `set_wildcard_predicate`:
```python
            else:
                field = dim.resolved_rule_field
                if dim.match_strategy in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION):
                    na_exprs.append(
                        set_wildcard_predicate(dim, ma.col(field))
                        .cast(int)
                        .alias(f"co_{field}_na")
                    )
                else:
                    sentinel = unknown_sentinel_for(dim.data_type)
                    na_exprs.append(
                        ma.col(field).eq(ma.lit(sentinel))
                        .cast(int)
                        .alias(f"co_{field}_na")
                    )
```
(The `co_` value loop above it needs no change — for a set dim it copies the already-normalized `field` into `co_{field}`.)

- [ ] **Step 6: Add the pre-frontier non-null assertion**

In `build`, replace the frontier line `result = self._frontier_filter(all_combos)` with a guarded version:
```python
        self._assert_set_columns_non_null(all_combos)
        result = self._frontier_filter(all_combos)
```
Add the assertion method:
```python
    def _assert_set_columns_non_null(self, all_combos: t.Any) -> None:
        """Safety net for the frontier 'no change' invariant: every set co_ column
        must be non-null before the dominance self-join (null keys silently defeat
        pruning). Runs only when set dims are present."""
        set_dims = self._set_dims()
        if not set_dims:
            return
        cols = [f"co_{d.resolved_rule_field}" for d in set_dims]
        checked = relation(all_combos).with_columns(*[
            ma.col(c).is_null().cast(int).alias(f"__null_{c}") for c in cols
        ]).collect()
        for c in cols:
            # collected native frame; builtin sum over the flag column
            if sum(checked[f"__null_{c}"]) > 0:
                raise AssertionError(
                    f"set co_ column {c!r} contains null before frontier filter — "
                    f"normalization did not reach every build path"
                )
```
> `checked[f"__null_{c}"]` indexes a collected native frame (polars/pandas); `sum(...)` over its values is backend-pure (builtin). This is a cheap invariant guard, not hot-path code. **Empty-build path:** `_normalize_set_columns` runs *before* the `n_rules == 0` early return (Step 4), so an empty build already carries normalized (0-row) set columns; the assertion is vacuously satisfied there and need not run on that path. Multi-level builds reach this assertion on `all_combos` (all levels concatenated), covering the "multi-level" case; partition-filtered builds reach it too (the filter happens before `to_polars`, upstream of normalization).

- [ ] **Step 7: Run the build tests to verify they pass**

Run: `hatch run test:test-target tests/accumulator/test_engine.py::TestSetMembershipBuildFrontier tests/accumulator/test_engine.py::TestSetExclusionBuild tests/accumulator/test_engine.py::TestSetBuildValidation -v`
Expected: PASS — `test_three_wildcard_rules_collapse_to_single_maximal` now returns `{30}`.

- [ ] **Step 8: Full accumulator suite + purity + ruff**

Run: `hatch run test:test-target tests/accumulator/ tests/test_backend_purity.py -q`
Expected: PASS.
Run: `hatch run ruff:check src/mountainash_rules/engines/accumulator/engine.py`
Expected: All checks passed.

- [ ] **Step 9: Commit**

```bash
git add src/mountainash_rules/engines/accumulator/engine.py tests/accumulator/test_engine.py
git commit -m "feat(accumulator): set-column normalization stage; frontier untouched, dedupes correctly"
```

---

### Task 6: Apply round-trip, float typing, docs (idempotence covered in Task 2)

**Files:**
- Test: `tests/accumulator/test_apply.py` (apply round-trip)
- Test: `tests/accumulator/test_engine.py` (float-dim build)
- Modify: `CLAUDE.md` (match-strategy note)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: end-to-end verification + docs. Nothing downstream.

- [ ] **Step 1: Write the apply round-trip verification test**

This is an integration **verification** test (expected to PASS on first run — Tasks 2–5 already implement build+apply), not a fail-first unit. Add to `tests/accumulator/test_apply.py` (uses `AccumulatorEngine`, `Dimension`, `DimensionsMetadata`, `_rows`, `pl` already present; add `from mountainash_rules.core.constants import DataType` if absent):
```python
class TestSetMembershipApply:
    def _metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.STR),
        ])

    def test_context_in_intersection_matches_combination(self):
        rules = pl.DataFrame({
            "rule_name": ["R1", "R2"],
            "region": pl.Series("region", [["AU", "NZ", "UK"], ["NZ", "UK", "US"]], dtype=pl.List(pl.Utf8)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        lattice = engine.build(rules)
        result = engine.apply(lattice, {"region": "NZ"})
        assert 6 in set(_rows(result.provenance)["__prime_product"])

    def test_wildcard_combination_matches_any_context_exact_count(self):
        rules = pl.DataFrame({
            "rule_name": ["R1"],
            "region": pl.Series("region", [None], dtype=pl.List(pl.Utf8)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        lattice = engine.build(rules)
        result = engine.apply(lattice, {"region": "ANYTHING"})
        assert result.count == 1  # exactly the single wildcard combination
```
> Accessors verified against `result.py`/existing `test_apply.py`: `result.provenance` (relation with `__prime_product`, read via `_rows`), `result.count`. No `.combinations` on the apply result. `apply` accepts a dict. This runs on polars (build polars-internal; apply on polars). If a future cross-backend apply sweep trips narwhals list ops, extend the existing `mountainash#89` entry in `tests/conftest.py` `_UPSTREAM_XFAILS` — never a new xfail group.

- [ ] **Step 2: Run to verify it passes**

Run: `hatch run test:test-target tests/accumulator/test_apply.py::TestSetMembershipApply -v`
Expected: PASS (build + apply already implemented by Tasks 2–5; this is end-to-end verification).

- [ ] **Step 3: Write the float-dimension build test**

Add to `tests/accumulator/test_engine.py`:
```python
class TestFloatSetDimensionBuild:
    def _metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="scores", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.FLOAT),
        ])

    def test_float_set_wildcard_and_coalesce(self):
        rules = pl.DataFrame({
            "rule_name": ["R1", "R2"],
            "scores": pl.Series("scores", [[1.5, 2.5, 3.5], None], dtype=pl.List(pl.Float64)),
        })
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        lattice = engine.build(rules)
        rows = _rows(lattice.combinations)
        by_pp = dict(zip(rows["__prime_product"], rows["co_scores"]))
        # R2 is a wildcard; {R1,R2} coalesces to R1's concrete set (wildcard passthrough).
        assert by_pp[6] == [1.5, 2.5, 3.5]
        # Column stays a Float list — verify no dtype collapse.
        import polars as _pl
        mat = relation(lattice.combinations).to_polars()
        assert mat.schema["co_scores"] == _pl.List(_pl.Float64)
```

- [ ] **Step 4: Run the float test**

Run: `hatch run test:test-target tests/accumulator/test_engine.py::TestFloatSetDimensionBuild -v`
Expected: PASS.

- [ ] **Step 5: Update `CLAUDE.md`**

In `CLAUDE.md`, the Match Strategies table row for `set_membership` / `set_exclusion` currently reads (from the A2 merge):
```
| `set_membership` / `set_exclusion` | list column | Polars-native fallback |
```
Replace with (note: the filter path is backend-agnostic `t_is_in`/`t_is_not_in`, polars/ibis, with narwhals under `mountainash#89` — NOT "polars-native fallback"):
```
| `set_membership` / `set_exclusion` | list column | Filter via `t_is_in`/`t_is_not_in` (polars/ibis; narwhals list ops under `mountainash#89`). Accumulator-coalesceable (membership → list intersection, exclusion → list union). Wildcard = in-band `[unknown_sentinel_for(dtype)]` (never null; bool unsupported); see `null-is-not-a-portable-sentinel` principle. |
```
And under the "Ternary match logic" / sentinels area, add one line noting that set-dimension wildcards use the in-band `[sentinel]` list (not null), normalized at ingestion (reservation check in both engines; element-null check in the accumulator build).

- [ ] **Step 6: Full quick suite + purity**

Run: `hatch run test:test-quick`
Expected: PASS (no new failures; existing xfails unchanged).
Run: `hatch run test:test-target tests/test_backend_purity.py -q`
Expected: PASS (three `# allow:` tags unchanged; no new native imports).

- [ ] **Step 7: Commit**

```bash
git add tests/accumulator/test_apply.py tests/accumulator/test_engine.py CLAUDE.md
git commit -m "test+docs(accumulator): set-wildcard apply round-trip, float typing; document in-band sentinel"
```

---

## Notes for the executor

- **The frontier bug fix is Task 5's normalization stage, not any change to `_frontier_filter`.** If you find yourself editing `_frontier_filter`, `_check_overflow`, or partition routing, stop — the design is explicit that these stay untouched. The fix is that their inputs are now null-free and canonical.
- **Never construct a set value with a bare `ma.lit([sent])` or a native list-dtype cast.** Always `sentinel_list_expr(dim)` (element-typed literal). A `.cast(pl.List(...))` needs `import polars` (purity failure) and is refused by the backend anyway.
- **Set `co_` columns must never be null.** Normalization at ingestion (Task 5) guarantees it; the pre-frontier assertion (Task 5 Step 6) is the safety net. `set_wildcard_predicate` and the coalesce branches assume non-null input.
- **Backend purity is the sharpest tripwire.** `validate_set_columns` and `_assert_set_columns_non_null` use `relation(...).collect()` + builtin `len`/`sum` — never `import polars`.
- **`co_<field>_na` = `co_wild AND rhs_wild`** (Task 4) — equals the wildcard status of the final coalesced value, computed from inputs in the same pass.
