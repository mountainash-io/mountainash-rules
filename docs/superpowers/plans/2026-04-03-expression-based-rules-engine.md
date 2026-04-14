# Expression-Based Rules Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the iterative dimension-by-dimension rule evaluation engine with a single-pass expression-based architecture using mountainash.

**Architecture:** Build ternary expression templates from dimension metadata at construction time, bind context values as literal columns at evaluation time, compile all dimensions in one `with_columns()` call. Survival = no FALSE(-1) in any dimension. Specificity = count of TRUE(1) values. Results ranked by specificity descending.

**Tech Stack:** mountainash (ternary logic, build-then-compile), polars (primary backend), ibis-framework (secondary), narwhals (tertiary), pydantic (models), pytest (testing)

**Spec:** `docs/superpowers/specs/2026-04-03-expression-based-rules-engine-design.md`

**Test command:** `hatch run test:test-quick` (all tests) or `hatch run test:test-target-quick tests/path::test_name` (single test)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/mountainash_utils_rules/__init__.py` | Rewrite | New public API exports |
| `src/mountainash_utils_rules/constants.py` | Rewrite | MatchStrategy enum, sentinel values, CTX_PREFIX |
| `src/mountainash_utils_rules/dimension.py` | Simplify | Keep Dimension + DimensionsMetadata, remove MetadataManager |
| `src/mountainash_utils_rules/compiler.py` | Create | DimensionCompiler: metadata → expression templates |
| `src/mountainash_utils_rules/engine.py` | Rewrite | ExpressionRulesEngine |
| `src/mountainash_utils_rules/result.py` | Create | RuleResult wrapper |
| `src/mountainash_utils_rules/context.py` | Rewrite | Simplified context extraction |
| `src/mountainash_utils_rules/rule_manager.py` | Delete | No longer needed |
| `src/mountainash_utils_rules/rule_strategies.py` | Delete | Replaced by compiler |
| `src/mountainash_utils_rules/rule_strategies_original.py` | Delete | Replaced by compiler |
| `src/mountainash_utils_rules/observer.py` | Delete | Replaced by result columns |
| `src/mountainash_utils_rules/vectorized_engine.py` | Delete | Replaced by engine |
| `src/mountainash_utils_rules/enhanced_ternary_processor.py` | Delete | Replaced by engine |
| `src/mountainash_utils_rules/deprecated/` | Delete | Entire directory |
| `tests/conftest.py` | Rewrite | New fixtures for expression-based engine |
| `tests/test_compiler.py` | Create | DimensionCompiler tests |
| `tests/test_engine.py` | Create | ExpressionRulesEngine tests |
| `tests/test_result.py` | Create | RuleResult tests |
| `tests/test_integration.py` | Create | End-to-end scenarios |
| `tests/test_rule_engine.py` | Delete | Old engine tests |
| `tests/test_rule_manager.py` | Delete | Old manager tests |
| `tests/test_rule_strategies.py` | Delete | Old strategy tests |
| `tests/test_vectorized_engine.py` | Delete | Old vectorized engine tests |
| `tests/test_context.py` | Delete | Old context tests |
| `tests/test_metadata_manager.py` | Delete | Old metadata tests |
| `tests/test_hybrid_engine.py` | Delete | Old hybrid tests |
| `tests/test_numpy_processor.py` | Delete | Old numpy tests |
| `tests/benchmarks/` | Delete | Old benchmark framework |
| `pyproject.toml` | Modify | Update dependencies |
| `hatch.toml` | Modify | Add mountainash dependency |

---

### Task 1: Clean Slate — Remove Old Code, Update Dependencies

**Files:**
- Delete: `src/mountainash_utils_rules/rule_manager.py`
- Delete: `src/mountainash_utils_rules/rule_strategies.py`
- Delete: `src/mountainash_utils_rules/rule_strategies_original.py`
- Delete: `src/mountainash_utils_rules/observer.py`
- Delete: `src/mountainash_utils_rules/vectorized_engine.py`
- Delete: `src/mountainash_utils_rules/enhanced_ternary_processor.py`
- Delete: `src/mountainash_utils_rules/deprecated/` (entire directory)
- Delete: `tests/test_rule_engine.py`
- Delete: `tests/test_rule_manager.py`
- Delete: `tests/test_rule_strategies.py`
- Delete: `tests/test_vectorized_engine.py`
- Delete: `tests/test_context.py`
- Delete: `tests/test_metadata_manager.py`
- Delete: `tests/test_hybrid_engine.py`
- Delete: `tests/test_numpy_processor.py`
- Delete: `tests/benchmarks/` (entire directory)
- Modify: `pyproject.toml`
- Modify: `hatch.toml`

- [ ] **Step 1: Delete old source files**

```bash
cd /home/nathanielramm/git/mountainash-io/mountainash/mountainash-utils-rules
rm -f src/mountainash_utils_rules/rule_manager.py
rm -f src/mountainash_utils_rules/rule_strategies.py
rm -f src/mountainash_utils_rules/rule_strategies_original.py
rm -f src/mountainash_utils_rules/observer.py
rm -f src/mountainash_utils_rules/vectorized_engine.py
rm -f src/mountainash_utils_rules/enhanced_ternary_processor.py
rm -rf src/mountainash_utils_rules/deprecated/
```

- [ ] **Step 2: Delete old test files**

```bash
cd /home/nathanielramm/git/mountainash-io/mountainash/mountainash-utils-rules
rm -f tests/test_rule_engine.py
rm -f tests/test_rule_manager.py
rm -f tests/test_rule_strategies.py
rm -f tests/test_vectorized_engine.py
rm -f tests/test_context.py
rm -f tests/test_metadata_manager.py
rm -f tests/test_hybrid_engine.py
rm -f tests/test_numpy_processor.py
rm -rf tests/benchmarks/
```

- [ ] **Step 3: Update pyproject.toml dependencies**

Replace the `dependencies` list in `pyproject.toml`:

```toml
dependencies = [
    "polars>=1.35.1",
    "ibis-framework[polars,duckdb]>=11.0.0",
    "narwhals>=1.0.0",
    "mountainash",
]
```

Changes: removed `pandas>=2.2.0`, removed `sqlite` and `pandas` extras from ibis-framework, added `narwhals>=1.0.0`, added `mountainash` (the expressions package).

- [ ] **Step 4: Add mountainash dependency to hatch.toml test environments**

In `hatch.toml`, add the mountainash expressions dependency to the `[envs.test]` dependencies list. Add this line alongside the other mountainash dependencies:

```
    "mountainash @                    {root:uri}/../mountainash",
