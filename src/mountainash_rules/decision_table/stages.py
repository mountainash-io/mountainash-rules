"""Relational construction stages of a DecisionTable.

Stages (each is a function over Mountainash relations, backend independent):

a. ``stage_a_boundaries`` binds the validated rules into one *column plan* per
   table column group and produces, per plan, the rules' regions, the ordered
   cut values or each scope's named values, and the derived pattern conditions.
b. ``stage_b_pieces`` turns cut values into closed pieces and named values into
   pieces per field and scope.
c. ``stage_c_coverage`` computes each rule's coverage per column, and the
   covered ``(scope, source_uuid, cell columns)`` rows that stages d-f
   (contributor words, folds, emitted entries) consume.
d. ``stage_d_membership`` turns the covered rows into each cell's contributor
   words (dynamic 63-bit int64 words, see ``masks``) and assigns
   ``entry_sources_id`` by sorted natural word tuple within each scope.
e. ``stage_e_entry_sources`` decodes the words into
   ``entry_sources(scope, entry_sources_id, source_uuid)`` and folds each
   declared output once per contributing rule per contributor set.

Natural keys are scope, plan, sorted cut/value and rule UUID, never row order;
``stage_key`` names them.

Column plans (one table column group each):

* positive column of a field: ``<field>__lo``/``<field>__hi`` for ordered fields,
  ``<field>`` for unordered fields; EXACT/SET_MEMBERSHIP/RANGE/MIN/MAX dimensions
  sharing the field are conjoined;
* negation column per NOT_EQUAL/SET_EXCLUSION dimension: its authored value (or
  list) or the field wildcard;
* int8 required-outcome column per TRUTHY, PRESENCE or OUTCOME dimension, per
  CONTEXT_REGEX dimension, and per distinct pattern of a REGEX/PREFIX/SUFFIX/
  CONTAINS dimension (``<dimension>__pattern_<k>``, ``k`` the pattern's sorted
  ordinal).
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash.relations import Relation, concat

from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import Dimension
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.decision_table.fields import (
    BOUND_SCOPE,
    BOUND_UUID,
    NEGATION_STRATEGIES,
    PATTERN_STRATEGIES,
    SCOPE,
    SOURCE_UUID,
    VALUE,
    WILDCARD,
    Field,
    OrderedField,
    Outcome,
    TruthyField,
    Unsatisfiable,
    is_expression,
)
from mountainash_rules.decision_table.folds import fold_contributions
from mountainash_rules.decision_table.helpers import check_sources
from mountainash_rules.decision_table.masks import (
    ENTRY_SOURCES_ID,
    decode_entry_sources,
    entry_sources_ids,
    membership_words,
    rule_ordinals,
    word_column,
    word_count,
)
from mountainash_rules.decision_table.prevalidate import ValidatedRules
from mountainash_rules.decision_table.strategies import strategy_for

POSITIVE_STRATEGIES = frozenset(
    {
        MatchStrategy.EXACT,
        MatchStrategy.SET_MEMBERSHIP,
        MatchStrategy.RANGE,
        MatchStrategy.MIN,
        MatchStrategy.MAX,
    }
)
RESERVED_COLUMNS = frozenset({SCOPE, SOURCE_UUID})
PATTERN_CONDITION_COLUMNS = ("column", "context_field", "strategy", "pattern")


@dataclass(frozen=True)
class ColumnPlan:
    """One table column group derived from one or more source dimensions."""

    name: str
    kind: t.Literal["interval", "value"]
    role: t.Literal["positive", "negation", "outcome"]
    family: Field
    dimensions: tuple[Dimension, ...]
    columns: tuple[str, ...]

    def entry_dimension(self) -> Dimension:
        """The engine dimension that reads this plan's table columns."""
        if self.role == "negation":
            is_set = self.dimensions[0].match_strategy is MatchStrategy.SET_EXCLUSION
            return self.family.entry_dimension(self.name, negated=True, is_set=is_set)
        return self.family.entry_dimension(self.name)


@dataclass(frozen=True)
class Boundaries:
    """Stage a: regions per plan, ordered cuts, named values, pattern conditions."""

    plans: tuple[ColumnPlan, ...]
    sources: Relation
    regions: dict[str, Relation]
    cuts: dict[str, Relation]
    named_values: dict[str, Relation]
    pattern_conditions: Relation


@dataclass(frozen=True)
class Pieces:
    """Stage b: ordered pieces ``(scope, lo, hi)`` and value pieces ``(scope, value, wildcard)``."""

    by_plan: dict[str, Relation]


@dataclass(frozen=True)
class Coverage:
    """Stage c: per-plan coverage and the covered cell rows of every rule."""

    by_plan: dict[str, Relation]
    cell_coverage: Relation
    cell_columns: tuple[str, ...]


