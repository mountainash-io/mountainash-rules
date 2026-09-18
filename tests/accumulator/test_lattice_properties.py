"""Lattice inspection must not expose mutable declaration ownership."""

import polars as pl

from mountainash_rules import Aggregate, Dimension, DimensionsMetadata, Lattice


def test_inspection_metadata_mutation_does_not_change_lattice():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="country", match_strategy="exact", data_type="str")
        ]
    )
    aggregates = [Aggregate(column_name="price", operation="sum")]
    lattice = Lattice(
        dataframe=pl.DataFrame({"country": ["AU"], "price": [100]}),
        metadata=metadata,
        aggregates=aggregates,
        partition_key=None,
    )

    metadata.dimensions.clear()
    aggregates.clear()
    exposed = lattice.metadata
    exposed.dimensions.clear()
    lattice.aggregates.clear()

    assert [dimension.dimension_name for dimension in lattice.metadata.dimensions] == [
        "country"
    ]
    assert [aggregate.column_name for aggregate in lattice.aggregates] == ["price"]
