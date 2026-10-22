"""Plan 3 Task 2: relational value validation of context batches, and request identity."""

from __future__ import annotations

import warnings

import polars as pl
import pytest

from mountainash_rules.context_validation import validate_contexts
from mountainash_rules.core.dimension import ContextField
from tests.context_validation.support import context_model, context_relation, metadata

REGION = ContextField(name="region", data_type="str", wildcard="*")
SCORE = ContextField(name="score", data_type="float")
CODE = ContextField(name="code", data_type="str", truthy={"yes": 1, "no": -1})
TAGS = ContextField(name="tags", data_type="str", wildcard="*")
NULL_WILDCARD = ContextField(name="vip", data_type="bool", wildcard=None, wildcard_declared=True)
META = metadata(REGION, SCORE, CODE, TAGS)


def relation_of(backend_name: str, data: dict, **schema):
    return context_relation(backend_name, pl.DataFrame(data, schema_overrides=schema or None))


def valid(contexts, meta=META):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert validate_contexts(contexts, meta) is contexts


# -- request identity (re-homed from the engine's batch identity tests) ----------------------


@pytest.mark.parametrize("region", ["AU", "XX"])
def test_null_ids_are_rejected_whether_or_not_contexts_would_match(backend_name, region):
    contexts = relation_of(backend_name, {"request_id": [None, 1], "region": [region, region]}, request_id=pl.Int64)
    with pytest.raises(ValueError, match=r"'request_id' must be non-null; 1 null value\(s\) among 2 row\(s\)"):
        validate_contexts(contexts, META)


def test_duplicate_ids_are_rejected_naming_them(backend_name):
    contexts = relation_of(backend_name, {"request_id": [7, 8, 7, 8, 9], "region": ["AU", "NZ", "XX", "AU", "AU"]})
    with pytest.raises(ValueError, match=r"must be unique; duplicated: 7, 8") as caught:
        validate_contexts(contexts, META)
    assert "9" not in str(caught.value).split("duplicated:")[1]


def test_missing_request_id_column_is_rejected(backend_name):
    with pytest.raises(ValueError, match="no 'request_id' column"):
        validate_contexts(relation_of(backend_name, {"region": ["AU"]}), META)


def test_string_ids_and_ordering_are_not_constrained(backend_name):
    valid(relation_of(backend_name, {"request_id": ["z", "m", "a"], "region": ["AU", "XX", "NZ"]}))


def test_null_and_duplicate_ids_are_listed_together(backend_name):
    contexts = relation_of(backend_name, {"request_id": [1, 1, None]}, request_id=pl.Int64)
    with pytest.raises(ValueError) as caught:
        validate_contexts(contexts, META)
    assert "must be non-null" in str(caught.value) and "duplicated: 1" in str(caught.value)


def test_list_input_identity_is_checked_after_conversion():
    model = context_model(region=str)
    with pytest.raises(ValueError, match=r"duplicated: 'a'"):
        validate_contexts([model(request_id="a"), model(request_id="a")], META)
    with pytest.raises(ValueError, match=r"must be non-null"):
        validate_contexts([model(request_id=None), model(request_id=1)], META)


def test_list_input_objects_without_request_id_are_rejected():
    class Bare:
        model_fields_set: set = set()

    with pytest.raises(ValueError, match=r"position\(s\) \[0\] have no 'request_id'"):
        validate_contexts([Bare()], META)


def test_unmatched_context_ids_reports_ids_without_validating_them():
    from mountainash_rules import Dimension, DimensionsMetadata, ExpressionRulesEngine

    region = ContextField(name="region", data_type="str", wildcard="<NA>")
    engine = ExpressionRulesEngine(
        pl.DataFrame({"rule_name": ["au", "nz"], "region": ["AU", "NZ"]}),
        dimension_metadata=DimensionsMetadata(
            fields=(region,), dimensions=[Dimension(dimension_name="region", context_field="region")]
        ),
    )
    contexts = pl.DataFrame({"cid": [7, 7, 9], "region": ["AU", "XX", "XX"]})  # duplicate id 7
    batch = engine.evaluate_batch(contexts, context_id_field="cid")
    assert batch.unmatched_context_ids(contexts) == [9]  # the post-match accessor never rejects identity
    with pytest.raises(ValueError, match="duplicated: 7"):
        validate_contexts(context_relation("polars", contexts.rename({"cid": "request_id"})), metadata(region))


# -- values ----------------------------------------------------------------------------------


def test_non_finite_numbers_are_rejected_naming_the_requests(backend_name):
    contexts = relation_of(
        backend_name,
        {"request_id": [1, 2, 3, 4], "score": [1.5, float("nan"), float("inf"), None]},
    )
    with pytest.raises(ValueError, match=r"field 'score': 2 value\(s\) is not finite; request_id 2, 3"):
        validate_contexts(contexts, META)


def test_unmapped_truthy_values_are_rejected(backend_name):
    contexts = relation_of(backend_name, {"request_id": [1, 2, 3], "code": ["yes", "maybe", None]})
    with pytest.raises(ValueError, match=r"field 'code': 1 value\(s\) is not mapped by the field's truthy values; request_id 2"):
        validate_contexts(contexts, META)


