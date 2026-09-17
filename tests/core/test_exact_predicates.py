"""Observable predicate-1 construction and lowering contracts."""

from __future__ import annotations

import datetime as dt

import pytest

from mountainash_rules.core.constants import (
    UNKNOWN_NUMERIC,
    DataType,
    MatchStrategy,
)
from mountainash_rules.core.contracts import DomainField, ExactLimits, OperationBudget
from mountainash_rules.core.dimension import Dimension
from mountainash_rules.core.language import LanguageLimits, StringLanguage
from mountainash_rules.core.predicates import PredicateGraph


def _budget() -> OperationBudget:
    return OperationBudget(
        ExactLimits(
            language=LanguageLimits(
                max_input_bytes=1_000_000,
                max_nesting=64,
                max_nfa_states=100_000,
                max_states=100_000,
                max_transitions=1_000_000,
                max_work=10_000_000,
            ),
            max_input_bytes=1_000_000,
            max_output_bytes=1_000_000,
            max_work=100_000_000,
            max_live_bytes=1_000_000_000,
            max_predicate_nodes=100_000,
            max_dfa_states=100_000,
            max_dfa_transitions=1_000_000,
            max_theory_states=100_000,
            max_regions=100_000,
            max_scopes=100_000,
            max_source_scope_edges=100_000,
            max_contributor_edges=100_000,
            max_word_rows=100_000,
            max_numeric_bits=100_000,
            max_witnesses=100_000,
        ),
        "exact-test",
    )


def _graph(*fields: DomainField) -> PredicateGraph:
    return PredicateGraph(fields, budget=_budget())


def test_canonical_syntax_aliases_intervals_and_reserved_values() -> None:
    graph = _graph(DomainField(name="x", data_type=DataType.FLOAT))
    point = graph.eq("x", 1.0)
    assert graph.and_(graph.true, point, point) == point
    assert graph.or_(graph.false, point) == point
    assert graph.not_(graph.not_(point)) == point
    assert graph.and_(point, graph.not_(point)) == graph.false
    assert (
        graph.interval("x", 1.0, 1.0, lower_closed=True, upper_closed=False)
        == graph.false
    )
    with pytest.raises(ValueError, match="Reserved"):
        graph.eq("x", -999999999.0)
    with pytest.raises(ValueError, match="Unknown field"):
        graph.eq("missing", 1.0)


@pytest.mark.parametrize(
    ("strategy", "data_type", "row"),
    [
        (MatchStrategy.EXACT, DataType.INT, {"rule": UNKNOWN_NUMERIC}),
        (MatchStrategy.EXACT_KEY, DataType.BOOL, {"rule": None}),
        (MatchStrategy.RANGE, DataType.INT, {"lo": UNKNOWN_NUMERIC, "hi": 4}),
        (MatchStrategy.SET_EXCLUSION, DataType.STR, {"rule": []}),
    ],
)
def test_source_wildcards_and_empty_exclusions_preserve_the_admissible_universe(
    strategy: MatchStrategy, data_type: DataType, row: dict[str, object]
) -> None:
    from mountainash_rules.core.reasoner import Reasoner

    graph = _graph(DomainField(name="@physical", data_type=data_type))
    dimension = Dimension(
        dimension_name="authored",
        context_field="@physical",
        rule_field="rule",
        match_strategy=strategy,
        data_type=data_type,
        range_min_field="lo" if strategy is MatchStrategy.RANGE else None,
        range_max_field="hi" if strategy is MatchStrategy.RANGE else None,
    )
    predicate = graph.lower_dimension(dimension, row, regex_options=None)
    expected = (
        graph.interval("@physical", None, 4, lower_closed=False, upper_closed=True)
        if strategy is MatchStrategy.RANGE
        else graph.true
    )
    assert Reasoner(graph).equivalent(predicate, expected)


