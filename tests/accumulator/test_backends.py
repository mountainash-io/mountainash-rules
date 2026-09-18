"""Native-backend admission and exact runtime batch contracts."""

import polars as pl
import pytest

import mountainash_rules as rules
from tests.accumulator.exact_runtime_fixtures import declarations, gate, uuid
from tests.conftest import ALL_BACKENDS, build_backend_df


TOTAL = rules.Aggregate(
    column_name="amount",
    output_name="pricing.total",
    data_type="float",
    numeric_semantics="numeric-1",
)


def _columns(rows):
    names = set().union(*(row.keys() for row in rows))
    return {name: [row.get(name) for row in rows] for name in sorted(names)}


def _candidate_contract(
    dimensions, outputs, *, contract_id="client", profile_id="inspect", masks=()
):
    return rules.ContextContract(
        schema_version=1,
        contract_id=contract_id,
        domain_ref="D",
        fields=[
            rules.ContextField(
                name=dimension.resolved_context_field,
                data_type=dimension.data_type,
                required=False,
            )
            for dimension in sorted(dimensions, key=lambda item: item.dimension_name)
        ],
        profiles=[
            rules.ResolutionProfile(
                profile_id=profile_id,
                mode="candidates",
                output_fields=sorted(outputs),
                provenance="none",
                dimensions=sorted(
                    dimension.dimension_name
                    for dimension in dimensions
                    if dimension.role == rules.DimensionRole.CONSTRAINT
                    and dimension.match_strategy != rules.MatchStrategy.CONTEXT_REGEX
                ),
                allow_dont_care=sorted(masks),
                promise="candidate_only",
            )
        ],
    )


def _build_native(
    rows,
    dimensions,
    aggregates,
    source_backend,
    *,
    decisions=(),
    partition_keys=((),),
    all_partitions=False,
):
    contract = _candidate_contract(
        dimensions, [aggregate.output_name for aggregate in aggregates]
    )
    kwargs = declarations(
        dimensions,
        aggregates,
        contracts=[contract],
        partition_keys=partition_keys,
    )
    validation = gate(rows, kwargs, decisions=decisions)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    source = build_backend_df(
        source_backend,
        _columns(rows),
        table_name=f"exact_sources_{source_backend}",
    )
    if all_partitions:
        return engine, engine.build_all(source, validation=validation)
    return engine, engine.build(source, validation=validation)


def _candidate_facts(result, output):
    values = {row["cell_id"]: row[output] for row in result.candidate_cells.to_dicts()}
    contributors = {}
    for row in result.candidate_contributors.to_dicts():
        contributors.setdefault(row["cell_id"], set()).add(row["source_id"])
    return sorted(
        (values[cell_id], tuple(sorted(source_ids)))
        for cell_id, source_ids in contributors.items()
    )


def _worked_example():
    dimensions = [
        rules.Dimension(dimension_name="channel"),
        rules.Dimension(
            dimension_name="lvr",
            data_type="int",
            match_strategy="range",
            range_min_field="lvr_min",
            range_max_field="lvr_max",
        ),
        rules.Dimension(dimension_name="foreign_resident"),
    ]
    rows = [
        {
            "id": uuid(1),
            "channel": "BROKER",
            "lvr_min": 60,
            "lvr_max": 80,
            "foreign_resident": rules.UNKNOWN,
            "amount": -0.10,
        },
        {
            "id": uuid(2),
            "channel": rules.UNKNOWN,
            "lvr_min": 70,
            "lvr_max": 90,
            "foreign_resident": "false",
            "amount": -0.05,
        },
        {
            "id": uuid(3),
            "channel": "BROKER",
            "lvr_min": rules.UNKNOWN_NUMERIC,
            "lvr_max": rules.UNKNOWN_NUMERIC,
            "foreign_resident": "false",
            "amount": -0.15,
        },
    ]
    decisions = [
        ("source_overlap", (uuid(left), uuid(right)))
        for left, right in ((1, 2), (1, 3), (2, 3))
    ]
    return rows, dimensions, decisions


