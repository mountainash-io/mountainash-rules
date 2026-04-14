# Cross-Backend Test Parameterisation — Design

> **Status:** Approved 2026-04-08
> **Exemplar:** `mountainash/tests/conftest.py`

## Goal

Upgrade the `mountainash-utils-rules` test suite to run against all 7 DataFrame backends that `mountainash.relations` supports, not just Polars. Prove the backend-agnosticism principle (`representation-fits-host-language.md`) with exercised tests on every backend, and catch future regressions where an innocuous change silently reintroduces a Polars assumption.

## Context

The rewrite landed in PR #38 made `engine.py`, `result.py`, and most of `compiler.py` backend-agnostic via `mountainash.relations.Relation` and `mountainash.expressions`. The only remaining backend dependency is `SET_MEMBERSHIP` / `SET_EXCLUSION` using a Polars `ma.native(pl.col(...).list.contains(...))` workaround, tracked upstream as `mountainash-io/mountainash#75`.

Current tests (`test_engine.py`, `test_result.py`, `test_integration.py`, `test_compiler.py`) were written against Polars fixtures and assert on Polars DataFrame methods directly. The backend-agnostic codepath has never actually been exercised on Ibis or Narwhals-wrapped backends in CI.

The exemplar `mountainash/tests/conftest.py` establishes a clean pattern: pure-Python data fixtures, a `backend_name` param fixture over 7 backends, per-backend DataFrame factory fixtures, and relation-API-based result extraction.

## Scope

### In

- Rewrite `tests/conftest.py` to mirror the exemplar pattern
- Parametrize `test_engine.py`, `test_result.py`, `test_integration.py` across all 7 backends transitively via fixture dependencies
- Extend `test_compiler.py`'s existing `TestBackendAgnosticism` class from 2 backends to 7
- Apply strict `xfail` to SET_MEMBERSHIP / SET_EXCLUSION tests on non-Polars backends, pointing at issue #75
- Introduce a narrower `list_capable_backends` param set for SET fixtures that need list-typed columns

### Out

- Changes to `engine.py`, `result.py`, `compiler.py`, or any source under `src/` — this is a test-only migration
- New API surface on `RuleResult`
- Performance benchmarking
- `test_dimension.py`, `test_context.py`, `test_backend_purity.py` — not backend-dependent, left untouched
- Adding backends beyond the 7 in the exemplar

## Backends

```python
ALL_BACKENDS = [
    "polars",
    "pandas",
    "narwhals-polars",
    "narwhals-pandas",
    "ibis-duckdb",
    "ibis-polars",
    "ibis-sqlite",
]

LIST_CAPABLE_BACKENDS = [
    "polars",
    "ibis-duckdb",
    "ibis-polars",
    "narwhals-polars",
]
```

`LIST_CAPABLE_BACKENDS` excludes pandas (object-dtype lists are lossy), narwhals-pandas (same), and ibis-sqlite (no array type).

## Fixture architecture

### Data fixtures (pure Python)

```python
@pytest.fixture
def rules_data() -> dict[str, list]:
    """Standard 3-dimension rules as a plain dict."""
    return {
        "rule_name": ["specific", "general", "mid", "no_match"],
        "region":    ["AU", UNKNOWN, "AU", "US"],
        "amount_min":[0, UNKNOWN_NUMERIC, 0, 0],
        "amount_max":[100, UNKNOWN_NUMERIC, 100, 100],
        "code":      ["^PRE.*", UNKNOWN, UNKNOWN, "^PRE.*"],
    }

@pytest.fixture
def rules_data_with_lists() -> dict[str, list]:
    """Rules with a list-typed column for SET_MEMBERSHIP fixtures."""
    return {
        "rule_name": ["au_nz", "us_ca", "eu"],
        "countries": [["AU", "NZ"], ["US", "CA"], ["DE", "FR", "IT"]],
    }
```

### Backend param fixtures

```python
@pytest.fixture(params=ALL_BACKENDS)
def backend_name(request) -> str:
    return request.param

@pytest.fixture(params=LIST_CAPABLE_BACKENDS)
def list_backend_name(request) -> str:
    return request.param
```

### Backend DataFrame factories

```python
@pytest.fixture
def backend_rules_df(backend_name: str, rules_data: dict):
    return _build_df(backend_name, rules_data, table_name="rules")

@pytest.fixture
def backend_rules_df_with_lists(list_backend_name: str, rules_data_with_lists: dict):
    return _build_df(list_backend_name, rules_data_with_lists, table_name="rules_lists")
```

`_build_df` is a helper at module level that dispatches on backend name, following the exemplar's inline `if/elif` chain (polars → `pl.DataFrame`, pandas → `pd.DataFrame`, narwhals-* → `nw.from_native`, ibis-* → `conn.create_table(name, data, overwrite=True)`).

### Engine fixture (transitive param)

```python
@pytest.fixture
def basic_engine(backend_rules_df, basic_metadata) -> ExpressionRulesEngine:
    return ExpressionRulesEngine(rules=backend_rules_df, dimension_metadata=basic_metadata)
```

