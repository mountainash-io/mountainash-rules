"""Plan 4 Task 3: ternary pattern, TRUTHY and PRESENCE evaluation; the worked null example."""

from __future__ import annotations

import polars as pl
import pytest

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.table import outcome_field
from tests.decision_table.apply_support import (
    all_contexts,
    build,
    build_once,
    check_application,
    context_model,
    context_relation,
    fetch,
    matched_sources,
    relation_batches,
    request_contexts,
    spy_on_engine,
)
from tests.decision_table.table_fixtures import built, built_oracle

M = MatchStrategy
CODE = ContextField(name="code", data_type=DataType.STR, wildcard="*")
TIER = ContextField(name="tier", data_type=DataType.STR, wildcard="*")
Code = context_model(code=str)
Presence = context_model(region=str, vip=bool)


def dimension(name, strategy, field, **kwargs):
    return Dimension(dimension_name=name, context_field=field, match_strategy=strategy, **kwargs)


def score(scope):
    return Aggregate(scope=scope, output_name="score", contribution_column="score", data_type=DataType.INT)


# ---------------------------------------------------------------------------
# P1 prefix "EU-" and P2 contains "-X"
# ---------------------------------------------------------------------------


def prefix_contains_rules():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(CODE,),
        dimensions=(
            dimension("p1", M.PREFIX, "code"),
            dimension("p2", M.CONTAINS, "code"),
        ),
        outputs=(Aggregate(output_name="score", contribution_column="score", data_type=DataType.INT),),
    )
    data = {"rule_id": ["A", "B"], "p1": ["EU-", "*"], "p2": ["*", "-X"], "score": [1, 2]}
    return data, metadata


@pytest.fixture
def prefix_contains(backend_name):
    return build_once(backend_name, prefix_contains_rules)


def union(found, request):
    return frozenset().union(*(sources for _, sources in found.get(request, set())))


def test_prefix_and_contains_patterns_select_their_rules(prefix_contains):
    contexts = [
        Code(request_id="EU-X", code="EU-X"),
        Code(request_id="EU-Y", code="EU-Y"),
        Code(request_id="US-X", code="US-X"),
    ]

    found = matched_sources(prefix_contains, contexts)

    assert union(found, "EU-X") == frozenset({"A", "B"})
    assert union(found, "EU-Y") == frozenset({"A"})
    assert union(found, "US-X") == frozenset({"B"})
    # each entry that holds is returned, never one merged winner
    assert found["EU-X"] == {
        ("default", frozenset({"A", "B"})),
        ("default", frozenset({"A"})),
        ("default", frozenset({"B"})),
    }


def test_context_outcomes_are_computed_before_the_engine_call(prefix_contains, monkeypatch):
    spy = spy_on_engine(monkeypatch)
    contexts = [
        Code(request_id="EU-X", code="EU-X"),
        Code(request_id="EU-Y", code="EU-Y"),
        Code(request_id="US-X", code="US-X"),
        Code(request_id="null", code=None),
        Code(request_id="unset"),
        Code(request_id="wildcard", code="*"),
    ]

    prefix_contains.match(contexts).fetch()

    (batch,) = spy.batches
    prepared = {row["request_id"]: row for row in batch["contexts"].to_dicts()}
    prefix, contains = outcome_field("p1__pattern_0"), outcome_field("p2__pattern_0")
    assert {k: (r[prefix], r[contains]) for k, r in prepared.items()} == {
        "EU-X": (1, 1),
        "EU-Y": (1, -1),
        "US-X": (-1, 1),
        "null": (0, 0),
        "unset": (0, 0),
        "wildcard": (0, 0),  # the field's declared wildcard is a don't-care, not a value
    }
    assert set(batch["dimensions"]) == {"p1__pattern_0", "p2__pattern_0"}


def test_a_context_without_the_field_is_unknown_on_every_pattern(backend_name, prefix_contains, monkeypatch):
    spy = spy_on_engine(monkeypatch)
    contexts = context_relation(backend_name, {"request_id": [1]})  # no `code` column

    found = matched_sources(prefix_contains, contexts)

    assert found[1] == {
        ("default", frozenset({"A", "B"})),
        ("default", frozenset({"A"})),
        ("default", frozenset({"B"})),
    }
    row = spy.batches[0]["contexts"].to_dicts()[0]
    assert (row[outcome_field("p1__pattern_0")], row[outcome_field("p2__pattern_0")]) == (0, 0)


