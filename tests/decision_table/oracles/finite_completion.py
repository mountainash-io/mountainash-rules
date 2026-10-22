"""Finite-completion oracle: a built table must agree with the authored rules it came from.

The oracle never touches construction code.  It judges a table by what the *authored* rules
say, using the existing ``ExpressionRulesEngine`` directly on the source rules (ternary
semantics for every dimension kind), and by what the table's ``entries`` say when the same
engine reads them with ``table.entry_metadata``.

For a context ``C`` over a finite grid (a field the context leaves out is *omitted*):

* ``survivors(scope, C)``: the source rules the engine keeps.  An omitted field scores 0 on
  every dimension over it, so these are the rules still possible.
* a *completion* of ``C`` supplies every omitted grid field from the grid; the *reachable*
  rule sets of ``C`` are the non-empty survivor sets of its completions.
* ``table_matches(table, scope, C)``: every entry the engine keeps, with its entry sources
  and outputs.

``check_equivalence`` asserts, for every context:

1. soundness: every matched entry's sources are survivors of ``C``;
2. completeness: the matched entries' sources together are exactly the survivors (so no
   entry matches iff no rule survives);
3. outputs: each matched entry's outputs are the SUM/MIN/MAX of the contributions of exactly
   its sources, recomputed from the authored rows;
4. reachability: each reachable rule set is the source set of some matched entry.  It
   holds for contexts whose supplied values, and completions drawn from ``exact_grid``, all
   score a definite outcome: a value the rules score 0 on (null, an unmapped truthy value, a
   ternary 0) keeps rules with opposing requirements all possible, and no entry holds
   opposing requirements.  Negation columns likewise hold one authored value per entry, so
   rules negating different values in one dimension never share an entry; fixtures with such
   rules pass ``exact_grid=None`` to skip the law.
"""

from __future__ import annotations

import itertools
import re
import typing as t
from dataclasses import dataclass
from decimal import Decimal

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.core.aggregate import AggregateOp
from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.decision_table.prevalidate import ValidatedRules
from mountainash_rules.decision_table.table import DecisionTable, outcome_field
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

OMITTED = object()
"""Grid choice meaning the context leaves the field out."""


@dataclass(frozen=True)
class Match:
    """One matched entry."""

    entry_id: int
    sources: frozenset
    outputs: tuple[tuple[str, t.Any], ...]
    specificity: int


def _freeze(value: t.Any) -> t.Any:
    return tuple(_freeze(item) for item in value) if isinstance(value, list) else value


def contexts_over(grid: t.Mapping[str, t.Sequence]) -> list[dict]:
    """Every context of the grid: each field omitted or set to one of its grid values."""
    names = list(grid)
    choices = [[OMITTED, *grid[name]] for name in names]
    return [
        {name: value for name, value in zip(names, combination) if value is not OMITTED}
        for combination in itertools.product(*choices)
    ]


