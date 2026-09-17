"""Scalar-1 exact concrete-universe and canonical transport tests."""

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

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
from mountainash_rules.core.scalar import (
    decode_scalar,
    encode_scalar,
    normalize_scalar,
    predecessor,
    rank,
    rank_bounds,
    successor,
    unrank,
)


@pytest.mark.parametrize(
    ("data_type", "value", "timezone", "payload"),
    [
        (DataType.STR, "Straße", None, {"type": "str", "value": "Straße"}),
        (DataType.BOOL, True, None, {"type": "bool", "value": True}),
        (DataType.INT, -42, None, {"type": "int", "value": "-42"}),
        (DataType.FLOAT, -1.5, None, {"type": "float", "value": "bff8000000000000"}),
        (
            DataType.DATE,
            dt.date(2024, 2, 29),
            None,
            {"type": "date", "value": "2024-02-29"},
        ),
        (
            DataType.DATETIME,
            dt.datetime(2024, 2, 29, 12, 30, 1, 2),
            "naive",
            {
                "type": "datetime",
                "value": "2024-02-29T12:30:01.000002",
                "timezone": "naive",
            },
        ),
        (
            DataType.DATETIME,
            dt.datetime(2024, 2, 29, 12, 30, 1, 2, tzinfo=dt.timezone.utc),
            "utc",
            {
                "type": "datetime",
                "value": "2024-02-29T12:30:01.000002Z",
                "timezone": "utc",
            },
        ),
    ],
)
def test_scalar_types_normalize_and_round_trip_canonical_transport(
    data_type, value, timezone, payload
):
    normalized = normalize_scalar(value, data_type, timezone=timezone)

    assert encode_scalar(normalized, data_type, timezone=timezone) == payload
    assert decode_scalar(payload) == normalized


def test_integer_enforces_signed_int64_boundaries_and_skips_reserved_ranks():
    lo, hi = -(2**63), 2**63 - 1

    assert normalize_scalar(lo, DataType.INT) == lo
    assert normalize_scalar(hi, DataType.INT) == hi
    with pytest.raises(ValueError):
        normalize_scalar(lo - 1, DataType.INT)
    with pytest.raises(ValueError):
        normalize_scalar(hi + 1, DataType.INT)

    assert rank_bounds(DataType.INT) == (0, 2**64 - 3)
    assert rank(lo, DataType.INT) == 0
    assert unrank(2**64 - 3, DataType.INT) == hi
    assert successor(UNKNOWN_NUMERIC - 1, DataType.INT) == NOT_SET_NUMERIC + 1
    assert predecessor(NOT_SET_NUMERIC + 1, DataType.INT) == UNKNOWN_NUMERIC - 1


@pytest.mark.parametrize(
    "reserved", [UNKNOWN_NUMERIC, NOT_SET_NUMERIC, UNKNOWN, NOT_SET]
)
def test_reserved_markers_require_explicit_admission(reserved):
    data_type = DataType.INT if type(reserved) is int else DataType.STR

    with pytest.raises(ValueError):
        normalize_scalar(reserved, data_type)
    assert normalize_scalar(reserved, data_type, allow_reserved=True) == reserved


def test_float_is_finite_binary64_with_canonical_zero_and_exact_context_int_cast():
    assert normalize_scalar(-0.0, DataType.FLOAT) == 0.0
    assert encode_scalar(-0.0, DataType.FLOAT) == {
        "type": "float",
        "value": "0000000000000000",
    }
    assert normalize_scalar(2**53, DataType.FLOAT, context=True) == float(2**53)

    for value in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError):
            normalize_scalar(value, DataType.FLOAT)
    with pytest.raises(ValueError):
        normalize_scalar(1, DataType.FLOAT)
    with pytest.raises(ValueError):
        normalize_scalar(2**53 + 1, DataType.FLOAT, context=True)


def test_float_ranks_cover_binary64_adjacency_and_exclude_reserved_holes():
    maximum = float.fromhex("0x1.fffffffffffffp+1023")
    one = 1.0
    following = math.nextafter(one, math.inf)

    assert rank_bounds(DataType.FLOAT)[0] == rank(-maximum, DataType.FLOAT) == 0
    assert rank_bounds(DataType.FLOAT)[1] == rank(maximum, DataType.FLOAT)
    assert rank(following, DataType.FLOAT) == rank(one, DataType.FLOAT) + 1
    assert successor(one, DataType.FLOAT) == following
    assert predecessor(following, DataType.FLOAT) == one

    for reserved in (float(UNKNOWN_NUMERIC), float(NOT_SET_NUMERIC)):
        before = math.nextafter(reserved, -math.inf)
        after = math.nextafter(reserved, math.inf)
        assert successor(before, DataType.FLOAT) == after
        assert predecessor(after, DataType.FLOAT) == before
    with pytest.raises(ValueError):
        rank(float(UNKNOWN_NUMERIC), DataType.FLOAT)


def test_boolean_rejects_numeric_and_text_synonyms_and_has_closed_ranks():
    assert normalize_scalar(False, DataType.BOOL) is False
    assert normalize_scalar(True, DataType.BOOL) is True
    assert rank_bounds(DataType.BOOL) == (0, 1)
    assert rank(False, DataType.BOOL) == 0
    assert unrank(1, DataType.BOOL) is True

    for value in (0, 1, 0.0, 1.0, "true", None):
        with pytest.raises(ValueError):
            normalize_scalar(value, DataType.BOOL)


