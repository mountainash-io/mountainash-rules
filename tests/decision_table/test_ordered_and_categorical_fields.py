"""Plan 2 Task 2: ordered cuts and unordered values per scope, negation columns, worked examples."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import mountainash.expressions as ma
import polars as pl
import pytest
from mountainash import DecimalDtype

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.fields import OrderedField

from tests.decision_table.construction_support import (
    cell_sources,
    engine_matches,
    folded_outputs,
    rows,
    run_stages,
    validated_rules,
)

INT_MIN, INT_MAX = -2147483648, 2147483647
M = MatchStrategy

TERM = ContextField(name="term", data_type=DataType.INT, wildcard=None, wildcard_declared=True)
REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")


def make_dimensions(*specs):
    return tuple(Dimension(**spec) for spec in specs)


def term_metadata(*, outputs=None):
    if outputs is None:
        outputs = (
            Aggregate(scope="rates", output_name="rate", contribution_column="rate", data_type=DataType.INT),
        )
    return DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(TERM,),
        dimensions=make_dimensions(
            dict(dimension_name="t_range", context_field="term", match_strategy=M.RANGE,
                 range_min_field="lo", range_max_field="hi"),
            dict(dimension_name="t_exact", context_field="term", match_strategy=M.EXACT),
            dict(dimension_name="t_min", context_field="term", match_strategy=M.MIN),
            dict(dimension_name="t_max", context_field="term", match_strategy=M.MAX),
            dict(dimension_name="t_set", context_field="term", match_strategy=M.SET_MEMBERSHIP),
        ),
        outputs=outputs,
    )


def term_rules(**rules):
    """Columns of ``term_metadata`` from ``{uuid: {dimension: value}}``; unset cells are wildcards."""
    uuids = list(rules)

    def ints(key, index=None):
        values = [rules[u].get(key) for u in uuids]
        if index is not None:
            values = [None if v is None else v[index] for v in values]
        return pl.Series(values, dtype=pl.Int64)

    return {
        "rule_id": uuids,
        "scope": ["rates"] * len(uuids),
        "lo": ints("range", 0),
        "hi": ints("range", 1),
        "t_exact": ints("exact"),
        "t_min": ints("min"),
        "t_max": ints("max"),
        "t_set": pl.Series([rules[u].get("set") for u in uuids], dtype=pl.List(pl.Int64)),
        "rate": [0] * len(uuids),
    }


def term_cells(backend_name, data, metadata=None):
    validated = validated_rules(backend_name, data, metadata or term_metadata())
    boundaries, pieces, coverage = run_stages(validated)
    return validated, boundaries, pieces, coverage


# ---------------------------------------------------------------------------
# Ordered intervals: INT
# ---------------------------------------------------------------------------


def test_int_range_exact_and_min_give_the_worked_pieces_entries_and_outputs(backend_name):
    rate = Aggregate(scope="rates", output_name="rate", contribution_column="rate",
                     data_type=DataType.DECIMAL, precision=16, scale=2, rounding="half_to_even")
    data = term_rules(T1={"range": (1, 5)}, T2={"exact": 3}, T3={"min": 10})
    data["rate"] = [Decimal("2.00"), Decimal("-0.25"), Decimal("3.00")]
    metadata = term_metadata(outputs=(rate,))
    validated, boundaries, pieces, coverage = term_cells(backend_name, data, metadata)

    assert rows(pieces.by_plan["term"]) == [
        ("rates", INT_MIN, 0),
        ("rates", 1, 2),
        ("rates", 3, 3),
        ("rates", 4, 5),
        ("rates", 6, 9),
        ("rates", 10, INT_MAX),
    ]
    # Cut points: T1 -> {1, 6}; T2 -> {3, 4}; T3 -> {10}; sorted, with the concrete minimum.
    assert [cut for _, cut in rows(boundaries.cuts["term"])] == [INT_MIN, 1, 3, 4, 6, 10]
    # Uncovered intervals ([INT minimum, 0] and [6, 9]) have no emitted row.
    assert cell_sources(coverage) == {
        ("rates", 1, 2): frozenset({"T1"}),
        ("rates", 3, 3): frozenset({"T1", "T2"}),
        ("rates", 4, 5): frozenset({"T1"}),
        ("rates", 10, INT_MAX): frozenset({"T3"}),
    }
    assert folded_outputs(validated, coverage, rate) == {
        ("rates", 1, 2): Decimal("2.00"),
        ("rates", 3, 3): Decimal("1.75"),
        ("rates", 4, 5): Decimal("2.00"),
        ("rates", 10, INT_MAX): Decimal("3.00"),
    }
    for context, expected in (
        ({"term": 3}, {(3, 3): 1}),
        ({"term": 5}, {(4, 5): 1}),
        ({"term": 7}, {}),
        ({"term": 12}, {(10, INT_MAX): 1}),
        ({}, {(1, 2): 0, (3, 3): 0, (4, 5): 0, (10, INT_MAX): 0}),
    ):
        assert engine_matches(boundaries, coverage, metadata, "rates", context) == expected


def test_nonadjacent_intervals_stay_separate_entries_with_shared_sources(backend_name):
    data = term_rules(A={"range": (1, 2)}, B={"range": (4, 5)}, C={"set": [1, 2, 4, 5]})
    _, _, _, coverage = term_cells(backend_name, data)

    assert cell_sources(coverage) == {
        ("rates", 1, 1): frozenset({"A", "C"}),
        ("rates", 2, 2): frozenset({"A", "C"}),
        ("rates", 4, 4): frozenset({"B", "C"}),
        ("rates", 5, 5): frozenset({"B", "C"}),
    }


def test_max_and_overlapping_ranges_cut_at_successors(backend_name):
    data = term_rules(A={"max": 5}, B={"range": (3, 8)})
    _, _, pieces, coverage = term_cells(backend_name, data)

    assert rows(pieces.by_plan["term"]) == [
        ("rates", INT_MIN, 2),
        ("rates", 3, 5),
        ("rates", 6, 8),
        ("rates", 9, INT_MAX),
    ]
    assert cell_sources(coverage) == {
        ("rates", INT_MIN, 2): frozenset({"A"}),
        ("rates", 3, 5): frozenset({"A", "B"}),
        ("rates", 6, 8): frozenset({"B"}),
    }


def test_dimensions_sharing_a_field_are_conjoined(backend_name):
    data = term_rules(
        X={"range": (1, 10), "min": 5},
        Y={"range": (1, 10), "exact": 3},
        Z={"range": (1, 10), "exact": 20},
        W={"range": (3, 9), "set": [2, 3, 4]},
    )
    _, _, pieces, coverage = term_cells(backend_name, data)

    assert rows(pieces.by_plan["term"]) == [
        ("rates", INT_MIN, 0),
        ("rates", 1, 1),
        ("rates", 2, 2),
        ("rates", 3, 3),
        ("rates", 4, 4),
        ("rates", 5, 9),
        ("rates", 10, 10),
        ("rates", 11, 19),
        ("rates", 20, 20),
        ("rates", 21, INT_MAX),
    ]
    assert cell_sources(coverage) == {
        ("rates", 3, 3): frozenset({"Y", "W"}),
        ("rates", 4, 4): frozenset({"W"}),
        ("rates", 5, 9): frozenset({"X"}),
        ("rates", 10, 10): frozenset({"X"}),
    }
    # Z's conjunction is empty: it contributes to no cell at all.
    assert "Z" not in set().union(*cell_sources(coverage).values())


def test_a_wildcard_rule_covers_every_piece_of_its_scope(backend_name):
    data = term_rules(ALL={}, A={"exact": 7})
    _, _, _, coverage = term_cells(backend_name, data)

    assert cell_sources(coverage) == {
        ("rates", INT_MIN, 6): frozenset({"ALL"}),
        ("rates", 7, 7): frozenset({"ALL", "A"}),
        ("rates", 8, INT_MAX): frozenset({"ALL"}),
    }


# ---------------------------------------------------------------------------
# Closed typed cuts: DECIMAL, DATE, DATETIME, concrete bounds
# ---------------------------------------------------------------------------


def single_field_metadata(field, *, with_range=False):
    dimensions = [Dimension(dimension_name="v", context_field=field.name, match_strategy=M.EXACT)]
    if with_range:
        dimensions = [
            Dimension(dimension_name="v", context_field=field.name, match_strategy=M.RANGE,
                      range_min_field="lo", range_max_field="hi")
        ]
    return DimensionsMetadata(uuid_column="rule_id", fields=(field,), dimensions=tuple(dimensions))


def decimal_field(rounding="half_away_from_zero"):
    return ContextField(name="v", data_type=DataType.DECIMAL, precision=16, scale=2, rounding=rounding,
                        wildcard=None, wildcard_declared=True)


def test_decimal_successor_at_scale_two(backend_name):
    data = {"rule_id": ["a", "b"], "v": [Decimal("1.50"), Decimal("1.51")]}
    _, _, pieces, coverage = run_stages_for(backend_name, data, decimal_field())

    assert rows(pieces.by_plan["v"]) == [
        ("default", Decimal("-99999999999999.99"), Decimal("1.49")),
        ("default", Decimal("1.50"), Decimal("1.50")),
        ("default", Decimal("1.51"), Decimal("1.51")),
        ("default", Decimal("1.52"), Decimal("99999999999999.99")),
    ]
    assert cell_sources(coverage) == {
        ("default", Decimal("1.50"), Decimal("1.50")): frozenset({"a"}),
        ("default", Decimal("1.51"), Decimal("1.51")): frozenset({"b"}),
    }


@pytest.mark.parametrize(
    ("rounding", "expected"),
    [("half_away_from_zero", Decimal("1.51")), ("half_to_even", Decimal("1.50"))],
)
def test_decimal_exact_values_are_quantised_with_the_declared_rounding(backend_name, rounding, expected):
    data = {"rule_id": ["a"], "v": [Decimal("1.505")]}
    _, _, _, coverage = run_stages_for(backend_name, data, decimal_field(rounding))

    assert cell_sources(coverage) == {("default", expected, expected): frozenset({"a"})}


def test_decimal_set_members_are_quantised_and_canonicalised(backend_name):
    field = decimal_field()
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(field,),
        dimensions=(Dimension(dimension_name="v", context_field="v", match_strategy=M.SET_MEMBERSHIP),),
    )
    data = {"rule_id": ["a"], "v": [[Decimal("1.5050"), Decimal("1.5040"), Decimal("1.5049")]]}
    validated = validated_rules(backend_name, data, metadata)
    _, _, coverage = run_stages(validated)

    assert cell_sources(coverage) == {
        ("default", Decimal("1.50"), Decimal("1.50")): frozenset({"a"}),
        ("default", Decimal("1.51"), Decimal("1.51")): frozenset({"a"}),
    }


def run_stages_for(backend_name, data, field, *, with_range=False):
    validated = validated_rules(backend_name, data, single_field_metadata(field, with_range=with_range))
    boundaries, pieces, coverage = run_stages(validated)
    return validated, boundaries, pieces, coverage


def test_date_successor_is_one_day(backend_name):
    field = ContextField(name="v", data_type=DataType.DATE, wildcard=None, wildcard_declared=True)
    data = {
        "rule_id": ["jan", "feb1"],
        "lo": [dt.date(2024, 1, 1), dt.date(2024, 2, 1)],
        "hi": [dt.date(2024, 1, 31), dt.date(2024, 2, 1)],
    }
    _, _, pieces, coverage = run_stages_for(backend_name, data, field, with_range=True)

    assert rows(pieces.by_plan["v"]) == [
        ("default", dt.date.min, dt.date(2023, 12, 31)),
        ("default", dt.date(2024, 1, 1), dt.date(2024, 1, 31)),
        ("default", dt.date(2024, 2, 1), dt.date(2024, 2, 1)),
        ("default", dt.date(2024, 2, 2), dt.date.max),
    ]
    assert cell_sources(coverage) == {
        ("default", dt.date(2024, 1, 1), dt.date(2024, 1, 31)): frozenset({"jan"}),
        ("default", dt.date(2024, 2, 1), dt.date(2024, 2, 1)): frozenset({"feb1"}),
    }


UNITS = {
    "s": dt.timedelta(seconds=1),
    "ms": dt.timedelta(milliseconds=1),
    "us": dt.timedelta(microseconds=1),
}
REDUCED = {
    "s": (dt.datetime(2024, 3, 1, 12, 0, 0), dt.datetime(2024, 3, 1, 12, 0, 0)),
    "ms": (dt.datetime(2024, 3, 1, 12, 0, 0, 123000), dt.datetime(2024, 3, 1, 12, 0, 0, 654000)),
    "us": (dt.datetime(2024, 3, 1, 12, 0, 0, 123456), dt.datetime(2024, 3, 1, 12, 0, 0, 654321)),
}


def datetime_field(unit, timezone="naive"):
    return ContextField(name="v", data_type=DataType.DATETIME, datetime_unit=unit, timezone=timezone,
                        wildcard=None, wildcard_declared=True)


@pytest.mark.parametrize("unit", ["s", "ms", "us"])
def test_datetime_cuts_use_the_next_declared_unit_and_reduce_rule_values(backend_name, unit):
    low, high = dt.datetime(2024, 3, 1, 12, 0, 0, 123456), dt.datetime(2024, 3, 1, 12, 0, 0, 654321)
    data = {"rule_id": ["a"], "lo": [low], "hi": [high]}
    field = datetime_field(unit)
    _, _, pieces, coverage = run_stages_for(backend_name, data, field, with_range=True)

    reduced_low, reduced_high = REDUCED[unit]
    step = UNITS[unit]
    family = OrderedField(field)
    assert rows(pieces.by_plan["v"]) == [
        ("default", family.minimum, reduced_low - step),
        ("default", reduced_low, reduced_high),
        ("default", reduced_high + step, family.maximum),
    ]
    assert cell_sources(coverage) == {("default", reduced_low, reduced_high): frozenset({"a"})}


CONTEXTS_FINER_THAN_THE_UNIT = {
    "s": dt.datetime(2024, 3, 1, 12, 0, 0, 900000),
    "ms": dt.datetime(2024, 3, 1, 12, 0, 0, 654400),
    "us": dt.datetime(2024, 3, 1, 12, 0, 0, 654321),
}


def pieces_containing(pieces, value, columns=("v__lo", "v__hi")):
    low, high = columns
    inside = pieces.filter(ma.col(low).le(value) & ma.col(high).ge(value))
    return rows(inside, low, high)


@pytest.mark.parametrize("unit", ["s", "ms", "us"])
def test_context_values_finer_than_the_unit_are_reduced_before_matching_cuts(backend_name, unit):
    low, high = dt.datetime(2024, 3, 1, 12, 0, 0, 123456), dt.datetime(2024, 3, 1, 12, 0, 0, 654321)
    field = datetime_field(unit)
    _, _, pieces, _ = run_stages_for(
        backend_name, {"rule_id": ["a"], "lo": [low], "hi": [high]}, field, with_range=True
    )
    family = OrderedField(field)
    reduced_low, reduced_high = REDUCED[unit]
    context = CONTEXTS_FINER_THAN_THE_UNIT[unit]

    assert family.reduce(context) == reduced_high
    reduced = family.quantise(ma.lit(context))
    assert pieces_containing(pieces.by_plan["v"], reduced) == [(reduced_low, reduced_high)]
    # Without the reduction a context finer than the unit misses the closed piece.
    unreduced = pieces_containing(pieces.by_plan["v"], ma.lit(context))
    assert unreduced == ([(reduced_low, reduced_high)] if context == reduced_high else [])


def test_datetime_utc_cuts_keep_the_declared_unit(backend_name):
    utc = dt.timezone.utc
    field = datetime_field("ms", "utc")
    data = {
        "rule_id": ["a"],
        "lo": [dt.datetime(2024, 3, 1, 12, 0, 0, 123456, tzinfo=utc)],
        "hi": [dt.datetime(2024, 3, 1, 12, 0, 0, 654321, tzinfo=utc)],
    }
    _, _, _, coverage = run_stages_for(backend_name, data, field, with_range=True)

    ((_, low, high),) = list(cell_sources(coverage))
    assert (low.replace(tzinfo=None), high.replace(tzinfo=None)) == REDUCED["ms"]


def test_decimal_context_values_finer_than_the_scale_are_rounded_before_matching_cuts(backend_name):
    field = decimal_field("half_away_from_zero")
    data = {"rule_id": ["a", "b"], "v": [Decimal("1.50"), Decimal("1.51")]}
    _, _, pieces, _ = run_stages_for(backend_name, data, field)
    finer = ma.lit(Decimal("1.505"), dtype=DecimalDtype(16, 3))
    reduced = OrderedField(field).quantise(finer)

    assert pieces_containing(pieces.by_plan["v"], reduced) == [(Decimal("1.51"), Decimal("1.51"))]
    assert pieces_containing(pieces.by_plan["v"], finer) == []


TERMINALS = [
    pytest.param(
        ContextField(name="v", data_type=DataType.INT, wildcard=None, wildcard_declared=True),
        INT_MIN, INT_MAX, 1, id="int",
    ),
    pytest.param(
        decimal_field(),
        Decimal("-99999999999999.99"), Decimal("99999999999999.99"), Decimal("0.01"), id="decimal",
    ),
    pytest.param(
        ContextField(name="v", data_type=DataType.DATE, wildcard=None, wildcard_declared=True),
        dt.date(1, 1, 1), dt.date(9999, 12, 31), dt.timedelta(days=1), id="date",
    ),
    pytest.param(
        datetime_field("s"),
        dt.datetime(1, 1, 1), dt.datetime(9999, 12, 31, 23, 59, 59), dt.timedelta(seconds=1), id="datetime-s",
    ),
    pytest.param(
        datetime_field("ms"),
        dt.datetime(1, 1, 1), dt.datetime(9999, 12, 31, 23, 59, 59, 999000), dt.timedelta(milliseconds=1),
        id="datetime-ms",
    ),
    pytest.param(
        datetime_field("us"),
        dt.datetime(1, 1, 1), dt.datetime(9999, 12, 31, 23, 59, 59, 999999), dt.timedelta(microseconds=1),
        id="datetime-us",
    ),
]


@pytest.mark.parametrize(("field", "minimum", "maximum", "step"), TERMINALS)
def test_each_type_has_correct_terminal_pieces_without_a_successor_beyond_the_maximum(
    backend_name, field, minimum, maximum, step
):
    family = OrderedField(field)
    assert (family.minimum, family.maximum) == (minimum, maximum)
    assert family.successor(maximum) is None
    assert family.predecessor(minimum) is None
    assert family.successor(minimum) == minimum + step
    assert family.predecessor(maximum) == maximum - step

    data = {"rule_id": ["at_min", "at_max"], "v": [minimum, maximum]}
    _, _, pieces, coverage = run_stages_for(backend_name, data, field)

    assert rows(pieces.by_plan["v"]) == [
        ("default", minimum, minimum),
        ("default", minimum + step, maximum - step),
        ("default", maximum, maximum),
    ]
    assert cell_sources(coverage) == {
        ("default", minimum, minimum): frozenset({"at_min"}),
        ("default", maximum, maximum): frozenset({"at_max"}),
    }


@pytest.mark.parametrize(("field", "minimum", "maximum", "step"), TERMINALS)
def test_ranges_ending_at_the_type_bounds_cover_the_terminal_pieces(
    backend_name, field, minimum, maximum, step
):
    middle = minimum + step * 5
    data = {"rule_id": ["low", "high"], "lo": [minimum, middle], "hi": [middle - step, maximum]}
    _, _, pieces, coverage = run_stages_for(backend_name, data, field, with_range=True)

    assert rows(pieces.by_plan["v"]) == [("default", minimum, middle - step), ("default", middle, maximum)]
    assert cell_sources(coverage) == {
        ("default", minimum, middle - step): frozenset({"low"}),
        ("default", middle, maximum): frozenset({"high"}),
    }


# ---------------------------------------------------------------------------
# Unordered fields: named values per scope plus one wildcard
# ---------------------------------------------------------------------------


def fees_rates_metadata(*, negation=False):
    dimensions = [Dimension(dimension_name="region", context_field="region", match_strategy=M.EXACT)]
    if negation:
        dimensions.append(
            Dimension(dimension_name="region_ne", context_field="region", rule_field="not_region",
                      match_strategy=M.NOT_EQUAL)
        )
    return DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(REGION,),
        dimensions=tuple(dimensions),
        outputs=(
            Aggregate(scope="fees", output_name="fee", contribution_column="fee", data_type=DataType.INT),
            Aggregate(scope="rates", output_name="rate", contribution_column="rate",
                      data_type=DataType.DECIMAL, precision=16, scale=2, rounding="half_to_even"),
        ),
    )


def fees_rates_rules(*, negation=False):
    uuids, scopes = ["F1", "F2", "R1"], ["fees", "fees", "rates"]
    regions, fees, rates = ["EU", "*", "US"], [10, 1, None], [None, None, Decimal("-0.10")]
    not_regions = ["*", "*", "*"]
    if negation:
        uuids, scopes, regions = [*uuids, "N1"], [*scopes, "fees"], [*regions, "*"]
        fees, rates, not_regions = [*fees, 5], [*rates, None], [*not_regions, "US"]
    return {
        "rule_id": uuids,
        "scope": scopes,
        "region": regions,
        "not_region": not_regions,
        "fee": pl.Series(fees, dtype=pl.Int64),
        "rate": pl.Series(rates, dtype=pl.Decimal(16, 2)),
    }


def match_all_scopes(boundaries, coverage, metadata, context):
    found = {}
    for scope in ("fees", "rates"):
        for key, specificity in engine_matches(boundaries, coverage, metadata, scope, context).items():
            found[(scope, *key)] = specificity
    return found


def test_categorical_cells_are_equality_entries_with_a_wildcard_entry(backend_name):
    metadata = fees_rates_metadata()
    validated = validated_rules(backend_name, fees_rates_rules(), metadata)
    boundaries, _, coverage = run_stages(validated)

    # Pieces for region: fees {EU, wildcard}; rates {US}.
    assert rows(boundaries.named_values["region"]) == [
        ("fees", "*", True),
        ("fees", "EU", False),
        ("rates", "US", False),
    ]
    assert cell_sources(coverage) == {
        ("fees", "EU"): frozenset({"F1", "F2"}),
        ("fees", "*"): frozenset({"F2"}),
        ("rates", "US"): frozenset({"R1"}),
    }
    fee, rate = metadata.outputs
    assert folded_outputs(validated, coverage, fee) == {("fees", "EU"): 11, ("fees", "*"): 1}
    assert folded_outputs(validated, coverage, rate) == {("rates", "US"): Decimal("-0.10")}

    expectations = {
        "EU": {("fees", "EU"): 1, ("fees", "*"): 0},
        "US": {("fees", "*"): 0, ("rates", "US"): 1},
        "APAC": {("fees", "*"): 0},
    }
    for region, expected in expectations.items():
        assert match_all_scopes(boundaries, coverage, metadata, {"region": region}) == expected
    # Region omitted: both fees entries and the rates entry are possible at specificity 0.
    assert match_all_scopes(boundaries, coverage, metadata, {}) == {
        ("fees", "EU"): 0, ("fees", "*"): 0, ("rates", "US"): 0,
    }


def test_negation_columns_carry_their_authored_value_or_the_wildcard(backend_name):
    metadata = fees_rates_metadata(negation=True)
    validated = validated_rules(backend_name, fees_rates_rules(negation=True), metadata)
    boundaries, _, coverage = run_stages(validated)

    assert coverage.cell_columns == ("region", "region_ne")
    assert cell_sources(coverage) == {
        ("fees", "EU", "*"): frozenset({"F1", "F2"}),
        ("fees", "*", "*"): frozenset({"F2"}),
        ("fees", "EU", "US"): frozenset({"F1", "F2", "N1"}),
        ("fees", "*", "US"): frozenset({"F2", "N1"}),
        ("rates", "US", "*"): frozenset({"R1"}),
    }
    fee, _ = metadata.outputs
    assert folded_outputs(validated, coverage, fee) == {
        ("fees", "EU", "*"): 11,
        ("fees", "*", "*"): 1,
        ("fees", "EU", "US"): 16,
        ("fees", "*", "US"): 6,
    }
    # region = EU matches all four; US only wildcard/wildcard; APAC wildcard/wildcard and wildcard/US.
    assert engine_matches(boundaries, coverage, metadata, "fees", {"region": "EU"}) == {
        ("EU", "*"): 1, ("*", "*"): 0, ("EU", "US"): 2, ("*", "US"): 1,
    }
    assert engine_matches(boundaries, coverage, metadata, "fees", {"region": "US"}) == {("*", "*"): 0}
    assert engine_matches(boundaries, coverage, metadata, "fees", {"region": "APAC"}) == {
        ("*", "*"): 0, ("*", "US"): 1,
    }


def test_negation_pieces_are_the_authored_values_of_the_scope(backend_name):
    metadata = fees_rates_metadata(negation=True)
    boundaries, _, _ = run_stages(validated_rules(backend_name, fees_rates_rules(negation=True), metadata))

    assert rows(boundaries.named_values["region_ne"]) == [
        ("fees", "*", True),
        ("fees", "US", False),
        ("rates", "*", True),
    ]
    plan = boundaries.plans[1]
    assert (plan.name, plan.role, plan.columns) == ("region_ne", "negation", ("region_ne",))


def test_unordered_pieces_preserve_distinct_values_even_when_sources_are_shared(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION,),
        dimensions=(Dimension(dimension_name="region", context_field="region", match_strategy=M.SET_MEMBERSHIP),),
    )
    data = {"rule_id": ["S", "W", "A"], "region": [["EU", "US"], ["*"], ["EU"]]}
    _, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert cell_sources(coverage) == {
        ("default", "EU"): frozenset({"S", "W", "A"}),
        ("default", "US"): frozenset({"S", "W"}),
        ("default", "*"): frozenset({"W"}),
    }


def test_scopes_have_their_own_named_values_and_wildcard_pieces(backend_name):
    metadata = fees_rates_metadata()
    data = {
        "rule_id": ["F1", "R1", "R2"],
        "scope": ["fees", "rates", "rates"],
        "region": ["EU", "US", "*"],
        "fee": [10, None, None],
        "rate": pl.Series([None, Decimal("1.00"), Decimal("2.00")], dtype=pl.Decimal(16, 2)),
    }
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert rows(boundaries.named_values["region"]) == [
        ("fees", "EU", False),
        ("rates", "*", True),
        ("rates", "US", False),
    ]
    assert cell_sources(coverage) == {
        ("fees", "EU"): frozenset({"F1"}),
        ("rates", "US"): frozenset({"R1", "R2"}),
        ("rates", "*"): frozenset({"R2"}),
    }


def test_bool_fields_use_the_unordered_family(backend_name):
    vip = ContextField(name="vip", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True)
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(vip,),
        dimensions=(Dimension(dimension_name="vip", context_field="vip", match_strategy=M.EXACT),),
    )
    data = {"rule_id": ["yes", "no", "any"], "vip": [True, False, None]}
    _, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert cell_sources(coverage) == {
        ("default", True): frozenset({"yes", "any"}),
        ("default", False): frozenset({"no", "any"}),
        ("default", None): frozenset({"any"}),
    }


# ---------------------------------------------------------------------------
# T1 / N1: negation dimensions, ordered and categorical variants
# ---------------------------------------------------------------------------


def test_ordered_negation_t1_n1_example(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(TERM,),
        dimensions=make_dimensions(
            dict(dimension_name="t_range", context_field="term", match_strategy=M.RANGE,
                 range_min_field="lo", range_max_field="hi"),
            dict(dimension_name="t_ne", context_field="term", match_strategy=M.NOT_EQUAL),
        ),
    )
    data = {
        "rule_id": ["T1", "N1"],
        "lo": [1, 1],
        "hi": [10, 10],
        "t_ne": pl.Series([None, 5], dtype=pl.Int64),
    }
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert coverage.cell_columns == ("term__lo", "term__hi", "t_ne")
    assert cell_sources(coverage) == {
        ("default", 1, 10, None): frozenset({"T1"}),
        ("default", 1, 10, 5): frozenset({"T1", "N1"}),
    }
    assert engine_matches(boundaries, coverage, metadata, "default", {"term": 3}) == {
        (1, 10, None): 1,
        (1, 10, 5): 2,
    }
    assert engine_matches(boundaries, coverage, metadata, "default", {"term": 5}) == {(1, 10, None): 1}


def test_categorical_negation_t1_n1_variant(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION,),
        dimensions=make_dimensions(
            dict(dimension_name="region", context_field="region", match_strategy=M.SET_MEMBERSHIP),
            dict(dimension_name="region_ne", context_field="region", match_strategy=M.NOT_EQUAL),
        ),
    )
    data = {
        "rule_id": ["T1", "N1"],
        "region": [["EU", "US"], ["EU", "US"]],
        "region_ne": ["*", "US"],
    }
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert cell_sources(coverage) == {
        ("default", "EU", "*"): frozenset({"T1"}),
        ("default", "US", "*"): frozenset({"T1"}),
        ("default", "EU", "US"): frozenset({"T1", "N1"}),
        ("default", "US", "US"): frozenset({"T1", "N1"}),
    }
    assert engine_matches(boundaries, coverage, metadata, "default", {"region": "EU"}) == {
        ("EU", "*"): 1,
        ("EU", "US"): 2,
    }
    assert engine_matches(boundaries, coverage, metadata, "default", {"region": "US"}) == {("US", "*"): 1}


def test_set_exclusion_columns_hold_the_canonical_authored_list(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION,),
        dimensions=make_dimensions(
            dict(dimension_name="region", context_field="region", match_strategy=M.EXACT),
            dict(dimension_name="not_regions", context_field="region", match_strategy=M.SET_EXCLUSION),
        ),
    )
    data = {
        "rule_id": ["A", "B", "C"],
        "region": ["EU", "EU", "*"],
        "not_regions": [["US", "AU", "US"], ["*"], ["AU", "US"]],
    }
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    # B excludes nothing (its list is the field wildcard), so it also covers the (AU, US) entries.
    assert cell_sources(coverage) == {
        ("default", "EU", ("AU", "US")): frozenset({"A", "B", "C"}),
        ("default", "EU", ("*",)): frozenset({"B"}),
        ("default", "*", ("AU", "US")): frozenset({"C"}),
    }
    assert engine_matches(boundaries, coverage, metadata, "default", {"region": "EU"}) == {
        ("EU", ("AU", "US")): 2,
        ("EU", ("*",)): 1,
        ("*", ("AU", "US")): 1,
    }
    assert engine_matches(boundaries, coverage, metadata, "default", {"region": "AU"}) == {}
