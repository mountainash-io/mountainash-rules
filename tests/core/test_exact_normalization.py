"""Source-authoritative normalization-2; research inputs, production predicates."""

from __future__ import annotations

import datetime as dt
import itertools
import json
from pathlib import Path

import pytest

from mountainash_rules import DomainField, ExactLimits
from mountainash_rules.core.codec import canonical_bytes, content_id
from mountainash_rules.core.contracts import OperationBudget
from mountainash_rules.core.normalization import (
    Fragment,
    covered_overlay,
    finalize_regions,
)
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.reasoner import Reasoner

_DATA = json.loads(
    (Path(__file__).parents[1] / "fixtures/exact_normalization.json").read_text()
)
_NATIVE = json.loads(
    (Path(__file__).parents[1] / "fixtures/exact_normalization_native.json").read_text()
)["fixtures"]
_IDS = tuple(f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 5))


def _budget():
    return OperationBudget(
        ExactLimits(
            language=dict(
                max_input_bytes=1_000_000,
                max_nesting=64,
                max_nfa_states=4096,
                max_states=4096,
                max_transitions=65536,
                max_work=1_000_000,
            ),
            max_input_bytes=100_000_000,
            max_output_bytes=100_000_000,
            max_work=10**15,
            max_live_bytes=10**9,
            max_predicate_nodes=1_000_000,
            max_dfa_states=1_000_000,
            max_dfa_transitions=10_000_000,
            max_theory_states=1_000_000,
            max_regions=1_000_000,
            max_scopes=1_000_000,
            max_source_scope_edges=1_000_000,
            max_contributor_edges=1_000_000,
            max_word_rows=1_000_000,
            max_numeric_bits=1_000_000,
            max_witnesses=1_000_000,
        ),
        "normalization-test",
    )


def _value(spec, value):
    if spec[0] == "rank" and spec[1] == "date":
        return dt.date(2026, 1, 1) + dt.timedelta(days=value)
    if spec[0] == "rank" and spec[1] == "datetime":
        return dt.datetime(2026, 1, 1) + dt.timedelta(microseconds=value)
    return value


def _case(fixture):
    fields = [
        DomainField(
            name=name,
            data_type=spec[1] if spec[0] != "str" else "str",
            **({"timezone": "naive"} if spec[:2] == ["rank", "datetime"] else {}),
        )
        for name, spec in sorted(fixture["fields"].items())
    ]
    graph = PredicateGraph(fields, budget=_budget())

    def lower(node):
        op = node[0]
        if op in ("true", "false"):
            return getattr(graph, op)
        if op in ("and", "or"):
            return getattr(graph, op + "_")(*(lower(child) for child in node[1:]))
        if op == "not":
            return graph.not_(lower(node[1]))
        if op == "interval":
            field = node[1]
            spec = fixture["fields"][field]
            return graph.interval(
                field,
                _value(spec, node[2]),
                _value(spec, node[3]),
                lower_closed=True,
                upper_closed=True,
            )
        if op == "in":
            spec = fixture["fields"][node[1]]
            return graph.in_(node[1], [_value(spec, value) for value in node[2]])
        if op == "compare":
            return graph.compare(node[1], node[3], node[2])
        if op == "language":
            identifier = graph.add_language(_DATA["languages"][node[2]])
            return graph.language(node[1], identifier)
        raise AssertionError(f"Unknown approved fixture operator: {op}")

    bounds = []
    for name, spec in fixture["fields"].items():
        if spec[0] == "rank":
            bounds.append(
                graph.interval(
                    name,
                    _value(spec, spec[2]),
                    _value(spec, spec[3]),
                    lower_closed=True,
                    upper_closed=True,
                )
            )
        elif spec[0] == "finite":
            bounds.append(graph.in_(name, spec[2]))
    domain = graph.and_(*bounds, lower(fixture["domain"]))
    return (
        graph,
        Reasoner(graph),
        domain,
        {sid: lower(node) for sid, node in fixture["sources"].items()},
    )


