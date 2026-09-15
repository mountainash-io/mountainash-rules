"""Typed absence and sentinel precedence across the filter entry points."""

from decimal import Decimal
from enum import IntFlag

from datetime import date, datetime

import polars as pl
import pytest
from pydantic import BaseModel
import numpy as np


import mountainash.expressions as ma
from mountainash.relations import relation
from mountainash_rules import (
    BooleanCoercion,
    DataType,
    Dimension,
    DimensionCompiler,
    DimensionsMetadata,
    ExpressionRulesEngine,
    MatchStrategy,
    NOT_SET,
    UNKNOWN,
)
from mountainash_rules.core.constants import not_set_sentinel_for, unknown_sentinel_for
from tests.conftest import build_backend_df


def _scores(frame):
    return (
        relation(frame)
        .to_polars()
        .sort("rule_name")
        .select("rule_name", "__t_value", "__specificity")
        .rows()
    )


@pytest.mark.parametrize("strategy", ["exact", "not_equal", "exact_key"])
def test_boolean_truth_table_across_entry_points(backend_name, strategy):
    if backend_name == "ibis-polars":
        pytest.skip(
            "mountainash#78: engine with_row_index lacks WindowFunction translation"
        )
    rules = build_backend_df(
        backend_name,
        {
            "rule_name": ["false", "true", "wildcard"],
            "flag": [False, True, None],
        },
    )
    engine = ExpressionRulesEngine(
        rules,
        DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name="value",
                    rule_field="flag",
                    context_field="active",
                    data_type=DataType.BOOL,
                    match_strategy=strategy,
                ),
            ]
        ),
    )

    class Context(BaseModel):
        active: bool | None = None

    for context in (
        {},
        {"active": None},
        {"active": False},
        {"active": True},
        Context(),
    ):
        value = (
            context.active if isinstance(context, Context) else context.get("active")
        )
        ternaries = [
            (0 if strategy != "exact_key" else -1)
            if value is None
            else (1 if (rule == value) == (strategy != "not_equal") else -1)
            for rule in (False, True)
        ] + [0]
        names = ["false", "true", "wildcard"]
        expected = [
            (name, score, int(score == 1)) for name, score in zip(names, ternaries)
        ]
        assert _scores(engine.explain(context).frame) == expected
        survivors = [row for row in expected if row[1] >= 0]
        assert _scores(engine.evaluate(context).survivors) == survivors
        data = context.model_dump() if isinstance(context, Context) else context
        contexts = pl.DataFrame(
            {"cid": [0], **{key: [val] for key, val in data.items()}}
        )
        for chunk_size in (None, 1):
            batch = engine.evaluate_batch(
                contexts, context_id_field="cid", chunk_size=chunk_size
            )
            assert _scores(batch.survivors) == survivors


@pytest.mark.parametrize(
    "strategy",
    [MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL, MatchStrategy.EXACT_KEY],
)
@pytest.mark.parametrize("concrete", [2, -1])
@pytest.mark.parametrize("entry_point", ["evaluate", "explain", "batch", "chunked"])
def test_boolean_invalid_concrete_rejected_across_entry_points(
    backend_name, strategy, concrete, entry_point
):
    rules = build_backend_df(
        backend_name,
        {
            "rule_name": ["false", "true", "wildcard"],
            "flag": [False, True, None],
        },
    )
    engine = ExpressionRulesEngine(
        rules,
        DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name="value",
                    rule_field="flag",
                    context_field="active",
                    data_type=DataType.BOOL,
                    match_strategy=strategy,
                ),
            ]
        ),
    )
    context = {"active": concrete}
    contexts = pl.DataFrame({"active": [concrete]})
    entry_points = {
        "evaluate": lambda: engine.evaluate(context),
        "explain": lambda: engine.explain(context),
        "batch": lambda: engine.evaluate_batch(contexts),
        "chunked": lambda: engine.evaluate_batch(contexts, chunk_size=1),
    }

    with pytest.raises(ValueError):
        entry_points[entry_point]()


