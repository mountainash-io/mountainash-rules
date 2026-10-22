"""Application: match caller-validated contexts against a DecisionTable's entries.

``DecisionTable.match(contexts, scopes=None)`` returns a deferred :class:`MatchOperation`;
:meth:`MatchOperation.fetch` runs it.  Fetch performs no validation (callers validate their
contexts before matching) and no rule re-validation: it filters the entries to the selected
scopes, restricts the derived pattern columns to those the selected entries reference,
computes the context's ternary outcome for every int8 outcome column, and then calls the
filter engine once over the selected entries with the table's ``entry_metadata``.

The engine reads each int8 outcome column of ``entries`` (patterns, authored TRUTHY and
PRESENCE dimensions) through ``MatchStrategy.OUTCOME`` against a context outcome supplied
under ``outcome_field(column)``, and handles every other dimension itself.  Every matching
entry is returned with its specificity; no rank filtering happens here.

Where things execute.  A list of protocol objects becomes a small host-originated (in-process
Polars) relation, and its outcome columns are computed there.  Every join between contexts and
entries states ``execute_on="left"`` with the entries/rules on the left, so the join runs on the
entries' own backend and Mountainash copies the small context relation to it (a temporary
table); the entries are never transferred to the contexts' backend.  The engine's cross join and
this module's no-engine path (nothing selected, or nothing left to score) follow that pattern.

Contexts are a conforming :class:`Relation` (a ``request_id`` column plus a column per
context field) or a list of protocol objects (``request_id`` plus attributes named after
context fields, as pydantic models).  :func:`contexts_to_relation` converts the list form,
keeping an unset field distinct from an explicit ``None`` through ``model_fields_set``.

Backend execution counts the application issues are logged at the pattern-evaluation and
matching stage boundaries on this module's logger; they are observability, not part of the
result.
"""

from __future__ import annotations

import logging
import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import Relation, relation

from mountainash_rules.core.compiler import truthy_outcome
from mountainash_rules.core.constants import HitPolicy, MatchStrategy
from mountainash_rules.core.context import bind_context_columns
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.decision_table.helpers import select_scopes
from mountainash_rules.decision_table.table import OUTCOME_STRATEGIES, DecisionTable, outcome_field

logger = logging.getLogger(__name__)

REQUEST_ID = "request_id"
"""The context column echoed as ``matches.request_id``."""

PRESENCE_PREFIX = "__presence__"
"""Prefix of the companion column carrying a converted list's per-row presence."""

IDENTITY_COLUMNS = ("scope", "entry_id", "entry_sources_id")
_BOUND_STRATEGIES = frozenset(
    {
        MatchStrategy.TRUTHY,
        MatchStrategy.PREFIX,
        MatchStrategy.SUFFIX,
        MatchStrategy.CONTAINS,
        MatchStrategy.REGEX,
        MatchStrategy.CONTEXT_REGEX,
    }
)
"""Outcome strategies read through the engine's context binding (typed value, null origin)."""


def presence_column(field: str) -> str:
    """The companion column holding ``field``'s presence for each row of a converted list."""
    return f"{PRESENCE_PREFIX}{field}"


def contexts_to_relation(contexts: t.Sequence[t.Any], fields: t.Iterable[str]) -> Relation:
    """Convert protocol objects to a context relation, preserving unset versus explicit null.

    Each object supplies ``request_id`` and, for every declared context field in its
    ``model_fields_set``, that attribute.  The relation has ``request_id``; for each field set
    on at least one object, a column with the value (null where unset or ``None``); and a
    ``presence_column(field)`` int8 ternary: unset 0, explicit ``None`` 1, value -1.  A field
    set on no object has neither column, which reads as unset (0) everywhere.
    """
    objects = list(contexts)
    if not objects:  # no rows to infer a type from; the empty request ids are text
        return relation({REQUEST_ID: []}).with_columns(ma.col(REQUEST_ID).cast(str))
    supplied = [obj.model_fields_set for obj in objects]
    data: dict[str, list] = {REQUEST_ID: [obj.request_id for obj in objects]}
    for field in fields:
        if not any(field in names for names in supplied):
            continue
        values = [getattr(obj, field) if field in names else None for obj, names in zip(objects, supplied)]
        data[field] = values
        data[presence_column(field)] = [
            0 if field not in names else (1 if value is None else -1)
            for value, names in zip(values, supplied)
        ]
    return relation(data)


@dataclass(frozen=True)
class MatchResult:
    """Every matching entry of every request.

    ``matches`` has ``request_id``, ``scope``, ``entry_id``, ``entry_sources_id``,
    ``specificity``, the entry's condition columns and its logical outputs.
    """

    matches: Relation


class _Executions:
    """Backend executions the application issues, logged per stage."""

    def __init__(self) -> None:
        self.total = 0
        self.reported = 0

    def boundary(self, stage: str, detail: str = "") -> None:
        since, self.reported = self.total - self.reported, self.total
        logger.info(
            "decision table match stage %s: %d backend executions (%d in total)%s",
            stage,
            since,
            self.total,
            f"; {detail}" if detail else "",
            extra={"match_stage": stage, "executions": since, "executions_total": self.total},
        )


