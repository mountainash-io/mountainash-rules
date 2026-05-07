# Cross-Backend Test Parameterisation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parametrize the `mountainash-utils-rules` test suite across all 7 mountainash-supported DataFrame backends, replacing Polars-specific assertions with backend-agnostic reads via `mountainash.relations`.

**Architecture:** Rewrite `tests/conftest.py` to mirror the `mountainash` exemplar: pure-Python data fixtures, a `backend_name` param fixture over 7 backends, backend DataFrame factory fixtures, and a `basic_engine` that auto-parametrizes transitively. Test files replace direct Polars assertions with `mountainash.relations.relation(...).to_dict()`. SET_MEMBERSHIP / SET_EXCLUSION cases use strict `xfail` on non-Polars backends pointing at `mountainash-io/mountainash#75`.

**Tech Stack:** pytest, polars, pandas, narwhals, ibis-framework[duckdb,polars,sqlite], mountainash.relations, mountainash.expressions.

**Spec:** `docs/superpowers/specs/2026-04-08-cross-backend-test-parameterisation-design.md`

---

## File Map

| File | Responsibility |
|---|---|
| `tests/conftest.py` | Backend constants, `backend_name` / `list_backend_name` param fixtures, data dict fixtures, backend DataFrame factory, `basic_metadata`, `basic_engine`, `valid_context`, `_xfail_set_on_non_polars` helper |
| `tests/test_engine.py` | Replace local `rules_df` / `metadata` / `engine` fixtures with conftest's `basic_engine`; replace Polars assertions with `relation(...).to_dict()` reads |
| `tests/test_result.py` | Replace Polars `sample_result_df` fixture with `backend_name`-parametrized equivalent; replace `.shape[0]` / `["col"][0]` with `relation(...).to_dict()` reads |
| `tests/test_integration.py` | Convert inline `pl.DataFrame` constructions to `backend_rules_df`-style fixtures built via conftest helper; replace assertions; add xfail marker to `TestMixedStrategyFraudDetection` on non-Polars backends |
| `tests/test_compiler.py` | Extend `TestBackendAgnosticism` parametrize list from 2 → 7 backends; add SET cases with xfail markers |

Untouched: `tests/test_dimension.py`, `tests/test_context.py`, `tests/test_backend_purity.py`.

---

## Task 1: Conftest backend fixtures

**Files:**
- Modify: `tests/conftest.py` (full rewrite)

- [ ] **Step 1: Replace conftest.py contents**

Write to `tests/conftest.py`:

```python
"""Shared fixtures for expression-based rules engine tests.

Mirrors the mountainash exemplar: data-as-dict fixtures + a
`backend_name` param fixture + per-backend DataFrame factory fixtures that
auto-parametrize every dependent test across all 7 supported backends.
"""

from __future__ import annotations

from typing import Any

import ibis
import narwhals as nw
import pandas as pd
import polars as pl
import pytest
from pydantic import BaseModel

from mountainash_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine


# ---------------------------------------------------------------------------
# Backend constants
# ---------------------------------------------------------------------------

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

SET_MEMBERSHIP_XFAIL_REASON = (
    "SET_MEMBERSHIP uses Polars-native workaround pending "
    "mountainash-io/mountainash#75 (t_list_contains)"
)


# ---------------------------------------------------------------------------
# Backend DataFrame construction
# ---------------------------------------------------------------------------

def build_backend_df(backend: str, data: dict, table_name: str = "t") -> Any:
    """Dispatch a data dict into the requested backend's DataFrame type."""
    if backend == "polars":
        return pl.DataFrame(data)
    if backend == "pandas":
        return pd.DataFrame(data)
    if backend == "narwhals-polars":
        return nw.from_native(pl.DataFrame(data))
    if backend == "narwhals-pandas":
        return nw.from_native(pd.DataFrame(data), eager_only=True)
    if backend == "ibis-duckdb":
        conn = ibis.duckdb.connect()
        return conn.create_table(table_name, data, overwrite=True)
    if backend == "ibis-polars":
        conn = ibis.polars.connect()
        return conn.create_table(table_name, pl.DataFrame(data), overwrite=True)
    if backend == "ibis-sqlite":
        conn = ibis.sqlite.connect(":memory:")
        return conn.create_table(table_name, data, overwrite=True)
    raise ValueError(f"Unknown backend: {backend}")


# ---------------------------------------------------------------------------
# Backend param fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(params=ALL_BACKENDS)
def backend_name(request) -> str:
    return request.param


@pytest.fixture(params=LIST_CAPABLE_BACKENDS)
def list_backend_name(request) -> str:
    return request.param


# ---------------------------------------------------------------------------
# Context model + data dicts
# ---------------------------------------------------------------------------

class TestContext(BaseModel):
    region: str
    amount: int
    code: str


@pytest.fixture
def rules_data() -> dict[str, list]:
    """Standard 3-dimension rules as plain Python."""
    return {
        "rule_name": ["specific", "general", "mid", "no_match"],
        "region":     ["AU", UNKNOWN, "AU", "US"],
        "amount_min": [0, UNKNOWN_NUMERIC, 0, 0],
        "amount_max": [100, UNKNOWN_NUMERIC, 100, 100],
        "code":       ["^PRE.*", UNKNOWN, UNKNOWN, "^PRE.*"],
    }


# ---------------------------------------------------------------------------
# Backend DataFrame fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def backend_rules_df(backend_name: str, rules_data: dict) -> Any:
    return build_backend_df(backend_name, rules_data, table_name="rules")


# ---------------------------------------------------------------------------
# Metadata + engine
# ---------------------------------------------------------------------------

@pytest.fixture
def basic_metadata() -> DimensionsMetadata:
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
def basic_engine(backend_rules_df, basic_metadata) -> ExpressionRulesEngine:
    return ExpressionRulesEngine(rules=backend_rules_df, dimension_metadata=basic_metadata)


@pytest.fixture
def valid_context() -> TestContext:
    return TestContext(region="AU", amount=50, code="PRE-001")
```

- [ ] **Step 2: Run the suite collection to confirm fixtures load**

