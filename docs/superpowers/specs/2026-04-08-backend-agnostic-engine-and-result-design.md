# Backend-Agnostic Engine and Result — Design Spec

**Date:** 2026-04-08
**Status:** Approved
**Scope:** Rewrite `engine.py` and `result.py` to be fully backend-agnostic via `mountainash.relations.Relation` and `mountainash.expressions`. Update the stale `representation-fits-host-language.md` principle to reflect the new architecture.

## Summary

The dimension compiler is already backend-agnostic — it uses `mountainash.expressions` exclusively. But `engine.py` and `result.py` still reach for Polars primitives (`pl.col`, `pl.lit`, `pl.min_horizontal`, `pl.sum_horizontal`, `with_row_index`, `df.shape[0]`, `df[col][row]`). This means the engine claims to be "polars/ibis/narwhals" capable but in practice only Polars works for the pipeline and result.

This spec rewrites both files to use `mountainash.relations.Relation` for all DataFrame operations and `mountainash.expressions` for all per-row operations. After this work the rules engine source has zero direct DataFrame-library imports (with one documented exception: the SET_MEMBERSHIP `ma.native(pl.col(...).list.contains(...))` workaround in `compiler.py`, pending upstream `t_list_contains`).

## Prerequisites (complete)

The following upstream additions to `mountainash` landed on 2026-04-08 and are required by this work:

- `Relation.count_rows() -> int` — backend-agnostic row count via `count_records` aggregate
- `Relation.item(column: str, row: int = 0) -> Any` — backend-agnostic single-cell extraction with strict bounds checking

Both work on Polars, Ibis, and Narwhals-wrapped backends (pandas, PyArrow).

## Goals

1. **Remove Polars from `engine.py`** — no `import polars as pl`. All DataFrame ops via `mountainash.relations.Relation`. All per-row ops via `mountainash.expressions`.
2. **Remove Polars from `result.py`** — no Polars-specific row counting (`shape[0]`), filtering (`df[col] == value`), or scalar extraction (`row[col][0]`).
3. **Rewrite `representation-fits-host-language.md`** to reflect the one-engine-many-backends reality.
4. **Enforce the backend-purity guarantee** with an import-check test that fails the build if `polars`, `ibis`, or `narwhals` are imported in `engine.py`, `result.py`, or `compiler.py` (except for an explicitly-allowed line in `compiler.py` for the SET_MEMBERSHIP workaround).
5. **Preserve all existing test passes** (113 tests, all Polars-input, must continue to pass with the rewritten internals).

## Non-Goals

- **Cross-backend test parameterisation.** Existing tests stay Polars-only. Deferred to a future spec.
- **SET_MEMBERSHIP backend-agnosticism.** Still pending upstream `t_list_contains`. The Polars-native workaround in `_compile_set_membership` and `_compile_set_exclusion` stays for now and is the *only* exception to the backend-purity rule.
- **Updating the rest of CLAUDE.md.** Outside scope; separate cleanup pass.
- **Performance benchmarking the new pipeline.** Spot-check only; full benchmark deferred.

## Architecture

### Engine pipeline rewrite

The current `engine._evaluate()` builds a chain of `pl.with_columns`, `pl.filter`, `pl.sort`, `pl.with_row_index`, `pl.drop` calls directly on the input DataFrame. After the rewrite, the chain is built on a `mountainash.relations.Relation`:

```
relation(self._rules)
  .with_columns(*context_literal_columns)        # context binding via ma.lit
  .with_columns(*per_dimension_ternary_columns)  # apply compiled dim expressions
  .with_columns(__survived, __specificity)       # survival + specificity via ma.least + chained add
  .filter(__survived)                            # ma.col("__survived")
  .sort("__specificity", descending=True)
  .with_row_index(name="__rank")                 # 0-based
  .with_columns(__rank = __rank + 1)             # convert to 1-based
  .drop("__survived", *ctx_column_names)
  .execute()                                     # returns native DataFrame in input backend
```

Backend dispatch happens inside `Relation` at `.execute()` time. The rules engine never asks "which backend?" — it just builds the relation and runs it.

### Per-row operations via mountainash.expressions

| Operation | Old (Polars) | New (mountainash.expressions) |
|---|---|---|
| Context literal | `pl.lit(value).alias("__ctx_x")` | `ma.lit(value).alias("__ctx_x")` |
| Column reference | `pl.col("x")` | `ma.col("x")` |
| Survival (no -1) | `pl.min_horizontal(*t_cols).ge(0)` | `ma.least(*t_cols).ge(ma.lit(0))` |
| Specificity (sum of 1s) | `pl.sum_horizontal(*[c.eq(1).cast(pl.Int32) for c in t_cols])` | `functools.reduce(lambda a, b: a.add(b), [c.eq(ma.lit(1)) for c in t_cols])` |
| Filter survivors | `df.filter(pl.col("__survived"))` | `rel.filter(ma.col("__survived"))` |
| Specificity threshold | `df.filter(pl.col("__specificity") >= n)` | `rel.filter(ma.col("__specificity").ge(ma.lit(n)))` |

