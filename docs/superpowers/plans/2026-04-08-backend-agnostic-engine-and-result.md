# Backend-Agnostic Engine and Result Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `engine.py` and `result.py` to use `mountainash.relations.Relation` and `mountainash.expressions` exclusively, removing all direct Polars imports from the rules engine source tree (with one documented exception in `compiler.py` for SET_MEMBERSHIP).

**Architecture:** The dimension compiler is already backend-agnostic. This rewrite extends the same discipline to the engine pipeline (`with_columns`, `filter`, `sort`, `with_row_index`, `drop`) and to all `RuleResult` accessors (`count`, `best_match`, `explain`, `at_least`). All DataFrame operations go through `mountainash.relations.relation()` and `Relation` methods. All per-row operations go through `mountainash.expressions` (`ma.col`, `ma.lit`, `ma.least`, chained `.add()`).

**Tech Stack:** mountainash (relational + scalar APIs), mountainash-relations (Relation, count_rows, item, with_row_index), pydantic, pytest

**Spec:** `docs/superpowers/specs/2026-04-08-backend-agnostic-engine-and-result-design.md`

**Prerequisites (already complete):**
- `Relation.count_rows() -> int` (upstream commit `4365176`)
- `Relation.item(column, row=0) -> Any` (upstream commit `5e12881`)

**Test command:** `hatch run test:test-target-quick tests/test_engine.py tests/test_result.py tests/test_backend_purity.py -v`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/mountainash_rules/engine.py` | Rewrite | Use `mountainash.relations.relation()` for all DataFrame ops, no `polars` import |
| `src/mountainash_rules/result.py` | Rewrite | Use `relation()`, `count_rows()`, `item()` for all accessors, no DataFrame-library imports |
| `src/mountainash_rules/compiler.py` | Modify | Add `# allow: SET_MEMBERSHIP workaround pending t_list_contains upstream` comment to the polars import line |
| `tests/test_backend_purity.py` | Create | Import-check test enforcing no polars/ibis/narwhals imports in the three pure files |
| `mountainash-central/01.principles/mountainash-utils-rules/c.identity-and-representation/representation-fits-host-language.md` | Rewrite | Reflect new one-engine-many-backends architecture; promote to ENFORCED |

The existing 113 tests use Polars input fixtures and continue to use Polars syntax in assertions. Since `survivors` continues to return the native input backend (Polars in → Polars out), all existing tests must continue to pass unchanged.

---

### Task 1: Backend Purity Test (Failing First)

**Files:**
- Create: `tests/test_backend_purity.py`

This test is the ENFORCED guarantee. It will initially **fail** because `engine.py` and `result.py` still import polars. We add the test first so the rewrite has a clear target — when the test passes, the rewrite is complete.

- [ ] **Step 1: Create the test file**

Create `tests/test_backend_purity.py` with:

