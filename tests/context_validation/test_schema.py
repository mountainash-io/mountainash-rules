"""Plan 3 Task 1: schema alignment of a context schema with the declared context fields."""

from __future__ import annotations

import datetime as dt
import typing as t
import warnings
from decimal import Decimal

import polars as pl
import pytest
from mountainash import DecimalDtype, MountainashDtype
from mountainash.relations import Relation
from pydantic import AwareDatetime, Field, NaiveDatetime, create_model

from mountainash_rules.context_validation import check_context_schema
from mountainash_rules.core.dimension import ContextField
from tests.context_validation.support import context_relation, metadata, required_model

REGION = ContextField(name="region", data_type="str", wildcard="*")
AMOUNT = ContextField(name="amount", data_type="decimal", precision=10, scale=2, rounding="half_to_even")
AT = ContextField(name="at", data_type="datetime", datetime_unit="us", timezone="utc")
LEVEL = ContextField(name="level", data_type="int", wildcard=-1, truthy={1: 1, 2: -1})

MATCHING = {
    "request_id": (int, pl.Int64),
    "region": (str, pl.String),
    "amount": (Decimal, pl.Decimal(10, 2)),
    "at": (AwareDatetime, pl.Datetime("us", "UTC")),
    "level": (int, pl.Int64),
}


@pytest.fixture(params=["class", "polars", "ibis-duckdb"])
def source_kind(request) -> str:
    return request.param


def schema_of(kind: str, columns: dict[str, tuple[t.Any, t.Any]]):
    """The same context schema as a pydantic class or as an empty Relation on a backend."""
    if kind == "class":
        fields = {name: annotation for name, (annotation, _) in columns.items() if name != "request_id"}
        return create_model("Context", **({"request_id": (t.Any, ...)} if "request_id" in columns else {}),
                            **{name: (annotation, ...) for name, annotation in fields.items()})
    frame = pl.DataFrame(schema={name: dtype for name, (_, dtype) in columns.items()})
    return context_relation(kind, frame)


def with_columns(**overrides: tuple[t.Any, t.Any]) -> dict[str, tuple[t.Any, t.Any]]:
    return {**MATCHING, **overrides}


def check(kind: str, columns: dict, *fields: ContextField) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a clean schema must not warn
        check_context_schema(schema_of(kind, columns), metadata(*fields))


def test_matching_schema_passes_without_warning(source_kind):
    check(source_kind, MATCHING, REGION, AMOUNT, AT, LEVEL)


def test_all_mismatches_are_reported_together(source_kind):
    columns = {
        "region": (int, pl.Int64),  # type
        "amount": (str, pl.String),  # type
        "at": (NaiveDatetime, pl.Datetime("us")),  # timezone
        "level": (int, pl.Int8),  # marker -1 fits; truthy fits
    }
    with pytest.raises(ValueError) as caught:
        check(source_kind, columns, REGION, AMOUNT, AT, LEVEL)
    message = str(caught.value)
    assert "'request_id' is missing" in message
    assert "field 'region'" in message and "field 'amount'" in message
    assert "field 'at'" in message and "timezone" in message
    assert "field 'level'" not in message


def test_datetime_unit_mismatch_is_an_error():
    columns = with_columns(at=(AwareDatetime, pl.Datetime("ms", "UTC")))
    with pytest.raises(ValueError, match=r"field 'at': schema unit 'ms' does not match declared 'us'"):
        check("polars", columns, AT)


def test_datetime_timezone_mismatch_is_an_error_on_relations():
    columns = with_columns(at=(NaiveDatetime, pl.Datetime("us")))
    with pytest.raises(ValueError, match=r"schema timezone 'naive' does not match declared 'utc'"):
        check("ibis-duckdb", columns, AT)


def test_unaware_datetime_annotation_leaves_timezone_unchecked():
    check("class", with_columns(at=(dt.datetime, pl.Datetime("us", "UTC"))), AT)


def test_wildcard_marker_of_the_wrong_type_is_not_representable(source_kind):
    field = ContextField(name="level", data_type="int", wildcard="*")
    with pytest.raises(ValueError, match=r"field 'level': wildcard marker is not representable"):
        check(source_kind, MATCHING, field)


def test_wildcard_marker_outside_the_schema_integer_range(source_kind):
    columns = with_columns(level=(t.Annotated[int, Field(ge=0, le=100)], pl.Int8))
    field = ContextField(name="level", data_type="int", wildcard=-999)
    with pytest.raises(ValueError, match=r"wildcard marker is not representable"):
        check(source_kind, columns, field)


