import pytest
from mountainash_utils_rules.metadata import MetadataManager, RuleMetadata, DimensionMetadata
from mountainash_utils_rules.constants import RuleType
from mountainash_data import BaseDataFrame, DataFrameFactory
import polars as pl
from pydantic import BaseModel

class Context(BaseModel):
    DIM_1: str
    DIM_2: str
    DIM_3: str

@pytest.fixture
def sample_rule_metadata():
    return RuleMetadata(
        dimensions=[
            DimensionMetadata(dimension_name="DIM_1", rule_type=RuleType.EXACT, data_type="string"),
            DimensionMetadata(dimension_name="DIM_2", rule_type=RuleType.EXACT, data_type="int"),
            DimensionMetadata(dimension_name="DIM_3", rule_type=RuleType.EXACT, data_type="string")
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
    return DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules_df, ibis_backend_schema="sqlite")

def test_metadata_manager_initialization(sample_rule_metadata):
    metadata_manager = MetadataManager(sample_rule_metadata)
    assert isinstance(metadata_manager.raw_rule_metadata, RuleMetadata)
    assert len(metadata_manager.lookup_rule_metadata) == 3

def test_get_dimension(sample_rule_metadata):
    metadata_manager = MetadataManager(sample_rule_metadata)
    dimension = metadata_manager.get_dimension("DIM_1")
    assert isinstance(dimension, DimensionMetadata)
    assert dimension.dimension_name == "DIM_1"

def test_get_dimensions_list(sample_rule_metadata):
    metadata_manager = MetadataManager(sample_rule_metadata)
    dimensions = metadata_manager.get_dimensions_list(["DIM_1", "DIM_2"])
    assert len(dimensions) == 2
    assert all(isinstance(dim, DimensionMetadata) for dim in dimensions)

def test_get_active_dimension_names(sample_rule_metadata, sample_rules):
    metadata_manager = MetadataManager(sample_rule_metadata)
    context = Context(DIM_1="A", DIM_2="1", DIM_3="X")
    active_dimensions = metadata_manager.get_active_dimension_names(context, sample_rules, ["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1", "DIM_2", "DIM_3"}

def test_get_active_dimension_names_with_missing_field(sample_rule_metadata, sample_rules):
    metadata_manager = MetadataManager(sample_rule_metadata)
    context = Context(DIM_1="A", DIM_2="1", DIM_3="X")
    rules_without_dim3 = sample_rules.drop("DIM_3")
    active_dimensions = metadata_manager.get_active_dimension_names(context, rules_without_dim3, ["DIM_1", "DIM_2", "DIM_3"])
    assert set(active_dimensions) == {"DIM_1", "DIM_2"}

def test_validate_unique_dimension_names():
    with pytest.raises(ValueError):
        MetadataManager(RuleMetadata(
            dimensions=[
                DimensionMetadata(dimension_name="DIM_1"),
                DimensionMetadata(dimension_name="DIM_1")
            ]
        ))

def test_get_dimension_nonexistent():
    metadata_manager = MetadataManager(RuleMetadata(dimensions=[]))
    with pytest.raises(ValueError):
        metadata_manager.get_dimension("NONEXISTENT")