@pytest.mark.parametrize("source_backend", ALL_BACKENDS)
def test_each_native_source_backend_builds_a_real_exact_artifact(source_backend):
    rows, dimensions, decisions = _worked_example()
    engine, lattice = _build_native(
        rows,
        dimensions,
        [TOTAL],
        source_backend,
        decisions=decisions,
    )

    result = engine.apply(
        lattice,
        {"channel": "BROKER", "lvr": 75, "foreign_resident": "false"},
        contract_id="client",
        profile_id="inspect",
    )

    assert lattice.artifact_kind == "exact_cells"
    assert _candidate_facts(result, "pricing.total") == [
        (pytest.approx(-0.30), (uuid(1), uuid(2), uuid(3))),
    ]


def _boolean_artifact():
    dimensions = [
        rules.Dimension(
            dimension_name="flag",
            data_type="bool",
            match_strategy="exact_key",
            role=rules.DimensionRole.CONTEXT_KEY,
        ),
        rules.Dimension(
            dimension_name="approved",
            data_type="bool",
            match_strategy="exact",
        ),
    ]
    aggregate = rules.Aggregate(
        column_name="margin",
        output_name="pricing.margin",
        data_type="float",
        numeric_semantics="numeric-1",
    )
    rows = [
        {"id": uuid(11), "flag": True, "approved": True, "margin": 10.0},
        {"id": uuid(12), "flag": False, "approved": False, "margin": 20.0},
        {"id": uuid(13), "flag": None, "approved": None, "margin": 30.0},
    ]
    contract = _candidate_contract(
        dimensions,
        [aggregate.output_name],
        masks=("approved",),
    )
    kwargs = declarations(
        dimensions,
        [aggregate],
        contracts=[contract],
        partition_keys=((True,), (False,), (None,)),
    )
    validation = gate(rows, kwargs)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    return engine, engine.build_all(rows, validation=validation)


@pytest.mark.parametrize("context_backend", ALL_BACKENDS)
def test_index_accepts_native_boolean_contexts(context_backend):
    engine, lattices = _boolean_artifact()
    index = engine.index(lattices)
    contexts = build_backend_df(
        context_backend,
        {
            "request_id": [100, 200],
            "flag": [True, False],
            "approved": [True, False],
        },
        table_name=f"native_boolean_context_{context_backend}",
    )

    batch = index.apply_batch(
        contexts,
        contract_id="client",
        profile_id="inspect",
        context_id_field="request_id",
    )

    assert list(batch.records) == [100, 200]
    assert _candidate_facts(batch.for_context(100), "pricing.margin") == [
        (10.0, (uuid(11),)),
    ]
    assert _candidate_facts(batch.for_context(200), "pricing.margin") == [
        (20.0, (uuid(12),)),
    ]


@pytest.mark.parametrize(
    "context_backend",
    ["polars", "pandas", "narwhals-polars", "narwhals-pandas"],
)
def test_mixed_boolean_carriers_keep_valid_siblings_and_reject_raw_invalid_values(
    context_backend,
):
    import narwhals as nw
    import pandas as pd

    engine, lattices = _boolean_artifact()
    index = engine.index(lattices)
    values = [True, 0, " FALSE ", 2]
    data = {
        "request_id": [11, 12, 13, 14],
        "flag": values,
        "approved": [True, False, False, True],
        "mask": [["approved"]] * 4,
    }
    if context_backend == "polars":
        contexts = pl.DataFrame(
            {
                **{name: value for name, value in data.items() if name != "flag"},
                "flag": pl.Series("flag", values, dtype=pl.Object),
            }
        )
    elif context_backend == "pandas":
        contexts = pd.DataFrame(data).astype({"flag": "object"})
    elif context_backend == "narwhals-polars":
        contexts = nw.from_native(
            pl.DataFrame(
                {
                    **{name: value for name, value in data.items() if name != "flag"},
                    "flag": pl.Series("flag", values, dtype=pl.Object),
                }
            )
        )
    else:
        contexts = nw.from_native(
            pd.DataFrame(data).astype({"flag": "object"}), eager_only=True
        )

    batch = index.apply_batch(
        contexts,
        contract_id="client",
        profile_id="inspect",
        context_id_field="request_id",
        dont_care_field="mask",
        chunk_size=1,
    )

    assert batch.records[11].status == "candidates"
    for context_id in (12, 13, 14):
        assert batch.records[context_id].status == "invalid_context"
        with pytest.raises(rules.InvalidContextError) as failure:
            batch.for_context(context_id)
        assert failure.value.outcome is batch.records[context_id]
        assert failure.value.result.outcome is batch.records[context_id]