def _is_rules_error(error: Exception) -> bool:
    return type(error).__module__.split(".")[0] == "mountainash_rules"


class MatchOperation:
    """A deferred match; the table and the contexts are retained until :meth:`fetch` completes."""

    def __init__(
        self,
        table: DecisionTable,
        contexts: Relation | t.Sequence[t.Any],
        scopes: t.Sequence[str] | None = None,
    ) -> None:
        if not isinstance(contexts, (Relation, list, tuple)):
            raise DecisionTableError("contexts must be a Relation or a list of context objects")
        self._table = table
        self._contexts = contexts
        self._scopes = scopes

    def fetch(self) -> MatchResult:
        """Run the match: scope filter, pattern filter, context outcomes, one engine call.

        Performs no validation: the caller must have validated the contexts first, in particular
        that ``request_id`` is non-null and unique (``validate_contexts``).  A null ``request_id``
        is dropped from the result by the engine's per-request ranking, and duplicates are
        matched as given.
        """
        table = self._table
        executions = _Executions()
        scopes = select_scopes(table.source_metadata, self._scopes)
        selected = self._selected_entries(scopes)
        try:
            matches = self._match(table, scopes, selected, executions)
        except Exception as error:
            if _is_rules_error(error):
                raise
            raise DecisionTableError(f"decision table matching failed: {error}") from error
        return MatchResult(matches)

    def _selected_entries(self, scopes: tuple[str, ...]) -> Relation:
        entries = self._table.entries
        if self._scopes is None:
            return entries
        condition = ma.col("scope").is_in(list(scopes)) if scopes else ma.lit(False)
        return entries.filter(condition)

    def _match(
        self,
        table: DecisionTable,
        scopes: tuple[str, ...],
        selected: Relation,
        executions: _Executions,
    ) -> Relation:
        contexts = self._contexts
        if not isinstance(contexts, Relation):
            contexts = contexts_to_relation(contexts, (field.name for field in table.source_metadata.fields))
        columns = _result_columns(table)
        if scopes:
            active, outcomes = _active_dimensions(table, selected, executions)
        else:  # no entries selected: nothing to read pattern requirements from or to score
            active, outcomes = [], {}
        executions.boundary("pattern", f"{len(outcomes)} outcome columns")
        if not active:  # nothing to score: every selected entry is possible for every request
            return _materialize(_without_engine(contexts, selected, columns), executions)
        metadata = table.entry_metadata
        prepared = _prepared_contexts(contexts, table.source_metadata, metadata, active, outcomes)

        from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

        engine = ExpressionRulesEngine(rules=selected, dimension_metadata=metadata)
        batch = engine.evaluate_batch(
            prepared,
            context_id_field=REQUEST_ID,
            dimensions=active,
            hit_policy=HitPolicy.COLLECT,
            include_observability=False,
        )
        renamed = {REQUEST_ID: "__context_id", "specificity": "__specificity"}
        scored = relation(batch.survivors).select(
            *(ma.col(renamed.get(name, name)).alias(name) for name in columns)
        )
        return _materialize(scored, executions)


def _result_columns(table: DecisionTable) -> list[str]:
    """``request_id``, identity, ``specificity``, the entry's condition columns, its outputs."""
    outputs = list(dict.fromkeys(output.output_name for output in table.source_metadata.outputs))
    reserved = {*IDENTITY_COLUMNS, *outputs}
    conditions = [name for name in table.entries.columns if name not in reserved]
    return [REQUEST_ID, *IDENTITY_COLUMNS, "specificity", *conditions, *outputs]


def _materialize(matches: Relation, executions: _Executions) -> Relation:
    """Execute the matches once; the result relation is the only thing that leaves the backend."""
    ordered = matches.sort(
        REQUEST_ID, "specificity", "scope", "entry_id", descending=[False, True, False, False]
    )
    result = ordered.snapshot()
    executions.total += 1
    executions.boundary("matching")
    return result


def _without_engine(contexts: Relation, selected: Relation, columns: list[str]) -> Relation:
    """Every selected entry for every request at specificity 0, for an empty or unscored selection.

    The join runs where the entries live (``execute_on="left"``); the request ids are copied to
    it, which executes, so the join is collected here as the engine path collects its result.
    """
    ids = contexts.select(REQUEST_ID)
    crossed = selected.cross_join(ids, execute_on="left").with_columns(ma.lit(0).alias("specificity"))
    return relation(crossed.select(*columns).collect())


@dataclass(frozen=True)
class _OutcomeSource:
    """What supplies one int8 outcome column: a context-side dimension and, for patterns, its text."""

    dimension: Dimension
    pattern: str | None = None


