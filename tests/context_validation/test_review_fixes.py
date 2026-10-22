"""Review fixes: decimal representability against the supplied schema, and uncapped diagnostics."""

from __future__ import annotations

from decimal import Decimal

import polars as pl
import pytest

from mountainash_rules.context_validation import check_context_schema, validate_contexts
from mountainash_rules.core.dimension import ContextField
from tests.context_validation.support import context_relation, metadata


def schema_relation(backend_name: str, amount: pl.DataType):
    return context_relation(backend_name, pl.DataFrame(schema={"request_id": pl.Int64, "amount": amount}))


def amount_field(precision=10, scale=2, **kwargs):
    return ContextField(
        name="amount", data_type="decimal", precision=precision, scale=scale, rounding="half_to_even", **kwargs
    )


# -- decimal representability: the declared field AND the supplied schema ---------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"wildcard": Decimal("1000.00")}, "wildcard marker is not representable"),
        ({"truthy": {Decimal("1000.00"): 1}}, "truthy value is not representable"),
    ],
    ids=["wildcard", "truthy-key"],
)
def test_a_decimal_marker_beyond_the_schema_precision_is_rejected(backend_name, kwargs, message):
    relation = schema_relation(backend_name, pl.Decimal(3, 2))  # holds at most 9.99
    with pytest.raises(ValueError, match=message):
        check_context_schema(relation, metadata(amount_field(**kwargs)))


@pytest.mark.parametrize("kwargs", [{"wildcard": Decimal("9.99")}, {"truthy": {Decimal("9.99"): 1}}], ids=["wildcard", "truthy-key"])
def test_a_decimal_marker_the_schema_can_carry_passes(backend_name, kwargs):
    check_context_schema(schema_relation(backend_name, pl.Decimal(3, 2)), metadata(amount_field(**kwargs)))


def test_a_coarser_schema_scale_does_not_widen_the_declared_integer_digits(backend_name):
    # declared DECIMAL(10, 4) holds below 10**6; the schema's DECIMAL(10, 2) would admit 10**8.
    relation = schema_relation(backend_name, pl.Decimal(10, 2))
    field = amount_field(10, 4, wildcard=Decimal("1000000.00"))
    with pytest.raises(ValueError, match="wildcard marker is not representable"):
        check_context_schema(relation, metadata(field))


# -- diagnostics name every offending request id and position --------------------------------


def test_every_duplicated_id_is_listed(backend_name):
    ids = list(range(30))
    contexts = context_relation(backend_name, pl.DataFrame({"request_id": ids + ids}))
    with pytest.raises(ValueError) as caught:
        validate_contexts(contexts, metadata())
    message = str(caught.value)
    assert "more" not in message
    assert f"duplicated: {', '.join(repr(i) for i in sorted(ids, key=repr))}" in message


def test_every_non_finite_value_names_its_request_id(backend_name):
    score = ContextField(name="score", data_type="float")
    ids = list(range(30))
    contexts = context_relation(backend_name, pl.DataFrame({"request_id": ids, "score": [float("inf")] * 30}))
    with pytest.raises(ValueError) as caught:
        validate_contexts(contexts, metadata(score))
    line = next(row for row in str(caught.value).splitlines() if "not finite" in row)
    assert "more" not in line
    assert sorted(int(i) for i in line.rsplit("request_id ", 1)[1].split(", ")) == ids


def test_every_position_without_a_request_id_is_listed():
    class Bare:
        model_fields_set: set = set()

    objects = [Bare() for _ in range(30)]
    with pytest.raises(ValueError) as caught:
        validate_contexts(objects, metadata())
    assert str(list(range(30))) in str(caught.value)
