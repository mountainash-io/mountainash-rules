"""Plan 4 Task 1: the deferred match operation, scope-first execution, and context conversion."""

from __future__ import annotations

import importlib

import polars as pl
import pytest
from mountainash.relations import Relation

from mountainash_rules.decision_table import helpers
from mountainash_rules.decision_table.apply import (
    MatchOperation,
    MatchResult,
    contexts_to_relation,
    presence_column,
)
from mountainash_rules.decision_table.errors import DecisionTableError

from tests.decision_table.apply_support import (
    build,
    context_model,
    context_relation,
    fetch,
    matched_sources,
    sources_by_entry,
    sources_of,
    spy_on_engine,
)
from tests.decision_table.test_build_equivalence import null_rules
from tests.decision_table.test_ordered_and_categorical_fields import (
    fees_rates_metadata,
    fees_rates_rules,
)

prevalidate_module = importlib.import_module("mountainash_rules.decision_table.prevalidate")

Region = context_model(region=str)
Presence = context_model(region=str, vip=bool)

F1, F2, R1 = frozenset({"F1", "F2"}), frozenset({"F2"}), frozenset({"R1"})


@pytest.fixture
def fees_rates(backend_name):
    return build(backend_name, fees_rates_rules(), fees_rates_metadata())


@pytest.fixture
def null_table(backend_name):
    data, metadata, _, _ = null_rules()
    return build(backend_name, data, metadata)


# ---------------------------------------------------------------------------
# match() is deferred; fetch() yields MatchResult(matches)
# ---------------------------------------------------------------------------


def test_match_is_deferred_and_fetch_yields_a_match_result(fees_rates, monkeypatch):
    spy = spy_on_engine(monkeypatch)

    operation = fees_rates.match([Region(request_id=1, region="EU")])

    assert isinstance(operation, MatchOperation)
    assert spy.constructed == [] and spy.batches == []  # nothing ran yet

    result = operation.fetch()

    assert isinstance(result, MatchResult)
    assert isinstance(result.matches, Relation)
    assert result.matches.columns == [
        "request_id", "scope", "entry_id", "entry_sources_id", "specificity", "region", "fee", "rate",
    ]
    assert len(spy.batches) == 1
    assert sources_of(fees_rates, result.matches.to_dicts()) == {F1, F2}


def test_match_rejects_contexts_that_are_neither_a_relation_nor_a_list(fees_rates):
    with pytest.raises(DecisionTableError, match="Relation or a list"):
        fees_rates.match({"request_id": 1, "region": "EU"})


# ---------------------------------------------------------------------------
# fetch performs no validation
# ---------------------------------------------------------------------------


def test_fetch_performs_no_validation_or_rule_revalidation(backend_name, fees_rates, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("fetch must not validate")

    monkeypatch.setattr(prevalidate_module, "prevalidate", refuse)
    monkeypatch.setattr(helpers, "check_sources", refuse)
    # duplicate request ids are the caller's to validate; fetch matches what it is given
    contexts = context_relation(
        backend_name, {"request_id": [1, 1, 2], "region": ["EU", "EU", "US"]}
    )

    rows = fetch(fees_rates, contexts)

    # both rows with id 1 are matched (EU: 2 entries each), and id 2 (US: 2 entries)
    assert [row["request_id"] for row in rows].count(1) == 4
    assert [row["request_id"] for row in rows].count(2) == 2


# ---------------------------------------------------------------------------
# Scope-first execution (the only DecisionTable integration test of scope selection)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scopes", "expected", "engine_calls"),
    [
        (None, {"fees", "rates"}, 1),
        (["fees"], {"fees"}, 1),
        (["rates"], {"rates"}, 1),
        (["rates", "fees"], {"fees", "rates"}, 1),
        ([], set(), 0),
    ],
)
def test_scope_selection_filters_entries_before_the_engine_runs(
    fees_rates, monkeypatch, scopes, expected, engine_calls
):
    spy = spy_on_engine(monkeypatch)
    columns = ["request_id", "scope", "entry_id", "entry_sources_id", "specificity", "region", "fee", "rate"]

    result = fees_rates.match([Region(request_id=1)], scopes).fetch()  # region omitted: all possible

    assert {row["scope"] for row in result.matches.to_dicts()} == expected
    assert result.matches.columns == columns  # an empty selection still has the result's shape
    assert len(spy.batches) == engine_calls
    for call in spy.constructed:  # the engine only ever sees the selected scopes' entries
        assert {row["scope"] for row in call["rules"].to_dicts()} == expected


