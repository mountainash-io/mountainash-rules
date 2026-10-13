"""Shared state and DECIMAL semantics at the filter consumer boundary."""

from datetime import date, datetime
from decimal import Decimal

import pytest
import polars as pl
import mountainash.expressions as ma
from mountainash import DecimalDtype
from mountainash.relations import relation

from mountainash_rules import (
    BooleanCoercion,
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    MatchStrategy,
)
from tests.conftest import build_backend_df


def _engine(backend, data, *dimensions, **kwargs):
    return ExpressionRulesEngine(
        build_backend_df(backend, data),
        DimensionsMetadata(dimensions=list(dimensions)),
        **kwargs,
    )


def _rows(frame, *columns):
    return relation(frame).select(*columns).to_dicts()


def _scores(engine, context, name="value"):
    return {
        row["rule"]: (row[f"__t_{name}"], row["__survived"])
        for row in _rows(
            engine.explain(context).frame, "rule", f"__t_{name}", "__survived"
        )
    }


def _decimal(**kwargs):
    return Dimension(
        dimension_name="amount",
        context_field="quoted",
        match_strategy="range",
        data_type="decimal",
        numeric_dtype=DecimalDtype(10, 3),
        rounding="half_to_even",
        range_min_field="floor",
        range_max_field="ceiling",
        **kwargs,
    )


@pytest.mark.parametrize("value", [-999999999, -999999998])
def test_reclaimed_integer_singletons_and_explicit_unbounded(backend_name, value):
    engine = _engine(
        backend_name,
        {
            "rule": ["singleton", "unbounded"],
            "lo": [value, 0],
            "hi": [value, 0],
            "open_lo": [False, True],
            "open_hi": [False, True],
        },
        Dimension(
            dimension_name="value",
            data_type="int",
            match_strategy="range",
            range_min_field="lo",
            range_max_field="hi",
            range_min_unbounded_field="open_lo",
            range_max_unbounded_field="open_hi",
        ),
    )
    assert _scores(engine, {"value": value}) == {
        "singleton": (1, True),
        "unbounded": (0, True),
    }
    assert _scores(engine, {"value": value + 1}) == {
        "singleton": (-1, False),
        "unbounded": (0, True),
    }
    assert _scores(engine, {}) == {"singleton": (0, True), "unbounded": (0, True)}


@pytest.mark.parametrize(
    "data_type,value",
    [
        ("str", "<NA>"),
        ("bool", False),
        ("date", date(1, 1, 1)),
        ("datetime", datetime(1, 1, 2)),
    ],
)
def test_reclaimed_typed_values_and_flag_preserve_authored_payload(
    backend_name, data_type, value
):
    engine = _engine(
        backend_name,
        {
            "rule": ["literal", "wildcard"],
            "payload": [value, value],
            "any_value": [False, True],
        },
        Dimension(
            dimension_name="value",
            rule_field="payload",
            data_type=data_type,
            rule_wildcard_field="any_value",
        ),
    )
    assert _scores(engine, {"value": value}) == {
        "literal": (1, True),
        "wildcard": (0, True),
    }
    rows = _rows(
        engine.evaluate({"value": value}).survivors, "rule", "payload", "any_value"
    )
    assert {row["rule"]: (row["payload"], row["any_value"]) for row in rows} == {
        "literal": (value, False),
        "wildcard": (value, True),
    }


def test_marker_declarations_are_per_alias_not_a_compatibility_gate(backend_name):
    engine = _engine(
        backend_name,
        {"rule": ["matches_b", "misses_b"], "a": ["EU", "US"], "b": ["ANY", "EU"]},
        Dimension(dimension_name="a", context_field="region", wildcard_values=["ANY"]),
        Dimension(dimension_name="b", context_field="region"),
    )
    assert _rows(
        engine.evaluate({"region": "ANY"}).survivors, "rule", "__t_a", "__t_b"
    ) == [
        {"rule": "matches_b", "__t_a": 0, "__t_b": 1},
    ]
    contexts = build_backend_df(backend_name, {"cid": [10], "region": ["ANY"]})
    for chunk_size in (None, 1):
        result = engine.evaluate_batch(
            contexts, context_id_field="cid", chunk_size=chunk_size
        )
        assert _rows(result.survivors, "rule", "__t_a", "__t_b") == [
            {"rule": "matches_b", "__t_a": 0, "__t_b": 1},
        ]
    assert {
        row["rule"]
        for row in _rows(
            engine.evaluate({"region": "ANY"}, dimensions=["a"]).survivors, "rule"
        )
    } == {"matches_b", "misses_b"}


