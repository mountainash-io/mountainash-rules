"""Plan 2 Task 1: region vocabulary, field families and one class per strategy."""

from __future__ import annotations

import pytest

from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table import strategies
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.decision_table.fields import (
    Everything,
    Interval,
    Matches,
    Members,
    Negation,
    OrderedField,
    Outcome,
    Point,
    TruthyField,
    Unsatisfiable,
    UnorderedField,
    everything,
    family_for,
    interval,
    matches,
    members,
    negation,
    outcome,
    point,
    unsatisfiable,
)
from mountainash_rules.decision_table.stages import stage_a_boundaries
from mountainash_rules.decision_table.strategies import Cell, strategy_for

from tests.decision_table.construction_support import (
    captured_rules,
    cell_sources,
    rows,
    run_stages,
)

INT_MIN, INT_MAX = -2147483648, 2147483647

TERM = ContextField(name="term", data_type=DataType.INT, wildcard=None, wildcard_declared=True)
REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")
VIP = ContextField(name="vip", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True)
CODE = ContextField(
    name="code",
    data_type=DataType.STR,
    wildcard="*",
    truthy={"yes": 1, "no": -1, "maybe": 0},
)


def dimension(strategy, field, name=None, **kwargs):
    if strategy is MatchStrategy.RANGE:
        kwargs.setdefault("range_min_field", "lo")
        kwargs.setdefault("range_max_field", "hi")
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(field,),
        dimensions=(
            Dimension(
                dimension_name=name or field.name,
                context_field=field.name,
                match_strategy=strategy,
                **kwargs,
            ),
        ),
    )
    return metadata.dimensions[0]


# ---------------------------------------------------------------------------
# Shared vocabulary and families
# ---------------------------------------------------------------------------


def test_region_vocabulary_constructors_build_the_shared_regions():
    assert point(3) == Point(3, False)
    assert interval(1, 5) == Interval(1, 5, False, False)
    assert interval(10) == Interval(10, None, False, False)
    assert everything() == Everything()
    assert matches("^a", MatchStrategy.REGEX) == Matches("^a", MatchStrategy.REGEX, False)
    assert outcome(-1) == Outcome(-1, False, None)
    assert members(["a", "b"]) == Members(["a", "b"], False)
    assert negation(point("US")) == Negation(Point("US", False))
    assert unsatisfiable("empty") == Unsatisfiable("empty")


@pytest.mark.parametrize(
    ("family_class", "field"),
    [(OrderedField, TERM), (UnorderedField, REGION), (TruthyField, CODE)],
)
def test_every_family_builds_the_same_vocabulary(family_class, field):
    family = family_class(field)
    assert family.point(1) == point(1)
    assert family.interval(1, 2) == interval(1, 2)
    assert family.everything() == everything()
    assert family.matches("p") == matches("p")
    assert family.unsatisfiable("x") == unsatisfiable("x")


def test_ordered_family_interprets_point_interval_and_everything_as_closed_intervals():
    family = OrderedField(TERM)

    assert family.interpret(point(3)) == Interval(3, 3, False, False)
    assert family.interpret(interval(1, 5)) == Interval(1, 5, False, False)
    assert family.interpret(everything()) == Interval(None, None)
    assert (family.minimum, family.maximum) == (INT_MIN, INT_MAX)
    with pytest.raises(DecisionTableError):
        family.interpret(matches("p"))


def test_unordered_family_interprets_point_and_everything_as_named_value_and_wildcard():
    family = UnorderedField(REGION)

    assert family.interpret(point("EU")) == point("EU")
    assert family.interpret(point("*", wildcard=True)) == everything()
    assert family.interpret(everything()) == everything()
    assert family.interpret(members(["EU", "US"])) == members(["EU", "US"])
    for not_applicable in (interval(1, 2), matches("p"), outcome(1)):
        with pytest.raises(DecisionTableError):
            family.interpret(not_applicable)


