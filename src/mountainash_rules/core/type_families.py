"""Private stateless owners of typed values, independent of matching strategies."""

from __future__ import annotations

import datetime as dt
import typing as t
from decimal import Decimal
from itertools import product

import mountainash.expressions as ma
from mountainash import (
    CaseFailureBehaviour,
    DecimalDtype,
    MountainashDtype,
    ValueKind,
    boolean_value,
    text_value,
    value_kind,
)
from mountainash.core.dtypes import ListDtype, TimestampDtype, dtype_family
from mountainash.expressions import BaseExpressionAPI

from mountainash_rules.core.constants import (
    UNKNOWN,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    UNKNOWN_NUMERIC,
    BooleanCoercion,
    DataType,
)
from mountainash_rules.core.numeric import (
    _FLOAT_TYPES,
    _INTEGER_TYPES,
    canonicalize_set_expr,
    decimal_operand,
    exact_literal,
    literal_dtype,
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
    _scalar_dtypes: t.ClassVar[frozenset] = frozenset()
    _rule_cast: t.ClassVar[type | None] = None
    _empty_element: t.ClassVar[MountainashDtype]

    def validate_field(self, field: ContextField) -> None:
        self._validate_field_decimal(field)
        self._validate_field_datetime(field)

    def _validate_field_decimal(self, field: ContextField) -> None:
        if any(
            value is not None
            for value in (field.precision, field.scale, field.rounding)
        ):
            raise ValueError("precision, scale, and rounding require DECIMAL")

    def _validate_field_datetime(self, field: ContextField) -> None:
        if field.datetime_unit is not None or field.timezone is not None:
            raise ValueError("datetime_unit/timezone require DATETIME")

    def validate_output(self, output: Aggregate) -> None:
        self._validate_output_decimal(output)
        self._validate_output_datetime(output)

    def _validate_output_decimal(self, output: Aggregate) -> None:
        if any(
            value is not None
            for value in (output.precision, output.scale, output.rounding)
        ):
            raise ValueError("Decimal declarations require DECIMAL outputs")

    def _validate_output_datetime(self, output: Aggregate) -> None:
        if output.timezone is not None:
            raise ValueError("timezone requires DATETIME outputs")

    def decimal_dtype(
        self, declaration: ContextField | Aggregate
    ) -> DecimalDtype | None:
        return None

    def value_dtype(self, field: ContextField) -> t.Any:
        return self.python_type

    def parse(self, text: str) -> t.Any:
        return self.python_type(text)

    def format(self, value: t.Any) -> str:
        return str(value)

    def successor(self, field: ContextField, value: t.Any) -> t.Any:
        raise ValueError(
            f"No ordered successor for {value!r} in {field.data_type.value}"
        )

    def bind_rule(
        self,
        field: ContextField,
        raw: BaseExpressionAPI,
        source_dtype: t.Any,
        inactive: BaseExpressionAPI,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        source_dtype = dtype_family(source_dtype)
        if source_dtype not in self._scalar_dtypes:
            return ma.lit(None).cast(self.value_dtype(field)), ~inactive
        invalid = ~inactive & self._invalid_rule_value(raw, source_dtype)
        payload = ma.when(inactive | invalid).then(None).otherwise(raw)
        if self._rule_cast is not None:
            payload = payload.cast(self._rule_cast)
        return payload, invalid.fill_null(False)

    def _invalid_rule_value(
        self, raw: BaseExpressionAPI, source_dtype: t.Any
    ) -> BaseExpressionAPI:
        return raw.is_null()

    def bind_context(
        self,
        field: ContextField,
        raw: BaseExpressionAPI,
        source_dtype: t.Any,
        present: BaseExpressionAPI,
        coercion: BooleanCoercion,
        *,
        source_name: str,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        # Availability is supplied by the caller, never inferred from a cast value.
        payload = (
            ma.when(~present)
            .then(ma.lit(None))
            .otherwise(raw)
            .cast(self.value_dtype(field))
        )
        return payload, ma.lit(False)

    def context_literal(
        self, value: t.Any, *, source_name: str, coercion: BooleanCoercion
    ) -> tuple[BaseExpressionAPI, t.Any]:
        return exact_literal(value), literal_dtype(value)

    def admits_engine_element(self, dtype: t.Any) -> bool:
        return dtype in self._scalar_dtypes

    def admits_rule_element(self, dtype: t.Any, field: ContextField) -> bool:
        return self.admits_engine_element(dtype)

    def empty_list(self, field: ContextField, *, source_name: str) -> BaseExpressionAPI:
        return ma.lit([]).list.cast_items(
            dtype=ListDtype(self._empty_element), field_name=source_name
        )

    def engine_list_payload(
        self, raw: BaseExpressionAPI, source_dtype: t.Any, *, source_name: str
    ) -> BaseExpressionAPI:
        return canonicalize_set_expr(raw)


class StringFamily(TypeFamily):
    __slots__ = ()
    python_type = str
    is_unordered = True
    supports_pattern = True
    _scalar_dtypes = frozenset({MountainashDtype.STRING})
    _empty_element = MountainashDtype.STRING


class IntegerFamily(TypeFamily):
    __slots__ = ()
    python_type = int
    is_numeric = True
    is_ordered = True
    conventional_marker = UNKNOWN_NUMERIC
    _scalar_dtypes = _INTEGER_TYPES
    _rule_cast = int
    _empty_element = MountainashDtype.I64

    def _invalid_rule_value(
        self, raw: BaseExpressionAPI, source_dtype: t.Any
    ) -> BaseExpressionAPI:
        invalid = raw.is_null()
        if source_dtype is MountainashDtype.U64:
            invalid = invalid | raw.gt((1 << 63) - 1)
        return invalid

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
    _scalar_dtypes = _FLOAT_TYPES | _INTEGER_TYPES
    _rule_cast = float
    _empty_element = MountainashDtype.FP64

    def _invalid_rule_value(
        self, raw: BaseExpressionAPI, source_dtype: t.Any
    ) -> BaseExpressionAPI:
        invalid = raw.is_null()
        if source_dtype in _FLOAT_TYPES:
            invalid = invalid | ~raw.is_finite()
        return invalid

    def engine_list_payload(
        self, raw: BaseExpressionAPI, source_dtype: t.Any, *, source_name: str
    ) -> BaseExpressionAPI:
        payload = canonicalize_set_expr(raw)
        if source_dtype not in _FLOAT_TYPES:
            payload = payload.list.cast_items(
                dtype=ListDtype(MountainashDtype.FP64), field_name=source_name
            )
        return payload


class DecimalFamily(TypeFamily):
    __slots__ = ()
    python_type = Decimal
    is_numeric = True
    is_ordered = True
    conventional_marker = UNKNOWN_NUMERIC

    def _validate_field_decimal(self, field: ContextField) -> None:
        if not _valid_precision(field):
            raise ValueError(
                "DECIMAL fields require precision 1..16 and scale within precision"
            )
        if field.rounding not in _ROUNDING:
            raise ValueError("DECIMAL fields require supported rounding")

    def _validate_output_decimal(self, output: Aggregate) -> None:
        if not _valid_precision(output):
            raise ValueError("DECIMAL outputs require precision and scale")
        if output.rounding not in _ROUNDING:
            raise ValueError("DECIMAL outputs require supported rounding")

    def decimal_dtype(
        self, declaration: ContextField | Aggregate
    ) -> DecimalDtype | None:
        return (
            DecimalDtype(declaration.precision, declaration.scale)
            if declaration.precision is not None
            else None
        )

    def format(self, value: t.Any) -> str:
        return format(value, "f")

    def successor(self, field: ContextField, value: t.Any) -> t.Any:
        if isinstance(value, Decimal):
            return value + Decimal(1).scaleb(-field.scale)
        return super().successor(field, value)

    def _bind_decimal(
        self,
        field: ContextField,
        raw: BaseExpressionAPI,
        source_dtype: t.Any,
        inactive: BaseExpressionAPI,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        dtype = self.decimal_dtype(field)
        assert dtype is not None and field.rounding is not None
        operand = decimal_operand(
            raw, source_dtype, dtype, field.rounding, inactive=inactive
        )
        # Safe conversion's zero is internal; it must never become a business value.
        payload = (
            ma.when(inactive | operand.invalid).then(None).otherwise(operand.value)
        )
        return payload, operand.invalid

    def bind_rule(
        self,
        field: ContextField,
        raw: BaseExpressionAPI,
        source_dtype: t.Any,
        inactive: BaseExpressionAPI,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        return self._bind_decimal(field, raw, dtype_family(source_dtype), inactive)

    def bind_context(
        self,
        field: ContextField,
        raw: BaseExpressionAPI,
        source_dtype: t.Any,
        present: BaseExpressionAPI,
        coercion: BooleanCoercion,
        *,
        source_name: str,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        return self._bind_decimal(field, raw, source_dtype, ~present)

    def admits_engine_element(self, dtype: t.Any) -> bool:
        return isinstance(dtype, DecimalDtype)

    def empty_list(self, field: ContextField, *, source_name: str) -> BaseExpressionAPI:
        return ma.lit([]).list.cast_items(
            dtype=ListDtype(self.decimal_dtype(field)), field_name=source_name
        )


class BooleanFamily(TypeFamily):
    __slots__ = ()
    python_type = bool
    is_unordered = True
    _scalar_dtypes = frozenset({MountainashDtype.BOOL})
    _empty_element = MountainashDtype.BOOL
    _trim = " \t\n\r\f\v"
    _true = tuple(
        "".join(chars) for chars in product(*[(c, c.upper()) for c in "true"])
    ) + ("1",)
    _false = tuple(
        "".join(chars) for chars in product(*[(c, c.upper()) for c in "false"])
    ) + ("0",)

    @staticmethod
    def _numeric_source(coercion: BooleanCoercion) -> str | None:
        if coercion & BooleanCoercion.NUMERIC_TRUTHINESS:
            return "finite_number"
        if coercion & BooleanCoercion.BINARY_NUMBERS:
            return "binary_number"
        return None

    def _normalize_literal(
        self, value: t.Any, source_name: str, coercion: BooleanCoercion
    ) -> bool | None:
        if value_kind(value) is ValueKind.ABSENT:
            return None
        normalized = boolean_value(value, source="boolean")
        if normalized is not None:
            return normalized
        numeric_source = self._numeric_source(coercion)
        if numeric_source is not None:
            normalized = boolean_value(value, source=numeric_source)
            if normalized is not None:
                return normalized
        if coercion & BooleanCoercion.BOOLEAN_TEXT:
            text = text_value(value)
            if text is not None:
                token = text.strip(self._trim)
                if token in self._true:
                    return True
                if token in self._false:
                    return False
        raise ValueError(
            f"Invalid Boolean input for context field {source_name!r} under {coercion!r}"
        )

    def bind_context(
        self,
        field: ContextField,
        raw: BaseExpressionAPI,
        source_dtype: t.Any,
        present: BaseExpressionAPI,
        coercion: BooleanCoercion,
        *,
        source_name: str,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        candidates = [raw.boolean_value(source="boolean")]
        numeric_source = self._numeric_source(coercion)
        if numeric_source is not None:
            candidates.append(raw.boolean_value(source=numeric_source))
        if coercion & BooleanCoercion.BOOLEAN_TEXT:
            candidates.append(
                raw.text_value()
                .str.strip_chars(self._trim)
                .parse_boolean(
                    true_values=self._true,
                    false_values=self._false,
                    field_name=source_name,
                    failure_behavior=CaseFailureBehaviour.NULL,
                )
            )
        payload = candidates[0] if len(candidates) == 1 else ma.coalesce(*candidates)
        return payload, present & payload.is_null()

    def context_literal(
        self, value: t.Any, *, source_name: str, coercion: BooleanCoercion
    ) -> tuple[BaseExpressionAPI, t.Any]:
        kind = value_kind(value)
        if kind is ValueKind.BOOLEAN:
            value = boolean_value(value, source="boolean")
        elif kind is ValueKind.INTEGER:
            value = int(value)
            if not -(1 << 63) <= value < (1 << 64):
                # No supported native INT marker can collide with this original value.
                return ma.lit(
                    self._normalize_literal(value, source_name, coercion)
                ), None
        elif kind is ValueKind.FLOAT:
            value = float(value)
        elif kind is ValueKind.TEXT:
            value = text_value(value)
        elif kind is ValueKind.ABSENT:
            value = None
        elif literal_dtype(value) in (None, MountainashDtype.LIST):
            raise ValueError(f"Invalid Boolean input for context field {source_name!r}")
        return exact_literal(value), literal_dtype(value)

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
    _scalar_dtypes = frozenset({MountainashDtype.DATE})
    _empty_element = MountainashDtype.DATE

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
    _scalar_dtypes = frozenset({MountainashDtype.TIMESTAMP})
    _empty_element = MountainashDtype.TIMESTAMP

    def bind_context(
        self,
        field: ContextField,
        raw: BaseExpressionAPI,
        source_dtype: t.Any,
        present: BaseExpressionAPI,
        coercion: BooleanCoercion,
        *,
        source_name: str,
    ) -> tuple[BaseExpressionAPI, BaseExpressionAPI]:
        payload, invalid = super().bind_context(
            field, raw, source_dtype, present, coercion, source_name=source_name
        )
        return payload.dt.truncate(f"1{field.datetime_unit}"), invalid

    def admits_engine_element(self, dtype: t.Any) -> bool:
        return isinstance(dtype, TimestampDtype) or super().admits_engine_element(dtype)

    def admits_rule_element(self, dtype: t.Any, field: ContextField) -> bool:
        if isinstance(dtype, TimestampDtype):
            aware = dtype.timezone_aware
            return not isinstance(aware, bool) or aware == (field.timezone == "utc")
        return super().admits_rule_element(dtype, field)

    def _validate_field_datetime(self, field: ContextField) -> None:
        if field.datetime_unit is None or field.timezone is None:
            raise ValueError("DATETIME fields require datetime_unit and timezone")

    def _validate_output_datetime(self, output: Aggregate) -> None:
        if output.timezone not in {"naive", "utc"}:
            raise ValueError("DATETIME outputs require timezone")

    def value_dtype(self, field: ContextField) -> TimestampDtype:
        utc = field.timezone == "utc"
        return TimestampDtype(
            precision=6, timezone_aware=utc, timezone="UTC" if utc else None
        )

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
_PYTHON_TYPES = {
    family.python_type: data_type for data_type, family in _FAMILIES.items()
}


def type_family(data_type: DataType) -> TypeFamily:
    return _FAMILIES[data_type]


def python_data_type(value: type) -> DataType | None:
    return _PYTHON_TYPES.get(value)