@pytest.mark.parametrize("marker", [0, "0"])
def test_boolean_marker_equality_uses_original_type(backend_name, marker):
    engine = _engine(
        backend_name,
        {"rule": ["false"], "value": [False]},
        Dimension(dimension_name="value", data_type="bool", wildcard_values=[marker]),
        boolean_coercion=BooleanCoercion.BINARY_NUMBERS | BooleanCoercion.BOOLEAN_TEXT,
    )
    assert _scores(engine, {"value": False}) == {"false": (1, True)}
    assert _scores(engine, {"value": marker}) == {"false": (0, True)}
    contexts = build_backend_df(backend_name, {"value": [marker]})
    assert _rows(engine.evaluate_batch(contexts).survivors, "__t_value") == [
        {"__t_value": 0}
    ]


@pytest.mark.parametrize("context", [{}, {"value": None}, {"value": "ANY"}])
def test_unavailable_exact_key_does_not_match_concrete_key(backend_name, context):
    engine = _engine(
        backend_name,
        {"rule": ["key", "wildcard"], "value": ["EU", "ANY"]},
        Dimension(
            dimension_name="value",
            match_strategy="exact_key",
            wildcard_values=[None, "ANY"],
        ),
    )
    assert _scores(engine, context) == {"key": (-1, False), "wildcard": (0, True)}


@pytest.mark.parametrize("context", [{}, {"value": None}, {"value": "ANY"}])
def test_context_regex_cannot_be_bypassed_by_markers(backend_name, context):
    engine = _engine(
        backend_name,
        {"rule": ["guarded"]},
        Dimension(
            dimension_name="value",
            match_strategy="context_regex",
            regex_pattern="^EU$",
            wildcard_values=[None, "ANY"],
        ),
    )
    assert _scores(engine, context) == {"guarded": (-1, False)}
    assert _scores(engine, {"value": "EU"}) == {"guarded": (1, True)}


@pytest.mark.parametrize(
    "strategy,empty_score", [("set_membership", -1), ("set_exclusion", 1)]
)
def test_sets_distinguish_empty_null_declared_and_reclaimed_singletons(
    backend_name, strategy, empty_score
):
    engine = _engine(
        backend_name,
        {
            "rule": ["empty", "null", "declared", "literal"],
            "value": [[], None, ["ANY"], ["<NA>"]],
        },
        Dimension(
            dimension_name="value",
            match_strategy=strategy,
            wildcard_values=[None, "ANY"],
        ),
    )
    assert _scores(engine, {"value": "EU"}) == {
        "empty": (empty_score, empty_score >= 0),
        "null": (0, True),
        "declared": (0, True),
        "literal": (empty_score, empty_score >= 0),
    }
    literal_score = 1 if strategy == "set_membership" else -1
    assert _scores(engine, {"value": "<NA>"})["literal"] == (
        literal_score,
        literal_score >= 0,
    )


@pytest.mark.parametrize("payload", [["ANY", "EU"], [None], None])
def test_invalid_set_payload_is_not_implicit_wildcard(backend_name, payload):
    with pytest.raises(ValueError):
        engine = _engine(
            backend_name,
            {"rule": ["bad", "control"], "value": [payload, ["EU"]]},
            Dimension(
                dimension_name="value",
                match_strategy="set_membership",
                wildcard_values=["ANY"],
            ),
        )
        engine.evaluate({"value": "EU"})


def test_true_set_flag_permits_unused_marker_mixture(backend_name):
    engine = _engine(
        backend_name,
        {"rule": ["wildcard"], "value": [["ANY", "EU"]], "wild": [True]},
        Dimension(
            dimension_name="value",
            match_strategy="set_membership",
            wildcard_values=["ANY"],
            rule_wildcard_field="wild",
        ),
    )
    assert _scores(engine, {"value": "US"}) == {"wildcard": (0, True)}
    assert _rows(engine.evaluate({"value": "US"}).survivors, "value") == [
        {"value": ["ANY", "EU"]}
    ]