def test_an_unknown_scope_is_an_error_before_any_engine_call(fees_rates, monkeypatch):
    spy = spy_on_engine(monkeypatch)

    with pytest.raises(DecisionTableError, match="Unknown outcome scope"):
        fees_rates.match([Region(request_id=1)], ["fees", "bonus"]).fetch()

    assert spy.batches == []


# ---------------------------------------------------------------------------
# Conversion of protocol objects: unset versus explicit null, request ids echoed
# ---------------------------------------------------------------------------


def test_list_conversion_encodes_presence_from_model_fields_set():
    objects = [
        Presence(request_id="a", region="EU"),
        Presence(request_id="b", region=None),
        Presence(request_id="c"),
    ]

    converted = contexts_to_relation(objects, ["region", "vip", "term"])

    assert converted.columns == ["request_id", "region", presence_column("region")]
    rows = converted.to_dicts()
    assert [row["request_id"] for row in rows] == ["a", "b", "c"]
    assert [row["region"] for row in rows] == ["EU", None, None]
    # value -> -1, explicit None -> 1, unset -> 0
    assert [row[presence_column("region")] for row in rows] == [-1, 1, 0]


def test_list_conversion_leaves_a_field_set_on_no_object_absent():
    converted = contexts_to_relation([Presence(request_id=1), Presence(request_id=2)], ["region", "vip"])

    assert converted.columns == ["request_id"]


def test_no_contexts_match_nothing_and_keep_the_result_shape(fees_rates):
    result = fees_rates.match([]).fetch()

    assert result.matches.count_rows() == 0
    assert result.matches.columns == [
        "request_id", "scope", "entry_id", "entry_sources_id", "specificity", "region", "fee", "rate",
    ]


def test_matches_echo_the_callers_request_ids(fees_rates):
    contexts = [
        Region(request_id="a", region="EU"),
        Region(request_id="b", region="APAC"),
        Region(request_id="c", region=None),
        Region(request_id="d"),
    ]

    found = matched_sources(fees_rates, contexts)

    assert found["a"] == {("fees", F1), ("fees", F2)}
    assert found["b"] == {("fees", F2)}
    # null and omitted are both unknown on a value dimension: every entry stays possible
    everything = {("fees", F1), ("fees", F2), ("rates", R1)}
    assert found["c"] == everything and found["d"] == everything


# ---------------------------------------------------------------------------
# Presence encoding: protocol objects and Relations agree
# ---------------------------------------------------------------------------


def survivors(table, contexts) -> dict:
    """``{request_id: union of the matched entries' sources}``."""
    found = matched_sources(table, contexts)
    return {
        request: frozenset().union(*(sources for _, sources in entries))
        for request, entries in found.items()
    }


def test_presence_distinguishes_unset_from_explicit_null_for_objects(null_table):
    objects = [
        Presence(request_id="null", region=None, vip=False),
        Presence(request_id="unset", vip=False),
        Presence(request_id="value", region="US", vip=False),
    ]

    result = survivors(null_table, objects)

    # explicit null is a known fact (R2 matches, specificity 1); unset is unknown (R2 only possible, 0)
    assert result["null"] == frozenset({"R1", "R2"})
    assert result["unset"] == frozenset({"R1", "R2"})
    assert "value" not in result  # a supplied region matches neither R1 (EU) nor R2 (null)
    owners = sources_by_entry(null_table)
    r2 = {
        row["request_id"]: row["specificity"]
        for row in fetch(null_table, objects)
        if owners[(row["scope"], row["entry_sources_id"])] == frozenset({"R2"})
    }
    assert r2 == {"null": 1, "unset": 0}


def test_presence_reads_a_relation_null_cell_as_null_and_an_absent_column_as_unset(
    backend_name, null_table
):
    with_null_cell = context_relation(
        backend_name,
        pl.DataFrame({"request_id": [1], "region": pl.Series([None], dtype=pl.String), "vip": [False]}),
    )
    without_column = context_relation(backend_name, {"request_id": [1], "vip": [False]})
    with_value = context_relation(backend_name, {"request_id": [1], "region": ["US"], "vip": [False]})

    assert survivors(null_table, with_null_cell) == {1: frozenset({"R1", "R2"})}
    assert survivors(null_table, without_column) == {1: frozenset({"R1", "R2"})}
    assert survivors(null_table, with_value) == {}  # R2 needs a null region, R3 needs vip