def test_boolean_all_null_and_empty_batches_without_wildcards(backend_name):
    if backend_name == "ibis-polars":
        pytest.skip(
            "mountainash#78: engine with_row_index lacks WindowFunction translation"
        )
    rules = build_backend_df(
        backend_name, {"rule_name": ["false", "true"], "value": [False, True]}
    )
    engine = ExpressionRulesEngine(
        rules,
        DimensionsMetadata(
            dimensions=[
                Dimension(dimension_name="value", data_type=DataType.BOOL),
            ]
        ),
    )
    contexts = pl.DataFrame({"cid": [0, 1], "value": [None, None]})
    batch = relation(
        engine.evaluate_batch(contexts, context_id_field="cid").survivors
    ).to_polars()
    assert batch.sort("__context_id", "rule_name").select(
        "__context_id", "rule_name", "__t_value", "__specificity"
    ).rows() == [(cid, name, 0, 0) for cid in (0, 1) for name in ("false", "true")]
    empty = relation(
        engine.evaluate_batch(contexts.head(0), context_id_field="cid").survivors
    ).to_polars()
    assert empty.rows() == []
    assert empty.schema["value"] == pl.Boolean


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb", "ibis-sqlite"])
@pytest.mark.parametrize(
    "strategy,pattern,concrete",
    [
        ("prefix", "<", "<valid"),
        ("suffix", ">", "valid>"),
        ("contains", "NOT_SET", "valid NOT_SET text"),
        ("regex", "^<NOT_SET>$", "<NOT_SET>"),
    ],
)
def test_ordinary_string_sentinels_never_earn_specificity(
    backend_name, strategy, pattern, concrete
):
    if strategy == "regex" and backend_name != "polars":
        pytest.skip("Per-row REGEX requires the existing Polars-native expression")
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {
                "rule_name": ["pattern", "unknown", "unset"],
                "value": [pattern, UNKNOWN, NOT_SET],
            },
        ),
        DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name="value", match_strategy=MatchStrategy(strategy)
                ),
            ]
        ),
    )
    contexts = [
        {},
        {"value": None},
        {"value": UNKNOWN},
        {"value": NOT_SET},
        {"value": concrete},
        {"value": "different"},
        {"value": ""},
    ]
    for context in contexts:
        value = context.get("value")
        score = (
            0 if value in (None, UNKNOWN, NOT_SET) else (1 if value == concrete else -1)
        )
        expected = [
            ("pattern", score, int(score == 1)),
            ("unknown", 0, 0),
            ("unset", 0, 0),
        ]
        assert _scores(engine.explain(context).frame) == expected
        survivors = [row for row in expected if row[1] >= 0]
        assert _scores(engine.evaluate(context).survivors) == survivors
        frame = pl.DataFrame(
            {"cid": [0], **{key: [val] for key, val in context.items()}}
        )
        for chunk_size in (None, 1):
            assert (
                _scores(engine.evaluate_batch(frame, chunk_size=chunk_size).survivors)
                == survivors
            )
    if strategy == "regex":
        assert engine.evaluate({"value": "A"}).count == 2
        # Concrete regex controls must still execute rather than always becoming unknown.
        matching = ExpressionRulesEngine(
            pl.DataFrame({"rule_name": ["pattern"], "value": ["^A$"]}),
            DimensionsMetadata(
                dimensions=[
                    Dimension(
                        dimension_name="value", match_strategy=MatchStrategy.REGEX
                    ),
                ]
            ),
        )
        assert _scores(matching.evaluate({"value": "A"}).survivors) == [
            ("pattern", 1, 1)
        ]


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb", "ibis-sqlite"])
@pytest.mark.parametrize("pattern", ["^.+$", "^.*$"])
def test_context_regex_requires_concrete_input(backend_name, pattern):
    engine = ExpressionRulesEngine(
        build_backend_df(backend_name, {"rule_name": ["guard"]}),
        DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name="value",
                    match_strategy=MatchStrategy.CONTEXT_REGEX,
                    regex_pattern=pattern,
                ),
            ]
        ),
    )
    contexts = [
        {},
        {"value": None},
        {"value": UNKNOWN},
        {"value": NOT_SET},
        {"value": ""},
        {"value": "A"},
    ]
    for context in contexts:
        value = context.get("value")
        score = 1 if value == "A" or (value == "" and pattern == "^.*$") else -1
        assert _scores(engine.explain(context).frame) == [
            ("guard", score, int(score == 1))
        ]
        expected = [("guard", 1, 1)] if score == 1 else []
        assert _scores(engine.evaluate(context).survivors) == expected
        frame = pl.DataFrame(
            {"cid": [0], **{key: [val] for key, val in context.items()}}
        )
        for chunk_size in (None, 1):
            assert (
                _scores(engine.evaluate_batch(frame, chunk_size=chunk_size).survivors)
                == expected
            )


