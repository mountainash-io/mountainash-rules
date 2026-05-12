"""Tests for AccumulatorResult."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.accumulator_result import AccumulatorResult
from mountainash_rules.aggregate import Aggregate
from mountainash_rules.lattice import Lattice
from mountainash_rules.constants import MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata


def _make_result():
    """Build an AccumulatorResult from hand-crafted data."""
    df = pl.DataFrame({
        "rule_name": ["{R1,R2,R3}", "{R1,R3}", "{R2,R3}"],
        "__t_channel": [1, 1, 1],
        "__t_lvr": [1, 1, 1],
        "__specificity": [2, 2, 2],
        "__rank": [1, 2, 3],
        "__agg_margin": [-0.30, -0.25, -0.20],
        "__prime_product": [30, 10, 15],
        "__level": [2, 1, 1],
    })
    metadata = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
    ])
    aggregates = [Aggregate(column_name="margin")]
    lattice = Lattice(dataframe=df, metadata=metadata, aggregates=aggregates, partition_key=None)
    return AccumulatorResult(
        dataframe=df,
        active_dimensions=["channel", "lvr"],
        aggregates=aggregates,
        lattice=lattice,
    )


class TestAccumulatorResult:
    def test_count(self):
        result = _make_result()
        assert result.count == 3

    def test_best_combination_delegates_to_best_match(self):
        result = _make_result()
        best = relation(result.best_combination).to_dict()
        assert best["rule_name"][0] == "{R1,R2,R3}"

    def test_accumulated_returns_values(self):
        result = _make_result()
        acc = relation(result.accumulated("margin")).to_dict()
        assert acc["__agg_margin"] == [-0.30, -0.25, -0.20]

    def test_provenance(self):
        result = _make_result()
        prov = relation(result.provenance).to_dict()
        assert prov["__prime_product"] == [30, 10, 15]

    def test_depths(self):
        result = _make_result()
        d = relation(result.depths).to_dict()
        assert d["__level"] == [2, 1, 1]

    def test_inherits_survivors(self):
        result = _make_result()
        rows = relation(result.survivors).to_dict()
        assert len(rows["rule_name"]) == 3
