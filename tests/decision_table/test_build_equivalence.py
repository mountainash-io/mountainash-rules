"""Plan 2 Task 5: a built table agrees with its authored rules (finite-completion oracle)."""

from __future__ import annotations

from decimal import Decimal

import polars as pl
import pytest

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.build import build_decision_table
from mountainash_rules.decision_table.table import DecisionTable, derive_entry_metadata, outcome_field

from tests.decision_table.construction_support import rows, validated_rules
from tests.decision_table.oracles.finite_completion import (
    FiniteCompletionOracle,
)
from tests.decision_table.test_ordered_and_categorical_fields import (
    INT_MAX,
    fees_rates_metadata,
    fees_rates_rules,
    term_metadata,
    term_rules,
)

M = MatchStrategy
REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")
TERM = ContextField(name="term", data_type=DataType.INT, wildcard=None, wildcard_declared=True)


def dimension(name, strategy, field, **kwargs):
    return Dimension(dimension_name=name, context_field=field, match_strategy=strategy, **kwargs)


def score(scope="s"):
    return Aggregate(scope=scope, output_name="score", contribution_column="score", data_type=DataType.INT)


def sources_by_entry(table: DecisionTable) -> dict:
    """``{(scope, entry_sources_id): source uuids}`` from the table's entry_sources."""
    grouped: dict = {}
    for row in table.entry_sources.to_dicts():
        grouped.setdefault((row["scope"], row["entry_sources_id"]), set()).add(row["source_uuid"])
    return {key: frozenset(value) for key, value in grouped.items()}


def entry_summary(table: DecisionTable, *columns: str) -> set[tuple]:
    """``{(columns..., sources)}`` for every entry."""
    sources = sources_by_entry(table)
    return {
        (*(_freeze(row[column]) for column in columns), sources[(row["scope"], row["entry_sources_id"])])
        for row in table.entries.to_dicts()
    }


def _freeze(value):
    return tuple(value) if isinstance(value, list) else value


# ---------------------------------------------------------------------------
# Fixtures: (data, metadata, grid, exact grid)
# ---------------------------------------------------------------------------


def categorical():
    grid = {"region": ["EU", "US", "APAC"]}
    return fees_rates_rules(), fees_rates_metadata(), grid, grid


def categorical_negation():
    grid = {"region": ["EU", "US", "APAC"]}
    return fees_rates_rules(negation=True), fees_rates_metadata(negation=True), grid, grid


def ordered():
    rate = Aggregate(
        scope="rates", output_name="rate", contribution_column="rate",
        data_type=DataType.DECIMAL, precision=16, scale=2, rounding="half_to_even",
    )
    data = term_rules(T1={"range": (1, 5)}, T2={"exact": 3}, T3={"min": 10})
    data["rate"] = [Decimal("2.00"), Decimal("-0.25"), Decimal("3.00")]
    grid = {"term": [0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 12]}
    return data, term_metadata(outputs=(rate,)), grid, grid


