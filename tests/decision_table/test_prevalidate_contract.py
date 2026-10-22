"""Public data-contract coverage for DecisionTable prevalidation."""

from __future__ import annotations

import datetime as dt
from dataclasses import FrozenInstanceError
from decimal import Decimal

import ibis
import polars as pl
import pytest
from mountainash.relations import relation
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.prevalidate import DataContractError, prevalidate


BACKENDS = ["polars", "ibis-duckdb"]


def rules(backend_name, data, name="prevalidate_contract"):
    frame = pl.DataFrame(data)
    if backend_name == "ibis-duckdb":
        frame = ibis.duckdb.connect().create_table(name, frame, overwrite=True)
    return relation(frame)


def declared(field, strategy=MatchStrategy.EXACT, *, rule_field="value", **dimension):
    return DimensionsMetadata(
        uuid_column="rule_id",
        fields=(field,),
        dimensions=(
            Dimension(
                dimension_name="condition",
                context_field=field.name,
                rule_field=rule_field,
                match_strategy=strategy,
                **dimension,
            ),
        ),
    )


def breach_kinds(error):
    return {finding.kind for finding in error.value.breaches}


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_capture_owns_source_snapshot_and_deep_copied_metadata(backend_name):
    original = declared(ContextField(name="region", data_type=DataType.STR, wildcard="*"))
    source = rules(backend_name, {"rule_id": ["r1"], "value": ["EU"], "notes": ["kept"]})

    validated = prevalidate(source, original)
    original.dimensions[0].rule_field = "mutated"

    assert validated.rules.to_dicts() == source.to_dicts()
    assert validated.source_metadata.dimensions[0].resolved_rule_field == "value"
    with pytest.raises(FrozenInstanceError):
        validated.rules = source


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_schema_breaches_and_typed_empty_admission(backend_name):
    ranged = declared(
        ContextField(name="amount", data_type=DataType.INT, wildcard=-1),
        MatchStrategy.RANGE,
        rule_field=None,
        range_min_field="lower",
        range_max_field="upper",
    ).model_copy(
        update={
            "outputs": (
                Aggregate(output_name="fee", contribution_column="fee", data_type=DataType.INT),
            )
        }
    )
    with pytest.raises(DataContractError) as error:
        prevalidate(rules(backend_name, {"rule_id": ["r1"], "lower": [1]}), ranged)
    assert {"missing_rule_column", "missing_contribution_column"} <= breach_kinds(error)

    empty = relation(pl.DataFrame(schema={"rule_id": pl.String, "value": pl.String}))
    assert prevalidate(empty, declared(ContextField(name="region", data_type=DataType.STR, wildcard="*"))).rules.count_rows() == 0


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_scalar_and_list_dtype_contracts(backend_name):
    scalar = declared(ContextField(name="active", data_type=DataType.BOOL, wildcard=False))
    with pytest.raises(DataContractError) as error:
        prevalidate(rules(backend_name, {"rule_id": ["bad"], "value": ["yes"]}), scalar)
    assert "incompatible_rule_dtype" in breach_kinds(error) or "incompatible_rule_value" in breach_kinds(error)

    set_metadata = declared(
        ContextField(name="active", data_type=DataType.BOOL, wildcard=False), MatchStrategy.SET_MEMBERSHIP
    )
    with pytest.raises(DataContractError) as error:
        prevalidate(rules(backend_name, {"rule_id": ["not-list"], "value": [True]}), set_metadata)
    assert {"incompatible_rule_dtype", "incompatible_list_dtype"} & breach_kinds(error)

    with pytest.raises(DataContractError) as error:
        prevalidate(rules(backend_name, {"rule_id": ["null-element"], "value": [[True, None]]}), set_metadata)
    assert "null_list_element" in breach_kinds(error)

    with pytest.raises(DataContractError) as error:
        prevalidate(rules(backend_name, {"rule_id": ["bad-element"], "value": [["no"]]}), set_metadata)
    assert "incompatible_list_element" in breach_kinds(error)


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        (DataType.INT, 4),
        (DataType.DECIMAL, Decimal("4.00")),
        (DataType.DATE, dt.date(2026, 1, 1)),
        (DataType.DATETIME, dt.datetime(2026, 1, 1)),
    ],
)
@pytest.mark.parametrize("strategy", [MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL, MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION, MatchStrategy.RANGE, MatchStrategy.MIN, MatchStrategy.MAX])
def test_ordered_strategy_matrix_accepts_declared_values(data_type, value, strategy):
    field_args = {"name": "ordered", "data_type": data_type, "wildcard": value}
    if data_type is DataType.DECIMAL:
        field_args.update(precision=8, scale=2, rounding="half_to_even")
    if data_type is DataType.DATETIME:
        field_args.update(datetime_unit="us", timezone="naive")
    field = ContextField(**field_args)
    if strategy is MatchStrategy.RANGE:
        metadata = declared(field, strategy, rule_field=None, range_min_field="minimum", range_max_field="maximum")
        source = relation({"rule_id": ["r1"], "minimum": [value], "maximum": [value]})
    else:
        metadata = declared(field, strategy)
        source = relation({"rule_id": ["r1"], "value": [[value] if strategy in {MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION} else value]})
    assert prevalidate(source, metadata).warnings == ()


