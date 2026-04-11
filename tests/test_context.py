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
