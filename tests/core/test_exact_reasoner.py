"""Observable exact correlated-theory contracts."""

from __future__ import annotations

import math

import pytest

from mountainash_rules.core.constants import DataType
from mountainash_rules.core.contracts import DomainField, ExactLimits, OperationBudget
from mountainash_rules.core.language import LanguageLimits, RegexOptions, StringLanguage
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.reasoner import Reasoner


def _budget(max_work: int = 10_000_000) -> OperationBudget:
    return OperationBudget(
        ExactLimits(
            language=LanguageLimits(
                max_input_bytes=65536,
                max_nesting=64,
                max_nfa_states=4096,
                max_states=512,
                max_transitions=8192,
                max_work=min(max_work, 100_000),
            ),
            max_input_bytes=1_000_000,
            max_output_bytes=1_000_000,
            max_work=max_work * 200,
            max_live_bytes=32_000_000,
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


def _theory(
    *fields: DomainField, max_work: int = 10_000_000
) -> tuple[PredicateGraph, Reasoner]:
    graph = PredicateGraph(fields, budget=_budget(max_work))
    return graph, Reasoner(graph)


def test_aliases_share_one_physical_variable_and_bool_has_real_finite_domain() -> None:
    graph, reasoner = _theory(
        DomainField(name="@x", data_type=DataType.BOOL),
        DomainField(name="y", data_type=DataType.INT),
    )
    assert reasoner.is_empty(graph.and_(graph.eq("@x", True), graph.eq("@x", False)))
    bools = graph.in_("@x", [False, True])
    witness = reasoner.witness(bools)
    assert (
        witness is not None and witness["@x"] is False and isinstance(witness["y"], int)
    )


def test_binary64_open_adjacency_extrema_and_reserved_holes_are_exact() -> None:
    graph, reasoner = _theory(DomainField(name="x", data_type=DataType.FLOAT))
    adjacent = math.nextafter(1.0, math.inf)
    assert reasoner.is_empty(
        graph.interval("x", 1.0, adjacent, lower_closed=False, upper_closed=False)
    )
    assert reasoner.witness(
        graph.interval("x", 1.0, adjacent, lower_closed=True, upper_closed=True)
    ) == {"x": 1.0}
    with pytest.raises(ValueError, match="Reserved"):
        graph.interval("x", None, -999999999.0, lower_closed=False, upper_closed=True)


def test_large_correlated_integer_region_is_not_tuple_enumerated() -> None:
    graph, reasoner = _theory(
        DomainField(name="x", data_type=DataType.INT),
        DomainField(name="y", data_type=DataType.INT),
    )
    formula = graph.and_(
        graph.interval("x", 0, 10**12, lower_closed=True, upper_closed=True),
        graph.interval("y", 0, 10**12, lower_closed=True, upper_closed=True),
        graph.compare("x", "y", "eq"),
    )
    assert reasoner.witness(formula) == {"x": 0, "y": 0}


def test_string_intersection_disequality_and_reserved_exclusion_use_exact_languages() -> (
    None
):
    graph, reasoner = _theory(
        DomainField(name="a", data_type=DataType.STR),
        DomainField(name="b", data_type=DataType.STR),
    )
    prefix = graph.language(
        "a",
        graph.add_language(StringLanguage.prefix("ab", limits=graph.language_limits)),
    )
    suffix = graph.language(
        "a",
        graph.add_language(StringLanguage.suffix("bc", limits=graph.language_limits)),
    )
    witness = reasoner.witness(graph.and_(prefix, suffix))
    assert (
        witness is not None
        and witness["a"].startswith("ab")
        and witness["a"].endswith("bc")
    )
    animals = graph.add_language(
        StringLanguage.regex(
            "cat|dog", limits=graph.language_limits, options=RegexOptions()
        )
    )
    distinct = graph.and_(
        graph.language("a", animals),
        graph.language("b", animals),
        graph.compare("a", "b", "ne"),
    )
    witness = reasoner.witness(distinct)
    assert witness is not None and {witness["a"], witness["b"]} == {"cat", "dog"}


def test_difference_equivalence_and_budget_abort_are_exact() -> None:
    graph, reasoner = _theory(DomainField(name="x", data_type=DataType.INT))
    one = graph.eq("x", 1)
    two = graph.in_("x", [1, 2])
    assert reasoner.equivalent(reasoner.difference(two, graph.eq("x", 2)), one)
    from mountainash_rules.core.contracts import ExactResourceError

    with pytest.raises(ExactResourceError) as failure:
        exhausted_graph, exhausted = _theory(
            DomainField(name="x", data_type=DataType.INT), max_work=0
        )
        exhausted.is_empty(exhausted_graph.eq("x", 1))
    assert failure.value.counter == "max_work"


def test_negated_order_atoms_match_the_full_bounded_truth_table():
    import operator

    graph, reasoner = _theory(
        DomainField(name="x", data_type="int"), DomainField(name="y", data_type="int")
    )
    for relation in ("eq", "ne", "lt", "le", "gt", "ge"):
        atom = graph.compare("x", "y", relation)
        for x in range(3):
            for y in range(3):
                point = graph.and_(graph.eq("x", x), graph.eq("y", y))
                expected = getattr(operator, relation)(x, y)
                assert reasoner.is_empty(graph.and_(point, atom)) is not expected
                assert (
                    reasoner.is_empty(graph.and_(point, graph.not_(atom))) is expected
                )


def test_merged_equality_satisfies_non_strict_order():
    graph, reasoner = _theory(
        DomainField(name="x", data_type="int"), DomainField(name="y", data_type="int")
    )
    region = graph.and_(
        graph.eq("x", 1), graph.compare("x", "y", "eq"), graph.compare("x", "y", "le")
    )
    assert reasoner.witness(region) == {"x": 1, "y": 1}


def test_three_mutually_distinct_booleans_are_impossible():
    graph, reasoner = _theory(
        *(DomainField(name=name, data_type="bool") for name in ("a", "b", "c"))
    )
    formula = graph.and_(
        graph.compare("a", "b", "ne"),
        graph.compare("b", "c", "ne"),
        graph.compare("a", "c", "ne"),
    )
    assert reasoner.is_empty(formula)


def test_sequential_queries_reuse_peak_theory_capacity():
    budget = OperationBudget(
        _budget().limits.model_copy(update={"max_theory_states": 3}), "peak"
    )
    graph = PredicateGraph([DomainField(name="x", data_type="int")], budget=budget)
    reasoner = Reasoner(graph)
    first = reasoner.witness(graph.true)
    assert first is not None and reasoner.witness(graph.true) == first


def test_long_compatible_conjunction_does_not_depend_on_python_recursion_depth():
    graph, reasoner = _theory(DomainField(name="x", data_type="int"))
    formula = graph.and_(
        *(
            graph.interval("x", 0, end, lower_closed=True, upper_closed=True)
            for end in range(1, 1200)
        )
    )
    assert reasoner.witness(formula) == {"x": 0}


def test_witness_cannot_escape_zero_output_budget():
    from mountainash_rules.core.contracts import ExactResourceError

    budget = OperationBudget(
        _budget().limits.model_copy(update={"max_output_bytes": 0}), "output"
    )
    graph = PredicateGraph([DomainField(name="x", data_type="int")], budget=budget)
    with pytest.raises(ExactResourceError) as failure:
        Reasoner(graph).witness(graph.true)
    assert failure.value.counter == "max_output_bytes"