def test_truthy_family_interprets_patterns_and_outcomes_as_required_ternaries():
    family = TruthyField(CODE)
    condition = matches("^a", MatchStrategy.REGEX)

    assert family.interpret(condition) == Outcome(1, False, condition)
    assert family.interpret(outcome(-1)) == outcome(-1)
    assert family.interpret(everything()) == outcome(0)
    for not_applicable in (point("a"), interval(1, 2)):
        with pytest.raises(DecisionTableError):
            family.interpret(not_applicable)
    with pytest.raises(DecisionTableError):
        family.interpret(outcome(2))


def test_families_report_structurally_empty_regions_through_the_shared_interface():
    ordered, unordered, truthy = OrderedField(TERM), UnorderedField(REGION), TruthyField(CODE)

    assert isinstance(ordered.interpret(interval(5, 1)), Unsatisfiable)
    assert isinstance(ordered.interpret(members([])), Unsatisfiable)
    assert isinstance(ordered.intersect(interval(1, 5), interval(6, 9)), Unsatisfiable)
    assert ordered.intersect(interval(1, 5), point(3)) == Interval(3, 3)
    assert isinstance(unordered.intersect(point("EU"), point("US")), Unsatisfiable)
    assert unordered.intersect(point("EU"), members(["EU", "US"])) == point("EU")
    assert unordered.intersect(everything(), point("EU")) == point("EU")
    assert isinstance(truthy.intersect(outcome(1), outcome(-1)), Unsatisfiable)
    assert truthy.intersect(outcome(1), everything()) == outcome(1)
    assert truthy.intersect(outcome(1), outcome(1)) == outcome(1)


def test_family_selection_follows_strategy_and_data_type():
    cases = [
        (MatchStrategy.EXACT, TERM, OrderedField),
        (MatchStrategy.NOT_EQUAL, TERM, OrderedField),
        (MatchStrategy.EXACT, REGION, UnorderedField),
        (MatchStrategy.EXACT, VIP, UnorderedField),
        (MatchStrategy.REGEX, REGION, TruthyField),
        (MatchStrategy.PRESENCE, TERM, TruthyField),
        (MatchStrategy.TRUTHY, CODE, TruthyField),
        (MatchStrategy.OUTCOME, TERM, TruthyField),
    ]
    for strategy, field, expected in cases:
        kwargs = {"regex_pattern": "x"} if strategy is MatchStrategy.CONTEXT_REGEX else {}
        assert type(family_for(dimension(strategy, field, **kwargs))) is expected


def test_table_columns_and_dimensions_are_derived_from_the_family():
    ordered = OrderedField(TERM).entry_dimension("term")
    assert OrderedField(TERM).entry_columns("term") == ("term__lo", "term__hi")
    assert (ordered.match_strategy, ordered.range_min_field, ordered.range_max_field) == (
        MatchStrategy.RANGE,
        "term__lo",
        "term__hi",
    )

    unordered = UnorderedField(REGION)
    assert unordered.entry_columns("region") == ("region",)
    assert unordered.entry_dimension("region").match_strategy is MatchStrategy.EXACT
    negated = unordered.entry_dimension("region_ne", negated=True)
    assert (negated.match_strategy, negated.resolved_rule_field) == (
        MatchStrategy.NOT_EQUAL,
        "region_ne",
    )
    assert unordered.entry_dimension("regions_ne", negated=True, is_set=True).match_strategy is (
        MatchStrategy.SET_EXCLUSION
    )

    truthy = TruthyField(CODE).entry_dimension("code_regex__pattern_0")
    assert truthy.match_strategy is MatchStrategy.OUTCOME
    assert truthy.resolved_rule_field == "code_regex__pattern_0"
    assert truthy.context_field == "code"


# ---------------------------------------------------------------------------
# One class per strategy
# ---------------------------------------------------------------------------


