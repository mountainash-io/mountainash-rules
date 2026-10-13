"""Typed contribution and output declarations for the replacement accumulator."""

from __future__ import annotations

import typing as t
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from mountainash_rules.core.codec import NumericDtype
from mountainash_rules.core.constants import DataType


class AggregateOp(StrEnum):
    """Native commutative folds over matching contributions."""

    SUM = "sum"
    MIN = "min"
    MAX = "max"
    PRODUCT = "product"


class Aggregate(BaseModel):
    """Authored contribution type and optional final decimal conversion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    column_name: str
    operation: AggregateOp = AggregateOp.SUM
    output_name: str | None = None
    data_type: DataType
    timezone: t.Literal["naive", "utc"] | None = None
    numeric_dtype: NumericDtype | None = None
    output_numeric_dtype: NumericDtype | None = None
    output_rounding: str | None = None

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
        if (
            self.operation in (AggregateOp.SUM, AggregateOp.PRODUCT)
            and not self.data_type.is_numeric
        ):
            raise ValueError("sum/product require INT or DECIMAL contributions")
        if self.data_type is DataType.DATETIME:
            if self.timezone is None:
                raise ValueError("DATETIME aggregates require an explicit timezone")
        elif self.timezone is not None:
            raise ValueError("timezone requires a DATETIME aggregate")
        if self.data_type is DataType.DECIMAL:
            if self.numeric_dtype is None:
                raise ValueError("DECIMAL contributions require numeric_dtype")
            if (
                self.operation is AggregateOp.PRODUCT
                and self.output_numeric_dtype is None
            ):
                raise ValueError(
                    "DECIMAL product requires an explicit output conversion"
                )
            if (self.output_numeric_dtype is None) != (self.output_rounding is None):
                raise ValueError("Output dtype and rounding must be declared together")
            if self.output_rounding is not None and self.output_rounding not in {
                "half_to_even",
                "half_away_from_zero",
            }:
                raise ValueError("Unsupported output rounding")
        elif any(
            value is not None
            for value in (
                self.numeric_dtype,
                self.output_numeric_dtype,
                self.output_rounding,
            )
        ):
            raise ValueError(
                "Decimal conversion declarations require DECIMAL contributions"
            )
        return self
