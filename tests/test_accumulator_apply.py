"""Tests for AccumulatorEngine apply phase and end-to-end integration."""

import polars as pl
import pytest
from pydantic import BaseModel
from mountainash.relations import relation

from mountainash_rules.accumulator_engine import AccumulatorEngine
from mountainash_rules.accumulator_result import AccumulatorResult
from mountainash_rules.aggregate import Aggregate
from mountainash_rules.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy, DimensionRole
from mountainash_rules.dimension import Dimension, DimensionsMetadata


def _rows(df) -> dict:
    return relation(df).to_dict()


class PricingContext(BaseModel):
    channel: str
    lvr: int
    foreign_resident: str


class PartitionedContext(BaseModel):
    product_id: int
    channel: str


def _worked_example_engine():
    metadata = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
        Dimension(
            dimension_name="lvr", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="lvr_min", range_max_field="lvr_max",
        ),
        Dimension(dimension_name="foreign_resident", match_strategy=MatchStrategy.EXACT),
    ])
    return AccumulatorEngine(
        dimension_metadata=metadata,
        aggregates=[Aggregate(column_name="margin")],
    )


def _worked_example_rules():
    return pl.DataFrame({
        "rule_name": ["R1", "R2", "R3"],
        "channel": ["BROKER", UNKNOWN, "BROKER"],
        "lvr_min": [60, 70, UNKNOWN_NUMERIC],
        "lvr_max": [80, 90, UNKNOWN_NUMERIC],
        "foreign_resident": [UNKNOWN, "false", "false"],
        "margin": [-0.10, -0.05, -0.15],
    })


class TestApplyWorkedExample:
    def test_returns_accumulator_result(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        result = engine.apply(lattice, PricingContext(channel="BROKER", lvr=75, foreign_resident="false"))
        assert isinstance(result, AccumulatorResult)

    def test_all_outermost_match_full_context(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        result = engine.apply(lattice, PricingContext(channel="BROKER", lvr=75, foreign_resident="false"))
        assert result.count == 6

    def test_best_combination_has_max_specificity(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        result = engine.apply(lattice, PricingContext(channel="BROKER", lvr=75, foreign_resident="false"))
        best = _rows(result.best_combination)
        # Best combination has the highest specificity (3 — all dimensions matched)
        assert best["__specificity"][0] == 3

    def test_deepest_combination_is_triple(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        result = engine.apply(lattice, PricingContext(channel="BROKER", lvr=75, foreign_resident="false"))
        rows = _rows(result.survivors)
        pp_to_margin = dict(zip(rows["__prime_product"], rows["__agg_margin"]))
        # The triple (R1+R2+R3, prime_product=30) has accumulated margin of -0.30
        assert pp_to_margin[30] == pytest.approx(-0.30)

    def test_partial_context_fewer_matches(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        result = engine.apply(lattice, PricingContext(channel="BROKER", lvr=95, foreign_resident="false"))
        assert result.count < 6

    def test_accumulated_margin_values(self):
        engine = _worked_example_engine()
        lattice = engine.build(_worked_example_rules())
        result = engine.apply(lattice, PricingContext(channel="BROKER", lvr=75, foreign_resident="false"))
        acc = _rows(result.accumulated("margin"))
        margins = sorted(acc["__agg_margin"])
        assert pytest.approx(-0.30) in margins
        assert pytest.approx(-0.05) in margins


class TestApplyAutoWithPartitions:
    def test_apply_auto_selects_correct_lattice(self):
        rules = pl.DataFrame({
            "product_id": [1, 1, 2],
            "rule_name": ["r1", "r2", "r3"],
            "channel": ["BROKER", UNKNOWN, "DIRECT"],
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
        lattices = engine.build_all(rules)
        assert len(lattices) == 2

        result = engine.apply_auto(
            lattices,
            PartitionedContext(product_id=1, channel="BROKER"),
        )
        assert isinstance(result, AccumulatorResult)
        assert result.count >= 1

    def test_apply_auto_missing_key_raises(self):
        rules = pl.DataFrame({
            "product_id": [1],
            "rule_name": ["r1"],
            "channel": ["BROKER"],
            "margin": [1.0],
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
        lattices = engine.build_all(rules)

        with pytest.raises(KeyError, match="No lattice"):
            engine.apply_auto(lattices, PartitionedContext(product_id=99, channel="X"))