def _snapshot(graph, cells):
    partition = {"routing_id": "routing:1:" + "a" * 64, "key_values": []}
    output = []
    for cell in cells:
        members = sorted(cell.contributors)
        membership_id = content_id(
            "contributor-set", {"schema_version": 1, "source_ids": members}
        )
        identifier = content_id(
            "cell",
            {
                "cell_schema": 1,
                "ruleset_id": "approved-fixtures",
                "partition_identity": partition,
                "predicate_id": cell.predicate_id,
                "contributor_set_id": membership_id,
            },
        )
        output.append(
            (
                identifier,
                cell.predicate_id,
                tuple(members),
                canonical_bytes(dict(graph.nodes[cell.predicate_id])),
            )
        )
    return tuple(sorted(output))


def _native_snapshot(graph, cells, domain, sources):
    partition = {"routing_id": "routing:1:" + "a" * 64, "key_values": []}
    cell_records = []
    memberships = {}
    pending = [domain, *sources.values(), *(cell.predicate_id for cell in cells)]
    predicates = {}
    languages = {}
    for cell in cells:
        membership = {"schema_version": 1, "source_ids": sorted(cell.contributors)}
        membership_id = content_id("contributor-set", membership)
        memberships[membership_id] = {"id": membership_id, "payload": membership}
        payload = {
            "cell_schema": 1,
            "ruleset_id": "approved-fixtures",
            "partition_identity": partition,
            "predicate_id": cell.predicate_id,
            "contributor_set_id": membership_id,
        }
        cell_records.append({"id": content_id("cell", payload), "payload": payload})
    while pending:
        identifier = pending.pop()
        if identifier in predicates:
            continue
        node = graph.nodes[identifier]
        payload = json.loads(canonical_bytes({"schema_version": 1, "node": node}))
        predicates[identifier] = {"id": identifier, "payload": payload}
        if node["op"] in ("and", "or"):
            pending.extend(node["args"])
        elif node["op"] == "not":
            pending.append(node["arg"])
        elif node["op"] == "language":
            language_id = node["language_id"]
            languages[language_id] = {
                "id": language_id,
                "payload": json.loads(
                    graph.languages[language_id].to_json(limits=graph.language_limits)
                ),
            }
    return {
        "domain_id": domain,
        "sources": dict(sorted(sources.items())),
        "cells": sorted(cell_records, key=lambda item: item["id"]),
        "memberships": [memberships[key] for key in sorted(memberships)],
        "predicates": [predicates[key] for key in sorted(predicates)],
        "languages": [languages[key] for key in sorted(languages)],
    }


def _split_fragments(graph, reasoner, fragments, axis_offset):
    result = []
    fields = sorted(
        graph.fields,
        key=lambda name: (
            not (
                graph.fields[name].data_type.is_numeric
                or graph.fields[name].data_type.is_temporal
            ),
            name,
        ),
    )
    fields = fields[axis_offset % len(fields) :] + fields[: axis_offset % len(fields)]
    for fragment in fragments:
        example = reasoner.witness(fragment.predicate_id)
        assert example is not None
        for field in fields:
            splitter = graph.eq(field, example[field])
            remainder = graph.and_(fragment.predicate_id, graph.not_(splitter))
            if not reasoner.is_empty(remainder):
                selected = graph.and_(fragment.predicate_id, splitter)
                assert not reasoner.is_empty(selected)
                result.extend(
                    (
                        Fragment(selected, fragment.contributors),
                        Fragment(remainder, fragment.contributors),
                    )
                )
                break
        else:
            # Some approved cases consist entirely of intrinsic singletons;
            # prove that no physical coordinate can split this fragment.
            point = graph.and_(*(graph.eq(field, example[field]) for field in fields))
            assert reasoner.equivalent(fragment.predicate_id, point)
            result.append(fragment)
    return tuple(result)


