"""Field families and the shared region vocabulary for DecisionTable construction.

A *region* is the semantic description of what one rule cell covers on one
field: ``point``, ``interval``, ``everything``, ``matches`` or ``outcome`` (plus
``members`` for a union of points and ``negation`` for the negated forms that
live in their own table column).  Strategies (``strategies.py``) lower a rule
cell into a region; a *field family* interprets that region:

* ``OrderedField`` (INT, DECIMAL, DATE, DATETIME) cuts the field into closed
  intervals; equality is a one-point interval.
* ``UnorderedField`` (STR, BOOL) uses the values the scope's rules name plus one
  wildcard piece.
* ``TruthyField`` (patterns, TRUTHY, PRESENCE, OUTCOME) holds an int8 required
  ternary outcome per condition.

Region operands are either Python values (semantic tests) or Mountainash
expressions over the rule relation (construction); families never inspect the
values beyond Python-level validity checks, so both use one code path.
"""

from __future__ import annotations

import abc
import datetime as dt
import typing as t
from dataclasses import dataclass
from decimal import Decimal

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import Relation, concat

from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension
from mountainash_rules.core.numeric import canonicalize_set_expr, exact_literal, typed_marker_predicate
from mountainash_rules.core.set_wildcard import set_state
from mountainash_rules.decision_table.errors import DecisionTableError

SCOPE = "scope"
BOUND_SCOPE = "__dt_scope"
BOUND_UUID = "__dt_uuid"
SOURCE_UUID = "source_uuid"
DIMENSION_TAG = "__dim"
REGION_LOW = "__r_lo"
REGION_HIGH = "__r_hi"
CUT = "__cut"
VALUE = "value"
WILDCARD = "wildcard"

ORDERED_TYPES = frozenset({DataType.INT, DataType.DECIMAL, DataType.DATE, DataType.DATETIME})
PATTERN_STRATEGIES = frozenset(
    {
        MatchStrategy.REGEX,
        MatchStrategy.PREFIX,
        MatchStrategy.SUFFIX,
        MatchStrategy.CONTAINS,
        MatchStrategy.CONTEXT_REGEX,
    }
)
TRUTHY_STRATEGIES = PATTERN_STRATEGIES | {
    MatchStrategy.TRUTHY,
    MatchStrategy.PRESENCE,
    MatchStrategy.OUTCOME,
}
NEGATION_STRATEGIES = frozenset({MatchStrategy.NOT_EQUAL, MatchStrategy.SET_EXCLUSION})

INT_MINIMUM = -(2**31)
INT_MAXIMUM = 2**31 - 1


# ---------------------------------------------------------------------------
# Region vocabulary
# ---------------------------------------------------------------------------


class Region:
    """Base class of the semantic region vocabulary."""


@dataclass(frozen=True)
class Point(Region):
    """One value: an ordered one-point interval or an unordered named value."""

    value: t.Any
    wildcard: t.Any = False


@dataclass(frozen=True)
class Interval(Region):
    """Closed interval; an endpoint of ``None`` is unbounded (the type bound)."""

    low: t.Any = None
    high: t.Any = None
    low_wildcard: t.Any = False
    high_wildcard: t.Any = False


@dataclass(frozen=True)
class Members(Region):
    """Union of point matches over a set of values."""

    values: t.Any
    wildcard: t.Any = False


@dataclass(frozen=True)
class Everything(Region):
    """No constraint: every piece, or ternary unknown for a truthy condition."""


@dataclass(frozen=True)
class Matches(Region):
    """A pattern condition: outcome 1 on a match, -1 otherwise, 0 when unknown."""

    pattern: t.Any
    strategy: MatchStrategy | None = None
    wildcard: t.Any = False


@dataclass(frozen=True)
class Outcome(Region):
    """A required ternary outcome.  ``condition`` records the pattern it derives from."""

    value: t.Any
    wildcard: t.Any = False
    condition: Matches | None = None


@dataclass(frozen=True)
class Negation(Region):
    """Negated match, stored in its own table column carrying the authored value."""

    region: Region


@dataclass(frozen=True)
class Unsatisfiable(Region):
    """Structurally empty coverage or conditions."""

    reason: str
    kind: t.ClassVar[str] = "unsatisfiable_rule"


def point(value: t.Any, *, wildcard: t.Any = False) -> Point:
    return Point(value, wildcard)


def interval(
    low: t.Any = None,
    high: t.Any = None,
    *,
    low_wildcard: t.Any = False,
    high_wildcard: t.Any = False,
) -> Interval:
    return Interval(low, high, low_wildcard, high_wildcard)


def members(values: t.Any, *, wildcard: t.Any = False) -> Members:
    return Members(values, wildcard)


