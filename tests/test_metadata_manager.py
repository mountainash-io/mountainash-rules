import pytest
from mountainash_utils_rules.dimension import MetadataManager, DimensionsMetadata, Dimension
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_dataframes import BaseDataFrame, IbisDataFrame
import polars as pl
from pydantic import BaseModel
from typing import Optional

class Context(BaseModel):
    DIM_1: Optional[str] = None
    DIM_2: Optional[str] = None
    DIM_3: Optional[str] = None

@pytest.fixture
def sample_rule_metadata():
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
            Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.EXACT, data_type=int),
            Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.EXACT, data_type=str)
        ]
    )

@pytest.fixture
def sample_rules():
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", "C"],
        "DIM_2": ["1", "2", "3"],
        "DIM_3": ["X", "<NA>", "<NA>"]
    })
    return IbisDataFrame(rules_df, ibis_backend_schema="sqlite")

def test_metadata_manager_initialization(sample_rules, sample_rule_metadata):
    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=sample_rule_metadata)
    assert isinstance(metadata_manager.raw_dimension_metadata, DimensionsMetadata)
    assert len(list(metadata_manager.lookup_dimension_metadata.keys())) == 3

def test_get_dimension(sample_rules, sample_rule_metadata):
    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=sample_rule_metadata)
    dimension = metadata_manager.get_dimension("DIM_1")
    assert isinstance(dimension, Dimension)
    assert dimension.dimension_name == "DIM_1"

def test_get_dimensions_list(sample_rules, sample_rule_metadata):
    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=sample_rule_metadata)
    dimensions = metadata_manager.get_dimensions_list(["DIM_1", "DIM_2"])
    assert len(dimensions) == 2
    assert all(isinstance(dim, Dimension) for dim in dimensions)

def test_get_active_dimension_names(sample_rule_metadata, sample_rules):
    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=sample_rule_metadata)
    context = Context(DIM_1="A", DIM_2="B", DIM_3="X")
    active_dimensions = metadata_manager.get_active_dimension_names(context, sample_rules, ["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1", "DIM_2", "DIM_3"}



def test_get_active_dimension_names_with_truncated_context(sample_rule_metadata, sample_rules):
    class TruncatedContext(BaseModel):
        DIM_1: str
        DIM_3: str

    truncated_context = TruncatedContext(DIM_1="A", DIM_3="X")

    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=sample_rule_metadata)
    # rules_without_dim3 = sample_rules.drop("DIM_3")

    active_dimensions = metadata_manager.get_active_dimension_names(context=truncated_context, rules=sample_rules, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1", "DIM_3"}


def test_get_active_dimension_names_with_early_truncated_rules(sample_rule_metadata, sample_rules):

    context = Context(DIM_1="A", DIM_2="B", DIM_3="X")

    rules_without_dim3 = sample_rules.drop(columns=["DIM_3"])
    metadata_manager = MetadataManager(rules=rules_without_dim3, dimension_metadata=sample_rule_metadata)

    active_dimensions = metadata_manager.get_active_dimension_names(context, rules_without_dim3, ["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1", "DIM_2"}


def test_get_active_dimension_names_with_late_truncated_rules(sample_rule_metadata, sample_rules):

    context = Context(DIM_1="A", DIM_2="B", DIM_3="X")

    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=sample_rule_metadata)
    rules_without_dim3 = sample_rules.drop("DIM_3")

    active_dimensions = metadata_manager.get_active_dimension_names(context, rules_without_dim3, ["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1", "DIM_2"}


def test_get_active_dimension_names_with_truncated_rules_and_context(sample_rule_metadata, sample_rules):

    class TruncatedContext(BaseModel):
        DIM_1: str
        DIM_3: str

    truncated_context = TruncatedContext(DIM_1="A", DIM_3="X")

    rules_without_dim3 = sample_rules.drop(columns=["DIM_3"])
    metadata_manager = MetadataManager(rules=rules_without_dim3, dimension_metadata=sample_rule_metadata)

    active_dimensions = metadata_manager.get_active_dimension_names(truncated_context, rules_without_dim3, ["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1"}


def test_get_active_dimension_names_with_none_context_value(sample_rule_metadata, sample_rules):
    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=sample_rule_metadata)

    context = Context(DIM_1="A", DIM_2=None, DIM_3="X")
    active_dimensions = metadata_manager.get_active_dimension_names(context=context, rules=sample_rules, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1", "DIM_3"}

    context = Context(DIM_1="A", DIM_2=None, DIM_3=None)
    active_dimensions = metadata_manager.get_active_dimension_names(context=context, rules=sample_rules, dimension_names=["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1"}


def test_validate_unique_dimension_names(sample_rules):
    with pytest.raises(ValueError):
        MetadataManager(rules=sample_rules,

            dimension_metadata=DimensionsMetadata(dimensions=[
                Dimension(dimension_name="DIM_1"),
                Dimension(dimension_name="DIM_1")
            ]
        ))

def test_get_dimension_nonexistent(sample_rules):
    metadata_manager = MetadataManager(rules=sample_rules, dimension_metadata=DimensionsMetadata(dimensions=[]))
    dimension = metadata_manager.get_dimension("NONEXISTENT")
    assert dimension.dimension_name == "NONEXISTENT"