@pytest.mark.parametrize(
    "fixture", _DATA["fixtures"], ids=lambda fixture: fixture["name"]
)
def test_all_approved_general_construction_histories(fixture):
    graph, reasoner, domain, sources = _case(fixture)
    baseline = None
    subdivision = 0
    for history in fixture["histories"]:
        overlay = covered_overlay(reasoner, domain, sources, order=history["order"])
        fragments = overlay.fragments
        if history["fragmentation"] == "duplicates":
            fragments = fragments + fragments
        elif history["fragmentation"] == "subdivision":
            fragments = _split_fragments(graph, reasoner, fragments, subdivision)
            subdivision += 1
        cells = finalize_regions(reasoner, overlay, fragments=fragments)
        snapshot = _snapshot(graph, cells)
        assert (
            _native_snapshot(graph, cells, domain, sources) == _NATIVE[fixture["name"]]
        )
        if baseline is None:
            baseline = snapshot
        assert snapshot == baseline
        assert len(cells) == fixture["expected_cell_count"]
        assert (
            _snapshot(
                graph,
                finalize_regions(
                    reasoner,
                    overlay,
                    fragments=tuple(
                        Fragment(c.predicate_id, c.contributors) for c in cells
                    ),
                ),
            )
            == baseline
        )

    # Full source authority, not a count-only or sampled coverage assertion.
    for source, predicate in sources.items():
        covered = graph.or_(
            *(cell.predicate_id for cell in cells if source in cell.contributors)
        )
        assert reasoner.equivalent(covered, graph.and_(domain, predicate))
    for left, right in itertools.combinations(cells, 2):
        assert reasoner.is_empty(graph.and_(left.predicate_id, right.predicate_id))

    if overlay.fragments:
        with pytest.raises(ValueError):
            finalize_regions(reasoner, overlay, fragments=overlay.fragments[1:])
        invalid = (Fragment(overlay.fragments[0].predicate_id, frozenset({_IDS[3]})),)
        with pytest.raises(ValueError):
            finalize_regions(reasoner, overlay, fragments=invalid)


def test_pure_rank_equality_stays_one_compact_cell_over_large_domain():
    for upper in (1, 10**12):
        fixture = {
            "fields": {"x": ["rank", "int", 0, upper], "y": ["rank", "int", 0, upper]},
            "domain": ["true"],
            "sources": {_IDS[0]: ["compare", "x", "eq", "y"]},
        }
        graph, reasoner, domain, sources = _case(fixture)
        overlay = covered_overlay(reasoner, domain, sources)
        cells = finalize_regions(reasoner, overlay)
        assert len(cells) == 1
        assert reasoner.equivalent(
            cells[0].predicate_id, graph.and_(domain, sources[_IDS[0]])
        )
        assert len(graph.nodes) < 100


def test_numeric_face_components_do_not_connect_diagonals():
    fixture = {
        "fields": {"x": ["rank", "int", 0, 1], "y": ["rank", "int", 0, 1]},
        "domain": ["true"],
        "sources": {
            _IDS[0]: ["true"],
            _IDS[1]: ["and", ["interval", "x", 0, 0], ["interval", "y", 1, 1]],
            _IDS[2]: ["and", ["interval", "x", 1, 1], ["interval", "y", 0, 0]],
        },
    }
    graph, reasoner, domain, sources = _case(fixture)
    cells = finalize_regions(reasoner, covered_overlay(reasoner, domain, sources))
    assert len(cells) == 4
    assert (
        len([cell for cell in cells if cell.contributors == frozenset({_IDS[0]})]) == 2
    )


