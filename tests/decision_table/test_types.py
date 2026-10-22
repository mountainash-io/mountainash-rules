from datetime import date

from mountainash import DecimalDtype

from mountainash_rules import ContextField
from mountainash_rules.core.constants import DataType


def test_decimal_context_field_uses_mountainash_decimal_descriptor():
    field = ContextField(
        name="amount",
        data_type=DataType.DECIMAL,
        precision=8,
        scale=2,
        rounding="half_away_from_zero",
    )
    assert field.decimal_dtype == DecimalDtype(8, 2)


def test_datetime_context_field_preserves_declared_resolution():
    for unit in ("s", "ms", "us"):
        field = ContextField(
            name="occurred_at",
            data_type=DataType.DATETIME,
            datetime_unit=unit,
            timezone="naive",
        )
        assert field.datetime_unit == unit


def test_context_field_wildcard_preserves_typed_scalar():
    field = ContextField(name="day", data_type=DataType.DATE, wildcard=date(2026, 10, 22))
    assert field.wildcard == date(2026, 10, 22)
    assert field.wildcard_declared


def test_decimal_successor_respects_declared_scale():
    from decimal import Decimal

    field = ContextField(
        name="amount", data_type=DataType.DECIMAL, precision=8, scale=2, rounding="half_to_even"
    )
    assert field.successor(Decimal("1.23")) == Decimal("1.24")


def test_date_successor_is_one_calendar_day():
    assert ContextField(name="day", data_type=DataType.DATE).successor(date(2026, 10, 22)) == date(2026, 10, 23)


def test_datetime_successor_uses_declared_resolution():
    from datetime import datetime

    value = datetime(2026, 10, 22, 12, 0, 0, 123456)
    for unit, expected in (
        ("s", datetime(2026, 10, 22, 12, 0, 1, 123456)),
        ("ms", datetime(2026, 10, 22, 12, 0, 0, 124456)),
        ("us", datetime(2026, 10, 22, 12, 0, 0, 123457)),
    ):
        field = ContextField(name="instant", data_type=DataType.DATETIME, datetime_unit=unit, timezone="naive")
        assert field.successor(value) == expected