def everything() -> Everything:
    return Everything()


def matches(
    pattern: t.Any,
    strategy: MatchStrategy | None = None,
    *,
    wildcard: t.Any = False,
) -> Matches:
    return Matches(pattern, strategy, wildcard)


def outcome(value: t.Any, *, wildcard: t.Any = False) -> Outcome:
    return Outcome(value, wildcard)


def negation(region: Region) -> Negation:
    return Negation(region)


def unsatisfiable(reason: str) -> Unsatisfiable:
    """The named interface prevalidation and construction share for empty coverage."""
    return Unsatisfiable(reason)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_expression(value: t.Any) -> bool:
    return isinstance(value, BaseExpressionAPI)


def _flag(value: t.Any) -> BaseExpressionAPI:
    """A wildcard operand (bool or expression) as a boolean expression."""
    return value if is_expression(value) else ma.lit(bool(value))


def _definitely(value: t.Any, truth: bool) -> bool:
    """True when a wildcard operand is the Python bool ``truth``."""
    return isinstance(value, bool) and value is truth


def _static(*values: t.Any) -> bool:
    return not any(is_expression(value) for value in values)


def _union(parts: t.Sequence[Relation]) -> Relation:
    return parts[0] if len(parts) == 1 else concat(list(parts))


def _split_entries(
    entries: t.Sequence[tuple[int, Dimension, Region]], positive: tuple[type, ...]
) -> tuple[list[tuple[int, Region]], list[Region]]:
    """Positive ``(index, region)`` pairs and the regions negated in their own columns."""
    included: list[tuple[int, Region]] = []
    negated: list[Region] = []
    for index, _, region in entries:
        if isinstance(region, Negation):
            negated.append(region.region)
        elif isinstance(region, positive):
            included.append((index, region))
    return included, negated


def _excluded_values(bound: Relation, inner: Region, column: str) -> Relation | None:
    """``(scope, source_uuid, column)`` values a negated region excludes (wildcards exclude none)."""
    scope, uuid = ma.col(BOUND_SCOPE).alias(SCOPE), ma.col(BOUND_UUID).alias(SOURCE_UUID)
    if isinstance(inner, Point):
        value = inner.value if is_expression(inner.value) else ma.lit(inner.value)
        return bound.filter(~_flag(inner.wildcard)).select(scope, uuid, value.alias(column))
    if isinstance(inner, Members):
        return (
            bound.filter(~_flag(inner.wildcard))
            .select(scope, uuid, inner.values.alias(column))
            .explode(column)
            .filter(ma.col(column).is_not_null())
        )
    return None


def required_outcome(region: Region) -> BaseExpressionAPI:
    """Required ternary outcome of an interpreted truthy region (0 when unconstrained)."""
    if isinstance(region, Outcome):
        value = region.value if is_expression(region.value) else ma.lit(region.value)
        return ma.when(_flag(region.wildcard)).then(0).otherwise(value)
    return ma.lit(0)


def concrete_flag(region: Region) -> BaseExpressionAPI:
    """True where an interpreted region constrains the field (is not the field's wildcard)."""
    if isinstance(region, Negation):
        return concrete_flag(region.region)
    if isinstance(region, Interval):
        low = ma.lit(False) if region.low is None else ~_flag(region.low_wildcard)
        high = ma.lit(False) if region.high is None else ~_flag(region.high_wildcard)
        return low | high
    if isinstance(region, (Point, Members, Matches, Outcome)):
        return ~_flag(region.wildcard)
    return ma.lit(False)


