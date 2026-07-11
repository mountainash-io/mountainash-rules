# Serialisable Dimension Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `DimensionsMetadata` a serialisable wire format — StrEnums, a `DataType` enum with temporal support, YAML round-trip, per-row REGEX — per `docs/superpowers/specs/2026-07-12-serializable-dimension-metadata-design.md`.

**Architecture:** Enum and sentinel changes land in `constants.py` first (everything imports from there), then `dimension.py` migrates `data_type` behind a deprecation shim, then the call-site sweep, then features that build on the new types (YAML, per-row REGEX, temporal, bool). Babel changes are the final two tasks (separate repo, path-installed dependency).

**Tech Stack:** Python 3.12 `enum.StrEnum`, pydantic v2, pyyaml (new core dep), mountainash expressions, polars/duckdb test fixtures.

## Global Constraints

- Enum values are explicit lowercase strings (`"exact"`, `"range"`, `"constraint"`, ...) — never `auto()`.
- All serialisation uses `model_dump(mode="json", exclude_defaults=True)`.
- Temporal sentinels: `UNKNOWN_DATE = date(1,1,1)`, `NOT_SET_DATE = date(1,1,2)`, `UNKNOWN_DATETIME = datetime(1,1,1)`, `NOT_SET_DATETIME = datetime(1,1,2)`.
- `valid_values` entries constrained to `str | int | float | bool`.
- This is the sanctioned breaking-change window (pre-adoption); the only compatibility shim is `data_type` accepting Python types with a `DeprecationWarning`.
- Test commands: `hatch run test:test-target <node>`, `hatch run test:test-quick`. Babel repo: same commands from `../mountainash-rules-babel`.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: StrEnum migration for MatchStrategy and DimensionRole

**Files:**
- Modify: `src/mountainash_rules/constants.py`
- Test: `tests/test_dimension_serialization.py` (create)

**Interfaces:**
- Produces: `MatchStrategy(StrEnum)` with values `"exact"`, `"not_equal"`, `"range"`, `"greater_than"`, `"less_than"`, `"prefix"`, `"suffix"`, `"contains"`, `"regex"`, `"context_regex"`, `"set_membership"`, `"set_exclusion"`; `DimensionRole(StrEnum)` with `"constraint"`, `"context_key"`. The new `CONTEXT_REGEX` member exists from this task but only gains behaviour in Task 6.

- [ ] **Step 1: Write the failing tests** (create `tests/test_dimension_serialization.py`)

```python
"""Tests for serialisable dimension metadata: StrEnums, DataType, YAML."""

import warnings

import pytest

from mountainash_rules.constants import DimensionRole, MatchStrategy


class TestStrEnums:
    def test_match_strategy_constructs_from_string(self):
        assert MatchStrategy("range") is MatchStrategy.RANGE

    def test_match_strategy_value_is_stable_string(self):
        assert MatchStrategy.GREATER_THAN.value == "greater_than"

    def test_match_strategy_is_str(self):
        assert isinstance(MatchStrategy.EXACT, str)

    def test_context_regex_member_exists(self):
        assert MatchStrategy("context_regex") is MatchStrategy.CONTEXT_REGEX

    def test_dimension_role_constructs_from_string(self):
        assert DimensionRole("context_key") is DimensionRole.CONTEXT_KEY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestStrEnums -v`
Expected: FAIL — `ValueError: 'range' is not a valid MatchStrategy` (int enum) and missing `CONTEXT_REGEX`.

- [ ] **Step 3: Implement** (replace the two enums in `src/mountainash_rules/constants.py`)

```python
from enum import StrEnum


class MatchStrategy(StrEnum):
    """How a dimension matches context values against rule values."""

    EXACT = "exact"
    NOT_EQUAL = "not_equal"
    RANGE = "range"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    REGEX = "regex"
    CONTEXT_REGEX = "context_regex"
    SET_MEMBERSHIP = "set_membership"
    SET_EXCLUSION = "set_exclusion"


class DimensionRole(StrEnum):
    """Whether a dimension partitions the lattice or participates in coalesce."""

    CONSTRAINT = "constraint"
    CONTEXT_KEY = "context_key"
```

Remove the now-unused `from enum import Enum, auto` import.

- [ ] **Step 4: Run the new tests, then the full quick suite**

Run: `hatch run test:test-target tests/test_dimension_serialization.py -v` → PASS
Run: `hatch run test:test-quick` → PASS (the enums are compared by identity everywhere in src; any test comparing `.value` to an int must be updated to the string — list them in the commit message).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/constants.py tests/test_dimension_serialization.py
git commit -m "feat!: MatchStrategy and DimensionRole become StrEnums with stable values

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: DataType enum, temporal sentinels, sentinels_for()

**Files:**
- Modify: `src/mountainash_rules/constants.py`
- Test: `tests/test_dimension_serialization.py`

