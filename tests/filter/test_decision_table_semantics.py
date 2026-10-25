"""Decision-table ternary, presence, and null-binding semantics."""

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
from tests.conftest import build_backend_df


BACKENDS = ("polars", "ibis-duckdb")


def _rows(frame, *columns):
    return relation(frame).to_polars().sort("rule_name").select(*columns).rows()


def _scores(engine, context, dimension):
    return _rows(
        engine.explain(context).frame,
        "rule_name",
        f"__t_{dimension}",
        "__specificity",
    )


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("context_outcome", [-1, 0, 1])
def test_outcome_scores_every_rule_context_cell(backend_name, context_outcome):
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {"rule_name": ["r-1", "r0", "r1"], "required": [-1, 0, 1]},
            table_name="outcome_rules",
        ),
        DimensionsMetadata(
            fields=(ContextField(name="outcome", data_type=DataType.INT),),
            dimensions=(
                Dimension(
                    dimension_name="outcome",
                    context_field="outcome",
                    rule_field="required",
                    match_strategy=MatchStrategy.OUTCOME,
                ),
            ),
        ),
    )

    expected = [
        (
            f"r{rule_outcome}",
            0
            if rule_outcome == 0 or context_outcome == 0
            else 1 if rule_outcome == context_outcome else -1,
            int(rule_outcome != 0 and rule_outcome == context_outcome),
        )
        for rule_outcome in (-1, 0, 1)
    ]
    assert _scores(engine, {"outcome": context_outcome}, "outcome") == expected


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("context", [{}, {"outcome": None}], ids=["omitted", "null"])
def test_an_omitted_or_null_outcome_context_is_unknown_for_every_rule(backend_name, context):
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {"rule_name": ["r-1", "r0", "r1"], "required": [-1, 0, 1]},
            table_name="omitted_outcome_rules",
        ),
        DimensionsMetadata(
            fields=(ContextField(name="outcome", data_type=DataType.INT),),
            dimensions=(
                Dimension(
                    dimension_name="outcome",
                    context_field="outcome",
                    rule_field="required",
                    match_strategy=MatchStrategy.OUTCOME,
                ),
            ),
        ),
    )

    assert _scores(engine, context, "outcome") == [("r-1", 0, 0), ("r0", 0, 0), ("r1", 0, 0)]
    survivors = _rows(engine.evaluate(context).survivors, "rule_name")
    assert survivors == [("r-1",), ("r0",), ("r1",)]


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(
    "context,expected",
    [
        ({"region": None}, [("is_not_null", -1, 0), ("is_null", 1, 1), ("wildcard", 0, 0)]),
        ({"region": "EU"}, [("is_not_null", 1, 1), ("is_null", -1, 0), ("wildcard", 0, 0)]),
        ({}, [("is_not_null", 0, 0), ("is_null", 0, 0), ("wildcard", 0, 0)]),
    ],
)
def test_presence_distinguishes_null_value_and_absent_column(
    backend_name, context, expected
):
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {
                "rule_name": ["is_null", "is_not_null", "wildcard"],
                "required_presence": ["IS_NULL", "IS_NOT_NULL", "*"],
            },
            table_name="presence_rules",
        ),
        DimensionsMetadata(
            fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
            dimensions=(
                Dimension(
                    dimension_name="region_presence",
                    context_field="region",
                    rule_field="required_presence",
                    match_strategy=MatchStrategy.PRESENCE,
                ),
            ),
        ),
    )

    assert _scores(engine, context, "region_presence") == expected


