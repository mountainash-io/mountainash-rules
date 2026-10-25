"""Dimensions, context-field types, and ruleset metadata."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator
from mountainash import DecimalDtype

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.type_families import python_data_type, type_family


class ContextField(BaseModel):
    """Typed context field declaration. Set wildcard_declared for a null wildcard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    data_type: DataType
    precision: int | None = None
    scale: int | None = None
    rounding: str | None = None
    datetime_unit: t.Literal["s", "ms", "us"] | None = None
    timezone: t.Literal["naive", "utc"] | None = None
    wildcard: t.Any = None
    wildcard_declared: bool = False
    truthy: dict[t.Any, int] = Field(default_factory=dict)

    @field_validator("data_type", mode="before")
    @classmethod
    def _coerce_data_type(cls, value):
        return (python_data_type(value) or value) if isinstance(value, type) else value

    @model_validator(mode="after")
    def _validate_field(self):
        if self.wildcard is not None and not self.wildcard_declared:
            object.__setattr__(self, "wildcard_declared", True)
        if not self.name:
            raise ValueError("Context field name must be nonempty")
        type_family(self.data_type).validate_field(self)
        if any(outcome not in (-1, 0, 1) for outcome in self.truthy.values()):
            raise ValueError("Truthy outcomes must be -1, 0, or 1")
        return self

    @property
    def decimal_dtype(self) -> DecimalDtype | None:
        return type_family(self.data_type).decimal_dtype(self)

    @property
    def value_dtype(self) -> t.Any:
        """Cast target for a value of this field, including a typed null.

        DATETIME keeps its declared timezone so an unavailable value compares with
        rule values instead of becoming a naive timestamp. Microsecond storage
        matches Python ``datetime``; the declared unit is applied by truncation.
        """
        return type_family(self.data_type).value_dtype(self)

    def successor(self, value):
        """Return the next representable ordered value at this field's resolution."""
        return type_family(self.data_type).successor(self, value)


class Dimension(BaseModel):
    """A matching strategy over one declared context field."""

    model_config = ConfigDict(extra="forbid")

    dimension_name: str
    context_field: str
    rule_field: str | None = None
    match_strategy: MatchStrategy = MatchStrategy.EXACT
    range_min_field: str | None = None
    range_max_field: str | None = None
    regex_pattern: str | None = None
    _field: ContextField | None = PrivateAttr(default=None)

    @property
    def field(self) -> ContextField:
        if self._field is None:
            raise ValueError(f"Dimension {self.dimension_name!r} is not resolved against metadata")
        return self._field

    def model_copy(self, *, update=None, deep=False):
        copied = super().model_copy(update=update, deep=deep)
        if self._field is not None:
            object.__setattr__(copied, "_field", self._field)
        return copied

    @property
    def data_type(self) -> DataType:
        return self.field.data_type

    @property
    def decimal_dtype(self) -> DecimalDtype | None:
        return self.field.decimal_dtype

    @property
    def rounding(self) -> str | None:
        return self.field.rounding

    @property
    def wildcard(self) -> t.Any:
        return self.field.wildcard

    @property
    def resolved_context_field(self) -> str:
        return self.context_field

    @property
    def resolved_rule_field(self) -> str:
        return self.rule_field or self.dimension_name

    @model_validator(mode="after")
    def _validate_strategy(self):
        if self.match_strategy is MatchStrategy.RANGE and (not self.range_min_field or not self.range_max_field):
            raise ValueError(f"Dimension '{self.dimension_name}' range requires both endpoints")
        if self.match_strategy is MatchStrategy.CONTEXT_REGEX:
            if not self.regex_pattern:
                raise ValueError("context_regex requires a non-empty regex_pattern")
        elif self.regex_pattern is not None:
            raise ValueError("regex_pattern is valid only for context_regex")
        return self


class DimensionsMetadata(BaseModel):
    """Ruleset metadata, serializable as five flat relational tables."""

    model_config = ConfigDict(extra="forbid")

    dimensions: tuple[Dimension, ...]
    fields: tuple[ContextField, ...] = ()
    uuid_column: str | None = None
    scope_column: str | None = None
    outputs: tuple[Aggregate, ...] = ()

    @model_validator(mode="after")
    def _bind_fields(self):
        by_name: dict[str, ContextField] = {}
        for field in self.fields:
            previous = by_name.setdefault(field.name, field)
            if previous != field:
                raise ValueError(f"conflicting definitions for context field {field.name!r}")
        if len(by_name) != len(self.fields):
            raise ValueError("Duplicate context field declarations")
        for dim in self.dimensions:
            try:
                field = by_name[dim.context_field]
            except KeyError as exc:
                raise ValueError(f"Undeclared context field {dim.context_field!r}") from exc
            object.__setattr__(dim, "_field", field)
            if dim.match_strategy in (MatchStrategy.MIN, MatchStrategy.MAX, MatchStrategy.RANGE):
                if field.data_type is DataType.FLOAT:
                    raise ValueError("FLOAT ordered dimensions require DECIMAL")
                if not (field.data_type.is_numeric or field.data_type.is_temporal):
                    raise ValueError(f"{dim.match_strategy.value} requires a numeric or temporal field")
            if dim.match_strategy in (MatchStrategy.REGEX, MatchStrategy.CONTEXT_REGEX, MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS) and field.data_type is not DataType.STR:
                raise ValueError(f"{dim.match_strategy.value} requires STR")
        if len({dim.dimension_name for dim in self.dimensions}) != len(self.dimensions):
            raise ValueError("Duplicate dimension names")
        outputs: set[tuple[str | None, str]] = set()
        for output in self.outputs:
            key = output.scope, output.output_name
            if key in outputs:
                raise ValueError("Output names must be unique within each scope")
            outputs.add(key)
        return self

    def get_dimension(self, name: str) -> Dimension:
        for dimension in self.dimensions:
            if dimension.dimension_name == name:
                return dimension
        raise KeyError(f"Dimension '{name}' not found")

    def to_tables(self):
        from mountainash.relations import relation
        from mountainash_rules.core.metadata_tables import metadata_to_tables
        return metadata_to_tables(self, relation)

    @classmethod
    def from_tables(cls, tables):
        from mountainash_rules.core.metadata_tables import metadata_from_tables
        return metadata_from_tables(tables, cls)
