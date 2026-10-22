"""Plan 2 Task 3: TruthyField pattern, truthy and presence conditions as int8 required outcomes."""

from __future__ import annotations

import re

import pytest
from mountainash.relations import relation

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.stages import classify_named_values
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

from tests.decision_table.construction_support import (
    cell_sources,
    folded_outputs,
    rows,
    rules_relation,
    run_stages,
    validated_rules,
)

M = MatchStrategy

CODE = ContextField(
    name="code", data_type=DataType.STR, wildcard="*", truthy={"yes": 1, "no": -1, "maybe": 0}
)
PATTERN_STRATEGIES = [M.REGEX, M.PREFIX, M.SUFFIX, M.CONTAINS]


def metadata_of(*dimensions, fields=(CODE,), **kwargs):
    return DimensionsMetadata(uuid_column="rule_id", fields=fields, dimensions=dimensions, **kwargs)


def dimension(name, strategy, field="code", **kwargs):
    return Dimension(dimension_name=name, context_field=field, match_strategy=strategy, **kwargs)


def arrow_types(relation_, *columns):
    schema = relation_.to_pyarrow().schema
    return {name: str(schema.field(name).type) for name in columns}


# ---------------------------------------------------------------------------
# Pattern strategies: derived ternary conditions on a TruthyField
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", PATTERN_STRATEGIES)
def test_each_pattern_strategy_stores_required_outcomes_per_distinct_pattern(backend_name, strategy):
    metadata = metadata_of(dimension("d", strategy))
    data = {"rule_id": ["r1", "r2", "r3"], "d": ["aa", "bb", "*"]}
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert coverage.cell_columns == ("d__pattern_0", "d__pattern_1")
    assert [type(plan.family).__name__ for plan in boundaries.plans] == ["TruthyField", "TruthyField"]
    # 1 = the pattern is required, 0 = no requirement; a wildcard rule covers every combination.
    assert cell_sources(coverage) == {
        ("default", 0, 0): frozenset({"r3"}),
        ("default", 0, 1): frozenset({"r2", "r3"}),
        ("default", 1, 0): frozenset({"r1", "r3"}),
        ("default", 1, 1): frozenset({"r1", "r2", "r3"}),
    }
    assert rows(boundaries.pattern_conditions) == [
        ("d__pattern_0", "code", strategy.value, "aa"),
        ("d__pattern_1", "code", strategy.value, "bb"),
    ]
    assert arrow_types(coverage.cell_coverage, "d__pattern_0", "d__pattern_1") == {
        "d__pattern_0": "int8",
        "d__pattern_1": "int8",
    }


def test_context_regex_is_a_static_condition_every_rule_requires(backend_name):
    metadata = metadata_of(dimension("digits", M.CONTEXT_REGEX, regex_pattern="[0-9]+"))
    data = {"rule_id": ["a"]}
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert cell_sources(coverage) == {("default", 1): frozenset({"a"})}
    assert rows(boundaries.pattern_conditions) == [("digits", "code", "context_regex", "[0-9]+")]
    assert arrow_types(coverage.cell_coverage, "digits") == {"digits": "int8"}


def test_all_wildcard_patterns_add_no_columns(backend_name):
    metadata = metadata_of(dimension("d", M.REGEX))
    boundaries, _, coverage = run_stages(
        validated_rules(backend_name, {"rule_id": ["a"], "d": ["*"]}, metadata)
    )

    assert boundaries.plans == ()
    assert rows(boundaries.pattern_conditions) == []
    assert cell_sources(coverage) == {("default",): frozenset({"a"})}


def test_pattern_ordinals_follow_sorted_patterns_not_row_order(backend_name):
    metadata = metadata_of(dimension("d", M.REGEX))
    forward = {"rule_id": ["r1", "r2", "r3"], "d": ["^a", "^b", "^c"]}
    backward = {name: list(reversed(values)) for name, values in forward.items()}

    results = []
    for data in (forward, backward):
        boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))
        results.append((rows(boundaries.pattern_conditions), cell_sources(coverage)))

    assert results[0] == results[1]
    assert [row[3] for row in results[0][0]] == ["^a", "^b", "^c"]
    assert [row[0] for row in results[0][0]] == ["d__pattern_0", "d__pattern_1", "d__pattern_2"]


