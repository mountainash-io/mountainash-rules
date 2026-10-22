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
