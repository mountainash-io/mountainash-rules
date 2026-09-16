"""Cross-backend tests for AccumulatorEngine apply phase."""

import polars as pl
import pytest
from pydantic import BaseModel
from mountainash.relations import relation
from mountainash.core.types import BackendCapabilityError

from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import (
    DimensionRole,
    MatchStrategy,
    UNKNOWN,
    UNKNOWN_NUMERIC,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.accumulator.lattice import Lattice
from tests.conftest import ALL_BACKENDS, build_backend_df


class PricingContext(BaseModel):
    channel: str
    lvr: int
    foreign_resident: str


def _worked_example_metadata():
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT),
            Dimension(
                dimension_name="lvr",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field="lvr_min",
                range_max_field="lvr_max",
            ),
            Dimension(
                dimension_name="foreign_resident", match_strategy=MatchStrategy.EXACT
            ),
        ]
    )


def _worked_example_engine():
    return AccumulatorEngine(
        dimension_metadata=_worked_example_metadata(),
        aggregates=[Aggregate(column_name="margin")],
    )


def _worked_example_rules():
    return pl.DataFrame(
        {
            "rule_name": ["R1", "R2", "R3"],
            "channel": ["BROKER", UNKNOWN, "BROKER"],
            "lvr_min": [60, 70, UNKNOWN_NUMERIC],
            "lvr_max": [80, 90, UNKNOWN_NUMERIC],
            "foreign_resident": [UNKNOWN, "false", "false"],
            "margin": [-0.10, -0.05, -0.15],
        }
    )


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
        lattice = _build_lattice_in_backend(
            engine, _worked_example_rules(), apply_backend
        )
        context = PricingContext(channel="BROKER", lvr=75, foreign_resident="false")
        result = engine.apply(lattice, context)
        assert result.count == 6

    def test_apply_accumulated_margin(self, apply_backend):
        engine = _worked_example_engine()
        lattice = _build_lattice_in_backend(
            engine, _worked_example_rules(), apply_backend
        )
        context = PricingContext(channel="BROKER", lvr=75, foreign_resident="false")
        result = engine.apply(lattice, context)
        rows = relation(result.survivors).to_dict()
        margins = dict(zip(rows["__prime_product"], rows["__agg_margin"]))
        assert margins[30] == pytest.approx(-0.30)

    def test_apply_partial_match(self, apply_backend):
        engine = _worked_example_engine()
        lattice = _build_lattice_in_backend(
            engine, _worked_example_rules(), apply_backend
        )
        context = PricingContext(channel="DIRECT", lvr=75, foreign_resident="false")
        result = engine.apply(lattice, context)
        # DIRECT doesn't match BROKER — only wildcards survive
        assert result.count < 6


def _boolean_backend_engine(*, boolean_coercion):
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="flag",
                match_strategy=MatchStrategy.EXACT,
                data_type="bool",
                role=DimensionRole.CONTEXT_KEY,
            ),
            Dimension(
                dimension_name="approved",
                match_strategy=MatchStrategy.EXACT,
                data_type="bool",
            ),
        ]
    )
    return AccumulatorEngine(
        dimension_metadata=metadata,
        aggregates=[Aggregate(column_name="margin")],
        boolean_coercion=boolean_coercion,
    )


def _boolean_backend_rules():
    return pl.DataFrame(
        {
            "flag": [True, False, None],
            "approved": [True, False, None],
            "rule_name": ["enabled", "disabled", "default"],
            "margin": [10.0, 20.0, 30.0],
        }
    )


def _boolean_backend_values(result):
    return sorted(relation(result.accumulated("margin")).to_dict()["__agg_margin"])


