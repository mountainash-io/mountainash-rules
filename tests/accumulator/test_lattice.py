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


from pydantic import BaseModel

from mountainash_rules import AmbiguousPartitionError
from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import (
    UNKNOWN,
    UNKNOWN_NUMERIC,
    NOT_SET,
    DimensionRole,
    MatchStrategy,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


class RoutingContext(BaseModel):
    region: str | None = None
    channel: str | None = None
    product: str


def _routing_engine():
    metadata = DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="region",
            match_strategy=MatchStrategy.EXACT,
            role=DimensionRole.CONTEXT_KEY,
        ),
        Dimension(
            dimension_name="channel",
            match_strategy=MatchStrategy.EXACT,
            role=DimensionRole.CONTEXT_KEY,
        ),
        Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT),
    ])
    return AccumulatorEngine(
        dimension_metadata=metadata,
        aggregates=[Aggregate(column_name="margin")],
    )


def _routing_rules(keys: list[tuple[str, str]]) -> pl.DataFrame:
    """One rule per (region, channel) partition key; UNKNOWN = wildcard."""
    return pl.DataFrame({
        "region": [k[0] for k in keys],
        "channel": [k[1] for k in keys],
        "rule_name": [f"r{i}" for i in range(len(keys))],
        "product": ["GOLD"] * len(keys),
        "margin": [1.0] * len(keys),
    })


def _index_for(keys, validate=True):
    engine = _routing_engine()
    lattices = engine.build_all(_routing_rules(keys))
    return engine, engine.index(lattices, validate=validate)