```

Do the same for `[envs.test_github]`:

```
    "mountainash @                    {root:uri}/temp/mountainash",
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove old engine code and update dependencies for expressions rearchitecture"
```

---

### Task 2: Constants and Dimension Models

**Files:**
- Rewrite: `src/mountainash_utils_rules/constants.py`
- Simplify: `src/mountainash_utils_rules/dimension.py`

- [ ] **Step 1: Rewrite constants.py**

```python
"""Constants for the expression-based rules engine."""

from enum import Enum, auto


class MatchStrategy(Enum):
    """How a dimension matches context values against rule values."""

    EXACT = auto()
    RANGE = auto()
    REGEX = auto()


# Sentinel values for unknown/unset rule and context fields.
# These are passed to ma.t_col(unknown={...}) so the expression library
# treats them as UNKNOWN (0) in ternary logic automatically.
UNKNOWN = "<NA>"
NOT_SET = "<NOT_SET>"
UNKNOWN_NUMERIC = -999999999
NOT_SET_NUMERIC = -999999998

# All string sentinels and all numeric sentinels, for convenience.
STRING_SENTINELS = {UNKNOWN, NOT_SET}
NUMERIC_SENTINELS = {UNKNOWN_NUMERIC, NOT_SET_NUMERIC}

# Prefix for context literal columns added to the rules DataFrame during evaluation.
CTX_PREFIX = "__ctx_"
```

- [ ] **Step 2: Simplify dimension.py**

Remove `MetadataManager` entirely. Keep `Dimension` and `DimensionsMetadata` with simplified accessors:

```python
"""Dimension metadata for rule evaluation."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, model_validator

from mountainash_utils_rules.constants import MatchStrategy


class Dimension(BaseModel):
    """A single dimension that rules are evaluated against."""

    dimension_name: str
    context_field: t.Optional[str] = None
    rule_field: t.Optional[str] = None
    match_strategy: MatchStrategy = MatchStrategy.EXACT
    data_type: type = str
    valid_values: list[t.Any] = []

    # RANGE strategy fields
    range_min_field: t.Optional[str] = None
    range_max_field: t.Optional[str] = None
    range_min_inclusive: bool = True
    range_max_inclusive: bool = True

    @property
    def resolved_context_field(self) -> str:
        """The field name to extract from the context object."""
        return self.context_field or self.dimension_name

    @property
    def resolved_rule_field(self) -> str:
        """The field name in the rules DataFrame."""
        return self.rule_field or self.dimension_name

    @model_validator(mode="after")
    def _validate_strategy_fields(self) -> "Dimension":
        if self.match_strategy == MatchStrategy.RANGE:
            if not self.range_min_field or not self.range_max_field:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses RANGE strategy "
                    f"but is missing range_min_field or range_max_field"
                )
            if self.data_type not in (int, float):
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses RANGE strategy "
                    f"but data_type is {self.data_type.__name__}, expected int or float"
                )
        if self.match_strategy == MatchStrategy.REGEX:
            if self.data_type is not str:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses REGEX strategy "
                    f"but data_type is {self.data_type.__name__}, expected str"
                )
        return self


class DimensionsMetadata(BaseModel):
    """Collection of dimension definitions for a rule set."""

    dimensions: list[Dimension]

    @model_validator(mode="after")
    def _validate_unique_names(self) -> "DimensionsMetadata":
        names = [d.dimension_name for d in self.dimensions]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise ValueError(f"Duplicate dimension names: {set(dupes)}")
        return self

    def get_dimension(self, name: str) -> Dimension:
        """Look up a dimension by name."""
        for d in self.dimensions:
            if d.dimension_name == name:
                return d
        raise KeyError(f"Dimension '{name}' not found")
```

- [ ] **Step 3: Verify the models work**

Run a quick Python check:

```bash
cd /home/nathanielramm/git/mountainash-io/mountainash/mountainash-utils-rules
hatch run test:test-target-quick -x -c "
from mountainash_utils_rules.constants import MatchStrategy, UNKNOWN, UNKNOWN_NUMERIC, CTX_PREFIX
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata

d = Dimension(dimension_name='test', match_strategy=MatchStrategy.EXACT, data_type=str)
assert d.resolved_context_field == 'test'
assert d.resolved_rule_field == 'test'

dm = DimensionsMetadata(dimensions=[d])
assert dm.get_dimension('test') == d
print('Constants and dimension models OK')
" 2>&1 || python3 -c "
from mountainash_utils_rules.constants import MatchStrategy, UNKNOWN, UNKNOWN_NUMERIC, CTX_PREFIX
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata

d = Dimension(dimension_name='test', match_strategy=MatchStrategy.EXACT, data_type=str)
assert d.resolved_context_field == 'test'
assert d.resolved_rule_field == 'test'

dm = DimensionsMetadata(dimensions=[d])
assert dm.get_dimension('test') == d
print('Constants and dimension models OK')
"
```

Expected: `Constants and dimension models OK`

- [ ] **Step 4: Commit**

```bash
git add src/mountainash_utils_rules/constants.py src/mountainash_utils_rules/dimension.py
git commit -m "refactor: simplify constants and dimension models for expression-based engine"
```

---

### Task 3: Context Extraction

**Files:**
- Rewrite: `src/mountainash_utils_rules/context.py`
- Create: `tests/test_context.py` (new, minimal)

- [ ] **Step 1: Write failing test for context extraction**

Create `tests/test_context.py`:

```python
"""Tests for context value extraction."""

import pytest
from pydantic import BaseModel

from mountainash_utils_rules.context import extract_context_values
from mountainash_utils_rules.constants import NOT_SET, NOT_SET_NUMERIC


class SampleContext(BaseModel):
    region: str
    amount: float
    category: str


def test_extract_from_pydantic_model():
    ctx = SampleContext(region="AU", amount=150.0, category="premium")
    values = extract_context_values(ctx, ["region", "amount"])
    assert values == {"region": "AU", "amount": 150.0}


def test_extract_from_dict():
    ctx = {"region": "AU", "amount": 150.0, "category": "premium"}
    values = extract_context_values(ctx, ["region", "amount"])
    assert values == {"region": "AU", "amount": 150.0}


def test_missing_field_returns_not_set():
    ctx = {"region": "AU"}
    values = extract_context_values(ctx, ["region", "missing_field"])
    assert values["region"] == "AU"
    assert values["missing_field"] == NOT_SET


def test_none_value_returns_not_set():
    ctx = {"region": None}
    values = extract_context_values(ctx, ["region"])
    assert values["region"] == NOT_SET
```

- [ ] **Step 2: Run test to verify it fails**

```bash
hatch run test:test-target-quick tests/test_context.py -v
```

Expected: FAIL — `extract_context_values` does not exist.

- [ ] **Step 3: Implement context.py**

```python
"""Context value extraction utilities."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel

from mountainash_utils_rules.constants import NOT_SET, NOT_SET_NUMERIC


def extract_context_values(
    context: BaseModel | dict,
    dimension_names: list[str],
) -> dict[str, t.Any]:
    """Extract context values for the given dimension names.

    Args:
        context: A Pydantic model or dict containing context values.
        dimension_names: The dimension names to extract values for.

    Returns:
        Dict mapping dimension name to its value, or NOT_SET/NOT_SET_NUMERIC
        if the field is missing or None.
    """
    if isinstance(context, BaseModel):
        raw = context.model_dump()
    elif isinstance(context, dict):
        raw = context
    else:
        raise TypeError(f"Context must be a BaseModel or dict, got {type(context).__name__}")

    result: dict[str, t.Any] = {}
    for name in dimension_names:
        value = raw.get(name)
        if value is None:
            result[name] = NOT_SET
        else:
            result[name] = value
    return result
```

- [ ] **Step 4: Run test to verify it passes**

```bash
hatch run test:test-target-quick tests/test_context.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_utils_rules/context.py tests/test_context.py
git commit -m "feat: add simplified context extraction for expression-based engine"
```

---

### Task 4: DimensionCompiler — EXACT Strategy

**Files:**
- Create: `src/mountainash_utils_rules/compiler.py`
- Create: `tests/test_compiler.py`

- [ ] **Step 1: Write failing test for EXACT compilation**

Create `tests/test_compiler.py`:

```python
"""Tests for DimensionCompiler."""

import polars as pl
import pytest

import mountainash.expressions as ma

from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import CTX_PREFIX, UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension


@pytest.fixture
def compiler():
    return DimensionCompiler()