def test_pattern_columns_are_shared_across_scopes_with_scope_local_pieces(backend_name):
    outputs = tuple(
        Aggregate(scope=scope, output_name="v", contribution_column="v", data_type=DataType.INT)
        for scope in ("a", "b")
    )
    metadata = metadata_of(dimension("d", M.REGEX), scope_column="scope", outputs=outputs)
    data = {"rule_id": ["ra", "rb"], "scope": ["a", "b"], "d": ["^a", "^b"], "v": [1, 2]}
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert rows(boundaries.pattern_conditions, "column", "pattern") == [("d__pattern_0", "^a"), ("d__pattern_1", "^b")]
    # Each scope only has the piece 0 on the other scope's pattern: no cross-scope combinations.
    assert cell_sources(coverage) == {
        ("a", 1, 0): frozenset({"ra"}),
        ("b", 0, 1): frozenset({"rb"}),
    }


# ---------------------------------------------------------------------------
# Conjunctions and composition with other dimensions on the source field
# ---------------------------------------------------------------------------


def test_two_pattern_conditions_on_one_field_emit_every_covered_ternary_combination(backend_name):
    metadata = metadata_of(dimension("rx", M.REGEX), dimension("px", M.PREFIX, rule_field="px"))
    data = {
        "rule_id": ["A", "B", "C"],
        "rx": ["^a", "*", "^a"],
        "px": ["*", "x", "x"],
    }
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert coverage.cell_columns == ("rx__pattern_0", "px__pattern_0")
    assert cell_sources(coverage) == {
        ("default", 1, 1): frozenset({"A", "B", "C"}),
        ("default", 1, 0): frozenset({"A"}),
        ("default", 0, 1): frozenset({"B"}),
    }
    assert rows(boundaries.pattern_conditions) == [
        ("px__pattern_0", "code", "prefix", "x"),
        ("rx__pattern_0", "code", "regex", "^a"),
    ]


def test_pattern_entries_carry_the_folded_outputs_of_their_sources(backend_name):
    fee = Aggregate(output_name="fee", contribution_column="fee", data_type=DataType.INT)
    metadata = metadata_of(dimension("d", M.REGEX), outputs=(fee,))
    data = {"rule_id": ["P1", "P2", "P3"], "d": ["^a", "^b", "*"], "fee": [5, 7, 1]}
    validated = validated_rules(backend_name, data, metadata)
    _, _, coverage = run_stages(validated)

    assert folded_outputs(validated, coverage, fee) == {
        ("default", 0, 0): 1,
        ("default", 0, 1): 8,
        ("default", 1, 0): 6,
        ("default", 1, 1): 13,
    }


def test_pattern_conditions_compose_with_a_value_dimension_on_the_same_field(backend_name):
    metadata = metadata_of(
        dimension("code", M.EXACT, rule_field="code_value"), dimension("rx", M.REGEX, rule_field="rx")
    )
    data = {
        "rule_id": ["X", "Y", "Z"],
        "code_value": ["EU", "US", "*"],
        "rx": ["^E", "*", "^E"],
    }
    _, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert coverage.cell_columns == ("code", "rx__pattern_0")
    # Rule identity survives: each cell keeps exactly the rules covering it.
    assert cell_sources(coverage) == {
        ("default", "EU", 1): frozenset({"X", "Z"}),
        ("default", "US", 1): frozenset({"Y", "Z"}),
        ("default", "US", 0): frozenset({"Y"}),
        ("default", "*", 1): frozenset({"Z"}),
    }


# ---------------------------------------------------------------------------
# Same backend operation as context patterns
# ---------------------------------------------------------------------------