def _worked_null_engine(backend_name):
    return ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {
                "rule_name": ["R1", "R2", "R3"],
                "region": ["EU", "*", "*"],
                "region_presence": ["*", "IS_NULL", "IS_NOT_NULL"],
                "vip": [None, None, True],
            },
            table_name="worked_null_rules",
        ),
        DimensionsMetadata(
            fields=(
                ContextField(name="region", data_type=DataType.STR, wildcard="*"),
                ContextField(
                    name="vip",
                    data_type=DataType.BOOL,
                    wildcard=None,
                    wildcard_declared=True,
                ),
            ),
            dimensions=(
                Dimension(dimension_name="region", context_field="region"),
                Dimension(
                    dimension_name="region_presence",
                    context_field="region",
                    match_strategy=MatchStrategy.PRESENCE,
                ),
                Dimension(dimension_name="vip", context_field="vip"),
            ),
        ),
    )


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(
    "context_data,expected",
    [
        (
            {"cid": ["C1"], "region": ["EU"], "vip": [True]},
            [("R3", 0, 1, 1, 2), ("R1", 1, 0, 0, 1)],
        ),
        (
            {
                "cid": ["C2"],
                "region": pl.Series("region", [None], dtype=pl.String),
                "vip": [False],
            },
            [("R2", 0, 1, 0, 1), ("R1", 0, 0, 0, 0)],
        ),
        (
            {"cid": ["C3"]},
            [("R1", 0, 0, 0, 0), ("R2", 0, 0, 0, 0), ("R3", 0, 0, 0, 0)],
        ),
        (
            {"cid": ["C4"], "region": ["US"], "vip": [True]},
            [("R3", 0, 1, 1, 2)],
        ),
    ],
)
def test_worked_null_example_batch_survivors_and_specificity(
    backend_name, context_data, expected
):
    result = _worked_null_engine(backend_name).evaluate_batch(
        pl.DataFrame(context_data),
        context_id_field="cid",
    )
    rows = relation(result.survivors).to_polars().sort("__rank").select(
        "rule_name",
        "__t_region",
        "__t_region_presence",
        "__t_vip",
        "__specificity",
    ).rows()
    assert rows == expected


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_value_null_is_unknown_but_mapped_truthy_null_is_an_outcome(backend_name):
    value_engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {"rule_name": ["concrete", "wildcard"], "region": ["EU", "*"]},
            table_name="value_null_rules",
        ),
        DimensionsMetadata(
            fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
            dimensions=(Dimension(dimension_name="region", context_field="region"),),
        ),
    )
    assert _scores(value_engine, {"region": None}, "region") == [
        ("concrete", 0, 0),
        ("wildcard", 0, 0),
    ]

    truthy_engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {"rule_name": ["yes", "no"], "required": ["yes", "no"]},
            table_name="truthy_null_rules",
        ),
        DimensionsMetadata(
            fields=(
                ContextField(
                    name="vip",
                    data_type=DataType.STR,
                    truthy={"yes": 1, "no": -1, None: -1},
                ),
            ),
            dimensions=(
                Dimension(
                    dimension_name="vip",
                    context_field="vip",
                    rule_field="required",
                    match_strategy=MatchStrategy.TRUTHY,
                ),
            ),
        ),
    )
    assert _scores(truthy_engine, {"vip": None}, "vip") == [
        ("no", 1, 1),
        ("yes", -1, 0),
    ]
    assert _scores(truthy_engine, {}, "vip") == [
        ("no", 0, 0),
        ("yes", 0, 0),
    ]


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(
    "truthy, context, expected",
    [
        ({Decimal(0): 1}, {}, 0),
        ({Decimal(0): 1}, {"amount": None}, 0),
        ({Decimal(0): 1}, {"amount": Decimal(-999)}, 0),
        ({Decimal(0): 1}, {"amount": Decimal(0)}, 1),
        ({None: -1, Decimal(0): 1}, {}, 0),
        ({None: -1, Decimal(0): 1}, {"amount": None}, -1),
        ({None: -1, Decimal(0): 1}, {"amount": Decimal(0)}, 1),
        ({Decimal(0): 1, None: -1}, {"amount": None}, -1),
    ],
    ids=[
        "omitted", "null", "wildcard", "zero",
        "mapped-null-omitted", "null-first", "mapped-null-zero", "null-last",
    ],
)
def test_decimal_truthy_uses_availability_not_the_internal_payload(
    backend_name, truthy, context, expected
):
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {"rule_name": ["zero"], "required": [Decimal("0.00")]},
            table_name="decimal_truthy_rules",
        ),
        DimensionsMetadata(
            fields=(
                ContextField(
                    name="amount", data_type=DataType.DECIMAL,
                    precision=5, scale=2, rounding="half_to_even",
                    wildcard=Decimal(-999), truthy=truthy,
                ),
            ),
            dimensions=(
                Dimension(
                    dimension_name="amount", context_field="amount",
                    rule_field="required", match_strategy=MatchStrategy.TRUTHY,
                ),
            ),
        ),
    )
    assert _scores(engine, context, "amount") == [("zero", expected, int(expected == 1))]


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_context_regex_missing_and_wildcard_are_unknown(backend_name):
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {"rule_name": ["guard"]},
            table_name="context_regex_rules",
        ),
        DimensionsMetadata(
            fields=(ContextField(name="code", data_type=DataType.STR, wildcard="*"),),
            dimensions=(
                Dimension(
                    dimension_name="code",
                    context_field="code",
                    match_strategy=MatchStrategy.CONTEXT_REGEX,
                    regex_pattern="^EU-",
                ),
            ),
        ),
    )

    assert _scores(engine, {}, "code") == [("guard", 0, 0)]
    assert _scores(engine, {"code": "*"}, "code") == [("guard", 0, 0)]


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(
    "strategy, expected",
    [
        (MatchStrategy.SET_MEMBERSHIP, [("included", 1, 1), ("other", -1, 0), ("wildcard", 0, 0)]),
        (MatchStrategy.SET_EXCLUSION, [("included", -1, 0), ("other", 1, 1), ("wildcard", 0, 0)]),
    ],
)
def test_decimal_set_values_match_at_declared_scale(backend_name, strategy, expected):
    """Membership compares declared-scale decimal values, not Decimal exponents."""
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {
                "rule_name": ["included", "other", "wildcard"],
                "accepted": [
                    [Decimal("1.50")],
                    [Decimal("2.25")],
                    None,
                ],
            },
            table_name="decimal_set_rules",
        ),
        DimensionsMetadata(
            fields=(
                ContextField(
                    name="amount",
                    data_type=DataType.DECIMAL,
                    precision=4,
                    scale=2,
                    rounding="half_to_even",
                    wildcard=None,
                    wildcard_declared=True,
                ),
            ),
            dimensions=(
                Dimension(
                    dimension_name="amount",
                    context_field="amount",
                    rule_field="accepted",
                    match_strategy=strategy,
                ),
            ),
        ),
    )

    assert _scores(engine, {"amount": Decimal("1.5")}, "amount") == expected




