"""Plan 2 Task 4: contributor words, relational membership, and output folds."""

from __future__ import annotations

import math
import re
from pathlib import Path

import mountainash.expressions as ma
import polars as pl
import pytest
from mountainash.relations import concat

from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table import masks
from mountainash_rules.decision_table.masks import BITS_PER_WORD, bit_lookup
from mountainash_rules.decision_table.prevalidate import ValidatedRules
from mountainash_rules.decision_table.stages import (
    Coverage,
    stage_d_membership,
    stage_e_entry_sources,
)

from tests.decision_table.construction_support import (
    rows,
    run_stages,
    captured_rules,
)
from tests.decision_table.test_stages import guard_host_extraction

M = MatchStrategy
REGION = ContextField(name="region", data_type=DataType.STR, wildcard="*")
TERM = ContextField(name="term", data_type=DataType.INT, wildcard=None, wildcard_declared=True)


def fee(scope="s", name="fee", operation=AggregateOp.SUM, column="fee"):
    return Aggregate(scope=scope, output_name=name, contribution_column=column,
                     operation=operation, data_type=DataType.INT)


def region_metadata(*outputs):
    return DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(REGION,),
        dimensions=(Dimension(dimension_name="region", context_field="region", match_strategy=M.EXACT),),
        outputs=outputs or (fee(),),
    )


def term_metadata(*outputs):
    return DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(TERM,),
        dimensions=(
            Dimension(dimension_name="t_set", context_field="term", match_strategy=M.SET_MEMBERSHIP),
            Dimension(dimension_name="t_exact", context_field="term", match_strategy=M.EXACT),
            Dimension(dimension_name="t_range", context_field="term", match_strategy=M.RANGE,
                      range_min_field="lo", range_max_field="hi"),
        ),
        outputs=outputs or (fee(),),
    )


def term_frame(**rules):
    """``{uuid: dict(set=, exact=, range=, fee=, scope=)}`` as a typed rules frame."""
    uuids = list(rules)

    def column(key, index=None):
        values = [rules[u].get(key) for u in uuids]
        if index is not None:
            values = [None if v is None else v[index] for v in values]
        return pl.Series(values, dtype=pl.Int64)

    return pl.DataFrame(
        {
            "rule_id": uuids,
            "scope": [rules[u].get("scope", "s") for u in uuids],
            "t_set": pl.Series([rules[u].get("set") for u in uuids], dtype=pl.List(pl.Int64)),
            "t_exact": column("exact"),
            "lo": column("range", 0),
            "hi": column("range", 1),
            "fee": column("fee"),
        }
    )


def staged(backend, data, metadata):
    validated = captured_rules(backend, data, metadata)
    boundaries, pieces, coverage = run_stages(validated)
    membership = stage_d_membership(coverage)
    return validated, coverage, membership, stage_e_entry_sources(validated, membership)


def sources_by_id(entry_sources):
    """``{(scope, entry_sources_id): uuids}`` and the raw rows (to detect duplicate memberships)."""
    raw = rows(entry_sources, "scope", "entry_sources_id", "source_uuid")
    grouped: dict[tuple, set] = {}
    for scope, sid, uuid in raw:
        grouped.setdefault((scope, sid), set()).add(uuid)
    return {key: frozenset(uuids) for key, uuids in grouped.items()}, raw


def cell_membership(membership, entry_sources, *columns):
    """``{(scope, *cell values): contributor uuids}`` joined through ``entry_sources_id``."""
    by_id, _ = sources_by_id(entry_sources)
    return {
        (row[0], *row[1:-1]): by_id[(row[0], row[-1])]
        for row in rows(membership.cells, "scope", *columns, "entry_sources_id")
    }


def uuid(index):
    return f"R{index:03d}"


# ---------------------------------------------------------------------------
# The 63-row bit lookup
# ---------------------------------------------------------------------------


def test_bit_lookup_is_63_rows_of_exact_integer_powers_of_two():
    lookup = rows(bit_lookup())
    assert BITS_PER_WORD == 63
    assert lookup == [(index, int(f"1{'0' * index}", 2)) for index in range(63)]
    assert lookup[0] == (0, 1)
    assert lookup[62] == (62, 4611686018427387904)
    # Bit 63 would be the sign bit; it is never offered, and the lookup sums to int64's maximum.
    assert sum(value for _, value in lookup) == 2**63 - 1


