"""One class per match strategy, lowering rule cells into the shared region vocabulary.

A strategy never touches a table: it reads the rule columns it needs
(``rule_columns``), builds the operand ``Cell`` s (``cells``) and lowers them into
a region (``lower``).  The field family (``fields.py``) interprets the region.
"""

from __future__ import annotations

import abc
import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash.relations import Relation

from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import Dimension
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.decision_table.fields import (
    BOUND_SCOPE,
    BOUND_UUID,
    SCOPE,
    SOURCE_UUID,
    concrete_flag,
    required_outcome,
    Field,
    Region,
    family_for,
    negation,
)


@dataclass(frozen=True)
class Cell:
    """One rule cell: its canonical value and whether it is the field's wildcard."""

    value: t.Any
    wildcard: t.Any = False


class Strategy(abc.ABC):
    """Common contract of the strategy classes."""

    match_strategy: t.ClassVar[MatchStrategy]
    #: True when the region lives in its own negated column (NOT_EQUAL, SET_EXCLUSION).
    negated: t.ClassVar[bool] = False
    #: True when the rule cell is a list.
    is_set: t.ClassVar[bool] = False

    def rule_columns(self, dimension: Dimension) -> tuple[str, ...]:
        return (dimension.resolved_rule_field,)

    def family(self, dimension: Dimension) -> Field:
        return family_for(dimension)

    def cells(self, family: Field, rel: Relation, dimension: Dimension) -> tuple[Cell, ...]:
        """Operand cells for ``dimension`` read from the rule relation ``rel``."""
        schema = rel.collect_schema(allow_execution=False)
        columns = self.rule_columns(dimension)
        if self.is_set:
            return tuple(Cell(*family.set_cell(rel, dimension, column)) for column in columns)
        return tuple(Cell(*family.scalar_cell(ma.col(column), schema[column])) for column in columns)

    @abc.abstractmethod
    def lower(
        self, family: Field, cells: t.Sequence[Cell], dimension: Dimension | None = None
    ) -> Region:
        """Lower rule cells to a region of the shared vocabulary."""

    def _one(self, cells: t.Sequence[Cell]) -> Cell:
        if len(cells) != 1:
            raise DecisionTableError(f"{self.match_strategy.value} takes one rule cell")
        return cells[0]


class Exact(Strategy):
    match_strategy = MatchStrategy.EXACT

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return family.point(cell.value, wildcard=cell.wildcard)


class NotEqual(Strategy):
    match_strategy = MatchStrategy.NOT_EQUAL
    negated = True

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return negation(family.point(cell.value, wildcard=cell.wildcard))


class SetMembership(Strategy):
    match_strategy = MatchStrategy.SET_MEMBERSHIP
    is_set = True

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return family.members(cell.value, wildcard=cell.wildcard)


class SetExclusion(Strategy):
    match_strategy = MatchStrategy.SET_EXCLUSION
    negated = True
    is_set = True

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return negation(family.members(cell.value, wildcard=cell.wildcard))


class Range(Strategy):
    match_strategy = MatchStrategy.RANGE

    def rule_columns(self, dimension):
        return (dimension.range_min_field, dimension.range_max_field)

    def lower(self, family, cells, dimension=None):
        if len(cells) != 2:
            raise DecisionTableError("range takes two rule cells")
        low, high = cells
        return family.interval(
            low.value, high.value, low_wildcard=low.wildcard, high_wildcard=high.wildcard
        )


class Min(Strategy):
    match_strategy = MatchStrategy.MIN

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return family.interval(cell.value, None, low_wildcard=cell.wildcard)


class Max(Strategy):
    match_strategy = MatchStrategy.MAX

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return family.interval(None, cell.value, high_wildcard=cell.wildcard)


class _Pattern(Strategy):
    """Per-row pattern strategies: a derived ternary condition ``matches(P)``."""

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return family.matches(cell.value, self.match_strategy, wildcard=cell.wildcard)


class Regex(_Pattern):
    match_strategy = MatchStrategy.REGEX


class Prefix(_Pattern):
    match_strategy = MatchStrategy.PREFIX