```python
"""Enforces backend-purity for the rules engine source files.

The engine reaches DataFrames only through mountainash.relations and per-row
data only through mountainash.expressions. Direct backend imports are forbidden
in engine.py, result.py, and compiler.py — except for explicitly-allowed lines
marked with `# allow: <reason>`.
"""

import re
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parent.parent / "src" / "mountainash_rules"
PROHIBITED_PACKAGES = ("polars", "ibis", "narwhals")
PURE_FILES = ("engine.py", "result.py", "compiler.py")
ALLOW_PATTERN = re.compile(r"#\s*allow:\s*\w+")


@pytest.mark.parametrize("filename", PURE_FILES)
def test_no_direct_backend_imports(filename: str):
    source = (SRC_ROOT / filename).read_text()
    violations = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for pkg in PROHIBITED_PACKAGES:
            if (
                stripped.startswith(f"import {pkg}")
                or stripped.startswith(f"from {pkg}")
                or stripped.startswith(f"import {pkg}.")
                or stripped.startswith(f"from {pkg}.")
            ):
                if ALLOW_PATTERN.search(line):
                    continue  # explicit opt-out for documented exceptions
                violations.append(f"{filename}:{lineno}: {stripped}")
    assert not violations, (
        f"Backend-impure imports in {filename}:\n" + "\n".join(violations)
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `hatch run test:test-target-quick tests/test_backend_purity.py -v`

Expected: 3 tests, 2 failures (engine.py and result.py both currently `import polars as pl`). compiler.py also currently imports polars but we'll handle that in Task 2 with the allow-comment.

The output should show violations like:
```
engine.py:7: import polars as pl
result.py:?: ... (if any direct imports exist)
compiler.py:5: import polars as pl
```

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_backend_purity.py
git commit -m "test(backend-purity): add import-check test (currently failing)"
```

---

### Task 2: Tag the SET_MEMBERSHIP Polars Exception in compiler.py

**Files:**
- Modify: `src/mountainash_rules/compiler.py`

The `import polars as pl` line in `compiler.py` is the only legitimate exception, used by `_compile_set_membership` and `_compile_set_exclusion` for the `ma.native(pl.col(...).list.contains(...))` workaround. Tag it with the allow-comment so the purity test recognises the exception.

- [ ] **Step 1: Read current imports in compiler.py**

Read `src/mountainash_rules/compiler.py` lines 1-10 to confirm the current `import polars as pl` line.

- [ ] **Step 2: Add the allow comment**

Replace the line `import polars as pl` with:

```python
import polars as pl  # allow: SET_MEMBERSHIP workaround pending t_list_contains upstream
```

The exact text after `# allow:` is required for the regex match — keep `SET_MEMBERSHIP workaround` or any non-empty word; the test only requires the `# allow: <word>` form to be present.

- [ ] **Step 3: Run the purity test for compiler.py only**

Run: `hatch run test:test-target-quick "tests/test_backend_purity.py::test_no_direct_backend_imports[compiler.py]" -v`

Expected: PASS. The compiler.py test now succeeds because the import is explicitly tagged.

The other two tests (engine.py, result.py) still fail — that's expected; they're handled in Tasks 3 and 4.

- [ ] **Step 4: Verify no regressions in compiler tests**

Run: `hatch run test:test-target-quick tests/test_compiler.py -v`

Expected: All 52 compiler tests still PASS. The comment is non-functional and shouldn't affect anything.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/compiler.py
git commit -m "chore(compiler): tag polars import as documented SET_MEMBERSHIP exception"
```

---

### Task 3: Rewrite engine.py to Use mountainash.relations

**Files:**
- Modify: `src/mountainash_rules/engine.py`

This is the core rewrite. Replace the entire `_evaluate` pipeline and the `_bind_context` helper with a single chained `Relation` pipeline. Remove the `import polars as pl` line.

- [ ] **Step 1: Read the current engine.py**

Read `src/mountainash_rules/engine.py` in full to understand the current structure. The file is approximately 145 lines.

- [ ] **Step 2: Replace the entire engine.py file**

Replace `src/mountainash_rules/engine.py` with this complete new content:

```python
"""ExpressionRulesEngine: single-pass rule evaluation using mountainash."""

from __future__ import annotations

import functools
import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import relation

from mountainash_rules.compiler import DimensionCompiler
from mountainash_rules.constants import CTX_PREFIX
from mountainash_rules.context import extract_context_values
from mountainash_rules.dimension import DimensionsMetadata
from mountainash_rules.result import RuleResult


class ExpressionRulesEngine:
    """Rule evaluation engine using mountainash.

    Compiles dimension metadata into expression templates at construction time,
    then evaluates contexts against the rules DataFrame in a single-pass
    vectorized operation.

    The engine is backend-agnostic. The DataFrame backend (Polars, Ibis,
    Narwhals-wrapped Pandas/PyArrow) is determined by the type of `rules`
    passed to the constructor. The `RuleResult.survivors` accessor returns
    a DataFrame in the same backend as the input.

    Two construction paths:
    - Convenience: provide dimension_metadata (auto-compiled to expressions)
    - Advanced: provide dimension_expressions directly
    """

    def __init__(
        self,
        rules: t.Any,
        dimension_metadata: DimensionsMetadata | None = None,
        dimension_expressions: dict[str, BaseExpressionAPI] | None = None,
    ) -> None:
        if dimension_metadata and dimension_expressions:
            raise ValueError("Provide dimension_metadata or dimension_expressions, not both")
        if not dimension_metadata and not dimension_expressions:
            raise ValueError("Must provide either dimension_metadata or dimension_expressions")

        if dimension_metadata:
            compiler = DimensionCompiler()
            self._expressions = compiler.compile_dimensions(dimension_metadata)
            self._metadata = dimension_metadata
        else:
            self._expressions = dimension_expressions
            self._metadata = None

        self._rules = rules

    def evaluate(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
        top_n: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
    ) -> RuleResult:
        """Evaluate rules against a context.

        Args:
            context: Context values as a Pydantic model or dict.
            dimensions: Subset of dimensions to evaluate (default: all).
            top_n: Return only the top N matches by specificity.
            min_specificity: Minimum hard-match count to include.
            include_observability: Include per-dimension ternary columns in result.

        Returns:
            RuleResult with ranked surviving rules.
        """
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names

        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        context_values = extract_context_values(context, active_dims)
        result_df = self._evaluate(
            active_dims=active_dims,
            context_values=context_values,
            top_n=top_n,
            min_specificity=min_specificity,
            include_observability=include_observability,
        )
        return RuleResult(dataframe=result_df, active_dimensions=active_dims)

    def _evaluate(
        self,
        active_dims: list[str],
        context_values: dict[str, t.Any],
        top_n: int | None,
        min_specificity: int | None,
        include_observability: bool,
    ) -> t.Any:
        """Run the single-pass evaluation pipeline via mountainash.relations.Relation."""
        rel = relation(self._rules)

        # Step 1: Bind context values as literal columns
        ctx_columns = [
            ma.lit(value).alias(f"{CTX_PREFIX}{name}")
            for name, value in context_values.items()
        ]
        rel = rel.with_columns(*ctx_columns)

        # Step 2: Apply each dimension expression as a named ternary column
        dim_columns = [
            self._expressions[dim_name].name.alias(f"__t_{dim_name}")
            for dim_name in active_dims
        ]
        rel = rel.with_columns(*dim_columns)

        # Step 3: Compute survival and specificity via mountainash expressions
        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        survived = ma.least(*t_cols).ge(ma.lit(0)).alias("__survived")
        specificity = functools.reduce(
            lambda a, b: a.add(b),
            [c.eq(ma.lit(1)) for c in t_cols],
        ).alias("__specificity")
        rel = rel.with_columns(survived, specificity)

        # Step 4: Filter survivors, sort by specificity, add 1-based rank
        rel = (
            rel
            .filter(ma.col("__survived"))
            .sort("__specificity", descending=True)
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )

        # Step 5: Apply optional filters (after ranking, so __rank reflects pre-filter position)
        if min_specificity is not None:
            rel = rel.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))
        if top_n is not None:
            rel = rel.head(top_n)

        # Step 6: Drop temporary and observability columns
        drop_cols = ["__survived"] + [f"{CTX_PREFIX}{d}" for d in active_dims]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        rel = rel.drop(*drop_cols)

        return rel.execute()
```

- [ ] **Step 3: Run the engine tests**

Run: `hatch run test:test-target-quick tests/test_engine.py -v`

Expected: All 16 engine tests PASS. If any fail, common causes:
- `Relation.with_columns` may require unpacking with `*` (the new code already does this)
- `Relation.drop` signature may differ; verify it accepts `*column_names`
- `with_row_index` may default to a different name; the new code passes `name="__rank"` explicitly
- `min_horizontal` semantics may differ on edge cases (e.g. when t_cols list has only one element); check that `ma.least(*single_col)` doesn't error

If `ma.least(*t_cols)` fails when there's only one dimension, special-case it:

```python
if len(t_cols) == 1:
    survived_inner = t_cols[0]
else:
    survived_inner = ma.least(*t_cols)
survived = survived_inner.ge(ma.lit(0)).alias("__survived")
```

Apply the same pattern to `specificity` if `functools.reduce` raises on a single-element list (it should default to the single element, but verify).

- [ ] **Step 4: Run the integration tests**

Run: `hatch run test:test-target-quick tests/test_integration.py -v`

Expected: All 11 integration tests PASS.

- [ ] **Step 5: Run the result tests**

Run: `hatch run test:test-target-quick tests/test_result.py -v`

Expected: All 9 result tests PASS. They use a Polars fixture DataFrame so they exercise the result.py path that hasn't been rewritten yet — they should still work because result.py is unchanged at this point.

- [ ] **Step 6: Run the backend-purity test for engine.py**

Run: `hatch run test:test-target-quick "tests/test_backend_purity.py::test_no_direct_backend_imports[engine.py]" -v`

Expected: PASS. engine.py no longer has a polars import.

- [ ] **Step 7: Commit**

```bash
git add src/mountainash_rules/engine.py
git commit -m "refactor(engine): use mountainash.relations.Relation for backend-agnostic pipeline

Replaces direct polars imports with mountainash.relations and
mountainash.expressions. Engine source has zero polars references.
Pipeline order: bind context -> apply dim expressions -> survival
+ specificity -> filter/sort/rank -> apply optional filters -> drop
temporary columns -> execute.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Rewrite result.py to Use mountainash.relations

**Files:**
- Modify: `src/mountainash_rules/result.py`

`RuleResult` currently has four Polars-specific idioms (`shape[0]`, `df[col] == value`, `row[col][0]`, `df.filter(df[col] >= n)`). Replace each with `relation()` + `count_rows()`/`item()` calls.

- [ ] **Step 1: Read current result.py**

Read `src/mountainash_rules/result.py` in full. The file is approximately 75 lines.

- [ ] **Step 2: Replace the entire result.py file**

Replace `src/mountainash_rules/result.py` with this complete new content:

```python
"""RuleResult: wrapper for evaluated rule results with backend-agnostic accessors."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import relation


