"""Plan 2 Task 1: relational stages a-c, their natural keys, and the no-host-extraction rule."""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
import pytest
from mountainash.relations import Relation

from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table import fields, stages, strategies
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.decision_table.stages import (
    Boundaries,
    Coverage,
    Pieces,
    stage_a_boundaries,
    stage_b_pieces,
    stage_c_coverage,
    stage_key,
)

from tests.decision_table.construction_support import (
    captured_rules,
    cell_sources,
    rows,
    run_stages,
)

INT_MIN, INT_MAX = -2147483648, 2147483647

TERM = ContextField(name="term", data_type=DataType.INT, wildcard=None, wildcard_declared=True)
REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")


def metadata():
    return DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(TERM, REGION),
        dimensions=(
            Dimension(dimension_name="t_range", context_field="term", match_strategy=MatchStrategy.RANGE,
                      range_min_field="lo", range_max_field="hi"),
            Dimension(dimension_name="t_exact", context_field="term", match_strategy=MatchStrategy.EXACT),
            Dimension(dimension_name="t_min", context_field="term", match_strategy=MatchStrategy.MIN),
            Dimension(dimension_name="region", context_field="region", match_strategy=MatchStrategy.EXACT),
        ),
    )


RULES = {
    "rule_id": ["T1", "T2", "T3", "F1", "F2"],
    "scope": ["rates", "rates", "rates", "fees", "fees"],
    "lo": [1, None, None, None, None],
    "hi": [5, None, None, None, None],
    "t_exact": [None, 3, None, None, None],
    "t_min": [None, None, 10, None, None],
    "region": ["*", "*", "*", "EU", "*"],
}


def reversed_rules():
    return {name: list(reversed(values)) for name, values in RULES.items()}


@pytest.fixture
def staged(backend_name):
    validated = captured_rules(backend_name, RULES, metadata())
    boundaries = stage_a_boundaries(validated)
    pieces = stage_b_pieces(boundaries)
    return boundaries, pieces, stage_c_coverage(boundaries, pieces)


# ---------------------------------------------------------------------------
# Stage contracts
# ---------------------------------------------------------------------------


def test_stage_a_yields_cut_values_and_named_values_per_scope(staged):
    boundaries, _, _ = staged

    assert isinstance(boundaries, Boundaries)
    assert [(plan.name, plan.kind, plan.role, plan.columns) for plan in boundaries.plans] == [
        ("term", "interval", "positive", ("term__lo", "term__hi")),
        ("region", "value", "positive", ("region",)),
    ]
    # T1 -> {1, 6}, T2 -> {3, 4}, T3 -> {10}, plus the concrete minimum; fees only the minimum.
    assert rows(boundaries.cuts["term"]) == [
        ("fees", INT_MIN),
        ("rates", INT_MIN),
        ("rates", 1),
        ("rates", 3),
        ("rates", 4),
        ("rates", 6),
        ("rates", 10),
    ]
    assert rows(boundaries.named_values["region"]) == [
        ("fees", "*", True),
        ("fees", "EU", False),
        ("rates", "*", True),
    ]
    assert rows(boundaries.sources) == [
        ("fees", "F1"), ("fees", "F2"), ("rates", "T1"), ("rates", "T2"), ("rates", "T3"),
    ]
    assert rows(boundaries.pattern_conditions) == []


def test_stage_b_cuts_each_scope_into_closed_pieces_ending_at_the_type_bounds(staged):
    _, pieces, _ = staged

    assert isinstance(pieces, Pieces)
    assert rows(pieces.by_plan["term"]) == [
        ("fees", INT_MIN, INT_MAX),
        ("rates", INT_MIN, 0),
        ("rates", 1, 2),
        ("rates", 3, 3),
        ("rates", 4, 5),
        ("rates", 6, 9),
        ("rates", 10, INT_MAX),
    ]
    assert rows(pieces.by_plan["region"]) == rows(staged[0].named_values["region"])