Run: `hatch run test:test-quick tests/test_dimension.py -q`
Expected: test_dimension tests still pass (they don't depend on these fixtures, so this confirms the conftest parses cleanly).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: rewrite conftest for cross-backend parameterisation

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Migrate test_engine.py to backend-agnostic reads

**Files:**
- Modify: `tests/test_engine.py` (full rewrite)

- [ ] **Step 1: Replace test_engine.py contents**

Write to `tests/test_engine.py`:

```python
"""Tests for ExpressionRulesEngine — parametrized across all backends."""

from __future__ import annotations

import mountainash.expressions as ma
import pytest
from mountainash.relations import relation

from mountainash_rules.constants import CTX_PREFIX, UNKNOWN, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine

from tests.conftest import build_backend_df


def _rows(df) -> dict:
    """Backend-agnostic read — returns column -> list[values]."""
    return relation(df).to_dict()


# ---------------------------------------------------------------------------
# Survival + matching
# ---------------------------------------------------------------------------

class TestSurvival:
    def test_non_matching_rules_eliminated(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = _rows(result.survivors)["rule_name"]
        assert "no_match" not in names

    def test_matching_rules_survive(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        names = _rows(result.survivors)["rule_name"]
        assert "specific" in names
        assert "general" in names
        assert "mid" in names


# ---------------------------------------------------------------------------
# Specificity
# ---------------------------------------------------------------------------

class TestSpecificity:
    def test_specific_rule_ranks_first(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "specific"

    def test_specificity_values(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        rows = _rows(result.survivors)
        name_to_spec = dict(zip(rows["rule_name"], rows["__specificity"]))
        assert name_to_spec["specific"] == 3
        assert name_to_spec["general"] == 0
        assert name_to_spec["mid"] == 2


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def test_rank_order(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        rows = _rows(result.survivors)
        pairs = sorted(zip(rows["__rank"], rows["rule_name"]))
        names_in_order = [name for _, name in pairs]
        assert names_in_order == ["specific", "mid", "general"]


# ---------------------------------------------------------------------------
# Empty result
# ---------------------------------------------------------------------------

class TestEmptyResult:
    def test_no_survivors(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["only_us"],
            "region": ["US"],
        }, table_name="empty_rules")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 0


# ---------------------------------------------------------------------------
# top_n / min_specificity / subset
# ---------------------------------------------------------------------------

class TestTopN:
    def test_top_n_limits_results(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=2,
        )
        assert result.count == 2
        rows = _rows(result.survivors)
        pairs = sorted(zip(rows["__rank"], rows["rule_name"]))
        assert pairs[0][1] == "specific"

    def test_top_n_larger_than_survivors(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            top_n=100,
        )
        assert result.count == 3


class TestMinSpecificity:
    def test_min_specificity_filters(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            min_specificity=2,
        )
        names = _rows(result.survivors)["rule_name"]
        assert "specific" in names
        assert "mid" in names
        assert "general" not in names


class TestDimensionsSubset:
    def test_subset_dimensions(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            dimensions=["region"],
        )
        assert result.count == 3
        assert "no_match" not in _rows(result.survivors)["rule_name"]

    def test_invalid_dimension_raises(self, basic_engine):
        with pytest.raises(KeyError, match="nonexistent"):
            basic_engine.evaluate(
                context={"region": "AU"},
                dimensions=["nonexistent"],
            )


# ---------------------------------------------------------------------------
# Observability toggle
# ---------------------------------------------------------------------------

class TestObservability:
    def test_observability_columns_present_by_default(self, basic_engine):
        result = basic_engine.evaluate(context={"region": "AU", "amount": 50, "code": "PRE-001"})
        cols = set(_rows(result.survivors).keys())
        assert "__t_region" in cols
        assert "__t_amount" in cols
        assert "__t_code" in cols

    def test_observability_columns_absent_when_disabled(self, basic_engine):
        result = basic_engine.evaluate(
            context={"region": "AU", "amount": 50, "code": "PRE-001"},
            include_observability=False,
        )
        cols = set(_rows(result.survivors).keys())
        assert "__t_region" not in cols
        assert "__t_amount" not in cols
        assert "__t_code" not in cols
        assert "__specificity" in cols
        assert "__rank" in cols


# ---------------------------------------------------------------------------
# Custom expressions
# ---------------------------------------------------------------------------

class TestCustomExpressions:
    def test_custom_expression_exact(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["r1", "r2"],
            "region": ["AU", "US"],
        }, table_name="custom_rules")

        engine = ExpressionRulesEngine(
            rules=rules,
            dimension_expressions={
                "region": ma.t_col("region", unknown={UNKNOWN}).t_eq(
                    ma.t_col(f"{CTX_PREFIX}region", unknown={UNKNOWN})
                ),
            },
        )

        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 1
        assert _rows(result.best_match)["rule_name"][0] == "r1"

    def test_cannot_provide_both_metadata_and_expressions(self, backend_name):
        rules = build_backend_df(backend_name, {"rule_name": ["r1"]}, table_name="two_ways")
        with pytest.raises(ValueError, match="not both"):
            ExpressionRulesEngine(
                rules=rules,
                dimension_metadata=DimensionsMetadata(dimensions=[
                    Dimension(dimension_name="x", match_strategy=MatchStrategy.EXACT, data_type=str),
                ]),
                dimension_expressions={"x": ma.col("x")},
            )

    def test_must_provide_one_of_metadata_or_expressions(self, backend_name):
        rules = build_backend_df(backend_name, {"rule_name": ["r1"]}, table_name="neither")
        with pytest.raises(ValueError, match="Must provide"):
            ExpressionRulesEngine(rules=rules)
```

- [ ] **Step 2: Run test_engine.py against all backends**

Run: `hatch run test:test-quick tests/test_engine.py -q`
Expected: PASS for all backends. Count should be ~16 tests × 7 backends ≈ 112 runs (some tests not using `basic_engine` still param via `backend_name`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine.py
git commit -m "test(engine): parametrize across all 7 backends

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Migrate test_result.py to backend-agnostic reads

**Files:**
- Modify: `tests/test_result.py` (full rewrite)

- [ ] **Step 1: Replace test_result.py contents**

Write to `tests/test_result.py`:

```python
"""Tests for RuleResult — parametrized across all backends."""

from __future__ import annotations

import pytest
from mountainash.relations import relation

from mountainash_rules.result import RuleResult

from tests.conftest import build_backend_df


@pytest.fixture
def sample_result_data() -> dict[str, list]:
    return {
        "rule_name": ["specific", "general", "mid"],
        "rate": [0.05, 0.10, 0.07],
        "__t_region": [1, 0, 1],
        "__t_product": [1, 0, 0],
        "__t_tier": [1, 1, 1],
        "__specificity": [3, 1, 2],
        "__rank": [1, 3, 2],
    }


@pytest.fixture
def result(backend_name, sample_result_data) -> RuleResult:
    df = build_backend_df(backend_name, sample_result_data, table_name="result")
    return RuleResult(dataframe=df, active_dimensions=["region", "product", "tier"])


def _rows(df) -> dict:
    return relation(df).to_dict()


class TestSurvivors:
    def test_survivors_returns_all_rows(self, result):
        assert result.count == 3

    def test_survivors_has_expected_shape(self, result):
        rows = _rows(result.survivors)
        assert len(rows["rule_name"]) == 3


class TestBestMatch:
    def test_best_match_returns_first_row(self, result):
        best = _rows(result.best_match)
        assert len(best["rule_name"]) == 1
        assert best["rule_name"][0] == "specific"
        assert best["__specificity"][0] == 3


class TestExplain:
    def test_explain_returns_per_dimension_values(self, result):
        assert result.explain("specific") == {"region": 1, "product": 1, "tier": 1}

    def test_explain_general_rule(self, result):
        assert result.explain("general") == {"region": 0, "product": 0, "tier": 1}

    def test_explain_missing_rule_raises(self, result):
        with pytest.raises(KeyError):
            result.explain("nonexistent")


class TestAtLeast:
    def test_at_least_filters_by_specificity(self, result):
        filtered = _rows(result.at_least(2))
        assert len(filtered["rule_name"]) == 2
        assert set(filtered["rule_name"]) == {"specific", "mid"}

    def test_at_least_zero_returns_all(self, result):
        assert len(_rows(result.at_least(0))["rule_name"]) == 3

    def test_at_least_high_returns_none(self, result):
        assert len(_rows(result.at_least(10)).get("rule_name", [])) == 0
```

- [ ] **Step 2: Run test_result.py against all backends**

Run: `hatch run test:test-quick tests/test_result.py -q`
Expected: PASS. ~9 tests × 7 backends ≈ 63 runs.

- [ ] **Step 3: Commit**

```bash
git add tests/test_result.py
git commit -m "test(result): parametrize across all 7 backends

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Migrate test_integration.py (non-SET classes)

**Files:**
- Modify: `tests/test_integration.py` — rewrite `TestPricingCarveOut`, `TestEntityPool`, `TestNoMatch`, `TestTieHandling`, `TestExplainIntegration`

- [ ] **Step 1: Rewrite the non-SET classes**

Replace the file contents up to (but not including) `TestMixedStrategyFraudDetection` with the following. Keep `TestMixedStrategyFraudDetection` for Task 5.

```python
"""Integration tests: end-to-end scenarios with real-world rule patterns."""

from __future__ import annotations

import pytest
from mountainash.relations import relation

from mountainash_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine

from tests.conftest import (
    LIST_CAPABLE_BACKENDS,
    SET_MEMBERSHIP_XFAIL_REASON,
    build_backend_df,
)


def _rows(df) -> dict:
    return relation(df).to_dict()


# ---------------------------------------------------------------------------
# Pricing carve-out
# ---------------------------------------------------------------------------

class TestPricingCarveOut:
    """Pricing hierarchy: general rate -> client-specific -> product-specific override."""

    @pytest.fixture
    def pricing_engine(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["base_rate", "client_au", "client_au_premium"],
            "rate": [0.10, 0.08, 0.05],
            "client_region": [UNKNOWN, "AU", "AU"],
            "product": [UNKNOWN, UNKNOWN, "premium"],
        }, table_name="pricing")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="client_region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)

    def test_specific_override_wins(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "client_au_premium"
        assert best["rate"][0] == 0.05

    def test_fallback_to_client_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "standard"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "client_au"
        assert best["rate"][0] == 0.08

    def test_fallback_to_base_rate(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "UK", "product": "standard"})
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "base_rate"
        assert best["rate"][0] == 0.10

    def test_hierarchy_preserved_in_ranking(self, pricing_engine):
        result = pricing_engine.evaluate(context={"client_region": "AU", "product": "premium"})
        rows = _rows(result.survivors)
        pairs = sorted(zip(rows["__rank"], rows["rule_name"]))
        assert [name for _, name in pairs] == ["client_au_premium", "client_au", "base_rate"]


# ---------------------------------------------------------------------------
# Entity pool
# ---------------------------------------------------------------------------

class TestEntityPool:
    """Entity pool with range-based and regex rules for increasing specificity."""

    @pytest.fixture
    def pool_engine(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["catch_all", "mid_tier", "high_value_au"],
            "pool": ["default", "tier_b", "tier_a"],
            "region": [UNKNOWN, UNKNOWN, "AU"],
            "value_min": [UNKNOWN_NUMERIC, 1000, 5000],
            "value_max": [UNKNOWN_NUMERIC, 9999, 99999],
            "code_pattern": [UNKNOWN, "^T.*", "^T.*"],
        }, table_name="pool")
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
        return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)

    def test_most_specific_wins(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "AU", "value": 7500, "code_pattern": "TXN-001"})
        assert _rows(result.best_match)["rule_name"][0] == "high_value_au"

    def test_mid_tier_fallback(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "UK", "value": 5000, "code_pattern": "TXN-001"})
        assert _rows(result.best_match)["rule_name"][0] == "mid_tier"

    def test_catch_all_fallback(self, pool_engine):
        result = pool_engine.evaluate(context={"region": "UK", "value": 500, "code_pattern": "ABC-001"})
        assert _rows(result.best_match)["rule_name"][0] == "catch_all"


