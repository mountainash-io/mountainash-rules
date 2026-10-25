"""Type-owned value contracts, separate from public lifecycle/region tests."""

import datetime as dt
from decimal import Decimal

import mountainash.expressions as ma
import pytest
from mountainash import DecimalDtype, MountainashDtype
from mountainash.core.dtypes import TimestampDtype
from mountainash.relations import relation

from mountainash_rules import Aggregate, BooleanCoercion, ContextField, DataType
from mountainash_rules.core.type_families import python_data_type, type_family
from tests.conftest import build_backend_df


@pytest.mark.parametrize(
    "data_type,value,text",
    [
        (DataType.STR, "*", "*"),
        (DataType.INT, -12, "-12"),
        (DataType.FLOAT, 0.25, "0.25"),
        (DataType.DECIMAL, Decimal("1.2300"), "1.2300"),
        (DataType.BOOL, False, "false"),
        (DataType.DATE, dt.date(2026, 10, 25), "2026-10-25"),
        (
            DataType.DATETIME,
            dt.datetime(2026, 10, 25, 12, 0, 0, 123456, dt.timezone.utc),
            "2026-10-25T12:00:00.123456+00:00",
        ),
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
    fine = ContextField(
        name="fine",
        data_type=DataType.DECIMAL,
        precision=7,
        scale=3,
        rounding="half_to_even",
    )
    coarse = ContextField(
        name="coarse",
        data_type=DataType.DECIMAL,
        precision=4,
        scale=1,
        rounding="half_away_from_zero",
    )
    assert family.decimal_dtype(fine) == DecimalDtype(7, 3)
    assert family.successor(fine, Decimal("2.718")) == Decimal("2.719")
    assert family.decimal_dtype(coarse) == DecimalDtype(4, 1)
    assert family.successor(coarse, Decimal("2.7")) == Decimal("2.8")
    assert family.successor(fine, Decimal("2.718")) == Decimal("2.719")


def test_datetime_descriptors_and_steps_follow_each_declaration():
    family = type_family(DataType.DATETIME)
    aware = ContextField(
        name="aware", data_type=DataType.DATETIME, datetime_unit="ms", timezone="utc"
    )
    naive = ContextField(
        name="naive", data_type=DataType.DATETIME, datetime_unit="s", timezone="naive"
    )
    instant = dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.timezone.utc)
    assert family.value_dtype(aware) == TimestampDtype(
        precision=6, timezone_aware=True, timezone="UTC"
    )
    assert family.successor(aware, instant) == instant + dt.timedelta(milliseconds=1)
    assert family.value_dtype(naive) == TimestampDtype(
        precision=6, timezone_aware=False, timezone=None
    )
    assert family.successor(naive, instant.replace(tzinfo=None)) == dt.datetime(
        2026, 10, 25, 12, 0, 1
    )
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
    output = Aggregate(
        output_name="event",
        contribution_column="at",
        data_type=DataType.DATETIME,
        timezone="utc",
    )
    family.validate_output(output)
    with pytest.raises(ValueError):
        family.validate_output(output.model_copy(update={"timezone": None}))
    with pytest.raises(ValueError):
        family.validate_output(output.model_copy(update={"timezone": "local"}))


@pytest.mark.parametrize(
    "data_type,value",
    [
        (DataType.INT, True),
        (DataType.DATE, dt.datetime(2026, 10, 25)),
        (DataType.FLOAT, 1.0),
    ],
)
def test_successor_does_not_broaden_value_admission(data_type, value):
    field = ContextField(name="x", data_type=data_type)
    with pytest.raises(ValueError):
        type_family(data_type).successor(field, value)


def _source(backend, values, dtype, inactive=None):
    return relation(
        build_backend_df(
            backend,
            {
                "raw": values,
                "inactive": [value is None for value in values]
                if inactive is None
                else inactive,
            },
        )
    ).with_columns(ma.col("raw").cast(dtype))


@pytest.mark.parametrize("role", ["rule", "context"])
@pytest.mark.parametrize(
    "rounding,rounded", [("half_to_even", "1.22"), ("half_away_from_zero", "1.23")]
)
def test_decimal_binding_contains_placeholder_without_losing_real_zero(
    backend_name, role, rounding, rounded
):
    field = ContextField(
        name="amount",
        data_type=DataType.DECIMAL,
        precision=5,
        scale=2,
        rounding=rounding,
    )
    family = type_family(field.data_type)
    source = _source(backend_name, [None, "bad", "0", "1.225"], MountainashDtype.STRING)
    if role == "rule":
        value, invalid = family.bind_rule(
            field, ma.col("raw"), source.schema["raw"], ma.col("inactive")
        )
    else:
        value, invalid = family.bind_context(
            field,
            ma.col("raw"),
            source.schema["raw"],
            ~ma.col("inactive"),
            BooleanCoercion.NONE,
            source_name="actual_amount",
        )
    assert source.select(value.alias("value"), invalid.alias("invalid")).to_dicts() == [
        {"value": None, "invalid": False},
        {"value": None, "invalid": True},
        {"value": Decimal("0.00"), "invalid": False},
        {"value": Decimal(rounded), "invalid": False},
    ]


@pytest.mark.parametrize(
    "kind,source_dtype,raw,rule_value,context_value,rule_invalid",
    [
        (DataType.STR, MountainashDtype.STRING, "alpha", "alpha", "alpha", False),
        (DataType.STR, MountainashDtype.I64, -7, None, "-7", True),
        (
            DataType.DATE,
            MountainashDtype.DATE,
            dt.date(2026, 10, 25),
            dt.date(2026, 10, 25),
            dt.date(2026, 10, 25),
            False,
        ),
        (
            DataType.DATE,
            MountainashDtype.STRING,
            "2026-10-25",
            None,
            dt.date(2026, 10, 25),
            True,
        ),
        (DataType.INT, MountainashDtype.I64, -7, -7, -7, False),
        (DataType.INT, MountainashDtype.STRING, "-7", None, -7, True),
        (DataType.FLOAT, MountainashDtype.I64, -7, -7.0, -7.0, False),
        (DataType.FLOAT, MountainashDtype.FP64, 0.25, 0.25, 0.25, False),
    ],
)
def test_rule_source_admission_does_not_restrict_context_casting(
    backend_name, kind, source_dtype, raw, rule_value, context_value, rule_invalid
):
    field = ContextField(name="logical", data_type=kind)
    family = type_family(kind)
    source = _source(backend_name, [raw, None], source_dtype)
    rule, bad_rule = family.bind_rule(
        field, ma.col("raw"), source.schema["raw"], ma.col("inactive")
    )
    context, bad_context = family.bind_context(
        field,
        ma.col("raw"),
        source.schema["raw"],
        ~ma.col("inactive"),
        BooleanCoercion.NONE,
        source_name="actual_source",
    )
    assert source.select(
        rule.alias("rule"),
        bad_rule.alias("bad_rule"),
        context.alias("context"),
        bad_context.alias("bad_context"),
    ).to_dicts() == [
        {
            "rule": rule_value,
            "bad_rule": rule_invalid,
            "context": context_value,
            "bad_context": False,
        },
        {"rule": None, "bad_rule": False, "context": None, "bad_context": False},
    ]


def test_integer_rule_binding_masks_unsigned_overflow_before_signed_cast(backend_name):
    field = ContextField(name="x", data_type=DataType.INT)
    source = _source(backend_name, [str(2**63 - 1), str(2**63)], MountainashDtype.U64)
    value, invalid = type_family(DataType.INT).bind_rule(
        field, ma.col("raw"), source.schema["raw"], ma.col("inactive")
    )
    assert source.select(value.alias("value"), invalid.alias("invalid")).to_dicts() == [
        {"value": 2**63 - 1, "invalid": False},
        {"value": None, "invalid": True},
    ]


def test_nonfinite_rule_check_is_not_context_admission(backend_name):
    field = ContextField(name="x", data_type=DataType.FLOAT)
    family = type_family(DataType.FLOAT)
    # Create native NaN: Ibis's Python-table loader otherwise turns it into SQL null.
    source = _source(backend_name, ["inf", "NaN"], MountainashDtype.FP64)
    rule, bad_rule = family.bind_rule(
        field, ma.col("raw"), source.schema["raw"], ma.lit(False)
    )
    context, bad_context = family.bind_context(
        field,
        ma.col("raw"),
        source.schema["raw"],
        ma.lit(True),
        BooleanCoercion.NONE,
        source_name="raw",
    )
    assert source.select(
        rule.alias("rule"),
        bad_rule.alias("bad_rule"),
        context.is_nan().alias("nan"),
        (~context.is_finite() & ~context.is_nan()).alias("inf"),
        bad_context.alias("bad_context"),
    ).to_dicts() == [
        {
            "rule": None,
            "bad_rule": True,
            "nan": False,
            "inf": True,
            "bad_context": False,
        },
        {
            "rule": None,
            "bad_rule": True,
            "nan": True,
            "inf": False,
            "bad_context": False,
        },
    ]


def test_boolean_context_coercion_never_admits_text_rules(backend_name):
    field = ContextField(name="logical", data_type=DataType.BOOL)
    family = type_family(DataType.BOOL)
    source = _source(backend_name, [" TrUe ", "no", None], MountainashDtype.STRING)
    rule, bad_rule = family.bind_rule(
        field, ma.col("raw"), source.schema["raw"], ma.col("inactive")
    )
    context, bad_context = family.bind_context(
        field,
        ma.col("raw"),
        source.schema["raw"],
        ~ma.col("inactive"),
        BooleanCoercion.BOOLEAN_TEXT,
        source_name="source_column",
    )
    assert source.select(
        rule.alias("rule"),
        bad_rule.alias("bad_rule"),
        context.alias("context"),
        bad_context.alias("bad_context"),
    ).to_dicts() == [
        {"rule": None, "bad_rule": True, "context": True, "bad_context": False},
        {"rule": None, "bad_rule": True, "context": None, "bad_context": True},
        {"rule": None, "bad_rule": False, "context": None, "bad_context": False},
    ]


@pytest.mark.parametrize("timezone", ["naive", "utc"])
def test_datetime_rule_precision_and_context_resolution_remain_distinct(
    backend_name, timezone
):
    field = ContextField(
        name="instant",
        data_type=DataType.DATETIME,
        datetime_unit="ms",
        timezone=timezone,
    )
    family = type_family(DataType.DATETIME)
    instant = dt.datetime(
        2026,
        10,
        25,
        12,
        0,
        0,
        123456,
        tzinfo=dt.timezone.utc if timezone == "utc" else None,
    )
    source = _source(backend_name, [instant, None], family.value_dtype(field))
    rule, bad_rule = family.bind_rule(
        field, ma.col("raw"), source.schema["raw"], ma.col("inactive")
    )
    context, bad_context = family.bind_context(
        field,
        ma.col("raw"),
        source.schema["raw"],
        ~ma.col("inactive"),
        BooleanCoercion.NONE,
        source_name="source_column",
    )
    # Compare the instant natively: Ibis collects truncated UTC values as naive
    # datetimes on the unchanged baseline, while comparisons retain UTC semantics.
    expected = ma.lit(
        instant.replace(microsecond=123000), dtype=family.value_dtype(field)
    )
    assert source.select(
        rule.alias("rule"),
        bad_rule.alias("bad_rule"),
        context.eq(expected).alias("at_resolution"),
        bad_context.alias("bad_context"),
    ).to_dicts() == [
        {
            "rule": instant,
            "bad_rule": False,
            "at_resolution": True,
            "bad_context": False,
        },
        {"rule": None, "bad_rule": False, "at_resolution": None, "bad_context": False},
    ]
    # A wrong-source inactive value still gets the declared timezone, not naive null.
    raw = ma.lit(None).cast(str)
    payload, invalid = family.bind_rule(
        field, raw, MountainashDtype.STRING, ma.lit(True)
    )
    assert source.select(
        payload.alias("value"), invalid.alias("invalid")
    ).to_dicts() == [
        {"value": None, "invalid": False},
        {"value": None, "invalid": False},
    ]
    descriptor = source.select(payload.alias("value")).schema["value"]
    assert descriptor.precision == 6
    # DuckDB exposes awareness but may not name the timezone in source evidence.
    assert descriptor.timezone_aware is (timezone == "utc")


def test_float_engine_lists_convert_integer_values_after_canonicalization(backend_name):
    family = type_family(DataType.FLOAT)
    source = relation(build_backend_df(backend_name, {"raw": [[2, -1, 2], []]}))
    element = source.schema["raw"].element
    assert family.admits_engine_element(element)
    payload = family.engine_list_payload(ma.col("raw"), element, source_name="raw")
    assert source.select(payload.alias("value")).to_dicts() == [
        {"value": [-1.0, 2.0]},
        {"value": []},
    ]


def test_datetime_list_rule_admission_checks_declared_timezone():
    family = type_family(DataType.DATETIME)
    field = ContextField(
        name="x", data_type=DataType.DATETIME, datetime_unit="ms", timezone="utc"
    )
    naive = TimestampDtype(precision=6, timezone_aware=False, timezone=None)
    aware = TimestampDtype(precision=6, timezone_aware=True, timezone="UTC")
    assert family.admits_engine_element(naive)
    assert not family.admits_rule_element(naive, field)
    assert family.admits_rule_element(aware, field)
