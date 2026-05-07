# Extended Match Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `DimensionCompiler` from 3 to 11 match strategies and rewrite REGEX to be backend-agnostic using the now-consistent mountainash string API.

**Architecture:** Each new strategy is a small compile method in `DimensionCompiler`. String-returning operations (`starts_with`, `ends_with`, `contains`, `regex_contains`) share a `_compile_string_match` helper that wraps the boolean result in a sentinel-aware when/then ternary expression. Direct ternary ops (`t_eq`, `t_ne`, `t_gt`, `t_lt`, `t_is_in`, `t_is_not_in`) compile to one-liners using `t_col` with sentinel sets.

**Tech Stack:** mountainash (ternary logic, backend-agnostic string ops), polars (primary test backend), pydantic (Dimension model validation), pytest (testing)

**Spec:** `docs/superpowers/specs/2026-04-07-extended-match-strategies-design.md`

**Prerequisite:** Upstream `mountainash` fixes (completed 2026-04-07):
- `contains`, `regex_contains`, `strpos`, `count_substring`, `like` accept column references
- `t_is_in`, `t_is_not_in` accept column references to list columns

**Test command:** `hatch run test:test-target-quick tests/test_compiler.py -v` (single file)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/mountainash_rules/constants.py` | Modify | Add 8 new values to `MatchStrategy` enum |
| `src/mountainash_rules/dimension.py` | Modify | Add validation rules for new strategies |
| `src/mountainash_rules/compiler.py` | Rewrite | Add 8 compile methods, rewrite REGEX, remove polars/re imports |
| `tests/test_compiler.py` | Modify | Add 8 new test classes, rewrite REGEX tests |
| `tests/test_dimension.py` | Create | Validation tests for new strategies |
| `tests/test_integration.py` | Modify | Add fraud detection scenario using mixed strategies |

---

### Task 1: Extend MatchStrategy Enum

**Files:**
- Modify: `src/mountainash_rules/constants.py`

- [ ] **Step 1: Add new enum values**

Replace the `MatchStrategy` class in `src/mountainash_rules/constants.py` with:

```python
class MatchStrategy(Enum):
    """How a dimension matches context values against rule values."""

    EXACT = auto()
    NOT_EQUAL = auto()
    RANGE = auto()
    GREATER_THAN = auto()
    LESS_THAN = auto()
    PREFIX = auto()
    SUFFIX = auto()
    CONTAINS = auto()
    REGEX = auto()
    SET_MEMBERSHIP = auto()
    SET_EXCLUSION = auto()
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
hatch run test:test-target-quick tests/test_compiler.py -v
```

Expected: All 11 existing compiler tests still PASS (new enum values don't break existing code).

- [ ] **Step 3: Commit**

```bash
git add src/mountainash_rules/constants.py
git commit -m "feat(constants): add 8 new MatchStrategy enum values"
```

---

### Task 2: Add Dimension Validation for New Strategies

**Files:**
- Modify: `src/mountainash_rules/dimension.py`
- Create: `tests/test_dimension.py`

- [ ] **Step 1: Write failing validation tests**

Create `tests/test_dimension.py`:

```python
"""Tests for Dimension model validation."""

import pytest

from mountainash_rules.constants import MatchStrategy
from mountainash_rules.dimension import Dimension


class TestNumericStrategyValidation:
    def test_greater_than_requires_numeric(self):
        with pytest.raises(ValueError, match="GREATER_THAN"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.GREATER_THAN,
                data_type=str,
            )

    def test_greater_than_accepts_int(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        assert d.match_strategy == MatchStrategy.GREATER_THAN

    def test_greater_than_accepts_float(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=float,
        )
        assert d.match_strategy == MatchStrategy.GREATER_THAN

    def test_less_than_requires_numeric(self):
        with pytest.raises(ValueError, match="LESS_THAN"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.LESS_THAN,
                data_type=str,
            )


class TestStringStrategyValidation:
    def test_prefix_requires_string(self):
        with pytest.raises(ValueError, match="PREFIX"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.PREFIX,
                data_type=int,
            )

    def test_prefix_accepts_string(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.PREFIX,
            data_type=str,
        )
        assert d.match_strategy == MatchStrategy.PREFIX

    def test_suffix_requires_string(self):
        with pytest.raises(ValueError, match="SUFFIX"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.SUFFIX,
                data_type=int,
            )

    def test_contains_requires_string(self):
        with pytest.raises(ValueError, match="CONTAINS"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.CONTAINS,
                data_type=int,
            )

    def test_regex_requires_string(self):
        with pytest.raises(ValueError, match="REGEX"):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.REGEX,
                data_type=int,
            )