@pytest.mark.parametrize(
    "dtype,concrete",
    [
        (DataType.STR, "A"),
        (DataType.INT, 0),
        (DataType.FLOAT, 0.0),
        (DataType.DATE, date(2026, 9, 12)),
        (DataType.DATETIME, datetime(2026, 9, 12)),
    ],
)
def test_exact_key_missing_marker_equality_is_not_a_match(dtype, concrete):
    unknown = unknown_sentinel_for(dtype)
    unset = not_set_sentinel_for(dtype)
    if dtype is DataType.FLOAT:
        unknown, unset = float(unknown), float(unset)
    dim = Dimension(
        dimension_name="value", data_type=dtype, match_strategy=MatchStrategy.EXACT_KEY
    )
    # A direct compiler caller must not get a hard match from equal NOT_SET markers.
    frame = pl.DataFrame(
        {
            "value": [concrete, unknown, unset, concrete],
            "__ctx_value": [unset, unset, unset, concrete],
        }
    )
    scored = (
        relation(frame)
        .with_columns(DimensionCompiler().compile_dimension(dim).alias("t"))
        .to_polars()
    )
    assert scored["t"].to_list() == [-1, 0, -1, 1]


def test_shared_aliases_and_unknown_do_not_rescue_mismatch():
    engine = ExpressionRulesEngine(
        pl.DataFrame(
            {
                "rule_name": ["pass", "fail"],
                "flag": [True, False],
                "pattern": ["<", UNKNOWN],
            }
        ),
        DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name="left",
                    rule_field="flag",
                    context_field="active",
                    data_type=DataType.BOOL,
                ),
                Dimension(
                    dimension_name="right",
                    rule_field="flag",
                    context_field="active",
                    data_type=DataType.BOOL,
                ),
                Dimension(
                    dimension_name="text",
                    rule_field="pattern",
                    match_strategy=MatchStrategy.PREFIX,
                ),
            ]
        ),
    )
    columns = ["rule_name", "__t_left", "__t_right", "__t_text", "__specificity"]
    assert relation(engine.explain({"active": True}).frame).to_polars().select(
        columns
    ).rows() == [
        ("pass", 1, 1, 0, 2),
        ("fail", -1, -1, 0, 0),
    ]
    single = relation(engine.evaluate({"active": True}).survivors).to_polars()
    batch = relation(
        engine.evaluate_batch(pl.DataFrame({"active": [True]})).survivors
    ).to_polars()
    assert (
        single.select(columns).rows()
        == batch.select(columns).rows()
        == [("pass", 1, 1, 0, 2)]
    )


def test_expressions_only_retains_string_not_set_binding():
    engine = ExpressionRulesEngine(
        pl.DataFrame({"rule_name": ["custom"]}),
        dimension_expressions={
            "value": ma.when(ma.col("__ctx_value").eq(ma.lit(NOT_SET)))
            .then(1)
            .otherwise(-1),
        },
    )
    assert _scores(engine.evaluate({}).survivors) == [("custom", 1, 1)]
    assert _scores(engine.evaluate_batch(pl.DataFrame({"cid": [0]})).survivors) == [
        ("custom", 1, 1)
    ]


def _boolean_coercion(bits: int):
    from mountainash_rules import BooleanCoercion

    return BooleanCoercion(bits)


def _boolean_engine(policy=BooleanCoercion.NONE, *, rules=None, metadata=None):
    return ExpressionRulesEngine(
        (
            pl.DataFrame(
                {
                    "rule_name": ["false", "true"],
                    "flag": [False, True],
                }
            )
            if rules is None
            else rules
        ),
        dimension_metadata=(
            DimensionsMetadata(
                dimensions=[
                    Dimension(
                        dimension_name="value",
                        rule_field="flag",
                        context_field="active",
                        data_type=DataType.BOOL,
                    )
                ]
            )
            if metadata is None
            else metadata
        ),
        boolean_coercion=policy,
    )


def _boolean_survivors(engine, value):
    return engine.evaluate({"active": value}).survivors["rule_name"].to_list()