**Interfaces:**
- Produces: `DataType(StrEnum)` with `.is_numeric`, `.is_temporal`, `.python_type`; sentinel constants `UNKNOWN_DATE`, `NOT_SET_DATE`, `UNKNOWN_DATETIME`, `NOT_SET_DATETIME`, sets `TEMPORAL_DATE_SENTINELS`, `TEMPORAL_DATETIME_SENTINELS`; functions `sentinels_for(data_type: DataType) -> set` and `not_set_sentinel_for(data_type: DataType)` and `unknown_sentinel_for(data_type: DataType)`. Tasks 3–7 consume all of these.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_dimension_serialization.py`)

```python
import datetime

from mountainash_rules.constants import (
    DataType,
    NOT_SET,
    NOT_SET_DATE,
    NOT_SET_NUMERIC,
    NUMERIC_SENTINELS,
    STRING_SENTINELS,
    TEMPORAL_DATE_SENTINELS,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    not_set_sentinel_for,
    sentinels_for,
    unknown_sentinel_for,
)


class TestDataType:
    def test_values(self):
        assert DataType("int") is DataType.INT
        assert DataType.DATE.value == "date"

    def test_is_numeric(self):
        assert DataType.INT.is_numeric and DataType.FLOAT.is_numeric
        assert not DataType.STR.is_numeric and not DataType.DATE.is_numeric

    def test_is_temporal(self):
        assert DataType.DATE.is_temporal and DataType.DATETIME.is_temporal
        assert not DataType.INT.is_temporal

    def test_python_type(self):
        assert DataType.DATE.python_type is datetime.date
        assert DataType.BOOL.python_type is bool


class TestSentinelSelection:
    def test_temporal_sentinels_are_proleptic_floor(self):
        assert UNKNOWN_DATE == datetime.date(1, 1, 1)
        assert NOT_SET_DATE == datetime.date(1, 1, 2)
        assert UNKNOWN_DATETIME == datetime.datetime(1, 1, 1)

    def test_sentinels_for(self):
        assert sentinels_for(DataType.INT) == NUMERIC_SENTINELS
        assert sentinels_for(DataType.STR) == STRING_SENTINELS
        assert sentinels_for(DataType.DATE) == TEMPORAL_DATE_SENTINELS

    def test_scalar_lookups(self):
        assert not_set_sentinel_for(DataType.FLOAT) == NOT_SET_NUMERIC
        assert not_set_sentinel_for(DataType.STR) == NOT_SET
        assert unknown_sentinel_for(DataType.DATE) == UNKNOWN_DATE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestDataType tests/test_dimension_serialization.py::TestSentinelSelection -v`
Expected: FAIL — ImportError on `DataType`.

- [ ] **Step 3: Implement** (append to `src/mountainash_rules/constants.py`)

```python
import datetime


class DataType(StrEnum):
    """Serialisable dimension data type."""

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATE = "date"
    DATETIME = "datetime"

    @property
    def is_numeric(self) -> bool:
        return self in (DataType.INT, DataType.FLOAT)

    @property
    def is_temporal(self) -> bool:
        return self in (DataType.DATE, DataType.DATETIME)

    @property
    def python_type(self) -> type:
        return _DATATYPE_TO_PYTHON[self]


_DATATYPE_TO_PYTHON: dict[DataType, type] = {
    DataType.STR: str,
    DataType.INT: int,
    DataType.FLOAT: float,
    DataType.BOOL: bool,
    DataType.DATE: datetime.date,
    DataType.DATETIME: datetime.datetime,
}
PYTHON_TO_DATATYPE: dict[type, DataType] = {
    v: k for k, v in _DATATYPE_TO_PYTHON.items()
}

# Temporal don't-care sentinels: the proleptic floor, where no business
# data lives.
UNKNOWN_DATE = datetime.date(1, 1, 1)
NOT_SET_DATE = datetime.date(1, 1, 2)
UNKNOWN_DATETIME = datetime.datetime(1, 1, 1)
NOT_SET_DATETIME = datetime.datetime(1, 1, 2)
TEMPORAL_DATE_SENTINELS = {UNKNOWN_DATE, NOT_SET_DATE}
TEMPORAL_DATETIME_SENTINELS = {UNKNOWN_DATETIME, NOT_SET_DATETIME}


def sentinels_for(data_type: DataType) -> set:
    """The unknown-sentinel set the expression layer treats as UNKNOWN (0)."""
    if data_type.is_numeric:
        return NUMERIC_SENTINELS
    if data_type is DataType.DATE:
        return TEMPORAL_DATE_SENTINELS
    if data_type is DataType.DATETIME:
        return TEMPORAL_DATETIME_SENTINELS
    return STRING_SENTINELS


def unknown_sentinel_for(data_type: DataType):
    """The rule-side don't-care value for a data type."""
    if data_type.is_numeric:
        return UNKNOWN_NUMERIC
    if data_type is DataType.DATE:
        return UNKNOWN_DATE
    if data_type is DataType.DATETIME:
        return UNKNOWN_DATETIME
    return UNKNOWN


def not_set_sentinel_for(data_type: DataType):
    """The context-side missing-value sentinel for a data type."""
    if data_type.is_numeric:
        return NOT_SET_NUMERIC
    if data_type is DataType.DATE:
        return NOT_SET_DATE
    if data_type is DataType.DATETIME:
        return NOT_SET_DATETIME
    return NOT_SET