class RuleResult:
    """Wraps the evaluated rules DataFrame with convenience accessors.

    The DataFrame is expected to contain:
    - Original rule columns (passed through unchanged)
    - __t_{dim_name} columns: ternary values (1=match, 0=unknown, -1=non-match)
    - __specificity: count of hard matches (TRUE=1 values)
    - __rank: 1-based ranking by specificity descending

    All accessors are backend-agnostic — they reach the DataFrame only through
    mountainash.relations.Relation. The `survivors` property returns the native
    input backend so users can chain backend-specific operations on the result.
    """

    def __init__(self, dataframe: t.Any, active_dimensions: list[str]) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions

    @property
    def survivors(self) -> t.Any:
        """All surviving rules, ranked by specificity descending.

        Returns the native DataFrame in the same backend as the input.
        """
        return self._df

    @property
    def best_match(self) -> t.Any:
        """The single most specific surviving rule."""
        return relation(self._df).head(1).execute()

    @property
    def count(self) -> int:
        """Number of surviving rules."""
        return relation(self._df).count_rows()

    @property
    def active_dimensions(self) -> list[str]:
        """Dimensions that were evaluated."""
        return self._active_dimensions

    def explain(self, rule_name: str) -> dict[str, int]:
        """Per-dimension ternary values for a specific rule.

        Args:
            rule_name: The value in the 'rule_name' column to look up.

        Returns:
            Dict mapping dimension name to ternary value (1, 0, or -1).

        Raises:
            KeyError: If the rule_name is not found in survivors.
        """
        rel = (
            relation(self._df)
            .filter(ma.col("rule_name").eq(ma.lit(rule_name)))
            .head(1)
        )
        if rel.count_rows() == 0:
            raise KeyError(f"Rule '{rule_name}' not found in survivors")
        return {
            dim: rel.item(f"__t_{dim}")
            for dim in self._active_dimensions
        }

    def at_least(self, n: int) -> t.Any:
        """Return survivors with specificity >= n.

        Args:
            n: Minimum number of hard matches required.

        Returns:
            Filtered DataFrame in the same backend as the input.
        """
        return (
            relation(self._df)
            .filter(ma.col("__specificity").ge(ma.lit(n)))
            .execute()
        )