def test_patterns_never_evaluate_wildcard_or_missing_values(backend_name, monkeypatch):
    """A regex that would hold on the wildcard marker or an empty value is not run on null or the marker."""
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(CODE,),
        dimensions=(dimension("rx", M.REGEX, "code"),),
    )
    data = {"rule_id": ["star", "empty"], "rx": [r"\*", "^$"]}
    table = build(backend_name, data, metadata)
    column_of = {row["pattern"]: row["column"] for row in table.pattern_conditions.to_dicts()}
    star, empty = outcome_field(column_of[r"\*"]), outcome_field(column_of["^$"])
    spy = spy_on_engine(monkeypatch)
    frame = pl.DataFrame({"request_id": ["marker", "empty", "null"], "code": ["*", "", None]})

    table.match(context_relation(backend_name, frame)).fetch()

    prepared = {row["request_id"]: row for row in spy.batches[0]["contexts"].to_dicts()}
    # the marker "*" matches the text \* but is a don't-care; null matches nothing and is unknown
    assert (prepared["marker"][star], prepared["marker"][empty]) == (0, 0)
    assert (prepared["null"][star], prepared["null"][empty]) == (0, 0)
    assert (prepared["empty"][star], prepared["empty"][empty]) == (-1, 1)


# ---------------------------------------------------------------------------
# Stored ternary requirements, CONTEXT_REGEX included; TRUTHY handled once
# ---------------------------------------------------------------------------


def test_pattern_requirements_are_stored_as_int8_ternaries_including_context_regex(backend_name):
    _, table, _, _ = built(backend_name, "patterns")

    schema = table.entries.to_pyarrow().schema
    columns = [row["column"] for row in table.pattern_conditions.to_dicts()]
    assert {str(schema.field(c).type) for c in columns} == {"int8"}
    frame = table.entries.to_polars()
    for column in columns:
        assert set(frame[column].unique().to_list()) <= {0, 1}
    assert set(frame["upper"].unique().to_list()) == {1}  # CONTEXT_REGEX is required of every entry
    # every one of them is read through OUTCOME against its own context outcome
    outcome_dimensions = {
        d.dimension_name for d in table.entry_metadata.dimensions if d.match_strategy is M.OUTCOME
    }
    assert set(columns) <= outcome_dimensions


def test_context_regex_filters_values_that_fail_it_and_ignores_missing_ones(backend_name):
    _, table, _, _ = built(backend_name, "patterns")
    model = context_model(code=str)

    found = matched_sources(
        table,
        [
            model(request_id="lower", code="xx"),
            model(request_id="upper", code="UK-2"),
            model(request_id="null", code=None),
            model(request_id="unset"),
        ],
    )

    assert "lower" not in found  # fails the CONTEXT_REGEX every rule requires
    assert found["upper"] and found["null"] and found["unset"]


def test_truthy_outcomes_are_computed_once_and_not_repeated_by_the_engine(backend_name, monkeypatch):
    _, table, _, _ = built(backend_name, "truthy")
    spy = spy_on_engine(monkeypatch)
    model = context_model(code=str)

    found = matched_sources(
        table,
        [
            model(request_id="yes", code="yes"),
            model(request_id="no", code="no"),
            model(request_id="maybe", code="maybe"),
            model(request_id="other", code="other"),
            model(request_id="null", code=None),
        ],
    )

    sources = {k: frozenset().union(*(s for _, s in v)) for k, v in found.items()}
    # rule M maps to 0 and ANY is a wildcard: both stay possible; Y and N need their own outcome
    assert sources["yes"] == frozenset({"Y", "M", "ANY"})
    assert sources["no"] == frozenset({"N", "M", "ANY"})
    # maybe, an unmapped value and null are all outcome 0: unknown on every rule
    everything = frozenset({"Y", "N", "M", "ANY"})
    assert sources["maybe"] == sources["other"] == sources["null"] == everything
    (batch,) = spy.batches
    assert all(d.match_strategy is M.OUTCOME for d in spy.constructed[0]["metadata"].dimensions)
    assert outcome_field("agree") in batch["contexts"].columns


# ---------------------------------------------------------------------------
# The worked null example C1-C4 (explicit-dont-care-sentinels.md)
# ---------------------------------------------------------------------------

C = {
    "C1": {"region": "EU", "vip": True},
    "C2": {"region": None, "vip": False},
    "C3": {},
    "C4": {"region": "US", "vip": True},
}
SURVIVORS = {
    "C1": frozenset({"R3", "R1"}),
    "C2": frozenset({"R2", "R1"}),
    "C3": frozenset({"R1", "R2", "R3"}),
    "C4": frozenset({"R3"}),
}


@pytest.fixture
def null_table(backend_name):
    return built(backend_name, "null_rules")[1]


def test_worked_null_example_survivors_for_protocol_objects(null_table):
    contexts = [Presence(request_id=name, **values) for name, values in C.items()]

    found = matched_sources(null_table, contexts)

    assert {name: union(found, name) for name in C} == SURVIVORS