class TestExactCompilation:
    def test_exact_match_produces_true(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US", "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [1, -1, -1]

    def test_exact_unknown_rule_value_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", UNKNOWN, "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values[0] == 1   # hard match
        assert values[1] == 0   # unknown (wildcard)
        assert values[2] == -1  # non-match

    def test_exact_unknown_context_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US"],
            f"{CTX_PREFIX}region": [UNKNOWN, UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [0, 0]  # all unknown when context is unknown

    def test_exact_numeric(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.EXACT, data_type=int)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "tier": [1, 2, UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}tier": [1, 1, 1],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values[0] == 1   # match
        assert values[1] == -1  # non-match
        assert values[2] == 0   # unknown
```

- [ ] **Step 2: Run test to verify it fails**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestExactCompilation -v
```

Expected: FAIL — `compiler` module does not exist.

- [ ] **Step 3: Implement compiler.py with EXACT strategy**

```python
"""DimensionCompiler: translates Dimension metadata into expression templates."""

from __future__ import annotations

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI

from mountainash_utils_rules.constants import (
    CTX_PREFIX,
    UNKNOWN,
    UNKNOWN_NUMERIC,
    NOT_SET,
    NOT_SET_NUMERIC,
    STRING_SENTINELS,
    NUMERIC_SENTINELS,
    MatchStrategy,
)
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata


class DimensionCompiler:
    """Compiles Dimension metadata into backend-agnostic expression templates.

    Each compiled expression references a context placeholder column (__ctx_<name>)
    that the engine populates at evaluation time.
    """

    def compile_dimensions(self, metadata: DimensionsMetadata) -> dict[str, BaseExpressionAPI]:
        """Compile all dimensions in a metadata set to expression templates."""
        return {
            dim.dimension_name: self.compile_dimension(dim)
            for dim in metadata.dimensions
        }

    def compile_dimension(self, dim: Dimension) -> BaseExpressionAPI:
        """Compile a single dimension to an expression template."""
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._compile_exact(dim)
            case MatchStrategy.RANGE:
                return self._compile_range(dim)
            case MatchStrategy.REGEX:
                return self._compile_regex(dim)
            case _:
                raise ValueError(f"Unknown match strategy: {dim.match_strategy}")

    def _sentinels_for_type(self, data_type: type) -> set:
        """Return the appropriate sentinel set for a data type."""
        if data_type in (int, float):
            return NUMERIC_SENTINELS
        return STRING_SENTINELS

    def _compile_exact(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return rule_col.t_eq(ctx_col)

    def _compile_range(self, dim: Dimension) -> BaseExpressionAPI:
        raise NotImplementedError("RANGE compilation is Task 5")

    def _compile_regex(self, dim: Dimension) -> BaseExpressionAPI:
        raise NotImplementedError("REGEX compilation is Task 6")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestExactCompilation -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_utils_rules/compiler.py tests/test_compiler.py
git commit -m "feat: add DimensionCompiler with EXACT strategy"
```

---

### Task 5: DimensionCompiler — RANGE Strategy

**Files:**
- Modify: `src/mountainash_utils_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing test for RANGE compilation**

Append to `tests/test_compiler.py`:

```python
class TestRangeCompilation:
    def test_range_within_bounds_produces_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=float,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [0.0, 100.0, 200.0],
            "amount_max": [99.0, 199.0, 299.0],
            f"{CTX_PREFIX}amount": [50.0, 50.0, 50.0],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, -1, -1]

    def test_range_boundary_inclusive(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
            range_min_inclusive=True,
            range_max_inclusive=True,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [10, 10],
            "amount_max": [20, 20],
            f"{CTX_PREFIX}amount": [10, 20],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [1, 1]  # both boundaries inclusive

    def test_range_boundary_exclusive(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
            range_min_inclusive=False,
            range_max_inclusive=False,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [10, 10],
            "amount_max": [20, 20],
            f"{CTX_PREFIX}amount": [10, 20],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1]  # both boundaries exclusive

    def test_range_unknown_min_produces_unknown(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount_min": [0, UNKNOWN_NUMERIC],
            "amount_max": [100, 100],
            f"{CTX_PREFIX}amount": [50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values[0] == 1  # known range, match
        assert values[1] == 0  # unknown min → unknown result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestRangeCompilation -v
```

Expected: FAIL — `NotImplementedError: RANGE compilation is Task 5`

- [ ] **Step 3: Implement _compile_range**

Replace the `_compile_range` method in `compiler.py`:

```python
    def _compile_range(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        min_col = ma.t_col(dim.range_min_field, unknown=sentinels)
        max_col = ma.t_col(dim.range_max_field, unknown=sentinels)

        if dim.range_min_inclusive:
            lower = min_col.t_le(ctx_col)
        else:
            lower = min_col.t_lt(ctx_col)

        if dim.range_max_inclusive:
            upper = max_col.t_ge(ctx_col)
        else:
            upper = max_col.t_gt(ctx_col)

        return lower.t_and(upper)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestRangeCompilation -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_utils_rules/compiler.py tests/test_compiler.py
git commit -m "feat: add RANGE strategy to DimensionCompiler"
```

---

### Task 6: DimensionCompiler — REGEX Strategy

**Files:**
- Modify: `src/mountainash_utils_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing test for REGEX compilation**

Append to `tests/test_compiler.py`:

```python
class TestRegexCompilation:
    def test_regex_match_produces_true(self, compiler):
        dim = Dimension(dimension_name="pattern", match_strategy=MatchStrategy.REGEX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "pattern": ["^AU.*", "^US.*", "^UK.*"],
            f"{CTX_PREFIX}pattern": ["AU-123", "AU-123", "AU-123"],
        })
        result = df.with_columns(expr.name.alias("__t_pattern").compile(df, booleanizer=None))
        values = result["__t_pattern"].to_list()
        # regex_contains with search semantics: ^AU.* matches AU-123
        assert values[0] == 1   # match
        assert values[1] == -1  # no match
        assert values[2] == -1  # no match

    def test_regex_search_semantics(self, compiler):
        """regex_contains uses search semantics (match anywhere, not anchored)."""
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.REGEX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["123", "xyz"],
            f"{CTX_PREFIX}code": ["abc-123-def", "abc-123-def"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values[0] == 1   # "123" found within "abc-123-def"
        assert values[1] == -1  # "xyz" not found

    def test_regex_unknown_pattern_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="pattern", match_strategy=MatchStrategy.REGEX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "pattern": ["^AU.*", UNKNOWN],
            f"{CTX_PREFIX}pattern": ["AU-123", "AU-123"],
        })
        result = df.with_columns(expr.name.alias("__t_pattern").compile(df, booleanizer=None))
        values = result["__t_pattern"].to_list()
        assert values[0] == 1  # match
        assert values[1] == 0  # unknown pattern → unknown result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestRegexCompilation -v
```

Expected: FAIL — `NotImplementedError: REGEX compilation is Task 6`

- [ ] **Step 3: Implement _compile_regex**

Replace the `_compile_regex` method in `compiler.py`:

```python
    def _compile_regex(self, dim: Dimension) -> BaseExpressionAPI:
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=STRING_SENTINELS)
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        return ctx_col.regex_contains(rule_col)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestRegexCompilation -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Run all compiler tests together**

```bash
hatch run test:test-target-quick tests/test_compiler.py -v
```

Expected: All 11 tests PASS (4 EXACT + 4 RANGE + 3 REGEX).

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_utils_rules/compiler.py tests/test_compiler.py
git commit -m "feat: add REGEX strategy to DimensionCompiler"
```

---

### Task 7: RuleResult

**Files:**
- Create: `src/mountainash_utils_rules/result.py`
- Create: `tests/test_result.py`

- [ ] **Step 1: Write failing tests for RuleResult**

Create `tests/test_result.py`:

```python
"""Tests for RuleResult."""

import polars as pl
import pytest

from mountainash_utils_rules.result import RuleResult


@pytest.fixture
def sample_result_df():
    """A pre-evaluated result DataFrame as the engine would produce."""
    return pl.DataFrame({
        "rule_name": ["specific", "general", "mid"],
        "rate": [0.05, 0.10, 0.07],
        "__t_region": [1, 0, 1],
        "__t_product": [1, 0, 0],
        "__t_tier": [1, 1, 1],
        "__specificity": [3, 1, 2],
        "__rank": [1, 3, 2],
    })


@pytest.fixture
def result(sample_result_df):
    return RuleResult(
        dataframe=sample_result_df,
        active_dimensions=["region", "product", "tier"],
    )


class TestSurvivors:
    def test_survivors_returns_all_rows(self, result):
        assert result.count == 3

    def test_survivors_is_the_dataframe(self, result):
        assert result.survivors.shape[0] == 3


class TestBestMatch:
    def test_best_match_returns_first_row(self, result):
        best = result.best_match
        assert best.shape[0] == 1
        assert best["rule_name"][0] == "specific"
        assert best["__specificity"][0] == 3


class TestExplain:
    def test_explain_returns_per_dimension_values(self, result):
        explanation = result.explain("specific")
        assert explanation == {"region": 1, "product": 1, "tier": 1}

    def test_explain_general_rule(self, result):
        explanation = result.explain("general")
        assert explanation == {"region": 0, "product": 0, "tier": 1}

    def test_explain_missing_rule_raises(self, result):
        with pytest.raises(KeyError):
            result.explain("nonexistent")


class TestAtLeast:
    def test_at_least_filters_by_specificity(self, result):
        filtered = result.at_least(2)
        assert filtered.shape[0] == 2
        assert set(filtered["rule_name"].to_list()) == {"specific", "mid"}

    def test_at_least_zero_returns_all(self, result):
        assert result.at_least(0).shape[0] == 3

    def test_at_least_high_returns_none(self, result):
        assert result.at_least(10).shape[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
hatch run test:test-target-quick tests/test_result.py -v
```

Expected: FAIL — `result` module does not exist.

- [ ] **Step 3: Implement result.py**

```python
"""RuleResult: wrapper for evaluated rule results with observability."""

from __future__ import annotations

import typing as t


class RuleResult:
    """Wraps the evaluated rules DataFrame with convenience accessors.

    The DataFrame is expected to contain:
    - Original rule columns (passed through unchanged)
    - __t_{dim_name} columns: ternary values (1=match, 0=unknown, -1=non-match)
    - __specificity: count of hard matches (TRUE=1 values)
    - __rank: 1-based ranking by specificity descending
    """

    def __init__(self, dataframe: t.Any, active_dimensions: list[str]) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions

    @property
    def survivors(self) -> t.Any:
        """All surviving rules, ranked by specificity descending."""
        return self._df

    @property
    def best_match(self) -> t.Any:
        """The single most specific surviving rule."""
        return self._df.head(1)

    @property
    def count(self) -> int:
        """Number of surviving rules."""
        return self._df.shape[0]

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
        filtered = self._df.filter(self._df["rule_name"] == rule_name)
        if filtered.shape[0] == 0:
            raise KeyError(f"Rule '{rule_name}' not found in survivors")

        row = filtered.head(1)
        return {
            dim: row[f"__t_{dim}"][0]
            for dim in self._active_dimensions
        }

    def at_least(self, n: int) -> t.Any:
        """Return survivors with specificity >= n.

        Args:
            n: Minimum number of hard matches required.

        Returns:
            Filtered DataFrame.
        """
        return self._df.filter(self._df["__specificity"] >= n)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
hatch run test:test-target-quick tests/test_result.py -v
```

Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_utils_rules/result.py tests/test_result.py
git commit -m "feat: add RuleResult with explain and filtering"
```

---

### Task 8: ExpressionRulesEngine — Core Evaluation

**Files:**
- Rewrite: `src/mountainash_utils_rules/engine.py`
- Create: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests for engine evaluation**

Create `tests/test_engine.py`:

```python
"""Tests for ExpressionRulesEngine."""

import polars as pl
import pytest

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine
from mountainash_utils_rules.result import RuleResult


@pytest.fixture
def rules_df():
    """Rules with 3 dimensions: region (EXACT), amount (RANGE), code (REGEX)."""
    return pl.DataFrame({
        "rule_name": ["specific", "general", "mid", "no_match"],
        "region": ["AU", UNKNOWN, "AU", "US"],
        "amount_min": [0, UNKNOWN_NUMERIC, 0, 0],
        "amount_max": [100, UNKNOWN_NUMERIC, 100, 100],
        "code": ["^PRE.*", UNKNOWN, UNKNOWN, "^PRE.*"],
    })


@pytest.fixture
def metadata():
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        ),
        Dimension(dimension_name="code", match_strategy=MatchStrategy.REGEX, data_type=str),
    ])


@pytest.fixture
def engine(rules_df, metadata):
    return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)


class TestSurvival:
    def test_non_matching_rules_eliminated(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = result.survivors["rule_name"].to_list()
        assert "no_match" not in names  # region=US doesn't match AU

    def test_matching_rules_survive(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = result.survivors["rule_name"].to_list()
        assert "specific" in names
        assert "general" in names
        assert "mid" in names


class TestSpecificity:
    def test_specific_rule_ranks_first(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        best = result.best_match
        assert best["rule_name"][0] == "specific"

    def test_specificity_values(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        df = result.survivors
        # specific: all 3 hard matches → specificity=3
        specific_row = df.filter(pl.col("rule_name") == "specific")
        assert specific_row["__specificity"][0] == 3

        # general: all unknown → specificity=0
        general_row = df.filter(pl.col("rule_name") == "general")
        assert general_row["__specificity"][0] == 0

        # mid: region match + amount match + unknown code → specificity=2
        mid_row = df.filter(pl.col("rule_name") == "mid")
        assert mid_row["__specificity"][0] == 2


class TestRanking:
    def test_rank_order(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        df = result.survivors
        names_in_order = df.sort("__rank")["rule_name"].to_list()
        assert names_in_order == ["specific", "mid", "general"]


class TestEmptyResult:
    def test_no_survivors(self):
        rules_df = pl.DataFrame({
            "rule_name": ["only_us"],
            "region": ["US"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
hatch run test:test-target-quick tests/test_engine.py -v
```

Expected: FAIL — `ExpressionRulesEngine` does not exist (old engine.py is still there with `RulesEngine`).

- [ ] **Step 3: Implement engine.py**

```python
"""ExpressionRulesEngine: single-pass rule evaluation using mountainash."""

from __future__ import annotations

import typing as t

import polars as pl
from pydantic import BaseModel

from mountainash.expressions import BaseExpressionAPI

from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import CTX_PREFIX
from mountainash_utils_rules.context import extract_context_values
from mountainash_utils_rules.dimension import DimensionsMetadata
from mountainash_utils_rules.result import RuleResult


class ExpressionRulesEngine:
    """Rule evaluation engine using mountainash.

    Compiles dimension metadata into expression templates at construction time,
    then evaluates contexts against the rules DataFrame in a single-pass
    vectorized operation.

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
        # Determine which dimensions to evaluate
        all_dim_names = list(self._expressions.keys())
        active_dims = dimensions if dimensions else all_dim_names

        # Validate requested dimensions exist
        for dim_name in active_dims:
            if dim_name not in self._expressions:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        # Extract context values
        context_values = extract_context_values(context, active_dims)

        # Bind context values as literal columns
        augmented = self._bind_context(self._rules, context_values)

        # Evaluate all dimensions in a single pass
        result_df = self._evaluate(augmented, active_dims)

        # Apply filters
        if min_specificity is not None:
            result_df = result_df.filter(pl.col("__specificity") >= min_specificity)

        if top_n is not None:
            result_df = result_df.head(top_n)

        # Optionally strip observability columns
        if not include_observability:
            t_cols = [f"__t_{d}" for d in active_dims]
            result_df = result_df.drop([c for c in t_cols if c in result_df.columns])

        return RuleResult(dataframe=result_df, active_dimensions=active_dims)

    def _bind_context(self, rules: t.Any, context_values: dict[str, t.Any]) -> t.Any:
        """Add context values as literal columns to the rules DataFrame."""
        ctx_columns = [
            pl.lit(value).alias(f"{CTX_PREFIX}{name}")
            for name, value in context_values.items()
        ]
        return rules.with_columns(ctx_columns)

    def _evaluate(self, augmented_df: t.Any, active_dims: list[str]) -> t.Any:
        """Run the single-pass evaluation pipeline."""
        # Step 1: Compile each dimension expression into a named ternary column
        dim_columns = [
            self._expressions[dim_name]
                .name.alias(f"__t_{dim_name}")
                .compile(augmented_df, booleanizer=None)
            for dim_name in active_dims
        ]

        # Step 2: Apply all ternary columns at once
        result = augmented_df.with_columns(dim_columns)

        # Step 3: Compute survival and specificity
        t_col_refs = [pl.col(f"__t_{d}") for d in active_dims]

        result = result.with_columns(
            pl.min_horizontal(*t_col_refs).ge(0).alias("__survived"),
            pl.sum_horizontal(*[c.eq(1).cast(pl.Int32) for c in t_col_refs]).alias("__specificity"),
        )

        # Step 4: Filter survivors, rank, clean up
        ctx_columns = [f"{CTX_PREFIX}{d}" for d in active_dims]

        result = (
            result
            .filter(pl.col("__survived"))
            .sort("__specificity", descending=True)
            .with_row_index("__rank", offset=1)
            .drop(["__survived"] + ctx_columns)
        )

        return result
```

- [ ] **Step 4: Run test to verify it passes**

```bash
hatch run test:test-target-quick tests/test_engine.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_utils_rules/engine.py tests/test_engine.py
git commit -m "feat: add ExpressionRulesEngine with single-pass evaluation"
```

---

### Task 9: Engine — Advanced Features (top_n, min_specificity, dimensions subset, observability)

**Files:**
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write tests for advanced features**

Append to `tests/test_engine.py`:

```python
class TestTopN:
    def test_top_n_limits_results(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=2,
        )
        assert result.count == 2
        # Should be the top 2 by specificity
        assert result.survivors["rule_name"][0] == "specific"

    def test_top_n_larger_than_survivors(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=100,
        )
        assert result.count == 3  # only 3 survivors exist


class TestMinSpecificity:
    def test_min_specificity_filters(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            min_specificity=2,
        )
        names = result.survivors["rule_name"].to_list()
        assert "specific" in names
        assert "mid" in names
        assert "general" not in names  # specificity=0


class TestDimensionsSubset:
    def test_subset_dimensions(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            dimensions=["region"],
        )
        # Only evaluating region: specific(AU), general(unknown), mid(AU) survive
        # no_match(US) eliminated
        assert result.count == 3
        assert "no_match" not in result.survivors["rule_name"].to_list()

    def test_invalid_dimension_raises(self, engine):
        with pytest.raises(KeyError, match="nonexistent"):
            engine.evaluate(
                context={"region": "AU"},
                dimensions=["nonexistent"],
            )


class TestObservability:
    def test_observability_columns_present_by_default(self, engine):
        result = engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        cols = result.survivors.columns
        assert "__t_region" in cols
        assert "__t_amount" in cols
        assert "__t_code" in cols

    def test_observability_columns_absent_when_disabled(self, engine):
        result = engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            include_observability=False,
        )
        cols = result.survivors.columns
        assert "__t_region" not in cols
        assert "__t_amount" not in cols
        assert "__t_code" not in cols
        # __specificity and __rank should still be present
        assert "__specificity" in cols
        assert "__rank" in cols
```

- [ ] **Step 2: Run tests**

```bash
hatch run test:test-target-quick tests/test_engine.py -v
```

Expected: All 13 tests PASS (7 from Task 8 + 6 new).

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine.py
git commit -m "test: add tests for top_n, min_specificity, dimensions subset, observability"
```

---

### Task 10: Engine — Advanced Construction Path (Custom Expressions)

**Files:**
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write tests for custom expressions path**

Append to `tests/test_engine.py`:

```python
import mountainash.expressions as ma
from mountainash_utils_rules.constants import CTX_PREFIX, UNKNOWN


class TestCustomExpressions:
    def test_custom_expression_exact(self):
        rules_df = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "region": ["AU", "US"],
        })

        engine = ExpressionRulesEngine(
            rules=rules_df,
            dimension_expressions={
                "region": ma.t_col("region", unknown={UNKNOWN}).t_eq(
                    ma.t_col(f"{CTX_PREFIX}region", unknown={UNKNOWN})
                ),
            },
        )

        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 1
        assert result.best_match["rule_name"][0] == "r1"

    def test_cannot_provide_both_metadata_and_expressions(self):
        with pytest.raises(ValueError, match="not both"):
            ExpressionRulesEngine(
                rules=pl.DataFrame({"rule_name": ["r1"]}),
                dimension_metadata=DimensionsMetadata(dimensions=[
                    Dimension(dimension_name="x", match_strategy=MatchStrategy.EXACT, data_type=str),
                ]),
                dimension_expressions={"x": ma.col("x")},
            )

    def test_must_provide_one_of_metadata_or_expressions(self):
        with pytest.raises(ValueError, match="Must provide"):
            ExpressionRulesEngine(
                rules=pl.DataFrame({"rule_name": ["r1"]}),
            )
```

- [ ] **Step 2: Run tests**

```bash
hatch run test:test-target-quick tests/test_engine.py -v
```

Expected: All 16 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine.py
git commit -m "test: add tests for custom expressions construction path"
```

---

### Task 11: Integration Tests — Hierarchical Rules

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration tests**

Create `tests/test_integration.py`:

```python
"""Integration tests: end-to-end scenarios with real-world rule patterns."""

import polars as pl
import pytest

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine


class TestPricingCarveOut:
    """Pricing hierarchy: general rate → client-specific → product-specific override."""

    @pytest.fixture
    def pricing_engine(self):
        rules_df = pl.DataFrame({
            "rule_name": ["base_rate", "client_au", "client_au_premium"],
            "rate": [0.10, 0.08, 0.05],
            "client_region": [UNKNOWN, "AU", "AU"],
            "product": [UNKNOWN, UNKNOWN, "premium"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="client_region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)

    def test_specific_override_wins(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        best = result.best_match
        assert best["rule_name"][0] == "client_au_premium"
        assert best["rate"][0] == 0.05

    def test_fallback_to_client_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "standard"})
        best = result.best_match
        assert best["rule_name"][0] == "client_au"
        assert best["rate"][0] == 0.08

    def test_fallback_to_base_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "UK", "product": "standard"})
        best = result.best_match
        assert best["rule_name"][0] == "base_rate"
        assert best["rate"][0] == 0.10

    def test_hierarchy_preserved_in_ranking(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        names = result.survivors.sort("__rank")["rule_name"].to_list()
        assert names == ["client_au_premium", "client_au", "base_rate"]


class TestEntityPool:
    """Entity pool with range-based and regex rules for increasing specificity."""

    @pytest.fixture
    def pool_engine(self):
        rules_df = pl.DataFrame({
            "rule_name": ["catch_all", "mid_tier", "high_value_au"],
            "pool": ["default", "tier_b", "tier_a"],
            "region": [UNKNOWN, UNKNOWN, "AU"],
            "value_min": [UNKNOWN_NUMERIC, 1000, 5000],
            "value_max": [UNKNOWN_NUMERIC, 9999, 99999],
            "code_pattern": [UNKNOWN, "^T.*", "^T.*"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(
                dimension_name="value",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field="value_min",
                range_max_field="value_max",
            ),
            Dimension(dimension_name="code_pattern", match_strategy=MatchStrategy.REGEX, data_type=str),
        ])
        return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)

    def test_most_specific_wins(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "AU", "value": 7500, "code_pattern": "TXN-001"})
        assert result.best_match["rule_name"][0] == "high_value_au"

    def test_mid_tier_fallback(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "UK", "value": 5000, "code_pattern": "TXN-001"})
        assert result.best_match["rule_name"][0] == "mid_tier"

    def test_catch_all_fallback(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "UK", "value": 500, "code_pattern": "ABC-001"})
        assert result.best_match["rule_name"][0] == "catch_all"


class TestNoMatch:
    def test_all_rules_eliminated(self):
        rules_df = pl.DataFrame({
            "rule_name": ["au_only", "us_only"],
            "region": ["AU", "US"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "UK"})
        assert result.count == 0


class TestTieHandling:
    def test_same_specificity_both_survive(self):
        rules_df = pl.DataFrame({
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
            "product": ["premium", "standard"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})
        # rule_a matches both, rule_b fails on product
        assert result.count == 1
        assert result.best_match["rule_name"][0] == "rule_a"

    def test_equal_specificity_both_returned(self):
        rules_df = pl.DataFrame({
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 2


class TestExplainIntegration:
    def test_explain_shows_dimension_breakdown(self):
        rules_df = pl.DataFrame({
            "rule_name": ["specific", "general"],
            "region": ["AU", UNKNOWN],
            "product": ["premium", UNKNOWN],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})

        assert result.explain("specific") == {"region": 1, "product": 1}
        assert result.explain("general") == {"region": 0, "product": 0}
```

- [ ] **Step 2: Run integration tests**

```bash
hatch run test:test-target-quick tests/test_integration.py -v
```

Expected: All 12 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for hierarchical rules, pricing, and entity pools"
```

---

### Task 12: Update __init__.py and Fixtures

**Files:**
- Rewrite: `src/mountainash_utils_rules/__init__.py`
- Rewrite: `tests/conftest.py`

- [ ] **Step 1: Rewrite __init__.py**

```python
"""Mountain Ash Utils Rules — expression-based rule evaluation engine."""

from mountainash_utils_rules.__version__ import __version__
from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine
from mountainash_utils_rules.result import RuleResult

__all__ = (
    "__version__",
    "DimensionCompiler",
    "Dimension",
    "DimensionsMetadata",
    "ExpressionRulesEngine",
    "MatchStrategy",
    "RuleResult",
)
```

- [ ] **Step 2: Rewrite tests/conftest.py**

```python
"""Shared fixtures for expression-based rules engine tests."""

import polars as pl
import pytest
from pydantic import BaseModel

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine


class TestContext(BaseModel):
    region: str
    amount: int
    code: str


@pytest.fixture
def sample_rules_df():
    """Standard rules DataFrame with 3 dimensions."""
    return pl.DataFrame({
        "rule_name": ["specific", "general", "mid", "no_match"],
        "region": ["AU", UNKNOWN, "AU", "US"],
        "amount_min": [0, UNKNOWN_NUMERIC, 0, 0],
        "amount_max": [100, UNKNOWN_NUMERIC, 100, 100],
        "code": ["^PRE.*", UNKNOWN, UNKNOWN, "^PRE.*"],
    })


@pytest.fixture
def basic_metadata():
    """Standard 3-dimension metadata."""
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        ),
        Dimension(dimension_name="code", match_strategy=MatchStrategy.REGEX, data_type=str),
    ])


@pytest.fixture
def basic_engine(sample_rules_df, basic_metadata):
    """Pre-configured engine for standard tests."""
    return ExpressionRulesEngine(rules=sample_rules_df, dimension_metadata=basic_metadata)


@pytest.fixture
def valid_context():
    """A context that matches the 'specific' rule."""
    return TestContext(region="AU", amount=50, code="PRE-001")
```

- [ ] **Step 3: Run all tests**

```bash
hatch run test:test-target-quick -v
```

Expected: All tests PASS across all test files.

- [ ] **Step 4: Commit**

```bash
git add src/mountainash_utils_rules/__init__.py tests/conftest.py
git commit -m "feat: update public API and shared test fixtures"
```

---

### Task 13: Run Full Test Suite and Lint

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite with coverage**

```bash
hatch run test:test
```

Expected: All tests PASS, coverage report generated.

- [ ] **Step 2: Run linter**

```bash
hatch run ruff:check
```

Expected: No errors, or only pre-existing issues in unchanged files. Fix any new issues introduced.

- [ ] **Step 3: Fix any lint issues**

If ruff reports issues in the new files, fix them. Common fixes: unused imports, missing trailing newlines, line length.

- [ ] **Step 4: Run tests again after lint fixes**

```bash
hatch run test:test-quick
```

Expected: All tests PASS.

- [ ] **Step 5: Commit any lint fixes**

```bash
git add -u
git commit -m "style: fix lint issues in new modules"
```

(Skip this step if no lint fixes were needed.)