```

- [ ] **Step 3: Run the result tests**

Run: `hatch run test:test-target-quick tests/test_result.py -v`

Expected: All 9 result tests PASS. The fixture DataFrame is Polars, so the new code path exercises Polars-via-relation. Common failure modes:

- `rel.item("col")` may return a numpy scalar instead of a Python int. The existing tests assert `== 1` which works for numpy scalars too, so this should pass — but if it fails, wrap with `int(...)`.
- `count_rows()` returns int, which matches the existing `count` property contract.

- [ ] **Step 4: Run engine tests**

Run: `hatch run test:test-target-quick tests/test_engine.py -v`

Expected: All 16 engine tests PASS. Engine tests construct `RuleResult` and check `.survivors`, `.best_match`, `.count` — all of which should work with the new implementation.

- [ ] **Step 5: Run integration tests**

Run: `hatch run test:test-target-quick tests/test_integration.py -v`

Expected: All 11 integration tests PASS. Integration tests use the same `RuleResult` API.

- [ ] **Step 6: Run the backend-purity test for result.py**

Run: `hatch run test:test-target-quick "tests/test_backend_purity.py::test_no_direct_backend_imports[result.py]" -v`

Expected: PASS. result.py has no polars/ibis/narwhals imports.

- [ ] **Step 7: Run the full backend-purity test suite**

Run: `hatch run test:test-target-quick tests/test_backend_purity.py -v`

Expected: All 3 tests PASS (compiler.py via the allow-comment, engine.py and result.py because they no longer import polars).

- [ ] **Step 8: Commit**

```bash
git add src/mountainash_rules/result.py
git commit -m "refactor(result): use mountainash.relations for all RuleResult accessors

