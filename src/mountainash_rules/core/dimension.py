"""Dimension metadata for rule evaluation."""

from __future__ import annotations

import pathlib
import typing as t
import warnings

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from mountainash_rules.core.constants import (
    PYTHON_TO_DATATYPE,
    DataType,
    DimensionRole,
    HitPolicy,
    MatchStrategy,
)


class Dimension(BaseModel):
    """A single dimension that rules are evaluated against."""

    dimension_name: str
    context_field: t.Optional[str] = None
    rule_field: t.Optional[str] = None
    match_strategy: MatchStrategy = MatchStrategy.EXACT
    data_type: DataType = DataType.STR
    role: DimensionRole = DimensionRole.CONSTRAINT
    valid_values: list[str | int | float | bool] = Field(
        default_factory=list,
        description=(
            "Declarative domain of context values for this dimension. "
            "Ignored by the engines; consumed by babel's coverage "
            "validation. Temporal domains are declared as ISO strings."
        ),
    )

    # RANGE strategy fields
    range_min_field: t.Optional[str] = None
    range_max_field: t.Optional[str] = None
    range_min_inclusive: bool = True
    range_max_inclusive: bool = True

    # REGEX strategy field — literal pattern stored on metadata (not per-rule)
    regex_pattern: t.Optional[str] = None

    @field_validator("data_type", mode="before")
    @classmethod
    def _coerce_data_type(cls, v):
        if isinstance(v, type):
            if v not in PYTHON_TO_DATATYPE:
                raise ValueError(f"Unsupported data_type class: {v!r}")
            warnings.warn(
                "Passing a Python type as data_type is deprecated; "
                f"use DataType.{PYTHON_TO_DATATYPE[v].name} or "
                f"'{PYTHON_TO_DATATYPE[v].value}'.",
                DeprecationWarning,
                stacklevel=4,
            )
            return PYTHON_TO_DATATYPE[v]
        return v

    @property
    def resolved_context_field(self) -> str:
        """The field name to extract from the context object."""
        return self.context_field or self.dimension_name

    @property
    def resolved_rule_field(self) -> str:
        """The field name in the rules DataFrame."""
        return self.rule_field or self.dimension_name

    @model_validator(mode="after")
    def _validate_strategy_fields(self) -> "Dimension":
        orderable = self.data_type.is_numeric or self.data_type.is_temporal

        if self.match_strategy == MatchStrategy.RANGE:
            if not self.range_min_field or not self.range_max_field:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses range strategy "
                    f"but is missing range_min_field or range_max_field"
                )
            if not orderable:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses range strategy "
                    f"but data_type is {self.data_type.value}; expected a "
                    f"numeric or temporal type"
                )

        if self.match_strategy in (
            MatchStrategy.REGEX,
            MatchStrategy.CONTEXT_REGEX,
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        ):
            if self.data_type is not DataType.STR:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses "
                    f"{self.match_strategy.value} but data_type is "
                    f"{self.data_type.value}; expected str"
                )

        if self.match_strategy == MatchStrategy.CONTEXT_REGEX:
            if not isinstance(self.regex_pattern, str) or not self.regex_pattern:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses context_regex and "
                    f"requires a non-empty literal 'regex_pattern'"
                )
        elif self.regex_pattern is not None:
            hint = (
                " (per-row regex reads patterns from the rule column; "
                "for a literal metadata pattern use context_regex)"
                if self.match_strategy == MatchStrategy.REGEX else ""
            )
            raise ValueError(
                f"Dimension '{self.dimension_name}' sets regex_pattern but "
                f"match_strategy is {self.match_strategy.value}{hint}"
            )

        if self.match_strategy in (
            MatchStrategy.GREATER_THAN,
            MatchStrategy.LESS_THAN,
        ):
            if not orderable:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses "
                    f"{self.match_strategy.value} but data_type is "
                    f"{self.data_type.value}; expected a numeric or "
                    f"temporal type"
                )

        if self.match_strategy in (
            MatchStrategy.SET_MEMBERSHIP,
            MatchStrategy.SET_EXCLUSION,
        ):
            if self.data_type is DataType.BOOL:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses "
                    f"{self.match_strategy.value} with data_type bool; boolean "
                    f"set dimensions are not supported (no typed wildcard sentinel "
                    f"exists and a set over {{true, false}} is degenerate)"
                )

        return self


class DimensionsMetadata(BaseModel):
    """Collection of dimension definitions for a rule set."""

    dimensions: list[Dimension]
    hit_policy: HitPolicy = HitPolicy.COLLECT
    priority_field: t.Optional[str] = None
    output_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_hit_policy(self) -> "DimensionsMetadata":
        if self.hit_policy == HitPolicy.PRIORITY and not self.priority_field:
            raise ValueError("hit_policy=priority requires priority_field")
        return self

    @model_validator(mode="after")
    def _validate_unique_names(self) -> "DimensionsMetadata":
        names = [d.dimension_name for d in self.dimensions]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise ValueError(f"Duplicate dimension names: {set(dupes)}")
        return self

    def get_dimension(self, name: str) -> Dimension:
        """Look up a dimension by name."""
        for d in self.dimensions:
            if d.dimension_name == name:
                return d
        raise KeyError(f"Dimension '{name}' not found")

    def to_yaml(self) -> str:
        """Serialise to YAML (defaults omitted for forward compatibility)."""
        return yaml.safe_dump(
            self.model_dump(mode="json", exclude_defaults=True),
            sort_keys=False,
        )

    @classmethod
    def from_yaml(cls, text: str) -> "DimensionsMetadata":
        return cls.model_validate(yaml.safe_load(text))

    def to_yaml_file(self, path: str | pathlib.Path) -> pathlib.Path:
        path = pathlib.Path(path)
        path.write_text(self.to_yaml(), encoding="utf-8")
        return path

    @classmethod
    def from_yaml_file(cls, path: str | pathlib.Path) -> "DimensionsMetadata":
        return cls.from_yaml(pathlib.Path(path).read_text(encoding="utf-8"))
