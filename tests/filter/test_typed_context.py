"""Typed absence and sentinel precedence across the filter entry points."""

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
    ContextField,
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
from tests.conftest import build_backend_df, bind_compiler_inputs


def _scores(frame):
    return (
        relation(frame)
        .to_polars()
        .sort("rule_name")
        .select("rule_name", "__t_value", "__specificity")
        .rows()
    )


@pytest.mark.parametrize("strategy", ["exact", "not_equal"])
def test_boolean_truth_table_across_entry_points(backend_name, strategy):
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
            fields=(ContextField(name="active", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True),),
            dimensions=[
                Dimension(
                    dimension_name="value", rule_field="flag", context_field="active",
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
            0 if value is None else (1 if (rule == value) == (strategy != "not_equal") else -1)
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



def test_boolean_all_null_and_empty_batches_without_wildcards(backend_name):
    rules = build_backend_df(
        backend_name, {"rule_name": ["false", "true"], "value": [False, True]}
    )
    engine = ExpressionRulesEngine(
        rules,
        DimensionsMetadata(
            fields=(ContextField(name="value", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True),),
            dimensions=(Dimension(dimension_name="value", context_field="value"),),
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


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
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
    engine = ExpressionRulesEngine(
        build_backend_df(
            backend_name,
            {
                "rule_name": ["pattern", "unknown"],
                "value": [pattern, UNKNOWN],
            },
        ),
        DimensionsMetadata(
            fields=(ContextField(name="value", data_type=DataType.STR, wildcard=UNKNOWN),),
            dimensions=[
                Dimension(
                    dimension_name="value", context_field="value",
                    match_strategy=MatchStrategy(strategy),
                ),
            ]
        ),
    )
    # Only the declared wildcard (UNKNOWN) is a sentinel; NOT_SET is an ordinary string.
    contexts = [
        {},
        {"value": None},
        {"value": UNKNOWN},
        {"value": concrete},
        {"value": "different"},
        {"value": ""},
    ]
    for context in contexts:
        value = context.get("value")
        score = 0 if value in (None, UNKNOWN) else (1 if value == concrete else -1)
        expected = [
            ("pattern", score, int(score == 1)),
            ("unknown", 0, 0),
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
        assert engine.evaluate({"value": "A"}).count == 1
        # Concrete regex controls must still execute rather than always becoming unknown.
        matching = ExpressionRulesEngine(
            pl.DataFrame({"rule_name": ["pattern"], "value": ["^A$"]}),
            DimensionsMetadata(
                fields=(ContextField(name="value", data_type=DataType.STR, wildcard=UNKNOWN),),
                dimensions=(Dimension(
                    dimension_name="value", context_field="value",
                    match_strategy=MatchStrategy.REGEX,
                ),),
            ),
        )
        assert _scores(matching.evaluate({"value": "A"}).survivors) == [
            ("pattern", 1, 1)
        ]


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
@pytest.mark.parametrize("pattern", ["^.+$", "^.*$"])
def test_context_regex_requires_concrete_input(backend_name, pattern):
    engine = ExpressionRulesEngine(
        build_backend_df(backend_name, {"rule_name": ["guard"]}),
        DimensionsMetadata(
            fields=(ContextField(name="value", data_type=DataType.STR, wildcard=UNKNOWN),),
            dimensions=(Dimension(
                dimension_name="value", context_field="value",
                match_strategy=MatchStrategy.CONTEXT_REGEX,
                regex_pattern=pattern,
            ),),
        ),
    )
    contexts = [
        {},
        {"value": None},
        {"value": UNKNOWN},
        {"value": ""},
        {"value": "A"},
    ]
    for context in contexts:
        value = context.get("value")
        score = (
            0
            if value in (None, UNKNOWN)
            else (1 if value == "A" or (value == "" and pattern == "^.*$") else -1)
        )
        assert _scores(engine.explain(context).frame) == [
            ("guard", score, int(score == 1))
        ]
        expected = [("guard", 1, 1)] if score == 1 else (
            [("guard", 0, 0)] if score == 0 else []
        )
        assert _scores(engine.evaluate(context).survivors) == expected
        frame = pl.DataFrame(
            {"cid": [0], **{key: [val] for key, val in context.items()}}
        )
        for chunk_size in (None, 1):
            assert (
                _scores(engine.evaluate_batch(frame, chunk_size=chunk_size).survivors)
                == expected
            )


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
            fields=(ContextField(name="active", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True), ContextField(name="text", data_type=DataType.STR, wildcard=UNKNOWN)),
            dimensions=[
                Dimension(
                    dimension_name="left", rule_field="flag", context_field="active",
                ),
                Dimension(
                    dimension_name="right", rule_field="flag", context_field="active",
                ),
                Dimension(
                    dimension_name="text", context_field="text",
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


def test_expressions_only_binds_unavailable_context_as_null():
    engine = ExpressionRulesEngine(
        pl.DataFrame({"rule_name": ["custom"]}),
        dimension_expressions={
            "value": ma.when(ma.col("__ctx_value").is_null()).then(1).otherwise(-1),
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
                fields=(ContextField(name="active", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True),),
                dimensions=[
                    Dimension(
                        dimension_name="value", rule_field="flag", context_field="active",
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
        "ibis-duckdb",
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


def test_unselected_boolean_dimension_does_not_validate_its_source_field():
    rules = pl.DataFrame({"rule_name": ["region"], "flag": [True], "region": ["AU"]})
    metadata = DimensionsMetadata(
        fields=(ContextField(name="active", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True), ContextField(name="region", data_type=DataType.STR, wildcard=UNKNOWN)),
        dimensions=[
            Dimension(
                dimension_name="boolean", rule_field="flag", context_field="active",
            ),
            Dimension(
                dimension_name="region", context_field="region",
            ),
        ]
    )
    result = _boolean_engine(
        _boolean_coercion(0), rules=rules, metadata=metadata
    ).evaluate_batch(
        pl.DataFrame({"active": [2], "region": ["AU"]}),
        dimensions=["region"],
    )
    assert result.survivors["rule_name"].to_list() == ["region"]