def _identity_artifact():
    dimensions = [
        rules.Dimension(
            dimension_name="tenant",
            data_type="str",
            match_strategy="exact_key",
            role=rules.DimensionRole.CONTEXT_KEY,
        ),
        rules.Dimension(dimension_name="region"),
    ]
    aggregate = rules.Aggregate(
        column_name="margin",
        output_name="pricing.margin",
        data_type="float",
        numeric_semantics="numeric-1",
    )
    rows = [
        {"id": uuid(21), "tenant": "A", "region": "AU", "margin": 10.0},
        {"id": uuid(22), "tenant": "A", "region": "NZ", "margin": 11.0},
        {"id": uuid(23), "tenant": "B", "region": "AU", "margin": 20.0},
        {"id": uuid(24), "tenant": "B", "region": "NZ", "margin": 21.0},
    ]
    return _build_native(
        rows,
        dimensions,
        [aggregate],
        "polars",
        partition_keys=(("A",), ("B",), (None,)),
        all_partitions=True,
    )


@pytest.mark.parametrize("context_backend", ALL_BACKENDS)
@pytest.mark.parametrize("ids", [None, [51, 29, 73], ["z", "a", "m"]])
def test_partition_batch_preserves_generated_and_custom_native_id_order(
    context_backend, ids
):
    engine, lattices = _identity_artifact()
    index = engine.index(lattices)
    data = {
        "position": [0, 1, 2],
        "tenant": ["A", "B", "A"],
        "region": ["AU", "NZ", "NZ"],
    }
    if ids is not None:
        data["request_id"] = ids
    contexts = build_backend_df(
        context_backend, data, table_name=f"identity_context_{context_backend}"
    )
    if context_backend.startswith("ibis-"):
        contexts = contexts.order_by("position")

    batch = index.apply_batch(
        contexts,
        contract_id="client",
        profile_id="inspect",
        context_id_field=None if ids is None else "request_id",
        chunk_size=1,
    )
    expected_ids = [0, 1, 2] if ids is None else ids

    assert list(batch.records) == expected_ids
    for context_id, margin, source_id, context in zip(
        expected_ids,
        [10.0, 21.0, 11.0],
        [uuid(21), uuid(24), uuid(22)],
        [
            {"tenant": "A", "region": "AU"},
            {"tenant": "B", "region": "NZ"},
            {"tenant": "A", "region": "NZ"},
        ],
        strict=True,
    ):
        view = batch.for_context(context_id)
        direct = index.apply(context, contract_id="client", profile_id="inspect")
        assert view.outcome == batch.records[context_id] == direct.outcome
        assert _candidate_facts(view, "pricing.margin") == [(margin, (source_id,))]


@pytest.mark.parametrize("context_backend", ALL_BACKENDS)
def test_partition_batch_rejects_duplicate_or_null_native_ids(context_backend):
    engine, lattices = _identity_artifact()
    index = engine.index(lattices)
    for ids in ([7, 7], [7, None]):
        contexts = build_backend_df(
            context_backend,
            {
                "request_id": ids,
                "tenant": ["A", "B"],
                "region": ["AU", "NZ"],
            },
            table_name=f"invalid_identity_{context_backend}",
        )
        with pytest.raises(ValueError):
            index.apply_batch(
                contexts,
                contract_id="client",
                profile_id="inspect",
                context_id_field="request_id",
            )


def test_empty_batch_retains_typed_identity_outcome_and_candidate_relations():
    engine, lattices = _identity_artifact()
    index = engine.index(lattices)
    contexts = pl.DataFrame(
        schema={"request_id": pl.Int64, "tenant": pl.Utf8, "region": pl.Utf8}
    )
    batch = index.apply_batch(
        contexts,
        contract_id="client",
        profile_id="inspect",
        context_id_field="request_id",
    )

    assert list(batch.records) == []
    assert batch.context_ids.collect().columns == ["__context_id"]
    assert batch.outcomes.collect().columns[:2] == ["__context_id", "status"]
    assert batch.candidate_cells.collect().columns == [
        "__context_id",
        "cell_id",
        "predicate_id",
        "contributor_set_id",
        "pricing.margin",
    ]
    assert batch.candidate_contributors.collect().columns == [
        "__context_id",
        "cell_id",
        "source_id",
    ]
