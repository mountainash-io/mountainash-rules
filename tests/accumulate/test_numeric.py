"""Native concrete admission: exact source values, lexical grammar and overflow."""

from decimal import Decimal

import numpy as np
import pytest

import mountainash.expressions as ma
from mountainash import DecimalDtype
from mountainash.relations import relation

from tests.conftest import build_backend_df


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
@pytest.mark.parametrize(
    ("rounding", "expected"),
    [
        ("half_to_even", [Decimal("0.004"), Decimal("-0.004"), Decimal("0.006")]),
        (
            "half_away_from_zero",
            [Decimal("0.005"), Decimal("-0.005"), Decimal("0.006")],
        ),
    ],
)
def test_original_decimal_literals_round_once(backend_name, rounding, expected):
    from mountainash_rules.core.numeric import (
        decimal_operand,
        exact_literal,
        literal_dtype,
    )

    rel = relation(build_backend_df(backend_name, {"id": [1]}))
    originals = [Decimal("0.0045"), Decimal("-0.0045"), Decimal("0.0055")]
    operands = [
        decimal_operand(
            exact_literal(value), literal_dtype(value), DecimalDtype(10, 3), rounding
        )
        for value in originals
    ]
    actual = rel.select(
        *[operand.value.alias(f"v{i}") for i, operand in enumerate(operands)]
    ).to_dicts()
    assert actual == [{f"v{i}": value for i, value in enumerate(expected)}]


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_decimal_lexical_admission_and_signed_overflow(backend_name):
    from mountainash_rules.core.numeric import decimal_operand, reject_invalid

    tokens = [
        "+.5",
        "1.",
        "000999.9949",
        "-999.9949",
        "999.995",
        "-999.995",
        "1000",
        "0.00000000007",
        "7e-11",
        " 1",
        "1 ",
        "1,000",
        "NaN",
        "∞",
        "１",
        ".",
        "",
        None,
        "9" * 60,
        "0" * 60 + ".5",
        "1\n",
    ]
    rel = relation(
        build_backend_df(backend_name, {"id": list(range(len(tokens))), "raw": tokens})
    )
    operand = decimal_operand(
        ma.col("raw"), rel.schema["raw"], DecimalDtype(5, 2), "half_to_even"
    )
    actual = (
        rel.select("id", operand.value.alias("value"), operand.invalid.alias("invalid"))
        .sort("id")
        .to_dicts()
    )
    valid = {
        0: Decimal("0.50"),
        1: Decimal("1.00"),
        2: Decimal("999.99"),
        3: Decimal("-999.99"),
        7: Decimal("0.00"),
        19: Decimal("0.50"),
    }
    assert {row["id"]: row["value"] for row in actual if not row["invalid"]} == valid
    assert [row["id"] for row in actual if row["invalid"]] == [
        4,
        5,
        6,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        20,
    ]
    with pytest.raises(ValueError, match="amount"):
        reject_invalid(rel, {"amount": operand.invalid})


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
@pytest.mark.parametrize("value", [True, 1.0, float("inf"), "broken", None])
def test_inactive_payload_is_not_admitted_or_cast(backend_name, value):
    from mountainash_rules.core.numeric import (
        decimal_operand,
        exact_literal,
        literal_dtype,
    )

    rel = relation(build_backend_df(backend_name, {"inactive": [True, False]}))
    operand = decimal_operand(
        exact_literal(value),
        literal_dtype(value),
        DecimalDtype(5, 2),
        "half_to_even",
        inactive=ma.col("inactive"),
    )
    actual = rel.select(
        "inactive", operand.invalid.alias("invalid"), operand.value.alias("value")
    ).to_dicts()
    assert [(row["inactive"], row["invalid"]) for row in actual] == [
        (True, False),
        (False, True),
    ]


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_native_source_precision_and_integer_extrema(backend_name):
    from mountainash_rules.core.numeric import (
        decimal_operand,
        exact_literal,
        literal_dtype,
    )

    rel = relation(build_backend_df(backend_name, {"id": [1]}))
    values = [
        Decimal("7e-11"),
        Decimal("0.12345678901234567890123456789012345678"),
        -(2**63),
        2**63 - 1,
        np.int64(7),
    ]
    operands = [
        decimal_operand(
            exact_literal(value),
            literal_dtype(value),
            DecimalDtype(16, 3),
            "half_to_even",
        )
        for value in values
    ]
    assert rel.select(
        *[o.invalid.alias(f"bad{i}") for i, o in enumerate(operands)]
    ).to_dicts() == [
        {"bad0": False, "bad1": False, "bad2": True, "bad3": True, "bad4": False}
    ]
    assert rel.select(
        *[operands[i].value.alias(f"v{i}") for i in (0, 1, 4)]
    ).to_dicts() == [
        {"v0": Decimal("0.000"), "v1": Decimal("0.123"), "v4": Decimal("7.000")}
    ]
    with pytest.raises(ValueError, match="38"):
        exact_literal(Decimal("0." + "1" * 39))


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
@pytest.mark.parametrize("rounding", ["half_to_even", "half_away_from_zero"])
def test_all_ordered_decimal_declarations(backend_name, rounding):
    from mountainash_rules.core.numeric import (
        decimal_operand,
        exact_literal,
        literal_dtype,
    )

    rel = relation(build_backend_df(backend_name, {"id": [1]}))
    values, expected = [], {}
    for precision in range(1, 17):
        for scale in range(precision + 1):
            name = f"p{precision}s{scale}"
            operand = decimal_operand(
                exact_literal("0.5"),
                literal_dtype("0.5"),
                DecimalDtype(precision, scale),
                rounding,
            )
            values.append(operand.value.alias(name))
            expected[name] = (
                Decimal("0.5")
                if scale
                else Decimal(0 if rounding == "half_to_even" else 1)
            )
    assert rel.select(*values).to_dicts() == [expected]


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
@pytest.mark.parametrize(
    ("values", "target", "expected", "invalid_ids"),
    [
        (
            [0, 999, -1000, -(2**63)],
            DecimalDtype(5, 2),
            {0: Decimal("0"), 1: Decimal("999")},
            [2, 3],
        ),
        (
            [Decimal("999.9949"), Decimal("999.995"), Decimal("-999.995")],
            DecimalDtype(5, 2),
            {0: Decimal("999.99")},
            [1, 2],
        ),
        (
            [Decimal("0.9949"), Decimal("0.995"), Decimal("-0.995")],
            DecimalDtype(2, 2),
            {0: Decimal("0.99")},
            [1, 2],
        ),
    ],
)
def test_native_columns_reject_only_out_of_range_values(
    backend_name, values, target, expected, invalid_ids
):
    from mountainash_rules.core.numeric import decimal_operand

    rel = relation(
        build_backend_df(backend_name, {"id": list(range(len(values))), "raw": values})
    )
    operand = decimal_operand(ma.col("raw"), rel.schema["raw"], target, "half_to_even")
    actual = (
        rel.select("id", operand.value.alias("value"), operand.invalid.alias("invalid"))
        .sort("id")
        .to_dicts()
    )
    assert {row["id"]: row["value"] for row in actual if not row["invalid"]} == expected
    assert [row["id"] for row in actual if row["invalid"]] == invalid_ids
