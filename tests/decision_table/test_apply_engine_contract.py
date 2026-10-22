"""Plan 4 Task 5: engine wiring, error translation and query-stage logging."""

from __future__ import annotations

import dataclasses
import logging

import duckdb
import polars as pl
import pytest
from mountainash.relations import Relation, relation

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, HitPolicy, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

from tests.decision_table.apply_support import build, context_model, spy_on_engine
from tests.decision_table.test_build_equivalence import built
from tests.decision_table.test_ordered_and_categorical_fields import (
    fees_rates_metadata,
    fees_rates_rules,
)

Region = context_model(region=str)
Code = context_model(code=str)
NATIVE_ERRORS = {"polars": pl.exceptions.ComputeError, "ibis-duckdb": duckdb.Error}
LOGGER = "mountainash_rules.decision_table.apply"


@pytest.fixture
def fees_rates(backend_name):
    return build(backend_name, fees_rates_rules(), fees_rates_metadata())


# ---------------------------------------------------------------------------
# One engine call over the selected entries
# ---------------------------------------------------------------------------


def test_one_engine_call_over_the_selected_entries_with_the_entry_metadata(fees_rates, monkeypatch):
    spy = spy_on_engine(monkeypatch)

    fees_rates.match([Region(request_id=1, region="EU")], ["fees"]).fetch()

    (constructed,) = spy.constructed
    (batch,) = spy.batches
    selected = constructed["rules"].to_dicts()
    expected = [row for row in fees_rates.entries.to_dicts() if row["scope"] == "fees"]
    assert sorted(row["entry_id"] for row in selected) == sorted(row["entry_id"] for row in expected)
    assert constructed["metadata"] is fees_rates.entry_metadata
    assert batch["context_id_field"] == "request_id"  # the request identity is the echoed id
    assert batch["hit_policy"] is HitPolicy.COLLECT  # every matching entry, no selection
    assert "top_n_per_context" not in batch and "min_specificity" not in batch  # no rank filtering
    assert batch["dimensions"] == ["region"]


def test_every_match_call_is_one_engine_call_whatever_the_number_of_requests(fees_rates, monkeypatch):
    spy = spy_on_engine(monkeypatch)
    contexts = [Region(request_id=index, region=region) for index, region in enumerate(["EU", "US", "APAC"])]

    fees_rates.match(contexts).fetch()

    assert len(spy.constructed) == len(spy.batches) == 1


@pytest.fixture
def pattern_only_scopes(backend_name):
    """Scope s2's entries require no pattern, and the pattern is the table's only dimension."""
    field = ContextField(name="code", data_type=DataType.STR, wildcard="*")
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(field,),
        dimensions=(Dimension(dimension_name="pre", context_field="code", match_strategy=MatchStrategy.PREFIX),),
        outputs=tuple(
            Aggregate(scope=s, output_name="score", contribution_column="score", data_type=DataType.INT)
            for s in ("s1", "s2")
        ),
    )
    data = {"rule_id": ["P", "Q"], "scope": ["s1", "s2"], "pre": ["EU", "*"], "score": [1, 2]}
    return build(backend_name, data, metadata)


def test_with_nothing_left_to_score_every_selected_entry_matches_at_specificity_zero(
    pattern_only_scopes, monkeypatch
):
    spy = spy_on_engine(monkeypatch)

    rows = pattern_only_scopes.match([Code(request_id="x", code="US-1")], ["s2"]).fetch().matches.to_dicts()

    assert spy.batches == []  # the engine rejects a table without dimensions; there is nothing to score
    assert [(row["request_id"], row["scope"], row["specificity"], row["score"]) for row in rows] == [
        ("x", "s2", 0, 2)
    ]


# ---------------------------------------------------------------------------
# Joins run on the entries' backend; the small context relation is copied to it
# ---------------------------------------------------------------------------


def record_cross_joins(monkeypatch) -> list[Relation]:
    """Every cross join built while matching, to be explained after the fact."""
    joined: list[Relation] = []
    original = Relation.join

    def join(self, other, **kwargs):
        result = original(self, other, **kwargs)
        if kwargs.get("how") == "cross":
            joined.append(result)
        return result

    monkeypatch.setattr(Relation, "join", join)
    return joined