`ma.least()` exists in the scalar API and compiles to backend-native `min_horizontal` (Polars), `least` (Ibis), `min_horizontal` (Narwhals). For specificity, the cleanest portable form is `functools.reduce(lambda a, b: a.add(b), bool_exprs)` — verified to work on Polars in initial spike, expected to work on Ibis/Narwhals via the same scalar add operation.

### RuleResult rewrite

Every method in `RuleResult` reaches DataFrames only through `mountainash.relations.Relation`:

```python
from mountainash.relations import relation
import mountainash.expressions as ma

class RuleResult:
    def __init__(self, dataframe: Any, active_dimensions: list[str]) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions

    @property
    def survivors(self) -> Any:
        return self._df  # native DataFrame, unchanged

    @property
    def best_match(self) -> Any:
        return relation(self._df).head(1).execute()

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def active_dimensions(self) -> list[str]:
        return self._active_dimensions

    def explain(self, rule_name: str) -> dict[str, int]:
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

    def at_least(self, n: int) -> Any:
        return (
            relation(self._df)
            .filter(ma.col("__specificity").ge(ma.lit(n)))
            .execute()
        )
```

The `survivors` property returns the underlying native DataFrame unchanged — this preserves the contract that "Polars in → Polars out" for users who want to chain Polars operations. The new methods (`best_match`, `at_least`) likewise return whatever backend `Relation.execute()` produces, which matches the input backend.

### Engine rewrite

```python
"""ExpressionRulesEngine: single-pass rule evaluation using mountainash."""

from __future__ import annotations

import functools
import typing as t

from pydantic import BaseModel
from mountainash.expressions import BaseExpressionAPI
import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.compiler import DimensionCompiler
from mountainash_rules.constants import CTX_PREFIX
from mountainash_rules.context import extract_context_values
from mountainash_rules.dimension import DimensionsMetadata
from mountainash_rules.result import RuleResult


class ExpressionRulesEngine:
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
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names

        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        context_values = extract_context_values(context, active_dims)
        result_df = self._evaluate(active_dims, context_values, top_n, min_specificity, include_observability)
        return RuleResult(dataframe=result_df, active_dimensions=active_dims)

    def _evaluate(
        self,
        active_dims: list[str],
        context_values: dict[str, t.Any],
        top_n: int | None,
        min_specificity: int | None,
        include_observability: bool,
    ) -> t.Any:
        rel = relation(self._rules)

        # 1. Bind context values as literal columns
        ctx_columns = [
            ma.lit(value).alias(f"{CTX_PREFIX}{name}")
            for name, value in context_values.items()
        ]
        rel = rel.with_columns(*ctx_columns)

        # 2. Apply each dimension expression as a named ternary column
        dim_columns = [
            self._expressions[dim_name].name.alias(f"__t_{dim_name}")
            for dim_name in active_dims
        ]
        rel = rel.with_columns(*dim_columns)

        # 3. Compute survival + specificity via mountainash expressions
        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        survived = ma.least(*t_cols).ge(ma.lit(0)).alias("__survived")
        specificity = functools.reduce(
            lambda a, b: a.add(b),
            [c.eq(ma.lit(1)) for c in t_cols],
        ).alias("__specificity")
        rel = rel.with_columns(survived, specificity)

        # 4. Filter, sort, rank, clean up
        rel = (
            rel
            .filter(ma.col("__survived"))
            .sort("__specificity", descending=True)
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )

        # 5. Apply optional filters
        if min_specificity is not None:
            rel = rel.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))
        if top_n is not None:
            rel = rel.head(top_n)

        # 6. Drop temporary columns
        drop_cols = ["__survived"] + [f"{CTX_PREFIX}{d}" for d in active_dims]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        rel = rel.drop(*drop_cols)

        return rel.execute()
```

**Notes on the rewrite:**

- The order of `with_row_index`, `with_columns(__rank + 1)`, and the optional filters (`min_specificity`, `top_n`) is deliberate: we rank first, then filter, so `__rank` reflects the *ranked position before filtering*. If the spec needs `__rank` to be re-numbered after filtering, that's a behaviour change to flag during implementation.
- `_bind_context` is folded into `_evaluate` because it's a single line and not reused.
- The 0-based → 1-based conversion uses `.add(ma.lit(1))` rather than re-aliasing the column to itself. The `.alias("__rank")` overwrites the original column, replacing the 0-based version.

## Principle update

Rewrite `mountainash-central/01.principles/mountainash-utils-rules/c.identity-and-representation/representation-fits-host-language.md`. Status promoted to **ENFORCED** (from ADOPTED) because the import-check test makes it a real bound.

**New principle text:**

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
- `mountainash-utils-rules/docs/superpowers/specs/2026-04-08-backend-agnostic-engine-and-result-design.md` — this spec

## Future Considerations