def test_there_is_one_strategy_class_per_match_strategy():
    assert set(strategies.STRATEGIES) == set(MatchStrategy)
    assert len({type(strategy) for strategy in strategies.STRATEGIES.values()}) == len(MatchStrategy)
    for match_strategy, strategy in strategies.STRATEGIES.items():
        assert strategy.match_strategy is match_strategy
        assert strategy_for(match_strategy) is strategy
    assert {s.match_strategy for s in strategies.STRATEGIES.values() if s.negated} == {
        MatchStrategy.NOT_EQUAL,
        MatchStrategy.SET_EXCLUSION,
    }


LOWERINGS = [
    (strategies.Exact, TERM, (Cell(3),), None, point(3)),
    (strategies.NotEqual, REGION, (Cell("US"),), None, negation(point("US"))),
    (strategies.SetMembership, REGION, (Cell(["EU", "US"]),), None, members(["EU", "US"])),
    (strategies.SetExclusion, REGION, (Cell(["EU", "US"]),), None, negation(members(["EU", "US"]))),
    (strategies.Range, TERM, (Cell(1), Cell(5)), None, interval(1, 5)),
    (strategies.Min, TERM, (Cell(10),), None, interval(10, None)),
    (strategies.Max, TERM, (Cell(5),), None, interval(None, 5)),
    (strategies.Regex, REGION, (Cell("^a"),), None, matches("^a", MatchStrategy.REGEX)),
    (strategies.Prefix, REGION, (Cell("ab"),), None, matches("ab", MatchStrategy.PREFIX)),
    (strategies.Suffix, REGION, (Cell("yz"),), None, matches("yz", MatchStrategy.SUFFIX)),
    (strategies.Contains, REGION, (Cell("mid"),), None, matches("mid", MatchStrategy.CONTAINS)),
    (strategies.ContextRegex, REGION, (), "[0-9]+", matches("[0-9]+", MatchStrategy.CONTEXT_REGEX)),
    (strategies.Truthy, CODE, (Cell("yes"),), None, outcome(1)),
    (strategies.Truthy, CODE, (Cell("no"),), None, outcome(-1)),
    (strategies.Presence, REGION, (Cell("IS_NULL"),), None, outcome(1)),
    (strategies.Presence, REGION, (Cell("IS_NOT_NULL"),), None, outcome(-1)),
    (strategies.Outcome, TERM, (Cell(-1),), None, outcome(-1)),
]


@pytest.mark.parametrize(
    ("strategy_class", "field", "cells", "regex", "expected"),
    LOWERINGS,
    ids=[f"{case[0].__name__}-{i}" for i, case in enumerate(LOWERINGS)],
)
def test_each_strategy_lowers_a_representative_rule_into_the_shared_vocabulary(
    strategy_class, field, cells, regex, expected
):
    strategy = strategy_class()
    kwargs = {"regex_pattern": regex} if regex else {}
    dim = dimension(strategy.match_strategy, field, **kwargs)
    family = family_for(dim)

    assert strategy.lower(family, cells, dim) == expected
    assert strategy_for(strategy.match_strategy).__class__ is strategy_class


def test_wildcard_cells_lower_to_everything_for_truthy_and_presence():
    for strategy_class, cell in (
        (strategies.Truthy, Cell("*", wildcard=True)),
        (strategies.Presence, Cell("*", wildcard=True)),
    ):
        dim = dimension(strategy_class.match_strategy, CODE)
        assert strategy_class().lower(family_for(dim), (cell,), dim) == everything()


def test_range_with_wildcard_endpoints_keeps_them_for_interpretation():
    dim = dimension(MatchStrategy.RANGE, TERM, range_min_field="lo", range_max_field="hi")
    family = family_for(dim)
    region = strategies.Range().lower(family, (Cell(None, wildcard=True), Cell(5)), dim)
    assert region == Interval(None, 5, True, False)
    assert strategies.Min().lower(family, (Cell(0, wildcard=True),), dim) == Interval(0, None, True, False)


