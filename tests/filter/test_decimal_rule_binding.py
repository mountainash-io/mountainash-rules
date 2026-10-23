"""A DECIMAL rule operand that is null or out of range is not a value: it never matches."""

from decimal import Decimal

import polars as pl
import pytest

from mountainash.relations import relation
from mountainash_rules import (
    ContextField,
    DataType,
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    MatchStrategy,
)
from tests.decision_table.construction_support import rules_relation

BACKENDS = ("polars", "ibis-duckdb")
FIELD = ContextField(name="amount", data_type=DataType.DECIMAL, precision=4, scale=2, rounding="half_to_even")


def scored(backend_name, column, context_value):
    rules = rules_relation(backend_name, pl.DataFrame({"rule_name": ["bad", "one"], "amount": column}))
    metadata = DimensionsMetadata(
        fields=(FIELD,),
        dimensions=(Dimension(dimension_name="amount", context_field="amount", match_strategy=MatchStrategy.EXACT),),
    )
    engine = ExpressionRulesEngine(rules, metadata)
    frame = relation(engine.explain({"amount": context_value}).frame).to_polars()
    return {row["rule_name"]: (row["__t_amount"], row["__specificity"]) for row in frame.to_dicts()}


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_a_null_decimal_rule_value_does_not_match_a_zero_context(backend_name):
    column = pl.Series([None, Decimal("1.00")], dtype=pl.Decimal(4, 2))

    assert scored(backend_name, column, Decimal("0.00")) == {"bad": (-1, 0), "one": (-1, 0)}


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_an_overflowing_decimal_rule_value_does_not_become_zero(backend_name):
    column = pl.Series([Decimal("12345.00"), Decimal("1.00")], dtype=pl.Decimal(10, 2))

    assert scored(backend_name, column, Decimal("0.00")) == {"bad": (-1, 0), "one": (-1, 0)}