# ---------------------------------------------------------------------------
# No match
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_all_rules_eliminated(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["au_only", "us_only"],
            "region": ["AU", "US"],
        }, table_name="no_match_rules")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "UK"})
        assert result.count == 0


# ---------------------------------------------------------------------------
# Tie handling
# ---------------------------------------------------------------------------

class TestTieHandling:
    def test_same_specificity_both_survive(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
            "product": ["premium", "standard"],
        }, table_name="tie_rules_a")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})
        assert result.count == 1
        assert _rows(result.best_match)["rule_name"][0] == "rule_a"

    def test_equal_specificity_both_returned(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["rule_a", "rule_b"],
            "region": ["AU", "AU"],
        }, table_name="tie_rules_b")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU"})
        assert result.count == 2


# ---------------------------------------------------------------------------
# Explain integration
# ---------------------------------------------------------------------------

class TestExplainIntegration:
    def test_explain_shows_dimension_breakdown(self, backend_name):
        rules = build_backend_df(backend_name, {
            "rule_name": ["specific", "general"],
            "region": ["AU", UNKNOWN],
            "product": ["premium", UNKNOWN],
        }, table_name="explain_rules")
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT, data_type=str),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
        result = engine.evaluate(context={"region": "AU", "product": "premium"})

        assert result.explain("specific") == {"region": 1, "product": 1}
        assert result.explain("general") == {"region": 0, "product": 0}
