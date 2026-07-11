"""Dimension metadata for rule evaluation."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, model_validator

from mountainash_rules.constants import DimensionRole, MatchStrategy


class Dimension(BaseModel):
    """A single dimension that rules are evaluated against."""

    dimension_name: str
    context_field: t.Optional[str] = None
    rule_field: t.Optional[str] = None
    match_strategy: MatchStrategy = MatchStrategy.EXACT
    data_type: type = str
    role: DimensionRole = DimensionRole.CONSTRAINT
    valid_values: list[t.Any] = []

    # RANGE strategy fields
    range_min_field: t.Optional[str] = None
    range_max_field: t.Optional[str] = None
    range_min_inclusive: bool = True
    range_max_inclusive: bool = True

    # REGEX strategy field — literal pattern stored on metadata (not per-rule)
    regex_pattern: t.Optional[str] = None

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
        if self.match_strategy == MatchStrategy.RANGE:
            if not self.range_min_field or not self.range_max_field:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses RANGE strategy "
                    f"but is missing range_min_field or range_max_field"
                )
            if self.data_type not in (int, float):
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses RANGE strategy "
                    f"but data_type is {self.data_type.__name__}, expected int or float"
                )

        if self.match_strategy in (
            MatchStrategy.REGEX,
            MatchStrategy.PREFIX,
            MatchStrategy.SUFFIX,
            MatchStrategy.CONTAINS,
        ):
            if self.data_type is not str:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses {self.match_strategy.name} "
                    f"but data_type is {self.data_type.__name__}, expected str"
                )

        if self.match_strategy == MatchStrategy.REGEX:
            if not isinstance(self.regex_pattern, str) or not self.regex_pattern:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses REGEX strategy and "
                    f"requires a non-empty literal 'regex_pattern' on the Dimension"
                )
        else:
            if self.regex_pattern is not None:
                raise ValueError(
                    f"Dimension '{self.dimension_name}' sets regex_pattern but "
                    f"match_strategy is {self.match_strategy.name}; "
                    f"regex_pattern is only valid for REGEX strategy"
                )

        if self.match_strategy in (
            MatchStrategy.GREATER_THAN,
            MatchStrategy.LESS_THAN,
        ):
            if self.data_type not in (int, float):
                raise ValueError(
                    f"Dimension '{self.dimension_name}' uses {self.match_strategy.name} "
                    f"but data_type is {self.data_type.__name__}, expected int or float"
                )

        return self


class DimensionsMetadata(BaseModel):
    """Collection of dimension definitions for a rule set."""

    dimensions: list[Dimension]

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