class Field(abc.ABC):
    """Shared behaviour of the three field families."""

    def __init__(self, context_field: ContextField):
        self.context_field = context_field

    @property
    def name(self) -> str:
        return self.context_field.name

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.context_field.name!r})"

    # -- vocabulary ------------------------------------------------------

    def point(self, value: t.Any, *, wildcard: t.Any = False) -> Point:
        return point(value, wildcard=wildcard)

    def interval(self, low: t.Any = None, high: t.Any = None, **kwargs: t.Any) -> Interval:
        return interval(low, high, **kwargs)

    def members(self, values: t.Any, *, wildcard: t.Any = False) -> Members:
        return members(values, wildcard=wildcard)

    def everything(self) -> Everything:
        return everything()

    def matches(
        self,
        pattern: t.Any,
        strategy: MatchStrategy | None = None,
        *,
        wildcard: t.Any = False,
    ) -> Matches:
        return matches(pattern, strategy, wildcard=wildcard)

    def outcome(self, value: t.Any, *, wildcard: t.Any = False) -> Outcome:
        return outcome(value, wildcard=wildcard)

    def unsatisfiable(self, reason: str) -> Unsatisfiable:
        return unsatisfiable(reason)

    def not_applicable(self, region: Region) -> DecisionTableError:
        return DecisionTableError(
            f"{type(region).__name__} is not applicable to {type(self).__name__}"
        )

    @abc.abstractmethod
    def interpret(self, region: Region) -> Region:
        """Interpret ``region`` for this family (resolving wildcards and bounds)."""

    @abc.abstractmethod
    def unsatisfiable_rules(
        self, bound: Relation, entries: t.Sequence[tuple[int, Dimension, Region]]
    ) -> Relation:
        """``(scope, source_uuid)`` of bound rules whose conditions on this field cannot all hold.

        ``entries`` are ``(dimension index, dimension, interpreted region)`` of one field; the
        coverage uses the same regions, cuts and pieces as construction.
        """

    def _rule_keys(self, rules: Relation) -> Relation:
        return rules.select(ma.col(BOUND_SCOPE).alias(SCOPE), ma.col(BOUND_UUID).alias(SOURCE_UUID))

    def _no_rules(self, bound: Relation) -> Relation:
        return self._rule_keys(bound.filter(ma.lit(False)))

    def _rules(self, rules: Relation) -> Relation:
        return self._rule_keys(rules).unique()

    def _empty_rules(self, bound: Relation, coverage: Relation) -> Relation:
        """Rules of ``bound`` absent from ``coverage`` ``(scope, source_uuid, ...)``."""
        return self._rule_keys(bound).unique().join(
            coverage.select(SCOPE, SOURCE_UUID).unique(), on=[SCOPE, SOURCE_UUID], how="anti"
        )

    # -- rule cells ------------------------------------------------------

    def quantise(self, value: BaseExpressionAPI) -> BaseExpressionAPI:
        """Canonical authored value (identity unless the family reduces values)."""
        return value

    def literal(self, value: t.Any) -> BaseExpressionAPI:
        return value if is_expression(value) else ma.lit(value)

    def wildcard_flag(self, raw: BaseExpressionAPI, source_dtype: t.Any) -> BaseExpressionAPI:
        markers = [self.context_field.wildcard] if self.context_field.wildcard_declared else []
        return typed_marker_predicate(raw, source_dtype, markers)

    def scalar_cell(self, raw: BaseExpressionAPI, source_dtype: t.Any) -> tuple[t.Any, t.Any]:
        """``(value, wildcard)`` for one scalar rule column."""
        return self.quantise(raw), self.wildcard_flag(raw, source_dtype)

    def set_cell(
        self, rel: Relation, dimension: Dimension, column: str
    ) -> tuple[t.Any, t.Any]:
        """``(canonical list, wildcard)`` for one list rule column."""
        wildcard, _malformed, payload = set_state(rel, dimension, column)
        return self.quantise_list(payload), wildcard

    def quantise_list(self, payload: BaseExpressionAPI) -> BaseExpressionAPI:
        return payload

    # -- discrete value plans (shared by every family) -------------------

    def value_regions(
        self, bound: Relation, region: Region, dimension_index: int
    ) -> Relation:
        """``(scope, source_uuid, __dim, value, wildcard)`` rows for one dimension."""
        tag = ma.lit(dimension_index).alias(DIMENSION_TAG)
        scope = ma.col(BOUND_SCOPE).alias(SCOPE)
        uuid = ma.col(BOUND_UUID).alias(SOURCE_UUID)
        if isinstance(region, Negation):
            inner = region.region
            if isinstance(inner, Members):
                return bound.select(
                    scope, uuid, tag, inner.values.alias(VALUE), _flag(inner.wildcard).alias(WILDCARD)
                )
            if isinstance(inner, Point):
                return bound.select(
                    scope, uuid, tag, self.literal(inner.value).alias(VALUE), _flag(inner.wildcard).alias(WILDCARD)
                )
            raise self.not_applicable(region)
        if isinstance(region, Point):
            return bound.select(
                scope, uuid, tag, self.literal(region.value).alias(VALUE), _flag(region.wildcard).alias(WILDCARD)
            )
        if isinstance(region, Everything):
            typed_null = ma.lit(None).cast(self.context_field.value_dtype)
            return bound.select(scope, uuid, tag, typed_null.alias(VALUE), ma.lit(True).alias(WILDCARD))
        if isinstance(region, Members):
            wildcard = _flag(region.wildcard)
            wild = bound.filter(wildcard).select(
                scope, uuid, tag, region.values.list.first().alias(VALUE), ma.lit(True).alias(WILDCARD)
            )
            named = (
                bound.filter(~wildcard)
                .select(scope, uuid, tag, region.values.alias(VALUE))
                .explode(VALUE)
                .filter(ma.col(VALUE).is_not_null())
                .with_columns(ma.lit(False).alias(WILDCARD))
            )
            return concat([wild, named])
        if isinstance(region, Outcome):
            value = ma.when(_flag(region.wildcard)).then(0).otherwise(self.literal(region.value)).cast("int8")
            return bound.select(scope, uuid, tag, value.alias(VALUE)).with_columns(
                ma.col(VALUE).eq(0).alias(WILDCARD)
            )
        raise self.not_applicable(region)

    def value_pieces(self, regions: Relation) -> Relation:
        """Each scope's named values plus its wildcard piece: ``(scope, value, wildcard)``."""
        return regions.select(SCOPE, VALUE, WILDCARD).unique()

    def value_coverage(self, regions: Relation, pieces: Relation, dimensions: int) -> Relation:
        """``(scope, source_uuid, value, wildcard)`` covered by every conjoined dimension."""
        named = regions.filter(~ma.col(WILDCARD)).select(SCOPE, SOURCE_UUID, DIMENSION_TAG, VALUE)
        by_value = named.join(pieces.filter(~ma.col(WILDCARD)), on=[SCOPE, VALUE], how="inner")
        wildcard_rows = regions.filter(ma.col(WILDCARD)).select(SCOPE, SOURCE_UUID, DIMENSION_TAG)
        by_wildcard = wildcard_rows.join(pieces, on=SCOPE, how="inner")
        columns = [SCOPE, SOURCE_UUID, DIMENSION_TAG, VALUE, WILDCARD]
        covered = concat([by_value.select(*columns), by_wildcard.select(*columns)], distinct=True)
        return _conjoin(covered, [SCOPE, SOURCE_UUID, VALUE, WILDCARD], dimensions)


