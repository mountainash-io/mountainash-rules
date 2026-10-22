"""Unsatisfiable-rule warnings come from the shared field-family coverage."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import polars as pl
import pytest
from tests.decision_table.construction_support import rules_relation, run_stages
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.prevalidate import DataContractError, prevalidate

BACKENDS = ["polars", "ibis-duckdb"]
M = MatchStrategy
INT = ContextField(name="n", data_type=DataType.INT, wildcard=-1)


def meta(field, *dims, outputs=()):
    return DimensionsMetadata(
        uuid_column="rule_id",
        fields=(field,),
        dimensions=tuple(
            Dimension(dimension_name=f"d{i}", context_field=field.name, match_strategy=s, **kw)
            for i, (s, kw) in enumerate(dims)
        ),
        outputs=tuple(outputs),
    )


def warned(backend, data, metadata):
    validated = prevalidate(rules_relation(backend, data), metadata)
    return [(w.kind, w.severity, w.rule_uuids) for w in validated.warnings]


UNSAT = [("unsatisfiable_rule", "warning", ("bad",))]


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_range_reversed_warns_valid_and_equal_endpoints_do_not(backend_name):
    m = meta(INT, (M.RANGE, {"range_min_field": "lo", "range_max_field": "hi"}))
    data = {"rule_id": ["bad", "ok", "eq"], "lo": [7, 3, 3], "hi": [3, 7, 3]}
    assert warned(backend_name, data, m) == UNSAT


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(
    "dims,data",
    [
        ([(M.EXACT, {"rule_field": "a"}), (M.NOT_EQUAL, {"rule_field": "b"})], {"a": [4, 4], "b": [4, 5]}),
        (
            [(M.RANGE, {"range_min_field": "a", "range_max_field": "b"}),
             (M.RANGE, {"range_min_field": "c", "range_max_field": "d"})],
            {"a": [1, 1], "b": [2, 6], "c": [5, 5], "d": [6, 6]},
        ),
        ([(M.SET_MEMBERSHIP, {"rule_field": "a"}), (M.EXACT, {"rule_field": "b"})], {"a": [[1, 3], [1, 3]], "b": [2, 3]}),
        ([(M.MIN, {"rule_field": "a"}), (M.MAX, {"rule_field": "b"})], {"a": [10, 5], "b": [5, 10]}),
        ([(M.SET_EXCLUSION, {"rule_field": "a"}), (M.EXACT, {"rule_field": "b"})], {"a": [[4, 5], [4, 5]], "b": [4, 6]}),
    ],
)
def test_same_field_contradictions_warn_and_satisfiable_conjunctions_do_not(backend_name, dims, data):
    rows = {"rule_id": ["bad", "ok"], **data}
    assert warned(backend_name, rows, meta(INT, *dims)) == UNSAT


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_string_contradictions(backend_name):
    field = ContextField(name="s", data_type=DataType.STR, wildcard="*")
    m = meta(field, (M.EXACT, {"rule_field": "a"}), (M.NOT_EQUAL, {"rule_field": "b"}))
    data = {"rule_id": ["bad", "ok", "any"], "a": ["x", "x", "*"], "b": ["x", "y", "x"]}
    assert warned(backend_name, data, m) == UNSAT
    m = meta(field, (M.EXACT, {"rule_field": "a"}), (M.EXACT, {"rule_field": "b"}))
    data = {"rule_id": ["bad", "ok", "any"], "a": ["x", "x", "*"], "b": ["y", "x", "*"]}
    assert warned(backend_name, data, m) == UNSAT


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_decimal_endpoints_use_declared_scale(backend_name):
    field = ContextField(name="d", data_type=DataType.DECIMAL, precision=6, scale=2, rounding="half_to_even", wildcard=Decimal("-1.00"))
    m = meta(field, (M.MIN, {"rule_field": "a"}), (M.MAX, {"rule_field": "b"}))
    frame = pl.DataFrame(
        {"rule_id": ["bad", "eq"], "a": [Decimal("1.01"), Decimal("1.00")], "b": [Decimal("1.00"), Decimal("1.00")]},
        schema={"rule_id": pl.String, "a": pl.Decimal(6, 2), "b": pl.Decimal(6, 2)},
    )
    assert warned(backend_name, frame, m) == UNSAT


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_datetime_endpoints_use_declared_unit(backend_name):
    field = ContextField(name="t", data_type=DataType.DATETIME, datetime_unit="s", timezone="naive", wildcard=dt.datetime(1970, 1, 1))
    m = meta(field, (M.MIN, {"rule_field": "a"}), (M.MAX, {"rule_field": "b"}))
    base = dt.datetime(2024, 1, 1, 12, 0, 0)
    frame = pl.DataFrame({
        "rule_id": ["bad", "eq"],
        "a": [base + dt.timedelta(seconds=1), base],
        "b": [base, base],
    })
    assert warned(backend_name, frame, m) == UNSAT


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_truthy_mappings_blocking_and_conflicts(backend_name):
    field = ContextField(name="f", data_type=DataType.STR, wildcard="*", truthy={"yes": 1, "no": -1, "dunno": 0})
    m = meta(field, (M.TRUTHY, {"rule_field": "a"}), (M.TRUTHY, {"rule_field": "b"}))
    data = {"rule_id": ["bad", "ok", "zero", "any"], "a": ["yes", "yes", "dunno", "*"], "b": ["no", "yes", "no", "no"]}
    assert warned(backend_name, data, m) == UNSAT

    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, {"rule_id": ["x", "y"], "a": ["maybe", "yes"], "b": ["yes", "yes"]}), m)
    findings = [f for f in error.value.breaches if f.kind == "unmapped_truthy_value"]
    assert [f.rule_uuids for f in findings] == [("x",)]
    assert error.value.warnings == ()


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_truthy_explicit_null_mapping_is_not_unmapped(backend_name):
    field = ContextField(
        name="f", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True, truthy={True: 1, False: -1, None: 0}
    )
    m = meta(field, (M.TRUTHY, {"rule_field": "a"}))
    data = pl.DataFrame({"rule_id": ["t", "f", "n"], "a": pl.Series([True, False, None], dtype=pl.Boolean)})
    assert warned(backend_name, data, m) == []


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_presence_null_with_concrete_condition_warns(backend_name):
    field = ContextField(name="s", data_type=DataType.STR, wildcard="*")
    m = meta(field, (M.EXACT, {"rule_field": "a"}), (M.PRESENCE, {"rule_field": "p"}))
    data = {"rule_id": ["bad", "ok", "any"], "a": ["x", "x", "*"], "p": ["IS_NULL", "IS_NOT_NULL", "IS_NULL"]}
    assert warned(backend_name, data, m) == UNSAT


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_unsatisfiable_rule_with_another_breach_reports_both(backend_name):
    out = Aggregate(output_name="fee", contribution_column="fee", data_type=DataType.INT)
    m = meta(INT, (M.RANGE, {"range_min_field": "lo", "range_max_field": "hi"}), outputs=[out])
    data = pl.DataFrame({"rule_id": ["bad", "ok"], "lo": [7, 1], "hi": [3, 2], "fee": pl.Series([None, 1], dtype=pl.Int64)})
    source = rules_relation(backend_name, data)
    with pytest.raises(DataContractError) as error:
        prevalidate(source, m)
    assert [(f.kind, f.rule_uuids) for f in error.value.breaches] == [("null_contribution", ("bad",))]
    assert [(w.kind, w.severity, w.rule_uuids) for w in error.value.warnings] == UNSAT
    assert source.to_dicts() == rules_relation(backend_name, data).to_dicts()


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_construction_receives_all_rules_and_unsatisfiable_rules_have_empty_coverage(backend_name):
    m = meta(INT, (M.RANGE, {"range_min_field": "lo", "range_max_field": "hi"}))
    data = {"rule_id": ["bad", "ok"], "lo": [7, 3], "hi": [3, 7]}
    validated = prevalidate(rules_relation(backend_name, data), m)
    assert sorted(r["rule_id"] for r in validated.rules.to_dicts()) == ["bad", "ok"]
    _, _, coverage = run_stages(validated)
    covered = {row["source_uuid"] for row in coverage.cell_coverage.to_dicts()}
    assert "bad" not in covered and "ok" in covered