def assert_contexts_move_to_the_entries(backend_name, joined) -> None:
    (join,) = joined
    placement = join.explain()
    assert "ibis_to_polars" not in placement, placement  # the entries are never pulled to the host
    if backend_name == "ibis-duckdb":  # the check bites: the context batch is what is transferred
        assert "polars_to_ibis" in placement, placement


def test_the_engine_join_runs_where_the_entries_live(backend_name, monkeypatch):
    _, table, _, _ = built(backend_name, "patterns")
    joined = record_cross_joins(monkeypatch)

    table.match([Code(request_id=1, code="EU-22")]).fetch()

    assert_contexts_move_to_the_entries(backend_name, joined)


@pytest.mark.parametrize("scopes", [["s2"], []])
def test_the_no_engine_join_runs_where_the_entries_live(backend_name, pattern_only_scopes, monkeypatch, scopes):
    joined = record_cross_joins(monkeypatch)

    pattern_only_scopes.match([Code(request_id="x", code="US-1")], scopes).fetch()

    assert_contexts_move_to_the_entries(backend_name, joined)


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def test_a_native_engine_error_is_raised_as_a_decision_table_error_chained_to_it(
    backend_name, fees_rates, monkeypatch
):
    native = NATIVE_ERRORS[backend_name]("backend down")

    def fail(self, contexts, **kwargs):
        raise native

    monkeypatch.setattr(ExpressionRulesEngine, "evaluate_batch", fail)

    with pytest.raises(DecisionTableError, match="backend down") as raised:
        fees_rates.match([Region(request_id=1, region="EU")]).fetch()

    assert raised.value.__cause__ is native


def test_a_failure_while_the_query_runs_is_wrapped_on_both_backends(backend_name):
    """An invalid pattern fails natively while the matching query executes."""
    _, table, _, _ = built(backend_name, "patterns")
    rows = table.pattern_conditions.to_dicts()
    for row in rows:
        if row["strategy"] == "regex":
            row["pattern"] = "("  # not a regular expression
    broken = dataclasses.replace(
        table, pattern_conditions=relation({column: [row[column] for row in rows] for column in rows[0]})
    )

    with pytest.raises(DecisionTableError) as raised:
        broken.match([Code(request_id=1, code="EU-22")]).fetch()

    assert raised.value.__cause__ is not None
    assert not isinstance(raised.value.__cause__, DecisionTableError)


def test_the_rules_own_errors_are_not_rewrapped(fees_rates, monkeypatch):
    own = DecisionTableError("rules' own")

    def fail(self, contexts, **kwargs):
        raise own

    monkeypatch.setattr(ExpressionRulesEngine, "evaluate_batch", fail)

    with pytest.raises(DecisionTableError) as raised:
        fees_rates.match([Region(request_id=1, region="EU")]).fetch()

    assert raised.value is own


# ---------------------------------------------------------------------------
# Query-stage logging
# ---------------------------------------------------------------------------


def stages(caplog) -> dict[str, int]:
    records = [r for r in caplog.records if r.name == LOGGER]
    assert [r.match_stage for r in records] == ["pattern", "matching"]
    assert all(r.executions_total >= r.executions for r in records)
    return {r.match_stage: r.executions for r in records}


def test_execution_counts_are_logged_at_each_query_stage(backend_name, caplog):
    _, table, _, _ = built(backend_name, "patterns")

    with caplog.at_level(logging.INFO, logger=LOGGER):
        table.match([Code(request_id=1, code="EU-22")]).fetch()

    # pattern evaluation reads which pattern columns the selected entries require and their
    # definitions; matching materializes the result once
    assert stages(caplog) == {"pattern": 2, "matching": 1}
    totals = [r.executions_total for r in caplog.records if r.name == LOGGER]
    assert totals == [2, 3]


def test_a_table_without_patterns_issues_nothing_in_the_pattern_stage(fees_rates, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        fees_rates.match([Region(request_id=1, region="EU")]).fetch()

    assert stages(caplog) == {"pattern": 0, "matching": 1}