def test_worked_null_example_survivors_for_relations(backend_name, null_table):
    """A null cell is explicit null (C2); an absent column is unset (C3)."""
    dtypes = {"region": pl.String, "vip": pl.Boolean}
    contexts = [(name, values) for name, values in C.items()]
    result = {}
    for name, values in contexts:
        frame = pl.DataFrame(
            {"request_id": [name], **{k: pl.Series([v], dtype=dtypes[k]) for k, v in values.items()}}
        )
        found = matched_sources(null_table, context_relation(backend_name, frame))
        result[name] = union(found, name)

    assert result == SURVIVORS


def test_worked_null_example_specificity(null_table):
    """C1: R3 matches presence and vip, R1 the region; C2: R2 matches the null, R1 stays possible."""
    rows = fetch(null_table, [Presence(request_id=name, **values) for name, values in C.items()])

    top = {}
    for row in rows:
        top[row["request_id"]] = max(top.get(row["request_id"], 0), row["specificity"])
    # the most specific entry per context carries the doc's rule specificities
    assert top == {"C1": 3, "C2": 1, "C3": 0, "C4": 2}


# ---------------------------------------------------------------------------
# Pattern columns are filtered to the selected scopes' entries
# ---------------------------------------------------------------------------


def scoped_patterns_rules():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(CODE, TIER),
        dimensions=(dimension("pre", M.PREFIX, "code"), dimension("tier", M.EXACT, "tier")),
        outputs=(score("s1"), score("s2")),
    )
    data = {
        "rule_id": ["P", "Q"],
        "scope": ["s1", "s2"],
        "pre": ["EU", "*"],
        "tier": ["*", "*"],
        "score": [1, 2],
    }
    return data, metadata


@pytest.fixture
def scoped_patterns(backend_name):
    return build_once(backend_name, scoped_patterns_rules)


@pytest.mark.parametrize(
    ("scopes", "evaluated"),
    [(None, True), (["s1"], True), (["s2"], False), (["s2", "s1"], True)],
)
def test_pattern_columns_are_restricted_to_the_selected_scopes_entries(
    scoped_patterns, monkeypatch, scopes, evaluated
):
    spy = spy_on_engine(monkeypatch)
    column = "pre__pattern_0"

    result = scoped_patterns.match([Code(request_id=1, code="EU-1")], scopes).fetch()

    (batch,) = spy.batches
    assert "tier" in batch["dimensions"]  # the scope's other dimensions are always scored
    assert (column in batch["dimensions"]) is evaluated
    assert (outcome_field(column) in batch["contexts"].columns) is evaluated
    expected_scopes = {"s1", "s2"} if scopes is None else set(scopes)
    assert {row["scope"] for row in result.matches.to_dicts()} == expected_scopes


def test_scoped_results_do_not_depend_on_the_pattern_filter(scoped_patterns):
    # s2's wildcard entry matches whatever the code is; s1's entry needs the EU prefix
    assert matched_sources(scoped_patterns, [Code(request_id=1, code="US-1")], ["s2"]) == {
        1: {("s2", frozenset({"Q"}))}
    }
    assert matched_sources(scoped_patterns, [Code(request_id=1, code="US-1")]) == {
        1: {("s2", frozenset({"Q"}))}
    }
    assert matched_sources(scoped_patterns, [Code(request_id=1, code="EU-1")]) == {
        1: {("s1", frozenset({"P"})), ("s2", frozenset({"Q"}))}
    }


# ---------------------------------------------------------------------------
# Agreement with the finite-completion oracle for every outcome kind
# ---------------------------------------------------------------------------

MODELS = {
    "patterns": (context_model(code=str), {"code": pl.String}),
    "truthy": (context_model(code=str), {"code": pl.String}),
    "null_rules": (context_model(region=str, vip=bool), {"region": pl.String, "vip": pl.Boolean}),
    "outcome": (context_model(flag=int), {"flag": pl.Int64}),
}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_context_agrees_with_the_authored_rules_for_protocol_objects(backend_name, name):
    _, table, _, _ = built(backend_name, name)
    oracle = built_oracle(backend_name, name)
    model, _ = MODELS[name]
    contexts = all_contexts(oracle)

    rows = table.match(request_contexts(model, contexts)).fetch().matches.to_dicts()

    check_application(table, oracle, contexts, rows)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_context_agrees_with_the_authored_rules_for_relations(backend_name, name):
    _, table, _, _ = built(backend_name, name)
    oracle = built_oracle(backend_name, name)
    _, dtypes = MODELS[name]
    contexts = all_contexts(oracle)

    rows = []
    for relation_, _indices in relation_batches(backend_name, contexts, dtypes):
        rows += table.match(relation_).fetch().matches.to_dicts()

    check_application(table, oracle, contexts, rows)