Replaces Polars-specific idioms (shape[0], df[col]==value, row[col][0])
with relation().count_rows() and relation().item() calls. RuleResult
source has zero direct DataFrame-library imports.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Full Test Suite and Lint Verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `hatch run test:test-target-quick tests/ -v`

Expected: All 116 tests PASS (113 existing + 3 new backend-purity tests).

If any tests fail, the most likely cause is a `RuleResult.item()` or `count_rows()` semantic mismatch — investigate before proceeding.

- [ ] **Step 2: Run with coverage**

Run: `hatch run test:test`

Expected: All tests PASS, coverage report generated. Coverage should remain at or above 90%.

- [ ] **Step 3: Run linter**

Run: `uvx ruff check src/`

Expected: "All checks passed!"

If there are unused imports (e.g. `pl` or `re` left over from the previous compiler implementation, or `pl` left over from the engine rewrite), fix them.

- [ ] **Step 4: Commit lint fixes if any**

If Step 3 needed fixes:

```bash
git add -u
git commit -m "style: clean up unused imports after backend-agnostic rewrite"
```

---

### Task 6: Update the representation-fits-host-language Principle

**Files:**
- Rewrite: `/home/nathanielramm/git/mountainash-io/mountainash/mountainash-central/01.principles/mountainash-utils-rules/c.identity-and-representation/representation-fits-host-language.md`

The principle currently references the old three-engine architecture (`RulesEngine`, `HybridRulesEngine`, `VectorizedRulesEngine`) which no longer exists. Rewrite it to reflect the new one-engine-many-backends reality and promote the status to ENFORCED.

- [ ] **Step 1: Replace the principle file content**

Replace the entire contents of `/home/nathanielramm/git/mountainash-io/mountainash/mountainash-central/01.principles/mountainash-utils-rules/c.identity-and-representation/representation-fits-host-language.md` with:

```markdown
# Representation Fits Host Language

> **Status:** ENFORCED — engine reaches DataFrames only through mountainash.relations and mountainash.expressions; verified by tests/test_backend_purity.py

## The Principle

Choose representations to fit the host language and its available libraries, not to mirror the source-language idioms of any precedent. The Python rules engine has options the SQL precedent did not, and is free to use them. Concretely: backend dispatch (Polars, Ibis, Narwhals-wrapped Pandas/PyArrow) happens at compile and execute time inside the mountainash stack — the rules engine itself is backend-blind. Engine source code never imports `polars`, `ibis`, or `narwhals` directly.

## Rationale

The rules engine ships **one** filter implementation, `ExpressionRulesEngine`, that compiles dimension metadata into mountainash expressions and applies them via `mountainash.relations.Relation`. Backend selection is automatic from the type of DataFrame the user passes in. The engine never branches on backend type; the mountainash stack handles that one layer down.

This is a stronger realisation of the original principle. The SQL precedent had to embed backend choices in code because there was no library layer to defer to. Python plus mountainash gives us a backend-neutral relational and expression vocabulary that compiles to the right native operations at the right time.

## Examples

A user with a Polars rules DataFrame gets a Polars result DataFrame back. A user with an Ibis table gets an Ibis table back. A user with a Pandas DataFrame gets a Narwhals-wrapped result. Same `ExpressionRulesEngine`, same `evaluate()` call, same metadata contract.

```python
engine = ExpressionRulesEngine(rules=polars_df, dimension_metadata=md)
result = engine.evaluate(context={...})  # result.survivors is a polars DataFrame

engine = ExpressionRulesEngine(rules=ibis_table, dimension_metadata=md)
result = engine.evaluate(context={...})  # result.survivors is an ibis Table
```

The dimension compiler builds backend-agnostic expression templates once at construction time:

```python
# inside compiler.py — no polars import, no ibis import
def _compile_exact(self, dim):
    sentinels = self._sentinels_for_type(dim.data_type)
    rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
    ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
    return rule_col.t_eq(ctx_col)
