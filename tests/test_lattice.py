"""Tests for Aggregate model and Lattice data class."""

import polars as pl
import pytest

from mountainash_rules.aggregate import Aggregate
from mountainash_rules.lattice import Lattice
from mountainash_rules.constants import MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata


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


from mountainash.relations import relation

from mountainash_rules.accumulator_engine import AccumulatorEngine


class TestIsComposed:
    def _metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region"),
        ])

    def test_built_lattice_is_composed(self):
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        rules = pl.DataFrame({"rule_name": ["r"], "region": ["AU"]})
        assert engine.build(rules).is_composed is True

    def test_hand_constructed_flat_lattice_is_not_composed(self):
        lattice = Lattice(
            dataframe=pl.DataFrame({"rule_name": ["r"], "region": ["AU"]}),
            metadata=self._metadata(),
            aggregates=[],
            partition_key=None,
        )
        assert lattice.is_composed is False

    def test_empty_build_is_still_composed(self):
        engine = AccumulatorEngine(dimension_metadata=self._metadata())
        rules = pl.DataFrame({"rule_name": [], "region": []},
                             schema={"rule_name": pl.Utf8, "region": pl.Utf8})
        lattice = engine.build(rules)
        assert lattice.count == 0
        assert lattice.is_composed is True
        assert "co_region" in relation(lattice.combinations).columns