@pytest.mark.parametrize("flags", [None, [True, None], [1], ["true"], [False]])
def test_mapped_flag_requires_boolean_and_rejects_marker_conflict(backend_name, flags):
    count = len(flags) if flags is not None else 1
    data = {
        "rule": [f"bad_{index}" for index in range(count)],
        "value": ["ANY"] * count,
    }
    if flags is not None:
        data["wild"] = flags
    with pytest.raises(ValueError):
        engine = _engine(
            backend_name,
            data,
            Dimension(
                dimension_name="value",
                wildcard_values=["ANY"],
                rule_wildcard_field="wild",
            ),
        )
        engine.evaluate({"value": "EU"})


def test_decimal_range_survivors_scalar_explain_batch_and_chunking(backend_name):
    engine = _engine(
        backend_name,
        {
            "rule": ["S", "U"],
            "floor": ["0.004", "0.010"],
            "ceiling": ["0.004", "0.020"],
            "kind": ["singleton", "upper"],
        },
        _decimal(),
        Dimension(dimension_name="kind"),
    )
    # U's upper boundary is independently exercised below with an exclusive declaration.
    values = ["0.00449", "0.00451", "0.01950"]
    expected = [{"S"}, set(), {"U"}]
    for value, survivors in zip(values, expected):
        assert {
            row["rule"]
            for row in _rows(engine.evaluate({"quoted": value}).survivors, "rule")
        } == survivors
        scores = _rows(
            engine.explain({"quoted": value}).frame, "rule", "__t_amount", "__survived"
        )
        assert {row["rule"] for row in scores if row["__survived"]} == survivors
        assert all(
            row["__t_amount"] == (1 if row["rule"] in survivors else -1)
            for row in scores
        )
    contexts = build_backend_df(backend_name, {"cid": [0, 1, 2], "quoted": values})
    for chunk_size in (None, 1, 2):
        rows = _rows(
            engine.evaluate_batch(
                contexts, context_id_field="cid", chunk_size=chunk_size
            ).survivors,
            "__context_id",
            "rule",
        )
        assert {(row["__context_id"], row["rule"]) for row in rows} == {
            (0, "S"),
            (2, "U"),
        }
    exclusive = _engine(
        backend_name,
        {"rule": ["U"], "floor": ["0.010"], "ceiling": ["0.020"]},
        _decimal(range_max_inclusive=False),
    )
    assert exclusive.evaluate({"quoted": "0.01950"}).count == 0
    assert _rows(
        engine.evaluate(
            {"kind": "singleton", "quoted": "invalid"}, dimensions=["kind"]
        ).survivors,
        "rule",
    ) == [{"rule": "S"}]


@pytest.mark.parametrize(
    "rounding,expected", [("half_to_even", "even"), ("half_away_from_zero", "away")]
)
def test_exact_decimal_literal_tie_reaches_one_target_conversion(
    backend_name, rounding, expected
):
    dimension = _decimal().model_copy(update={"rounding": rounding})
    engine = _engine(
        backend_name,
        {
            "rule": ["even", "away"],
            "floor": ["0.004", "0.005"],
            "ceiling": ["0.004", "0.005"],
        },
        dimension,
    )
    assert _rows(engine.evaluate({"quoted": Decimal("0.0045")}).survivors, "rule") == [
        {"rule": expected}
    ]
    assert _rows(
        engine.evaluate_batch(
            build_backend_df(backend_name, {"quoted": [Decimal("0.0045")]})
        ).survivors,
        "rule",
    ) == [{"rule": expected}]


