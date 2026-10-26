"""Output contribution declarations used by DecisionTable metadata."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator
from mountainash import DecimalDtype

from mountainash_rules.core.constants import DataType
from mountainash_rules.core.type_families import type_family


class AggregateOp(StrEnum):
    SUM = "sum"
    MIN = "min"
    MAX = "max"


class Aggregate(BaseModel):
    """Output fold declaration, keyed by scope and logical output name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str | None = None
    output_name: str
    contribution_column: str
    operation: AggregateOp = AggregateOp.SUM
    data_type: DataType
    precision: int | None = None
    scale: int | None = None
    rounding: str | None = None
    timezone: str | None = None

    @model_validator(mode="after")
    def _validate_declaration(self):
        if not self.output_name or not self.contribution_column:
            raise ValueError("Output names and contribution columns must be nonempty")
        type_family(self.data_type).validate_output(self)
        return self

    @property
    def decimal_dtype(self) -> DecimalDtype | None:
        return type_family(self.data_type).decimal_dtype(self)
