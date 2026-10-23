"""Shared fixtures for expression-based rules engine tests.

Data-as-dict fixtures + a `backend_name` param fixture + per-backend
DataFrame factory fixtures that auto-parametrize across the two supported
backends (polars, ibis-duckdb), selected by `--backends`.
"""

from __future__ import annotations

from typing import Any

import ibis
import polars as pl
import pytest
from pydantic import BaseModel

from mountainash_rules import ContextField
from mountainash_rules.core.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine




# ---------------------------------------------------------------------------
# Backend constants
# ---------------------------------------------------------------------------

ALL_BACKENDS = ["polars", "ibis-duckdb"]
LIST_CAPABLE_BACKENDS = ["polars", "ibis-duckdb"]


def pytest_addoption(parser):
    parser.addoption(
        "--backends",
        default="polars,ibis-duckdb",
        help="Comma-separated test backends, or 'all' (default: polars,ibis-duckdb).",
    )


def pytest_collection_modifyitems(config, items):
    """Deselect unused backend variants."""
    requested = config.getoption("--backends")
    selected = (
        set(ALL_BACKENDS)
        if requested == "all"
        else {name.strip() for name in requested.split(",")}
    )
    unknown = selected.difference(ALL_BACKENDS)
    if unknown:
        raise pytest.UsageError(
            f"Unknown --backends values: {sorted(unknown)!r}; "
            f"choose from {ALL_BACKENDS!r} or 'all'"
        )
    backend_params = (
        "backend_name",
        "list_backend_name",
        "list_backend",
        "apply_backend",
        "source_backend",
        "context_backend",
    )
    retained, deselected = [], []
    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is not None and any(
            name in callspec.params and callspec.params[name] not in selected
            for name in backend_params
        ):
            deselected.append(item)
        else:
            retained.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = retained



# ---------------------------------------------------------------------------
# Backend DataFrame construction
# ---------------------------------------------------------------------------


def build_backend_df(backend: str, data: dict, table_name: str = "t") -> Any:
    """Dispatch a data dict into the requested backend's DataFrame type."""
    if backend == "polars":
        return pl.DataFrame(data)
    if backend == "ibis-duckdb":
        conn = ibis.duckdb.connect()
        return conn.create_table(table_name, data, overwrite=True)
    raise ValueError(f"Unknown backend: {backend}")


def bind_compiler_inputs(frame: Any, dimensions: list[Dimension]) -> Any:
    """Bind direct compiler fixtures through the production state contract."""
    from mountainash.relations import Relation, relation
    from mountainash_rules.core.compiler import normalize_rule_relation
    from mountainash_rules.core.context import bind_context_columns

    rel = normalize_rule_relation(
        frame if isinstance(frame, Relation) else relation(frame), dimensions
    )
    return bind_context_columns(
        rel,
        [
            dim.model_copy(update={"context_field": "__ctx_" + dim.dimension_name})
            for dim in dimensions
        ],
    )


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
        "region": ["AU", UNKNOWN, "AU", "US"],
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
    return DimensionsMetadata(
        fields=(
            ContextField(name="region", data_type="str", wildcard=UNKNOWN),
            ContextField(name="amount", data_type="int", wildcard=UNKNOWN_NUMERIC),
            ContextField(name="code", data_type="str"),
        ),
        dimensions=(
            Dimension(
                dimension_name="region",
                context_field="region",
                match_strategy=MatchStrategy.EXACT,
            ),
            Dimension(
                dimension_name="amount",
                context_field="amount",
                match_strategy=MatchStrategy.RANGE,
                range_min_field="amount_min",
                range_max_field="amount_max",
            ),
            Dimension(
                dimension_name="code",
                context_field="code",
                match_strategy=MatchStrategy.CONTEXT_REGEX,
                regex_pattern="^PRE.*",
            ),
        ),
    )


@pytest.fixture
def basic_engine(backend_rules_df, basic_metadata) -> ExpressionRulesEngine:
    return ExpressionRulesEngine(
        rules=backend_rules_df, dimension_metadata=basic_metadata
    )


@pytest.fixture
def valid_context() -> TestContext:
    return TestContext(region="AU", amount=50, code="PRE-001")
