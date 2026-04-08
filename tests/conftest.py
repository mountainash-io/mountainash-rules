"""Shared fixtures for expression-based rules engine tests.

Mirrors the mountainash-expressions exemplar: data-as-dict fixtures + a
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

from mountainash_utils_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine


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
    "mountainash-io/mountainash-expressions#75 (t_list_contains)"
)

# Backends with known upstream bugs that break the engine pipeline.
# Tests on these backends are xfail'd non-strictly — tests that happen to
# avoid the bug path still pass; tests that hit it xfail without failing CI.
# Remove entries as upstream bugs are fixed.
UPSTREAM_BROKEN_BACKENDS: dict[str, str] = {
    "pandas": (
        "narwhals DuplicateError: ma.lit().alias() emits duplicate 'literal' "
        "columns on narwhals-pandas path — upstream mountainash bug"
    ),
    "narwhals-pandas": (
        "narwhals DuplicateError: ma.lit().alias() emits duplicate 'literal' "
        "columns on narwhals-pandas path — upstream mountainash bug"
    ),
    "ibis-polars": (
        "ibis polars backend missing WindowFunction translation "
        "(with_row_index) — upstream ibis bug"
    ),
}


def pytest_collection_modifyitems(config, items):
    """Mark tests on known-broken backends as non-strict xfail."""
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is None:
            continue
        for param_name in ("backend_name", "list_backend_name"):
            backend = callspec.params.get(param_name)
            if backend in UPSTREAM_BROKEN_BACKENDS:
                item.add_marker(
                    pytest.mark.xfail(
                        strict=False,
                        reason=UPSTREAM_BROKEN_BACKENDS[backend],
                    )
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
            match_strategy=MatchStrategy.REGEX,
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
