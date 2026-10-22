"""The DecisionTable: an immutable, physicalised rules table.

``entries`` are the output rules: one row per covered cell, with ``entry_id``, its
``entry_sources_id``, the cell's columns and the folded outputs.  ``entry_sources`` stores
which source rules each entry came from, ``pattern_conditions`` records what each derived
pattern column means, and ``source_metadata`` describes the input rules.  How the engine
reads ``entries`` (``entry_metadata``) is derived from ``source_metadata`` and
``pattern_conditions`` alone, so a table rebuilt from saved parts reads the same way.
"""

from __future__ import annotations

import re
import typing as t
from dataclasses import dataclass
from functools import cached_property

from mountainash.relations import Relation

from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.fields import NEGATION_STRATEGIES, family_for
from mountainash_rules.decision_table.stages import POSITIVE_STRATEGIES

OUTCOME_STRATEGIES = frozenset({MatchStrategy.TRUTHY, MatchStrategy.PRESENCE, MatchStrategy.OUTCOME})
OUTCOME_FIELD_PREFIX = "outcome__"


def outcome_field(column: str) -> str:
    """The context field that supplies the ternary outcome the engine compares with ``column``.

    Each int8 outcome column of ``entries`` is read by one OUTCOME dimension, whose context
    value is the caller's already computed ternary for that column.  The field is named after
    the column, never after the source field, so one source field can feed any number of
    outcome columns and never collides with the field's own value dimensions.
    """
    return f"{OUTCOME_FIELD_PREFIX}{column}"


def _natural(column: str) -> tuple:
    """Sort key putting ``d__pattern_2`` before ``d__pattern_10``."""
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", column))


def _outcome(column: str, context_field: str) -> tuple[ContextField, Dimension]:
    field = ContextField(name=outcome_field(column), data_type=DataType.INT)
    dimension = Dimension(
        dimension_name=column,
        context_field=field.name,
        rule_field=column,
        match_strategy=MatchStrategy.OUTCOME,
    )
    return field, dimension


def derive_entry_metadata(
    source_metadata: DimensionsMetadata, pattern_conditions: Relation
) -> DimensionsMetadata:
    """The metadata the engine reads ``entries`` with.

    Mirrors the column plans of construction: positive dimensions sharing a field are one
    ordered-interval or value dimension on that field's own interpretation; each NOT_EQUAL
    or SET_EXCLUSION dimension keeps its own negation column; authored TRUTHY, PRESENCE and
    OUTCOME dimensions and every ``pattern_conditions`` column are int8 required outcomes
    read as OUTCOME against a context outcome supplied under ``outcome_field(column)``.
    Pattern dimensions of the source contribute only through ``pattern_conditions``; the
    source fields are carried over unchanged.
    """
    fields = list(source_metadata.fields)
    dimensions: list[Dimension] = []
    positive: set[str] = set()
    for dimension in source_metadata.dimensions:
        strategy = dimension.match_strategy
        if strategy in POSITIVE_STRATEGIES:
            if dimension.context_field not in positive:
                positive.add(dimension.context_field)
                dimensions.append(family_for(dimension).entry_dimension(dimension.context_field))
        elif strategy in NEGATION_STRATEGIES:
            dimensions.append(
                family_for(dimension).entry_dimension(
                    dimension.dimension_name,
                    negated=True,
                    is_set=strategy is MatchStrategy.SET_EXCLUSION,
                )
            )
        elif strategy in OUTCOME_STRATEGIES:
            field, outcome = _outcome(dimension.dimension_name, dimension.context_field)
            fields.append(field)
            dimensions.append(outcome)
    columns = [
        (row["column"], row["context_field"])
        for row in pattern_conditions.select("column", "context_field").to_dicts()
    ]
    for column, context_field in sorted(columns, key=lambda pair: _natural(pair[0])):
        field, outcome = _outcome(column, context_field)
        fields.append(field)
        dimensions.append(outcome)
    return DimensionsMetadata(fields=tuple(fields), dimensions=tuple(dimensions))


@dataclass(frozen=True, eq=False)
class DecisionTable:
    """A built decision table; build and load construct it the same way."""

    entries: Relation
    source_metadata: DimensionsMetadata
    entry_sources: Relation
    pattern_conditions: Relation
    build_id: str

    @cached_property
    def entry_metadata(self) -> DimensionsMetadata:
        """How the engine reads ``entries``: ``derive_entry_metadata`` of the saved parts."""
        return derive_entry_metadata(self.source_metadata, self.pattern_conditions)


__all__: t.Sequence[str] = ("DecisionTable", "derive_entry_metadata", "outcome_field")