def _conjoin(covered: Relation, keys: list[str], dimensions: int) -> Relation:
    """Keep pieces covered by all ``dimensions`` conjoined dimensions of one column."""
    if dimensions == 1:
        return covered.select(*keys).unique()
    return (
        covered.group_by(*keys)
        .agg(ma.count_records().alias("__n"))
        .filter(ma.col("__n").eq(dimensions))
        .select(*keys)
    )


# ---------------------------------------------------------------------------
# OrderedField
# ---------------------------------------------------------------------------


class OrderedField(Field):
    """INT, DECIMAL, DATE and DATETIME: closed intervals cut at concrete type bounds."""

    def __init__(self, context_field: ContextField):
        if context_field.data_type not in ORDERED_TYPES:
            raise DecisionTableError(
                f"OrderedField requires INT, DECIMAL, DATE or DATETIME, not {context_field.data_type.value}"
            )
        super().__init__(context_field)

    # -- concrete type bounds and steps (Python values) ------------------

    @property
    def minimum(self) -> t.Any:
        return self._bounds()[0]

    @property
    def maximum(self) -> t.Any:
        return self._bounds()[1]

    def _bounds(self) -> tuple[t.Any, t.Any]:
        cf = self.context_field
        if cf.data_type is DataType.INT:
            return INT_MINIMUM, INT_MAXIMUM
        if cf.data_type is DataType.DECIMAL:
            top = Decimal(10**cf.precision - 1).scaleb(-cf.scale)
            return -top, top
        if cf.data_type is DataType.DATE:
            return dt.date.min, dt.date.max
        tzinfo = dt.timezone.utc if cf.timezone == "utc" else None
        microsecond = {"s": 0, "ms": 999_000, "us": 999_999}[cf.datetime_unit]
        return (
            dt.datetime(1, 1, 1, tzinfo=tzinfo),
            dt.datetime(9999, 12, 31, 23, 59, 59, microsecond, tzinfo=tzinfo),
        )

    def _step(self) -> t.Any:
        cf = self.context_field
        if cf.data_type is DataType.INT:
            return 1
        if cf.data_type is DataType.DECIMAL:
            return Decimal(1).scaleb(-cf.scale)
        if cf.data_type is DataType.DATE:
            return dt.timedelta(days=1)
        return {"s": dt.timedelta(seconds=1), "ms": dt.timedelta(milliseconds=1), "us": dt.timedelta(microseconds=1)}[
            cf.datetime_unit
        ]

    def successor(self, value: t.Any) -> t.Any:
        """Next representable value, or ``None`` at the maximum (nothing beyond it is computed)."""
        if value >= self.maximum:
            return None
        return self.context_field.successor(value)

    def predecessor(self, value: t.Any) -> t.Any:
        """Previous representable value, or ``None`` at the minimum."""
        if value <= self.minimum:
            return None
        return value - self._step()

    def reduce(self, value: t.Any) -> t.Any:
        """Reduce a Python context value to the declared resolution."""
        cf = self.context_field
        if cf.data_type is DataType.DATETIME:
            if cf.datetime_unit == "s":
                return value.replace(microsecond=0)
            if cf.datetime_unit == "ms":
                return value.replace(microsecond=value.microsecond // 1000 * 1000)
        return value

    # -- expressions -----------------------------------------------------

    def literal(self, value: t.Any) -> BaseExpressionAPI:
        if is_expression(value):
            return value
        cf = self.context_field
        if cf.data_type is DataType.INT:
            return ma.lit(value).cast(int)
        if cf.data_type is DataType.DECIMAL:
            return ma.lit(value, dtype=cf.decimal_dtype)
        return ma.lit(value)

    def quantise(self, value: BaseExpressionAPI) -> BaseExpressionAPI:
        """Canonical authored value: DECIMAL at declared scale, DATETIME at its unit."""
        cf = self.context_field
        if cf.data_type is DataType.DECIMAL:
            return value.cast(cf.decimal_dtype, rounding=cf.rounding)
        if cf.data_type is DataType.DATETIME:
            return self._floor(value)
        if cf.data_type is DataType.INT:
            return value.cast(int)
        return value

    def quantise_list(self, payload: BaseExpressionAPI) -> BaseExpressionAPI:
        cf = self.context_field
        if cf.data_type is DataType.DECIMAL:
            element = ma.element().cast(cf.decimal_dtype, rounding=cf.rounding)
        elif cf.data_type is DataType.DATETIME:
            element = self._floor(ma.element())
        else:
            return payload
        return canonicalize_set_expr(payload.list.agg(element))

    def _floor(self, value: BaseExpressionAPI) -> BaseExpressionAPI:
        """Reduce a DATETIME expression to the declared unit, keeping UTC fields tz-aware."""
        floored = value.dt.floor(f"1{self.context_field.datetime_unit}")
        return floored.dt.convert_time_zone("UTC") if self.context_field.timezone == "utc" else floored

    def shift(self, value: BaseExpressionAPI, direction: int) -> BaseExpressionAPI:
        """Successor (``+1``) or predecessor (``-1``) of an expression, one declared step."""
        cf = self.context_field
        if cf.data_type is DataType.INT:
            return value + direction
        if cf.data_type is DataType.DECIMAL:
            step = ma.lit(self._step() * direction, dtype=cf.decimal_dtype)
            return (value + step).cast(cf.decimal_dtype, rounding=cf.rounding)
        if cf.data_type is DataType.DATE:
            return value.dt.add_days(direction)
        unit = {"s": "add_seconds", "ms": "add_milliseconds", "us": "add_microseconds"}[cf.datetime_unit]
        return getattr(value.dt, unit)(direction)

    # -- interpretation --------------------------------------------------

    def interpret(self, region: Region) -> Region:
        if isinstance(region, Unsatisfiable):
            return region
        if isinstance(region, Point):
            return self.interpret(
                Interval(region.value, region.value, region.wildcard, region.wildcard)
            )
        if isinstance(region, Everything):
            return Interval()
        if isinstance(region, Interval):
            if (
                _static(region.low, region.high)
                and region.low is not None
                and region.high is not None
                and not _definitely(region.low_wildcard, True)
                and not _definitely(region.high_wildcard, True)
                and region.low > region.high
            ):
                return self.unsatisfiable(f"empty interval [{region.low!r}, {region.high!r}]")
            return region
        if isinstance(region, Members):
            if _static(region.values) and not region.values and not _definitely(region.wildcard, True):
                return self.unsatisfiable("empty set")
            return region
        if isinstance(region, Negation) and isinstance(region.region, (Point, Members, Everything)):
            return region
        raise self.not_applicable(region)

    def unsatisfiable_rules(
        self, bound: Relation, entries: t.Sequence[tuple[int, Dimension, Region]]
    ) -> Relation:
        """Rules whose interval coverage is empty once negated values are removed."""
        positive, negated = _split_entries(entries, (Interval, Members))
        if not positive:
            return self._no_rules(bound)
        regions = _union([self.interval_regions(bound, region, index) for index, region in positive])
        excluded = [
            relation
            for inner in negated
            if (relation := _excluded_values(bound, inner, REGION_LOW)) is not None
        ]
        columns = ("__u_lo", "__u_hi")
        bounds = regions.select(SCOPE, SOURCE_UUID, REGION_LOW, REGION_HIGH)
        points = [
            relation.with_columns(ma.col(REGION_LOW).alias(REGION_HIGH)).select(
                SCOPE, SOURCE_UUID, REGION_LOW, REGION_HIGH
            )
            for relation in excluded
        ]
        pieces = self.pieces(self.cuts(_union([bounds, *points])), columns)
        coverage = self.interval_coverage(regions, pieces, columns, len(positive))
        for relation in excluded:
            coverage = coverage.join(
                relation.select(
                    SCOPE, SOURCE_UUID, ma.col(REGION_LOW).alias(columns[0]), ma.col(REGION_LOW).alias(columns[1])
                ),
                on=[SCOPE, SOURCE_UUID, *columns],
                how="anti",
            )
        return self._empty_rules(bound, coverage)

    # -- relational pieces ----------------------------------------------

    def _bound(self, value: t.Any, wildcard: t.Any, default: t.Any) -> BaseExpressionAPI:
        if value is None or _definitely(wildcard, True):
            return self.literal(default)
        if _definitely(wildcard, False):
            return self.literal(value)
        return ma.when(wildcard).then(self.literal(default)).otherwise(self.literal(value))

    def interval_regions(
        self, bound: Relation, region: Region, dimension_index: int
    ) -> Relation:
        """``(scope, source_uuid, __dim, __r_lo, __r_hi)`` closed intervals for one dimension."""
        tag = ma.lit(dimension_index).alias(DIMENSION_TAG)
        scope = ma.col(BOUND_SCOPE).alias(SCOPE)
        uuid = ma.col(BOUND_UUID).alias(SOURCE_UUID)
        if isinstance(region, Interval):
            low = self._bound(region.low, region.low_wildcard, self.minimum)
            high = self._bound(region.high, region.high_wildcard, self.maximum)
            return bound.select(
                scope, uuid, tag, low.alias(REGION_LOW), high.alias(REGION_HIGH)
            ).filter(ma.col(REGION_LOW).le(ma.col(REGION_HIGH)))
        if isinstance(region, Members):
            wildcard = _flag(region.wildcard)
            wild = bound.filter(wildcard).select(
                scope,
                uuid,
                tag,
                self.literal(self.minimum).alias(REGION_LOW),
                self.literal(self.maximum).alias(REGION_HIGH),
            )
            named = (
                bound.filter(~wildcard)
                .select(scope, uuid, tag, region.values.alias(REGION_LOW))
                .explode(REGION_LOW)
                .filter(ma.col(REGION_LOW).is_not_null())
                .with_columns(ma.col(REGION_LOW).alias(REGION_HIGH))
            )
            return concat([wild, named])
        raise self.not_applicable(region)

    def regions(self, bound: Relation, region: Region, dimension_index: int) -> Relation:
        """Standard region relation of one interpreted dimension region."""
        if isinstance(region, (Interval, Members)):
            return self.interval_regions(bound, region, dimension_index)
        return self.value_regions(bound, region, dimension_index)

    def cuts(self, regions: Relation) -> Relation:
        """Each scope's ordered cut values: interval starts, successors of ends, and the minimum."""
        starts = regions.select(SCOPE, ma.col(REGION_LOW).alias(CUT))
        stops = regions.filter(ma.col(REGION_HIGH).lt(self.literal(self.maximum))).select(
            SCOPE, self.shift(ma.col(REGION_HIGH), 1).alias(CUT)
        )
        floor = regions.select(SCOPE).unique().with_columns(self.literal(self.minimum).alias(CUT))
        return concat([starts, stops, floor], distinct=True)

    def pieces(self, cuts: Relation, columns: tuple[str, str]) -> Relation:
        """Closed pieces ``(scope, lo, hi)``; the last piece ends at the concrete maximum."""
        low, high = columns
        following = ma.col(CUT).lead(1).over(SCOPE, order_by=CUT).alias("__next")
        ended = ma.col("__next").is_null()
        return cuts.with_columns(following).select(
            ma.col(SCOPE),
            ma.col(CUT).alias(low),
            ma.when(ended)
            .then(self.literal(self.maximum))
            .otherwise(self.shift(ma.col("__next"), -1))
            .alias(high),
        )

    def interval_coverage(
        self, regions: Relation, pieces: Relation, columns: tuple[str, str], dimensions: int
    ) -> Relation:
        """``(scope, source_uuid, lo, hi)`` pieces inside every conjoined dimension's intervals."""
        low, high = columns
        joined = regions.join(pieces, on=SCOPE, how="inner").filter(
            ma.col(REGION_LOW).le(ma.col(low)) & ma.col(high).le(ma.col(REGION_HIGH))
        )
        covered = joined.select(SCOPE, SOURCE_UUID, DIMENSION_TAG, low, high).unique()
        return _conjoin(covered, [SCOPE, SOURCE_UUID, low, high], dimensions)

    # -- derivation of the engine-readable table columns -----------------

    def entry_columns(self, name: str) -> tuple[str, str]:
        return f"{name}__lo", f"{name}__hi"

    def entry_dimension(self, name: str, *, negated: bool = False, is_set: bool = False) -> Dimension:
        if negated:
            return _negation_dimension(self.context_field, name, is_set)
        low, high = self.entry_columns(name)
        return Dimension(
            dimension_name=name,
            context_field=self.context_field.name,
            match_strategy=MatchStrategy.RANGE,
            range_min_field=low,
            range_max_field=high,
        )


def _negation_dimension(context_field: ContextField, name: str, is_set: bool) -> Dimension:
    """Engine dimension reading a negation column: its authored value or the field wildcard."""
    return Dimension(
        dimension_name=name,
        context_field=context_field.name,
        rule_field=name,
        match_strategy=MatchStrategy.SET_EXCLUSION if is_set else MatchStrategy.NOT_EQUAL,
    )


# ---------------------------------------------------------------------------
# UnorderedField
# ---------------------------------------------------------------------------


class UnorderedField(Field):
    """STR and BOOL: the values a scope's rules name plus one wildcard piece."""

    def __init__(self, context_field: ContextField):
        if context_field.data_type not in {DataType.STR, DataType.BOOL}:
            raise DecisionTableError(
                f"UnorderedField requires STR or BOOL, not {context_field.data_type.value}"
            )
        super().__init__(context_field)

    def interpret(self, region: Region) -> Region:
        if isinstance(region, Unsatisfiable):
            return region
        if isinstance(region, Point):
            return Everything() if _definitely(region.wildcard, True) else region
        if isinstance(region, Members):
            if _definitely(region.wildcard, True):
                return Everything()
            if _static(region.values) and not region.values:
                return self.unsatisfiable("empty set")
            return region
        if isinstance(region, Everything):
            return region
        if isinstance(region, Negation) and isinstance(region.region, (Point, Members, Everything)):
            return region
        raise self.not_applicable(region)

    def unsatisfiable_rules(
        self, bound: Relation, entries: t.Sequence[tuple[int, Dimension, Region]]
    ) -> Relation:
        """Rules whose positive regions share no named value once negated values are removed."""
        positive, negated = _split_entries(entries, (Point, Members, Everything))
        if not positive:
            return self._no_rules(bound)
        regions = _union([self.value_regions(bound, region, index) for index, region in positive])
        coverage = self.value_coverage(regions, self.value_pieces(regions), len(positive))
        for inner in negated:
            excluded = _excluded_values(bound, inner, VALUE)
            if excluded is not None:
                coverage = coverage.join(excluded, on=[SCOPE, SOURCE_UUID, VALUE], how="anti")
        return self._empty_rules(bound, coverage)

    def regions(self, bound: Relation, region: Region, dimension_index: int) -> Relation:
        return self.value_regions(bound, region, dimension_index)

    def entry_columns(self, name: str) -> tuple[str]:
        return (name,)

    def entry_dimension(self, name: str, *, negated: bool = False, is_set: bool = False) -> Dimension:
        if negated:
            return _negation_dimension(self.context_field, name, is_set)
        return Dimension(
            dimension_name=name,
            context_field=self.context_field.name,
            rule_field=name,
            match_strategy=MatchStrategy.EXACT,
        )


# ---------------------------------------------------------------------------
# TruthyField
# ---------------------------------------------------------------------------


class TruthyField(Field):
    """Ternary conditions: patterns, TRUTHY, PRESENCE and OUTCOME dimensions.

    The stored form is an int8 required outcome per condition (1 match/required,
    -1 required opposite, 0 no requirement); it is read as OUTCOME by the engine.
    """

    def interpret(self, region: Region) -> Region:
        if isinstance(region, Unsatisfiable):
            return region
        if isinstance(region, Matches):
            return Outcome(1, region.wildcard, region)
        if isinstance(region, Outcome):
            if _static(region.value) and region.value not in (-1, 0, 1):
                raise DecisionTableError(f"outcome must be -1, 0 or 1, not {region.value!r}")
            return region
        if isinstance(region, Everything):
            return Outcome(0)
        raise self.not_applicable(region)

    def unsatisfiable_rules(
        self, bound: Relation, entries: t.Sequence[tuple[int, Dimension, Region]]
    ) -> Relation:
        """Rules whose conflicting required outcomes (same strategy, one field) cannot both hold."""
        by_strategy: dict[MatchStrategy, list[BaseExpressionAPI]] = {}
        for _, dimension, region in entries:
            if dimension.match_strategy in (MatchStrategy.TRUTHY, MatchStrategy.PRESENCE, MatchStrategy.OUTCOME):
                by_strategy.setdefault(dimension.match_strategy, []).append(required_outcome(region))
        conflict = ma.lit(False)
        for outcomes in by_strategy.values():
            for i, left in enumerate(outcomes):
                for right in outcomes[i + 1 :]:
                    conflict = conflict | (left.eq(1) & right.eq(-1)) | (left.eq(-1) & right.eq(1))
        if not by_strategy:
            return self._no_rules(bound)
        return self._rules(bound.filter(conflict.fill_null(False)))

    def map_truthy(self, value: t.Any) -> t.Any:
        """Rule value to its declared outcome through the field's truthy mapping."""
        mapping = self.context_field.truthy
        if not is_expression(value):
            try:
                return mapping[value]
            except KeyError:
                raise DecisionTableError(f"{value!r} has no truthy mapping on {self.name!r}") from None
        result = ma.lit(0)
        for mapped, mapped_outcome in mapping.items():
            if mapped is None:
                continue
            result = ma.when(value.eq(exact_literal(mapped)).fill_null(False)).then(mapped_outcome).otherwise(result)
        return result

    def map_presence(self, value: t.Any) -> t.Any:
        """``IS_NULL`` to 1, ``IS_NOT_NULL`` to -1; the wildcard is handled by the caller."""
        if not is_expression(value):
            try:
                return {"IS_NULL": 1, "IS_NOT_NULL": -1}[value]
            except KeyError:
                raise DecisionTableError(f"{value!r} is not IS_NULL or IS_NOT_NULL") from None
        return (
            ma.when(value.eq("IS_NULL"))
            .then(1)
            .when(value.eq("IS_NOT_NULL"))
            .then(-1)
            .otherwise(0)
        )

    @staticmethod
    def pattern_match(strategy: t.Any, value: BaseExpressionAPI, pattern: t.Any) -> BaseExpressionAPI:
        """Whether ``value`` satisfies a pattern: the one operation used for context patterns.

        ``strategy`` is a ``MatchStrategy`` or, for per-row conditions, an expression of
        strategy names (``pattern_conditions.strategy``).
        """
        if is_expression(strategy):
            return (
                ma.when(strategy.eq(MatchStrategy.PREFIX.value))
                .then(value.str.starts_with(pattern))
                .when(strategy.eq(MatchStrategy.SUFFIX.value))
                .then(value.str.ends_with(pattern))
                .when(strategy.eq(MatchStrategy.CONTAINS.value))
                .then(value.str.contains(pattern))
                .otherwise(value.str.regex_contains(pattern))
            )
        if strategy is MatchStrategy.PREFIX:
            return value.str.starts_with(pattern)
        if strategy is MatchStrategy.SUFFIX:
            return value.str.ends_with(pattern)
        if strategy is MatchStrategy.CONTAINS:
            return value.str.contains(pattern)
        if strategy in (MatchStrategy.REGEX, MatchStrategy.CONTEXT_REGEX):
            return value.str.regex_contains(pattern)
        raise DecisionTableError(f"{strategy!r} is not a pattern strategy")

    @classmethod
    def classify(cls, strategy: t.Any, value: BaseExpressionAPI, pattern: t.Any) -> BaseExpressionAPI:
        """Ternary outcome of a concrete value: 1 on a match, -1 otherwise, 0 when missing."""
        return (
            ma.when(value.is_null())
            .then(0)
            .when(cls.pattern_match(strategy, value, pattern))
            .then(1)
            .otherwise(-1)
        )

    def scalar_cell(self, raw: BaseExpressionAPI, source_dtype: t.Any) -> tuple[t.Any, t.Any]:
        return raw, self.wildcard_flag(raw, source_dtype)

    def regions(self, bound: Relation, region: Region, dimension_index: int) -> Relation:
        return self.value_regions(bound, region, dimension_index)

    def entry_columns(self, name: str) -> tuple[str]:
        return (name,)

    def entry_dimension(self, name: str) -> Dimension:
        return Dimension(
            dimension_name=name,
            context_field=self.context_field.name,
            rule_field=name,
            match_strategy=MatchStrategy.OUTCOME,
        )


# ---------------------------------------------------------------------------
# Family selection
# ---------------------------------------------------------------------------


def family_for(dimension: Dimension) -> Field:
    """The field family a dimension's strategy is interpreted by."""
    if dimension.match_strategy in TRUTHY_STRATEGIES:
        return TruthyField(dimension.field)
    if dimension.field.data_type in ORDERED_TYPES:
        return OrderedField(dimension.field)
    return UnorderedField(dimension.field)
