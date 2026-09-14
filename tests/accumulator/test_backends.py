"""Cross-backend tests for AccumulatorEngine apply phase."""

import polars as pl
import pytest
from pydantic import BaseModel
from mountainash.relations import relation
from mountainash.core.types import BackendCapabilityError

from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import UNKNOWN, UNKNOWN_NUMERIC, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.accumulator.lattice import Lattice
from tests.conftest import ALL_BACKENDS, build_backend_df


class PricingContext(BaseModel):
    channel: str
    lvr: int
    foreign_resident: str


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


def _worked_example_rules():
    return pl.DataFrame({
        "rule_name": ["R1", "R2", "R3"],
        "channel": ["BROKER", UNKNOWN, "BROKER"],
        "lvr_min": [60, 70, UNKNOWN_NUMERIC],
        "lvr_max": [80, 90, UNKNOWN_NUMERIC],
        "foreign_resident": [UNKNOWN, "false", "false"],
        "margin": [-0.10, -0.05, -0.15],
    })


def _build_lattice_in_backend(engine, rules, backend_name):
    """Build with polars, then convert lattice DataFrame to target backend."""
    lattice = engine.build(rules)
    lattice_data = relation(lattice.combinations).to_dict()
    backend_df = build_backend_df(backend_name, lattice_data, table_name="lattice")
    return Lattice(
        dataframe=backend_df,
        metadata=lattice._metadata,
        aggregates=lattice._aggregates,
        partition_key=lattice.partition_key,
    )


@pytest.fixture(params=ALL_BACKENDS)
def apply_backend(request):
    return request.param


class TestApplyCrossBackend:
    """Verify the apply phase works across all 7 backends."""

    def test_apply_reports_unsupported_row_index(self):
        engine = _worked_example_engine()
        lattice = _build_lattice_in_backend(
            engine, _worked_example_rules(), "ibis-polars"
        )
        context = PricingContext(channel="BROKER", lvr=75, foreign_resident="false")
        with pytest.raises(BackendCapabilityError) as error:
            engine.apply(lattice, context)
        assert error.value.backend == "ibis"

    def test_apply_correct_count(self, apply_backend):
        engine = _worked_example_engine()
        lattice = _build_lattice_in_backend(engine, _worked_example_rules(), apply_backend)
        context = PricingContext(channel="BROKER", lvr=75, foreign_resident="false")
        result = engine.apply(lattice, context)
        assert result.count == 6

    def test_apply_accumulated_margin(self, apply_backend):
        engine = _worked_example_engine()
        lattice = _build_lattice_in_backend(engine, _worked_example_rules(), apply_backend)
        context = PricingContext(channel="BROKER", lvr=75, foreign_resident="false")
        result = engine.apply(lattice, context)
        rows = relation(result.survivors).to_dict()
        margins = dict(zip(rows["__prime_product"], rows["__agg_margin"]))
        assert margins[30] == pytest.approx(-0.30)

    def test_apply_partial_match(self, apply_backend):
        engine = _worked_example_engine()
        lattice = _build_lattice_in_backend(engine, _worked_example_rules(), apply_backend)
        context = PricingContext(channel="DIRECT", lvr=75, foreign_resident="false")
        result = engine.apply(lattice, context)
        # DIRECT doesn't match BROKER — only wildcards survive
        assert result.count < 6
