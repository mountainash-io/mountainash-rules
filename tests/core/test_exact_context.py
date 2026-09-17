"""Availability is validated before profiles erase observations."""

import pytest
from pydantic import BaseModel

from mountainash_rules import DataType, Dimension, DimensionsMetadata


def contract_payload(*, required=True, dimensions=("x",), allow=("x",)):
    return {
        "schema_version": 1,
        "contract_id": "provider",
        "domain_ref": "domain",
        "fields": [{"name": "x", "data_type": "int", "required": required}],
        "profiles": [
            {
                "profile_id": "preview",
                "mode": "resolve",
                "output_fields": ["amount.sum"],
                "provenance": "none",
                "dimensions": list(dimensions),
                "allow_dont_care": list(allow),
                "promise": "allow_unresolved",
                "on_unresolved": "return",
            }
        ],
    }


def metadata(payload=None):
    return DimensionsMetadata(
        dimensions=[Dimension(dimension_name="x", data_type=DataType.INT)],
        context_contracts=[payload or contract_payload()],
    )


def classify(md, values, *, dont_care=()):
    from mountainash_rules.core.context import classify_exact_context

    contract = md.context_contracts[0]
    return classify_exact_context(
        values, md, contract, contract.profiles[0], dont_care=dont_care
    )


def test_contract_yaml_roundtrip_retains_required_policies():
    md = metadata()
    restored = DimensionsMetadata.from_yaml(md.to_yaml())
    assert restored.context_contracts == md.context_contracts
    assert restored.context_contracts[0].fields[0].required is True
    with pytest.raises(ValueError):
        metadata({**contract_payload(), "unknown_policy": True})


def test_required_value_is_checked_before_explicit_mask():
    md = metadata()
    valid = classify(md, {"x": 3}, dont_care=("x",))
    assert dict(valid.provided_values) == {"x": 3}
    assert dict(valid.effective_values) == {}
    assert not valid.issues
    for values in ({}, {"x": None}, {"x": "<NA>"}, {"x": "3"}):
        invalid = classify(md, values, dont_care=("x",))
        assert invalid.issues


def test_pydantic_defaults_do_not_invent_provider_presence():
    class Request(BaseModel):
        x: int = 3

    result = classify(metadata(), Request())
    assert result.observations["x"] == "omitted"
    assert any(issue.code == "missing_required" for issue in result.issues)
    assert not classify(metadata(), Request(x=3)).issues


def test_unmasked_alias_retains_shared_field_fact():
    payload = contract_payload(dimensions=("alias", "x"), allow=("x",))
    md = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="x", data_type="int"),
            Dimension(dimension_name="alias", context_field="x", data_type="int"),
        ],
        context_contracts=[payload],
    )
    result = classify(md, {"x": 3}, dont_care=("x",))
    assert dict(result.effective_values) == {"x": 3}
    assert not result.issues


def test_boolean_input_is_not_legalized_by_masking():
    payload = contract_payload()
    payload["fields"][0]["data_type"] = "bool"
    md = DimensionsMetadata(
        dimensions=[Dimension(dimension_name="x", data_type="bool")],
        context_contracts=[payload],
    )
    assert not classify(md, {"x": False}, dont_care=("x",)).issues
    for value in (0, 1, "true", None):
        assert classify(md, {"x": value}, dont_care=("x",)).issues


def test_contract_rejects_mixed_alias_types_and_masked_guards():
    with pytest.raises(ValueError):
        DimensionsMetadata(
            dimensions=[
                Dimension(dimension_name="x", data_type="int"),
                Dimension(dimension_name="alias", context_field="x", data_type="float"),
            ],
            context_contracts=[contract_payload()],
        )
    payload = contract_payload()
    payload["fields"][0]["data_type"] = "str"
    with pytest.raises(ValueError):
        DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name="x",
                    match_strategy="context_regex",
                    regex_pattern="a",
                )
            ],
            context_contracts=[payload],
        )


def test_nan_is_unavailable_not_invalid_concrete_input():
    md = metadata(contract_payload(required=False))
    result = classify(md, {"x": float("nan")})
    assert result.observations["x"] == "native_missing"
    assert not result.issues
    assert dict(result.provided_values) == {}


def test_utc_not_set_is_unavailable_before_profile_masking():
    import datetime as dt

    payload = contract_payload(required=False)
    payload["fields"][0].update(data_type="datetime", timezone="utc")
    md = DimensionsMetadata(
        dimensions=[Dimension(dimension_name="x", data_type="datetime")],
        context_contracts=[payload],
    )
    result = classify(md, {"x": dt.datetime(1, 1, 2, tzinfo=dt.timezone.utc)})
    assert result.observations["x"] == "not_set"
    assert dict(result.provided_values) == {}
