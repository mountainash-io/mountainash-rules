"""Positive and negative source applicability across supported typed strategies."""

import datetime as dt

import pytest

from mountainash_rules import Dimension, DomainField, ExactLimits
from mountainash_rules.core.contracts import OperationBudget
from mountainash_rules.core.language import RegexOptions
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.reasoner import Reasoner


def _cases():
    typed_values = (
        ("str", None, "ab", "bc", "zz"),
        ("bool", None, False, True, False),
        ("int", None, 3, 7, 10),
        ("float", None, 3.0, 7.0, 10.0),
        ("date", None, dt.date(2026, 1, 3), dt.date(2026, 1, 7), dt.date(2026, 1, 10)),
        (
            "datetime",
            "naive",
            dt.datetime(2026, 1, 3),
            dt.datetime(2026, 1, 7),
            dt.datetime(2026, 1, 10),
        ),
        (
            "datetime",
            "utc",
            dt.datetime(2026, 1, 3, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 1, 7, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 1, 10, tzinfo=dt.timezone.utc),
        ),
    )
    for dtype, timezone, lower, upper, outside in typed_values:
        for strategy in (
            "exact",
            "exact_key",
            "not_equal",
            "set_membership",
            "set_exclusion",
        ):
            positive, negative = (
                (upper, lower)
                if strategy in {"not_equal", "set_exclusion"}
                else (lower, upper)
            )
            value = [lower] if strategy.startswith("set_") else lower
            yield pytest.param(
                dtype,
                timezone,
                strategy,
                {"r": value},
                {},
                (positive,),
                (negative,),
                id=f"{strategy}-{dtype}-{timezone}",
            )
        if dtype in {"str", "bool"}:
            continue
        for low_closed, high_closed in (
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ):
            middle = (
                lower + (upper - lower) / 2
                if dtype in {"float", "date", "datetime"}
                else 5
            )
            positives = (
                (middle,)
                + ((lower,) if low_closed else ())
                + ((upper,) if high_closed else ())
            )
            negatives = (
                (outside,)
                + (() if low_closed else (lower,))
                + (() if high_closed else (upper,))
            )
            yield pytest.param(
                dtype,
                timezone,
                "range",
                {"lo": lower, "hi": upper},
                {
                    "range_min_field": "lo",
                    "range_max_field": "hi",
                    "range_min_inclusive": low_closed,
                    "range_max_inclusive": high_closed,
                },
                positives,
                negatives,
                id=f"range-{dtype}-{timezone}-{low_closed}-{high_closed}",
            )
        yield pytest.param(
            dtype,
            timezone,
            "greater_than",
            {"r": lower},
            {},
            (upper,),
            (lower,),
            id=f"greater_than-{dtype}-{timezone}",
        )
        yield pytest.param(
            dtype,
            timezone,
            "less_than",
            {"r": upper},
            {},
            (lower,),
            (upper,),
            id=f"less_than-{dtype}-{timezone}",
        )
    for strategy, value, positive, negative in (
        ("prefix", "ab", "abc", "zab"),
        ("suffix", "bc", "abc", "bca"),
        ("contains", "a.b", "za.bz", "axb"),
        ("regex", "^ab.+$", "abc", "ab"),
        ("context_regex", "^ab.+$", "abc", "ab"),
    ):
        row = {} if strategy == "context_regex" else {"r": value}
        options = {"regex_pattern": value} if strategy == "context_regex" else {}
        yield pytest.param(
            "str", None, strategy, row, options, (positive,), (negative,), id=strategy
        )


@pytest.mark.parametrize(
    ("dtype", "timezone", "strategy", "row", "options", "positives", "negatives"),
    list(_cases()),
)
def test_source_applicability_matches_typed_positive_and_negative_controls(
    dtype,
    timezone,
    strategy,
    row,
    options,
    positives,
    negatives,
):
    limits = ExactLimits(
        language=dict(
            max_input_bytes=65536,
            max_nesting=64,
            max_nfa_states=4096,
            max_states=512,
            max_transitions=8192,
            max_work=1_000_000,
        ),
        max_input_bytes=10_000_000,
        max_output_bytes=10_000_000,
        max_work=10**11,
        max_live_bytes=100_000_000,
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
    )
    graph = PredicateGraph(
        [DomainField(name="@physical", data_type=dtype, timezone=timezone)],
        budget=OperationBudget(limits, "applicability"),
    )
    dimension = Dimension(
        dimension_name="authored",
        context_field="@physical",
        rule_field="r",
        data_type=dtype,
        match_strategy=strategy,
        **options,
    )
    predicate = graph.lower_dimension(
        dimension,
        row,
        regex_options=RegexOptions()
        if strategy in {"regex", "context_regex"}
        else None,
    )
    reasoner = Reasoner(graph)
    for context in positives:
        assert not reasoner.is_empty(
            graph.and_(predicate, graph.eq("@physical", context))
        ), context
    for context in negatives:
        assert reasoner.is_empty(
            graph.and_(predicate, graph.eq("@physical", context))
        ), context