@dataclass(frozen=True)
class Membership:
    """Stage d: contributor words per covered cell and the ids of their word tuples.

    ``cell_words`` is ``(scope, cell columns..., word columns...)`` with one row per covered
    cell; ``word_ids`` is ``(scope, entry_sources_id, word columns...)`` with one row per
    distinct word tuple in a scope; ``cells`` is ``(scope, cell columns..., entry_sources_id)``;
    ``ordinals`` is ``(scope, source_uuid, __word_index, __word_bit)`` for the contributors.
    """

    ordinals: Relation
    words: int
    word_columns: tuple[str, ...]
    cell_columns: tuple[str, ...]
    cell_words: Relation
    word_ids: Relation
    cells: Relation


@dataclass(frozen=True)
class EntrySourcesAndFolds:
    """Stage e: ``entry_sources`` and one folded output row per scope and contributor set.

    ``outputs`` is ``(scope, entry_sources_id, output names...)``: a scope without an output
    declares null in that output's column.
    """

    entry_sources: Relation
    outputs: Relation
    output_names: tuple[str, ...]


def stage_key(stage: str, plan: ColumnPlan) -> tuple[str, ...]:
    """Natural-key columns of ``plan``'s relation at ``stage`` (a, b or c)."""
    if stage == "a":
        return (SCOPE, "__cut") if plan.kind == "interval" else (SCOPE, VALUE, WILDCARD)
    if stage == "b":
        return (SCOPE, plan.columns[0]) if plan.kind == "interval" else (SCOPE, VALUE, WILDCARD)
    if stage == "c":
        return (SCOPE, SOURCE_UUID, *plan.columns)
    raise DecisionTableError(f"No natural key is defined for stage {stage!r}")


# ---------------------------------------------------------------------------
# Stage a
# ---------------------------------------------------------------------------


def _bound_rules(validated: ValidatedRules) -> Relation:
    metadata = validated.source_metadata
    rules = check_sources(validated.rules, metadata)
    scope = ma.lit("default") if metadata.scope_column is None else ma.col(metadata.scope_column)
    return rules.with_columns(
        scope.alias(BOUND_SCOPE), ma.col(metadata.uuid_column).alias(BOUND_UUID)
    )


def _dimension_region(bound: Relation, dimension: Dimension):
    strategy = strategy_for(dimension.match_strategy)
    family = strategy.family(dimension)
    region = family.interpret(strategy.lower(family, strategy.cells(family, bound, dimension), dimension))
    if isinstance(region, Unsatisfiable):
        raise DecisionTableError(f"{dimension.dimension_name!r}: {region.reason}")
    return family, region


def _pattern_ordinals(bound: Relation, pattern: str, wildcard: t.Any, ordinal: str) -> Relation:
    """Distinct non-wildcard patterns with their sorted ordinal (a backend-neutral rank from 0)."""
    patterns = bound.filter(~wildcard).select(ma.col(pattern)).unique()
    ranked = patterns.with_columns(ma.col(pattern).row_number().over(order_by=pattern).alias("__rn"))
    first = ranked.group_by().agg(ma.col("__rn").min().alias("__rn0"))
    return ranked.cross_join(first).select(
        ma.col(pattern), (ma.col("__rn") - ma.col("__rn0")).cast(int).alias(ordinal)
    )


def _empty_conditions(bound: Relation) -> Relation:
    return bound.filter(ma.lit(False)).select(
        *(ma.lit("").alias(name) for name in PATTERN_CONDITION_COLUMNS)
    )


def _validate_columns(plans: t.Sequence[ColumnPlan]) -> None:
    seen: set[str] = set(RESERVED_COLUMNS)
    for plan in plans:
        for column in plan.columns:
            if column in seen:
                raise DecisionTableError(f"Table column {column!r} is declared twice or reserved")
            seen.add(column)