def test_lowering_rejects_source_not_set_missing_columns_bad_sets_and_reversed_ranges() -> (
    None
):
    graph = _graph(DomainField(name="x", data_type=DataType.INT))
    exact = Dimension(dimension_name="x", rule_field="r", data_type=DataType.INT)
    with pytest.raises(ValueError, match="NOT_SET"):
        graph.lower_dimension(exact, {"r": -999999998}, regex_options=None)
    with pytest.raises(ValueError, match="Missing source column"):
        graph.lower_dimension(exact, {}, regex_options=None)
    membership = Dimension(
        dimension_name="x",
        rule_field="r",
        data_type=DataType.INT,
        match_strategy=MatchStrategy.SET_MEMBERSHIP,
    )
    with pytest.raises(ValueError, match="null elements"):
        graph.lower_dimension(membership, {"r": [1, None]}, regex_options=None)
    ranged = Dimension(
        dimension_name="x",
        rule_field="r",
        data_type=DataType.INT,
        match_strategy=MatchStrategy.RANGE,
        range_min_field="lo",
        range_max_field="hi",
    )
    with pytest.raises(ValueError, match="reversed"):
        graph.lower_dimension(ranged, {"lo": 3, "hi": 2}, regex_options=None)


def test_decode_validates_dead_arms_and_language_envelope_payload_boundary() -> None:
    graph = _graph(DomainField(name="x", data_type=DataType.INT))
    good = graph.eq("x", 1)
    with pytest.raises(ValueError):
        graph.decode(
            [
                {
                    "id": graph.true,
                    "payload": {"schema_version": 1, "node": {"op": "true"}},
                },
                {
                    "id": good,
                    "payload": {
                        "schema_version": 1,
                        "node": {
                            "op": "and",
                            "args": [graph.true, "predicate:1:" + "0" * 64],
                        },
                    },
                },
            ],
            [],
        )


@pytest.mark.parametrize(
    "dtype", [DataType.INT, DataType.FLOAT, DataType.DATE, DataType.DATETIME]
)
def test_ordered_universe_spellings_share_canonical_ids(dtype):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.reasoner import Reasoner
    from mountainash_rules.core.scalar import encode_scalar, rank_bounds, unrank

    timezone = "utc" if dtype is DataType.DATETIME else None
    field = DomainField(name="x", data_type=dtype, timezone=timezone)
    graph = _graph(field)
    lo, hi = rank_bounds(dtype, timezone=timezone)
    first, last = (
        unrank(lo, dtype, timezone=timezone),
        unrank(hi, dtype, timezone=timezone),
    )
    assert (
        graph.interval("x", first, last, lower_closed=True, upper_closed=True)
        == graph.true
    )
    assert (
        graph.interval("x", None, None, lower_closed=False, upper_closed=False)
        == graph.true
    )
    point = graph.eq("x", first)
    expected = content_id(
        "predicate",
        {
            "schema_version": 1,
            "node": {
                "op": "interval",
                "field": "x",
                "lower": None,
                "upper": encode_scalar(first, dtype, timezone=timezone),
                "lower_closed": False,
                "upper_closed": True,
            },
        },
    )
    assert point == expected
    assert Reasoner(graph).witness(point) == {"x": first}


@pytest.mark.parametrize(
    ("dtype", "marker"),
    [
        (DataType.FLOAT, UNKNOWN_NUMERIC),
        (DataType.INT, float(UNKNOWN_NUMERIC)),
    ],
)
def test_cross_typed_source_marker_cannot_become_a_wildcard(dtype, marker):
    graph = _graph(DomainField(name="x", data_type=dtype))
    dimension = Dimension(dimension_name="x", rule_field="r", data_type=dtype)
    with pytest.raises(ValueError):
        graph.lower_dimension(dimension, {"r": marker}, regex_options=None)


def test_utc_source_marker_is_classified_after_declared_type_admission():
    graph = _graph(DomainField(name="x", data_type="datetime", timezone="utc"))
    dimension = Dimension(dimension_name="x", rule_field="r", data_type="datetime")
    assert (
        graph.lower_dimension(
            dimension,
            {"r": dt.datetime(1, 1, 1, tzinfo=dt.timezone.utc)},
            regex_options=None,
        )
        == graph.true
    )


def test_partial_or_boolean_version_wrapper_cannot_bypass_node_validation():
    graph = _graph(DomainField(name="x", data_type="int"))
    for payload in (
        {"node": {"op": "true"}},
        {"schema_version": True, "node": {"op": "true"}},
        {"node": {"op": "true"}, "ignored": 1},
    ):
        with pytest.raises(ValueError):
            graph.add(payload)