```

Leave `TestMixedStrategyFraudDetection` at the bottom of the file for now, but verify the top-of-file imports match the new import block above (polars import removed).

- [ ] **Step 2: Run the rewritten classes**

Run: `hatch run test:test-quick tests/test_integration.py -q -k "not MixedStrategy"`
Expected: PASS across all 7 backends. ~11 tests × 7 backends ≈ 77 runs.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(integration): parametrize non-SET classes across backends

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Handle TestMixedStrategyFraudDetection with SET xfail

**Files:**
- Modify: `tests/test_integration.py` — replace `TestMixedStrategyFraudDetection` class

- [ ] **Step 1: Replace the class**

Append (replacing the old `TestMixedStrategyFraudDetection`) at the bottom of `tests/test_integration.py`:

```python
# ---------------------------------------------------------------------------
# Mixed strategy (includes SET_MEMBERSHIP) — requires list-capable backends
# ---------------------------------------------------------------------------

def _fraud_rules_data() -> dict:
    return {
        "rule_name": ["catch_all", "high_value", "blacklist_merchant", "specific_txn"],
        "action": ["allow", "review", "block", "block"],
        "merchant_type": [UNKNOWN, UNKNOWN, "CASINO", "RETAIL"],
        "allowed_countries": [
            ["AU", "NZ", "US", "UK"],
            ["AU", "NZ", "US", "UK"],
            ["AU", "NZ", "US", "UK"],
            ["AU"],
        ],
        "amount_threshold": [UNKNOWN_NUMERIC, 10000, UNKNOWN_NUMERIC, 500],
        "code_prefix": [UNKNOWN, UNKNOWN, UNKNOWN, "TXN-"],
    }