def test_the_lookup_is_exact_integer_literals_not_exponentiation_or_host_bit_extraction():
    source = Path(masks.__file__).read_text()
    assert re.findall(r"\*\*|\bpow\(|\bmath\b|\bnumpy\b|\.bit_length|\bbin\(|\bfloat\b", source) == []
    assert re.findall(
        r"\.(to_dicts|collect|to_polars|to_pandas|to_pyarrow|to_arrow|to_dict|to_tuples|item)\(", source
    ) == []
    assert re.findall(r"^\s*(?:import|from)\s+(polars|ibis|pyarrow|pandas|narwhals)\b", source, re.M) == []


# ---------------------------------------------------------------------------
# Word counts and exact bits for N contributors
# ---------------------------------------------------------------------------

COUNTS = (62, 63, 64, 126, 127)


def expected_words(contributors, member_ordinals):
    """Python reference: ordinal ``n`` is bit ``n % 63`` of word ``n // 63``."""
    words = [0] * math.ceil(contributors / BITS_PER_WORD)
    for ordinal in member_ordinals:
        words[ordinal // BITS_PER_WORD] |= 1 << (ordinal % BITS_PER_WORD)
    return tuple(words)


@pytest.mark.parametrize("contributors", COUNTS)
def test_one_cell_covered_by_every_rule_has_ceil_n_over_63_full_words(backend_name, contributors):
    data = {
        "rule_id": [uuid(i) for i in range(contributors)],
        "scope": ["s"] * contributors,
        "region": ["*"] * contributors,
        "fee": [1] * contributors,
    }
    _, _, membership, result = staged(backend_name, data, region_metadata())

    assert membership.words == math.ceil(contributors / 63)
    assert len(membership.word_columns) == membership.words
    [(scope, region, *words)] = rows(membership.cell_words, "scope", "region", *membership.word_columns)
    assert (scope, region) == ("s", "*")
    assert tuple(words) == expected_words(contributors, range(contributors))
    # Bit 62 is the highest used bit of a full word; the sign bit is unused: no word is negative.
    assert all(0 <= word < 2**63 for word in words)
    if contributors >= 63:
        assert words[0] == 2**63 - 1
    assert result.entry_sources.columns == ["scope", "entry_sources_id", "source_uuid"]
    assert sources_by_id(result.entry_sources)[0] == {("s", 0): frozenset(uuid(i) for i in range(contributors))}
    assert rows(result.outputs, "scope", "entry_sources_id", "fee") == [("s", 0, contributors)]


@pytest.mark.parametrize("contributors", COUNTS)
def test_every_rule_sets_exactly_its_own_bit_including_first_and_last_of_every_word(backend_name, contributors):
    data = {
        "rule_id": [uuid(i) for i in range(contributors)],
        "scope": ["s"] * contributors,
        "region": [f"v{i:03d}" for i in range(contributors)],
        "fee": [1] * contributors,
    }
    _, _, membership, result = staged(backend_name, data, region_metadata())

    assert membership.words == math.ceil(contributors / 63)
    cells = {
        region: tuple(words)
        for _, region, *words in rows(membership.cell_words, "scope", "region", *membership.word_columns)
    }
    assert cells == {f"v{i:03d}": expected_words(contributors, [i]) for i in range(contributors)}
    for last_of_word in (62, 63, 125, 126):
        if last_of_word < contributors:
            words = cells[f"v{last_of_word:03d}"]
            assert sum(1 for word in words if word) == 1
    if contributors > 62:
        assert 2**62 in cells["v062"]  # bit 62: the highest used bit of word 0
    if contributors > 63:
        assert cells["v063"][1] == 1  # bit 0 of word 1
    # Membership decoded from the bits recovers the rule of every cell.
    assert cell_membership(membership, result.entry_sources, "region") == {
        ("s", f"v{i:03d}"): frozenset({uuid(i)}) for i in range(contributors)
    }


def test_rule_ordinals_follow_sorted_natural_uuids_not_row_order(backend_name):
    forward = {"rule_id": ["B", "C", "A"], "scope": ["s"] * 3, "region": ["*"] * 3, "fee": [1, 2, 3]}
    reverse = {name: list(reversed(values)) for name, values in forward.items()}
    results = []
    for data in (forward, reverse):
        _, _, membership, result = staged(backend_name, data, region_metadata())
        ordinals = rows(membership.ordinals, "scope", "source_uuid", "__word_index", "__word_bit")
        results.append((ordinals, rows(membership.cell_words), rows(result.entry_sources)))
    assert results[0] == results[1]
    assert results[0][0] == [("s", "A", 0, 0), ("s", "B", 0, 1), ("s", "C", 0, 2)]
    assert results[0][1] == [("s", "*", 0b111)]


# ---------------------------------------------------------------------------
# Relational membership
# ---------------------------------------------------------------------------


def test_a_wildcard_reaching_a_cell_by_several_derivations_occurs_once(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(REGION, TERM),
        dimensions=(
            Dimension(dimension_name="region", context_field="region", match_strategy=M.EXACT),
            Dimension(dimension_name="t_range", context_field="term", match_strategy=M.RANGE,
                      range_min_field="lo", range_max_field="hi"),
        ),
        outputs=(fee(),),
    )
    data = {
        "rule_id": ["E1", "E2", "W"],
        "scope": ["s"] * 3,
        "region": ["EU", "US", "*"],
        "lo": [1, 3, None],
        "hi": [2, 4, None],
        "fee": [1, 2, 4],
    }
    _, coverage, membership, result = staged(backend_name, data, metadata)

    by_id, raw = sources_by_id(result.entry_sources)
    assert len(raw) == len(set(raw))  # no duplicate (scope, entry_sources_id, source_uuid)
    cells = cell_membership(membership, result.entry_sources, "region", "term__lo", "term__hi")
    wildcard_cells = {key for key, members in cells.items() if "W" in members}
    assert wildcard_cells == set(cells)  # W reaches every covered cell, one membership per cell
    assert cells[("s", "EU", 1, 2)] == frozenset({"E1", "W"})
    assert cells[("s", "US", 3, 4)] == frozenset({"E2", "W"})
    assert cells[("s", "*", 3, 4)] == frozenset({"W"})
    assert rows(result.outputs, "scope", "entry_sources_id", "fee") == [
        ("s", sid, sum({"E1": 1, "E2": 2, "W": 4}[u] for u in members)) for (_, sid), members in sorted(by_id.items())
    ]


def test_duplicate_coverage_rows_contribute_once(backend_name):
    data = {"rule_id": ["A", "B"], "scope": ["s"] * 2, "region": ["*", "x"], "fee": [4, 7]}
    validated = captured_rules(backend_name, data, region_metadata())
    _, _, coverage = run_stages(validated)
    doubled = Coverage(
        by_plan=coverage.by_plan,
        cell_coverage=concat([coverage.cell_coverage, coverage.cell_coverage]),
        cell_columns=coverage.cell_columns,
    )
    once, twice = stage_d_membership(coverage), stage_d_membership(doubled)

    assert rows(once.cell_words) == rows(twice.cell_words)
    assert rows(stage_e_entry_sources(validated, once).entry_sources) == rows(
        stage_e_entry_sources(validated, twice).entry_sources
    )
    assert rows(stage_e_entry_sources(validated, twice).outputs, "scope", "entry_sources_id", "fee") == [
        ("s", 0, 4),
        ("s", 1, 11),
    ]


def test_distinct_rules_with_equal_outputs_keep_distinct_memberships(backend_name):
    data = {"rule_id": ["A", "B"], "scope": ["s"] * 2, "region": ["x", "y"], "fee": [5, 5]}
    _, _, membership, result = staged(backend_name, data, region_metadata())

    by_id, _ = sources_by_id(result.entry_sources)
    assert by_id == {("s", 0): frozenset({"A"}), ("s", 1): frozenset({"B"})}
    assert rows(result.outputs, "scope", "entry_sources_id", "fee") == [("s", 0, 5), ("s", 1, 5)]
    assert cell_membership(membership, result.entry_sources, "region") == {
        ("s", "x"): frozenset({"A"}),
        ("s", "y"): frozenset({"B"}),
    }


def test_repeated_membership_on_separated_cells_shares_an_entry_sources_id(backend_name):
    data = term_frame(T1={"range": (1, 5), "fee": 2}, T2={"exact": 3, "fee": 7})
    _, _, membership, result = staged(backend_name, data, term_metadata())

    ids = {
        (lo, hi): sid
        for _, lo, hi, sid in rows(membership.cells, "scope", "term__lo", "term__hi", "entry_sources_id")
    }
    assert set(ids) == {(1, 2), (3, 3), (4, 5)}
    assert ids[(1, 2)] == ids[(4, 5)] != ids[(3, 3)]
    by_id, raw = sources_by_id(result.entry_sources)
    assert by_id[("s", ids[(1, 2)])] == frozenset({"T1"})
    assert by_id[("s", ids[(3, 3)])] == frozenset({"T1", "T2"})
    assert len(raw) == 3  # the shared membership is stored once
    assert len(rows(membership.word_ids)) == 2


def test_the_same_words_in_different_scopes_have_scope_local_ids(backend_name):
    data = {
        "rule_id": ["A1", "A2", "B1", "B2"],
        "scope": ["a", "a", "b", "b"],
        "region": ["*", "x", "x", "x"],
        "fee": [1, 1, 1, 1],
    }
    metadata = region_metadata(fee("a"), fee("b"))
    _, _, membership, result = staged(backend_name, data, metadata)

    words = {
        (scope, region): tuple(rest)
        for scope, region, *rest in rows(membership.cell_words, "scope", "region", *membership.word_columns)
    }
    # Scope a: {A1} = 0b01 and {A1, A2} = 0b11.  Scope b: {B1, B2} = 0b11 only.
    assert words == {("a", "*"): (1,), ("a", "x"): (3,), ("b", "x"): (3,)}
    ids = {(scope, region): sid for scope, region, sid in rows(membership.cells, "scope", "region", "entry_sources_id")}
    assert ids == {("a", "*"): 0, ("a", "x"): 1, ("b", "x"): 0}
    assert sources_by_id(result.entry_sources)[0] == {
        ("a", 0): frozenset({"A1"}),
        ("a", 1): frozenset({"A1", "A2"}),
        ("b", 0): frozenset({"B1", "B2"}),
    }


def test_scopes_of_different_sizes_share_the_widest_scopes_word_columns(backend_name):
    wide = 64
    data = {
        "rule_id": [uuid(i) for i in range(wide)] + ["S1", "S2"],
        "scope": ["wide"] * wide + ["small"] * 2,
        "region": ["*"] * (wide + 2),
        "fee": [1] * (wide + 2),
    }
    metadata = region_metadata(fee("wide"), fee("small"))
    _, _, membership, result = staged(backend_name, data, metadata)

    assert membership.words == 2
    assert rows(membership.cell_words, "scope", "region", *membership.word_columns) == [
        ("small", "*", 3, 0),
        ("wide", "*", 2**63 - 1, 1),
    ]
    by_id, _ = sources_by_id(result.entry_sources)
    assert by_id == {
        ("small", 0): frozenset({"S1", "S2"}),
        ("wide", 0): frozenset(uuid(i) for i in range(wide)),
    }


# ---------------------------------------------------------------------------
# Output folds
# ---------------------------------------------------------------------------

TOTALS = (
    fee(name="total"),
    fee(name="low", operation=AggregateOp.MIN),
    fee(name="high", operation=AggregateOp.MAX),
)


def cell_outputs(membership, result, *names):
    """``{cell: (output values...)}`` joining each cell to its contributor set's fold."""
    joined = membership.cells.join(result.outputs, on=["scope", "entry_sources_id"], how="inner")
    return {
        (row[0], row[1], row[2]): tuple(row[3:])
        for row in rows(joined, "scope", "term__lo", "term__hi", *names)
    }


def test_sum_min_and_max_fold_the_contributors_of_a_single_cell(backend_name):
    data = term_frame(A={"exact": 3, "fee": 4}, B={"exact": 3, "fee": 7})
    _, _, membership, result = staged(backend_name, data, term_metadata(*TOTALS))

    assert result.output_names == ("total", "low", "high")
    assert cell_membership(membership, result.entry_sources, "term__lo", "term__hi") == {
        ("s", 3, 3): frozenset({"A", "B"})
    }
    assert cell_outputs(membership, result, "total", "low", "high") == {("s", 3, 3): (11, 4, 7)}


def test_a_contributor_counts_once_in_each_of_two_separated_cells(backend_name):
    # A covers [1,1] and [5,5]; B covers [1,1]: A=4 and B=7 meet only in the first cell.
    data = term_frame(A={"set": [1, 5], "fee": 4}, B={"exact": 1, "fee": 7})
    _, _, membership, result = staged(backend_name, data, term_metadata(*TOTALS))

    assert cell_outputs(membership, result, "total", "low", "high") == {
        ("s", 1, 1): (11, 4, 7),
        ("s", 5, 5): (4, 4, 4),  # A once, not twice
    }
    # Two contributor sets, so two folded rows; nothing is manufactured for the uncovered
    # [2,4] or outside the covered pieces.
    assert rows(result.outputs, "scope", "entry_sources_id", "total") == [("s", 0, 4), ("s", 1, 11)]
    assert rows(membership.cells, "term__lo", "term__hi") == [(1, 1), (5, 5)]


def test_folds_exist_only_for_covered_contributor_sets(backend_name):
    data = term_frame(A={"range": (1, 2), "fee": 4}, B={"range": (10, 11), "fee": 7})
    _, _, membership, result = staged(backend_name, data, term_metadata(*TOTALS))

    assert rows(result.outputs, "scope", "entry_sources_id", "total", "low", "high") == [
        ("s", 0, 4, 4, 4),
        ("s", 1, 7, 7, 7),
    ]
    assert cell_outputs(membership, result, "total") == {("s", 1, 2): (4,), ("s", 10, 11): (7,)}


def test_outputs_are_folded_per_scope_with_null_for_outputs_a_scope_does_not_declare(backend_name):
    metadata = region_metadata(
        fee("s1"),
        fee("s2", operation=AggregateOp.MAX),
        fee("s2", name="rate", operation=AggregateOp.MIN, column="rate"),
    )
    data = pl.DataFrame(
        {
            "rule_id": ["X1", "X2", "Y1", "Y2"],
            "scope": ["s1", "s1", "s2", "s2"],
            "region": ["*"] * 4,
            "fee": [3, 4, 5, 8],
            "rate": [1, 1, 9, 2],
        }
    )
    _, _, membership, result = staged(backend_name, data, metadata)

    assert result.output_names == ("fee", "rate")
    assert rows(result.outputs, "scope", "entry_sources_id", "fee", "rate") == [
        ("s1", 0, 7, None),
        ("s2", 0, 8, 2),
    ]


def test_contributor_sets_are_unique_per_scope_and_folds_align_with_entry_sources(backend_name):
    data = term_frame(
        A={"set": [1, 5], "fee": 4},
        B={"exact": 1, "fee": 7},
        C={"exact": 5, "fee": 1, "scope": "t"},
    )
    metadata = term_metadata(fee("s"), fee("t"))
    _, _, membership, result = staged(backend_name, data, metadata)

    by_id, raw = sources_by_id(result.entry_sources)
    assert len(raw) == len(set(raw))
    outputs = rows(result.outputs, "scope", "entry_sources_id", "fee")
    assert [(scope, sid) for scope, sid, _ in outputs] == sorted(by_id)
    assert {(scope, sid): total for scope, sid, total in outputs} == {
        ("s", 0): 4,
        ("s", 1): 11,
        ("t", 0): 1,
    }


# ---------------------------------------------------------------------------
# No contributors, and no host extraction
# ---------------------------------------------------------------------------


def test_no_contributors_gives_no_words_and_empty_membership(backend_name):
    data = {"rule_id": ["A"], "scope": ["s"], "region": ["x"], "fee": [1]}
    validated = captured_rules(backend_name, data, region_metadata())
    empty = ValidatedRules(validated.rules.filter(ma.lit(False)), validated.source_metadata, ())
    _, _, coverage = run_stages(empty)

    membership = stage_d_membership(coverage)
    result = stage_e_entry_sources(empty, membership)

    assert membership.words == 0 and membership.word_columns == ()
    assert rows(membership.cell_words) == [] and rows(membership.cells) == []
    assert rows(result.entry_sources) == [] and rows(result.outputs) == []
    assert result.entry_sources.columns == ["scope", "entry_sources_id", "source_uuid"]
    assert result.outputs.columns == ["scope", "entry_sources_id", "fee"]


def test_stages_d_and_e_read_only_the_word_count(backend_name, monkeypatch):
    data = {"rule_id": ["A", "B"], "scope": ["s"] * 2, "region": ["*", "x"], "fee": [4, 7]}
    validated = captured_rules(backend_name, data, region_metadata())
    _, _, coverage = run_stages(validated)
    counted = guard_host_extraction(monkeypatch, allow_count=True)

    membership = stage_d_membership(coverage)
    result = stage_e_entry_sources(validated, membership)

    assert counted == ["count_rows"]
    assert result.entry_sources is not None and result.outputs is not None