```

(`BOOL` intentionally falls through to string sentinels in `sentinels_for` — it is never used for bool; Task 7 gives bool null semantics instead.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/test_dimension_serialization.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/constants.py tests/test_dimension_serialization.py
git commit -m "feat: DataType enum, temporal sentinels, sentinels_for lookup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Dimension.data_type migration + call-site sweep

**Files:**
- Modify: `src/mountainash_rules/dimension.py`, `src/mountainash_rules/compiler.py`, `src/mountainash_rules/context.py`, `src/mountainash_rules/accumulator_compiler.py`, `src/mountainash_rules/accumulator_engine.py`
- Test: `tests/test_dimension_serialization.py`, existing suites

**Interfaces:**
- Consumes: `DataType`, `PYTHON_TO_DATATYPE`, `sentinels_for`, `unknown_sentinel_for`, `not_set_sentinel_for` (Task 2).
- Produces: `Dimension.data_type: DataType = DataType.STR` (shim accepts Python types with `DeprecationWarning`). Every internal type check goes through the enum; RANGE/GT/LT validation accepts numeric **or** temporal.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_dimension_serialization.py`)

```python
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.constants import MatchStrategy


class TestDataTypeMigration:
    def test_enum_accepted_directly(self):
        d = Dimension(dimension_name="x", data_type=DataType.INT)
        assert d.data_type is DataType.INT

    def test_string_value_accepted(self):
        d = Dimension(dimension_name="x", data_type="float")
        assert d.data_type is DataType.FLOAT

    def test_python_type_accepted_with_deprecation_warning(self):
        with pytest.warns(DeprecationWarning):
            d = Dimension(dimension_name="x", data_type=int)
        assert d.data_type is DataType.INT

    def test_range_accepts_temporal(self):
        d = Dimension(
            dimension_name="eff", match_strategy=MatchStrategy.RANGE,
            data_type=DataType.DATE,
            range_min_field="eff_from", range_max_field="eff_to",
        )
        assert d.data_type is DataType.DATE

    def test_range_rejects_bool(self):
        with pytest.raises(ValueError, match="range"):
            Dimension(
                dimension_name="x", match_strategy=MatchStrategy.RANGE,
                data_type=DataType.BOOL,
                range_min_field="a", range_max_field="b",
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestDataTypeMigration -v`
Expected: FAIL — pydantic rejects `DataType.INT`/`"float"` against `data_type: type`, or validators reject temporal RANGE.

- [ ] **Step 3: Implement in `dimension.py`**

```python
import warnings

from pydantic import BaseModel, field_validator, model_validator

from mountainash_rules.constants import (
    PYTHON_TO_DATATYPE,
    DataType,
    DimensionRole,
    MatchStrategy,
)


class Dimension(BaseModel):
    """A single dimension that rules are evaluated against."""

    dimension_name: str
    context_field: t.Optional[str] = None
    rule_field: t.Optional[str] = None
    match_strategy: MatchStrategy = MatchStrategy.EXACT
    data_type: DataType = DataType.STR
    role: DimensionRole = DimensionRole.CONSTRAINT
    valid_values: list[t.Any] = []          # tightened in Task 8

    # ... (RANGE / REGEX fields unchanged)

    @field_validator("data_type", mode="before")
    @classmethod
    def _coerce_data_type(cls, v):
        if isinstance(v, type):
            if v not in PYTHON_TO_DATATYPE:
                raise ValueError(f"Unsupported data_type class: {v!r}")
            warnings.warn(
                "Passing a Python type as data_type is deprecated; "
                f"use DataType.{PYTHON_TO_DATATYPE[v].name} or "
                f"'{PYTHON_TO_DATATYPE[v].value}'.",
                DeprecationWarning,
                stacklevel=4,
            )
            return PYTHON_TO_DATATYPE[v]
        return v
```

Update `_validate_strategy_fields` — the orderable checks accept numeric or temporal, the string-strategy check uses the enum, and error messages format `.value` (StrEnum members have no useful `__name__`):

```python
        orderable = self.data_type.is_numeric or self.data_type.is_temporal
        if self.match_strategy == MatchStrategy.RANGE:
            if not self.range_min_field or not self.range_max_field:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses RANGE strategy "
                    f"but is missing range_min_field or range_max_field"
                )
            if not orderable:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses range strategy "
                    f"but data_type is {self.data_type.value}; expected a "
                    f"numeric or temporal type"
                )

        if self.match_strategy in (
            MatchStrategy.REGEX,
            MatchStrategy.CONTEXT_REGEX,
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        ):
            if self.data_type is not DataType.STR:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses "
                    f"{self.match_strategy.value} but data_type is "
                    f"{self.data_type.value}; expected str"
                )

        if self.match_strategy in (
            MatchStrategy.GREATER_THAN,
            MatchStrategy.LESS_THAN,
        ):
            if not orderable:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses "
                    f"{self.match_strategy.value} but data_type is "
                    f"{self.data_type.value}; expected a numeric or "
                    f"temporal type"
                )
```

(Keep the existing REGEX/regex_pattern block for now; Task 6 rewrites it.)

- [ ] **Step 4: Sweep every internal call site**