@pytest.mark.parametrize("backend_name", BACKENDS)
def test_decimal_exact_match_uses_declared_scale_without_ordering(backend_name):
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {
                "rule_name": ["included", "other"],
                "amount": [Decimal("1.50"), Decimal("2.25")],
            },
            table_name="decimal_exact_rules",
        ),
        DimensionsMetadata(
            fields=(
                ContextField(
                    name="amount",
                    data_type=DataType.DECIMAL,
                    precision=4,
                    scale=2,
                    rounding="half_to_even",
                ),
            ),
            dimensions=(Dimension(dimension_name="amount", context_field="amount"),),
        ),
    )

    assert _scores(engine, {"amount": Decimal("1.5")}, "amount") == [
        ("included", 1, 1),
        ("other", -1, 0),
    ]
@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize(
    "strategy, expected",
    [
        (MatchStrategy.SET_MEMBERSHIP, [("false", -1, 0), ("true", 1, 1), ("wildcard", 0, 0)]),
        (MatchStrategy.SET_EXCLUSION, [("false", 1, 1), ("true", -1, 0), ("wildcard", 0, 0)]),
    ],
)
def test_bool_set_declared_null_wildcard(backend_name, strategy, expected):
    """A declared null set wildcard remains distinct from concrete Boolean sets."""
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {
                "rule_name": ["true", "false", "wildcard"],
                "accepted": [[True], [False], None],
            },
            table_name="bool_set_rules",
        ),
        DimensionsMetadata(
            fields=(
                ContextField(
                    name="enabled",
                    data_type=DataType.BOOL,
                    wildcard=None,
                    wildcard_declared=True,
                ),
            ),
            dimensions=(
                Dimension(
                    dimension_name="enabled",
                    context_field="enabled",
                    rule_field="accepted",
                    match_strategy=strategy,
                ),
            ),
        ),
    )

    assert _scores(engine, {"enabled": True}, "enabled") == expected
