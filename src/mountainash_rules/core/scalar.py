"""Exact scalar-1 recognition, transport, and discrete ordered universes."""

from __future__ import annotations

import datetime as dt
import math
import re
import struct
import typing as t

from mountainash import ValueKind, text_value, value_kind

from mountainash_rules.core.constants import (
    NOT_SET,
    NOT_SET_DATE,
    NOT_SET_DATETIME,
    NOT_SET_NUMERIC,
    UNKNOWN,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    UNKNOWN_NUMERIC,
    DataType,
)

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_UINT64_MASK = 2**64 - 1
_FLOAT_SIGN = 1 << 63
_FLOAT_FINITE_MAGNITUDE = 0x7FF0000000000000
_FLOAT_MIN_TRANSFORMED = 0x0010000000000000
_MICROSECONDS_PER_DAY = 86_400_000_000
_HEX_BITS = re.compile(r"[0-9a-f]{16}\Z")
_INT_PAYLOAD = re.compile(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z")
_DATE_PAYLOAD = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_NAIVE_DATETIME_PAYLOAD = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\Z"
)
_UTC_DATETIME_PAYLOAD = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z"
)

_RESERVED: dict[DataType, frozenset[t.Any]] = {
    DataType.STR: frozenset({UNKNOWN, NOT_SET}),
    DataType.BOOL: frozenset(),
    DataType.INT: frozenset({UNKNOWN_NUMERIC, NOT_SET_NUMERIC}),
    DataType.FLOAT: frozenset({float(UNKNOWN_NUMERIC), float(NOT_SET_NUMERIC)}),
    DataType.DATE: frozenset({UNKNOWN_DATE, NOT_SET_DATE}),
    DataType.DATETIME: frozenset({UNKNOWN_DATETIME, NOT_SET_DATETIME}),
}


def _data_type(data_type: DataType | str) -> DataType:
    try:
        return DataType(data_type)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported scalar data type: {data_type!r}") from exc


def _validate_timezone(data_type: DataType, timezone: str | None) -> None:
    if data_type is DataType.DATETIME:
        if timezone not in {"naive", "utc"}:
            raise ValueError(
                "datetime scalars require timezone='naive' or timezone='utc'"
            )
    elif timezone is not None:
        raise ValueError("timezone is valid only for datetime scalars")


def _check_reserved(value: t.Any, data_type: DataType, allow_reserved: bool) -> None:
    comparable = value.replace(tzinfo=None) if data_type is DataType.DATETIME else value
    if not allow_reserved and comparable in _RESERVED[data_type]:
        raise ValueError(f"Reserved {data_type.value} marker is not a concrete scalar")


def _unicode_scalar_text(value: t.Any) -> str:
    text = text_value(value)
    if text is None:
        raise ValueError("str scalar requires text")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
        raise ValueError("str scalar cannot contain an unpaired surrogate")
    return text


def _normalize_int(value: t.Any) -> int:
    if value_kind(value) is not ValueKind.INTEGER:
        raise ValueError("int scalar requires an integral runtime scalar")
    normalized = int(value)
    if not _INT64_MIN <= normalized <= _INT64_MAX:
        raise ValueError("int scalar must fit signed int64")
    return normalized


def _normalize_float(value: t.Any, context: bool) -> float:
    kind = value_kind(value)
    if kind is ValueKind.INTEGER:
        if not context:
            raise ValueError("float scalar requires a floating runtime scalar")
        integer = int(value)
        try:
            normalized = float(integer)
        except OverflowError as exc:
            raise ValueError(
                "integer cannot be represented as finite binary64"
            ) from exc
        if not math.isfinite(normalized) or int(normalized) != integer:
            raise ValueError("integer cannot be represented exactly as binary64")
        return normalized
    if kind is not ValueKind.FLOAT:
        raise ValueError("float scalar requires a floating runtime scalar")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError("float scalar must be finite binary64")
    # A wider physical float is admissible only when it denotes one binary64 value.
    if value != normalized:
        raise ValueError("float scalar is not losslessly representable as binary64")
    return 0.0 if normalized == 0.0 else normalized


def _is_numpy_datetime64(value: t.Any) -> bool:
    value_type = type(value)
    return value_type.__module__ == "numpy" and value_type.__name__ == "datetime64"


def _is_pandas_timestamp(value: t.Any) -> bool:
    value_type = type(value)
    return (
        value_type.__module__.startswith("pandas")
        and value_type.__name__ == "Timestamp"
    )