def test_decimal_marker_and_flags_precede_cast_without_post_round_reclassification(
    backend_name,
):
    engine = _engine(
        backend_name,
        {
            "rule": ["marker", "rounds_to_marker", "flagged"],
            "floor": ["0.004", "0.00449", "broken"],
            "ceiling": ["0.004", "0.00449", "broken"],
            "lo_open": [True, False, True],
            "hi_open": [True, False, True],
        },
        _decimal(
            wildcard_values=["0.004"],
            range_min_unbounded_field="lo_open",
            range_max_unbounded_field="hi_open",
        ),
    )
    assert _scores(engine, {"quoted": "0.00449"}, "amount") == {
        "marker": (0, True),
        "rounds_to_marker": (1, True),
        "flagged": (0, True),
    }
    assert _scores(engine, {"quoted": "0.006"}, "amount")["rounds_to_marker"] == (
        -1,
        False,
    )
    assert _scores(engine, {"quoted": "0.004"}, "amount")["rounds_to_marker"] == (
        0,
        True,
    )
    rows = _rows(
        engine.evaluate({"quoted": "0.006"}).survivors, "rule", "floor", "lo_open"
    )
    assert {row["rule"]: (row["floor"], row["lo_open"]) for row in rows} == {
        "marker": ("0.004", True),
        "flagged": ("broken", True),
    }


@pytest.mark.parametrize("entrypoint", ["evaluate", "explain", "batch", "chunked"])
@pytest.mark.parametrize("value", ["broken", "999.995", True])
def test_invalid_decimal_concrete_input_is_value_error(backend_name, entrypoint, value):
    dimension = _decimal().model_copy(update={"numeric_dtype": DecimalDtype(5, 2)})
    engine = _engine(
        backend_name,
        {"rule": ["bounded"], "floor": ["0"], "ceiling": ["1"]},
        dimension,
    )
    with pytest.raises(ValueError):
        if entrypoint in {"evaluate", "explain"}:
            getattr(engine, entrypoint)({"quoted": value})
        else:
            engine.evaluate_batch(
                build_backend_df(backend_name, {"quoted": [value]}),
                chunk_size=1 if entrypoint == "chunked" else None,
            )


def test_invalid_decimal_rule_payload_is_value_error_not_no_match(backend_name):
    with pytest.raises(ValueError):
        engine = _engine(
            backend_name,
            {"rule": ["invalid"], "floor": ["broken"], "ceiling": ["1"]},
            _decimal(),
        )
        engine.evaluate({"quoted": "0.5"})


@pytest.mark.parametrize(
    "strategy,expected",
    [
        ("greater_than", [False, True, False]),
        ("less_than", [False, False, True]),
    ],
)
def test_decimal_thresholds_compare_normalized_values_strictly(
    backend_name, strategy, expected
):
    dimension = _decimal().model_copy(
        update={
            "match_strategy": MatchStrategy(strategy),
            "rule_field": "threshold",
        }
    )
    engine = _engine(
        backend_name,
        {"rule": ["threshold"], "threshold": ["0.004"]},
        dimension,
    )
    for value, survives in zip(["0.00449", "0.00451", "0.00349"], expected):
        assert engine.evaluate({"quoted": value}).count == int(survives)


def test_one_unbounded_endpoint_retains_range_ternary_specificity(backend_name):
    engine = _engine(
        backend_name,
        {
            "rule": ["lower", "upper"],
            "floor": ["0.010", "unused"],
            "ceiling": ["unused", "0.020"],
            "lo_open": [False, True],
            "hi_open": [True, False],
        },
        _decimal(
            range_min_unbounded_field="lo_open", range_max_unbounded_field="hi_open"
        ),
    )
    assert _scores(engine, {"quoted": "0.015"}, "amount") == {
        "lower": (0, True),
        "upper": (0, True),
    }
    assert _scores(engine, {"quoted": "0.009"}, "amount") == {
        "lower": (-1, False),
        "upper": (0, True),
    }
    assert _scores(engine, {"quoted": "0.021"}, "amount") == {
        "lower": (0, True),
        "upper": (-1, False),
    }


def test_decimal_batch_null_availability_and_empty_transport(backend_name):
    engine = _engine(
        backend_name,
        {"rule": ["S"], "floor": ["0.004"], "ceiling": ["0.004"]},
        _decimal(),
    )
    contexts = (
        relation(build_backend_df(backend_name, {"cid": [0, 1], "quoted": ["0", "0"]}))
        .with_columns(
            ma.lit(None).cast(str).alias("quoted"),
        )
        .collect()
    )
    for chunk_size in (None, 1):
        result = engine.evaluate_batch(
            contexts, context_id_field="cid", chunk_size=chunk_size
        )
        assert sorted(
            (row["__context_id"], row["__t_amount"])
            for row in _rows(result.survivors, "__context_id", "__t_amount")
        ) == [(0, 0), (1, 0)]
    empty = relation(contexts).head(0).collect()
    assert engine.evaluate_batch(empty, context_id_field="cid").count == 0