@pytest.mark.parametrize(
    "fixture", _DATA["fixtures"], ids=lambda fixture: fixture["name"]
)
def test_approved_cells_match_independent_typed_finite_oracle(fixture):
    """Finite applicability controls supplement, never replace, exact proofs."""
    import operator
    import struct

    graph, reasoner, domain, sources = _case(fixture)
    cells = finalize_regions(reasoner, covered_overlay(reasoner, domain, sources))

    def language_accepts(identifier, text):
        payload = _DATA["languages"][identifier]
        state = payload["start"]
        for character in text:
            codepoint = ord(character)
            state = next(
                target
                for source, low, high, target in payload["transitions"]
                if source == state and low <= codepoint <= high
            )
        return state in payload["accepting"]

    def scalar(payload):
        kind, value = payload["type"], payload["value"]
        if kind == "int":
            return int(value)
        if kind == "float":
            return struct.unpack(">d", bytes.fromhex(value))[0]
        if kind == "date":
            return dt.date.fromisoformat(value)
        if kind == "datetime":
            return dt.datetime.fromisoformat(value)
        return value

    def authored(node, point):
        op = node[0]
        if op in {"true", "false"}:
            return op == "true"
        if op in {"and", "or"}:
            return (all if op == "and" else any)(
                authored(child, point) for child in node[1:]
            )
        if op == "not":
            return not authored(node[1], point)
        if op == "interval":
            spec = fixture["fields"][node[1]]
            return _value(spec, node[2]) <= point[node[1]] <= _value(spec, node[3])
        if op == "in":
            spec = fixture["fields"][node[1]]
            return point[node[1]] in [_value(spec, value) for value in node[2]]
        if op == "compare":
            return getattr(operator, node[2])(point[node[1]], point[node[3]])
        if op == "language":
            return language_accepts(node[2], point[node[1]])
        raise AssertionError(op)

    def compiled(identifier, point):
        node = graph.nodes[identifier]
        op = node["op"]
        if op in {"true", "false"}:
            return op == "true"
        if op in {"and", "or"}:
            return (all if op == "and" else any)(
                compiled(child, point) for child in node["args"]
            )
        if op == "not":
            return not compiled(node["arg"], point)
        if op == "compare":
            return getattr(operator, node["relation"])(
                point[node["left"]], point[node["right"]]
            )
        value = point[node["field"]]
        if op == "eq":
            return value == scalar(node["value"])
        if op == "in":
            return value in [scalar(item) for item in node["values"]]
        if op == "language":
            return language_accepts(node["language_id"], value)
        if op == "interval":
            lower, upper = node["lower"], node["upper"]
            return (
                lower is None
                or (
                    value >= scalar(lower)
                    if node["lower_closed"]
                    else value > scalar(lower)
                )
            ) and (
                upper is None
                or (
                    value <= scalar(upper)
                    if node["upper_closed"]
                    else value < scalar(upper)
                )
            )
        raise AssertionError(op)

    choices = []
    for field in graph.fields:
        spec = fixture["fields"][field]
        if spec[0] == "rank":
            lower, upper = spec[2:4]
            values = (
                range(lower - 1, upper + 2)
                if upper - lower < 20
                else (
                    lower - 1,
                    lower,
                    lower + 1,
                    (lower + upper) // 2,
                    upper - 1,
                    upper,
                    upper + 1,
                )
            )
            choices.append(tuple(_value(spec, value) for value in values))
        elif spec[0] == "finite":
            choices.append(tuple(spec[2]) + (("_outside",) if spec[1] == "str" else ()))
        else:
            choices.append(
                (
                    "",
                    "a",
                    "ab",
                    "abc",
                    "bc",
                    "cat",
                    "dog",
                    "abx",
                    "x",
                    "é",
                    "a.b",
                    "cab",
                )
            )
    for values in itertools.product(*choices):
        point = dict(zip(graph.fields, values))
        admitted = authored(fixture["domain"], point)
        for field, spec in fixture["fields"].items():
            if spec[0] == "rank":
                admitted &= (
                    _value(spec, spec[2]) <= point[field] <= _value(spec, spec[3])
                )
            elif spec[0] == "finite":
                admitted &= point[field] in spec[2]
        expected = frozenset(
            source
            for source, predicate in fixture["sources"].items()
            if admitted and authored(predicate, point)
        )
        matches = [cell for cell in cells if compiled(cell.predicate_id, point)]
        assert len(matches) == bool(expected), point
        if matches:
            assert matches[0].contributors == expected, point


def test_numeric_box_recognition_reserves_its_wide_frontier():
    from mountainash_rules.core.contracts import ExactResourceError
    from mountainash_rules.core.normalization import _as_box

    fields = tuple(f"x{index:02}" for index in range(10))
    budget = _budget()
    graph = PredicateGraph(
        [DomainField(name=name, data_type="int") for name in fields], budget=budget
    )
    predicate = graph.and_(*(graph.eq(name, 0) for name in fields))
    used = budget.check("max_live_bytes", 0, phase="probe", units="bytes")
    budget.reserve(
        "max_live_bytes",
        budget.limits.max_live_bytes - used - 1024,
        phase="other-owned-data",
        units="bytes",
    )
    with pytest.raises(ExactResourceError) as failure:
        _as_box(graph, predicate, fields)
    assert failure.value.counter == "max_live_bytes"


