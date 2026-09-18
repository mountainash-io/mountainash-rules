"""Range membership agrees with independent interval and filter oracles."""

import itertools
import pytest

from mountainash_rules import (
    AccumulatorEngine,
    Dimension,
    DimensionsMetadata,
    ExactResourceError,
    ExpressionRulesEngine,
    UNKNOWN_NUMERIC,
)
from tests.accumulator.exact_runtime_fixtures import (
    build,
    declarations,
    gate,
    memberships,
    uuid,
)
from tests.accumulator.source_analysis_fixtures import limits

S = UNKNOWN_NUMERIC
FLAGS = [(True, True), (True, False), (False, True), (False, False)]


def _dimension(min_inc=True, max_inc=True):
    return Dimension(
        dimension_name="x",
        match_strategy="range",
        data_type="int",
        range_min_field="lo",
        range_max_field="hi",
        range_min_inclusive=min_inc,
        range_max_inclusive=max_inc,
    )


def _inside(value, interval, min_inc, max_inc):
    lo, hi = interval
    return (lo == S or value > lo or (min_inc and value == lo)) and (
        hi == S or value < hi or (max_inc and value == hi)
    )


@pytest.mark.parametrize("min_inc,max_inc", FLAGS)
def test_compiled_joint_membership_matches_source_and_interval_oracles(
    min_inc, max_inc
):
    intervals = [
        (lo, hi)
        for lo, hi in itertools.product([S, 0, 5, 10], repeat=2)
        if lo == S or hi == S or lo < hi or (lo == hi and min_inc and max_inc)
    ]
    probes = [-1, 0, 1, 4, 5, 6, 9, 10, 11]
    dimension = _dimension(min_inc, max_inc)
    kwargs = declarations([dimension])
    for a, b in itertools.combinations_with_replacement(intervals, 2):
        rows = [dict(id=uuid(1), lo=a[0], hi=a[1]), dict(id=uuid(2), lo=b[0], hi=b[1])]
        joint = [
            value
            for value in probes
            if _inside(value, a, min_inc, max_inc)
            and _inside(value, b, min_inc, max_inc)
        ]
        decisions = []
        if joint:
            contact = (
                min_inc
                and max_inc
                and ((a[1] != S and a[1] == b[0]) or (b[1] != S and b[1] == a[0]))
            )
            code = "singleton_boundary_overlap" if contact else "source_overlap"
            decisions.append((code, (uuid(1), uuid(2))))
            if a == b:
                decisions.append(("duplicate_source", (uuid(1), uuid(2))))
        _, lattice = build(rows, kwargs, decisions=decisions)
        combined = frozenset([uuid(1), uuid(2)]) in memberships(lattice).values()
        assert combined == bool(joint), (a, b, min_inc, max_inc)
        frame = {"rule_name": ["A", "B"], "lo": [a[0], b[0]], "hi": [a[1], b[1]]}
        legacy_filter = ExpressionRulesEngine(
            frame, dimension_metadata=DimensionsMetadata(dimensions=[dimension])
        )
        assert combined == any(
            legacy_filter.evaluate({"x": value}).count == 2 for value in probes
        )


@pytest.mark.parametrize(
    "lo,hi,decisions,count",
    [
        (S, 10, [], 1),
        (S, S, [], 1),
        (10, 10, [], 1),
    ],
)
def test_half_open_wildcard_and_point_sources_retain_real_cells(
    lo, hi, decisions, count
):
    _, lattice = build(
        [dict(id=uuid(1), lo=lo, hi=hi)],
        declarations([_dimension()]),
        decisions=decisions,
    )
    assert lattice.count == count
    assert set(memberships(lattice).values()) == {frozenset([uuid(1)])}


def test_scope_capacity_failure_does_not_return_partial_partition():
    rows = [dict(id=uuid(1), lo=0, hi=10)]
    kwargs = declarations([_dimension()])
    validation = gate(rows, kwargs)
    engine = AccumulatorEngine(kwargs["metadata"], limits=limits(max_scopes=0))
    with pytest.raises(ExactResourceError) as failure:
        engine.build_all(rows, validation=validation)
    assert failure.value.counter == "max_scopes"
    assert failure.value.operation == "build_all"