def _active_dimensions(
    table: DecisionTable,
    selected: Relation,
    executions: _Executions,
) -> tuple[list[str], dict[str, _OutcomeSource]]:
    """The engine's dimensions and the source of every outcome column among them.

    A derived pattern column no selected entry requires (all zero) scores 0 on every entry, so
    it is dropped from both the engine's dimensions and the outcome computation.
    """
    metadata = table.entry_metadata
    sources = {
        dimension.dimension_name: _OutcomeSource(dimension)
        for dimension in table.source_metadata.dimensions
        if dimension.match_strategy in OUTCOME_STRATEGIES
    }
    derived = [
        dimension.dimension_name
        for dimension in metadata.dimensions
        if dimension.match_strategy is MatchStrategy.OUTCOME and dimension.dimension_name not in sources
    ]
    unreferenced: set[str] = set()
    if derived:
        required = (
            selected.group_by()
            .agg(*(ma.col(name).ne(ma.lit(0)).cast(int).max().alias(name) for name in derived))
            .to_dicts()
        )
        executions.total += 1
        referenced = {name for row in required for name, flag in row.items() if flag}
        unreferenced = set(derived) - referenced
        if referenced:
            conditions = table.pattern_conditions.to_dicts()
            executions.total += 1
            for row in conditions:
                if row["column"] in referenced:
                    sources[row["column"]] = _OutcomeSource(_pattern_dimension(row), row["pattern"])
    active = [d.dimension_name for d in metadata.dimensions if d.dimension_name not in unreferenced]
    outcomes = {
        d.dimension_name: sources[d.dimension_name]
        for d in metadata.dimensions
        if d.match_strategy is MatchStrategy.OUTCOME and d.dimension_name not in unreferenced
    }
    return active, outcomes


def _pattern_dimension(row: dict) -> Dimension:
    """The context-side dimension of one ``pattern_conditions`` row."""
    strategy = MatchStrategy(row["strategy"])
    if strategy is MatchStrategy.CONTEXT_REGEX:
        return Dimension(
            dimension_name=row["column"],
            context_field=row["context_field"],
            match_strategy=strategy,
            regex_pattern=row["pattern"],
        )
    return Dimension(
        dimension_name=row["column"],
        context_field=row["context_field"],
        match_strategy=strategy,
        rule_field=row["column"],
    )


def _prepared_contexts(
    contexts: Relation,
    source: DimensionsMetadata,
    metadata: DimensionsMetadata,
    active: list[str],
    outcomes: dict[str, _OutcomeSource],
) -> Relation:
    """The engine's contexts: ``request_id``, the value-dimension fields, one outcome per column."""
    present = set(contexts.columns)
    by_name = {dimension.dimension_name: dimension for dimension in metadata.dimensions}
    value_fields = dict.fromkeys(
        by_name[name].context_field
        for name in active
        if by_name[name].match_strategy is not MatchStrategy.OUTCOME
    )
    keep = [REQUEST_ID, *(field for field in value_fields if field in present)]
    to_bind = [
        source.dimension
        for source in outcomes.values()
        if source.dimension.context_field in present
        and source.dimension.match_strategy in _BOUND_STRATEGIES
    ]
    bound = contexts
    if to_bind:
        # Binding types the context value, drops the declared wildcard and notes null origin.
        resolved = DimensionsMetadata(fields=source.fields, dimensions=tuple(to_bind))
        bound = bind_context_columns(contexts, list(resolved.dimensions))
    names = [outcome_field(name) for name in outcomes]
    expressions = [
        _outcome_expression(source, present).cast(int).alias(outcome_field(name))
        for name, source in outcomes.items()
    ]
    return bound.with_columns(*expressions).select(*keep, *names)


def _outcome_expression(source: _OutcomeSource, present: set[str]) -> BaseExpressionAPI:
    """The ternary a context scores on one int8 outcome column: 1 match, -1 no match, 0 unknown."""
    dimension = source.dimension
    field = dimension.context_field
    if field not in present:
        return ma.lit(0)  # an absent column is unknown everywhere
    strategy = dimension.match_strategy
    name = dimension.dimension_name
    companion = presence_column(field)
    if strategy is MatchStrategy.OUTCOME:
        return ma.col(field).fill_null(0)
    if strategy is MatchStrategy.PRESENCE:
        if companion in present:
            return ma.col(companion)
        return ma.when(ma.col(field).is_null()).then(1).otherwise(-1)
    if strategy is MatchStrategy.TRUTHY:
        mapped = truthy_outcome(dimension, ma.col(f"__ctx_value_{name}"), context=True)
        if companion in present:
            return ma.when(ma.col(companion).eq(ma.lit(0))).then(0).otherwise(mapped)
        return mapped
    value = ma.col(f"__ctx_value_{name}")
    pattern = source.pattern
    match strategy:
        case MatchStrategy.PREFIX:
            holds = value.str.starts_with(pattern)
        case MatchStrategy.SUFFIX:
            holds = value.str.ends_with(pattern)
        case MatchStrategy.CONTAINS:
            holds = value.str.contains(pattern)
        case _:  # REGEX, CONTEXT_REGEX
            holds = value.str.regex_contains(pattern)
    return ma.when(~ma.col(f"__ctx_present_{name}")).then(0).when(holds).then(1).otherwise(-1)


__all__: t.Sequence[str] = (
    "MatchOperation",
    "MatchResult",
    "contexts_to_relation",
    "presence_column",
)