@pytest.mark.parametrize(
    ("a", "b", "upper", "extruded"),
    [
        ((0.0, 10.0), (5.0, 15.0), 15.0, False),
        ((0.0, 20.0), (5.0, 10.0), 20.0, False),
        ((0.0, 20.0), (5.0, 10.0), 20.0, True),
        ((0.0, 10.0), (10.0, 20.0), 20.0, False),
    ],
    ids=("G01-overlap", "G02-nesting", "G02-extruded", "G03-touching"),
)
def test_binary64_overlap_nesting_and_touching_have_exact_canonical_cells(
    a, b, upper, extruded
):
    fields = [DomainField(name="x", data_type="float")]
    if extruded:
        fields.append(DomainField(name="y", data_type="float"))
    graph = PredicateGraph(fields, budget=_budget())
    reasoner = Reasoner(graph)
    extra = (
        graph.interval("y", 0.0, upper, lower_closed=True, upper_closed=True)
        if extruded
        else graph.true
    )
    domain = graph.and_(
        graph.interval("x", 0.0, upper, lower_closed=True, upper_closed=True), extra
    )
    sources = {
        _IDS[0]: graph.interval("x", *a, lower_closed=True, upper_closed=True),
        _IDS[1]: graph.interval("x", *b, lower_closed=True, upper_closed=True),
    }
    expected = {
        (
            graph.and_(
                graph.interval("x", 0.0, b[0], lower_closed=True, upper_closed=False),
                extra,
            ),
            frozenset({_IDS[0]}),
        ),
        (
            graph.and_(
                graph.interval(
                    "x", b[0], min(a[1], b[1]), lower_closed=True, upper_closed=True
                ),
                extra,
            ),
            frozenset({_IDS[0], _IDS[1]}),
        ),
        (
            graph.and_(
                graph.interval(
                    "x", min(a[1], b[1]), upper, lower_closed=False, upper_closed=True
                ),
                extra,
            ),
            frozenset({_IDS[0] if a[1] == upper else _IDS[1]}),
        ),
    }
    for order in itertools.permutations(sources):
        cells = finalize_regions(
            reasoner, covered_overlay(reasoner, domain, sources, order=order)
        )
        assert {(cell.predicate_id, cell.contributors) for cell in cells} == expected
        assert len({row[0] for row in _snapshot(graph, cells)}) == 3


@pytest.mark.parametrize(
    "three_sources", [False, True], ids=("G04-ring", "G05-three-sources")
)
def test_numeric_compound_vectors_have_canonical_box_unions(three_sources):
    dtype, upper, lo, hi = (
        ("float", 20.0, 5.0, 10.0) if three_sources else ("int", 4, 1, 3)
    )
    zero = 0.0 if three_sources else 0
    graph = PredicateGraph(
        [DomainField(name=name, data_type=dtype) for name in ("x", "y")],
        budget=_budget(),
    )
    reasoner = Reasoner(graph)
    domain = graph.and_(
        *(
            graph.interval(name, zero, upper, lower_closed=True, upper_closed=True)
            for name in graph.fields
        )
    )
    sources = {
        _IDS[0]: graph.true,
        _IDS[1]: graph.and_(
            *(
                graph.interval(name, lo, hi, lower_closed=True, upper_closed=True)
                for name in graph.fields
            )
        ),
    }
    if three_sources:
        sources[_IDS[2]] = graph.and_(
            graph.interval("x", 8.0, 15.0, lower_closed=False, upper_closed=True),
            graph.interval("y", None, 7.0, lower_closed=False, upper_closed=True),
        )
    baseline = None
    for order in itertools.permutations(sources):
        cells = finalize_regions(
            reasoner, covered_overlay(reasoner, domain, sources, order=order)
        )
        assert len(cells) == (4 if three_sources else 2)
        box_count = sum(
            len(graph.nodes[cell.predicate_id]["args"])
            if graph.nodes[cell.predicate_id]["op"] == "or"
            else 1
            for cell in cells
        )
        assert box_count == (11 if three_sources else 5)
        snapshot = _snapshot(graph, cells)
        if baseline is None:
            baseline = snapshot
        assert snapshot == baseline


