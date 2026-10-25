"""Private stateless owners of typed values, independent of matching strategies."""

from __future__ import annotations

import datetime as dt
import typing as t
from decimal import Decimal

from mountainash import DecimalDtype
from mountainash.core.dtypes import TimestampDtype

from mountainash_rules.core.constants import (
    UNKNOWN,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    UNKNOWN_NUMERIC,
    DataType,
)

if t.TYPE_CHECKING:
    from mountainash_rules.core.aggregate import Aggregate
    from mountainash_rules.core.dimension import ContextField


_DATETIME_STEPS = {
    "s": dt.timedelta(seconds=1),
    "ms": dt.timedelta(milliseconds=1),
    "us": dt.timedelta(microseconds=1),
}
_ROUNDING = frozenset({"half_to_even", "half_away_from_zero"})


def _valid_precision(declaration: ContextField | Aggregate) -> bool:
    return (
        declaration.precision is not None
        and declaration.scale is not None
        and 0 <= declaration.scale <= declaration.precision <= 16
    )


class TypeFamily:
    """Shared behavior; declarations carry configuration, never family instances."""

    __slots__ = ()
    python_type: t.ClassVar[type]
    is_numeric = False
    is_temporal = False
    is_ordered = False
    is_unordered = False
    supports_pattern = False
    supports_table_equality = True
    ordered_refusal: t.ClassVar[str | None] = None
    conventional_marker: t.ClassVar[t.Any] = UNKNOWN

    def validate_field(self, field: ContextField) -> None:
        self._validate_field_decimal(field)
        self._validate_field_datetime(field)

    def _validate_field_decimal(self, field: ContextField) -> None:
        if any(value is not None for value in (field.precision, field.scale, field.rounding)):
            raise ValueError("precision, scale, and rounding require DECIMAL")

    def _validate_field_datetime(self, field: ContextField) -> None:
        if field.datetime_unit is not None or field.timezone is not None:
            raise ValueError("datetime_unit/timezone require DATETIME")

    def validate_output(self, output: Aggregate) -> None:
        self._validate_output_decimal(output)
        self._validate_output_datetime(output)

    def _validate_output_decimal(self, output: Aggregate) -> None:
        if any(value is not None for value in (output.precision, output.scale, output.rounding)):
            raise ValueError("Decimal declarations require DECIMAL outputs")

    def _validate_output_datetime(self, output: Aggregate) -> None:
        if output.timezone is not None:
            raise ValueError("timezone requires DATETIME outputs")

    def decimal_dtype(self, declaration: ContextField | Aggregate) -> DecimalDtype | None:
        return None

    def value_dtype(self, field: ContextField) -> t.Any:
        return self.python_type

    def parse(self, text: str) -> t.Any:
        return self.python_type(text)

    def format(self, value: t.Any) -> str:
        return str(value)

    def successor(self, field: ContextField, value: t.Any) -> t.Any:
        raise ValueError(f"No ordered successor for {value!r} in {field.data_type.value}")


class StringFamily(TypeFamily):
    __slots__ = ()
    python_type = str
    is_unordered = True
    supports_pattern = True


class IntegerFamily(TypeFamily):
    __slots__ = ()
    python_type = int
    is_numeric = True
    is_ordered = True
    conventional_marker = UNKNOWN_NUMERIC

    def successor(self, field: ContextField, value: t.Any) -> t.Any:
        if type(value) is int:
            return value + 1
        return super().successor(field, value)


class FloatFamily(TypeFamily):
    __slots__ = ()
    python_type = float
    is_numeric = True
    supports_table_equality = False
    conventional_marker = UNKNOWN_NUMERIC
    ordered_refusal = "FLOAT ordered dimensions require DECIMAL"


class DecimalFamily(TypeFamily):
    __slots__ = ()
    python_type = Decimal
    is_numeric = True
    is_ordered = True
    conventional_marker = UNKNOWN_NUMERIC

    def _validate_field_decimal(self, field: ContextField) -> None:
        if not _valid_precision(field):
            raise ValueError("DECIMAL fields require precision 1..16 and scale within precision")
        if field.rounding not in _ROUNDING:
            raise ValueError("DECIMAL fields require supported rounding")

    def _validate_output_decimal(self, output: Aggregate) -> None:
        if not _valid_precision(output):
            raise ValueError("DECIMAL outputs require precision and scale")
        if output.rounding not in _ROUNDING:
            raise ValueError("DECIMAL outputs require supported rounding")

    def decimal_dtype(self, declaration: ContextField | Aggregate) -> DecimalDtype | None:
        return DecimalDtype(declaration.precision, declaration.scale) if declaration.precision is not None else None

    def format(self, value: t.Any) -> str:
        return format(value, "f")

    def successor(self, field: ContextField, value: t.Any) -> t.Any:
        if isinstance(value, Decimal):
            return value + Decimal(1).scaleb(-field.scale)
        return super().successor(field, value)


class BooleanFamily(TypeFamily):
    __slots__ = ()
    python_type = bool
    is_unordered = True

    def parse(self, text: str) -> bool:
        if text not in ("true", "false"):
            raise ValueError(f"Invalid canonical BOOL marker {text!r}")
        return text == "true"

    def format(self, value: t.Any) -> str:
        return "true" if value else "false"


class DateFamily(TypeFamily):
    __slots__ = ()
    python_type = dt.date
    is_temporal = True
    is_ordered = True
    conventional_marker = UNKNOWN_DATE

    def parse(self, text: str) -> dt.date:
        return dt.date.fromisoformat(text)

    def format(self, value: t.Any) -> str:
        return value.isoformat()

    def successor(self, field: ContextField, value: t.Any) -> t.Any:
        if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
            return value + dt.timedelta(days=1)
        return super().successor(field, value)


class DatetimeFamily(TypeFamily):
    __slots__ = ()
    python_type = dt.datetime
    is_temporal = True
    is_ordered = True
    conventional_marker = UNKNOWN_DATETIME

    def _validate_field_datetime(self, field: ContextField) -> None:
        if field.datetime_unit is None or field.timezone is None:
            raise ValueError("DATETIME fields require datetime_unit and timezone")

    def _validate_output_datetime(self, output: Aggregate) -> None:
        if output.timezone not in {"naive", "utc"}:
            raise ValueError("DATETIME outputs require timezone")

    def value_dtype(self, field: ContextField) -> TimestampDtype:
        utc = field.timezone == "utc"
        return TimestampDtype(precision=6, timezone_aware=utc, timezone="UTC" if utc else None)

    def parse(self, text: str) -> dt.datetime:
        return dt.datetime.fromisoformat(text)

    def format(self, value: t.Any) -> str:
        return value.isoformat()

    def successor(self, field: ContextField, value: t.Any) -> t.Any:
        if isinstance(value, dt.datetime):
            return value + _DATETIME_STEPS[field.datetime_unit]
        return super().successor(field, value)


_FAMILIES: dict[DataType, TypeFamily] = {
    DataType.STR: StringFamily(),
    DataType.INT: IntegerFamily(),
    DataType.FLOAT: FloatFamily(),
    DataType.DECIMAL: DecimalFamily(),
    DataType.BOOL: BooleanFamily(),
    DataType.DATE: DateFamily(),
    DataType.DATETIME: DatetimeFamily(),
}
_PYTHON_TYPES = {family.python_type: data_type for data_type, family in _FAMILIES.items()}


def type_family(data_type: DataType) -> TypeFamily:
    return _FAMILIES[data_type]


def python_data_type(value: type) -> DataType | None:
    return _PYTHON_TYPES.get(value)
