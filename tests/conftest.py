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
]

# Note: narwhals-polars is intentionally excluded from LIST_CAPABLE_BACKENDS.
# narwhals (as of 2.19.0) types list.contains(item) as NonNestedLiteral and
# rejects expression arguments across all its native backends, so t_is_in
# against a list column cannot compile through the narwhals path.

# ---------------------------------------------------------------------------
# Per-test upstream xfails
# ---------------------------------------------------------------------------
# Surgical xfail markers for specific test × backend combinations that fail
# due to known upstream bugs.  strict=True so CI flags when upstream fixes land.
# Remove entries as upstream bugs are fixed.

_ISSUE_78_REASON = (
    "ibis-polars: missing WindowFunction translation for with_row_index "
    "— mountainash-io/mountainash#78"
)

# (backends, reason, test node substrings)
_UPSTREAM_XFAILS: list[tuple[set[str], str, list[str]]] = [
    # #78 — hits any test that reaches with_row_index in the engine pipeline.
    (
        {"ibis-polars"},
        _ISSUE_78_REASON,
        [
            # test_engine.py
            "TestSurvival::test_non_matching_rules_eliminated",
            "TestSurvival::test_matching_rules_survive",
            "TestSpecificity::test_specific_rule_ranks_first",
            "TestSpecificity::test_specificity_values",
            "TestRanking::test_rank_order",
            "TestEmptyResult::test_no_survivors",
            "TestTopN::test_top_n_limits_results",
            "TestTopN::test_top_n_larger_than_survivors",
            "TestMinSpecificity::test_min_specificity_filters",
            "TestDimensionsSubset::test_subset_dimensions",
            "TestObservability::test_observability_columns_present_by_default",
            "TestObservability::test_observability_columns_absent_when_disabled",
            "TestCustomExpressions::test_custom_expression_exact",
            # test_integration.py
            "TestPricingCarveOut::test_specific_override_wins",
            "TestPricingCarveOut::test_fallback_to_client_rate",
            "TestPricingCarveOut::test_fallback_to_base_rate",
            "TestPricingCarveOut::test_hierarchy_preserved_in_ranking",
            "TestEntityPool::test_most_specific_wins",
            "TestEntityPool::test_mid_tier_fallback",
            "TestEntityPool::test_no_match_when_regex_fails",
            "TestNoMatch::test_all_rules_eliminated",
            "TestTieHandling::test_same_specificity_both_survive",
            "TestTieHandling::test_equal_specificity_both_returned",
            "TestExplainIntegration::test_explain_shows_dimension_breakdown",
            "TestMixedStrategyFraudDetection::test_high_value_review",
            "TestMixedStrategyFraudDetection::test_blacklist_merchant_blocks",
            "TestMixedStrategyFraudDetection::test_specific_txn_most_specific",
            # test_batch_evaluation.py
            "TestBatchBackendSweep::test_batch_agrees_with_single_context_evaluation",
            # test_accumulator_backends.py
            "TestApplyCrossBackend::test_apply_correct_count",
            "TestApplyCrossBackend::test_apply_accumulated_margin",
            "TestApplyCrossBackend::test_apply_partial_match",
        ],
    ),
]


def pytest_collection_modifyitems(config, items):
    """Mark specific test × backend combinations as strict xfail."""
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is None:
            continue
        backend = None
        for param_name in ("backend_name", "list_backend_name", "list_backend", "apply_backend"):
            backend = callspec.params.get(param_name)
            if backend is not None:
                break
        if backend is None:
            continue
        for backends, reason, patterns in _UPSTREAM_XFAILS:
            if backend not in backends:
                continue
            if any(p in item.nodeid for p in patterns):
                item.add_marker(
                    pytest.mark.xfail(strict=True, reason=reason)
                )
                break


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
        Dimension(
            dimension_name="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            data_type=str,
            regex_pattern="^PRE.*",
        ),
    ])


@pytest.fixture
def basic_engine(backend_rules_df, basic_metadata) -> ExpressionRulesEngine:
    return ExpressionRulesEngine(rules=backend_rules_df, dimension_metadata=basic_metadata)


@pytest.fixture
def valid_context() -> TestContext:
    return TestContext(region="AU", amount=50, code="PRE-001")