@pytest.mark.parametrize(
    "data_type,strategy,payload,context",
    [
        ("int", "greater_than", "unused", 12),
        ("int", "exact", "unused", 12),
        ("bool", "exact", 2, True),
        ("date", "less_than", "unused", date(2026, 10, 11)),
        ("datetime", "exact_key", "unused", datetime(2026, 10, 11)),
        ("str", "prefix", 42, "EU"),
    ],
)
def test_mapped_wildcard_discards_inadmissible_original_payload_type(
    backend_name,
    data_type,
    strategy,
    payload,
    context,
):
    dimension = Dimension(
        dimension_name="value",
        data_type=data_type,
        match_strategy=strategy,
        rule_wildcard_field="wild",
    )
    engine = _engine(
        backend_name,
        {"rule": ["unused"], "value": [payload], "wild": [True]},
        dimension,
    )
    assert _scores(engine, {"value": context}) == {"unused": (0, True)}
    assert _rows(engine.evaluate({"value": context}).survivors, "value", "wild") == [
        {"value": payload, "wild": True},
    ]
    with pytest.raises(ValueError):
        concrete = _engine(
            backend_name,
            {"rule": ["invalid"], "value": [payload], "wild": [False]},
            dimension,
        )
        concrete.evaluate({"value": context})


@pytest.mark.parametrize(
    "source_scale,marker,expected",
    [
        (3, Decimal("0.0045"), 1),
        (10, Decimal("0.004"), 0),
    ],
)
def test_decimal_markers_compare_exactly_in_original_column_dtype(
    backend_name,
    source_scale,
    marker,
    expected,
):
    typed = pl.DataFrame(
        {
            "rule": ["original"],
            "floor": pl.Series([Decimal("0.004")], dtype=pl.Decimal(38, source_scale)),
            "ceiling": pl.Series(
                [Decimal("0.004")], dtype=pl.Decimal(38, source_scale)
            ),
        }
    )
    rules = build_backend_df(backend_name, typed)
    engine = ExpressionRulesEngine(
        rules,
        DimensionsMetadata(
            dimensions=[_decimal(wildcard_values=[marker])],
        ),
    )
    assert _scores(engine, {"quoted": "0.00449"}, "amount") == {
        "original": (expected, True)
    }


def test_dimension_names_do_not_collide_with_presence_or_endpoint_bindings(
    backend_name,
):
    engine = _engine(
        backend_name,
        {"rule": ["matches"], "lo": [0], "hi": [10], "scalar": [3], "other": [7]},
        Dimension(
            dimension_name="value",
            context_field="a",
            data_type="int",
            match_strategy="range",
            range_min_field="lo",
            range_max_field="hi",
        ),
        Dimension(
            dimension_name="value_min",
            context_field="b",
            rule_field="scalar",
            data_type="int",
        ),
        Dimension(
            dimension_name="present_value",
            context_field="c",
            rule_field="other",
            data_type="int",
        ),
    )
    assert _rows(
        engine.evaluate({"a": 5, "b": 3, "c": 7}).survivors, "rule", "__specificity"
    ) == [{"rule": "matches", "__specificity": 3}]
    batch = build_backend_df(backend_name, {"a": [5], "b": [3], "c": [7]})
    assert _rows(engine.evaluate_batch(batch).survivors, "rule", "__specificity") == [
        {"rule": "matches", "__specificity": 3}
    ]


@pytest.mark.parametrize("payload", [["unused"], "unused"])
def test_true_set_flag_discards_unusable_element_or_container_domain(
    backend_name, payload
):
    dimension = Dimension(
        dimension_name="value",
        data_type="int",
        match_strategy="set_membership",
        rule_wildcard_field="wild",
    )
    engine = _engine(
        backend_name,
        {"rule": ["unused"], "value": [payload], "wild": [True]},
        dimension,
    )
    assert _scores(engine, {"value": 3}) == {"unused": (0, True)}
    with pytest.raises(ValueError):
        concrete = _engine(
            backend_name,
            {"rule": ["invalid"], "value": [payload], "wild": [False]},
            dimension,
        )
        concrete.evaluate({"value": 3})