Run: `grep -rn "data_type" src/ | grep -v "\.value\|DataType\|data_type:"` and fix each hit:

- `compiler.py::_sentinels_for_type` — delete the method; every caller uses `sentinels_for(dim.data_type)` imported from constants.
- `accumulator_compiler.py::_sentinel_checks` — `sentinel = unknown_sentinel_for(dim.data_type)`.
- `accumulator_engine.py::_create_anchor` non-RANGE branch — `sentinel = unknown_sentinel_for(dim.data_type)`.
- `context.py` — the P0 branch `dim.data_type in (int, float)` becomes `not_set_sentinel_for(dim.data_type)` for the general case (Task 7 adds the BOOL exception).

- [ ] **Step 5: Run the full quick suite**

Run: `hatch run test:test-quick`
Expected: PASS. Existing tests construct `Dimension(data_type=int)` etc. — they now emit `DeprecationWarning`s through the shim, which is correct; add `filterwarnings = ["ignore::DeprecationWarning:mountainash_rules"]` is **not** wanted — instead migrate test fixtures opportunistically only in files this plan already touches, and leave the rest warning (they prove the shim works).

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/ tests/test_dimension_serialization.py
git commit -m "feat!: Dimension.data_type becomes DataType enum with deprecation shim

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: YAML round-trip on DimensionsMetadata

**Files:**
- Modify: `src/mountainash_rules/dimension.py`, `pyproject.toml` (add `pyyaml` to `dependencies`)
- Test: `tests/test_dimension_serialization.py`

**Interfaces:**
- Produces: `DimensionsMetadata.to_yaml() -> str`, `from_yaml(text) -> DimensionsMetadata` (classmethod), `to_yaml_file(path) -> Path`, `from_yaml_file(path) -> DimensionsMetadata` (classmethod). Babel Task 9 and the babel lattice-contract plan consume these.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_dimension_serialization.py`)

```python
class TestYamlRoundTrip:
    def _full_metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", valid_values=["AU", "NZ"]),
            Dimension(
                dimension_name="amount", match_strategy=MatchStrategy.RANGE,
                data_type=DataType.INT,
                range_min_field="amt_min", range_max_field="amt_max",
                range_max_inclusive=False,
            ),
            Dimension(
                dimension_name="chan", context_field="channel",
                rule_field="chan_rule", match_strategy=MatchStrategy.PREFIX,
            ),
            Dimension(
                dimension_name="segment", role="context_key",
            ),
        ])

    def test_round_trip_equality(self, tmp_path):
        md = self._full_metadata()
        assert DimensionsMetadata.from_yaml(md.to_yaml()) == md

    def test_yaml_contains_string_values_not_ints(self):
        text = self._full_metadata().to_yaml()
        assert "range" in text
        assert "context_key" in text

    def test_file_round_trip(self, tmp_path):
        md = self._full_metadata()
        p = md.to_yaml_file(tmp_path / "md.yaml")
        assert DimensionsMetadata.from_yaml_file(p) == md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestYamlRoundTrip -v`
Expected: FAIL — `AttributeError: ... no attribute 'to_yaml'`.

- [ ] **Step 3: Implement** (append methods to `DimensionsMetadata`; add `pyyaml` to `pyproject.toml` `[project] dependencies`)

```python
import pathlib

import yaml


class DimensionsMetadata(BaseModel):
    # ... existing ...

    def to_yaml(self) -> str:
        """Serialise to YAML (defaults omitted for forward compatibility)."""
        return yaml.safe_dump(
            self.model_dump(mode="json", exclude_defaults=True),
            sort_keys=False,
        )

    @classmethod
    def from_yaml(cls, text: str) -> "DimensionsMetadata":
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml_file(self, path: "str | pathlib.Path") -> pathlib.Path:
        path = pathlib.Path(path)
        path.write_text(self.to_yaml(), encoding="utf-8")
        return path

    @classmethod
    def from_yaml_file(cls, path: "str | pathlib.Path") -> "DimensionsMetadata":
        return cls.from_yaml(pathlib.Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `hatch run test:test-target tests/test_dimension_serialization.py -v` → PASS
Run: `hatch run test:test-quick` → PASS (env rebuild picks up pyyaml).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/dimension.py pyproject.toml tests/test_dimension_serialization.py
git commit -m "feat: YAML round-trip for DimensionsMetadata (new dep: pyyaml)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Temporal RANGE end-to-end (filter engine + accumulator)

**Files:**
- Modify: `src/mountainash_rules/accumulator_compiler.py` (replace remaining hard-coded `UNKNOWN_NUMERIC` in `_range_sentinel_checks`, `compile_coalesce_na_flag`, `_coalesce_range`, `_coalesce_threshold` with `unknown_sentinel_for(dim.data_type)`); `src/mountainash_rules/accumulator_engine.py` (`_create_anchor` RANGE branch likewise)
- Test: `tests/test_dimension_serialization.py`

**Interfaces:**
- Consumes: `sentinels_for`/`unknown_sentinel_for` (Task 2), migrated `data_type` (Task 3).
- Produces: `DataType.DATE`/`DATETIME` dimensions work for EXACT/NOT_EQUAL/RANGE/GT/LT in both engines, with sentinel bounds acting unconstrained.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_dimension_serialization.py`)

```python
import polars as pl

from mountainash_rules.constants import UNKNOWN_DATE
from mountainash_rules.engine import ExpressionRulesEngine


def _effective_dated_metadata():
    return DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="asof", match_strategy=MatchStrategy.RANGE,
            data_type=DataType.DATE,
            range_min_field="eff_from", range_max_field="eff_to",
        ),
    ])