class TestBooleanNativeContexts:
    @pytest.mark.parametrize(
        "context_backend",
        [
            "polars",
            "pandas",
            "narwhals-polars",
            "narwhals-pandas",
            "ibis-duckdb",
            "ibis-sqlite",
        ],
    )
    def test_index_accepts_native_boolean_context_frame(self, context_backend):
        from mountainash_rules import BooleanCoercion

        engine = _boolean_backend_engine(boolean_coercion=BooleanCoercion.NONE)
        index = engine.index(engine.build_all(_boolean_backend_rules()))
        contexts = build_backend_df(
            context_backend,
            {
                "source_id": [100, 200, 300],
                "flag": [True, False, None],
                "approved": [True, False, None],
            },
            table_name=f"native_boolean_context_{context_backend}",
        )

        result = index.apply_batch(contexts, context_id_field="source_id")

        assert relation(result.survivors).to_polars().sort(
            "__context_id", "__agg_margin"
        ).select(
            "__context_id", "__agg_margin", "__t_approved", "__specificity"
        ).rows() == [
            (100, 10.0, 1, 1),
            (200, 20.0, 1, 1),
            (300, 10.0, 0, 0),
            (300, 20.0, 0, 0),
            (300, 30.0, 0, 0),
        ]

    @pytest.mark.parametrize(
        "lattice_backend",
        [
            "polars",
            "pandas",
            "narwhals-polars",
            "narwhals-pandas",
            "ibis-duckdb",
            "ibis-sqlite",
        ],
    )
    def test_converted_lattice_apply_accepts_normalized_boolean_input(
        self, lattice_backend
    ):
        from mountainash_rules import BooleanCoercion

        metadata = DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name="flag",
                    match_strategy=MatchStrategy.EXACT,
                    data_type="bool",
                ),
            ]
        )
        engine = AccumulatorEngine(
            dimension_metadata=metadata,
            aggregates=[Aggregate(column_name="margin")],
            boolean_coercion=BooleanCoercion.BINARY_NUMBERS,
        )
        rules = pl.DataFrame(
            {
                "flag": [True, False],
                "rule_name": ["enabled", "disabled"],
                "margin": [10.0, 20.0],
            }
        )
        lattice = _build_lattice_in_backend(engine, rules, lattice_backend)

        assert _boolean_backend_values(engine.apply(lattice, {"flag": 0})) == [20.0]

    @pytest.mark.parametrize(
        "context_backend",
        ["polars", "pandas", "narwhals-polars", "narwhals-pandas"],
    )
    def test_mixed_object_contexts_keep_original_boolean_domains(self, context_backend):
        from mountainash_rules import BooleanCoercion
        import narwhals as nw
        import pandas as pd

        engine = _boolean_backend_engine(
            boolean_coercion=(
                BooleanCoercion.BOOLEAN_TEXT | BooleanCoercion.NUMERIC_TRUTHINESS
            )
        )
        index = engine.index(engine.build_all(_boolean_backend_rules()))
        values = [True, 0, " FALSE ", 2]
        data = {
            "source_id": [11, 12, 13, 14],
            "flag": values,
            "approved": [True, False, False, True],
        }
        if context_backend == "polars":
            contexts = pl.DataFrame(
                {
                    **{key: value for key, value in data.items() if key != "flag"},
                    "flag": pl.Series("flag", values, dtype=pl.Object),
                }
            )
        elif context_backend == "pandas":
            contexts = pd.DataFrame(data).astype({"flag": "object"})
        elif context_backend == "narwhals-polars":
            contexts = nw.from_native(
                pl.DataFrame(
                    {
                        **{key: value for key, value in data.items() if key != "flag"},
                        "flag": pl.Series("flag", values, dtype=pl.Object),
                    }
                )
            )
        else:
            contexts = nw.from_native(
                pd.DataFrame(data).astype({"flag": "object"}),
                eager_only=True,
            )

        result = index.apply_batch(contexts, context_id_field="source_id")

        assert relation(result.survivors).to_polars().sort("__context_id").select(
            "__context_id", "__agg_margin", "__t_approved", "__specificity"
        ).rows() == [
            (11, 10.0, 1, 1),
            (12, 20.0, 1, 1),
            (13, 20.0, 1, 1),
            (14, 10.0, 1, 1),
        ]


@pytest.fixture
def identity_lattices():
    from mountainash_rules import (
        AccumulatorEngine,
        Aggregate,
        Dimension,
        DimensionRole,
        DimensionsMetadata,
    )

    engine = AccumulatorEngine(
        dimension_metadata=DimensionsMetadata(
            dimensions=[
                Dimension(dimension_name="tenant", role=DimensionRole.CONTEXT_KEY),
                Dimension(dimension_name="region"),
            ]
        ),
        aggregates=[Aggregate(column_name="margin")],
    )
    rules = pl.DataFrame(
        {
            "rule_name": ["a-au", "a-nz", "b-au", "b-nz"],
            "tenant": ["A", "A", "B", "B"],
            "region": ["AU", "NZ", "AU", "NZ"],
            "margin": [10.0, 11.0, 20.0, 21.0],
        }
    )
    return engine, engine.build_all(rules)


_EXECUTABLE_IDENTITY_BACKENDS = [name for name in ALL_BACKENDS if name != "ibis-polars"]


def _native_schema(frame):
    """Inspect native types without inferring types from empty Python rows."""
    if hasattr(frame, "schema"):
        schema = frame.schema
        return dict(schema() if callable(schema) else schema)
    return dict(frame.dtypes)