@pytest.mark.parametrize("data_type,value", [(DataType.STR, "EU"), (DataType.BOOL, True)])
@pytest.mark.parametrize("strategy", [MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL, MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION])
def test_unordered_strategy_matrix_accepts_declared_values(data_type, value, strategy):
    metadata = declared(ContextField(name="unordered", data_type=data_type, wildcard=value), strategy)
    source = relation({"rule_id": ["r1"], "value": [[value] if strategy in {MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION} else value]})
    assert prevalidate(source, metadata).warnings == ()


@pytest.mark.parametrize("strategy", [MatchStrategy.REGEX, MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS])
def test_string_pattern_strategies_accept_ternary_authorship(strategy):
    metadata = declared(ContextField(name="text", data_type=DataType.STR, wildcard="*", truthy={"pattern": 1}), strategy)
    assert prevalidate(relation({"rule_id": ["r1"], "value": ["pattern"]}), metadata).warnings == ()


def test_truthy_presence_outcome_and_context_regex_contracts():
    truthy = declared(ContextField(name="enabled", data_type=DataType.BOOL, wildcard=False, truthy={True: 1, False: -1}), MatchStrategy.TRUTHY)
    assert prevalidate(relation({"rule_id": ["r1"], "value": [True]}), truthy).warnings == ()

    presence = declared(ContextField(name="enabled", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True), MatchStrategy.PRESENCE)
    outcome = declared(ContextField(name="enabled", data_type=DataType.BOOL, wildcard=False), MatchStrategy.OUTCOME)
    assert prevalidate(relation({"rule_id": ["r1"], "value": ["IS_NULL"]}), presence).warnings == ()
    assert prevalidate(relation({"rule_id": ["r1"], "value": [0]}), outcome).warnings == ()

    context_regex = declared(ContextField(name="text", data_type=DataType.STR, wildcard="*"), MatchStrategy.CONTEXT_REGEX, rule_field=None, regex_pattern="^A")
    assert prevalidate(relation({"rule_id": ["r1"]}), context_regex).warnings == ()


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_presence_accepts_the_fields_declared_wildcard_and_rejects_other_text(backend_name):
    presence = declared(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"), MatchStrategy.PRESENCE
    )
    accepted = {"rule_id": ["null", "present", "any"], "value": ["IS_NULL", "IS_NOT_NULL", "*"]}
    assert prevalidate(rules(backend_name, accepted), presence).warnings == ()

    with pytest.raises(DataContractError) as error:
        prevalidate(rules(backend_name, {"rule_id": ["bad", "any"], "value": ["MAYBE", "*"]}), presence)
    findings = [f for f in error.value.breaches if f.kind == "invalid_presence_value"]
    assert [f.rule_uuids for f in findings] == [("bad",)]

    null_wildcard = declared(
        ContextField(name="region", data_type=DataType.STR, wildcard=None, wildcard_declared=True),
        MatchStrategy.PRESENCE,
    )
    data = {"rule_id": ["null", "any"], "value": ["IS_NULL", None]}
    assert prevalidate(rules(backend_name, data), null_wildcard).warnings == ()