class FiniteCompletionOracle:
    """Judge a table against the authored source rules over a finite grid."""

    def __init__(
        self,
        validated: ValidatedRules,
        grid: t.Mapping[str, t.Sequence],
        exact_grid: t.Mapping[str, t.Sequence] | None | str = "grid",
    ):
        self.metadata = validated.source_metadata
        self.grid = {name: list(values) for name, values in grid.items()}
        self.exact_grid = (
            self.grid
            if exact_grid == "grid"
            else None if exact_grid is None else {n: list(v) for n, v in exact_grid.items()}
        )
        self._rows = validated.rules.to_dicts()
        self._engines: dict[str, ExpressionRulesEngine | None] = {}
        self._survivors: dict[tuple, frozenset] = {}
        self._validated = validated
        # Everything read from a built table is a function of that (immutable) table: read it once.
        # Entries hold their table so an ``id`` is never reused while cached.
        self._table_scopes: dict[tuple[int, str], tuple[DecisionTable, _ScopeEntries]] = {}
        self._pattern_rows: dict[int, tuple[DecisionTable, list[dict]]] = {}
        self._matches: dict[tuple, list[Match]] = {}

    # -- source rules ------------------------------------------------------

    @property
    def uuid_column(self) -> str:
        return self.metadata.uuid_column

    def scope_of(self, row: dict) -> str:
        column = self.metadata.scope_column
        return "default" if column is None else row[column]

    @property
    def scopes(self) -> list[str]:
        return sorted({self.scope_of(row) for row in self._rows})

    def rows_in(self, scope: str) -> list[dict]:
        return [row for row in self._rows if self.scope_of(row) == scope]

    def _engine(self, scope: str) -> ExpressionRulesEngine:
        if scope not in self._engines:
            rules = self._validated.rules
            if self.metadata.scope_column is not None:
                rules = rules.filter(ma.col(self.metadata.scope_column).eq(scope))
            self._engines[scope] = ExpressionRulesEngine(
                rules=rules.to_polars(), dimension_metadata=self.metadata
            )
        return self._engines[scope]

    def survivors(self, scope: str, context: dict) -> frozenset:
        """The source rules of ``scope`` that survive ``context`` (omitted fields are unknown)."""
        key = (scope, tuple(sorted((k, _freeze(v)) for k, v in context.items())))
        if key not in self._survivors:
            found = relation(
                self._engine(scope).evaluate(context=context).survivors
            ).to_dicts()
            self._survivors[key] = frozenset(row[self.uuid_column] for row in found)
        return self._survivors[key]

    def completions(self, context: dict, grid: t.Mapping[str, t.Sequence] | None = None) -> list[dict]:
        """``context`` with every omitted field of ``grid`` supplied from it, in every way."""
        grid = self.grid if grid is None else grid
        omitted = [name for name in grid if name not in context]
        if not omitted:
            return [dict(context)]
        return [
            {**context, **dict(zip(omitted, values))}
            for values in itertools.product(*(grid[name] for name in omitted))
        ]

    def reachable(self, scope: str, context: dict) -> set[frozenset]:
        """The non-empty survivor sets over the ``exact_grid`` completions of ``context``."""
        sets = {
            self.survivors(scope, completion)
            for completion in self.completions(context, self.exact_grid)
        }
        return {sources for sources in sets if sources}

    def fold(self, scope: str, sources: t.Iterable) -> dict[str, t.Any]:
        """Each declared output of ``scope`` over exactly ``sources``, from the authored rows."""
        by_uuid = {row[self.uuid_column]: row for row in self.rows_in(scope)}
        folded: dict[str, t.Any] = {}
        for output in self.metadata.outputs:
            if (output.scope or "default") != scope:
                continue
            values = [
                by_uuid[uuid][output.contribution_column]
                for uuid in sources
                if by_uuid[uuid][output.contribution_column] is not None
            ]
            if not values:
                folded[output.output_name] = None
            elif output.operation is AggregateOp.SUM:
                folded[output.output_name] = sum(values)
            elif output.operation is AggregateOp.MIN:
                folded[output.output_name] = min(values)
            else:
                folded[output.output_name] = max(values)
        return folded

    # -- the built table, read through the existing engine -------------------

    def table_matches(self, table: DecisionTable, scope: str, context: dict) -> list[Match]:
        """Every entry of ``scope`` the engine keeps for ``context``, with sources and outputs."""
        key = (id(table), scope, tuple(sorted((k, _freeze(v)) for k, v in context.items())))
        if key not in self._matches:
            self._matches[key] = self._read_matches(table, scope, context)
        return list(self._matches[key])

    def _scope_entries(self, table: DecisionTable, scope: str) -> _ScopeEntries:
        """The scope's entries, their sources and the engine over them, read once per table."""
        key = (id(table), scope)
        if key not in self._table_scopes:
            entries = table.entries.filter(ma.col("scope").eq(scope))
            if entries.count_rows() == 0:
                held = _ScopeEntries([], {}, None)
            else:
                sources: dict[int, set] = {}
                for row in table.entry_sources.filter(ma.col("scope").eq(scope)).to_dicts():
                    sources.setdefault(row["entry_sources_id"], set()).add(row["source_uuid"])
                metadata = table.entry_metadata
                if not metadata.dimensions:
                    held = _ScopeEntries(entries.to_dicts(), sources, None)
                else:
                    engine = ExpressionRulesEngine(rules=entries.to_polars(), dimension_metadata=metadata)
                    held = _ScopeEntries(None, sources, engine)
            self._table_scopes[key] = (table, held)
        return self._table_scopes[key][1]

    def _read_matches(self, table: DecisionTable, scope: str, context: dict) -> list[Match]:
        held = self._scope_entries(table, scope)
        if held.engine is None and not held.undimensioned:
            return []
        names = [o.output_name for o in self.metadata.outputs if (o.scope or "default") == scope]
        if held.engine is None:
            kept = [dict(row, __specificity=0) for row in held.undimensioned]
        else:
            result = held.engine.evaluate(context={**context, **self.context_outcomes(table, context)})
            kept = relation(result.survivors).to_dicts()
        return [
            Match(
                row["entry_id"],
                frozenset(held.sources[row["entry_sources_id"]]),
                tuple((name, row[name]) for name in names),
                row["__specificity"],
            )
            for row in kept
        ]

    def context_outcomes(self, table: DecisionTable, context: dict) -> dict[str, int]:
        """The ternary outcome the context scores on each int8 outcome column of ``entries``.

        An omitted or null value is unknown (0) on pattern and TRUTHY columns unless the field
        maps null; PRESENCE reads null as 1 and a supplied value as -1; an authored OUTCOME
        dimension takes the supplied ternary itself.
        """
        fields = {field.name: field for field in self.metadata.fields}
        outcomes: dict[str, int] = {}
        for dimension in self.metadata.dimensions:
            value = context.get(dimension.context_field, OMITTED)
            column = outcome_field(dimension.dimension_name)
            if dimension.match_strategy is MatchStrategy.TRUTHY:
                mapping = fields[dimension.context_field].truthy
                outcomes[column] = 0 if value is OMITTED else mapping.get(value, 0)
            elif dimension.match_strategy is MatchStrategy.PRESENCE:
                outcomes[column] = 0 if value is OMITTED else (1 if value is None else -1)
            elif dimension.match_strategy is MatchStrategy.OUTCOME:
                outcomes[column] = 0 if value is OMITTED else value
        for row in self._patterns_of(table):
            value = context.get(row["context_field"], OMITTED)
            outcomes[outcome_field(row["column"])] = (
                0
                if value is OMITTED or value is None
                else (1 if self._pattern_holds(row["strategy"], row["pattern"], value) else -1)
            )
        return outcomes

    def _patterns_of(self, table: DecisionTable) -> list[dict]:
        key = id(table)
        if key not in self._pattern_rows:
            self._pattern_rows[key] = (table, table.pattern_conditions.to_dicts())
        return self._pattern_rows[key][1]

    @staticmethod
    def _pattern_holds(strategy: str, pattern: str, value: str) -> bool:
        if strategy == MatchStrategy.PREFIX.value:
            return value.startswith(pattern)
        if strategy == MatchStrategy.SUFFIX.value:
            return value.endswith(pattern)
        if strategy == MatchStrategy.CONTAINS.value:
            return pattern in value
        return re.search(pattern, value) is not None

    # -- the laws ------------------------------------------------------------

    def check_equivalence(
        self, table: DecisionTable, contexts: t.Iterable[dict] | None = None
    ) -> int:
        """Assert the laws for every context; returns the number of (scope, context) pairs checked."""
        checked = 0
        for context in contexts if contexts is not None else contexts_over(self.grid):
            for scope in self.scopes:
                self._check(table, scope, context)
                checked += 1
        return checked

    def _check(self, table: DecisionTable, scope: str, context: dict) -> None:
        where = f"scope {scope!r}, context {context!r}"
        expected = self.survivors(scope, context)
        matches = self.table_matches(table, scope, context)
        for match in matches:
            assert match.sources <= expected, f"unsound entry {match.entry_id} for {where}"
            outputs = self.fold(scope, match.sources)
            for name, value in match.outputs:
                assert _equal(value, outputs[name]), (
                    f"entry {match.entry_id} output {name!r}={value!r}, authored fold {outputs[name]!r}; {where}"
                )
        covered = frozenset().union(*(match.sources for match in matches))
        assert covered == expected, (
            f"entries cover {sorted(covered, key=repr)}, survivors {sorted(expected, key=repr)}; {where}"
        )
        if self.exact_grid is not None and all(
            name not in context or context[name] in values for name, values in self.exact_grid.items()
        ):
            found = {match.sources for match in matches}
            for needed in self.reachable(scope, context):
                assert needed in found, f"no entry has sources {sorted(needed, key=repr)}; {where}"


def _equal(left: t.Any, right: t.Any) -> bool:
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        return left is not None and right is not None and Decimal(left) == Decimal(right)
    return left == right


@dataclass(frozen=True)
class _ScopeEntries:
    """One scope of a built table as the oracle reads it: its entry sources and the engine over its entries.

    ``undimensioned`` holds the entry rows of a table whose entries have no dimension (no engine
    can run over them); it is ``[]`` for a scope without entries.
    """

    undimensioned: list[dict] | None
    sources: dict[int, set]
    engine: ExpressionRulesEngine | None