def test_horizontal_vertical_l_decompositions_have_identical_canonical_identity():
    graph = PredicateGraph(
        [DomainField(name=name, data_type="int") for name in ("x", "y")],
        budget=_budget(),
    )
    reasoner = Reasoner(graph)

    def box(xlo, xhi, ylo, yhi):
        return graph.and_(
            graph.interval("x", xlo, xhi, lower_closed=True, upper_closed=True),
            graph.interval("y", ylo, yhi, lower_closed=True, upper_closed=True),
        )

    vertical = (box(0, 1, 0, 3), box(2, 3, 0, 1))
    horizontal = (box(0, 3, 0, 1), box(0, 1, 2, 3))
    source = graph.or_(*vertical)
    overlay = covered_overlay(reasoner, box(0, 3, 0, 3), {_IDS[0]: source})
    baseline = _snapshot(graph, finalize_regions(reasoner, overlay))
    for pieces in (
        vertical,
        horizontal,
        vertical + vertical,
        (box(0, 0, 0, 3), box(1, 1, 0, 3), vertical[1]),
    ):
        fragments = tuple(
            Fragment(predicate, frozenset({_IDS[0]})) for predicate in pieces
        )
        cells = finalize_regions(reasoner, overlay, fragments=fragments)
        assert len(cells) == 1 and cells[0].predicate_id == source
        assert _snapshot(graph, cells) == baseline
        assert (
            _snapshot(
                graph,
                finalize_regions(
                    reasoner,
                    overlay,
                    fragments=(Fragment(cells[0].predicate_id, cells[0].contributors),),
                ),
            )
            == baseline
        )


def test_adjacent_binary64_singletons_reconcile_without_dense_real_points():
    import math

    graph = PredicateGraph([DomainField(name="x", data_type="float")], budget=_budget())
    reasoner = Reasoner(graph)
    first, second = 1.0, math.nextafter(1.0, math.inf)
    closed = graph.interval("x", first, second, lower_closed=True, upper_closed=True)
    empty = graph.interval("x", first, second, lower_closed=False, upper_closed=False)
    overlay = covered_overlay(reasoner, closed, {_IDS[0]: closed, _IDS[1]: empty})
    fragments = tuple(
        Fragment(graph.eq("x", value), frozenset({_IDS[0]}))
        for value in (first, second)
    )
    cells = finalize_regions(reasoner, overlay, fragments=fragments)
    assert len(cells) == 1
    assert cells[0].predicate_id == closed and cells[0].contributors == frozenset(
        {_IDS[0]}
    )
    assert not finalize_regions(
        reasoner, covered_overlay(reasoner, closed, {_IDS[1]: empty})
    )


def test_deep_boolean_source_normalizes_without_python_call_stack_limits():
    graph = PredicateGraph(
        [
            DomainField(name="flag", data_type="bool"),
            DomainField(name="x", data_type="int"),
        ],
        budget=_budget(),
    )
    reasoner = Reasoner(graph)
    point = graph.eq("x", 0)
    flag = graph.eq("flag", True)
    source = point
    for _ in range(1100):
        source = graph.not_(graph.or_(flag, source))
    domain = graph.interval("x", 0, 1, lower_closed=True, upper_closed=True)
    cells = finalize_regions(
        reasoner, covered_overlay(reasoner, domain, {_IDS[0]: source})
    )
    assert len(cells) == 1
    assert reasoner.equivalent(
        cells[0].predicate_id, graph.and_(point, graph.not_(flag))
    )


def test_repeated_finalization_reuses_unchanged_peak_region_capacity():
    limits = _budget().limits.model_copy(update={"max_regions": 256})
    graph = PredicateGraph(
        [DomainField(name="x", data_type="int")],
        budget=OperationBudget(limits, "normalization-peak"),
    )
    reasoner = Reasoner(graph)
    interval = graph.interval("x", 0, 10, lower_closed=True, upper_closed=True)
    overlay = covered_overlay(reasoner, interval, {_IDS[0]: interval})
    for _ in range(40):
        cells = finalize_regions(reasoner, overlay)
        assert len(cells) == 1 and cells[0].predicate_id == interval