@pytest.mark.parametrize(
    "field,strategy",
    [
        (ContextField(name="text", data_type=DataType.STR, wildcard="*"), MatchStrategy.RANGE),
        (ContextField(name="flag", data_type=DataType.BOOL, wildcard=False), MatchStrategy.REGEX),
    ],
)
def test_wrong_strategy_family_pairings_are_blocked(field, strategy):
    with pytest.raises(ValueError):
        declared(field, strategy)
    with pytest.raises(ValueError):
        declared(ContextField(name="ratio", data_type=DataType.FLOAT, wildcard=-1.0), MatchStrategy.MIN)


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_null_wildcards_and_set_element_contracts(backend_name):
    nullable = declared(ContextField(name="active", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True))
    assert prevalidate(rules(backend_name, {"rule_id": ["r1"], "value": pl.Series([None], dtype=pl.Boolean)}), nullable).warnings == ()

    nonnullable = declared(ContextField(name="active", data_type=DataType.BOOL, wildcard=False))
    with pytest.raises(DataContractError) as error:
        prevalidate(rules(backend_name, {"rule_id": ["r1"], "value": pl.Series([None], dtype=pl.Boolean)}), nonnullable)
    assert "null_rule_cell" in breach_kinds(error)


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_scope_contribution_identity_and_output_type_contracts(backend_name):
    field = ContextField(name="region", data_type=DataType.STR, wildcard="*")
    base = declared(field).model_copy(
        update={
            "scope_column": "scope",
            "outputs": (
                Aggregate(scope="fees", output_name="result", contribution_column="fee", data_type=DataType.INT),
                Aggregate(scope="rates", output_name="result", contribution_column="rate", data_type=DataType.STR),
            ),
        }
    )
    source = rules(backend_name, {"rule_id": [None, "same", "same"], "scope": [None, "fees", "fees"], "value": ["EU", "EU", "US"], "fee": [None, None, "wrong"], "rate": ["allowed", "allowed", "allowed"]})
    with pytest.raises(DataContractError) as error:
        prevalidate(source, base)
    findings = {finding.kind: finding for finding in error.value.breaches}
    assert {"output_type_conflict", "missing_uuid", "null_scope", "duplicate_uuid", "null_contribution", "incompatible_contribution_dtype"} <= findings.keys()
    assert findings["duplicate_uuid"].rule_uuids == ("same",)

    equal_types = base.model_copy(update={"outputs": (
        Aggregate(scope="fees", output_name="result", contribution_column="fee", data_type=DataType.INT),
        Aggregate(scope="rates", output_name="result", contribution_column="rate", data_type=DataType.INT),
    )})
    accepted = rules(backend_name, {"rule_id": ["same", "same"], "scope": ["fees", "rates"], "value": ["EU", "EU"], "fee": [1, 99], "rate": [99, 1]})
    assert prevalidate(accepted, equal_types).warnings == ()


def test_implicit_default_scope_and_datetime_metadata_contracts():
    metadata = declared(ContextField(name="region", data_type=DataType.STR, wildcard="*"))
    assert prevalidate(relation({"rule_id": ["one", "two"], "value": ["EU", "US"]}), metadata).warnings == ()

    with pytest.raises(ValueError):
        ContextField(name="when", data_type=DataType.DATETIME, wildcard=dt.datetime(2026, 1, 1), timezone="naive")


@pytest.mark.parametrize("timezone,value", [("naive", dt.datetime(2026, 1, 1)), ("utc", dt.datetime(2026, 1, 1, tzinfo=dt.UTC))])
def test_datetime_timezone_interpretation_contract(timezone, value):
    field = ContextField(name="when", data_type=DataType.DATETIME, wildcard=value, datetime_unit="us", timezone=timezone)
    assert prevalidate(relation({"rule_id": ["r1"], "value": [value]}), declared(field)).warnings == ()