def stage_a_boundaries(validated: ValidatedRules) -> Boundaries:
    """Stage a: ordered cut values and each scope's named values, per column plan."""
    metadata = validated.source_metadata
    bound = _bound_rules(validated)

    # Group positive dimensions by field, preserving first appearance in the metadata.
    layout: list[tuple[str, t.Any]] = []
    positive: dict[str, list[tuple[int, Dimension]]] = {}
    for index, dimension in enumerate(metadata.dimensions):
        if dimension.match_strategy in POSITIVE_STRATEGIES:
            if dimension.context_field not in positive:
                positive[dimension.context_field] = []
                layout.append(("positive", dimension.context_field))
            positive[dimension.context_field].append((index, dimension))
        else:
            layout.append(("dimension", (index, dimension)))

    plans: list[ColumnPlan] = []
    regions: dict[str, Relation] = {}
    conditions: list[Relation] = []

    def add(plan: ColumnPlan, relations: t.Sequence[Relation]) -> None:
        plans.append(plan)
        regions[plan.name] = relations[0] if len(relations) == 1 else concat(list(relations))

    for kind, payload in layout:
        if kind == "positive":
            dims = positive[payload]
            parts = []
            family = None
            for index, dimension in dims:
                family, region = _dimension_region(bound, dimension)
                parts.append(family.regions(bound, region, index))
            ordered = isinstance(family, OrderedField)
            columns = family.entry_columns(payload)
            add(
                ColumnPlan(payload, "interval" if ordered else "value", "positive", family,
                           tuple(d for _, d in dims), tuple(columns)),
                parts,
            )
            continue

        index, dimension = payload
        family, region = _dimension_region(bound, dimension)
        if dimension.match_strategy in NEGATION_STRATEGIES:
            add(
                ColumnPlan(dimension.dimension_name, "value", "negation", family, (dimension,),
                           (dimension.dimension_name,)),
                [family.regions(bound, region, index)],
            )
        elif dimension.match_strategy in PATTERN_STRATEGIES:
            condition = region.condition
            if not is_expression(condition.pattern):
                name = dimension.dimension_name
                add(
                    ColumnPlan(name, "value", "outcome", family, (dimension,), (name,)),
                    [family.regions(bound, Outcome(1), index)],
                )
                conditions.append(
                    bound.limit(1).select(
                        ma.lit(name).alias("column"),
                        ma.lit(dimension.context_field).alias("context_field"),
                        ma.lit(dimension.match_strategy.value).alias("strategy"),
                        ma.lit(condition.pattern).alias("pattern"),
                    )
                )
                continue
            pattern, ordinal = f"__pat_{index}", f"__ord_{index}"
            bound = bound.with_columns(condition.pattern.alias(pattern))
            ordinals = _pattern_ordinals(bound, pattern, condition.wildcard, ordinal)
            count = ordinals.count_rows()
            bound = bound.join(ordinals, on=pattern, how="left")
            prefix = f"{dimension.dimension_name}__pattern_"
            for k in range(count):
                required = ma.when(ma.col(ordinal).eq(k)).then(1).otherwise(0)
                name = f"{prefix}{k}"
                add(
                    ColumnPlan(name, "value", "outcome", family, (dimension,), (name,)),
                    [family.regions(bound, Outcome(required), index)],
                )
            conditions.append(
                ordinals.select(
                    (ma.lit(prefix) + ma.col(ordinal).cast(str)).alias("column"),
                    ma.lit(dimension.context_field).alias("context_field"),
                    ma.lit(dimension.match_strategy.value).alias("strategy"),
                    ma.col(pattern).alias("pattern"),
                )
            )
        else:  # TRUTHY, PRESENCE, OUTCOME
            add(
                ColumnPlan(dimension.dimension_name, "value", "outcome", family, (dimension,),
                           (dimension.dimension_name,)),
                [family.regions(bound, region, index)],
            )

    _validate_columns(plans)
    cuts: dict[str, Relation] = {}
    named: dict[str, Relation] = {}
    for plan in plans:
        if plan.kind == "interval":
            cuts[plan.name] = plan.family.cuts(regions[plan.name])
        else:
            named[plan.name] = plan.family.value_pieces(regions[plan.name])
    return Boundaries(
        plans=tuple(plans),
        sources=bound.select(ma.col(BOUND_SCOPE).alias(SCOPE), ma.col(BOUND_UUID).alias(SOURCE_UUID)),
        regions=regions,
        cuts=cuts,
        named_values=named,
        pattern_conditions=concat(conditions) if conditions else _empty_conditions(bound),
    )


def classify_named_values(
    named_values: Relation, conditions: Relation, context_field: str
) -> Relation:
    """Classify a field's named values against its pattern conditions.

    Uses the one pattern operation that scores context values (``TruthyField.classify``):
    ``(scope, value, column, outcome)`` with outcome 1 on a match, -1 otherwise, and 0 for the
    wildcard piece (unknown).  ``named_values`` is a stage-a ``(scope, value, wildcard)``
    relation; ``conditions`` is ``pattern_conditions``.
    """
    field_conditions = conditions.filter(ma.col("context_field").eq(context_field)).select(
        "column", "strategy", "pattern"
    )
    outcome = (
        ma.when(ma.col(WILDCARD))
        .then(0)
        .otherwise(TruthyField.classify(ma.col("strategy"), ma.col(VALUE), ma.col("pattern")))
    )
    return named_values.cross_join(field_conditions).select(
        SCOPE, VALUE, "column", outcome.cast("int8").alias("outcome")
    )