def _fraud_metadata() -> DimensionsMetadata:
    return DimensionsMetadata(dimensions=[
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


@pytest.mark.parametrize(
    "list_backend",
    [
        pytest.param(
            backend,
            marks=(
                []
                if backend == "polars"
                else [pytest.mark.xfail(strict=True, reason=SET_MEMBERSHIP_XFAIL_REASON)]
            ),
        )
        for backend in LIST_CAPABLE_BACKENDS
    ],
)
class TestMixedStrategyFraudDetection:
    """Exercises EXACT, SET_MEMBERSHIP, GREATER_THAN, and PREFIX together.

    SET_MEMBERSHIP uses a Polars-native workaround (ma.native) pending
    mountainash-io/mountainash#75. Non-Polars backends are
    strict xfail — when #75 lands and the workaround is removed, these
    flip XPASS and force removal of the markers.
    """

    @pytest.fixture
    def fraud_engine(self, list_backend):
        rules = build_backend_df(list_backend, _fraud_rules_data(), table_name="fraud_rules")
        return ExpressionRulesEngine(rules=rules, dimension_metadata=_fraud_metadata())

    def test_high_value_review(self, fraud_engine):
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "US",
            "amount": 15000,
            "code": "TXN-999",
        })
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "high_value"
        assert best["action"][0] == "review"

    def test_blacklist_merchant_blocks(self, fraud_engine):
        result = fraud_engine.evaluate(context={
            "merchant_type": "CASINO",
            "country": "AU",
            "amount": 100,
            "code": "TXN-001",
        })
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "blacklist_merchant"
        assert best["action"][0] == "block"

    def test_specific_txn_most_specific(self, fraud_engine):
        result = fraud_engine.evaluate(context={
            "merchant_type": "RETAIL",
            "country": "AU",
            "amount": 1000,
            "code": "TXN-001",
        })
        best = _rows(result.best_match)
        assert best["rule_name"][0] == "specific_txn"
        assert best["__specificity"][0] == 4