def ordered_negation():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(TERM,),
        dimensions=(
            dimension("t_range", M.RANGE, "term", range_min_field="lo", range_max_field="hi"),
            dimension("t_ne", M.NOT_EQUAL, "term"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["T1", "N1"],
        "scope": ["s", "s"],
        "lo": [1, 1],
        "hi": [10, 10],
        "t_ne": pl.Series([None, 5], dtype=pl.Int64),
        "score": [1, 10],
    }
    grid = {"term": [0, 1, 3, 5, 7, 10, 11]}
    return data, metadata, grid, grid


def conjunction():
    """Two fields, overlapping rules, a wildcard rule: conjunction and overlapping entries."""
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(REGION, TERM),
        dimensions=(
            dimension("region", M.EXACT, "region"),
            dimension("term_range", M.RANGE, "term", range_min_field="lo", range_max_field="hi"),
            dimension("term_min", M.MIN, "term"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["A", "B", "C", "D"],
        "scope": ["s"] * 4,
        "region": ["EU", "*", "US", "EU"],
        "lo": pl.Series([1, None, None, None], dtype=pl.Int64),
        "hi": pl.Series([5, None, None, None], dtype=pl.Int64),
        "term_min": pl.Series([None, 3, None, 4], dtype=pl.Int64),
        "score": [1, 2, 4, 8],
    }
    grid = {"region": ["EU", "US", "APAC"], "term": [0, 1, 3, 4, 5, 6]}
    return data, metadata, grid, grid


CODE = ContextField(
    name="code", data_type=DataType.STR, wildcard="*", truthy={"yes": 1, "no": -1, "maybe": 0}
)


def patterns():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(CODE,),
        dimensions=(
            dimension("code", M.EXACT, "code"),
            dimension("pre", M.PREFIX, "code"),
            dimension("rx", M.REGEX, "code"),
            dimension("anywhere", M.CONTAINS, "code"),
            dimension("upper", M.CONTEXT_REGEX, "code", regex_pattern="^[A-Z]"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["P1", "P2", "P3", "P4", "P5"],
        "scope": ["s"] * 5,
        "code": ["*", "*", "*", "US-1", "*"],
        "pre": ["EU", "*", "EU", "*", "*"],
        "rx": ["*", "-1$", "-22$", "*", "*"],
        "anywhere": ["*", "*", "*", "*", "U"],
        "score": [1, 2, 4, 8, 16],
    }
    grid = {"code": ["EU-1", "EU-22", "US-1", "xx", "UK-2", None]}
    return data, metadata, grid, {"code": ["EU-1", "EU-22", "US-1", "xx", "UK-2"]}


def truthy():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(CODE,),
        dimensions=(dimension("agree", M.TRUTHY, "code"),),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["Y", "N", "M", "ANY"],
        "scope": ["s"] * 4,
        "agree": ["yes", "no", "maybe", "*"],
        "score": [1, 2, 4, 8],
    }
    return data, metadata, {"code": ["yes", "no", "maybe", "other", None]}, {"code": ["yes", "no"]}


VIP = ContextField(name="vip", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True)


def null_rules():
    """The worked null rules R1-R3 (explicit-dont-care-sentinels.md)."""
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(REGION, VIP),
        dimensions=(
            dimension("region", M.EXACT, "region"),
            dimension("region_presence", M.PRESENCE, "region"),
            dimension("vip", M.EXACT, "vip"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["R1", "R2", "R3"],
        "scope": ["s"] * 3,
        "region": ["EU", "*", "*"],
        "region_presence": ["*", "IS_NULL", "IS_NOT_NULL"],
        "vip": [None, None, True],
        "score": [1, 2, 4],
    }
    grid = {"region": ["EU", "US", None], "vip": [True, False]}
    return data, metadata, grid, grid


def outcome_rules():
    flag = ContextField(name="flag", data_type=DataType.INT, wildcard=None, wildcard_declared=True)
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(flag,),
        dimensions=(dimension("flag_outcome", M.OUTCOME, "flag"),),
        outputs=(score("s"),),
    )
    data = {"rule_id": ["neg", "zero", "pos"], "scope": ["s"] * 3, "flag_outcome": [-1, 0, 1], "score": [1, 2, 4]}
    return data, metadata, {"flag": [-1, 0, 1]}, {"flag": [-1, 1]}


FIXTURES = {
    "categorical": categorical,
    "categorical_negation": categorical_negation,
    "ordered": ordered,
    "ordered_negation": ordered_negation,
    "conjunction": conjunction,
    "patterns": patterns,
    "truthy": truthy,
    "null_rules": null_rules,
    "outcome": outcome_rules,
}


def built(backend_name, name):
    data, metadata, grid, exact_grid = FIXTURES[name]()
    validated = validated_rules(backend_name, data, metadata)
    return validated, build_decision_table(validated), grid, exact_grid


# ---------------------------------------------------------------------------
# Equivalence with the authored rules, over every complete and omitted context
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_built_table_agrees_with_the_authored_rules_on_every_context(backend_name, name):
    validated, table, grid, exact_grid = built(backend_name, name)
    oracle = FiniteCompletionOracle(validated, grid, exact_grid)

    checked = oracle.check_equivalence(table)

    # every combination of {omitted, grid value} per field, for every scope
    expected = 1
    for values in grid.values():
        expected *= len(values) + 1
    assert checked == expected * len(oracle.scopes)


@pytest.mark.parametrize("name", ["conjunction", "categorical_negation"])
def test_overlapping_entries_are_all_returned_with_distinct_source_sets(backend_name, name):
    validated, table, grid, _ = built(backend_name, name)
    oracle = FiniteCompletionOracle(validated, grid)
    context = {"region": "EU", "term": 4} if name == "conjunction" else {"region": "EU"}

    matches = oracle.table_matches(table, "s" if name == "conjunction" else "fees", context)

    assert len(matches) > 1
    assert len({match.sources for match in matches}) == len(matches)
    assert len({match.entry_id for match in matches}) == len(matches)
    # the most specific entry carries every rule the context satisfies
    top = max(matches, key=lambda match: match.specificity)
    assert top.sources == oracle.survivors("s" if name == "conjunction" else "fees", context)


def test_a_context_no_rule_covers_matches_no_entry(backend_name):
    validated, table, grid, _ = built(backend_name, "ordered")
    oracle = FiniteCompletionOracle(validated, grid)

    assert oracle.survivors("rates", {"term": 7}) == frozenset()
    assert oracle.table_matches(table, "rates", {"term": 7}) == []
    assert oracle.table_matches(table, "rates", {"term": 0}) == []


def test_conjunction_entries_hold_only_cells_a_rule_covers(backend_name):
    _, table, _, _ = built(backend_name, "conjunction")

    summary = entry_summary(table, "region", "term__lo", "term__hi")

    # A: EU & [1,5]; B: any & >=3; C: US; D: EU & >=4. Every entry is covered by a rule.
    assert ("EU", 1, 2, frozenset({"A"})) in summary
    assert ("EU", 3, 3, frozenset({"A", "B"})) in summary
    assert ("EU", 4, 5, frozenset({"A", "B", "D"})) in summary
    assert ("EU", 6, INT_MAX, frozenset({"B", "D"})) in summary
    assert ("*", 3, 3, frozenset({"B"})) in summary
    assert ("US", 3, 3, frozenset({"B", "C"})) in summary
    assert all(sources for *_, sources in summary)


# ---------------------------------------------------------------------------
# The worked examples, through the finished table
# ---------------------------------------------------------------------------


def test_categorical_worked_example_entries_sources_and_outputs(backend_name):
    validated, table, grid, _ = built(backend_name, "categorical")

    assert entry_summary(table, "scope", "region", "fee", "rate") == {
        ("fees", "EU", 11, None, frozenset({"F1", "F2"})),
        ("fees", "*", 1, None, frozenset({"F2"})),
        ("rates", "US", None, Decimal("-0.10"), frozenset({"R1"})),
    }
    oracle = FiniteCompletionOracle(validated, grid)
    eu = {(m.sources, m.specificity) for m in oracle.table_matches(table, "fees", {"region": "EU"})}
    assert eu == {(frozenset({"F1", "F2"}), 1), (frozenset({"F2"}), 0)}
    assert {m.sources for m in oracle.table_matches(table, "fees", {"region": "APAC"})} == {frozenset({"F2"})}
    assert {m.sources for m in oracle.table_matches(table, "rates", {})} == {frozenset({"R1"})}


def test_categorical_negation_worked_example(backend_name):
    validated, table, grid, _ = built(backend_name, "categorical_negation")

    assert entry_summary(table, "scope", "region", "region_ne", "fee") == {
        ("fees", "EU", "*", 11, frozenset({"F1", "F2"})),
        ("fees", "*", "*", 1, frozenset({"F2"})),
        ("fees", "EU", "US", 16, frozenset({"F1", "F2", "N1"})),
        ("fees", "*", "US", 6, frozenset({"F2", "N1"})),
        ("rates", "US", "*", None, frozenset({"R1"})),
    }
    oracle = FiniteCompletionOracle(validated, grid)
    found = {
        region: {m.sources for m in oracle.table_matches(table, "fees", {"region": region})}
        for region in ("EU", "US", "APAC")
    }
    assert len(found["EU"]) == 4
    assert found["US"] == {frozenset({"F2"})}
    assert found["APAC"] == {frozenset({"F2"}), frozenset({"F2", "N1"})}


def test_ordered_worked_example_entries_sources_and_outputs(backend_name):
    validated, table, grid, _ = built(backend_name, "ordered")

    assert entry_summary(table, "term__lo", "term__hi", "rate") == {
        (1, 2, Decimal("2.00"), frozenset({"T1"})),
        (3, 3, Decimal("1.75"), frozenset({"T1", "T2"})),
        (4, 5, Decimal("2.00"), frozenset({"T1"})),
        (10, INT_MAX, Decimal("3.00"), frozenset({"T3"})),
    }
    oracle = FiniteCompletionOracle(validated, grid)
    expectations = {
        3: {(frozenset({"T1", "T2"}), Decimal("1.75"))},
        5: {(frozenset({"T1"}), Decimal("2.00"))},
        7: set(),
        12: {(frozenset({"T3"}), Decimal("3.00"))},
    }
    for term, expected in expectations.items():
        found = {(m.sources, dict(m.outputs)["rate"]) for m in oracle.table_matches(table, "rates", {"term": term})}
        assert found == expected, term
    omitted = oracle.table_matches(table, "rates", {})
    assert len(omitted) == 4


def test_ordered_negation_worked_example(backend_name):
    validated, table, grid, _ = built(backend_name, "ordered_negation")
    oracle = FiniteCompletionOracle(validated, grid)

    assert entry_summary(table, "term__lo", "term__hi", "t_ne", "score") == {
        (1, 10, None, 1, frozenset({"T1"})),
        (1, 10, 5, 11, frozenset({"T1", "N1"})),
    }
    assert {(m.sources, m.specificity) for m in oracle.table_matches(table, "s", {"term": 3})} == {
        (frozenset({"T1"}), 1),
        (frozenset({"T1", "N1"}), 2),
    }
    assert {m.sources for m in oracle.table_matches(table, "s", {"term": 5})} == {frozenset({"T1"})}


def test_pattern_worked_example_entries_and_matches(backend_name):
    validated, table, grid, _ = built(backend_name, "patterns")
    oracle = FiniteCompletionOracle(validated, grid)

    patterns = {row["column"]: (row["strategy"], row["pattern"]) for row in table.pattern_conditions.to_dicts()}
    assert patterns == {
        "pre__pattern_0": ("prefix", "EU"),
        "rx__pattern_0": ("regex", "-1$"),
        "rx__pattern_1": ("regex", "-22$"),
        "anywhere__pattern_0": ("contains", "U"),
        "upper": ("context_regex", "^[A-Z]"),
    }
    # context EU-22: prefix EU holds, -22$ holds, -1$ does not, contains U holds, upper holds
    expected = {frozenset({"P1", "P3"}), frozenset({"P1", "P3", "P5"})}
    assert {m.sources for m in oracle.table_matches(table, "s", {"code": "EU-22"})} >= expected
    survivors = oracle.survivors("s", {"code": "EU-22"})
    assert survivors == frozenset({"P1", "P3", "P5"})
    # a lowercase value fails the CONTEXT_REGEX every rule requires, and matches nothing
    assert oracle.survivors("s", {"code": "xx"}) == frozenset()
    assert oracle.table_matches(table, "s", {"code": "xx"}) == []
    # null is unknown on every pattern: all rules remain possible
    assert oracle.table_matches(table, "s", {"code": None}) != []


def test_zero_specificity_matches_survive_an_omitted_context(backend_name):
    validated, table, grid, _ = built(backend_name, "null_rules")
    oracle = FiniteCompletionOracle(validated, grid)

    # C3: nothing set -> every rule possible at specificity 0
    matches = oracle.table_matches(table, "s", {})
    assert {m.specificity for m in matches} == {0}
    assert frozenset().union(*(m.sources for m in matches)) == frozenset({"R1", "R2", "R3"})
    # C2: explicit null region and vip=False -> R2 (presence 1) and R1 (value unknown)
    assert oracle.survivors("s", {"region": None, "vip": False}) == frozenset({"R1", "R2"})
    assert frozenset().union(
        *(m.sources for m in oracle.table_matches(table, "s", {"region": None, "vip": False}))
    ) == frozenset({"R1", "R2"})


# ---------------------------------------------------------------------------
# Derived entry metadata
# ---------------------------------------------------------------------------


def arrow_types(relation_, *columns):
    schema = relation_.to_pyarrow().schema
    return {name: str(schema.field(name).type) for name in columns}


def test_entry_metadata_maps_pattern_truthy_and_presence_columns_to_int8_outcome_dimensions(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION, CODE),
        dimensions=(
            dimension("region", M.EXACT, "region"),
            dimension("region_presence", M.PRESENCE, "region"),
            dimension("agree", M.TRUTHY, "code"),
            dimension("pre", M.PREFIX, "code"),
            dimension("rx", M.REGEX, "code"),
        ),
    )
    data = {
        "rule_id": ["a", "b"],
        "region": ["EU", "*"],
        "region_presence": ["*", "IS_NULL"],
        "agree": ["yes", "no"],
        "pre": ["EU", "*"],
        "rx": ["-1$", "-22$"],
    }
    table = build_decision_table(validated_rules(backend_name, data, metadata))

    derived = table.entry_metadata
    by_name = {d.dimension_name: d for d in derived.dimensions}
    outcome_columns = {
        "region_presence", "agree", "pre__pattern_0", "rx__pattern_0", "rx__pattern_1",
    }
    assert set(by_name) == {"region", *outcome_columns}
    for column in outcome_columns:
        assert by_name[column].match_strategy is M.OUTCOME
        assert by_name[column].resolved_rule_field == column
        assert by_name[column].context_field == outcome_field(column)
    assert by_name["region"].match_strategy is M.EXACT
    assert by_name["region"].context_field == "region"
    assert arrow_types(table.entries, *sorted(outcome_columns)) == {c: "int8" for c in sorted(outcome_columns)}
    # source fields are carried over unchanged, beside one INT field per outcome column
    by_field = {f.name: f for f in derived.fields}
    assert by_field["region"] == REGION and by_field["code"] == CODE
    assert {name for name in by_field if name.startswith("outcome__")} == {
        outcome_field(column) for column in outcome_columns
    }
    assert all(by_field[outcome_field(c)].data_type is DataType.INT for c in outcome_columns)


def test_entry_metadata_is_derived_from_the_saved_parts_alone(backend_name):
    validated, table, _, _ = built(backend_name, "patterns")

    # what load will have: source metadata round-tripped through its flat tables + pattern_conditions
    reloaded = DimensionsMetadata.from_tables(table.source_metadata.to_tables())
    derived = derive_entry_metadata(reloaded, table.pattern_conditions)

    assert derived.model_dump() == table.entry_metadata.model_dump()
    rebuilt = DecisionTable(table.entries, reloaded, table.entry_sources, table.pattern_conditions, table.build_id)
    assert rebuilt.entry_metadata.model_dump() == table.entry_metadata.model_dump()


def test_pattern_columns_sort_by_ordinal_not_text(backend_name):
    patterns_ = [f"p{i:02d}" for i in range(11)]
    metadata = DimensionsMetadata(
        uuid_column="rule_id", fields=(CODE,), dimensions=(dimension("pre", M.PREFIX, "code"),)
    )
    data = {"rule_id": [f"r{i:02d}" for i in range(11)], "pre": patterns_}
    table = build_decision_table(validated_rules(backend_name, data, metadata))

    names = [d.dimension_name for d in table.entry_metadata.dimensions]
    assert names == [f"pre__pattern_{k}" for k in range(11)]
    assert rows(table.pattern_conditions, "column", "pattern")[0] == ("pre__pattern_0", "p00")
