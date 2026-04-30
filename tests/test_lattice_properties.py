import polars as pl
from mountainash_utils_rules.aggregate import Aggregate
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.lattice import Lattice


def test_lattice_metadata_property():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="country", match_strategy=MatchStrategy.EXACT, data_type=str),
        ]
    )
    aggregates = [Aggregate(column_name="price", operation="sum")]
    df = pl.DataFrame({"country": ["AU"], "price": [100]})
    lattice = Lattice(dataframe=df, metadata=metadata, aggregates=aggregates, partition_key=None)

    assert lattice.metadata is metadata
    assert lattice.aggregates is aggregates


def test_lattice_aggregates_default_empty():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="country", match_strategy=MatchStrategy.EXACT, data_type=str),
        ]
    )
    df = pl.DataFrame({"country": ["AU"]})
    lattice = Lattice(dataframe=df, metadata=metadata, aggregates=[], partition_key=None)

    assert lattice.aggregates == []
