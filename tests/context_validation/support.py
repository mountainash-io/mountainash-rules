"""Shared helpers for the context-validation tests: metadata, protocol models and backend relations."""

from __future__ import annotations

import typing as t

import polars as pl
from pydantic import BaseModel, create_model

from mountainash_rules.core.dimension import ContextField, DimensionsMetadata
from tests.decision_table.construction_support import rules_relation


def metadata(*fields: ContextField) -> DimensionsMetadata:
    """Metadata declaring only the given context fields."""
    return DimensionsMetadata(dimensions=(), fields=fields)


def context_model(**fields: t.Any) -> type[BaseModel]:
    """A protocol model: ``request_id`` plus the named fields, each optional and unset by default."""
    definitions = {name: (kind | None, None) for name, kind in fields.items()}
    return create_model("Context", request_id=(t.Any, ...), **definitions)


def required_model(**fields: t.Any) -> type[BaseModel]:
    """A protocol model whose annotations are exactly the given ones (no implicit Optional)."""
    return create_model("Context", request_id=(t.Any, ...), **{n: (k, ...) for n, k in fields.items()})


def context_relation(backend_name: str, data: dict | pl.DataFrame):
    """A context Relation on the requested backend."""
    return rules_relation(backend_name, data, name="contexts")
