"""Typed contribution and output declarations for the replacement accumulator."""

from __future__ import annotations

import typing as t
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from mountainash_rules.core.constants import DataType


class AggregateOp(StrEnum):
    """Native commutative folds over matching contributions."""

    SUM = "sum"
    MIN = "min"
    MAX = "max"


class Aggregate(BaseModel):
    """Authored contribution type and optional final decimal conversion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    column_name: str
    operation: AggregateOp = AggregateOp.SUM
    output_name: str | None = None
    data_type: DataType
    timezone: t.Literal["naive", "utc"] | None = None

    @field_validator("column_name", "output_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is not None:
            if not value:
                raise ValueError("Aggregate names must be nonempty")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "Aggregate names must contain Unicode scalars"
                ) from exc
        return value

    @property
    def resolved_output_name(self) -> str:
        return self.output_name or self.column_name

    @model_validator(mode="after")
    def _declaration(self) -> Aggregate:
        if self.data_type is DataType.FLOAT:
            raise ValueError("Fractional aggregates require DECIMAL declarations")
        if self.operation is AggregateOp.SUM and not self.data_type.is_numeric:
            raise ValueError("sum requires INT or DECIMAL contributions")
        if self.data_type is DataType.DATETIME:
            if self.timezone is None:
                raise ValueError("DATETIME aggregates require an explicit timezone")
        elif self.timezone is not None:
            raise ValueError("timezone requires a DATETIME aggregate")
        return self