def test_strings_reject_surrogates_and_reserved_business_values():
    assert normalize_scalar("", DataType.STR) == ""
    with pytest.raises(ValueError):
        normalize_scalar("bad\ud800text", DataType.STR)
    with pytest.raises(ValueError):
        normalize_scalar(UNKNOWN, DataType.STR)


def test_dates_use_proleptic_calendar_ranks_with_reserved_holes():
    earliest_admissible = dt.date(1, 1, 3)

    assert (
        rank_bounds(DataType.DATE)[0] == rank(earliest_admissible, DataType.DATE) == 0
    )
    assert successor(earliest_admissible, DataType.DATE) == dt.date(1, 1, 4)
    with pytest.raises(ValueError):
        predecessor(earliest_admissible, DataType.DATE)
    with pytest.raises(ValueError):
        normalize_scalar(UNKNOWN_DATE, DataType.DATE)
    with pytest.raises(ValueError):
        normalize_scalar(NOT_SET_DATE, DataType.DATE)


def test_datetimes_require_declared_naive_or_utc_conventions_and_preserve_microseconds():
    naive = dt.datetime(2024, 1, 2, 3, 4, 5, 6)
    aware_utc = naive.replace(tzinfo=dt.timezone.utc)
    offset = naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=2)))

    assert normalize_scalar(naive, DataType.DATETIME, timezone="naive") == naive
    assert normalize_scalar(aware_utc, DataType.DATETIME, timezone="utc") == aware_utc
    assert normalize_scalar(
        offset, DataType.DATETIME, timezone="utc"
    ) == aware_utc - dt.timedelta(hours=2)

    for value, timezone in (
        (naive, "utc"),
        (aware_utc, "naive"),
        (naive, None),
        (naive, "Europe/London"),
    ):
        with pytest.raises(ValueError):
            normalize_scalar(value, DataType.DATETIME, timezone=timezone)


def test_temporal_carriers_reject_nanosecond_loss_before_python_datetime_conversion():
    microsecond = dt.datetime(2024, 1, 2, 3, 4, 5, 6)
    numpy_microsecond = np.datetime64("2024-01-02T03:04:05.000006", "us")
    pandas_microsecond = pd.Timestamp("2024-01-02T03:04:05.000006")

    assert (
        normalize_scalar(numpy_microsecond, DataType.DATETIME, timezone="naive")
        == microsecond
    )
    assert (
        normalize_scalar(pandas_microsecond, DataType.DATETIME, timezone="naive")
        == microsecond
    )

    for value in (
        np.datetime64("2024-01-02T03:04:05.000006001", "ns"),
        pd.Timestamp("2024-01-02T03:04:05.000006001"),
    ):
        with pytest.raises(ValueError):
            normalize_scalar(value, DataType.DATETIME, timezone="naive")


def test_datetime_ranks_are_microsecond_discrete_and_skip_reserved_midnights():
    earliest_admissible = dt.datetime(1, 1, 1, 0, 0, 0, 1)
    next_value = dt.datetime(1, 1, 1, 0, 0, 0, 2)

    assert rank_bounds(DataType.DATETIME, timezone="naive")[0] == 0
    assert rank(earliest_admissible, DataType.DATETIME, timezone="naive") == 0
    assert (
        successor(earliest_admissible, DataType.DATETIME, timezone="naive")
        == next_value
    )
    with pytest.raises(ValueError):
        predecessor(earliest_admissible, DataType.DATETIME, timezone="naive")
    for value in (UNKNOWN_DATETIME, NOT_SET_DATETIME):
        with pytest.raises(ValueError):
            normalize_scalar(value, DataType.DATETIME, timezone="naive")
        with pytest.raises(ValueError):
            normalize_scalar(
                value.replace(tzinfo=dt.timezone.utc),
                DataType.DATETIME,
                timezone="utc",
            )


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "int", "value": 1},
        {"type": "int", "value": "+1"},
        {"type": "int", "value": "01"},
        {"type": "float", "value": "3FF0000000000000"},
        {"type": "float", "value": "8000000000000000"},
        {"type": "date", "value": "2024-2-29"},
        {"type": "datetime", "value": "2024-01-02T03:04:05.000006"},
        {"type": "bool", "value": 1},
        {"type": "str", "value": "ok", "extra": None},
    ],
)
def test_decode_rejects_noncanonical_or_nonexact_typed_wire(payload):
    with pytest.raises(ValueError):
        decode_scalar(payload)


def test_typed_null_requires_explicit_permission_and_keeps_datetime_timezone():
    payload = {"type": "datetime", "value": None, "timezone": "utc"}

    with pytest.raises(ValueError):
        encode_scalar(None, DataType.DATETIME, timezone="utc")
    assert (
        encode_scalar(None, DataType.DATETIME, timezone="utc", allow_null=True)
        == payload
    )
    with pytest.raises(ValueError):
        decode_scalar(payload)
    assert decode_scalar(payload, allow_null=True) is None