Every test that depends on `basic_engine` auto-parametrizes across all 7 backends with no further annotation.

## Result extraction

Tests read `RuleResult.survivors` through the relation API, not backend-specific methods:

```python
import mountainash.expressions as ma
from mountainash.relations import relation

def test_survivor_names(basic_engine, valid_context):
    result = basic_engine.evaluate(valid_context)
    rows = relation(result.survivors).to_dict()
    assert rows["rule_name"] == ["specific", "mid", "general"]
```

`relation(df).to_dict()` is backend-agnostic (already used internally by the engine) and returns plain Python lists. No new fixture helpers needed — the relation API is the helper.

Shape assertions use `RuleResult.count` (already backend-agnostic). Column presence assertions use `set(relation(df).to_dict().keys())`.

## SET_MEMBERSHIP xfail strategy

SET tests currently live in `test_compiler.py` and potentially `test_integration.py`. Under multi-backend parametrization, the Polars-native `ma.native` escape hatch fails on every non-Polars backend at `evaluate` time.

**Approach:** per-test strict xfail keyed on `backend_name`.

```python
import pytest

SET_MEMBERSHIP_XFAIL_REASON = (
    "SET_MEMBERSHIP uses Polars-native workaround pending "
    "mountainash-io/mountainash#75 (t_list_contains)"
)

def _xfail_if_not_polars(backend_name: str):
    """Return a marker that xfails strictly on non-Polars backends."""
    if backend_name != "polars":
        return pytest.mark.xfail(strict=True, reason=SET_MEMBERSHIP_XFAIL_REASON)
    return None
```

Applied in tests via a request-level marker injection or explicit `pytest.param(..., marks=...)` on SET test cases. When #75 lands and the Polars workaround is removed from `compiler.py`, these xfails flip to XPASS and fail the suite — forcing us to remove the markers in the same PR that removes the workaround.

`test_compiler.py::TestBackendAgnosticism` already uses `pytest.param` with marks; SET cases there get the xfail marker directly in the parametrize list.

## File-by-file changes

| File | Change |
|---|---|
| `tests/conftest.py` | Full rewrite — add backend constants, param fixtures, data fixtures, backend DF factories. Preserve `basic_metadata`, `valid_context`, `TestContext`. Rename `sample_rules_df` → `backend_rules_df`. Rewrite `basic_engine` to depend on `backend_rules_df`. |
| `tests/test_engine.py` | Replace direct Polars DataFrame assertions with `relation(...).to_dict()` extraction. Tests auto-parametrize via `basic_engine`. No new imports of polars. |
| `tests/test_result.py` | Same pattern. `best_match` and `at_least` return a DataFrame in the input backend — read via `relation(...).to_dict()`. |
| `tests/test_integration.py` | Same pattern. The four test classes (pricing carve-out, entity pool, tie handling, fraud detection) use `basic_engine` transitively. Any test that constructs its own rules DataFrame inline must switch to `backend_rules_df` pattern. |
| `tests/test_compiler.py` | Extend `TestBackendAgnosticism` parametrize from 2 → 7 backends. Add xfail markers on SET_MEMBERSHIP / SET_EXCLUSION param cases. Other test classes that exercise compile-only (no evaluate) stay Polars-only since they only inspect AST structure. |
| `tests/test_dimension.py` | Unchanged — pure Pydantic validation tests. |
| `tests/test_context.py` | Unchanged — Pydantic + dict extraction, no DataFrame. |
| `tests/test_backend_purity.py` | Unchanged — source inspection, no runtime DataFrames. |

## Expected test volume

- Current: ~113 behavioral tests + 3 purity = 116
- After migration: behavioral tests × 7 backends ≈ 700–790 collected runs
- Xfails: SET_MEMBERSHIP / SET_EXCLUSION tests × 6 non-Polars backends ≈ 12–24
- Purity tests: still 3 (not parametrized)

## Dependencies

Test environment must have all backend libs installed:
- `polars` (already)
- `pandas` (already via narwhals)
- `narwhals` (via mountainash stack)
- `ibis-framework[duckdb,polars,sqlite]` (already in `pyproject.toml`)
- `pyarrow` (transitive)

No new runtime deps. Verify `hatch.toml` test env picks these up; if not, add to the test env feature list.

## Success criteria

1. `hatch run test:test-quick` runs ~700+ tests collected across 7 backends
2. All tests pass except the explicit SET_MEMBERSHIP xfails on non-Polars backends
3. No `import polars as pl` in any test file except where a backend-specific construction is genuinely required (should be zero — conftest handles it)
4. `test_backend_purity.py` still passes
5. When issue #75 lands and the Polars workaround is removed from `compiler.py`, the SET xfails flip XPASS and force a cleanup PR

## Non-goals (restated)

- No source changes under `src/`
- No benchmark / performance work
- No changes to CI workflow (tests run under existing `python-run-pytest.yml`)
- No helper fixture proliferation — the relation API is the helper