@pytest.mark.parametrize("context_backend", _EXECUTABLE_IDENTITY_BACKENDS)
@pytest.mark.parametrize("ids", [None, [51, 29, 73], ["z", "a", "m"]])
def test_partition_native_context_identity(identity_lattices, context_backend, ids):
    engine, lattices = identity_lattices
    index = engine.index(lattices)
    data = {
        "position": [0, 1, 2],
        "tenant": ["A", "B", "A"],
        "region": ["AU", "XX", "NZ"],
    }
    if ids is not None:
        data["request_id"] = ids
    contexts = build_backend_df(context_backend, data, table_name="identity_contexts")
    if context_backend.startswith("ibis-"):
        contexts = contexts.order_by("position")
    original = relation(contexts).to_dict()
    result = index.apply_batch(
        contexts, context_id_field=None if ids is None else "request_id", chunk_size=1
    )
    submitted = [0, 1, 2] if ids is None else ids
    assert result.matched_context_ids == sorted([submitted[0], submitted[2]])
    assert result.unmatched_context_ids(contexts) == [submitted[1]]
    for cid, expected in zip(submitted, [[10.0], [], [11.0]]):
        assert (
            relation(result.for_context(cid).survivors).to_dict()["__agg_margin"]
            == expected
        )
    assert relation(contexts).to_dict() == original


@pytest.mark.parametrize("context_backend", _EXECUTABLE_IDENTITY_BACKENDS)
def test_partition_native_invalid_identity(identity_lattices, context_backend):
    engine, lattices = identity_lattices
    index = engine.index(lattices)
    for ids in [[7, 7], [7, None]]:
        contexts = build_backend_df(
            context_backend,
            {
                "request_id": ids,
                "tenant": ["A", "B"],
                "region": ["AU", "XX"],
            },
            table_name="invalid_identity",
        )
        with pytest.raises(ValueError):
            index.apply_batch(contexts, context_id_field="request_id", chunk_size=1)


@pytest.mark.parametrize("lattice_backend", _EXECUTABLE_IDENTITY_BACKENDS)
def test_partition_apply_representation_identity(identity_lattices, lattice_backend):
    from mountainash_rules import Lattice, UNKNOWN
    import ibis

    engine, lattices = identity_lattices
    connection = None
    if lattice_backend == "ibis-duckdb":
        connection = ibis.duckdb.connect()
    elif lattice_backend == "ibis-sqlite":
        connection = ibis.sqlite.connect(":memory:")
    try:
        converted = []
        for position, lattice in enumerate(lattices):
            data = relation(lattice.combinations).to_dict()
            name = f"identity_partition_{position}"
            frame = (
                connection.create_table(name, data)
                if connection is not None
                else build_backend_df(lattice_backend, data, table_name=name)
            )
            converted.append(
                Lattice(
                    dataframe=frame,
                    metadata=lattice.metadata,
                    aggregates=lattice.aggregates,
                    partition_key=lattice.partition_key,
                )
            )
        for ids in [None, [30, 10, 20], ["z", "a", "m"]]:
            contexts = pl.DataFrame(
                {
                    "tenant": ["B", "A", "B"],
                    "region": [UNKNOWN] * 3,
                    **({} if ids is None else {"request_id": ids}),
                }
            )
            original = contexts.clone()
            submitted = [0, 1, 2] if ids is None else ids
            expected = sorted(
                (cid, rank, margin)
                for cid, margins in zip(
                    submitted, [[20.0, 21.0], [10.0, 11.0], [20.0, 21.0]]
                )
                for rank, margin in enumerate(margins, 1)
            )
            field = None if ids is None else "request_id"
            for reverse in [False, True]:
                index = engine.index(
                    list(reversed(converted)) if reverse else converted
                )
                for chunk in [None, 1, 2]:
                    result = index.apply_batch(
                        contexts, context_id_field=field, chunk_size=chunk
                    )
                    rows = relation(result.survivors).to_dict()
                    assert (
                        list(
                            zip(
                                rows["__context_id"],
                                rows["__rank"],
                                rows["__agg_margin"],
                            )
                        )
                        == expected
                    )
                    assert result.unmatched_context_ids(contexts) == []
                    for cid, margins in zip(
                        submitted, [[20.0, 21.0], [10.0, 11.0], [20.0, 21.0]]
                    ):
                        view = result.for_context(cid)
                        assert (
                            relation(view.survivors).to_dict()["__agg_margin"]
                            == margins
                        )
                        with pytest.raises(ValueError):
                            view.select("first")
            for options in [{"min_specificity": 1}, {"top_n_per_context": 0}]:
                empty = index.apply_batch(
                    contexts, context_id_field=field, chunk_size=2, **options
                )
                assert empty.count == 0
                assert empty.unmatched_context_ids(contexts) == sorted(submitted)
                assert _native_schema(empty.survivors) == _native_schema(
                    result.survivors
                )
                view = empty.for_context(submitted[0])
                assert _native_schema(view.survivors) == _native_schema(
                    result.for_context(submitted[0]).survivors
                )
                with pytest.raises(ValueError):
                    view.select("collect")
            assert contexts.equals(original)
    finally:
        if connection is not None:
            connection.disconnect()
