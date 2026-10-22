"""Plan 4 Task 2: categorical values, negated dimensions and overlapping entries."""

from __future__ import annotations

import pytest

from tests.decision_table.apply_support import (
    build,
    context_model,
    fetch,
    matched_sources,
    sources_by_entry,
)
from tests.decision_table.test_build_equivalence import ordered_negation
from tests.decision_table.test_ordered_and_categorical_fields import (
    fees_rates_metadata,
    fees_rates_rules,
)

Region = context_model(region=str)
Term = context_model(term=int)

F1F2, F2, F1F2N1, F2N1, R1 = (
    frozenset({"F1", "F2"}),
    frozenset({"F2"}),
    frozenset({"F1", "F2", "N1"}),
    frozenset({"F2", "N1"}),
    frozenset({"R1"}),
)


def specificity_by_sources(table, contexts) -> dict:
    """``{request_id: {(scope, sources): specificity}}``."""
    owners = sources_by_entry(table)
    found: dict = {}
    for row in fetch(table, contexts):
        found.setdefault(row["request_id"], {})[(row["scope"], owners[(row["scope"], row["entry_sources_id"])])] = (
            row["specificity"]
        )
    return found


# ---------------------------------------------------------------------------
# The categorical fees/rates example
# ---------------------------------------------------------------------------


def test_categorical_fees_and_rates_entries_per_region(backend_name):
    table = build(backend_name, fees_rates_rules(), fees_rates_metadata())
    contexts = [
        Region(request_id="EU", region="EU"),
        Region(request_id="US", region="US"),
        Region(request_id="APAC", region="APAC"),
        Region(request_id="omitted"),
    ]

    found = specificity_by_sources(table, contexts)

    assert found["EU"] == {("fees", F1F2): 1, ("fees", F2): 0}
    assert found["US"] == {("fees", F2): 0, ("rates", R1): 1}
    assert found["APAC"] == {("fees", F2): 0}
    # an omitted region leaves every entry possible, all at specificity 0
    assert found["omitted"] == {("fees", F1F2): 0, ("fees", F2): 0, ("rates", R1): 0}


def test_categorical_outputs_travel_with_each_entry(backend_name):
    table = build(backend_name, fees_rates_rules(), fees_rates_metadata())

    rows = fetch(table, [Region(request_id=1, region="EU")], ["fees"])

    assert {(row["region"], row["fee"]) for row in rows} == {("EU", 11), ("*", 1)}
    assert all(row["rate"] is None for row in rows)


def test_negated_categorical_entries_are_each_returned_when_their_condition_holds(backend_name):
    table = build(backend_name, fees_rates_rules(negation=True), fees_rates_metadata(negation=True))
    contexts = [Region(request_id=region, region=region) for region in ("EU", "US", "APAC")]

    found = matched_sources(table, contexts, ["fees"])

    # EU: all four entries; US: the wildcard/wildcard entry only (US != US is false); APAC: both wildcard rows
    assert found["EU"] == {("fees", s) for s in (F1F2, F2, F1F2N1, F2N1)}
    assert found["US"] == {("fees", F2)}
    assert found["APAC"] == {("fees", F2), ("fees", F2N1)}


# ---------------------------------------------------------------------------
# T1 RANGE [1, 10] and N1 NOT_EQUAL 5: overlapping entries are not merged
# ---------------------------------------------------------------------------


@pytest.fixture
def term_table(backend_name):
    data, metadata, _, _ = ordered_negation()
    return build(backend_name, data, metadata)


def test_overlapping_negation_entries_both_match_and_stay_distinct(term_table):
    rows = fetch(term_table, [Term(request_id=3, term=3)])
    owners = sources_by_entry(term_table)

    assert len(rows) == 2
    by_sources = {owners[("s", row["entry_sources_id"])]: row for row in rows}
    assert set(by_sources) == {frozenset({"T1"}), frozenset({"T1", "N1"})}
    t1, n1 = by_sources[frozenset({"T1"})], by_sources[frozenset({"T1", "N1"})]
    assert (t1["specificity"], n1["specificity"]) == (1, 2)
    # both keep their own condition columns, including the negation value, and their own ids
    assert (t1["term__lo"], t1["term__hi"], t1["t_ne"]) == (1, 10, None)
    assert (n1["term__lo"], n1["term__hi"], n1["t_ne"]) == (1, 10, 5)
    assert t1["entry_id"] != n1["entry_id"]
    assert (t1["score"], n1["score"]) == (1, 11)


def test_the_negated_value_excludes_only_the_entry_that_carries_it(term_table):
    rows = fetch(term_table, [Term(request_id=5, term=5)])
    owners = sources_by_entry(term_table)

    assert [owners[("s", row["entry_sources_id"])] for row in rows] == [frozenset({"T1"})]
    assert rows[0]["specificity"] == 1


def test_a_term_outside_the_range_matches_neither_overlapping_entry(term_table):
    assert fetch(term_table, [Term(request_id=1, term=11)]) == []