@pytest.mark.parametrize(
    "policy,value,expected",
    [
        (0, True, ["true"]),
        (1, np.int64(1), ["true"]),
        (2, " \tTrUe\n", ["true"]),
        (4, -0.0, ["false"]),
        (3, "0", ["false"]),
        (3, 1, ["true"]),
        (5, np.int64(-1), ["true"]),
        (6, 0.5, ["true"]),
        (7, " \vFALSE\f", ["false"]),
    ],
)
def test_boolean_coercion_combinations_admit_distinguishing_original_domains(
    policy, value, expected
):
    assert (
        _boolean_survivors(_boolean_engine(_boolean_coercion(policy)), value)
        == expected
    )


@pytest.mark.parametrize(
    "policy,value",
    [
        (0, 1),
        (0, 0),
        (0, 0.0),
        (0, 1.0),
        (0, "true"),
        (0, "0"),
        (1, 0.5),
        (2, 1),
        (4, "true"),
        (3, "2"),
        (5, float("inf")),
        (6, "2"),
        (7, "2"),
    ],
)
def test_boolean_coercion_combinations_reject_unadmitted_original_domains(
    policy, value
):
    with pytest.raises(ValueError):
        _boolean_survivors(_boolean_engine(_boolean_coercion(policy)), value)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, ["false"]),
        (-0.0, ["false"]),
        (np.float64(1.0), ["true"]),
    ],
)
def test_binary_boolean_coercion_accepts_only_exact_zero_or_one(value, expected):
    assert _boolean_survivors(_boolean_engine(_boolean_coercion(1)), value) == expected


@pytest.mark.parametrize(
    "value",
    [0.5, -1, 2**100 + 1, float("nan"), float("inf"), float("-inf")],
)
def test_binary_boolean_coercion_rejects_fraction_large_and_nonfinite_numbers(value):
    with pytest.raises(ValueError):
        _boolean_survivors(_boolean_engine(_boolean_coercion(1)), value)


@pytest.mark.parametrize(
    "value,expected",
    [
        (np.float64(-0.0), ["false"]),
        (np.float64(0.5), ["true"]),
        (np.int64(-1), ["true"]),
        (2**100 + 1, ["true"]),
    ],
)
def test_numeric_truthiness_preserves_numeric_meaning_without_narrowing(
    value, expected
):
    assert _boolean_survivors(_boolean_engine(_boolean_coercion(4)), value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "yes",
        "no",
        "<NA>",
        "<NOT_SET>",
        "t rue",
        "\u00a0true",
        "true\u00a0",
        "2",
        b"true",
        Decimal("1"),
        1 + 0j,
        [],
        object(),
    ],
)
def test_boolean_text_and_unsupported_scalars_reject_outside_exact_policy_domain(value):
    policy = _boolean_coercion(7)
    with pytest.raises(ValueError):
        _boolean_survivors(_boolean_engine(policy), value)


class _ForeignBooleanCoercion(IntFlag):
    BINARY = 1


@pytest.mark.parametrize(
    "value,unknown_bits",
    [
        (0, False),
        (False, False),
        ("binary", False),
        (None, False),
        (_ForeignBooleanCoercion.BINARY, False),
        (8, True),
    ],
)
def test_boolean_coercion_constructor_rejects_non_enum_values_and_unknown_bits(
    value, unknown_bits
):
    if unknown_bits:
        value = _boolean_coercion(value)
    with pytest.raises(ValueError):
        _boolean_engine(value)


def test_semantic_python_and_numpy_booleans_are_admitted_without_conversion():
    engine = _boolean_engine()
    assert _boolean_survivors(engine, False) == ["false"]
    assert _boolean_survivors(engine, np.bool_(True)) == ["true"]


@pytest.mark.parametrize(
    "context_backend",
    [
        "polars",
        "pandas",
        "narwhals-polars",
        "narwhals-pandas",
        "ibis-duckdb",
        "ibis-sqlite",
    ],
)
def test_native_boolean_and_null_context_columns_retain_ternaries(context_backend):
    contexts = build_backend_df(
        context_backend,
        {"cid": [0, 1, 2], "active": [True, False, None]},
        table_name=f"boolean_context_{context_backend.replace('-', '_')}",
    )
    result = relation(
        _boolean_engine().evaluate_batch(contexts, context_id_field="cid").survivors
    ).to_polars()
    assert result.sort("__context_id", "rule_name").select(
        "__context_id", "rule_name", "__t_value", "__specificity"
    ).rows() == [
        (0, "true", 1, 1),
        (1, "false", 1, 1),
        (2, "false", 0, 0),
        (2, "true", 0, 0),
    ]