```

The engine pipeline reaches DataFrames only through `mountainash.relations.Relation`:

```python
# inside engine.py — no polars import
rel = relation(self._rules)
rel = rel.with_columns(*ctx_columns).with_columns(*dim_columns)
rel = rel.filter(ma.col("__survived")).sort("__specificity", descending=True)
result_df = rel.execute()
```

## Anti-Patterns

- **Importing `polars`, `ibis`, or `narwhals` in `engine.py`, `result.py`, or `compiler.py`.** The engine reaches DataFrames only through `mountainash.relations.Relation` and per-row data only through `mountainash.expressions`. Direct imports leak backend specifics into engine logic and break backend portability. Enforced by `tests/test_backend_purity.py`.
- **Branching on backend type inside engine code.** If the engine asks "is this a Polars DataFrame or an Ibis table?", the abstraction has leaked. Backend dispatch belongs inside the mountainash stack, not in the rules engine.
- **Materialising results to a specific backend in `RuleResult.survivors`.** The result preserves the user's input backend; converting it would force a copy and surprise the caller.
- **Locking a single backend into the engine because it was the fastest at the time.** The whole point of the mountainash dispatch layer is that the fastest backend can change without rewriting the engine.

## Technical Reference

- `mountainash-utils-rules/src/mountainash_rules/engine.py` — `ExpressionRulesEngine`, the only engine
- `mountainash-utils-rules/src/mountainash_rules/result.py` — `RuleResult`, all backend-agnostic
- `mountainash-utils-rules/src/mountainash_rules/compiler.py` — `DimensionCompiler`, all backend-agnostic except the documented SET_MEMBERSHIP exception
- `mountainash-utils-rules/tests/test_backend_purity.py` — the import-check test that enforces this principle
- `mountainash-utils-rules/docs/superpowers/specs/2026-04-08-backend-agnostic-engine-and-result-design.md` — the design spec for this principle's current form

## Future Considerations

The one place this principle is currently violated is in `compiler.py` for `SET_MEMBERSHIP` and `SET_EXCLUSION` strategies. These compile to `ma.native(pl.col(rule_field).list.contains(pl.col(ctx_field)))` because `t_is_in` / `t_is_not_in` in `mountainash.expressions` do not yet handle column references to list-typed columns (`TypeError: not yet implemented: Nested object types`). The exception is documented inline in `compiler.py` with an `# allow: SET_MEMBERSHIP workaround pending t_list_contains upstream` comment that the import-check test recognises. The resolution path is a future upstream addition of a backend-agnostic `t_list_contains` operation in `mountainash.expressions`, mirroring the `regex_contains` extension key pattern landed on 2026-04-07.
```

- [ ] **Step 2: Commit the principle update**

Note: this file lives in a separate repository (`mountainash-central`). Commit it from there:

```bash
cd /home/nathanielramm/git/mountainash-io/mountainash/mountainash-central
git add 01.principles/mountainash-utils-rules/c.identity-and-representation/representation-fits-host-language.md
git commit -m "principle(rules): rewrite representation-fits-host-language for backend-agnostic engine

Promotes status from ADOPTED to ENFORCED. Replaces stale references
to RulesEngine/HybridRulesEngine/VectorizedRulesEngine with the new
one-engine-many-backends architecture via mountainash.relations.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

Then return to the rules repo:

```bash
cd /home/nathanielramm/git/mountainash-io/mountainash/mountainash-utils-rules
```

---

### Task 7: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite one more time**

Run: `hatch run test:test`

Expected: All tests PASS, coverage report shows >= 90%.

- [ ] **Step 2: Manually verify backend-purity by inspection**

Run these commands and confirm the output:

```bash
grep -nE '^(import|from)\s+(polars|ibis|narwhals)' src/mountainash_rules/engine.py
```
Expected: no output (no matches).

```bash
grep -nE '^(import|from)\s+(polars|ibis|narwhals)' src/mountainash_rules/result.py
```
Expected: no output.

```bash
grep -nE '^(import|from)\s+(polars|ibis|narwhals)' src/mountainash_rules/compiler.py
```
Expected: one line — `import polars as pl  # allow: SET_MEMBERSHIP workaround pending t_list_contains upstream`

- [ ] **Step 3: Lint check**

Run: `uvx ruff check src/`

Expected: "All checks passed!"

- [ ] **Step 4: Confirm task completion**

At this point:
- `engine.py` and `result.py` have zero direct DataFrame-library imports
- `compiler.py` has one tagged exception for SET_MEMBERSHIP
- `tests/test_backend_purity.py` enforces the rule (3 tests pass)
- All 113 existing behavioural tests still pass
- The principle document reflects the current architecture and is marked ENFORCED
- No public API changes