def test_stage_c_yields_each_rules_coverage_per_column_and_its_covered_cells(staged):
    _, _, coverage = staged

    assert isinstance(coverage, Coverage)
    assert rows(coverage.by_plan["term"], "scope", "source_uuid", "term__lo", "term__hi") == [
        ("fees", "F1", INT_MIN, INT_MAX),
        ("fees", "F2", INT_MIN, INT_MAX),
        ("rates", "T1", 1, 2),
        ("rates", "T1", 3, 3),
        ("rates", "T1", 4, 5),
        ("rates", "T2", 3, 3),
        ("rates", "T3", 10, INT_MAX),
    ]
    assert rows(coverage.by_plan["region"]) == [
        ("fees", "F1", "EU"),
        ("fees", "F2", "*"),
        ("fees", "F2", "EU"),
        ("rates", "T1", "*"),
        ("rates", "T2", "*"),
        ("rates", "T3", "*"),
    ]
    assert coverage.cell_columns == ("term__lo", "term__hi", "region")
    assert cell_sources(coverage) == {
        ("fees", INT_MIN, INT_MAX, "EU"): frozenset({"F1", "F2"}),
        ("fees", INT_MIN, INT_MAX, "*"): frozenset({"F2"}),
        ("rates", 1, 2, "*"): frozenset({"T1"}),
        ("rates", 3, 3, "*"): frozenset({"T1", "T2"}),
        ("rates", 4, 5, "*"): frozenset({"T1"}),
        ("rates", 10, INT_MAX, "*"): frozenset({"T3"}),
    }


def test_natural_keys_name_scope_sorted_cut_or_value_and_rule_uuid(staged):
    boundaries, pieces, coverage = staged
    term, region = boundaries.plans

    assert stage_key("a", term) == ("scope", "__cut")
    assert stage_key("a", region) == ("scope", "value", "wildcard")
    assert stage_key("b", term) == ("scope", "term__lo")
    assert stage_key("b", region) == ("scope", "value", "wildcard")
    assert stage_key("c", term) == ("scope", "source_uuid", "term__lo", "term__hi")
    with pytest.raises(DecisionTableError):
        stage_key("d", term)

    # Each key identifies exactly one row at its stage.
    for stage, plan, relation in (
        ("a", term, boundaries.cuts["term"]),
        ("a", region, boundaries.named_values["region"]),
        ("b", term, pieces.by_plan["term"]),
        ("b", region, pieces.by_plan["region"]),
        ("c", term, coverage.by_plan["term"]),
        ("c", region, coverage.by_plan["region"]),
    ):
        key = list(stage_key(stage, plan))
        assert set(key) <= set(relation.columns)
        assert relation.count_rows() == relation.select(*key).unique().count_rows()


def test_stage_outputs_do_not_depend_on_rule_row_order(backend_name):
    forward = run_stages(captured_rules(backend_name, RULES, metadata()))
    backward = run_stages(captured_rules(backend_name, reversed_rules(), metadata()))

    assert rows(forward[0].cuts["term"]) == rows(backward[0].cuts["term"])
    assert rows(forward[1].by_plan["term"]) == rows(backward[1].by_plan["term"])
    assert rows(forward[2].cell_coverage) == rows(backward[2].cell_coverage)
    assert cell_sources(forward[2]) == cell_sources(backward[2])


def test_scopes_are_isolated(staged):
    _, pieces, coverage = staged
    fees_pieces = [r for r in rows(pieces.by_plan["term"]) if r[0] == "fees"]
    assert fees_pieces == [("fees", INT_MIN, INT_MAX)]
    assert {key[0]: set() for key in cell_sources(coverage)} == {"fees": set(), "rates": set()}
    assert all(
        uuid.startswith("F") == (key[0] == "fees")
        for key, uuids in cell_sources(coverage).items()
        for uuid in uuids
    )


def test_zero_rules_stage_to_empty_relations(backend_name):
    typed_empty = pl.DataFrame(
        {
            "rule_id": pl.Series([], dtype=pl.String),
            "scope": pl.Series([], dtype=pl.String),
            "lo": pl.Series([], dtype=pl.Int64),
            "hi": pl.Series([], dtype=pl.Int64),
            "t_exact": pl.Series([], dtype=pl.Int64),
            "t_min": pl.Series([], dtype=pl.Int64),
            "region": pl.Series([], dtype=pl.String),
        }
    )
    boundaries, pieces, coverage = run_stages(captured_rules(backend_name, typed_empty, metadata()))

    assert rows(boundaries.cuts["term"]) == []
    assert rows(pieces.by_plan["term"]) == []
    assert rows(coverage.cell_coverage) == []
    assert rows(boundaries.pattern_conditions) == []


