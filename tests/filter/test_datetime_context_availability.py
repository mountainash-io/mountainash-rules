"""Unavailable DATETIME contexts score unknown for every declared unit and timezone."""

import datetime as dt

import polars as pl
import pytest

from mountainash.relations import relation
from mountainash_rules import (
    ContextField,
    DataType,
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    MatchStrategy,
    build_decision_table,
    prevalidate,
)
from tests.conftest import build_backend_df


BACKENDS = ("polars", "ibis-duckdb")
STRATEGIES = (MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL, MatchStrategy.MIN, MatchStrategy.RANGE)


def _field(unit, timezone, **kwargs):
    tzinfo = dt.timezone.utc if timezone == "utc" else None
    return ContextField(
        name="moment", data_type=DataType.DATETIME, datetime_unit=unit, timezone=timezone,
        wildcard=dt.datetime(1900, 1, 1, tzinfo=tzinfo), **kwargs,
    ), dt.datetime(2026, 1, 1, tzinfo=tzinfo)


def _setup(backend_name, unit, timezone, strategy):
    field, base = _field(unit, timezone)
    if strategy is MatchStrategy.RANGE:
        options = {"range_min_field": "low", "range_max_field": "high"}
        operands = {"low": [base, field.wildcard], "high": [base + dt.timedelta(seconds=2), field.wildcard]}
    else:
        options = {"rule_field": "authored"}
        operands = {"authored": [base, field.wildcard]}
    metadata = DimensionsMetadata(
        uuid_column="rule_name",
        fields=(field,),
        dimensions=(Dimension(dimension_name="moment", context_field="moment", match_strategy=strategy, **options),),
    )
    rules = build_backend_df(backend_name, {"rule_name": ["authored", "any"], **operands}, table_name="moment_rules")
    return ExpressionRulesEngine(rules, metadata), build_decision_table(prevalidate(relation(rules), metadata)), field, base


def _contexts(unit, timezone):
    """Polars context frames; the typed null is the control that always worked."""
    native_unit = "ms" if unit == "s" else unit
    return {
        "omitted": pl.DataFrame({"request_id": [1]}),
        "untyped-null": pl.DataFrame({"request_id": [1], "moment": [None]}),
        "typed-null": pl.DataFrame(
            {"request_id": [1], "moment": [None]},
            schema={"request_id": pl.Int64, "moment": pl.Datetime(native_unit, "UTC" if timezone == "utc" else None)},
        ),
    }


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("unit", ["s", "ms", "us"])
@pytest.mark.parametrize("timezone", ["naive", "utc"])
@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
def test_unavailable_datetime_scores_unknown_on_every_path(backend_name, unit, timezone, strategy):
    engine, table, _, _ = _setup(backend_name, unit, timezone, strategy)
    unknown = [("any", 0, 0), ("authored", 0, 0)]

    for context in ({}, {"moment": None}):
        rows = relation(engine.explain(context).frame).to_polars().sort("rule_name")
        assert rows.select("rule_name", "__t_moment", "__specificity").rows() == unknown

    for name, frame in _contexts(unit, timezone).items():
        batch = relation(engine.evaluate_batch(frame, context_id_field="request_id").survivors).to_polars()
        assert sorted(batch.select("rule_name", "__specificity").rows()) == [("any", 0), ("authored", 0)], name
        matches = table.match(relation(frame)).fetch().matches.to_polars()
        assert matches["specificity"].to_list() == [0] * matches.height and matches.height >= 1, name


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("unit", ["s", "ms", "us"])
@pytest.mark.parametrize("timezone", ["naive", "utc"])
def test_concrete_datetime_still_matches(backend_name, unit, timezone):
    engine, table, _, base = _setup(backend_name, unit, timezone, MatchStrategy.EXACT)
    rows = relation(engine.explain({"moment": base}).frame).to_polars().filter(pl.col("rule_name") == "authored")
    assert rows.select("__t_moment", "__specificity").rows() == [(1, 1)]
    matches = table.match(relation(pl.DataFrame({"request_id": [1], "moment": [base]}))).fetch().matches
    assert matches.to_polars()["specificity"].to_list() == [1]


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("unit", ["s", "ms", "us"])
@pytest.mark.parametrize("timezone", ["naive", "utc"])
@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: s.value)
def test_declared_datetime_wildcard_rule_is_dont_care(backend_name, unit, timezone, strategy):
    engine, _, _, base = _setup(backend_name, unit, timezone, strategy)
    rows = relation(engine.explain({"moment": base}).frame).to_polars().filter(pl.col("rule_name") == "any")
    assert rows.select("__t_moment", "__survived", "__specificity").rows() == [(0, True, 0)]


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("timezone", ["naive", "utc"])
def test_datetime_presence_keeps_null_distinct_from_omission(backend_name, timezone):
    field, base = _field("ms", timezone)
    engine = ExpressionRulesEngine(
        build_backend_df(backend_name, {"rule_name": ["null"], "required": ["IS_NULL"]}, table_name="presence_rules"),
        DimensionsMetadata(
            fields=(field,),
            dimensions=(Dimension(dimension_name="moment", context_field="moment",
                                  rule_field="required", match_strategy=MatchStrategy.PRESENCE),),
        ),
    )
    scores = [
        relation(engine.explain(context).frame).to_polars()["__t_moment"].to_list()
        for context in ({}, {"moment": None}, {"moment": base})
    ]
    assert scores == [[0], [1], [-1]]