```

- [ ] **Step 2: Run the class**

Run: `hatch run test:test-quick tests/test_integration.py::TestMixedStrategyFraudDetection -v`
Expected: Polars → 3 PASS. Each of the other 3 `LIST_CAPABLE_BACKENDS` → 3 XFAIL each. No failures, no xpasses.

- [ ] **Step 3: Full integration file run**

Run: `hatch run test:test-quick tests/test_integration.py -q`
Expected: All non-SET tests pass × 7 backends; SET fraud tests are 3 PASS + 9 XFAIL.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(integration): parametrize fraud detection with xfail for non-polars SET

Refs mountainash-io/mountainash#75

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Extend test_compiler.py TestBackendAgnosticism to 7 backends

**Files:**
- Modify: `tests/test_compiler.py` — replace the `TestBackendAgnosticism` class only (lines 580–628). Leave all other test classes untouched.

- [ ] **Step 1: Replace the class**

At the top of `tests/test_compiler.py`, add a new import line alongside the existing imports:

```python
from tests.conftest import (
    ALL_BACKENDS,
    SET_MEMBERSHIP_XFAIL_REASON,
    build_backend_df,
)
```

Then replace the `TestBackendAgnosticism` class with:

```python
class TestBackendAgnosticism:
    """Smoke test: each strategy compiles and runs on every supported backend.

    SET_MEMBERSHIP / SET_EXCLUSION are included but xfail-strict on non-Polars
    backends, pending mountainash-io/mountainash#75.
    """

    _SAMPLE_DATA = {
        "str_col": ["A", "B"],
        "num_col": [10, 20],
        "min_col": [0, 0],
        "max_col": [100, 100],
        "list_col": [["A", "B"], ["C", "D"]],
        f"{CTX_PREFIX}str_col": ["A", "A"],
        f"{CTX_PREFIX}num_col": [15, 15],
        f"{CTX_PREFIX}list_col": ["A", "A"],
    }

    _NON_LIST_DATA = {k: v for k, v in _SAMPLE_DATA.items() if k != "list_col"}

    @pytest.mark.parametrize("backend_name", ALL_BACKENDS)
    @pytest.mark.parametrize("strategy,field,data_type,extras", [
        (MatchStrategy.EXACT, "str_col", str, {}),
        (MatchStrategy.NOT_EQUAL, "str_col", str, {}),
        (MatchStrategy.RANGE, "num_col", int, {"range_min_field": "min_col", "range_max_field": "max_col"}),
        (MatchStrategy.GREATER_THAN, "num_col", int, {}),
        (MatchStrategy.LESS_THAN, "num_col", int, {}),
        (MatchStrategy.PREFIX, "str_col", str, {}),
        (MatchStrategy.SUFFIX, "str_col", str, {}),
        (MatchStrategy.CONTAINS, "str_col", str, {}),
        (MatchStrategy.REGEX, "str_col", str, {}),
    ])
    def test_non_set_strategy_compiles_on_backend(
        self, compiler, backend_name, strategy, field, data_type, extras,
    ):
        dim = Dimension(
            dimension_name=field,
            match_strategy=strategy,
            data_type=data_type,
            **extras,
        )
        expr = compiler.compile_dimension(dim)
        df = build_backend_df(backend_name, self._NON_LIST_DATA, table_name="smoke")
        compiled = expr.compile(df, booleanizer=None)
        assert compiled is not None

    @pytest.mark.parametrize(
        "backend_name",
        [
            pytest.param(
                backend,
                marks=(
                    []
                    if backend == "polars"
                    else [pytest.mark.xfail(strict=True, reason=SET_MEMBERSHIP_XFAIL_REASON)]
                ),
            )
            for backend in ALL_BACKENDS
        ],
    )
    @pytest.mark.parametrize("strategy", [
        MatchStrategy.SET_MEMBERSHIP,
        MatchStrategy.SET_EXCLUSION,
    ])
    def test_set_strategy_compiles_on_backend(
        self, compiler, backend_name, strategy,
    ):
        dim = Dimension(
            dimension_name="list_col",
            match_strategy=strategy,
            data_type=str,
        )
        expr = compiler.compile_dimension(dim)
        df = build_backend_df(backend_name, self._SAMPLE_DATA, table_name="set_smoke")
        compiled = expr.compile(df, booleanizer=None)
        assert compiled is not None