The one place this principle is currently violated is in `compiler.py` for `SET_MEMBERSHIP` and `SET_EXCLUSION` strategies. These compile to `ma.native(pl.col(rule_field).list.contains(pl.col(ctx_field)))` because `t_is_in` / `t_is_not_in` in `mountainash.expressions` do not yet handle column references to list-typed columns (`TypeError: not yet implemented: Nested object types`). The exception is documented inline in `compiler.py` with an `# allow: polars` comment that the import-check test recognises. The resolution path is a future upstream addition of a backend-agnostic `t_list_contains` operation in `mountainash.expressions`, mirroring the `regex_contains` extension key pattern landed on 2026-04-07.
```

## Public API

**Unchanged.** Same `ExpressionRulesEngine` constructor, same `evaluate()` signature, same `RuleResult` properties and methods. Same return types from the user's perspective:

- `survivors` returns the input backend's native DataFrame type
- `best_match`, `at_least` return the input backend's native DataFrame type
- `count` returns `int`
- `explain` returns `dict[str, int]`

## Testing Strategy

### New test: `tests/test_backend_purity.py`

Reads the source of `engine.py`, `result.py`, and `compiler.py`. Asserts no import lines for `polars`, `ibis`, or `narwhals`. Recognises an opt-out comment for the SET_MEMBERSHIP exception:

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
            if (stripped.startswith(f"import {pkg}") or
                stripped.startswith(f"from {pkg}")):
                if ALLOW_PATTERN.search(line):
                    continue  # explicit opt-out for documented exceptions
                violations.append(f"{filename}:{lineno}: {stripped}")
    assert not violations, (
        f"Backend-impure imports in {filename}:\n" + "\n".join(violations)
    )
```

For the SET_MEMBERSHIP exception, the existing `import polars as pl` line in `compiler.py` gets the comment `import polars as pl  # allow: SET_MEMBERSHIP workaround pending t_list_contains upstream`.

### Existing tests

All 113 existing tests must continue to pass unchanged after the rewrite. They use Polars input fixtures and Polars syntax in assertions. Since `survivors` continues to return the native input backend (Polars in → Polars out), the assertion code keeps working.

### Deferred: cross-backend test parameterisation

Adding parametrized tests that run the engine against Polars, Ibis, and Narwhals-wrapped Pandas inputs is **out of scope for this spec**. Tracked as a follow-up. The backend-purity test gives us a strong static guarantee that nothing in the source can branch on backend, and the existing 113 Polars tests give us behavioural coverage. Cross-backend behavioural coverage is the next pass.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `Relation.execute()` overhead vs. native Polars chaining could regress performance on hot paths | Spot-check with one realistic rule set (~100 rules, ~20 dimensions) after the rewrite. Full benchmark deferred. |
| `ma.least` / chained `add` doesn't compile cleanly to Ibis or Narwhals for some edge case | Caught by the existing `TestBackendAgnosticism` smoke tests in `test_compiler.py` (these test compilation across Polars and Ibis). Engine pipeline will be exercised end-to-end on Polars by the 113 existing tests. |
| 0-based → 1-based row index conversion is easy to forget | Explicit step in the spec; will appear as a discrete task in the implementation plan. |
| Spec calls for `with_row_index` then `__rank + 1` then `head(top_n)` — the order matters because we want top_n to slice the *ranked* result, not the unranked one | Spec is explicit about the order. Implementation must follow it. |
| Some `Relation` operation we expect (e.g. `head` after `with_row_index`) may have unexpected behaviour | Caught during implementation; minor adjustments expected. |

## Migration Notes

**No public API changes.** No user code changes. The rewrite is purely internal.

**The `import polars as pl` line moves from `engine.py` and `result.py` to *only* `compiler.py`** — and even there, only with the `# allow: SET_MEMBERSHIP workaround pending t_list_contains upstream` comment. The import-check test enforces this.

## Out of Scope (deferred)

- **SET_MEMBERSHIP / SET_EXCLUSION backend-agnosticism.** Pending upstream `t_list_contains` in `mountainash.expressions`. The Polars-native workaround stays for now and is the only allowed exception to the backend-purity rule.
- **Cross-backend test parameterisation** for engine and result behavioural tests.
- **Performance benchmarking** the new pipeline against the old one.
- **CLAUDE.md cleanup.** It's stale beyond the match-strategies section and needs a separate sweep.
- **Cleanup of accidentally-committed discussion docs and SQL files** (e.g. `sp_productpricingmatrix_discretion_combos.sql`, the case-study and brainstorming docs that landed in `docs/superpowers/discussions/`). Out of scope for this spec.

## Dependencies

**No new dependencies.** Uses existing `mountainash` package which now provides:
- `mountainash.relations.relation` (backend dispatch)
- `mountainash.relations.Relation` with `count_rows`, `item`, `with_row_index`, `filter`, `sort`, `with_columns`, `head`, `drop`, `execute`
- `mountainash.expressions` with `col`, `lit`, `least`, `t_col`, etc.
