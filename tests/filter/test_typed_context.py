"""Typed absence and sentinel precedence across the filter entry points."""

from datetime import date, datetime

import polars as pl
import pytest
from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.relations import relation
from mountainash_rules import (
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