def classified(backend_name, strategy, patterns, values):
    """Classify the named values of ``code`` against ``patterns`` of one pattern strategy."""
    metadata = metadata_of(dimension("rx", strategy, rule_field="rx"), dimension("code", M.EXACT, rule_field="code_value"))
    data = {
        "rule_id": [f"n{i}" for i in range(len(values))] + [f"p{i}" for i in range(len(patterns))],
        "code_value": [*values, *["*"] * len(patterns)],
        "rx": [*["*"] * len(values), *patterns],
    }
    boundaries, _, _ = run_stages(validated_rules(backend_name, data, metadata))
    return rows(
        classify_named_values(boundaries.named_values["code"], boundaries.pattern_conditions, "code"),
        "value",
        "column",
        "outcome",
    )


@pytest.mark.parametrize(
    ("strategy", "patterns", "matches"),
    [
        (M.REGEX, ["^E", "S$"], lambda value, pattern: re.search(pattern, value) is not None),
        (M.PREFIX, ["E", "U"], lambda value, pattern: value.startswith(pattern)),
        (M.SUFFIX, ["U", "S"], lambda value, pattern: value.endswith(pattern)),
        (M.CONTAINS, ["EU", "S"], lambda value, pattern: pattern in value),
    ],
    ids=["regex", "prefix", "suffix", "contains"],
)
def test_named_values_are_classified_with_the_operation_used_for_context_patterns(
    backend_name, strategy, patterns, matches
):
    values = ["EU", "US"]
    found = classified(backend_name, strategy, patterns, values)
    prefix = "rx__pattern_"
    by_value = {
        (value, int(column.removeprefix(prefix))): outcome
        for value, column, outcome in found
        if value != "*"
    }
    # Pattern ordinals follow the sorted patterns.
    expected = {
        (value, ordinal): 1 if matches(value, pattern) else -1
        for ordinal, pattern in enumerate(sorted(patterns))
        for value in values
    }
    assert by_value == expected
    assert set(by_value.values()) == {1, -1}
    # The wildcard piece is not a value: it is unknown (0) for every pattern.
    assert {outcome for value, _, outcome in found if value == "*"} == {0}


@pytest.mark.parametrize(
    ("strategy", "pattern"),
    [(M.REGEX, "^E.*U$"), (M.PREFIX, "EU"), (M.SUFFIX, "OPE"), (M.CONTAINS, "ROP")],
)
def test_classification_agrees_with_the_engine_context_ternary(backend_name, strategy, pattern):
    values = ["EUROPE", "EU", "AUSTRALIA"]
    found = classified(backend_name, strategy, [pattern], values)
    ours = {value: outcome for value, _, outcome in found if value != "*"}

    # The existing engine evaluates the same pattern against each context value.
    engine = ExpressionRulesEngine(
        rules=rules_relation(backend_name, {"rule_name": ["p"], "pat": [pattern]}).collect(),
        dimension_metadata=DimensionsMetadata(
            fields=(CODE,),
            dimensions=(dimension("pat", strategy, rule_field="pat"),),
        ),
    )
    for value in values:
        engine_ternary = relation(engine.explain({"code": value}).frame).to_dicts()[0]["__t_pat"]
        assert ours[value] == engine_ternary, (strategy, pattern, value)
    assert set(ours.values()) == {1, -1}, "the fixture must exercise both a match and a non-match"


# ---------------------------------------------------------------------------
# Authored TRUTHY and PRESENCE
# ---------------------------------------------------------------------------


def test_authored_truthy_values_lower_to_their_declared_outcomes(backend_name):
    metadata = metadata_of(dimension("agree", M.TRUTHY))
    data = {"rule_id": ["Y", "N", "M", "ANY"], "agree": ["yes", "no", "maybe", "*"]}
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert [(p.name, p.role, p.columns) for p in boundaries.plans] == [("agree", "outcome", ("agree",))]
    # maybe -> 0 and the wildcard cover every piece; yes -> 1 and no -> -1 cover their own.
    assert cell_sources(coverage) == {
        ("default", 0): frozenset({"M", "ANY"}),
        ("default", 1): frozenset({"Y", "M", "ANY"}),
        ("default", -1): frozenset({"N", "M", "ANY"}),
    }
    assert arrow_types(coverage.cell_coverage, "agree") == {"agree": "int8"}