class TestSetStrategyValidation:
    def test_set_membership_accepts_any_type(self):
        # SET strategies don't constrain data_type — lists can hold anything
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        )
        assert d.match_strategy == MatchStrategy.SET_MEMBERSHIP

    def test_set_exclusion_accepts_any_type(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.SET_EXCLUSION,
            data_type=int,
        )
        assert d.match_strategy == MatchStrategy.SET_EXCLUSION


class TestExistingValidationUnchanged:
    def test_exact_unchanged(self):
        d = Dimension(
            dimension_name="x",
            match_strategy=MatchStrategy.EXACT,
            data_type=str,
        )
        assert d.match_strategy == MatchStrategy.EXACT

    def test_range_still_requires_numeric(self):
        with pytest.raises(ValueError):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.RANGE,
                data_type=str,
                range_min_field="min",
                range_max_field="max",
            )

    def test_range_still_requires_min_max_fields(self):
        with pytest.raises(ValueError):
            Dimension(
                dimension_name="x",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
hatch run test:test-target-quick tests/test_dimension.py -v
```

Expected: Numeric/string strategy tests FAIL (validation not implemented yet). Existing validation tests PASS.

- [ ] **Step 3: Update `_validate_strategy_fields` in dimension.py**

In `src/mountainash_rules/dimension.py`, replace the `_validate_strategy_fields` method with:

```python
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

        if self.match_strategy in (
            MatchStrategy.REGEX,
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        ):
            if self.data_type is not str:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses {self.match_strategy.name} "
                    f"but data_type is {self.data_type.__name__}, expected str"
                )

        if self.match_strategy in (
            MatchStrategy.GREATER_THAN,
            MatchStrategy.LESS_THAN,
        ):
            if self.data_type not in (int, float):
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses {self.match_strategy.name} "
                    f"but data_type is {self.data_type.__name__}, expected int or float"
                )

        return self
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
hatch run test:test-target-quick tests/test_dimension.py -v
```

Expected: All 13 validation tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/dimension.py tests/test_dimension.py
git commit -m "feat(dimension): add validation rules for new match strategies"
```

---

### Task 3: Add _compile_string_match Helper and NOT_EQUAL

