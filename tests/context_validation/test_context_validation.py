"""Plan 3 Task 3: the documented three levels and context protocol, and their use together."""

from __future__ import annotations

import warnings

import polars as pl
import pytest
from pydantic import BaseModel, ValidationError, create_model

import mountainash_rules.context_validation as context_validation
from mountainash_rules.context_validation import check_context_schema, validate_contexts
from mountainash_rules.core.dimension import ContextField
from tests.context_validation.support import context_model, context_relation, metadata

REGION = ContextField(name="region", data_type="str", wildcard="*")
META = metadata(REGION)


def test_the_module_documents_the_three_levels_and_the_context_protocol():
    doc = context_validation.__doc__
    for phrase in (
        "caller's own pydantic model",
        "check_context_schema",
        "validate_contexts",
        "request_id",
        "model_fields_set",
        "performs no validation",
        "Relation",
    ):
        assert phrase in doc


def test_the_public_names_of_the_module_are_the_two_capabilities():
    assert set(context_validation.__all__) == {"check_context_schema", "validate_contexts"}


class Strict(BaseModel):
    request_id: int
    region: str


def test_the_three_levels_run_independently(backend_name):
    # Level 1: the caller's own model owns instance validation and knows nothing about rules.
    with pytest.raises(ValidationError):
        Strict(request_id="x", region="AU")
    # Level 2: schema alignment, by class and by relation, without data.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        check_context_schema(Strict, META)
        relation = context_relation(backend_name, {"request_id": [1, 1], "region": ["AU", "NZ"]})
        check_context_schema(relation, META)
    # Level 3: relational values; identity is only checked here.
    with pytest.raises(ValueError, match="duplicated: 1"):
        validate_contexts(relation, META)
    objects = [Strict(request_id=1, region="AU"), Strict(request_id=2, region="NZ")]
    assert validate_contexts(objects, META) is objects


def test_protocol_objects_need_only_request_id_and_named_fields():
    plain = create_model("Anything", request_id=(str, ...), region=(str | None, None), unrelated=(int, 3))
    objects = [plain(request_id="a", region="AU"), plain(request_id="b")]
    assert validate_contexts(objects, META) is objects
    check_context_schema(plain, META)


def test_validation_is_a_separate_caller_step_that_raises_before_matching():
    model = context_model(region=str)
    duplicates = [model(request_id=1), model(request_id=1)]
    with pytest.raises(ValueError, match="duplicated: 1"):
        validate_contexts(duplicates, META)