def test_presence_lowers_is_null_to_1_is_not_null_to_minus_1_and_wildcard_to_0(backend_name):
    metadata = metadata_of(dimension("code_presence", M.PRESENCE))
    data = {"rule_id": ["null", "present", "any"], "code_presence": ["IS_NULL", "IS_NOT_NULL", "*"]}
    boundaries, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert cell_sources(coverage) == {
        ("default", 1): frozenset({"null", "any"}),
        ("default", -1): frozenset({"present", "any"}),
        ("default", 0): frozenset({"any"}),
    }
    assert arrow_types(coverage.cell_coverage, "code_presence") == {"code_presence": "int8"}


def test_authored_outcome_columns_are_stored_as_they_are(backend_name):
    field = ContextField(name="flag", data_type=DataType.INT, wildcard=None, wildcard_declared=True)
    metadata = metadata_of(dimension("flag_outcome", M.OUTCOME, field="flag"), fields=(field,))
    data = {"rule_id": ["neg", "zero", "pos"], "flag_outcome": [-1, 0, 1]}
    _, _, coverage = run_stages(validated_rules(backend_name, data, metadata))

    assert cell_sources(coverage) == {
        ("default", -1): frozenset({"neg", "zero"}),
        ("default", 0): frozenset({"zero"}),
        ("default", 1): frozenset({"zero", "pos"}),
    }


# ---------------------------------------------------------------------------
# Worked null rules R1-R3 (explicit-dont-care-sentinels.md)
# ---------------------------------------------------------------------------

REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")
VIP = ContextField(name="vip", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True)


def null_metadata():
    return DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION, VIP),
        dimensions=(
            dimension("region", M.EXACT, field="region"),
            dimension("region_presence", M.PRESENCE, field="region"),
            dimension("vip", M.EXACT, field="vip"),
        ),
    )


NULL_RULES = {
    "rule_id": ["R1", "R2", "R3"],
    "region": ["EU", "*", "*"],
    "region_presence": ["*", "IS_NULL", "IS_NOT_NULL"],
    "vip": [None, None, True],
}


def test_presence_and_value_dimensions_on_one_field_are_separate_columns(backend_name):
    boundaries, _, coverage = run_stages(validated_rules(backend_name, NULL_RULES, null_metadata()))

    assert coverage.cell_columns == ("region", "region_presence", "vip")
    assert [(p.name, type(p.family).__name__) for p in boundaries.plans] == [
        ("region", "UnorderedField"),
        ("region_presence", "TruthyField"),
        ("vip", "UnorderedField"),
    ]
    assert arrow_types(coverage.cell_coverage, "region_presence") == {"region_presence": "int8"}


def test_worked_null_rules_r1_to_r3_entries(backend_name):
    _, _, coverage = run_stages(validated_rules(backend_name, NULL_RULES, null_metadata()))

    # R1 EU / presence 0 / vip wildcard; R2 any / IS_NULL=1 / wildcard; R3 any / IS_NOT_NULL=-1 / true.
    assert cell_sources(coverage) == {
        ("default", "EU", 0, None): frozenset({"R1"}),
        ("default", "EU", 0, True): frozenset({"R1"}),
        ("default", "EU", 1, None): frozenset({"R1", "R2"}),
        ("default", "EU", 1, True): frozenset({"R1", "R2"}),
        ("default", "EU", -1, None): frozenset({"R1"}),
        ("default", "EU", -1, True): frozenset({"R1", "R3"}),
        ("default", "*", 1, None): frozenset({"R2"}),
        ("default", "*", 1, True): frozenset({"R2"}),
        ("default", "*", -1, True): frozenset({"R3"}),
    }
