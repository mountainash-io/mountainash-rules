"""Output contribution declarations used by DecisionTable metadata."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator
from mountainash import DecimalDtype

from mountainash_rules.core.constants import DataType


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
        if self.data_type is DataType.DECIMAL:
            if self.precision is None or self.scale is None or not 0 <= self.scale <= self.precision <= 16:
                raise ValueError("DECIMAL outputs require precision and scale")
            if self.rounding not in {"half_to_even", "half_away_from_zero"}:
                raise ValueError("DECIMAL outputs require supported rounding")
        elif any(value is not None for value in (self.precision, self.scale, self.rounding)):
            raise ValueError("Decimal declarations require DECIMAL outputs")
        if self.data_type is DataType.DATETIME:
            if self.timezone not in {"naive", "utc"}:
                raise ValueError("DATETIME outputs require timezone")
        elif self.timezone is not None:
            raise ValueError("timezone requires DATETIME outputs")
        return self

    @property
    def decimal_dtype(self) -> DecimalDtype | None:
        return DecimalDtype(self.precision, self.scale) if self.precision is not None else None