def test_predicate_decode_debits_outer_input_limit():
    from mountainash_rules.core.contracts import ExactResourceError

    graph = PredicateGraph(
        [DomainField(name="x", data_type="int")],
        budget=OperationBudget(
            _budget().limits.model_copy(update={"max_input_bytes": 0}), "decode"
        ),
    )
    envelope = {
        "id": graph.true,
        "payload": {"schema_version": 1, "node": {"op": "true"}},
    }
    with pytest.raises(ExactResourceError) as failure:
        graph.decode([envelope], [])
    assert failure.value.counter == "max_input_bytes"


def test_native_solver_cannot_bypass_outer_dfa_capacity():
    from mountainash_rules.core.contracts import ExactResourceError
    from mountainash_rules.core.reasoner import Reasoner

    budget = OperationBudget(
        _budget().limits.model_copy(
            update={"max_dfa_states": 0, "max_dfa_transitions": 0}
        ),
        "native-cap",
    )
    graph = PredicateGraph([DomainField(name="s", data_type="str")], budget=budget)
    with pytest.raises(ExactResourceError) as failure:
        Reasoner(graph).witness(graph.true)
    assert failure.value.counter in {"max_dfa_states", "max_dfa_transitions"}


def test_native_state_exhaustion_preserves_its_resource_category():
    from mountainash_rules.core.contracts import ExactResourceError

    inner = LanguageLimits(
        max_input_bytes=1000,
        max_nesting=64,
        max_nfa_states=1000,
        max_states=0,
        max_transitions=1000,
        max_work=10000,
    )
    graph = PredicateGraph(
        [DomainField(name="s", data_type="str")],
        budget=OperationBudget(
            _budget().limits.model_copy(update={"language": inner}), "native-state"
        ),
    )
    dimension = Dimension(
        dimension_name="s", rule_field="r", data_type="str", match_strategy="prefix"
    )
    with pytest.raises(ExactResourceError) as failure:
        graph.lower_dimension(dimension, {"r": "ab"}, regex_options=None)
    assert "state" in failure.value.counter
    assert failure.value.limit == 0


def test_native_scope_releases_transient_dfa_reservations() -> None:
    inner = LanguageLimits(
        max_input_bytes=1000,
        max_nesting=64,
        max_nfa_states=1000,
        max_states=1,
        max_transitions=2,
        max_work=1000,
    )
    limits = _budget().limits.model_copy(
        update={
            "language": inner,
            "max_dfa_states": 1,
            "max_dfa_transitions": 2,
            "max_work": 6000,
        }
    )
    graph = PredicateGraph(
        [DomainField(name="s", data_type="str")],
        budget=OperationBudget(limits, "native-scope"),
    )
    with graph.native_scope():
        graph._native_call(
            "language_empty",
            lambda: StringLanguage.empty(limits=graph.language_limits),
        )
    with graph.native_scope():
        graph._native_call(
            "language_empty",
            lambda: StringLanguage.empty(limits=graph.language_limits),
        )


def test_physical_schema_is_bounded_before_retaining_all_fields():
    from mountainash_rules.core.contracts import ExactResourceError

    limits = _budget().limits.model_copy(update={"max_live_bytes": 1024})
    with pytest.raises(ExactResourceError) as failure:
        PredicateGraph(
            (
                DomainField(name=f"field{index}", data_type="int")
                for index in range(100)
            ),
            budget=OperationBudget(limits, "schema-admission"),
        )
    assert failure.value.counter == "max_live_bytes"


def test_predicate_buffer_is_admitted_before_serialization(monkeypatch):
    from mountainash_rules.core import predicates
    from mountainash_rules.core.contracts import ExactResourceError

    graph = _graph(DomainField(name="x", data_type="int"))
    used = graph.budget.check("max_live_bytes", 0, phase="probe", units="bytes")
    graph.budget.reserve(
        "max_live_bytes",
        graph.budget.limits.max_live_bytes - used - 64,
        phase="other-owned-data",
        units="bytes",
    )

    def forbidden_buffer(_):
        raise AssertionError("predicate buffer allocated before admission")

    monkeypatch.setattr(predicates, "canonical_bytes", forbidden_buffer)
    with pytest.raises(ExactResourceError) as failure:
        graph.eq("x", 1)
    assert failure.value.counter == "max_live_bytes"