def test_null_wildcard_needs_a_nullable_class_annotation():
    field = ContextField(name="region", data_type="str", wildcard=None, wildcard_declared=True)
    with pytest.raises(ValueError, match=r"null is not allowed by the schema"):
        check("class", MATCHING, field)
    check("class", with_columns(region=(str | None, pl.String)), field)
    check("polars", MATCHING, field)  # relation columns are nullable


def test_truthy_value_that_the_schema_cannot_carry(source_kind):
    columns = with_columns(level=(t.Annotated[int, Field(ge=0, le=100)], pl.Int8))
    field = ContextField(name="level", data_type="int", truthy={1: 1, 300: -1})
    with pytest.raises(ValueError, match=r"truthy value is not representable"):
        check(source_kind, columns, field)


def test_truthy_value_of_the_wrong_type(source_kind):
    field = ContextField(name="region", data_type="str", truthy={1: 1})
    with pytest.raises(ValueError, match=r"truthy value is not representable: 1 is not a str value"):
        check(source_kind, MATCHING, field)


def test_missing_request_id_is_an_error(source_kind):
    columns = {name: spec for name, spec in MATCHING.items() if name != "request_id"}
    with pytest.raises(ValueError, match=r"'request_id' is missing"):
        check(source_kind, columns, REGION)


def test_a_declared_field_absent_from_the_schema_warns_and_passes(source_kind):
    columns = {name: spec for name, spec in MATCHING.items() if name != "region"}
    with pytest.warns(UserWarning, match=r"declared context field 'region' is absent .* unknown at evaluation"):
        check_context_schema(schema_of(source_kind, columns), metadata(REGION, AMOUNT))


def test_a_finer_decimal_scale_warns_and_passes(source_kind):
    columns = with_columns(amount=(t.Annotated[Decimal, Field(decimal_places=4)], pl.Decimal(10, 4)))
    with pytest.warns(UserWarning, match=r"schema scale 4 is finer than declared scale 2.*half_to_even"):
        check_context_schema(schema_of(source_kind, columns), metadata(AMOUNT))


def test_a_coarser_decimal_scale_passes_silently(source_kind):
    check(source_kind, with_columns(amount=(t.Annotated[Decimal, Field(decimal_places=1)], pl.Decimal(10, 1))), AMOUNT)


def test_schema_mapping_of_dtypes_and_annotations():
    dtypes = {
        "request_id": MountainashDtype.I64,
        "region": MountainashDtype.STRING,
        "amount": DecimalDtype(10, 2),
        "level": MountainashDtype.I32,
    }
    check_context_schema(dtypes, metadata(REGION, AMOUNT, LEVEL))
    check_context_schema({"request_id": str, "region": str, "level": int}, metadata(REGION, LEVEL))
    with pytest.raises(ValueError, match=r"field 'region'"):
        check_context_schema({"request_id": str, "region": int}, metadata(REGION))


def test_list_valued_fields_are_checked_by_element_type():
    field = ContextField(name="tags", data_type="str", wildcard="*")
    check_context_schema(required_model(tags=list[str]), metadata(field))
    check_context_schema({"request_id": str, "tags": MountainashDtype.STRING}, metadata(field))
    with pytest.raises(ValueError, match=r"field 'tags'"):
        check_context_schema(required_model(tags=list[int]), metadata(field))


def test_a_value_that_is_not_a_schema_is_rejected():
    with pytest.raises(TypeError, match="schema_or_class"):
        check_context_schema(42, metadata(REGION))  # type: ignore[arg-type]


def test_relation_schema_is_read_without_execution(backend_name, monkeypatch):
    relation = context_relation(backend_name, pl.DataFrame(MATCHING_DATA))
    calls: list[dict] = []
    original = Relation.collect_schema

    def recording(self, *args, **kwargs):
        calls.append(kwargs)
        return original(self, *args, **kwargs)

    def executed(*_args, **_kwargs):
        raise AssertionError("the relation was executed")

    monkeypatch.setattr(Relation, "collect_schema", recording)
    for name in ("to_dicts", "to_dict", "snapshot", "collect", "count_rows", "head", "first", "to_pandas"):
        monkeypatch.setattr(Relation, name, executed)
    check_context_schema(relation, metadata(REGION, LEVEL))
    assert calls and all(call == {"allow_execution": False} for call in calls)


MATCHING_DATA = {"request_id": [1, 2], "region": ["AU", "NZ"], "level": [1, 2]}


def test_relation_input_matches_class_input_on_both_backends(backend_name):
    relation = context_relation(backend_name, pl.DataFrame(MATCHING_DATA))
    check_context_schema(relation, metadata(REGION, LEVEL))
    bad = context_relation(backend_name, pl.DataFrame({**MATCHING_DATA, "region": [1, 2]}))
    with pytest.raises(ValueError, match=r"field 'region'"):
        check_context_schema(bad, metadata(REGION, LEVEL))