**Files:**
- Modify: `src/mountainash_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing tests for NOT_EQUAL**

Append to `tests/test_compiler.py`:

```python
class TestNotEqualCompilation:
    def test_not_equal_mismatch_produces_true(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.NOT_EQUAL, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": ["AU", "US", "UK"],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        # AU != AU → FALSE (-1), US != AU → TRUE (1), UK != AU → TRUE (1)
        assert values == [-1, 1, 1]

    def test_not_equal_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="region", match_strategy=MatchStrategy.NOT_EQUAL, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [UNKNOWN, "US"],
            f"{CTX_PREFIX}region": ["AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values[0] == 0   # unknown rule → unknown
        assert values[1] == 1   # US != AU → true
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestNotEqualCompilation -v
```

Expected: FAIL — `Unknown match strategy: MatchStrategy.NOT_EQUAL`.

- [ ] **Step 3: Add NOT_EQUAL case and method to compiler.py**

In `src/mountainash_rules/compiler.py`:

1. Add the case to `compile_dimension`'s match statement (before the wildcard):

```python
            case MatchStrategy.NOT_EQUAL:
                return self._compile_not_equal(dim)
```

2. Add the method after `_compile_exact`:

```python
    def _compile_not_equal(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return rule_col.t_ne(ctx_col)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestNotEqualCompilation -v
```

Expected: Both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/compiler.py tests/test_compiler.py
git commit -m "feat(compiler): add NOT_EQUAL match strategy"
```

---

### Task 4: GREATER_THAN and LESS_THAN Strategies

**Files:**
- Modify: `src/mountainash_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing tests for GREATER_THAN**

Append to `tests/test_compiler.py`:

```python
class TestGreaterThanCompilation:
    def test_greater_than_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [1500, 1500, 1500],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        # ctx > rule: 1500 > 100 (1), 1500 > 500 (1), 1500 > 1000 (1)
        assert values == [1, 1, 1]

    def test_greater_than_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [50, 50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        # 50 > 100 (-1), 50 > 500 (-1), 50 > 1000 (-1)
        assert values == [-1, -1, -1]

    def test_greater_than_equal_is_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [100],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1]  # strict >, not >=

    def test_greater_than_unknown_rule(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.GREATER_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [0]


class TestLessThanCompilation:
    def test_less_than_true(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [100, 500, 1000],
            f"{CTX_PREFIX}amount": [50, 50, 50],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        # ctx < rule: 50 < 100 (1), 50 < 500 (1), 50 < 1000 (1)
        assert values == [1, 1, 1]

    def test_less_than_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [100, 500],
            f"{CTX_PREFIX}amount": [1500, 1500],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1, -1]

    def test_less_than_equal_is_false(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [100],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [-1]

    def test_less_than_unknown_rule(self, compiler):
        dim = Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.LESS_THAN,
            data_type=int,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "amount": [UNKNOWN_NUMERIC],
            f"{CTX_PREFIX}amount": [100],
        })
        result = df.with_columns(expr.name.alias("__t_amount").compile(df, booleanizer=None))
        values = result["__t_amount"].to_list()
        assert values == [0]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestGreaterThanCompilation tests/test_compiler.py::TestLessThanCompilation -v
```

Expected: All 8 tests FAIL with `Unknown match strategy`.

- [ ] **Step 3: Add GREATER_THAN and LESS_THAN to compiler.py**

Add cases to `compile_dimension`'s match statement (before the wildcard):

```python
            case MatchStrategy.GREATER_THAN:
                return self._compile_greater_than(dim)
            case MatchStrategy.LESS_THAN:
                return self._compile_less_than(dim)
```

Add methods after `_compile_not_equal`:

```python
    def _compile_greater_than(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return ctx_col.t_gt(rule_col)

    def _compile_less_than(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        return ctx_col.t_lt(rule_col)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestGreaterThanCompilation tests/test_compiler.py::TestLessThanCompilation -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/compiler.py tests/test_compiler.py
git commit -m "feat(compiler): add GREATER_THAN and LESS_THAN strategies"
```

---

### Task 5: PREFIX Strategy and Shared _compile_string_match Helper

**Files:**
- Modify: `src/mountainash_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing tests for PREFIX**

Append to `tests/test_compiler.py`:

```python
class TestPrefixCompilation:
    def test_prefix_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["PRE-", "POST-", "MID-"],
            f"{CTX_PREFIX}code": ["PRE-001", "PRE-001", "PRE-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [1, -1, -1]

    def test_prefix_no_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["PRE-"],
            f"{CTX_PREFIX}code": ["XYZ-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [-1]

    def test_prefix_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["PRE-", UNKNOWN],
            f"{CTX_PREFIX}code": ["PRE-001", "PRE-001"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values[0] == 1
        assert values[1] == 0  # unknown pattern → unknown

    def test_prefix_per_row_different_patterns(self, compiler):
        """Each row uses its own pattern — proves column-reference support."""
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["PRE-", "POST-", "MID-"],
            f"{CTX_PREFIX}code": ["PRE-001", "POST-002", "MID-003"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [1, 1, 1]  # each row matches its own pattern
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestPrefixCompilation -v
```

Expected: All 4 tests FAIL.

- [ ] **Step 3: Add _compile_string_match helper and _compile_prefix**

Add cases to `compile_dimension`'s match statement (before the wildcard):

```python
            case MatchStrategy.PREFIX:
                return self._compile_prefix(dim)
```

Add methods after `_compile_less_than`:

```python
    def _compile_string_match(self, dim: Dimension, op_name: str) -> BaseExpressionAPI:
        """Shared wrapper for PREFIX/SUFFIX/CONTAINS/REGEX.

        Wraps a boolean-returning string operation in a sentinel-aware
        ternary expression: unknown rule → 0, match → 1, no-match → -1.
        """
        rule_col = ma.col(dim.resolved_rule_field)
        ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
        rule_is_sentinel = (
            rule_col.__eq__(ma.lit(UNKNOWN)) | rule_col.__eq__(ma.lit(NOT_SET))
        )
        match = getattr(ctx_col, op_name)(rule_col)
        return ma.when(rule_is_sentinel).then(0).when(match).then(1).otherwise(-1)

    def _compile_prefix(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "starts_with")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestPrefixCompilation -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/compiler.py tests/test_compiler.py
git commit -m "feat(compiler): add PREFIX strategy with shared string-match helper"
```

---

### Task 6: SUFFIX and CONTAINS Strategies

**Files:**
- Modify: `src/mountainash_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing tests for SUFFIX and CONTAINS**

Append to `tests/test_compiler.py`:

```python
class TestSuffixCompilation:
    def test_suffix_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["-AUD", "-USD", "-EUR"],
            f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-AUD", "TXN-AUD"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [1, -1, -1]

    def test_suffix_no_match(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["-AUD"],
            f"{CTX_PREFIX}code": ["TXN-USD"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [-1]

    def test_suffix_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["-AUD", UNKNOWN],
            f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-AUD"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_suffix_per_row_different_patterns(self, compiler):
        dim = Dimension(dimension_name="code", match_strategy=MatchStrategy.SUFFIX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "code": ["-AUD", "-USD", "-EUR"],
            f"{CTX_PREFIX}code": ["TXN-AUD", "TXN-USD", "TXN-EUR"],
        })
        result = df.with_columns(expr.name.alias("__t_code").compile(df, booleanizer=None))
        values = result["__t_code"].to_list()
        assert values == [1, 1, 1]


class TestContainsCompilation:
    def test_contains_match(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "tier": ["gold", "silver", "bronze"],
            f"{CTX_PREFIX}tier": ["gold_tier", "gold_tier", "gold_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values == [1, -1, -1]

    def test_contains_no_match(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "tier": ["gold"],
            f"{CTX_PREFIX}tier": ["platinum_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values == [-1]

    def test_contains_unknown_rule_produces_unknown(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "tier": ["gold", UNKNOWN],
            f"{CTX_PREFIX}tier": ["gold_tier", "gold_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values[0] == 1
        assert values[1] == 0

    def test_contains_per_row_different_patterns(self, compiler):
        dim = Dimension(dimension_name="tier", match_strategy=MatchStrategy.CONTAINS, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "tier": ["gold", "silver", "bronze"],
            f"{CTX_PREFIX}tier": ["gold_tier", "silver_tier", "bronze_tier"],
        })
        result = df.with_columns(expr.name.alias("__t_tier").compile(df, booleanizer=None))
        values = result["__t_tier"].to_list()
        assert values == [1, 1, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestSuffixCompilation tests/test_compiler.py::TestContainsCompilation -v
```

Expected: All 8 tests FAIL.

- [ ] **Step 3: Add SUFFIX and CONTAINS to compiler.py**

Add cases to `compile_dimension`'s match statement:

```python
            case MatchStrategy.SUFFIX:
                return self._compile_suffix(dim)
            case MatchStrategy.CONTAINS:
                return self._compile_contains(dim)
```

Add methods after `_compile_prefix`:

```python
    def _compile_suffix(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "ends_with")

    def _compile_contains(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "contains")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestSuffixCompilation tests/test_compiler.py::TestContainsCompilation -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/compiler.py tests/test_compiler.py
git commit -m "feat(compiler): add SUFFIX and CONTAINS strategies"
```

---

### Task 7: Rewrite REGEX Strategy (Backend-Agnostic)

**Files:**
- Modify: `src/mountainash_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Add new REGEX per-row test**

Append to the existing `TestRegexCompilation` class in `tests/test_compiler.py`:

```python
    def test_regex_per_row_different_patterns(self, compiler):
        """Each row uses its own regex pattern — proves backend-agnostic per-row support."""
        dim = Dimension(dimension_name="pattern", match_strategy=MatchStrategy.REGEX, data_type=str)
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "pattern": ["^AU.*", "^US.*", "^UK.*"],
            f"{CTX_PREFIX}pattern": ["AU-123", "US-456", "UK-789"],
        })
        result = df.with_columns(expr.name.alias("__t_pattern").compile(df, booleanizer=None))
        values = result["__t_pattern"].to_list()
        assert values == [1, 1, 1]  # each row matches its own pattern
```

- [ ] **Step 2: Run test — it will currently pass with the old implementation**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestRegexCompilation -v
```

Expected: All 4 tests PASS (old Polars-specific implementation still handles per-row).

- [ ] **Step 3: Rewrite _compile_regex in compiler.py**

Replace the entire `_compile_regex` method in `src/mountainash_rules/compiler.py` with:

```python
    def _compile_regex(self, dim: Dimension) -> BaseExpressionAPI:
        return self._compile_string_match(dim, "regex_contains")
```

- [ ] **Step 4: Remove now-unused imports from compiler.py**

Remove these lines from the top of `src/mountainash_rules/compiler.py`:

```python
import re

import polars as pl
```

Keep `import mountainash.expressions as ma` and `from mountainash.expressions import BaseExpressionAPI`.

- [ ] **Step 5: Run all regex tests to verify they still pass**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestRegexCompilation -v
```

Expected: All 4 tests PASS with the new backend-agnostic implementation.

- [ ] **Step 6: Run all compiler tests to verify nothing regressed**

```bash
hatch run test:test-target-quick tests/test_compiler.py -v
```

Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/mountainash_rules/compiler.py tests/test_compiler.py
git commit -m "refactor(compiler): rewrite REGEX to be backend-agnostic using regex_contains"
```

---

### Task 8: SET_MEMBERSHIP and SET_EXCLUSION Strategies

**Files:**
- Modify: `src/mountainash_rules/compiler.py`
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing tests for SET strategies**

Append to `tests/test_compiler.py`:

```python
class TestSetMembershipCompilation:
    def test_set_membership_match(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [["AU", "NZ", "UK"], ["US", "CA"], ["DE", "FR"]],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        # AU in [AU,NZ,UK] → 1; AU in [US,CA] → -1; AU in [DE,FR] → -1
        assert values == [1, -1, -1]

    def test_set_membership_unknown_context(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [["AU", "NZ"]],
            f"{CTX_PREFIX}region": [UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [0]  # unknown context → unknown

    def test_set_membership_empty_list(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [[]],
            f"{CTX_PREFIX}region": ["AU"],
        }, schema={"region": pl.List(pl.Utf8), f"{CTX_PREFIX}region": pl.Utf8})
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [-1]  # not in empty list


class TestSetExclusionCompilation:
    def test_set_exclusion_match(self, compiler):
        """Returns TRUE when context value is NOT in the rule's list."""
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_EXCLUSION,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [["AU", "NZ", "UK"], ["US", "CA"], ["DE", "FR"]],
            f"{CTX_PREFIX}region": ["AU", "AU", "AU"],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        # AU not in [AU,NZ,UK] → -1; AU not in [US,CA] → 1; AU not in [DE,FR] → 1
        assert values == [-1, 1, 1]

    def test_set_exclusion_unknown_context(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_EXCLUSION,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [["AU", "NZ"]],
            f"{CTX_PREFIX}region": [UNKNOWN],
        })
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [0]

    def test_set_exclusion_empty_list(self, compiler):
        dim = Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.SET_EXCLUSION,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)

        df = pl.DataFrame({
            "region": [[]],
            f"{CTX_PREFIX}region": ["AU"],
        }, schema={"region": pl.List(pl.Utf8), f"{CTX_PREFIX}region": pl.Utf8})
        result = df.with_columns(expr.name.alias("__t_region").compile(df, booleanizer=None))
        values = result["__t_region"].to_list()
        assert values == [1]  # not in empty list → true
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestSetMembershipCompilation tests/test_compiler.py::TestSetExclusionCompilation -v
```

Expected: All 6 tests FAIL with `Unknown match strategy`.

- [ ] **Step 3: Add SET strategies to compiler.py**

Add cases to `compile_dimension`'s match statement:

```python
            case MatchStrategy.SET_MEMBERSHIP:
                return self._compile_set_membership(dim)
            case MatchStrategy.SET_EXCLUSION:
                return self._compile_set_exclusion(dim)
```

Add methods after `_compile_contains`:

```python
    def _compile_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        rule_col = ma.col(dim.resolved_rule_field)
        return ctx_col.t_is_in(rule_col)

    def _compile_set_exclusion(self, dim: Dimension) -> BaseExpressionAPI:
        sentinels = self._sentinels_for_type(dim.data_type)
        ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
        rule_col = ma.col(dim.resolved_rule_field)
        return ctx_col.t_is_not_in(rule_col)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestSetMembershipCompilation tests/test_compiler.py::TestSetExclusionCompilation -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/compiler.py tests/test_compiler.py
git commit -m "feat(compiler): add SET_MEMBERSHIP and SET_EXCLUSION strategies"
```

---

### Task 9: Integration Test — Mixed Strategy Scenario

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Append fraud detection scenario**

Append to `tests/test_integration.py`:

```python
class TestMixedStrategyFraudDetection:
    """Exercises EXACT, NOT_EQUAL, SET_MEMBERSHIP, GREATER_THAN, and PREFIX together."""

    @pytest.fixture
    def fraud_engine(self):
        rules_df = pl.DataFrame({
            "rule_name": ["catch_all", "high_value", "blacklist_merchant", "specific_txn"],
            "action": ["allow", "review", "block", "block"],
            # EXACT: merchant_type must match
            "merchant_type": [UNKNOWN, UNKNOWN, "CASINO", "RETAIL"],
            # SET_MEMBERSHIP: country must be in whitelist
            "allowed_countries": [
                ["AU", "NZ", "US", "UK"],
                ["AU", "NZ", "US", "UK"],
                ["AU", "NZ", "US", "UK"],
                ["AU"],
            ],
            # GREATER_THAN: transaction exceeds threshold
            "amount_threshold": [UNKNOWN_NUMERIC, 10000, UNKNOWN_NUMERIC, 500],
            # PREFIX: transaction code starts with pattern
            "code_prefix": [UNKNOWN, UNKNOWN, UNKNOWN, "TXN-"],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="merchant_type",
                match_strategy=MatchStrategy.EXACT,
                data_type=str,
            ),
            Dimension(
                dimension_name="country",
                context_field="country",
                rule_field="allowed_countries",
                match_strategy=MatchStrategy.SET_MEMBERSHIP,
                data_type=str,
            ),
            Dimension(
                dimension_name="amount",
                context_field="amount",
                rule_field="amount_threshold",
                match_strategy=MatchStrategy.GREATER_THAN,
                data_type=int,
            ),
            Dimension(
                dimension_name="code",
                context_field="code",
                rule_field="code_prefix",
                match_strategy=MatchStrategy.PREFIX,
                data_type=str,
            ),
        ])
        return ExpressionRulesEngine(rules=rules_df, dimension_metadata=metadata)

    def test_catch_all_fallback(self, fraud_engine):
        """Low-value retail in allowed country → catch_all allows."""
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "AU",
            "amount": 100,
            "code": "TXN-001",
        })
        # catch_all (1 hard match: country) beats nothing else
        # specific_txn: merchant=RETAIL (1), country AU in [AU] (1), amount 100 > 500 FALSE → eliminated
        # So catch_all and specific_txn compete — but specific_txn's GREATER_THAN fails
        # Expected: catch_all wins
        assert result.best_match["rule_name"][0] == "catch_all"
        assert result.best_match["action"][0] == "allow"

    def test_high_value_review(self, fraud_engine):
        """High-value retail transaction → high_value rule triggers review."""
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "US",
            "amount": 15000,
            "code": "TXN-999",
        })
        # high_value: country US in list (1), amount 15000 > 10000 (1) → 2 hard matches
        # catch_all: country US in list (1) → 1 hard match
        assert result.best_match["rule_name"][0] == "high_value"
        assert result.best_match["action"][0] == "review"

    def test_blacklist_merchant_blocks(self, fraud_engine):
        """Casino merchant in allowed country → blacklist blocks."""
        result = fraud_engine.evaluate(context={
            "merchant_type": "CASINO",
            "country": "AU",
            "amount": 100,
            "code": "TXN-001",
        })
        # blacklist_merchant: merchant=CASINO (1), country AU in list (1) → 2 hard matches
        # catch_all: country AU in list (1) → 1 hard match
        assert result.best_match["rule_name"][0] == "blacklist_merchant"
        assert result.best_match["action"][0] == "block"

    def test_all_strategies_rank_together(self, fraud_engine):
        """Retail, AU, 1000, TXN-001 matches specific_txn (highest specificity)."""
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "AU",
            "amount": 1000,
            "code": "TXN-001",
        })
        # specific_txn: merchant=RETAIL (1), country AU in [AU] (1), 1000 > 500 (1), code TXN-* (1) → 4 hard matches
        # catch_all: country AU in list (1) → 1 hard match
        assert result.best_match["rule_name"][0] == "specific_txn"
        assert result.best_match["__specificity"][0] == 4
```

- [ ] **Step 2: Run integration tests**

```bash
hatch run test:test-target-quick tests/test_integration.py::TestMixedStrategyFraudDetection -v
```

Expected: All 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(integration): add fraud detection scenario using mixed strategies"
```

---

### Task 10: Backend Agnosticism Smoke Tests

**Files:**
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Add parametrized backend smoke test**

Append to `tests/test_compiler.py`:

```python
import ibis


class TestBackendAgnosticism:
    """Smoke tests: each strategy compiles cleanly against multiple backends."""

    def _sample_df_polars(self):
        return pl.DataFrame({
            "str_col": ["A", "B"],
            "num_col": [10, 20],
            "list_col": [["A", "B"], ["C", "D"]],
            f"{CTX_PREFIX}str_col": ["A", "A"],
            f"{CTX_PREFIX}num_col": [15, 15],
            f"{CTX_PREFIX}list_col": ["A", "A"],
        })

    def _sample_df_ibis(self):
        return ibis.memtable(self._sample_df_polars().to_pandas())

    @pytest.mark.parametrize("backend_name", ["polars", "ibis"])
    @pytest.mark.parametrize("strategy,field,data_type,extras", [
        (MatchStrategy.EXACT, "str_col", str, {}),
        (MatchStrategy.NOT_EQUAL, "str_col", str, {}),
        (MatchStrategy.RANGE, "num_col", int, {"range_min_field": "num_col", "range_max_field": "num_col"}),
        (MatchStrategy.GREATER_THAN, "num_col", int, {}),
        (MatchStrategy.LESS_THAN, "num_col", int, {}),
        (MatchStrategy.PREFIX, "str_col", str, {}),
        (MatchStrategy.SUFFIX, "str_col", str, {}),
        (MatchStrategy.CONTAINS, "str_col", str, {}),
        (MatchStrategy.REGEX, "str_col", str, {}),
        (MatchStrategy.SET_MEMBERSHIP, "list_col", str, {}),
        (MatchStrategy.SET_EXCLUSION, "list_col", str, {}),
    ])
    def test_strategy_compiles_on_backend(self, compiler, backend_name, strategy, field, data_type, extras):
        dim = Dimension(
            dimension_name=field,
            match_strategy=strategy,
            data_type=data_type,
            **extras,
        )
        expr = compiler.compile_dimension(dim)

        if backend_name == "polars":
            df = self._sample_df_polars()
        else:
            df = self._sample_df_ibis()

        # Compilation should succeed and return a native backend expression
        compiled = expr.compile(df, booleanizer=None)
        assert compiled is not None
```

- [ ] **Step 2: Run the smoke tests**

```bash
hatch run test:test-target-quick tests/test_compiler.py::TestBackendAgnosticism -v
```

Expected: 22 tests PASS (11 strategies × 2 backends).

Note: If Ibis doesn't support list columns natively in memtable, the SET_MEMBERSHIP/SET_EXCLUSION Ibis cases may fail. If so, mark them as `@pytest.mark.xfail` with a comment explaining the upstream limitation rather than removing them.

- [ ] **Step 3: Commit**

```bash
git add tests/test_compiler.py
git commit -m "test(compiler): add backend agnosticism smoke tests for all strategies"
```

---

### Task 12: Full Test Suite and Lint Pass

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

```bash
hatch run test:test-target-quick tests/ -v
```

Expected: All tests PASS. Count should be 51 (Task 1 of previous plan) + 13 (dimension validation) + 2 (NOT_EQUAL) + 8 (GT/LT) + 4 (PREFIX) + 8 (SUFFIX/CONTAINS) + 1 (new REGEX per-row) + 6 (SET) + 4 (fraud integration) = 97 tests approximately.

- [ ] **Step 2: Run linter**

```bash
uvx ruff check src/
```

Expected: "All checks passed!" If there are issues, fix them inline (most likely unused imports from the REGEX rewrite).

- [ ] **Step 3: Run full suite with coverage**

```bash
hatch run test:test
```

Expected: All tests PASS. Coverage should remain high (>= 90%).

- [ ] **Step 4: Commit any lint fixes**

If Step 2 found issues that needed fixing:

```bash
git add -u
git commit -m "style: fix lint issues in extended match strategies"
```

Skip this step if no fixes were needed.

---

### Task 13: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Match Strategies section**

Locate the section in `CLAUDE.md` that describes the rule engine (look for "MatchStrategy" or "match strategies"). Update it to reflect the expanded catalog.

Add a section describing all 11 strategies with their column formats:

```markdown
## Match Strategies

The rules engine supports 11 match strategies via the `MatchStrategy` enum:

| Strategy | Rule Column Format | Data Type | Description |
|----------|-------------------|-----------|-------------|
| `EXACT` | Scalar value | any | Rule value equals context value |
| `NOT_EQUAL` | Scalar value | any | Rule value does not equal context value |
| `RANGE` | Two columns (min/max) | int, float | Context value within [min, max] |
| `GREATER_THAN` | Threshold value | int, float | Context value > rule threshold |
| `LESS_THAN` | Threshold value | int, float | Context value < rule threshold |
| `PREFIX` | Prefix string | str | Context value starts with rule |
| `SUFFIX` | Suffix string | str | Context value ends with rule |
| `CONTAINS` | Substring | str | Context value contains rule |
| `REGEX` | Regex pattern | str | Context value matches rule pattern (search semantics) |
| `SET_MEMBERSHIP` | List column | any | Context value is in rule's list |
| `SET_EXCLUSION` | List column | any | Context value is not in rule's list |

All strategies are backend-agnostic (Polars, Ibis, Narwhals) and support per-row patterns.

Unknown sentinel values (`<NA>` for strings, `-999999999` for numerics) in either the rule or context column produce UNKNOWN (0) ternary results, which count as wildcards in ranking but do not eliminate the rule.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with extended match strategies catalog"
```

---
