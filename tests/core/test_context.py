"""Tests for context value extraction."""

import polars as pl
import pytest
from pydantic import BaseModel

from mountainash_rules.core.context import extract_context_values
from mountainash_rules.core.constants import NOT_SET, NOT_SET_NUMERIC, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine


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


# ---------------------------------------------------------------------------
# Typed sentinels + context_field (metadata-aware extraction)
# ---------------------------------------------------------------------------

@pytest.fixture
def typed_metadata() -> DimensionsMetadata:
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        ),
        Dimension(dimension_name="score", match_strategy=MatchStrategy.GREATER_THAN, data_type=float),
    ])


def test_missing_numeric_field_returns_numeric_sentinel(typed_metadata):
    values = extract_context_values(
        {"region": "AU"}, ["region", "amount", "score"], metadata=typed_metadata
    )
    assert values["region"] == "AU"
    assert values["amount"] == NOT_SET_NUMERIC
    assert values["score"] == NOT_SET_NUMERIC


def test_none_numeric_field_returns_numeric_sentinel(typed_metadata):
    values = extract_context_values(
        {"region": "AU", "amount": None}, ["amount"], metadata=typed_metadata
    )
    assert values["amount"] == NOT_SET_NUMERIC


def test_missing_string_field_returns_string_sentinel(typed_metadata):
    values = extract_context_values({}, ["region"], metadata=typed_metadata)
    assert values["region"] == NOT_SET


def test_context_field_remap_honoured():
    metadata = DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="region",
            context_field="cust_region",
            match_strategy=MatchStrategy.EXACT,
            data_type=str,
        ),
    ])
    values = extract_context_values(
        {"cust_region": "AU", "region": "WRONG"}, ["region"], metadata=metadata
    )
    assert values["region"] == "AU"


# ---------------------------------------------------------------------------
# Engine-level: missing numeric context must be UNKNOWN, not a string literal
# ---------------------------------------------------------------------------

def _range_engine() -> ExpressionRulesEngine:
    rules = pl.DataFrame({
        "rule_name": ["in_range"],
        "region": ["AU"],
        "amount_min": [0],
        "amount_max": [100],
    })
    metadata = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(
            dimension_name="amount",
            match_strategy=MatchStrategy.RANGE,
            data_type=int,
            range_min_field="amount_min",
            range_max_field="amount_max",
        ),
    ])
    return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)


def test_engine_missing_numeric_context_is_unknown():
    result = _range_engine().evaluate({"region": "AU"})
    assert result.count == 1
    assert result.explain("in_range") == {"region": 1, "amount": 0}


def test_engine_context_field_remap_end_to_end():
    rules = pl.DataFrame({"rule_name": ["au_rule"], "region": ["AU"]})
    metadata = DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="region",
            context_field="cust_region",
            match_strategy=MatchStrategy.EXACT,
            data_type=str,
        ),
    ])
    engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
    result = engine.evaluate({"cust_region": "AU"})
    assert result.count == 1
    # A hard match (1), not a missing-context wildcard (0): proves the value
    # was read from cust_region, not from the dimension name.
    assert result.explain("au_rule") == {"region": 1}


def test_bool_dimension_missing_context_stays_none():
    from mountainash_rules.core.constants import DataType
    md = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="active", data_type=DataType.BOOL),
    ])
    values = extract_context_values({}, ["active"], metadata=md)
    assert values["active"] is None