@pytest.mark.parametrize(
    "active",
    [
        pl.Series("active", [None, None], dtype=pl.Boolean),
        pl.Series("active", [None, None], dtype=pl.Object),
    ],
)
def test_boolean_nullable_and_object_all_null_batches_remain_absent(active):
    contexts = pl.DataFrame({"cid": [0, 1], "active": active})
    batch = relation(
        _boolean_engine().evaluate_batch(contexts, context_id_field="cid").survivors
    ).to_polars()
    assert batch.sort("__context_id", "rule_name").select(
        "__context_id", "rule_name", "__t_value", "__specificity"
    ).rows() == [
        (0, "false", 0, 0),
        (0, "true", 0, 0),
        (1, "false", 0, 0),
        (1, "true", 0, 0),
    ]
    empty = (
        _boolean_engine()
        .evaluate_batch(contexts.head(0), context_id_field="cid")
        .survivors
    )
    assert relation(empty).to_polars().schema["flag"] == pl.Boolean


def test_object_context_values_are_classified_before_backend_conversion():
    policy = (
        BooleanCoercion.BINARY_NUMBERS
        | BooleanCoercion.BOOLEAN_TEXT
        | BooleanCoercion.NUMERIC_TRUTHINESS
    )
    engine = _boolean_engine(policy)
    valid = pl.DataFrame(
        {
            "cid": [0, 1, 2, 3],
            "active": pl.Series(
                "active",
                [np.bool_(True), np.int64(0), " false ", None],
                dtype=pl.Object,
            ),
        }
    )
    result = relation(
        engine.evaluate_batch(valid, context_id_field="cid").survivors
    ).to_polars()
    assert result.sort("__context_id", "rule_name").select(
        "__context_id", "rule_name", "__t_value", "__specificity"
    ).rows() == [
        (0, "true", 1, 1),
        (1, "false", 1, 1),
        (2, "false", 1, 1),
        (3, "false", 0, 0),
        (3, "true", 0, 0),
    ]
    invalid = valid.vstack(
        pl.DataFrame(
            {
                "cid": [4],
                "active": pl.Series("active", [object()], dtype=pl.Object),
            }
        )
    )
    with pytest.raises(ValueError):
        engine.evaluate_batch(invalid, context_id_field="cid")


def test_boolean_alias_normalization_preserves_a_shared_nonboolean_binding():
    rules = pl.DataFrame(
        {
            "rule_name": ["both"],
            "bool_flag": [True],
            "number_flag": [1],
        }
    )
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="as_boolean",
                rule_field="bool_flag",
                context_field="active",
                data_type=DataType.BOOL,
            ),
            Dimension(
                dimension_name="as_number",
                rule_field="number_flag",
                context_field="active",
                data_type=DataType.INT,
            ),
        ]
    )
    contexts = pl.DataFrame({"cid": [7], "active": [1]})
    result = _boolean_engine(
        _boolean_coercion(1), rules=rules, metadata=metadata
    ).evaluate_batch(contexts, context_id_field="cid")
    assert relation(result.survivors).to_polars().select(
        "rule_name", "__t_as_boolean", "__t_as_number", "__specificity"
    ).rows() == [("both", 1, 1, 2)]
    assert contexts.schema["active"] == pl.Int64
    assert contexts["active"].to_list() == [1]


def test_unselected_boolean_dimension_does_not_validate_its_source_field():
    rules = pl.DataFrame({"rule_name": ["region"], "flag": [True], "region": ["AU"]})
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="boolean",
                rule_field="flag",
                context_field="active",
                data_type=DataType.BOOL,
            ),
            Dimension(dimension_name="region"),
        ]
    )
    result = _boolean_engine(
        _boolean_coercion(0), rules=rules, metadata=metadata
    ).evaluate_batch(
        pl.DataFrame({"active": [2], "region": ["AU"]}),
        dimensions=["region"],
    )
    assert result.survivors["rule_name"].to_list() == ["region"]
