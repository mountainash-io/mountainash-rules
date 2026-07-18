"""Tests for Aggregate model and Lattice data class."""

import polars as pl
import pytest

from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.engines.accumulator.lattice import Lattice
from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


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

from mountainash_rules.engines.accumulator.engine import AccumulatorEngine


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


from mountainash_rules import (
    AccumulatorEngine,
    Aggregate,
    Dimension,
    DimensionsMetadata,
    Lattice,
    MatchStrategy,
    UNKNOWN,
)


@pytest.fixture
def built_lattice_and_engine():
    import polars as pl

    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type="str"),
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT, data_type="str"),
        ]
    )
    rules = pl.DataFrame(
        {
            "rule_name": ["au_base", "broker_bonus", "au_broker"],
            "region": ["AU", UNKNOWN, "AU"],
            "channel": [UNKNOWN, "BROKER", "BROKER"],
            "discount": [5.0, 2.5, 10.0],
        }
    )
    engine = AccumulatorEngine(
        dimension_metadata=metadata,
        aggregates=[Aggregate(column_name="discount", operation="sum")],
    )
    return engine.build(rules), engine


class TestLatticeSaveLoad:
    def test_round_trip_identity(self, built_lattice_and_engine, tmp_path):
        lattice, _ = built_lattice_and_engine
        out = lattice.save(tmp_path / "snap")
        loaded = Lattice.load(out)
        assert loaded.count == lattice.count
        assert loaded.is_composed is True  # __prime_product travels
        assert loaded.metadata == lattice.metadata
        assert loaded.aggregates == lattice.aggregates
        assert loaded.partition_key == lattice.partition_key

    def test_apply_equivalence(self, built_lattice_and_engine, tmp_path):
        lattice, engine = built_lattice_and_engine
        loaded = Lattice.load(lattice.save(tmp_path / "snap"))
        ctx = {"region": "AU", "channel": "BROKER"}
        original = engine.apply(lattice, ctx)
        reloaded = engine.apply(loaded, ctx)
        assert reloaded.count == original.count
        assert (
            reloaded.accumulated("discount").to_dicts()
            == original.accumulated("discount").to_dicts()
        )

    def test_save_creates_expected_files(self, built_lattice_and_engine, tmp_path):
        lattice, _ = built_lattice_and_engine
        out = lattice.save(tmp_path / "snap")
        assert (out / "lattice.parquet").exists()
        assert (out / "manifest.yaml").exists()

    def test_manifest_is_babel_superset(self, built_lattice_and_engine, tmp_path):
        import yaml

        lattice, _ = built_lattice_and_engine
        out = lattice.save(tmp_path / "snap")
        raw = yaml.safe_load((out / "manifest.yaml").read_text())
        assert set(raw) == {"dimensions", "aggregates", "partition_key"}
        assert raw["aggregates"] == [{"column_name": "discount", "operation": "sum"}]

    def test_load_missing_manifest_raises(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(FileNotFoundError):
            Lattice.load(tmp_path / "empty")
