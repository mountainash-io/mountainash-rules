"""Type-owned value contracts, separate from public lifecycle/region tests."""

import datetime as dt
from decimal import Decimal

import pytest
from mountainash import DecimalDtype
from mountainash.core.dtypes import TimestampDtype

from mountainash_rules import Aggregate, ContextField, DataType
from mountainash_rules.core.type_families import python_data_type, type_family


@pytest.mark.parametrize(
    "data_type,value,text",
    [
        (DataType.STR, "*", "*"),
        (DataType.INT, -12, "-12"),
        (DataType.FLOAT, 0.25, "0.25"),
        (DataType.DECIMAL, Decimal("1.2300"), "1.2300"),
        (DataType.BOOL, False, "false"),
        (DataType.DATE, dt.date(2026, 10, 25), "2026-10-25"),
        (DataType.DATETIME, dt.datetime(2026, 10, 25, 12, 0, 0, 123456, dt.timezone.utc),
         "2026-10-25T12:00:00.123456+00:00"),
    ],
)
def test_codec_preserves_canonical_wire_value(data_type, value, text):
    family = type_family(data_type)
    assert family.format(value) == text
    parsed = family.parse(text)
    assert type(parsed) is type(value)
    assert parsed == value
    if data_type is DataType.DECIMAL:
        assert parsed.as_tuple() == value.as_tuple()


@pytest.mark.parametrize("text", ["True", "1", "FALSE"])
def test_boolean_codec_rejects_noncanonical_text(text):
    with pytest.raises(ValueError):
        type_family(DataType.BOOL).parse(text)


def test_python_declaration_lookup_does_not_infer_subclasses():
    class BusinessInt(int):
        pass

    assert python_data_type(bool) is DataType.BOOL
    assert python_data_type(int) is DataType.INT
    assert python_data_type(BusinessInt) is None


def test_decimal_declarations_do_not_leak_between_family_calls():
    family = type_family(DataType.DECIMAL)
    fine = ContextField(name="fine", data_type=DataType.DECIMAL, precision=7, scale=3, rounding="half_to_even")
    coarse = ContextField(name="coarse", data_type=DataType.DECIMAL, precision=4, scale=1, rounding="half_away_from_zero")
    assert family.decimal_dtype(fine) == DecimalDtype(7, 3)
    assert family.successor(fine, Decimal("2.718")) == Decimal("2.719")
    assert family.decimal_dtype(coarse) == DecimalDtype(4, 1)
    assert family.successor(coarse, Decimal("2.7")) == Decimal("2.8")
    assert family.successor(fine, Decimal("2.718")) == Decimal("2.719")


def test_datetime_descriptors_and_steps_follow_each_declaration():
    family = type_family(DataType.DATETIME)
    aware = ContextField(name="aware", data_type=DataType.DATETIME, datetime_unit="ms", timezone="utc")
    naive = ContextField(name="naive", data_type=DataType.DATETIME, datetime_unit="s", timezone="naive")
    instant = dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.timezone.utc)
    assert family.value_dtype(aware) == TimestampDtype(precision=6, timezone_aware=True, timezone="UTC")
    assert family.successor(aware, instant) == instant + dt.timedelta(milliseconds=1)
    assert family.value_dtype(naive) == TimestampDtype(precision=6, timezone_aware=False, timezone=None)
    assert family.successor(naive, instant.replace(tzinfo=None)) == dt.datetime(2026, 10, 25, 12, 0, 1)
    assert family.successor(aware, instant) == instant + dt.timedelta(milliseconds=1)


@pytest.mark.parametrize(
    "data_type,options",
    [
        (DataType.INT, {"scale": 2}),
        (DataType.DATE, {"datetime_unit": "ms"}),
        (DataType.DECIMAL, {"precision": 3, "scale": 4, "rounding": "half_to_even"}),
        (DataType.DECIMAL, {"precision": 5, "scale": 2, "rounding": "truncate"}),
        (DataType.DATETIME, {"timezone": "utc"}),
    ],
)
def test_field_checks_reject_invalid_interpretation(data_type, options):
    field = ContextField.model_construct(name="x", data_type=data_type, **options)
    with pytest.raises(ValueError):
        type_family(data_type).validate_field(field)


def test_output_datetime_needs_timezone_but_not_context_unit():
    family = type_family(DataType.DATETIME)
    output = Aggregate(output_name="event", contribution_column="at", data_type=DataType.DATETIME, timezone="utc")
    family.validate_output(output)
    with pytest.raises(ValueError):
        family.validate_output(output.model_copy(update={"timezone": None}))
    with pytest.raises(ValueError):
        family.validate_output(output.model_copy(update={"timezone": "local"}))


@pytest.mark.parametrize("data_type,value", [(DataType.INT, True), (DataType.DATE, dt.datetime(2026, 10, 25)), (DataType.FLOAT, 1.0)])
def test_successor_does_not_broaden_value_admission(data_type, value):
    field = ContextField(name="x", data_type=data_type)
    with pytest.raises(ValueError):
        type_family(data_type).successor(field, value)