```

- [ ] **Step 2: Run the compiler test file**

Run: `hatch run test:test-quick tests/test_compiler.py -q`
Expected: all pre-existing per-strategy tests still pass (they were not touched); `TestBackendAgnosticism::test_non_set_strategy_compiles_on_backend` runs 9 strategies × 7 backends = 63 cases; `test_set_strategy_compiles_on_backend` runs 2 strategies × 7 backends = 14 cases (2 pass, 12 xfail).

- [ ] **Step 3: Commit**

```bash
git add tests/test_compiler.py
git commit -m "test(compiler): extend backend agnosticism smoke tests to 7 backends

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Full suite verification and cleanup

**Files:**
- Verify: entire `tests/` tree

- [ ] **Step 1: Run full suite**

Run: `hatch run test:test-quick -q`
Expected: All tests pass, xfails exactly match the SET tests on non-Polars backends. Rough counts:
- `test_backend_purity.py`: 3 pass
- `test_dimension.py`: unchanged count pass
- `test_context.py`: unchanged count pass
- `test_compiler.py`: existing per-strategy tests unchanged; backend agnosticism ~63 pass + 2 pass + 12 xfail
- `test_engine.py`: ~16 × 7 = ~112 pass
- `test_result.py`: ~9 × 7 = ~63 pass
- `test_integration.py`: ~11 × 7 non-SET pass + 3 SET pass + 9 SET xfail

Total: ~700+ collected, 0 failures, 0 xpasses.

- [ ] **Step 2: Confirm no stray polars imports in tests that should be agnostic**

Run: `grep -n "^import polars\|^from polars" tests/test_engine.py tests/test_result.py tests/test_integration.py`
Expected: no output (test_compiler.py is allowed to retain its `import polars as pl` for the per-strategy Polars-specific tests that were not migrated).

- [ ] **Step 3: Confirm xfail count matches expectations**

Run: `hatch run test:test-quick -q -rx 2>&1 | tail -40`
Expected: Report shows 21 XFAIL entries (9 compiler SET + 9 integration fraud + 3 extra from SET_EXCLUSION × non-polars = verify the exact count matches the fixture math; it should be `(2 set strategies × 6 non-polars) + (3 fraud tests × 3 non-polars list backends) = 12 + 9 = 21`).

If the count differs, read the xfail list and reconcile against the parametrize definitions in Tasks 5 and 6. Do not weaken markers — fix miscounts in the plan's expectations only if the fixtures are correct.

- [ ] **Step 4: Commit any tidying**

If steps 1–3 passed with no changes, skip this commit. Otherwise:

```bash
git add -u tests/
git commit -m "test: reconcile cross-backend parameterisation

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Push branch**

Run: `git push`
Expected: Push succeeds to existing feature branch.

---

## Self-review

**Spec coverage:**
- 7-backend fixture + data dict pattern → Task 1 ✓
- `list_capable_backends` narrower param set → Task 1 (conftest constant) + Task 5 uses it for fraud class ✓
- `relation(...).to_dict()` extraction (no new API) → Tasks 2–5 use `_rows` helper ✓
- Transitive `basic_engine` parametrization → Task 1 + Task 2 ✓
- Strict xfail on non-polars SET → Tasks 5 and 6 ✓
- Backend purity test unchanged → Task 7 step 1 confirms ✓
- `test_dimension.py`, `test_context.py` untouched → not in any task ✓
- No source changes under `src/` → no task modifies `src/` ✓

**Placeholder scan:** No TBDs, no "handle edge cases", no "similar to Task N" references.

**Type consistency:** `build_backend_df` signature `(backend, data, table_name)` consistent across all call sites. `_rows` helper identical in Tasks 2, 3, 4, 5. `ALL_BACKENDS`, `LIST_CAPABLE_BACKENDS`, `SET_MEMBERSHIP_XFAIL_REASON` defined once in conftest and imported consistently.