class TestTemporalRange:
    def _rules(self):
        return pl.DataFrame({
            "rule_name": ["current", "expired", "open_ended"],
            "eff_from": [
                datetime.date(2026, 1, 1),
                datetime.date(2024, 1, 1),
                datetime.date(2026, 6, 1),
            ],
            "eff_to": [
                datetime.date(2026, 12, 31),
                datetime.date(2024, 12, 31),
                UNKNOWN_DATE,  # no expiry
            ],
        })

    def test_filter_engine_matches_by_date(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({"asof": datetime.date(2026, 7, 12)})
        assert result.count == 2  # current + open_ended, not expired

    def test_sentinel_bound_is_unconstrained_ternary_zero(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({"asof": datetime.date(2027, 6, 1)})
        # only open_ended survives; its sentinel max yields UNKNOWN (0)
        assert result.explain("open_ended") == {"asof": 0}

    def test_missing_date_context_is_unknown(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({})
        assert result.count == 3  # NOT_SET_DATE -> all UNKNOWN wildcards

    def test_accumulator_combines_overlapping_date_ranges(self):
        from mountainash.relations import relation
        from mountainash_rules.accumulator_engine import AccumulatorEngine
        engine = AccumulatorEngine(dimension_metadata=_effective_dated_metadata())
        rules = pl.DataFrame({
            "rule_name": ["A", "B"],
            "eff_from": [datetime.date(2026, 1, 1), datetime.date(2026, 6, 1)],
            "eff_to": [datetime.date(2026, 12, 31), UNKNOWN_DATE],
        })
        lattice = engine.build(rules)
        rows = relation(lattice.combinations).to_dict()
        assert 6 in set(rows["__prime_product"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestTemporalRange -v`
Expected: FAIL — either the accumulator compares dates against the numeric sentinel (type error) or missing-context handling substitutes the wrong sentinel type.

- [ ] **Step 3: Implement the sentinel sweep**

In `accumulator_compiler.py`, every occurrence of the literal `UNKNOWN_NUMERIC` inside `_range_sentinel_checks`, `compile_coalesce_na_flag`, `_coalesce_range`, and `_coalesce_threshold` becomes `unknown_sentinel_for(dim.data_type)` (import from constants; assign once per method: `sentinel = unknown_sentinel_for(dim.data_type)`). Same in `accumulator_engine._create_anchor`'s RANGE branch. In `compiler.py`, confirm the RANGE/GT/LT/EXACT paths already select sentinels via `sentinels_for(dim.data_type)` after Task 3; temporal falls out.

- [ ] **Step 4: Run tests, then the ibis-duckdb backend regression**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestTemporalRange -v` → PASS

Append and run the backend regression (proleptic-minimum dates are an untested corner):

```python
class TestTemporalBackendRegression:
    def test_duckdb_backend_stores_and_compares_sentinel_dates(self):
        import ibis
        rules_pl = pl.DataFrame({
            "rule_name": ["r"],
            "eff_from": [datetime.date(2026, 1, 1)],
            "eff_to": [UNKNOWN_DATE],
        })
        con = ibis.duckdb.connect()
        rules_ibis = con.create_table("rules_tmp", rules_pl.to_pandas())
        engine = ExpressionRulesEngine(
            rules=rules_ibis, dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({"asof": datetime.date(2026, 7, 12)})
        assert result.count == 1
```

(Match the connection idiom used in `tests/test_accumulator_backends.py` — reuse its fixture/helper if one exists rather than this inline connect.)

Run: `hatch run test:test-target tests/test_dimension_serialization.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/ tests/test_dimension_serialization.py
git commit -m "feat: temporal (date/datetime) dimensions in filter and accumulator engines

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Per-row REGEX + CONTEXT_REGEX split

**Files:**
- Modify: `src/mountainash_rules/compiler.py`, `src/mountainash_rules/dimension.py` (regex validator block), `README.md`, `CLAUDE.md` (strategy tables)
- Test: `tests/test_compiler.py` (rename existing REGEX tests to CONTEXT_REGEX; add per-row REGEX class), `tests/test_dimension_serialization.py`

**Interfaces:**
- Produces: `MatchStrategy.REGEX` = per-row pattern in the rule column (sentinel → UNKNOWN); `MatchStrategy.CONTEXT_REGEX` = current literal-on-metadata behaviour. `regex_pattern` required for CONTEXT_REGEX, forbidden otherwise.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dimension_serialization.py`:

```python
class TestRegexSplit:
    def test_regex_forbids_regex_pattern(self):
        with pytest.raises(ValueError, match="context_regex"):
            Dimension(
                dimension_name="x", match_strategy=MatchStrategy.REGEX,
                regex_pattern="^A.*",
            )

    def test_context_regex_requires_pattern(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(dimension_name="x", match_strategy=MatchStrategy.CONTEXT_REGEX)

    def test_per_row_regex_matches_per_rule(self):
        from mountainash_rules.constants import UNKNOWN
        rules = pl.DataFrame({
            "rule_name": ["au", "nz", "any"],
            "x": ["^AU-", "^NZ-", UNKNOWN],
        })
        md = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="x", match_strategy=MatchStrategy.REGEX),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=md)
        result = engine.evaluate({"x": "AU-1234"})
        assert result.count == 2  # au (match) + any (sentinel wildcard)
        assert result.explain("au") == {"x": 1}
        assert result.explain("any") == {"x": 0}
```

In `tests/test_compiler.py`, rename the existing `REGEX` test class/usages to `CONTEXT_REGEX` (mechanical: the old behaviour is unchanged under the new name).

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestRegexSplit -v`
Expected: FAIL — REGEX still demands `regex_pattern`; CONTEXT_REGEX has no compile path.

- [ ] **Step 3: Implement**

`dimension.py` — replace the regex validator block:

```python
        if self.match_strategy == MatchStrategy.CONTEXT_REGEX:
            if not isinstance(self.regex_pattern, str) or not self.regex_pattern:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses context_regex and "
                    f"requires a non-empty literal 'regex_pattern'"
                )
        elif self.regex_pattern is not None:
            hint = (
                " (per-row regex reads patterns from the rule column; "
                "for a literal metadata pattern use context_regex)"
                if self.match_strategy == MatchStrategy.REGEX else ""
            )
            raise ValueError(
                f"Dimension '{self.dimension_name}' sets regex_pattern but "
                f"match_strategy is {self.match_strategy.value}{hint}"
            )
```

`compiler.py` — dispatch `CONTEXT_REGEX` to the old `_compile_regex` (rename it `_compile_context_regex`) and add the per-row path:

```python
            case MatchStrategy.REGEX:
                return self._compile_regex_per_row(dim)
            case MatchStrategy.CONTEXT_REGEX:
                return self._compile_context_regex(dim)
```

```python
    def _compile_regex_per_row(self, dim: Dimension) -> BaseExpressionAPI:
        """Per-row REGEX: the rule column holds the pattern for each rule.

        mountainash's regex_contains currently takes a literal pattern only,
        so first try the column form; fall back to a backend-native
        expression pending upstream support (same precedent as
        SET_MEMBERSHIP).
        """
        return self._compile_string_match(dim, "regex_contains")
```

**Implementation checkpoint:** run the Step 1 test. If mountainash rejects the column argument (TypeError/compile error), replace the body with the native fallback:

```python
    def _compile_regex_per_row(self, dim: Dimension) -> BaseExpressionAPI:
        import polars as pl
        rule_field = dim.resolved_rule_field
        ctx_name = CTX_PREFIX + dim.dimension_name
        rule_col = ma.col(rule_field)
        rule_is_sentinel = (
            rule_col.__eq__(ma.lit(UNKNOWN)) | rule_col.__eq__(ma.lit(NOT_SET))
        )
        match = ma.native(
            polars=pl.col(ctx_name).str.contains(pl.col(rule_field)),
        )
        return ma.when(rule_is_sentinel).then(0).when(match).then(1).otherwise(-1)
```

matching the exact `ma.native(...)` signature used for SET_MEMBERSHIP in this codebase's history/CLAUDE.md (check `git log -S "ma.native" -p` for the precedent); non-polars backends then raise mountainash's native-expression error — wrap construction so the message names REGEX and the backend, and file the upstream mountainash issue for column-valued `regex_contains` (note the issue URL in a code comment).

- [ ] **Step 4: Fix the docs tables**

In `README.md` and `CLAUDE.md`, update the match-strategy table: `REGEX` = "Rule column holds per-row pattern (search semantics)", add `CONTEXT_REGEX` = "Literal pattern on Dimension metadata; global context validator".

- [ ] **Step 5: Run tests**

Run: `hatch run test:test-target tests/test_dimension_serialization.py tests/test_compiler.py -v` → PASS
Run: `hatch run test:test-quick` → PASS

- [ ] **Step 6: Commit**

```bash
git add src/mountainash_rules/ tests/ README.md CLAUDE.md
git commit -m "feat!: split REGEX (per-row pattern) from CONTEXT_REGEX (metadata literal)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Bool dimensions with null don't-care

**Files:**
- Modify: `src/mountainash_rules/context.py`, `src/mountainash_rules/compiler.py` (`_compile_exact`, `_compile_not_equal`)
- Test: `tests/test_dimension_serialization.py`, `tests/test_context.py`

**Interfaces:**
- Consumes: `DataType.BOOL` (Task 2/3).
- Produces: BOOL dims valid for EXACT/NOT_EQUAL only; null rule or context value → ternary 0; `extract_context_values` preserves `None` for BOOL dims.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_dimension_serialization.py`)

```python
class TestBoolDimensions:
    def _md(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="active", data_type=DataType.BOOL),
        ])

    def test_bool_exact_match(self):
        rules = pl.DataFrame({"rule_name": ["on", "off", "any"],
                              "active": [True, False, None]})
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=self._md())
        result = engine.evaluate({"active": True})
        assert result.count == 2
        assert result.explain("on") == {"active": 1}
        assert result.explain("any") == {"active": 0}

    def test_missing_bool_context_preserved_as_null_wildcard(self):
        rules = pl.DataFrame({"rule_name": ["on"], "active": [True]})
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=self._md())
        result = engine.evaluate({})
        assert result.count == 1
        assert result.explain("on") == {"active": 0}
