"""Tests for context value extraction."""

import polars as pl
import pytest
from pydantic import BaseModel

from mountainash_rules import ContextField
from mountainash_rules.core.context import extract_context_values
from mountainash_rules.core.constants import DataType, MatchStrategy
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


def test_missing_field_remains_omitted():
    ctx = {"region": "AU"}
    values = extract_context_values(ctx, ["region", "missing_field"])
    assert values["region"] == "AU"
    assert "missing_field" not in values


def test_supplied_none_stays_supplied_none():
    ctx = {"region": None}
    values = extract_context_values(ctx, ["region"])
    assert values == {"region": None}


# ---------------------------------------------------------------------------
# Original presence + context_field (metadata-aware extraction)
# ---------------------------------------------------------------------------


@pytest.fixture
def typed_metadata() -> DimensionsMetadata:
    return DimensionsMetadata(
        fields=(
            ContextField(name="region", data_type="str"),
            ContextField(name="amount", data_type="int"),
            ContextField(name="score", data_type="float"),
        ),
        dimensions=(
            Dimension(dimension_name="region", context_field="region"),
            Dimension(
                dimension_name="amount", context_field="amount",
                match_strategy=MatchStrategy.RANGE,
                range_min_field="amount_min", range_max_field="amount_max",
            ),
            Dimension(dimension_name="score", context_field="score"),
        ),
    )


def test_missing_numeric_fields_remain_omitted(typed_metadata):
    values = extract_context_values(
        {"region": "AU"}, ["region", "amount", "score"], metadata=typed_metadata
    )
    assert values["region"] == "AU"
    assert "amount" not in values
    assert "score" not in values


def test_supplied_numeric_null_is_not_omitted(typed_metadata):
    values = extract_context_values(
        {"region": "AU", "amount": None}, ["amount"], metadata=typed_metadata
    )
    assert values == {"amount": None}


def test_missing_string_field_remains_omitted(typed_metadata):
    values = extract_context_values({}, ["region"], metadata=typed_metadata)
    assert values == {}


def test_context_field_remap_honoured():
    metadata = DimensionsMetadata(
        fields=(ContextField(name="cust_region", data_type="str"),),
        dimensions=(Dimension(dimension_name="region", context_field="cust_region"),)
    )
    values = extract_context_values(
        {"cust_region": "AU", "region": "WRONG"}, ["region"], metadata=metadata
    )
    assert values["region"] == "AU"


# ---------------------------------------------------------------------------
# Engine-level: missing numeric context must be UNKNOWN, not a string literal
# ---------------------------------------------------------------------------


def _range_engine() -> ExpressionRulesEngine:
    rules = pl.DataFrame(
        {
            "rule_name": ["in_range"],
            "region": ["AU"],
            "amount_min": [0],
            "amount_max": [100],
        }
    )
    metadata = DimensionsMetadata(
        fields=(
            ContextField(name="region", data_type="str"),
            ContextField(name="amount", data_type="int"),
        ),
        dimensions=(
            Dimension(dimension_name="region", context_field="region"),
            Dimension(
                dimension_name="amount", context_field="amount",
                match_strategy=MatchStrategy.RANGE,
                range_min_field="amount_min", range_max_field="amount_max",
            ),
        )
    )
    return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)


def test_engine_missing_numeric_context_is_unknown():
    result = _range_engine().evaluate({"region": "AU"})
    assert result.count == 1
    assert result.explain("in_range") == {"region": 1, "amount": 0}


def test_engine_context_field_remap_end_to_end():
    rules = pl.DataFrame({"rule_name": ["au_rule"], "region": ["AU"]})
    metadata = DimensionsMetadata(
        fields=(ContextField(name="cust_region", data_type="str"),),
        dimensions=(Dimension(dimension_name="region", context_field="cust_region"),)
    )
    engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
    result = engine.evaluate({"cust_region": "AU"})
    assert result.count == 1
    # A hard match (1), not a missing-context wildcard (0): proves the value
    # was read from cust_region, not from the dimension name.
    assert result.explain("au_rule") == {"region": 1}


def test_bool_dimension_missing_context_stays_omitted():
    md = DimensionsMetadata(
        fields=(ContextField(name="active", data_type=DataType.BOOL),),
        dimensions=(Dimension(dimension_name="active", context_field="active"),)
    )
    values = extract_context_values({}, ["active"], metadata=md)
    assert values == {}


@pytest.mark.parametrize(
    "context,origin,supplied",
    [
        ({}, "omitted", False),
        ({"region": None}, "null", True),
        ({"region": "EU"}, "concrete", True),
    ],
)
def test_reusable_context_bindings_retain_original_provenance(
    backend_name,
    context,
    origin,
    supplied,
):
    from mountainash.relations import relation
    from mountainash_rules.core.context import bind_context_columns
    from tests.conftest import build_backend_df

    (dim,) = DimensionsMetadata(
        fields=(ContextField(name="region", data_type="str"),),
        dimensions=(Dimension(dimension_name="region", context_field="region"),),
    ).dimensions
    rel = relation(build_backend_df(backend_name, {"row": [1]}))
    bound = bind_context_columns(rel, [dim], values=context).to_dicts()[0]
    assert bound["__ctx_origin_region"] == origin
    assert bound["__ctx_supplied_region"] is supplied
    assert bound["__ctx_present_region"] is (origin == "concrete")