# ---------------------------------------------------------------------------
# Stage b
# ---------------------------------------------------------------------------


def stage_b_pieces(boundaries: Boundaries) -> Pieces:
    """Stage b: pieces per field and scope."""
    by_plan: dict[str, Relation] = {}
    for plan in boundaries.plans:
        if plan.kind == "interval":
            by_plan[plan.name] = plan.family.pieces(boundaries.cuts[plan.name], plan.columns)
        else:
            by_plan[plan.name] = boundaries.named_values[plan.name]
    return Pieces(by_plan)


# ---------------------------------------------------------------------------
# Stage c
# ---------------------------------------------------------------------------


def stage_c_coverage(boundaries: Boundaries, pieces: Pieces) -> Coverage:
    """Stage c: each rule's coverage per column and its covered cell rows."""
    by_plan: dict[str, Relation] = {}
    for plan in boundaries.plans:
        regions = boundaries.regions[plan.name]
        piece = pieces.by_plan[plan.name]
        dimensions = len(plan.dimensions)
        if plan.kind == "interval":
            by_plan[plan.name] = plan.family.interval_coverage(regions, piece, plan.columns, dimensions)
        else:
            by_plan[plan.name] = plan.family.value_coverage(regions, piece, dimensions).select(
                SCOPE, SOURCE_UUID, ma.col(VALUE).alias(plan.columns[0])
            )

    cell_columns = tuple(column for plan in boundaries.plans for column in plan.columns)
    if by_plan:
        coverages = [by_plan[plan.name] for plan in boundaries.plans]
        cells = coverages[0]
        for coverage in coverages[1:]:
            cells = cells.join(coverage, on=[SCOPE, SOURCE_UUID], how="inner")
    else:
        cells = boundaries.sources
    return Coverage(by_plan=by_plan, cell_coverage=cells, cell_columns=cell_columns)


# ---------------------------------------------------------------------------
# Stage d
# ---------------------------------------------------------------------------


def stage_d_membership(coverage: Coverage) -> Membership:
    """Stage d: contributor words per covered cell, ``entry_sources_id`` per distinct tuple.

    A rule contributes once per covered cell however many derivations reach the cell, so the
    covered rows are made distinct on ``(scope, cell columns, source_uuid)`` first.
    """
    cell_columns = coverage.cell_columns
    keys = (SCOPE, *cell_columns)
    covered = coverage.cell_coverage.select(*keys, SOURCE_UUID).unique()
    ordinals = rule_ordinals(covered.select(SCOPE, SOURCE_UUID).unique())
    words = word_count(ordinals)
    cell_words = membership_words(covered, ordinals, words, keys)
    word_ids, cells = entry_sources_ids(cell_words, keys, words)
    return Membership(
        ordinals=ordinals,
        words=words,
        word_columns=tuple(word_column(index) for index in range(words)),
        cell_columns=cell_columns,
        cell_words=cell_words,
        word_ids=word_ids,
        cells=cells,
    )


# ---------------------------------------------------------------------------
# Stage e
# ---------------------------------------------------------------------------


def stage_e_entry_sources(
    validated: ValidatedRules, membership: Membership
) -> EntrySourcesAndFolds:
    """Stage e: ``entry_sources`` from the words' bits, and each output folded per contributor set.

    Every contributor set holds each source rule once, so a rule contributes once to every
    cell sharing the set; no output exists for a cell no rule covers.
    """
    entry_sources = decode_entry_sources(membership.word_ids, membership.ordinals, membership.words)
    bound = _bound_rules(validated)
    folds: dict[str, list[Relation]] = {}
    for output in validated.source_metadata.outputs:
        scope = output.scope or "default"
        contributions = bound.select(
            ma.col(BOUND_SCOPE).alias(SCOPE),
            ma.col(BOUND_UUID).alias(SOURCE_UUID),
            ma.col(output.contribution_column).alias("a0001"),
        )
        contributors = entry_sources.filter(ma.col(SCOPE).eq(scope)).join(
            contributions, on=[SCOPE, SOURCE_UUID], how="inner"
        )
        folds.setdefault(output.output_name, []).append(
            fold_contributions(contributors, [output], [SCOPE, ENTRY_SOURCES_ID])
        )
    outputs = membership.word_ids.select(SCOPE, ENTRY_SOURCES_ID)
    for parts in folds.values():
        outputs = outputs.join(
            parts[0] if len(parts) == 1 else concat(parts),
            on=[SCOPE, ENTRY_SOURCES_ID],
            how="left",
        )
    return EntrySourcesAndFolds(entry_sources, outputs, tuple(folds))
