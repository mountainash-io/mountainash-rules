"""Exact build preserves source membership, residuals and declared folds."""

import pytest

from mountainash_rules import Aggregate, Dimension, UNKNOWN, UNKNOWN_NUMERIC
from tests.accumulator.exact_runtime_fixtures import (
    build,
    declarations,
    gate,
    memberships,
    uuid,
)


def _aggregate(column="margin", operation="sum"):
    return Aggregate(
        column_name=column,
        output_name=f"pricing.{operation}",
        operation=operation,
        data_type="float",
        numeric_semantics="numeric-1",
    )


def test_worked_example_preserves_all_applicable_source_sets():
    dimensions = [
        Dimension(dimension_name="channel"),
        Dimension(
            dimension_name="lvr",
            data_type="int",
            match_strategy="range",
            range_min_field="lo",
            range_max_field="hi",
        ),
        Dimension(dimension_name="foreign_resident"),
    ]
    rows = [
        dict(
            id=uuid(1),
            channel="BROKER",
            lo=60,
            hi=80,
            foreign_resident=UNKNOWN,
            margin=-0.10,
        ),
        dict(
            id=uuid(2),
            channel=UNKNOWN,
            lo=70,
            hi=90,
            foreign_resident="false",
            margin=-0.05,
        ),
        dict(
            id=uuid(3),
            channel="BROKER",
            lo=UNKNOWN_NUMERIC,
            hi=UNKNOWN_NUMERIC,
            foreign_resident="false",
            margin=-0.15,
        ),
    ]
    decisions = [
        ("source_overlap", (uuid(a), uuid(b))) for a, b in [(1, 2), (1, 3), (2, 3)]
    ]
    _, lattice = build(
        rows, declarations(dimensions, [_aggregate()]), decisions=decisions
    )
    actual = memberships(lattice)
    assert set(actual.values()) == {
        frozenset(uuid(i) for i in indices)
        for indices in [(1,), (2,), (3,), (1, 3), (2, 3), (1, 2, 3)]
    }
    triple = [
        r
        for r in lattice.combinations.to_dicts()
        if actual[r["cell_id"]] == {uuid(1), uuid(2), uuid(3)}
    ]
    assert [r["pricing.sum"] for r in triple] == [-0.3]


@pytest.mark.parametrize(
    "values,decisions,expected",
    [
        (["BROKER"], [], [{1}]),
        (["BROKER", "DIRECT"], [], [{1}, {2}]),
        (
            [UNKNOWN, UNKNOWN, UNKNOWN],
            [
                (code, (uuid(a), uuid(b)))
                for code in ("duplicate_source", "source_overlap")
                for a, b in [(1, 2), (1, 3), (2, 3)]
            ],
            [{1, 2, 3}],
        ),
    ],
)
def test_scalar_source_membership(values, decisions, expected):
    rows = [
        dict(id=uuid(i + 1), channel=value, margin=float(i + 1))
        for i, value in enumerate(values)
    ]
    _, lattice = build(
        rows,
        declarations([Dimension(dimension_name="channel")], [_aggregate()]),
        decisions=decisions,
    )
    assert set(memberships(lattice).values()) == {
        frozenset(uuid(i) for i in group) for group in expected
    }
    if len(expected) == 1:
        assert lattice.combinations["pricing.sum"].to_list() == [
            sum(range(1, len(values) + 1))
        ]


@pytest.mark.parametrize(
    "strategy,values,decisions,expected",
    [
        (
            "set_membership",
            [None, None, None],
            [
                (code, (uuid(a), uuid(b)))
                for code in ("duplicate_source", "source_overlap")
                for a, b in [(1, 2), (1, 3), (2, 3)]
            ],
            [{1, 2, 3}],
        ),
        (
            "set_membership",
            [["AU", "NZ", "UK"], ["NZ", "UK", "US"]],
            [("source_overlap", (uuid(1), uuid(2)))],
            [{1}, {2}, {1, 2}],
        ),
        (
            "set_membership",
            [["UK", "NZ"], ["NZ", "UK"]],
            [
                ("duplicate_source", (uuid(1), uuid(2))),
                ("source_overlap", (uuid(1), uuid(2))),
            ],
            [{1, 2}],
        ),
        (
            "set_exclusion",
            [["AU", "NZ"], ["NZ", "US"]],
            [("source_overlap", (uuid(1), uuid(2)))],
            [{1}, {2}, {1, 2}],
        ),
    ],
)
def test_sets_retain_residual_membership_without_coalesced_columns(
    strategy, values, decisions, expected
):
    rows = [dict(id=uuid(i + 1), region=value) for i, value in enumerate(values)]
    _, lattice = build(
        rows,
        declarations([Dimension(dimension_name="region", match_strategy=strategy)]),
        decisions=decisions,
    )
    assert set(memberships(lattice).values()) == {
        frozenset(uuid(i) for i in group) for group in expected
    }
    cell_id = lattice.combinations["cell_id"][0]
    assert lattice.lineage(cell_id).to_dicts() == []
    assert lattice.lineage(cell_id).collect().columns == [
        "output_name",
        "column_name",
        "operation",
        "source_id",
        "source_label",
    ]


def test_embedded_set_sentinel_rejects_before_source_permission():
    kwargs = declarations(
        [Dimension(dimension_name="region", match_strategy="set_membership")]
    )
    with pytest.raises(ValueError):
        gate([dict(id=uuid(1), region=["AU", UNKNOWN])], kwargs)


def test_float_set_wildcard_keeps_distinct_sources_and_residual_cells():
    rows = [dict(id=uuid(1), scores=[1.5, 2.5, 3.5]), dict(id=uuid(2), scores=None)]
    _, lattice = build(
        rows,
        declarations(
            [
                Dimension(
                    dimension_name="scores",
                    data_type="float",
                    match_strategy="set_membership",
                )
            ]
        ),
        decisions=[("source_overlap", (uuid(1), uuid(2)))],
    )
    assert set(memberships(lattice).values()) == {
        frozenset([uuid(2)]),
        frozenset([uuid(1), uuid(2)]),
    }


@pytest.mark.parametrize(
    "operation,expected",
    [("sum", 14.0), ("min", 4.0), ("max", 10.0), ("product", 40.0)],
)
def test_reducers_fold_each_source_once(operation, expected):
    rows = [
        dict(id=uuid(1), channel="BROKER", margin=10.0),
        dict(id=uuid(2), channel=UNKNOWN, margin=4.0),
    ]
    _, lattice = build(
        rows,
        declarations(
            [Dimension(dimension_name="channel")], [_aggregate(operation=operation)]
        ),
        decisions=[("source_overlap", (uuid(1), uuid(2)))],
    )
    sets = memberships(lattice)
    combined = [
        r
        for r in lattice.combinations.to_dicts()
        if sets[r["cell_id"]] == {uuid(1), uuid(2)}
    ]
    assert [r[f"pricing.{operation}"] for r in combined] == [expected]
    assert {
        r["source_id"] for r in lattice.lineage(combined[0]["cell_id"]).to_dicts()
    } == {uuid(1), uuid(2)}
