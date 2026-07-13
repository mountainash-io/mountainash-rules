"""Tests for AccumulatorEngine build phase."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy, DimensionRole
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


def _rows(df) -> dict:
    return relation(df).to_dict()


def _worked_example_rules():
    """The 3-rule example from the architecture analysis."""
    return pl.DataFrame({
        "rule_name": ["R1", "R2", "R3"],
        "channel": ["BROKER", UNKNOWN, "BROKER"],
        "lvr_min": [60, 70, UNKNOWN_NUMERIC],
        "lvr_max": [80, 90, UNKNOWN_NUMERIC],
        "foreign_resident": [UNKNOWN, "false", "false"],
        "margin": [-0.10, -0.05, -0.15],
    })


def _worked_example_metadata():
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        ),
        Dimension(dimension_name="foreign_resident", match_strategy=MatchStrategy.EXACT),
    ])


def _worked_example_engine():
    return AccumulatorEngine(
        dimension_metadata=_worked_example_metadata(),
        aggregates=[Aggregate(column_name="margin")],
    )


class TestBuildWorkedExample:
    def test_lattice_has_six_outermost_rulesets(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        assert lattice.count == 6

    def test_deepest_combination_has_correct_margin(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        rows = _rows(lattice.combinations)
        margins = dict(zip(rows["__prime_product"], rows["__agg_margin"]))
        assert margins[30] == pytest.approx(-0.30)

    def test_singleton_combinations_present(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        rows = _rows(lattice.combinations)
        primes = set(rows["__prime_product"])
        assert {2, 3, 5}.issubset(primes)

    def test_pair_combinations_present(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        rows = _rows(lattice.combinations)
        primes = set(rows["__prime_product"])
        assert {10, 15}.issubset(primes)

    def test_dominated_pair_removed(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        rows = _rows(lattice.combinations)
        primes = set(rows["__prime_product"])
        assert 6 not in primes

    def test_levels_correct(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        rows = _rows(lattice.combinations)
        pp_to_level = dict(zip(rows["__prime_product"], rows["__level"]))
        assert pp_to_level[2] == 0
        assert pp_to_level[30] == 2


class TestBuildEdgeCases:
    def test_single_rule_produces_one_combination(self):
        rules = pl.DataFrame({
            "rule_name": ["only"],
            "channel": ["BROKER"],
            "margin": [1.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        assert lattice.count == 1

    def test_incompatible_rules_no_combinations(self):
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2"],
            "channel": ["BROKER", "DIRECT"],
            "margin": [1.0, 2.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        assert lattice.count == 2

    def test_all_wildcard_rules_combine_fully(self):
        rules = pl.DataFrame({
            "rule_name": ["r1", "r2", "r3"],
            "channel": [UNKNOWN, UNKNOWN, UNKNOWN],
            "margin": [1.0, 2.0, 3.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules)
        assert lattice.count == 1
        rows = _rows(lattice.combinations)
        assert rows["__agg_margin"][0] == pytest.approx(6.0)


class TestBuildWithPartitionKey:
    def test_partition_filters_rules(self):
        rules = pl.DataFrame({
            "product_id": [1, 1, 2],
            "rule_name": ["r1", "r2", "r3"],
            "channel": ["BROKER", UNKNOWN, "BROKER"],
            "margin": [1.0, 2.0, 3.0],
        })
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="product_id",
                match_strategy=MatchStrategy.EXACT,
                data_type=int,
                role=DimensionRole.CONTEXT_KEY,
            ),
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        lattice = engine.build(rules, partition_key={"product_id": 1})
        assert lattice.partition_key == {"product_id": 1}
        rows = _rows(lattice.combinations)
        primes = set(rows["__prime_product"])
        assert max(primes) <= 6

    def test_missing_partition_key_raises(self):
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="product_id",
                match_strategy=MatchStrategy.EXACT,
                role=DimensionRole.CONTEXT_KEY,
            ),
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        ])
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
        )
        rules = pl.DataFrame({
            "product_id": [1], "channel": ["X"], "margin": [1.0],
        })
        with pytest.raises(ValueError, match="partition_key"):
            engine.build(rules)