```

And in `tests/test_context.py`:

```python
def test_bool_dimension_missing_context_stays_none():
    from mountainash_rules.constants import DataType
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="active", data_type=DataType.BOOL),
    ])
    values = extract_context_values({}, ["active"], metadata=md)
    assert values["active"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `hatch run test:test-target tests/test_dimension_serialization.py::TestBoolDimensions tests/test_context.py::test_bool_dimension_missing_context_stays_none -v`
Expected: FAIL — context extraction substitutes the string `NOT_SET` for the missing bool (type clash or wrong ternary).

- [ ] **Step 3: Implement**

`context.py` — in the missing/None branch, before the general sentinel substitution:

```python
            if value is None:
                if dim is not None and dim.data_type is DataType.BOOL:
                    result[name] = None  # bool don't-care is null, not a sentinel
                elif dim is not None:
                    result[name] = not_set_sentinel_for(dim.data_type)
                else:
                    result[name] = NOT_SET
```

`compiler.py` — in `_compile_exact`/`_compile_not_equal`, add the BOOL branch. First check the free path: if `ma.t_col(col, unknown=set())` already yields 0 for nulls, no change is needed (run the Step 1 test to find out). Otherwise wrap explicitly:

```python
    def _compile_exact(self, dim: Dimension) -> BaseExpressionAPI:
        if dim.data_type is DataType.BOOL:
            rule_col = ma.col(dim.resolved_rule_field)
            ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
            either_null = rule_col.is_null().__or__(ctx_col.is_null())
            return (
                ma.when(either_null).then(0)
                .when(rule_col.__eq__(ctx_col)).then(1)
                .otherwise(-1)
            )
        sentinels = sentinels_for(dim.data_type)
        ...
```

(mirror for `_compile_not_equal` with `.__ne__`).

- [ ] **Step 4: Run tests**

Run: `hatch run test:test-target tests/test_dimension_serialization.py tests/test_context.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/context.py src/mountainash_rules/compiler.py tests/
git commit -m "feat: bool dimensions with null as don't-care

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Constrain valid_values

**Files:**
- Modify: `src/mountainash_rules/dimension.py`
- Test: `tests/test_dimension_serialization.py`

**Interfaces:**
- Produces: `valid_values: list[str | int | float | bool] = Field(default_factory=list)` — serialisable, non-shared default, engines still ignore it (documented).

- [ ] **Step 1: Write the failing tests**

```python
class TestValidValues:
    def test_default_is_not_shared(self):
        a = Dimension(dimension_name="a")
        b = Dimension(dimension_name="b")
        a.valid_values.append("X")
        assert b.valid_values == []

    def test_non_scalar_rejected(self):
        with pytest.raises(ValueError):
            Dimension(dimension_name="a", valid_values=[object()])

    def test_serialises(self):
        md = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="a", valid_values=["AU", "NZ"]),
        ])
        assert DimensionsMetadata.from_yaml(md.to_yaml()) == md
