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