def test_unmapped_truthy_and_presence_values_are_rejected():
    dim = dimension(MatchStrategy.TRUTHY, CODE)
    with pytest.raises(DecisionTableError):
        strategies.Truthy().lower(family_for(dim), (Cell("unmapped"),), dim)
    with pytest.raises(DecisionTableError):
        strategies.Presence().lower(family_for(dim), (Cell("MAYBE_NULL"),), dim)


def test_strategies_reject_a_wrong_number_of_cells():
    dim = dimension(MatchStrategy.EXACT, TERM)
    with pytest.raises(DecisionTableError):
        strategies.Exact().lower(family_for(dim), (), dim)
    with pytest.raises(DecisionTableError):
        strategies.Range().lower(family_for(dim), (Cell(1),), dim)
    with pytest.raises(DecisionTableError):
        strategies.ContextRegex().lower(family_for(dim), (), None)


# ---------------------------------------------------------------------------
# Strategies lowered relationally on both backends
# ---------------------------------------------------------------------------


def single_dimension_regions(backend_name, field, strategy, data, **kwargs):
    dim = Dimension(
        dimension_name="d", context_field=field.name, match_strategy=strategy, **kwargs
    )
    metadata = DimensionsMetadata(uuid_column="rule_id", fields=(field,), dimensions=(dim,))
    boundaries = stage_a_boundaries(captured_rules(backend_name, data, metadata))
    return boundaries, {plan.name: boundaries.regions[plan.name] for plan in boundaries.plans}


def interval_rows(regions):
    return rows(regions, "source_uuid", "__r_lo", "__r_hi")


def test_ordered_strategies_lower_to_closed_intervals(backend_name):
    _, regions = single_dimension_regions(
        backend_name,
        TERM,
        MatchStrategy.EXACT,
        {"rule_id": ["exact", "wild"], "d": [3, None]},
    )
    assert interval_rows(regions["term"]) == [("exact", 3, 3), ("wild", INT_MIN, INT_MAX)]

    _, regions = single_dimension_regions(
        backend_name,
        TERM,
        MatchStrategy.RANGE,
        {"rule_id": ["r", "open_hi", "wild"], "lo": [1, 4, None], "hi": [5, None, None]},
        range_min_field="lo",
        range_max_field="hi",
    )
    assert interval_rows(regions["term"]) == [
        ("open_hi", 4, INT_MAX),
        ("r", 1, 5),
        ("wild", INT_MIN, INT_MAX),
    ]

    _, regions = single_dimension_regions(
        backend_name, TERM, MatchStrategy.MIN, {"rule_id": ["m", "w"], "d": [10, None]}
    )
    assert interval_rows(regions["term"]) == [("m", 10, INT_MAX), ("w", INT_MIN, INT_MAX)]

    _, regions = single_dimension_regions(
        backend_name, TERM, MatchStrategy.MAX, {"rule_id": ["m", "w"], "d": [5, None]}
    )
    assert interval_rows(regions["term"]) == [("m", INT_MIN, 5), ("w", INT_MIN, INT_MAX)]


def test_ordered_set_membership_is_a_union_of_one_point_intervals(backend_name):
    _, regions = single_dimension_regions(
        backend_name,
        TERM,
        MatchStrategy.SET_MEMBERSHIP,
        {"rule_id": ["s", "empty", "wild"], "d": [[3, 1, 3], [], None]},
    )
    assert interval_rows(regions["term"]) == [
        ("s", 1, 1),
        ("s", 3, 3),
        ("wild", INT_MIN, INT_MAX),
    ]