```

- [ ] **Step 2: Run to verify failure** — `test_default_is_not_shared` FAILS (mutable class default), `test_non_scalar_rejected` FAILS (accepts anything).

- [ ] **Step 3: Implement**

```python
from pydantic import Field

    valid_values: list[str | int | float | bool] = Field(
        default_factory=list,
        description=(
            "Declarative domain of context values for this dimension. "
            "Ignored by the engines; consumed by babel's coverage "
            "validation. Temporal domains are declared as ISO strings."
        ),
    )
```

- [ ] **Step 4: Run** `hatch run test:test-target tests/test_dimension_serialization.py -v` → PASS, then `hatch run test:test-quick` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/dimension.py tests/test_dimension_serialization.py
git commit -m "fix: valid_values gets default_factory and scalar constraint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Babel — explicit metadata on CSV import + DataType comparisons

**Files (in `../mountainash-rules-babel`):**
- Modify: `src/mountainash_rules_babel/importers/csv_.py`, `src/mountainash_rules_babel/exporters/dmn.py`
- Test: `tests/test_csv_importer.py` (or the existing importer test module — check `ls tests/`), `tests/test_dmn_exporter.py`

**Interfaces:**
- Consumes: `DimensionsMetadata.from_yaml_file` (Task 4), `DataType` (Task 2).
- Produces: `CsvImporter.import_lattice(path, *, metadata=None, ...)` accepting `DimensionsMetadata | str | Path`; `dmn.py` type checks via `DataType`.

- [ ] **Step 1: Write the failing tests** (in the babel repo's existing importer test module)

```python
def test_import_with_explicit_metadata_produces_range_dimension(tmp_path):
    import polars as pl
    from mountainash_rules.constants import DataType, MatchStrategy
    from mountainash_rules.dimension import Dimension, DimensionsMetadata
    from mountainash_rules_babel.importers.csv_ import CsvImporter

    csv = tmp_path / "rules.csv"
    pl.DataFrame({
        "rule_name": ["r1"], "amt_min": [0], "amt_max": [100],
    }).write_csv(csv)
    md = DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="amount", match_strategy=MatchStrategy.RANGE,
            data_type=DataType.INT,
            range_min_field="amt_min", range_max_field="amt_max",
        ),
    ])
    lattice = CsvImporter().import_lattice(csv, metadata=md)
    assert lattice.metadata.dimensions[0].match_strategy is MatchStrategy.RANGE


