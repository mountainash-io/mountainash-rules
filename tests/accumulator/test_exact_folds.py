"""Numeric-1 folds depend on contributions, never reduction order."""

import datetime as dt
import itertools
import math
from fractions import Fraction

import pytest

from mountainash_rules import Aggregate


def fold(
    values,
    *,
    operation="sum",
    data_type="float",
    bits=100000,
    timezone=None,
    max_input_bytes=1000000,
    max_work=10000000,
    max_live_bytes=10000000,
):
    from mountainash_rules.core.contracts import ExactLimits, OperationBudget
    from mountainash_rules.engines.accumulator.aggregate import exact_fold

    language = dict(
        max_input_bytes=100000,
        max_nesting=64,
        max_nfa_states=10000,
        max_states=10000,
        max_transitions=100000,
        max_work=1000000,
    )
    limits = ExactLimits(
        language=language,
        max_input_bytes=max_input_bytes,
        max_output_bytes=1000000,
        max_work=max_work,
        max_live_bytes=max_live_bytes,
        max_predicate_nodes=100000,
        max_dfa_states=100000,
        max_dfa_transitions=1000000,
        max_theory_states=100000,
        max_regions=100000,
        max_scopes=100000,
        max_source_scope_edges=100000,
        max_contributor_edges=100000,
        max_word_rows=100000,
        max_numeric_bits=bits,
        max_witnesses=10000,
    )
    declaration = dict(
        column_name="amount",
        output_name=f"amount.{operation}",
        operation=operation,
        data_type=data_type,
        numeric_semantics="numeric-1",
    )
    if timezone is not None:
        declaration["timezone"] = timezone
    aggregate = Aggregate(**declaration)
    return exact_fold(aggregate, values, budget=OperationBudget(limits, "analysis"))


def test_empty_contributions_do_not_invent_reducer_identity():
    for operation in ("sum", "product", "min", "max"):
        with pytest.raises(ValueError, match="at least one contribution"):
            fold([], operation=operation)


def test_cancellation_is_order_independent_with_one_final_rounding():
    for values in itertools.permutations([1e16, 1.0, -1e16]):
        assert fold(values) == 1.0
        assert fold(values, data_type="float", operation="product") == -1e32


@pytest.mark.parametrize(
    "values",
    [
        [0.1, 0.2, 0.3],
        [math.ldexp(1.0, -1074), 0.5],
        [math.ldexp(1.0, -1074), 1.5],
        [float.fromhex("0x1.fffffffffffffp+1023"), 0.5],
        [1.0000000000000002] * 10,
    ],
)
def test_float_product_matches_independent_exact_rational_oracle(values):
    expected = float(math.prod(Fraction.from_float(value) for value in values))
    actual = fold(values, operation="product")
    assert actual.hex() == expected.hex()


def test_int64_is_checked_after_cancellation_not_intermediate_addition():
    maximum = 2**63 - 1
    assert fold([maximum, maximum, -maximum], data_type="int") == maximum
    with pytest.raises(ValueError):
        fold([maximum, 1], data_type="int")


def test_zero_product_still_validates_all_contributions():
    for invalid in (None, math.nan, math.inf, "1", True):
        with pytest.raises(ValueError):
            fold([0.0, invalid], operation="product")
    assert fold([0.0, 1e300, 1e300], operation="product") == 0.0
    assert math.copysign(1, fold([-0.0], operation="product")) == 1


def test_string_extrema_reserve_input_normalization_and_comparison_work():
    from mountainash_rules.core.contracts import ExactResourceError

    with pytest.raises(ExactResourceError) as input_failure:
        fold(["abcd"], operation="min", data_type="str", max_input_bytes=15)
    assert input_failure.value.counter == "max_input_bytes"

    with pytest.raises(ExactResourceError) as comparison_failure:
        fold(["aaaa", "aaab"], operation="min", data_type="str", max_work=10)
    assert comparison_failure.value.counter == "max_work"


def test_zero_product_scan_is_charged_after_admission():
    from mountainash_rules.core.contracts import ExactResourceError

    with pytest.raises(ExactResourceError) as failure:
        fold([2, 0], operation="product", data_type="int", max_work=2)
    assert failure.value.counter == "max_work"


def test_float_retention_reserves_the_actual_parts_and_list_graph():
    from mountainash_rules.core.contracts import ExactResourceError

    with pytest.raises(ExactResourceError) as failure:
        fold([0.0], operation="product", max_live_bytes=96)
    assert failure.value.counter == "max_live_bytes"


def test_negative_float_rounding_reserves_all_simultaneous_bigint_workspace():
    from mountainash_rules.core.contracts import ExactResourceError

    with pytest.raises(ExactResourceError) as failure:
        fold([-1.0], max_live_bytes=764)
    assert failure.value.counter == "max_live_bytes"


def test_final_float_overflow_and_workspace_exhaustion_are_distinct():
    from mountainash_rules.core.contracts import ExactResourceError

    with pytest.raises(ValueError) as overflow:
        fold([1e308, 1e308])
    assert not isinstance(overflow.value, ExactResourceError)
    for values in itertools.permutations([1e16, 1.0, -1e16]):
        with pytest.raises(ExactResourceError) as failure:
            fold(values, bits=64)
        assert failure.value.counter == "max_numeric_bits"


def test_nonwinning_and_reserved_contributions_remain_valid():
    assert fold([-999999999, 0, 3], data_type="int", operation="max") == 3
    assert fold([False, True], data_type="bool", operation="min") is False
    assert fold(["z", "ß"], data_type="str", operation="max") == "ß"


def test_min_max_preserve_date_and_explicit_utc_datetime_ordering():
    earliest = dt.date(2024, 1, 1)
    latest = dt.date(2024, 1, 2)
    assert fold([latest, earliest], operation="min", data_type="date") == earliest

    utc = dt.timezone.utc
    first = dt.datetime(2024, 1, 1, 0, 0, tzinfo=utc)
    second = dt.datetime(2024, 1, 1, 1, 0, tzinfo=utc)
    assert (
        fold(
            [second, first],
            operation="max",
            data_type="datetime",
            timezone="utc",
        )
        == second
    )
