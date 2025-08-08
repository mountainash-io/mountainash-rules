"""Shared fixtures for mountainash_utils_rules tests."""

import pytest
from mountainash_utils_rules import RulesEngine, DimensionsMetadata, Dimension, MatchStrategy
from mountainash_utils_rules.constants import RuleConstants, RuleTrinaryFlags
from mountainash_dataframes import BaseDataFrame, IbisDataFrame
import polars as pl
import ibis
from pydantic import BaseModel


class TestContext(BaseModel):
    """Standard test context model for use across tests."""
    DIM_1: str
    DIM_2: int
    DIM_3: str


class ExtendedTestContext(BaseModel):
    """Extended test context with more dimensions for complex testing."""
    DIM_1: str
    DIM_2: int
    DIM_3: str
    DIM_4: float
    DIM_5: bool


@pytest.fixture
def sample_rules_data():
    """Basic rules data as Polars DataFrame."""
    return pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3", "rule_4", "rule_5"],
        "DIM_1": ["A", "B", "C", RuleConstants.UNKNOWN, "D"],
        "DIM_2_MIN": [0, 10, 20, 30, 40],
        "DIM_2_MAX": [9, 19, 29, 39, 49],
        "DIM_3": ["X.*", "Y.*", "Z.*", "W.*", RuleConstants.UNKNOWN]
    })


@pytest.fixture
def sample_rules(sample_rules_data):
    """Sample rules as IbisDataFrame for testing."""
    return IbisDataFrame(sample_rules_data, ibis_backend_schema="sqlite")


@pytest.fixture
def extended_rules_data():
    """Extended rules data with more dimensions."""
    return pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3", "rule_4"],
        "DIM_1": ["A", "B", "C", RuleConstants.UNKNOWN],
        "DIM_2_MIN": [0, 10, 20, 30],
        "DIM_2_MAX": [9, 19, 29, 39],
        "DIM_3": ["X.*", "Y.*", "Z.*", "W.*"],
        "DIM_4_MIN": [0.0, 1.5, 3.0, 4.5],
        "DIM_4_MAX": [1.4, 2.9, 4.4, 5.9],
        "DIM_5": [True, False, True, RuleConstants.UNKNOWN]
    })


@pytest.fixture
def extended_rules(extended_rules_data):
    """Extended rules as IbisDataFrame for complex testing."""
    return IbisDataFrame(extended_rules_data, ibis_backend_schema="sqlite")


@pytest.fixture
def basic_dimension_metadata():
    """Basic dimension metadata for standard testing."""
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
        ]
    )


@pytest.fixture
def extended_dimension_metadata():
    """Extended dimension metadata for complex testing."""
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                     range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str),
            Dimension(dimension_name="DIM_4", match_strategy=MatchStrategy.RANGE, data_type=float,
                     range_min_field="DIM_4_MIN", range_max_field="DIM_4_MAX"),
            Dimension(dimension_name="DIM_5", match_strategy=MatchStrategy.EXACT, data_type=bool)
        ]
    )


@pytest.fixture
def basic_rules_engine(sample_rules, basic_dimension_metadata):
    """Basic RulesEngine instance for standard testing."""
    return RulesEngine(rules=sample_rules, dimension_metadata=basic_dimension_metadata)


@pytest.fixture
def extended_rules_engine(extended_rules, extended_dimension_metadata):
    """Extended RulesEngine instance for complex testing."""
    return RulesEngine(rules=extended_rules, dimension_metadata=extended_dimension_metadata)


@pytest.fixture
def valid_context():
    """Valid context instance for testing."""
    return TestContext(DIM_1="A", DIM_2=5, DIM_3="XYZ")


@pytest.fixture
def extended_valid_context():
    """Extended valid context instance for complex testing."""
    return ExtendedTestContext(DIM_1="A", DIM_2=5, DIM_3="XYZ", DIM_4=2.5, DIM_5=True)


@pytest.fixture
def empty_rules_data():
    """Empty rules dataframe for edge case testing."""
    return pl.DataFrame({
        "rule_name": [],
        "DIM_1": [],
        "DIM_2_MIN": [],
        "DIM_2_MAX": [],
        "DIM_3": []
    })


@pytest.fixture
def empty_rules(empty_rules_data):
    """Empty rules as IbisDataFrame for edge case testing."""
    return IbisDataFrame(empty_rules_data, ibis_backend_schema="sqlite")


@pytest.fixture
def single_dimension():
    """Single dimension for isolated testing."""
    return Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str)


@pytest.fixture
def range_dimension():
    """Range dimension for range matching tests."""
    return Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int,
                    range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX")


@pytest.fixture
def regex_dimension():
    """Regex dimension for pattern matching tests."""
    return Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)


@pytest.fixture(params=["sqlite", "polars"])
def backend_schema(request):
    """Parameterized fixture for testing different backends."""
    return request.param


@pytest.fixture
def sample_context_variations():
    """Various context instances for comprehensive testing."""
    return [
        TestContext(DIM_1="A", DIM_2=5, DIM_3="XYZ"),
        TestContext(DIM_1="B", DIM_2=15, DIM_3="YAB"),
        TestContext(DIM_1="C", DIM_2=25, DIM_3="ZCD"),
        TestContext(DIM_1="D", DIM_2=45, DIM_3="WEF"),
        TestContext(DIM_1=RuleConstants.UNKNOWN, DIM_2=35, DIM_3="WAB")
    ]