class TestTernaryRouting:
    def test_default_partition_catches_unmatched_context(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(RoutingContext(region="NZ", channel="DIRECT", product="GOLD"))
        assert result.count >= 1  # routed to the all-wildcard default

    def test_partial_specific_miss_falls_to_default(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        # Exercise _route (not the dict fast path): (AU, DIRECT) is not an
        # exact key; (AU, BROKER) dies on channel; default survives.
        result = index.apply(RoutingContext(region="AU", channel="DIRECT", product="GOLD"))
        assert result.count >= 1

    def test_exact_hit_uses_fast_path_and_wins(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(RoutingContext(region="AU", channel="BROKER", product="GOLD"))
        assert result.count >= 1

    def test_runtime_ambiguity_raises(self):
        engine, index = _index_for(
            [("AU", UNKNOWN), (UNKNOWN, "BROKER")], validate=False
        )
        with pytest.raises(AmbiguousPartitionError):
            index.apply(RoutingContext(region="AU", channel="BROKER", product="GOLD"))

    def test_missing_key_field_routes_to_default(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(RoutingContext(product="GOLD"))  # region/channel None
        assert result.count >= 1

    def test_missing_key_field_without_default_raises(self):
        engine, index = _index_for([("AU", "BROKER")])
        with pytest.raises(KeyError, match="No lattice"):
            index.apply(RoutingContext(product="GOLD"))

    def test_context_unknown_sentinel_is_wildcard_only(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        result = index.apply(
            RoutingContext(region=UNKNOWN, channel="BROKER", product="GOLD")
        )
        # UNKNOWN region kills (AU, BROKER); only the default survives.
        assert result.count >= 1

    def test_wildcard_free_index_miss_raises_keyerror(self):
        engine, index = _index_for([("AU", "BROKER")])
        with pytest.raises(KeyError, match="No lattice"):
            index.apply(RoutingContext(region="US", channel="X", product="GOLD"))

    def test_ambiguous_is_a_keyerror(self):
        assert issubclass(AmbiguousPartitionError, KeyError)


class TestIndexStructuralChecks:
    def test_empty_index_raises(self):
        engine = _routing_engine()
        with pytest.raises(ValueError, match="at least one lattice"):
            engine.index([])

    def test_duplicate_keys_raise(self):
        engine = _routing_engine()
        lattices = engine.build_all(_routing_rules([("AU", "BROKER")]))
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            engine.index(lattices + lattices)

    def test_not_set_key_raises(self):
        engine = _routing_engine()
        lattices = engine.build_all(_routing_rules([(NOT_SET, "BROKER")]))
        with pytest.raises(ValueError, match="NOT_SET"):
            engine.index(lattices)

    def test_keyless_lattice_with_key_dims_raises(self):
        engine = _routing_engine()
        (good,) = engine.build_all(_routing_rules([("AU", "BROKER")]))
        flat = Lattice(
            dataframe=good.combinations,
            metadata=good.metadata,
            aggregates=good.aggregates,
            partition_key=None,
        )
        with pytest.raises(ValueError, match="partition_key"):
            engine.index([good, flat])


class TestTernaryRoutingBatch:
    def test_batch_mixes_specific_and_default(self):
        engine, index = _index_for([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        contexts = pl.DataFrame({
            "region": ["AU", "NZ", None],
            "channel": ["BROKER", "DIRECT", None],
            "product": ["GOLD", "GOLD", "GOLD"],
        })
        result = index.apply_batch(contexts)
        rows = relation(result.survivors).to_dict()
        # every context produced at least one survivor row
        assert set(rows["__context_id"]) == {0, 1, 2}

    def test_batch_unroutable_combo_raises(self):
        engine, index = _index_for([("AU", "BROKER")])
        contexts = pl.DataFrame({
            "region": ["AU", "US"],
            "channel": ["BROKER", "X"],
            "product": ["GOLD", "GOLD"],
        })
        with pytest.raises(KeyError, match="No lattice"):
            index.apply_batch(contexts)

    def test_batch_ambiguous_combo_raises(self):
        engine, index = _index_for(
            [("AU", UNKNOWN), (UNKNOWN, "BROKER")], validate=False
        )
        contexts = pl.DataFrame({
            "region": ["AU"],
            "channel": ["BROKER"],
            "product": ["GOLD"],
        })
        with pytest.raises(AmbiguousPartitionError):
            index.apply_batch(contexts)


class TestIndexValidation:
    def test_crossing_pair_without_cover_raises_at_index(self):
        engine = _routing_engine()
        lattices = engine.build_all(
            _routing_rules([("AU", UNKNOWN), (UNKNOWN, "BROKER")])
        )
        with pytest.raises(AmbiguousPartitionError) as exc:
            engine.index(lattices)
        # message carries a witness context
        assert "AU" in str(exc.value) and "BROKER" in str(exc.value)

    def test_crossing_pair_with_cover_validates_and_routes(self):
        engine = _routing_engine()
        lattices = engine.build_all(_routing_rules([
            ("AU", UNKNOWN), (UNKNOWN, "BROKER"), ("AU", "BROKER"),
        ]))
        index = engine.index(lattices)  # must NOT raise (false-positive guard)
        result = index.apply(
            RoutingContext(region="AU", channel="BROKER", product="GOLD")
        )
        assert result.count >= 1

    def test_validate_false_defers_to_runtime(self):
        engine = _routing_engine()
        lattices = engine.build_all(
            _routing_rules([("AU", UNKNOWN), (UNKNOWN, "BROKER")])
        )
        index = engine.index(lattices, validate=False)  # no raise here
        with pytest.raises(AmbiguousPartitionError):
            index.apply(RoutingContext(region="AU", channel="BROKER", product="GOLD"))

    def test_witness_cap_overflow_raises(self):
        engine = _routing_engine()
        lattices = engine.build_all(
            _routing_rules([("AU", "BROKER"), ("NZ", "DIRECT")])
        )
        # 3 classes per dim (AU, NZ, OTHER) x (BROKER, DIRECT, OTHER) = 9 > 4
        with pytest.raises(ValueError, match="max_witnesses"):
            engine.index(lattices, max_witnesses=4)

    def test_bool_key_dim_full_domain_validates(self):
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="flag",
                match_strategy=MatchStrategy.EXACT,
                data_type="bool",
                role=DimensionRole.CONTEXT_KEY,
            ),
            Dimension(dimension_name="product", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        rules = pl.DataFrame({
            "flag": [True, False, None],   # null rule value = bool wildcard
            "rule_name": ["r0", "r1", "r2"],
            "product": ["GOLD"] * 3,
            "margin": [1.0] * 3,
        })
        index = engine.index(engine.build_all(rules))  # OTHER = None, no raise
        result = index.apply({"product": "GOLD"})      # flag missing -> wildcard
        assert result.count >= 1


class TestRoutingPersistence:
    def test_saved_wildcard_suite_routes_after_load(self, tmp_path):
        engine = _routing_engine()
        built = engine.build_all(
            _routing_rules([("AU", "BROKER"), (UNKNOWN, UNKNOWN)])
        )
        loaded = [
            Lattice.load(lattice.save(tmp_path / f"part{i}"))
            for i, lattice in enumerate(built)
        ]
        index = engine.index(loaded)
        result = index.apply(
            RoutingContext(region="NZ", channel="DIRECT", product="GOLD")
        )
        assert result.count >= 1  # sentinel key survived the round-trip