class Suffix(_Pattern):
    match_strategy = MatchStrategy.SUFFIX


class Contains(_Pattern):
    match_strategy = MatchStrategy.CONTAINS


class ContextRegex(Strategy):
    """Literal pattern on the dimension; the rules carry no column for it."""

    match_strategy = MatchStrategy.CONTEXT_REGEX

    def rule_columns(self, dimension):
        return ()

    def lower(self, family, cells, dimension=None):
        if dimension is None or not dimension.regex_pattern:
            raise DecisionTableError("context_regex requires a dimension regex_pattern")
        return family.matches(dimension.regex_pattern, self.match_strategy)


class Truthy(Strategy):
    """Authored value mapped to its declared outcome through the field's truthy table."""

    match_strategy = MatchStrategy.TRUTHY

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        if cell.wildcard is True:
            return family.everything()
        return family.outcome(family.map_truthy(cell.value), wildcard=cell.wildcard)


class Presence(Strategy):
    """``IS_NULL`` -> 1, ``IS_NOT_NULL`` -> -1, wildcard -> 0."""

    match_strategy = MatchStrategy.PRESENCE

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        if cell.wildcard is True:
            return family.everything()
        return family.outcome(family.map_presence(cell.value), wildcard=cell.wildcard)


class Outcome(Strategy):
    """Authored required ternary outcome, stored as is."""

    match_strategy = MatchStrategy.OUTCOME

    def lower(self, family, cells, dimension=None):
        cell = self._one(cells)
        return family.outcome(cell.value, wildcard=cell.wildcard)


STRATEGIES: dict[MatchStrategy, Strategy] = {
    strategy.match_strategy: strategy
    for strategy in (
        Exact(),
        NotEqual(),
        SetMembership(),
        SetExclusion(),
        Range(),
        Min(),
        Max(),
        Regex(),
        Prefix(),
        Suffix(),
        Contains(),
        ContextRegex(),
        Truthy(),
        Presence(),
        Outcome(),
    )
}


def strategy_for(match_strategy: MatchStrategy) -> Strategy:
    return STRATEGIES[match_strategy]


def unsatisfiable_rules(
    bound: Relation, dimensions: t.Sequence[Dimension]
) -> list[tuple[str, str, Relation]]:
    """``(context field, reason, rules)`` for rules whose conditions on a field cannot all hold.

    Each dimension is lowered and interpreted exactly as construction does; the field family's
    ``unsatisfiable_rules`` decides emptiness from those regions.  A rule requiring a null
    context value (PRESENCE ``IS_NULL``) together with any other concrete condition on the
    same field is unsatisfiable across families.
    """
    by_field: dict[str, list[tuple[int, Dimension, t.Any]]] = {}
    families: dict[str, dict[type, Field]] = {}
    for index, dimension in enumerate(dimensions):
        if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        strategy = strategy_for(dimension.match_strategy)
        family = strategy.family(dimension)
        region = family.interpret(strategy.lower(family, strategy.cells(family, bound, dimension), dimension))
        by_field.setdefault(dimension.context_field, []).append((index, dimension, region))
        families.setdefault(dimension.context_field, {})[type(family)] = family

    found: list[tuple[str, str, Relation]] = []
    for field_name, entries in by_field.items():
        for family_type, family in families[field_name].items():
            members = [entry for entry in entries if type(family_for(entry[1])) is family_type]
            found.append((field_name, "conflicting conditions", family.unsatisfiable_rules(bound, members)))
        presence = [e for e in entries if e[1].match_strategy is MatchStrategy.PRESENCE]
        concrete = [e for e in entries if e[1].match_strategy is not MatchStrategy.PRESENCE]
        if presence and concrete:
            null = ma.lit(False)
            for _, _, region in presence:
                null = null | required_outcome(region).eq(1)
            other = ma.lit(False)
            for _, _, region in concrete:
                other = other | concrete_flag(region)
            keys = bound.filter((null & other).fill_null(False)).select(
                ma.col(BOUND_SCOPE).alias(SCOPE), ma.col(BOUND_UUID).alias(SOURCE_UUID)
            )
            found.append((field_name, "both null and concrete", keys))
    return found