def test_mapped_truthy_values_and_the_declared_marker_pass(backend_name):
    field = ContextField(name="code", data_type="str", wildcard="*", truthy={"yes": 1})
    valid(relation_of(backend_name, {"request_id": [1, 2, 3], "code": ["yes", "*", None]}), metadata(field))


def test_a_wildcard_marker_mapped_by_truthy_is_misused(backend_name):
    field = ContextField(name="code", data_type="str", wildcard="*", truthy={"yes": 1, "*": -1})
    contexts = relation_of(backend_name, {"request_id": [1, 2], "code": ["yes", "*"]})
    with pytest.raises(ValueError, match=r"is the reserved wildcard marker and also a mapped truthy value; request_id 2"):
        validate_contexts(contexts, metadata(field))


def test_an_explicitly_mapped_null_that_is_also_the_declared_wildcard_is_misused(backend_name):
    field = ContextField(name="vip", data_type="bool", wildcard=None, wildcard_declared=True, truthy={True: 1, None: -1})
    contexts = relation_of(backend_name, {"request_id": [1, 2], "vip": [True, None]})
    with pytest.raises(ValueError, match=r"is null, the declared wildcard, and also a mapped truthy value; request_id 2"):
        validate_contexts(contexts, metadata(field))


def test_a_mapped_null_without_a_null_wildcard_is_ordinary(backend_name):
    field = ContextField(name="code", data_type="str", truthy={"yes": 1, None: -1})
    valid(relation_of(backend_name, {"request_id": [1, 2], "code": ["yes", None]}), metadata(field))


def test_context_nulls_are_unknown_and_not_rejected(backend_name):
    contexts = relation_of(
        backend_name,
        {"request_id": [1, 2], "region": [None, "AU"], "score": [None, 1.0], "code": [None, "no"]},
        request_id=pl.Int64,
        region=pl.String,
        score=pl.Float64,
        code=pl.String,
    )
    valid(contexts)


def test_absent_columns_are_not_supplied_and_not_checked(backend_name):
    valid(relation_of(backend_name, {"request_id": [1, 2]}))


def test_a_wildcard_marker_among_other_list_elements_is_misused(backend_name):
    contexts = relation_of(backend_name, {"request_id": [1, 2, 3], "tags": [["*"], ["a", "*"], ["a"]]})
    with pytest.raises(ValueError, match=r"embeds the reserved wildcard marker among other list elements; request_id 2"):
        validate_contexts(contexts, META)


def test_null_elements_in_list_values_are_rejected(backend_name):
    contexts = relation_of(backend_name, {"request_id": [1, 2, 3], "tags": [["a", None], ["b"], None]})
    with pytest.raises(ValueError, match=r"field 'tags': 1 value\(s\) has a null element in a list value; request_id 1"):
        validate_contexts(contexts, META)


def test_a_null_list_value_is_unknown_and_empty_lists_pass(backend_name):
    contexts = relation_of(backend_name, {"request_id": [1, 2], "tags": [None, []]}, request_id=pl.Int64, tags=pl.List(pl.String))
    valid(contexts)


def test_every_invalid_value_is_listed_in_one_error(backend_name):
    contexts = relation_of(
        backend_name,
        {
            "request_id": [1, 1],
            "score": [float("nan"), 1.0],
            "code": ["bad", "yes"],
            "tags": [["a", None], ["b"]],
        },
    )
    with pytest.raises(ValueError) as caught:
        validate_contexts(contexts, META)
    message = str(caught.value)
    for part in ("duplicated: 1", "field 'score'", "field 'code'", "field 'tags'"):
        assert part in message


# -- list input -----------------------------------------------------------------------------


def test_list_input_is_returned_as_passed():
    model = context_model(region=str, score=float, code=str, tags=list[str])
    contexts = [model(request_id=1, region="AU", score=1.0), model(request_id=2)]
    valid(contexts)
    as_tuple = tuple(contexts)
    assert validate_contexts(as_tuple, META) is as_tuple


def test_list_input_unset_is_unknown_and_explicit_null_is_not_rejected():
    model = context_model(region=str, code=str)
    valid([model(request_id=1), model(request_id=2, region=None, code=None)])


def test_list_input_values_are_validated_after_conversion():
    model = context_model(score=float, code=str, tags=list[str | None])
    with pytest.raises(ValueError) as caught:
        validate_contexts(
            [
                model(request_id=1, score=float("inf")),
                model(request_id=2, code="maybe"),
                model(request_id=3, tags=["a", None]),
            ],
            META,
        )
    message = str(caught.value)
    assert "request_id 1" in message and "request_id 2" in message and "request_id 3" in message


def test_an_empty_list_is_valid():
    assert validate_contexts([], META) == []


def test_other_inputs_are_rejected():
    with pytest.raises(ValueError, match="Relation or a list"):
        validate_contexts({"request_id": [1]}, META)  # type: ignore[arg-type]