def test_unordered_strategies_lower_to_named_values_and_the_wildcard(backend_name):
    _, regions = single_dimension_regions(
        backend_name, REGION, MatchStrategy.EXACT, {"rule_id": ["eu", "any"], "d": ["EU", "*"]}
    )
    assert rows(regions["region"], "source_uuid", "value", "wildcard") == [
        ("any", "*", True),
        ("eu", "EU", False),
    ]

    _, regions = single_dimension_regions(
        backend_name,
        VIP,
        MatchStrategy.EXACT,
        {"rule_id": ["yes", "no", "any"], "d": [True, False, None]},
    )
    assert rows(regions["vip"], "source_uuid", "value", "wildcard") == [
        ("any", None, True),
        ("no", False, False),
        ("yes", True, False),
    ]

    _, regions = single_dimension_regions(
        backend_name,
        REGION,
        MatchStrategy.SET_MEMBERSHIP,
        {"rule_id": ["s", "any"], "d": [["US", "EU"], ["*"]]},
    )
    assert rows(regions["region"], "source_uuid", "value", "wildcard") == [
        ("any", "*", True),
        ("s", "EU", False),
        ("s", "US", False),
    ]


def test_negation_strategies_lower_to_their_own_value_columns(backend_name):
    boundaries, regions = single_dimension_regions(
        backend_name,
        REGION,
        MatchStrategy.NOT_EQUAL,
        {"rule_id": ["ne", "any"], "d": ["US", "*"]},
    )
    plan = boundaries.plans[0]
    assert (plan.name, plan.role, plan.columns) == ("d", "negation", ("d",))
    assert rows(regions["d"], "source_uuid", "value", "wildcard") == [
        ("any", "*", True),
        ("ne", "US", False),
    ]

    _, regions = single_dimension_regions(
        backend_name,
        TERM,
        MatchStrategy.SET_EXCLUSION,
        {"rule_id": ["ne", "any"], "d": [[9, 7, 9], None]},
    )
    assert rows(regions["d"], "source_uuid", "value", "wildcard") == [
        ("any", None, True),
        ("ne", (7, 9), False),
    ]


def test_truthy_presence_and_outcome_lower_to_int8_required_outcomes(backend_name):
    _, regions = single_dimension_regions(
        backend_name,
        CODE,
        MatchStrategy.TRUTHY,
        {"rule_id": ["y", "n", "m", "any"], "d": ["yes", "no", "maybe", "*"]},
    )
    assert rows(regions["d"], "source_uuid", "value", "wildcard") == [
        ("any", 0, True),
        ("m", 0, True),
        ("n", -1, False),
        ("y", 1, False),
    ]

    _, regions = single_dimension_regions(
        backend_name,
        REGION,
        MatchStrategy.PRESENCE,
        {"rule_id": ["null", "present", "any"], "d": ["IS_NULL", "IS_NOT_NULL", "*"]},
    )
    assert rows(regions["d"], "source_uuid", "value", "wildcard") == [
        ("any", 0, True),
        ("null", 1, False),
        ("present", -1, False),
    ]

    _, regions = single_dimension_regions(
        backend_name,
        TERM,
        MatchStrategy.OUTCOME,
        {"rule_id": ["a", "b", "c"], "d": [-1, 0, 1]},
    )
    assert rows(regions["d"], "source_uuid", "value", "wildcard") == [
        ("a", -1, False),
        ("b", 0, True),
        ("c", 1, False),
    ]


@pytest.mark.parametrize(
    "strategy",
    [MatchStrategy.REGEX, MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS],
)
def test_pattern_strategies_produce_ternary_conditions_on_a_truthy_field(backend_name, strategy):
    boundaries, regions = single_dimension_regions(
        backend_name,
        REGION,
        strategy,
        {"rule_id": ["b", "a", "any"], "d": ["pb", "pa", "*"]},
    )

    assert [(p.name, p.kind, p.role, type(p.family).__name__) for p in boundaries.plans] == [
        ("d__pattern_0", "value", "outcome", "TruthyField"),
        ("d__pattern_1", "value", "outcome", "TruthyField"),
    ]
    assert rows(regions["d__pattern_0"], "source_uuid", "value") == [("a", 1), ("any", 0), ("b", 0)]
    assert rows(regions["d__pattern_1"], "source_uuid", "value") == [("a", 0), ("any", 0), ("b", 1)]
    assert rows(boundaries.pattern_conditions) == [
        ("d__pattern_0", "region", strategy.value, "pa"),
        ("d__pattern_1", "region", strategy.value, "pb"),
    ]


