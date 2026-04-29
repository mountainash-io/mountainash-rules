"""Tests for Aggregate model and Lattice data class."""

import polars as pl
import pytest

from mountainash_utils_rules.aggregate import Aggregate
from mountainash_utils_rules.lattice import Lattice
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata


class TestAggregate:
    def test_default_operation_is_sum(self):
        agg = Aggregate(column_name="margin")
        assert agg.operation == "sum"

    def test_explicit_operation(self):
        agg = Aggregate(column_name="margin", operation="max")
        assert agg.operation == "max"

    def test_column_name_required(self):
        with pytest.raises(Exception):
            Aggregate()


class TestLattice:
    @pytest.fixture
    def sample_lattice(self):
        df = pl.DataFrame({
            "co_channel": ["BROKER", "BROKER"],
            "__prime_product": [6, 10],
            "__level": [1, 1],
            "__agg_margin": [-0.15, -0.25],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        return Lattice(
            dataframe=df,
            metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
            partition_key={"product_id": 1},
        )

    def test_combinations_returns_dataframe(self, sample_lattice):
        assert sample_lattice.combinations is not None

    def test_count_returns_row_count(self, sample_lattice):
        assert sample_lattice.count == 2

    def test_partition_key_returned(self, sample_lattice):
        assert sample_lattice.partition_key == {"product_id": 1}

    def test_partition_key_none_when_not_set(self):
        df = pl.DataFrame({"co_channel": ["BROKER"], "__prime_product": [2]})
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        lattice = Lattice(dataframe=df, metadata=metadata, aggregates=[], partition_key=None)
        assert lattice.partition_key is None