def _normalize_datetime_carrier(value: t.Any) -> dt.datetime:
    if _is_numpy_datetime64(value):
        if str(value) == "NaT":
            raise ValueError("datetime scalar cannot be missing")
        microsecond_value = value.astype("datetime64[us]")
        if not bool(microsecond_value.astype(value.dtype) == value):
            raise ValueError("datetime scalar would lose sub-microsecond precision")
        value = microsecond_value.astype(dt.datetime)
    elif _is_pandas_timestamp(value):
        if value.nanosecond:
            raise ValueError("datetime scalar would lose sub-microsecond precision")
        value = value.to_pydatetime()
    if not isinstance(value, dt.datetime):
        raise ValueError("datetime scalar requires a datetime runtime scalar")
    return value


def _normalize_datetime(value: t.Any, timezone: str) -> dt.datetime:
    normalized = _normalize_datetime_carrier(value)
    aware = normalized.utcoffset() is not None
    if timezone == "naive":
        if aware:
            raise ValueError("naive datetime field cannot accept an aware value")
        return normalized
    if not aware:
        raise ValueError("UTC datetime field requires an aware value")
    try:
        return normalized.astimezone(dt.timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError(
            "UTC datetime conversion is outside the supported range"
        ) from exc


def normalize_scalar(
    value: t.Any,
    data_type: DataType | str,
    *,
    timezone: str | None = None,
    context: bool = False,
    allow_null: bool = False,
    allow_reserved: bool = False,
) -> t.Any:
    """Validate and canonicalize one scalar in its declared concrete universe."""
    normalized_type = _data_type(data_type)
    _validate_timezone(normalized_type, timezone)
    if value is None:
        if allow_null:
            return None
        raise ValueError(f"{normalized_type.value} scalar cannot be null")

    if normalized_type is DataType.STR:
        normalized = _unicode_scalar_text(value)
    elif normalized_type is DataType.BOOL:
        if value_kind(value) is not ValueKind.BOOLEAN:
            raise ValueError("bool scalar requires an actual Boolean")
        normalized = bool(value)
    elif normalized_type is DataType.INT:
        normalized = _normalize_int(value)
    elif normalized_type is DataType.FLOAT:
        normalized = _normalize_float(value, context)
    elif normalized_type is DataType.DATE:
        if not isinstance(value, dt.date) or isinstance(value, dt.datetime):
            raise ValueError("date scalar requires a date without a time component")
        normalized = value
    else:
        normalized = _normalize_datetime(value, t.cast(str, timezone))

    _check_reserved(normalized, normalized_type, allow_reserved)
    return normalized


def _float_bits(value: float) -> int:
    return int.from_bytes(struct.pack(">d", value), "big")


def _float_from_bits(bits: int) -> float:
    return struct.unpack(">d", bits.to_bytes(8, "big"))[0]


def _encode_concrete(
    value: t.Any, data_type: DataType, timezone: str | None
) -> dict[str, t.Any]:
    if data_type is DataType.STR or data_type is DataType.BOOL:
        return {"type": data_type.value, "value": value}
    if data_type is DataType.INT:
        return {"type": "int", "value": str(value)}
    if data_type is DataType.FLOAT:
        return {"type": "float", "value": struct.pack(">d", value).hex()}
    if data_type is DataType.DATE:
        return {"type": "date", "value": value.isoformat()}
    suffix = "" if timezone == "naive" else "Z"
    encoded = (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
        f"T{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}{suffix}"
    )
    return {"type": "datetime", "value": encoded, "timezone": timezone}


def encode_scalar(
    value: t.Any,
    data_type: DataType | str,
    *,
    timezone: str | None = None,
    context: bool = False,
    allow_null: bool = False,
    allow_reserved: bool = False,
) -> dict[str, t.Any]:
    """Return the exact typed transport object for one validated scalar."""
    normalized_type = _data_type(data_type)
    _validate_timezone(normalized_type, timezone)
    normalized = normalize_scalar(
        value,
        normalized_type,
        timezone=timezone,
        context=context,
        allow_null=allow_null,
        allow_reserved=allow_reserved,
    )
    if normalized is None:
        payload: dict[str, t.Any] = {"type": normalized_type.value, "value": None}
        if normalized_type is DataType.DATETIME:
            payload["timezone"] = timezone
        return payload
    return _encode_concrete(normalized, normalized_type, timezone)


def _decode_datetime(value: str, timezone: str) -> dt.datetime:
    valid = _NAIVE_DATETIME_PAYLOAD if timezone == "naive" else _UTC_DATETIME_PAYLOAD
    if valid.fullmatch(value) is None:
        raise ValueError("datetime payload is not canonical")
    try:
        parsed = dt.datetime.strptime(value.rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError as exc:
        raise ValueError("datetime payload is invalid") from exc
    return parsed if timezone == "naive" else parsed.replace(tzinfo=dt.timezone.utc)


def decode_scalar(
    payload: t.Any,
    *,
    allow_null: bool = False,
    allow_reserved: bool = False,
) -> t.Any:
    """Decode exactly one canonical typed transport object."""
    if type(payload) is not dict or type(payload.get("type")) is not str:
        raise ValueError("scalar payload must be an object with a string type")
    data_type = _data_type(payload["type"])
    expected_keys = (
        {"type", "value", "timezone"}
        if data_type is DataType.DATETIME
        else {"type", "value"}
    )
    if set(payload) != expected_keys:
        raise ValueError("scalar payload has missing or unknown fields")
    timezone = payload.get("timezone")
    _validate_timezone(data_type, timezone)
    value = payload["value"]
    if value is None:
        if not allow_null:
            raise ValueError("null scalar payload is not permitted")
        return None

    if data_type is DataType.STR:
        if type(value) is not str:
            raise ValueError("str payload value must be text")
        decoded: t.Any = value
    elif data_type is DataType.BOOL:
        if type(value) is not bool:
            raise ValueError("bool payload value must be Boolean")
        decoded = value
    elif data_type is DataType.INT:
        if type(value) is not str or _INT_PAYLOAD.fullmatch(value) is None:
            raise ValueError("int payload value is not canonical")
        decoded = int(value)
    elif data_type is DataType.FLOAT:
        if type(value) is not str or _HEX_BITS.fullmatch(value) is None:
            raise ValueError("float payload value is not canonical binary64 bits")
        decoded = _float_from_bits(int(value, 16))
    elif data_type is DataType.DATE:
        if type(value) is not str or _DATE_PAYLOAD.fullmatch(value) is None:
            raise ValueError("date payload value is not canonical")
        try:
            decoded = dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("date payload value is invalid") from exc
    else:
        if type(value) is not str:
            raise ValueError("datetime payload value must be text")
        decoded = _decode_datetime(value, t.cast(str, timezone))

    normalized = normalize_scalar(
        decoded,
        data_type,
        timezone=timezone,
        allow_reserved=allow_reserved,
    )
    if _encode_concrete(normalized, data_type, timezone) != payload:
        raise ValueError("scalar payload is not canonical")
    return normalized


def _date_raw_rank(value: dt.date) -> int:
    return value.toordinal() - 1


def _datetime_raw_rank(value: dt.datetime) -> int:
    return (
        (value.toordinal() - 1) * _MICROSECONDS_PER_DAY
        + ((value.hour * 3600 + value.minute * 60 + value.second) * 1_000_000)
        + value.microsecond
    )


def _float_raw_rank(value: float) -> int:
    bits = _float_bits(value)
    transformed = (~bits & _UINT64_MASK) if bits & _FLOAT_SIGN else bits | _FLOAT_SIGN
    wire_rank = transformed - _FLOAT_MIN_TRANSFORMED
    return wire_rank if wire_rank < _FLOAT_FINITE_MAGNITUDE else wire_rank - 1


_INT_RESERVED_RAW = tuple(
    sorted(
        _date for _date in (UNKNOWN_NUMERIC - _INT64_MIN, NOT_SET_NUMERIC - _INT64_MIN)
    )
)
_FLOAT_RESERVED_RAW = tuple(
    sorted(
        _float_raw_rank(float(value)) for value in (UNKNOWN_NUMERIC, NOT_SET_NUMERIC)
    )
)
_DATE_RESERVED_RAW = tuple(
    sorted(_date_raw_rank(value) for value in (UNKNOWN_DATE, NOT_SET_DATE))
)
_DATETIME_RESERVED_RAW = tuple(
    sorted(_datetime_raw_rank(value) for value in (UNKNOWN_DATETIME, NOT_SET_DATETIME))
)


def _raw_holes(data_type: DataType) -> tuple[int, ...]:
    if data_type is DataType.INT:
        return _INT_RESERVED_RAW
    if data_type is DataType.FLOAT:
        return _FLOAT_RESERVED_RAW
    if data_type is DataType.DATE:
        return _DATE_RESERVED_RAW
    if data_type is DataType.DATETIME:
        return _DATETIME_RESERVED_RAW
    return ()


def _rank_from_raw(raw_rank: int, holes: tuple[int, ...]) -> int:
    return raw_rank - sum(hole < raw_rank for hole in holes)


def _raw_from_rank(value_rank: int, holes: tuple[int, ...]) -> int:
    raw_rank = value_rank
    for hole in holes:
        if raw_rank >= hole:
            raw_rank += 1
    return raw_rank


def _raw_rank(value: t.Any, data_type: DataType) -> int:
    if data_type is DataType.INT:
        return value - _INT64_MIN
    if data_type is DataType.FLOAT:
        return _float_raw_rank(value)
    if data_type is DataType.DATE:
        return _date_raw_rank(value)
    if data_type is DataType.DATETIME:
        return _datetime_raw_rank(value)
    if data_type is DataType.BOOL:
        return int(value)
    raise ValueError("str has no finite discrete rank universe")


def rank(
    value: t.Any,
    data_type: DataType | str,
    *,
    timezone: str | None = None,
) -> int:
    """Return the contiguous rank of an admissible ordered scalar."""
    normalized_type = _data_type(data_type)
    normalized = normalize_scalar(value, normalized_type, timezone=timezone)
    return _rank_from_raw(
        _raw_rank(normalized, normalized_type), _raw_holes(normalized_type)
    )


def rank_bounds(
    data_type: DataType | str,
    *,
    timezone: str | None = None,
) -> tuple[int, int]:
    """Return inclusive rank limits for the declared finite ordered universe."""
    normalized_type = _data_type(data_type)
    _validate_timezone(normalized_type, timezone)
    if normalized_type is DataType.BOOL:
        return (0, 1)
    if normalized_type is DataType.INT:
        return (0, 2**64 - 3)
    if normalized_type is DataType.FLOAT:
        return (0, 2 * _FLOAT_FINITE_MAGNITUDE - 4)
    if normalized_type is DataType.DATE:
        return (0, dt.date.max.toordinal() - 3)
    if normalized_type is DataType.DATETIME:
        return (0, dt.date.max.toordinal() * _MICROSECONDS_PER_DAY - 3)
    raise ValueError("str has no finite discrete rank universe")


def _unrank_raw(raw_rank: int, data_type: DataType, timezone: str | None) -> t.Any:
    if data_type is DataType.INT:
        return raw_rank + _INT64_MIN
    if data_type is DataType.FLOAT:
        wire_rank = raw_rank if raw_rank < _FLOAT_FINITE_MAGNITUDE - 1 else raw_rank + 1
        transformed = _FLOAT_MIN_TRANSFORMED + wire_rank
        bits = (
            transformed & ~_FLOAT_SIGN
            if transformed & _FLOAT_SIGN
            else ~transformed & _UINT64_MASK
        )
        return _float_from_bits(bits)
    if data_type is DataType.DATE:
        return dt.date.fromordinal(raw_rank + 1)
    if data_type is DataType.DATETIME:
        day_rank, microseconds = divmod(raw_rank, _MICROSECONDS_PER_DAY)
        value = dt.datetime.combine(
            dt.date.fromordinal(day_rank + 1), dt.time()
        ) + dt.timedelta(microseconds=microseconds)
        return value if timezone == "naive" else value.replace(tzinfo=dt.timezone.utc)
    if data_type is DataType.BOOL:
        return bool(raw_rank)
    raise ValueError("str has no finite discrete rank universe")


def unrank(
    value_rank: int,
    data_type: DataType | str,
    *,
    timezone: str | None = None,
) -> t.Any:
    """Return the scalar at one contiguous admissible rank."""
    normalized_type = _data_type(data_type)
    if type(value_rank) is not int:
        raise ValueError("rank must be an integer")
    lower, upper = rank_bounds(normalized_type, timezone=timezone)
    if not lower <= value_rank <= upper:
        raise ValueError("rank is outside the scalar universe")
    return _unrank_raw(
        _raw_from_rank(value_rank, _raw_holes(normalized_type)),
        normalized_type,
        timezone,
    )


def successor(
    value: t.Any,
    data_type: DataType | str,
    *,
    timezone: str | None = None,
) -> t.Any:
    """Return the next admissible scalar, skipping intrinsic reserved holes."""
    current = rank(value, data_type, timezone=timezone)
    _, upper = rank_bounds(data_type, timezone=timezone)
    if current == upper:
        raise ValueError("scalar has no successor")
    return unrank(current + 1, data_type, timezone=timezone)


def predecessor(
    value: t.Any,
    data_type: DataType | str,
    *,
    timezone: str | None = None,
) -> t.Any:
    """Return the previous admissible scalar, skipping intrinsic reserved holes."""
    current = rank(value, data_type, timezone=timezone)
    lower, _ = rank_bounds(data_type, timezone=timezone)
    if current == lower:
        raise ValueError("scalar has no predecessor")
    return unrank(current - 1, data_type, timezone=timezone)