def test_context_regex_is_one_static_condition_required_by_every_rule(backend_name):
    boundaries, regions = single_dimension_regions(
        backend_name,
        REGION,
        MatchStrategy.CONTEXT_REGEX,
        {"rule_id": ["a", "b"]},
        regex_pattern="[0-9]+",
    )
    assert [plan.columns for plan in boundaries.plans] == [("d",)]
    assert rows(regions["d"], "source_uuid", "value") == [("a", 1), ("b", 1)]
    assert rows(boundaries.pattern_conditions) == [("d", "region", "context_regex", "[0-9]+")]


# ---------------------------------------------------------------------------
# Type-selected behaviour and mixed strategies on one field
# ---------------------------------------------------------------------------


def test_type_selects_the_family_and_column_kind(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(TERM, REGION, VIP, CODE),
        dimensions=(
            Dimension(dimension_name="term", context_field="term", match_strategy=MatchStrategy.EXACT),
            Dimension(dimension_name="region", context_field="region", match_strategy=MatchStrategy.EXACT),
            Dimension(dimension_name="vip", context_field="vip", match_strategy=MatchStrategy.EXACT),
            Dimension(dimension_name="code_rx", context_field="code", rule_field="rx", match_strategy=MatchStrategy.REGEX),
        ),
    )
    data = {"rule_id": ["a"], "term": [3], "region": ["EU"], "vip": [True], "rx": ["^c"]}
    boundaries = stage_a_boundaries(captured_rules(backend_name, data, metadata))

    assert [(p.name, p.kind, p.columns, type(p.family).__name__) for p in boundaries.plans] == [
        ("term", "interval", ("term__lo", "term__hi"), "OrderedField"),
        ("region", "value", ("region",), "UnorderedField"),
        ("vip", "value", ("vip",), "UnorderedField"),
        ("code_rx__pattern_0", "value", ("code_rx__pattern_0",), "TruthyField"),
    ]
    assert rows(boundaries.regions["term"], "__r_lo", "__r_hi") == [(3, 3)]
    assert rows(boundaries.regions["region"], "value", "wildcard") == [("EU", False)]
    assert rows(boundaries.regions["vip"], "value", "wildcard") == [(True, False)]


def test_same_field_mixed_strategies_cover_each_rule_independently(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(TERM,),
        dimensions=(
            Dimension(dimension_name="t_range", context_field="term", match_strategy=MatchStrategy.RANGE,
                      range_min_field="lo", range_max_field="hi"),
            Dimension(dimension_name="t_exact", context_field="term", match_strategy=MatchStrategy.EXACT),
            Dimension(dimension_name="t_min", context_field="term", match_strategy=MatchStrategy.MIN),
        ),
    )
    data = {
        "rule_id": ["T1", "T2", "T3"],
        "lo": [1, None, None],
        "hi": [5, None, None],
        "t_exact": [None, 3, None],
        "t_min": [None, None, 10],
    }
    validated = captured_rules(backend_name, data, metadata)
    _, pieces, coverage = run_stages(validated)

    assert coverage.cell_columns == ("term__lo", "term__hi")
    assert cell_sources(coverage) == {
        ("default", 1, 2): frozenset({"T1"}),
        ("default", 3, 3): frozenset({"T1", "T2"}),
        ("default", 4, 5): frozenset({"T1"}),
        ("default", 10, INT_MAX): frozenset({"T3"}),
    }