def test_no_dimensions_covers_every_rule_with_the_single_empty_cell(backend_name):
    declared = DimensionsMetadata(uuid_column="rule_id", scope_column="scope", dimensions=())
    data = {"rule_id": ["a", "b"], "scope": ["s", "s"]}
    _, _, coverage = run_stages(captured_rules(backend_name, data, declared))

    assert coverage.cell_columns == ()
    assert cell_sources(coverage) == {("s",): frozenset({"a", "b"})}


def test_a_negation_column_named_like_a_positive_column_is_rejected(backend_name):
    clash = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION,),
        dimensions=(
            Dimension(dimension_name="region_pos", context_field="region", match_strategy=MatchStrategy.EXACT,
                      rule_field="region_pos"),
            Dimension(dimension_name="region", context_field="region", match_strategy=MatchStrategy.NOT_EQUAL),
        ),
    )
    data = {"rule_id": ["a"], "region_pos": ["EU"], "region": ["US"]}
    with pytest.raises(DecisionTableError, match="region"):
        stage_a_boundaries(captured_rules(backend_name, data, clash))


def test_reserved_table_columns_are_rejected(backend_name):
    reserved = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="scope", data_type=DataType.STR, wildcard="*"),),
        dimensions=(Dimension(dimension_name="scope", context_field="scope", match_strategy=MatchStrategy.EXACT),),
    )
    with pytest.raises(DecisionTableError, match="scope"):
        stage_a_boundaries(captured_rules(backend_name, {"rule_id": ["a"], "scope": ["x"]}, reserved))


# ---------------------------------------------------------------------------
# Construction never extracts source rule rows
# ---------------------------------------------------------------------------

HOST_EXTRACTION = ("to_dicts", "collect", "to_polars", "to_pandas", "to_pyarrow", "to_dict", "item", "to_tuples")


def guard_host_extraction(monkeypatch, *, allow_count: bool) -> list[str]:
    """Make host extraction raise; ``count_rows`` (and what it runs) is allowed once requested.

    Returns the list of ``count_rows`` calls observed.
    """
    originals = {name: getattr(Relation, name) for name in (*HOST_EXTRACTION, "count_rows")}
    inside_count = [0]
    counted: list[str] = []

    def forbidden(name):
        def spy(self, *args, **kwargs):
            if inside_count[0]:
                return originals[name](self, *args, **kwargs)
            raise AssertionError(f"Relation.{name} would extract rule data to the host")

        return spy

    def count_rows(self, *args, **kwargs):
        if not allow_count:
            raise AssertionError("Relation.count_rows executed during construction")
        counted.append("count_rows")
        inside_count[0] += 1
        try:
            return originals["count_rows"](self, *args, **kwargs)
        finally:
            inside_count[0] -= 1

    for name in HOST_EXTRACTION:
        monkeypatch.setattr(Relation, name, forbidden(name))
    monkeypatch.setattr(Relation, "count_rows", count_rows)
    return counted


def test_stages_a_to_c_execute_nothing_for_non_pattern_dimensions(backend_name, monkeypatch):
    validated = captured_rules(backend_name, RULES, metadata())
    guard_host_extraction(monkeypatch, allow_count=False)

    boundaries, pieces, coverage = run_stages(validated)

    assert coverage.cell_coverage is not None and pieces.by_plan and boundaries.plans


def test_pattern_dimensions_read_only_the_distinct_pattern_count(backend_name, monkeypatch):
    declared = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(REGION,),
        dimensions=(Dimension(dimension_name="rx", context_field="region", match_strategy=MatchStrategy.REGEX),),
    )
    validated = captured_rules(backend_name, {"rule_id": ["a", "b"], "rx": ["^a", "^b"]}, declared)
    counted = guard_host_extraction(monkeypatch, allow_count=True)

    run_stages(validated)

    assert counted == ["count_rows"]


@pytest.mark.parametrize("module", [fields, strategies, stages], ids=lambda module: module.__name__)
def test_construction_modules_never_extract_rows_or_import_a_backend(module):
    source = Path(module.__file__).read_text()
    forbidden_calls = re.findall(
        r"\.(to_dicts|collect|to_polars|to_pandas|to_pyarrow|to_arrow|to_dict|to_tuples|item)\(", source
    )
    forbidden_imports = re.findall(r"^\s*(?:import|from)\s+(polars|ibis|pyarrow|pandas|narwhals)\b", source, re.M)
    assert forbidden_calls == []
    assert forbidden_imports == []
