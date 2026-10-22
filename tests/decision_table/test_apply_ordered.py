"""Plan 4 Task 4: ordered fields, closed-interval boundaries and the finite-completion oracle."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import polars as pl
import pytest

from mountainash_rules.core.constants import DataType
from mountainash_rules.core.dimension import ContextField

from tests.decision_table.apply_support import (
    all_contexts,
    build,
    build_once,
    check_application,
    context_model,
    fetch,
    matched_sources,
    relation_batches,
    request_contexts,
    sources_by_entry,
)
from tests.decision_table.table_fixtures import built, built_oracle
from tests.decision_table.test_ordered_and_categorical_fields import (
    datetime_field,
    decimal_field,
    single_field_metadata,
    term_metadata,
    term_rules,
)

Term = context_model(term=int)
T1, T1T2, T3 = frozenset({"T1"}), frozenset({"T1", "T2"}), frozenset({"T3"})


def union(found, request):
    return frozenset().union(*(sources for _, sources in found.get(request, set())))


# ---------------------------------------------------------------------------
# T1 RANGE [1,5], T2 EXACT 3, T3 MIN 10
# ---------------------------------------------------------------------------


def term_rules_and_metadata():
    data = term_rules(T1={"range": (1, 5)}, T2={"exact": 3}, T3={"min": 10})
    return data, term_metadata()


@pytest.fixture
def term_table(backend_name):
    return build_once(backend_name, term_rules_and_metadata)


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        (3, {("rates", T1T2)}),
        (5, {("rates", T1)}),
        (1, {("rates", T1)}),
        (7, set()),
        (0, set()),
        (12, {("rates", T3)}),
        (10, {("rates", T3)}),
    ],
)
def test_ordered_worked_example_per_term(term_table, term, expected):
    assert matched_sources(term_table, [Term(request_id=term, term=term)]).get(term, set()) == expected


def test_an_omitted_term_returns_every_interval_entry_distinct_with_its_specificity(term_table):
    omitted = fetch(term_table, [Term(request_id=1)])
    explicit_null = fetch(term_table, [Term(request_id=1, term=None)])
    owners = sources_by_entry(term_table)

    for rows in (omitted, explicit_null):  # null on a value dimension is unknown, like an omission
        sources = [owners[("rates", r["entry_sources_id"])] for r in rows]
        assert sorted(sources, key=sorted) == sorted([T1, T1T2, T1, T3], key=sorted)
        assert len({r["entry_id"] for r in rows}) == 4  # the two {T1} intervals stay separate entries
        assert {r["specificity"] for r in rows} == {0}
        assert sorted((r["term__lo"], r["term__hi"]) for r in rows)[:3] == [(1, 2), (3, 3), (4, 5)]


# ---------------------------------------------------------------------------
# Closed interval boundaries by type
# ---------------------------------------------------------------------------

D = Decimal
BOUNDARIES = {
    "int": (
        ContextField(name="v", data_type=DataType.INT, wildcard=None, wildcard_declared=True),
        int,
        (10, 20),
        [(9, False), (10, True), (15, True), (20, True), (21, False)],
        pl.Int64,
    ),
    "decimal": (
        decimal_field(),
        Decimal,
        (D("1.00"), D("2.00")),
        [
            (D("0.99"), False),
            (D("1.00"), True),
            (D("2.00"), True),
            (D("2.01"), False),
            # finer than the scale: rounded half away from zero before comparing
            (D("0.994"), False),
            (D("0.995"), True),
            (D("2.004"), True),
            (D("2.005"), False),
        ],
        pl.Decimal(16, 2),
    ),
    "date": (
        ContextField(name="v", data_type=DataType.DATE, wildcard=None, wildcard_declared=True),
        dt.date,
        (dt.date(2024, 1, 10), dt.date(2024, 1, 20)),
        [
            (dt.date(2024, 1, 9), False),
            (dt.date(2024, 1, 10), True),
            (dt.date(2024, 1, 20), True),
            (dt.date(2024, 1, 21), False),
        ],
        pl.Date,
    ),
    "datetime-s": (
        datetime_field("s"),
        dt.datetime,
        (dt.datetime(2024, 3, 1, 12, 0, 0), dt.datetime(2024, 3, 1, 12, 0, 5)),
        [
            (dt.datetime(2024, 3, 1, 11, 59, 59), False),
            (dt.datetime(2024, 3, 1, 12, 0, 0), True),
            (dt.datetime(2024, 3, 1, 12, 0, 5), True),
            (dt.datetime(2024, 3, 1, 12, 0, 5, 999999), True),  # truncated to the unit
            (dt.datetime(2024, 3, 1, 12, 0, 6), False),
        ],
        pl.Datetime("us"),
    ),
    "datetime-ms": (
        datetime_field("ms"),
        dt.datetime,
        (dt.datetime(2024, 3, 1, 12, 0, 0, 123000), dt.datetime(2024, 3, 1, 12, 0, 0, 654000)),
        [
            (dt.datetime(2024, 3, 1, 12, 0, 0, 122999), False),
            (dt.datetime(2024, 3, 1, 12, 0, 0, 123000), True),
            (dt.datetime(2024, 3, 1, 12, 0, 0, 654000), True),
            (dt.datetime(2024, 3, 1, 12, 0, 0, 654999), True),  # truncated to the unit
            (dt.datetime(2024, 3, 1, 12, 0, 0, 655000), False),
        ],
        pl.Datetime("us"),
    ),
    "datetime-us": (
        datetime_field("us"),
        dt.datetime,
        (dt.datetime(2024, 3, 1, 12, 0, 0, 123456), dt.datetime(2024, 3, 1, 12, 0, 0, 654321)),
        [
            (dt.datetime(2024, 3, 1, 12, 0, 0, 123455), False),
            (dt.datetime(2024, 3, 1, 12, 0, 0, 123456), True),
            (dt.datetime(2024, 3, 1, 12, 0, 0, 654321), True),
            (dt.datetime(2024, 3, 1, 12, 0, 0, 654322), False),
        ],
        pl.Datetime("us"),
    ),
}


@pytest.mark.parametrize("kind", sorted(BOUNDARIES))
def test_closed_interval_boundaries(backend_name, kind):
    field, python_type, (low, high), probes, dtype = BOUNDARIES[kind]
    data = {
        "rule_id": ["a"],
        "lo": pl.Series([low], dtype=dtype),
        "hi": pl.Series([high], dtype=dtype),
    }
    table = build(backend_name, data, single_field_metadata(field, with_range=True))
    model = context_model(v=python_type)

    found = matched_sources(
        table, [model(request_id=index, v=value) for index, (value, _) in enumerate(probes)]
    )

    assert {index: index in found for index in range(len(probes))} == {
        index: inside for index, (_, inside) in enumerate(probes)
    }
    # an omitted value leaves the interval's entry possible at specificity 0
    (row,) = fetch(table, [model(request_id="omitted")])
    assert row["specificity"] == 0 and (row["v__lo"], row["v__hi"]) == (low, high)


# ---------------------------------------------------------------------------
# The finite-completion oracle at the application boundary
# ---------------------------------------------------------------------------

MODELS = {
    "categorical": (context_model(region=str), {"region": pl.String}),
    "categorical_negation": (context_model(region=str), {"region": pl.String}),
    "ordered": (Term, {"term": pl.Int64}),
    "ordered_negation": (Term, {"term": pl.Int64}),
    "conjunction": (context_model(region=str, term=int), {"region": pl.String, "term": pl.Int64}),
}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_completion_of_every_context_agrees_with_the_authored_rules_for_objects(backend_name, name):
    _, table, _, _ = built(backend_name, name)
    oracle = built_oracle(backend_name, name)
    model, _ = MODELS[name]
    contexts = all_contexts(oracle)

    rows = table.match(request_contexts(model, contexts)).fetch().matches.to_dicts()

    check_application(table, oracle, contexts, rows)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_completion_of_every_context_agrees_with_the_authored_rules_for_relations(backend_name, name):
    _, table, _, _ = built(backend_name, name)
    oracle = built_oracle(backend_name, name)
    _, dtypes = MODELS[name]
    contexts = all_contexts(oracle)

    rows = []
    for relation_, _indices in relation_batches(backend_name, contexts, dtypes):
        rows += table.match(relation_).fetch().matches.to_dicts()

    check_application(table, oracle, contexts, rows)


def test_alternatives_are_retained_not_summed(backend_name):
    """An omitted term keeps every entry with its own output; no row folds the alternatives together."""
    _, table, _, _ = built(backend_name, "ordered")

    rows = fetch(table, [Term(request_id=1)])

    assert sorted(row["rate"] for row in rows) == [D("1.75"), D("2.00"), D("2.00"), D("3.00")]
    assert len({row["entry_id"] for row in rows}) == len(rows) == 4
    oracle = built_oracle(backend_name, "ordered")
    expected = {m.entry_id for m in oracle.table_matches(table, "rates", {})}
    assert {row["entry_id"] for row in rows} == expected
