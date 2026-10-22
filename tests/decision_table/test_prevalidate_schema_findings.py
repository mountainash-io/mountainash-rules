"""Schema-level prevalidation: DATETIME outputs, timezone interpretation, missing scope, warnings."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.prevalidate import DataContractError, prevalidate

from tests.decision_table.construction_support import rules_relation

M = MatchStrategy
UTC, NAIVE = dt.timezone.utc, None
REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")
LEVEL = ContextField(name="level", data_type=DataType.INT, wildcard=-1)


def when(timezone):
    return ContextField(name="when", data_type=DataType.DATETIME, datetime_unit="us", timezone=timezone)


def dimension(name, field, strategy=M.EXACT, **kwargs):
    if strategy is not M.RANGE:
        kwargs.setdefault("rule_field", "value")
    return Dimension(dimension_name=name, context_field=field.name, match_strategy=strategy, **kwargs)


def breaches(error):
    return {finding.kind for finding in error.value.breaches}


# -- DATETIME output declarations ------------------------------------------------------------


def datetime_output(operation, timezone):
    return Aggregate(
        output_name="seen", contribution_column="seen", operation=operation, data_type=DataType.DATETIME, timezone=timezone
    )


def region_metadata(*outputs):
    return DimensionsMetadata(
        uuid_column="rule_id", fields=(REGION,), dimensions=(dimension("region", REGION, rule_field="region"),), outputs=tuple(outputs)
    )


def datetimes(count, tz):
    return pl.Series([dt.datetime(2026, 1, 1 + i, tzinfo=tz) for i in range(count)])


@pytest.mark.parametrize("operation", [AggregateOp.MIN, AggregateOp.MAX])
@pytest.mark.parametrize(("timezone", "tz"), [("utc", UTC), ("naive", NAIVE)])
def test_declared_datetime_outputs_pass_prevalidation(backend_name, operation, timezone, tz):
    data = pl.DataFrame({"rule_id": ["a", "b"], "region": ["EU", "US"], "seen": datetimes(2, tz)})
    validated = prevalidate(rules_relation(backend_name, data), region_metadata(datetime_output(operation, timezone)))
    assert validated.warnings == ()


@pytest.mark.parametrize("operation", [AggregateOp.MIN, AggregateOp.MAX])
def test_datetime_output_signatures_are_still_checked(backend_name, operation):
    data = pl.DataFrame({"rule_id": ["a", "b"], "region": ["EU", "EU"], "seen": datetimes(2, UTC)[[0, 0]]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), region_metadata(datetime_output(operation, "utc")))
    assert breaches(error) == {"duplicate_rule"}


def test_datetime_output_with_the_wrong_interpretation_or_type_is_a_breach(backend_name):
    naive = pl.DataFrame({"rule_id": ["a"], "region": ["EU"], "seen": datetimes(1, NAIVE)})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, naive), region_metadata(datetime_output(AggregateOp.MIN, "utc")))
    assert breaches(error) == {"incompatible_contribution_dtype"}

    text = pl.DataFrame({"rule_id": ["a"], "region": ["EU"], "seen": ["2026-01-01"]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, text), region_metadata(datetime_output(AggregateOp.MIN, "naive")))
    assert breaches(error) == {"incompatible_contribution_dtype"}


# -- DATETIME timezone interpretation of rule cells ------------------------------------------


@pytest.mark.parametrize(("timezone", "tz"), [("naive", UTC), ("utc", NAIVE)], ids=["aware-for-naive", "naive-for-utc"])
@pytest.mark.parametrize("strategy", [M.EXACT, M.NOT_EQUAL, M.MIN, M.MAX])
def test_scalar_rule_cells_must_match_the_declared_timezone(backend_name, timezone, tz, strategy):
    field = when(timezone)
    metadata = DimensionsMetadata(uuid_column="rule_id", fields=(field,), dimensions=(dimension("when", field, strategy),))
    data = pl.DataFrame({"rule_id": ["a"], "value": datetimes(1, tz)})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), metadata)
    assert breaches(error) == {"incompatible_rule_dtype"}


@pytest.mark.parametrize(("timezone", "tz"), [("naive", UTC), ("utc", NAIVE)], ids=["aware-for-naive", "naive-for-utc"])
def test_range_endpoints_must_match_the_declared_timezone(backend_name, timezone, tz):
    field = when(timezone)
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(field,),
        dimensions=(dimension("when", field, M.RANGE, range_min_field="lo", range_max_field="hi"),),
    )
    data = pl.DataFrame({"rule_id": ["a"], "lo": datetimes(1, tz), "hi": datetimes(1, tz)})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), metadata)
    assert breaches(error) == {"incompatible_rule_dtype"}


@pytest.mark.parametrize("strategy", [M.SET_MEMBERSHIP, M.SET_EXCLUSION])
@pytest.mark.parametrize(("timezone", "tz"), [("naive", UTC), ("utc", NAIVE)], ids=["aware-for-naive", "naive-for-utc"])
def test_list_elements_must_match_the_declared_timezone(backend_name, strategy, timezone, tz):
    field = when(timezone)
    metadata = DimensionsMetadata(uuid_column="rule_id", fields=(field,), dimensions=(dimension("when", field, strategy),))
    data = pl.DataFrame({"rule_id": ["a"], "value": [list(datetimes(2, tz))]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), metadata)
    assert breaches(error) == {"incompatible_list_element"}


@pytest.mark.parametrize(("timezone", "tz"), [("naive", NAIVE), ("utc", UTC)])
def test_matching_interpretations_pass_for_scalars_and_lists(backend_name, timezone, tz):
    field = when(timezone)
    for strategy, value in ((M.EXACT, datetimes(1, tz)), (M.SET_MEMBERSHIP, [list(datetimes(2, tz))])):
        metadata = DimensionsMetadata(uuid_column="rule_id", fields=(field,), dimensions=(dimension("when", field, strategy),))
        data = pl.DataFrame({"rule_id": ["a"], "value": value})
        assert prevalidate(rules_relation(backend_name, data), metadata).warnings == ()


# -- a missing scope column is one finding among the others ----------------------------------


def scoped_metadata(fee_column="fee"):
    outputs = tuple(
        Aggregate(scope=scope, output_name="fee", contribution_column=fee_column, data_type=DataType.INT)
        for scope in ("a", "b")
    )
    return DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION,),
        dimensions=(dimension("region", REGION, rule_field="region"),),
        outputs=outputs,
        scope_column="scope",
    )


def test_a_missing_scope_column_is_aggregated_with_the_other_findings(backend_name):
    data = pl.DataFrame({"rule_id": ["a", "a"], "region": ["EU", None]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), scoped_metadata())
    assert breaches(error) == {"missing_scope_column", "missing_contribution_column", "null_rule_cell"}


def test_a_missing_scope_column_alone_is_a_data_contract_error(backend_name):
    data = pl.DataFrame({"rule_id": ["a", "b"], "region": ["EU", "US"], "fee": [1, 2]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), scoped_metadata())
    assert breaches(error) == {"missing_scope_column"}


# -- warnings depend on their own condition columns only -------------------------------------


def range_metadata(*extra, outputs=()):
    return DimensionsMetadata(
        uuid_column="rule_id",
        fields=(LEVEL, REGION),
        dimensions=(dimension("level", LEVEL, M.RANGE, range_min_field="lo", range_max_field="hi"), *extra),
        outputs=outputs,
    )


def warning_ids(error):
    return [(w.kind, w.rule_uuids) for w in error.value.warnings]


def test_a_reversed_range_still_warns_beside_a_missing_contribution_column(backend_name):
    fee = Aggregate(output_name="fee", contribution_column="fee", data_type=DataType.INT)
    data = pl.DataFrame({"rule_id": ["bad", "ok"], "lo": [7, 1], "hi": [3, 2]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), range_metadata(outputs=(fee,)))
    assert breaches(error) == {"missing_contribution_column"}
    assert warning_ids(error) == [("unsatisfiable_rule", ("bad",))]


def test_a_reversed_range_still_warns_beside_another_dimensions_missing_or_mistyped_column(backend_name):
    missing = dimension("region", REGION, rule_field="region")
    data = pl.DataFrame({"rule_id": ["bad", "ok"], "lo": [7, 1], "hi": [3, 2]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, data), range_metadata(missing))
    assert breaches(error) == {"missing_rule_column"}
    assert warning_ids(error) == [("unsatisfiable_rule", ("bad",))]

    mistyped = pl.DataFrame({"rule_id": ["bad", "ok"], "lo": [7, 1], "hi": [3, 2], "region": [1, 2]})
    with pytest.raises(DataContractError) as error:
        prevalidate(rules_relation(backend_name, mistyped), range_metadata(missing))
    assert breaches(error) == {"incompatible_rule_dtype"}
    assert warning_ids(error) == [("unsatisfiable_rule", ("bad",))]