def test_import_with_metadata_missing_range_column_raises(tmp_path):
    import polars as pl
    from mountainash_rules.constants import DataType, MatchStrategy
    from mountainash_rules.dimension import Dimension, DimensionsMetadata
    from mountainash_rules_babel.importers.csv_ import CsvImporter

    csv = tmp_path / "rules.csv"
    pl.DataFrame({"rule_name": ["r1"], "amt_min": [0]}).write_csv(csv)
    md = DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="amount", match_strategy=MatchStrategy.RANGE,
            data_type=DataType.INT,
            range_min_field="amt_min", range_max_field="amt_max",
        ),
    ])
    with pytest.raises(ValueError, match="amt_max"):
        CsvImporter().import_lattice(csv, metadata=md)


def test_import_with_metadata_yaml_path(tmp_path):
    import polars as pl
    from mountainash_rules.constants import MatchStrategy
    from mountainash_rules.dimension import Dimension, DimensionsMetadata
    from mountainash_rules_babel.importers.csv_ import CsvImporter

    csv = tmp_path / "rules.csv"
    pl.DataFrame({"rule_name": ["r1"], "region": ["AU"]}).write_csv(csv)
    md = DimensionsMetadata(dimensions=[Dimension(dimension_name="region")])
    yaml_path = md.to_yaml_file(tmp_path / "md.yaml")
    lattice = CsvImporter().import_lattice(csv, metadata=yaml_path)
    assert lattice.metadata == md
```

- [ ] **Step 2: Run to verify failure**

Run (from babel repo): `hatch run test:test-target <importer test module> -v`
Expected: FAIL — `import_lattice` has no `metadata` parameter.

- [ ] **Step 3: Implement**

`importers/csv_.py`:

```python
import pathlib

from mountainash_rules.dimension import DimensionsMetadata


    def import_lattice(
        self,
        path: Path,
        *,
        metadata: "DimensionsMetadata | str | Path | None" = None,
        dimension_columns: list[str] | None = None,
        aggregate_columns: dict[str, str] | None = None,
        **options,
    ) -> Lattice:
        df = pl.read_csv(path)

        if metadata is not None:
            if isinstance(metadata, (str, pathlib.Path)):
                metadata = DimensionsMetadata.from_yaml_file(metadata)
            missing: list[str] = []
            for dim in metadata.dimensions:
                if dim.match_strategy == MatchStrategy.RANGE:
                    needed = [dim.range_min_field, dim.range_max_field]
                else:
                    needed = [dim.resolved_rule_field]
                missing.extend(c for c in needed if c not in df.columns)
            if missing:
                raise ValueError(
                    f"Metadata references columns missing from CSV: {missing}"
                )
            aggregates = [
                Aggregate(column_name=c, operation=op)
                for c, op in (aggregate_columns or {}).items()
            ]
            return Lattice(
                dataframe=df, metadata=metadata,
                aggregates=aggregates, partition_key=None,
            )

        # inference fallback: existing body unchanged below this line
```

`exporters/dmn.py`: replace `_type_ref(data_type: type)` and the `dtype == str` comparisons:

```python
from mountainash_rules.constants import DataType, MatchStrategy


def _type_ref(data_type: DataType) -> str:
    if data_type.is_numeric:
        return "number"
    if data_type is DataType.BOOL:
        return "boolean"
    if data_type.is_temporal:
        return "date"
    return "string"
```

and in `_feel_entry`, `dtype == str` → `dtype is DataType.STR` (both the EXACT and NOT_EQUAL branches).

- [ ] **Step 4: Run the babel suite**

Run (babel repo): `hatch run test:test-quick`
Expected: PASS.

- [ ] **Step 5: Commit (babel repo)**

```bash
git add src/mountainash_rules_babel/ tests/
git commit -m "feat: CSV import accepts explicit DimensionsMetadata; DataType-aware DMN typing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Final cross-repo verification

**Files:** none new.

- [ ] **Step 1:** rules repo: `hatch run test:test-quick` → PASS; `hatch run ruff:check` → clean or pre-existing-only; `hatch build` → wheel builds.
- [ ] **Step 2:** babel repo: `hatch run test:test-quick` → PASS.
- [ ] **Step 3:** `grep -rn "auto()" src/` in rules repo → no hits in enum definitions.
- [ ] **Step 4:** Push both repos to develop.
