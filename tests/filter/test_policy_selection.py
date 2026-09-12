"""Consumer contracts for schema authority, policy assertions and sound selection."""

import polars as pl
import pytest

import mountainash.expressions as ma
from mountainash.relations import relation
from mountainash_rules import (
    BatchRuleResult,
    DataType,
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    HitPolicy,
    HitPolicyViolationError,
    MatchStrategy,
    RuleResult,
    SelectionInfo,
    UNKNOWN,
)
from tests.conftest import build_backend_df


def _frame(frame):
    return relation(frame).to_polars()


def _engine(data=None, backend="polars", **metadata):
    if backend == "ibis-polars":
        pytest.skip(
            "mountainash#78: engine row indexing lacks WindowFunction translation"
        )
    if data is None:
        data = {
            "rule_name": ["generic", "specific"],
            "region": [UNKNOWN, "AU"],
            "salience": [10, 1],
            "price": [5, 5],
        }
    return ExpressionRulesEngine(
        build_backend_df(backend, data),
        DimensionsMetadata(dimensions=[Dimension(dimension_name="region")], **metadata),
    )


@pytest.mark.parametrize(
    "outputs",
    [
        {"price": [None, 10, 5, 5]},
        {"price": [None, None, 5, 5], "decision": ["yes", "no", "yes", "yes"]},
        {"decision": ["yes", "no", "yes", "yes"], "price": [None, None, 5, 5]},
    ],
)
def test_any_counts_null_tuples_and_reports_all_conflicting_contexts(
    backend_name, outputs
):
    engine = _engine(
        {
            "rule_name": ["a", "b", "c", "d"],
            "region": ["AU", "AU", "NZ", "NZ"],
            **outputs,
        },
        backend_name,
        output_fields=list(outputs),
    )
    with pytest.raises(HitPolicyViolationError) as single:
        engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY, top_n=0)
    assert single.value.policy is HitPolicy.ANY
    assert sorted(_frame(single.value.offending)["rule_name"]) == ["a", "b"]
    contexts = pl.DataFrame({"cid": [3, 2, 1], "region": ["AU", "NZ", "AU"]})
    for chunk_size in (None, 1):
        with pytest.raises(HitPolicyViolationError) as batch:
            engine.evaluate_batch(
                contexts,
                context_id_field="cid",
                hit_policy=HitPolicy.ANY,
                chunk_size=chunk_size,
                min_specificity=2,
            )
        assert batch.value.policy is HitPolicy.ANY
        assert _frame(batch.value.offending).sort("__context_id").rows() == [
            (1, 2),
            (3, 2),
        ]
    agreed = engine.evaluate_batch(
        pl.DataFrame({"region": ["NZ", "NZ"]}), hit_policy="any"
    )
    assert _frame(agreed.survivors)["rule_name"].to_list() == ["c", "c"]


def test_any_identical_null_tuples_agree():
    engine = _engine(
        {
            "rule_name": ["a", "b"],
            "region": ["AU", "AU"],
            "price": [None, None],
            "decision": ["yes", "yes"],
        },
        output_fields=["price", "decision"],
    )
    assert engine.evaluate({"region": "AU"}, hit_policy="any").count == 1
    assert (
        engine.evaluate_batch(
            pl.DataFrame({"region": ["AU", "AU"]}), hit_policy="any", chunk_size=1
        ).count
        == 2
    )


@pytest.mark.parametrize("policy", ["first", "priority", "rule_order"])
def test_filtered_first_and_top_n_preserve_old_rank(backend_name, policy):
    engine = _engine(backend=backend_name)
    single = engine.evaluate(
        {"region": "AU"},
        hit_policy=policy,
        priority_field="salience",
        min_specificity=1,
        top_n=1,
    )
    assert _frame(single.survivors).select("rule_name", "__rank").rows() == [
        ("specific", 2)
    ]
    contexts = pl.DataFrame({"cid": [4, 1], "region": ["AU", "AU"]})
    for chunk_size in (None, 1):
        batch = engine.evaluate_batch(
            contexts,
            context_id_field="cid",
            hit_policy=policy,
            priority_field="salience",
            min_specificity=1,
            top_n_per_context=1,
            chunk_size=chunk_size,
        )
        assert _frame(batch.survivors).select(
            "__context_id", "rule_name", "__rank"
        ).rows() == [
            (1, "specific", 2),
            (4, "specific", 2),
        ]
        assert _frame(batch.best_matches).select("__context_id", "__rank").rows() == [
            (1, 2),
            (4, 2),
        ]
        assert _frame(batch.for_context(4).best_match)["rule_name"].to_list() == [
            "specific"
        ]


def test_top_n_uses_remaining_positions_not_original_ranks():
    engine = _engine(
        {
            "rule_name": ["a", "b", "c", "d", "e"],
            "region": [UNKNOWN, "AU", UNKNOWN, "AU", "AU"],
        }
    )
    batch = engine.evaluate_batch(
        pl.DataFrame({"region": ["AU", "AU"]}),
        hit_policy="rule_order",
        min_specificity=1,
        top_n_per_context=2,
    )
    assert _frame(batch.survivors).select(
        "__context_id", "rule_name", "__rank"
    ).rows() == [
        (0, "b", 2),
        (0, "d", 4),
        (1, "b", 2),
        (1, "d", 4),
    ]


def test_accessors_use_minimum_remaining_rank_not_physical_order(backend_name):
    frame = build_backend_df(
        backend_name,
        {
            "rule_name": ["later", "best"],
            "__rank": [4, 2],
            "__context_id": [7, 7],
            "__specificity": [0, 1],
        },
    )
    result = RuleResult(frame, [])
    assert _frame(result.best_match)["rule_name"].to_list() == ["best"]
    batch = BatchRuleResult(frame, [], "cid")
    assert _frame(batch.best_matches)["rule_name"].to_list() == ["best"]
    assert _frame(batch.for_context(7).survivors)["__rank"].to_list() == [2, 4]
    assert batch.for_context(99).count == 0


@pytest.mark.parametrize(
    "options",
    [
        {"top_n": 1},
        {"top_n": 100},
        {"min_specificity": 0},
        {"hit_policy": "first"},
        {"hit_policy": "priority", "priority_field": "salience"},
        {"hit_policy": "any"},
    ],
)
def test_possibly_lossy_results_refuse_reselection(options):
    engine = _engine(output_fields=["price"])
    single = engine.evaluate({"region": "AU"}, include_observability=False, **options)
    for policy in ("unique", "priority"):
        with pytest.raises(ValueError):
            single.select(policy, priority_field="salience")
    batch_options = {
        ("top_n_per_context" if key == "top_n" else key): value
        for key, value in options.items()
    }
    for chunk_size in (None, 1):
        batch = engine.evaluate_batch(
            pl.DataFrame({"region": ["AU"]}),
            chunk_size=chunk_size,
            include_observability=False,
            **batch_options,
        )
        for cid in (0, 99):
            with pytest.raises(ValueError):
                batch.for_context(cid).select("unique")


def test_complete_selection_chains_keep_assertions_and_loss_guards():
    engine = _engine(output_fields=["price"])
    original = engine.evaluate({"region": "AU"}, include_observability=False)
    assert not any(c.startswith("__t_") for c in _frame(original.survivors).columns)
    assert {"__rank", "__specificity", "__rule_index"} <= set(
        _frame(original.survivors).columns
    )
    reordered = original.select("rule_order")
    with pytest.raises(HitPolicyViolationError):
        reordered.select("unique")
    for policy in ("first", "priority", "any"):
        reduced = reordered.select(policy, priority_field="salience")
        with pytest.raises(ValueError):
            reduced.select("unique")
    assert _frame(original.best_match)["rule_name"].to_list() == ["specific"]
    assert original.count == 2
    singleton = _engine({"rule_name": ["a"], "region": ["AU"], "price": [5]})
    assert (
        singleton.evaluate({"region": "AU"}, hit_policy="unique")
        .select("collect")
        .count
        == 1
    )
    for context in ({"region": "AU"}, {"region": "NZ"}):
        for policy in ("first", "any"):
            with pytest.raises(ValueError):
                singleton.evaluate(context, hit_policy=policy).select("collect")


def test_surviving_null_priority_rejected_before_filters(backend_name):
    engine = _engine(
        {
            "rule_name": ["null", "winner"],
            "region": [UNKNOWN, "AU"],
            "salience": [None, 10],
        },
        backend_name,
    )
    with pytest.raises(ValueError):
        engine.evaluate(
            {"region": "AU"},
            hit_policy="priority",
            priority_field="salience",
            min_specificity=1,
            top_n=1,
        )
    with pytest.raises(ValueError):
        engine.evaluate_batch(
            pl.DataFrame({"region": ["AU"]}),
            hit_policy="priority",
            priority_field="salience",
            min_specificity=1,
            top_n_per_context=1,
        )
    with pytest.raises(ValueError):
        engine.evaluate({"region": "AU"}).select("priority", priority_field="salience")
    valid = _engine(
        {
            "rule_name": ["unmatched", "winner"],
            "region": ["NZ", "AU"],
            "salience": [None, 10],
        },
        backend_name,
    )
    assert _frame(
        valid.evaluate(
            {"region": "AU"}, hit_policy="priority", priority_field="salience"
        ).best_match
    )["rule_name"].to_list() == ["winner"]


@pytest.mark.parametrize("fields", [["price", "missing"], ["price", "price"], [""]])
def test_declared_output_errors_are_not_hidden_by_empty_rules(fields):
    for rules in (
        pl.DataFrame({"region": ["AU"], "price": [1]}),
        pl.DataFrame(schema={"region": pl.String, "price": pl.Int64}),
    ):
        with pytest.raises(ValueError):
            ExpressionRulesEngine(
                rules,
                DimensionsMetadata(
                    dimensions=[Dimension(dimension_name="region")],
                    output_fields=fields,
                ),
            )


def test_expression_output_schema_is_explicit_and_constructor_only():
    rules = pl.DataFrame(
        {"rule_name": ["a", "b"], "region": ["AU", UNKNOWN], "price": [5, 5]}
    )
    expressions = {
        "region": ma.t_col("region", unknown={UNKNOWN}).t_eq(
            ma.t_col("__ctx_region", unknown={UNKNOWN})
        )
    }
    explicit = ExpressionRulesEngine(
        rules, dimension_expressions=expressions, output_fields=["price"]
    )
    assert explicit.evaluate({"region": "AU"}, hit_policy="any").count == 1
    assert (
        explicit.evaluate_batch(
            pl.DataFrame({"region": ["AU"]}), hit_policy="any"
        ).count
        == 1
    )
    assert explicit.evaluate({"region": "AU"}).select("any").count == 1
    for data in (rules, rules.head(0)):
        engine = ExpressionRulesEngine(data, dimension_expressions=expressions)
        with pytest.raises(ValueError):
            engine.evaluate({"region": "AU"}, hit_policy="any")
        with pytest.raises(ValueError):
            engine.evaluate_batch(
                pl.DataFrame(schema={"region": pl.String}), hit_policy="any"
            )
        with pytest.raises(ValueError):
            engine.evaluate({"region": "AU"}).select("any")
    for invalid in ([], ["missing"], ["price", "price"], [None], "price"):
        with pytest.raises(ValueError):
            ExpressionRulesEngine(
                rules, dimension_expressions=expressions, output_fields=invalid
            )
    for fields in ([], ["price"]):
        with pytest.raises(ValueError):
            ExpressionRulesEngine(
                rules,
                DimensionsMetadata(dimensions=[Dimension(dimension_name="region")]),
                output_fields=fields,
            )


def test_metadata_without_outputs_compares_cardinality_per_context(backend_name):
    engine = _engine({"rule_name": ["a", "b"], "region": ["AU", "NZ"]}, backend_name)
    for context, count in (({"region": "XX"}, 0), ({"region": "AU"}, 1)):
        assert engine.evaluate(context, hit_policy="any").count == count
    for contexts, count in (
        (pl.DataFrame(schema={"region": pl.String}), 0),
        (pl.DataFrame({"region": ["XX"]}), 0),
        (pl.DataFrame({"region": ["AU", "NZ", "AU"]}), 3),
    ):
        assert engine.evaluate_batch(contexts, hit_policy="any").count == count
    with pytest.raises(ValueError):
        engine.evaluate({}, hit_policy="any")
    with pytest.raises(ValueError):
        engine.evaluate_batch(pl.DataFrame({"region": [None]}), hit_policy="any")


@pytest.mark.parametrize("policy", ["uniqe", "COLLECT", "", [], {}, 7, True])
def test_invalid_policies_never_fall_back_to_collect(policy):
    engine = _engine()
    with pytest.raises(ValueError):
        engine.evaluate({"region": "AU"}, hit_policy=policy)
    with pytest.raises(ValueError):
        engine.evaluate_batch(pl.DataFrame({"region": ["AU"]}), hit_policy=policy)
    with pytest.raises(ValueError):
        engine.evaluate({"region": "AU"}).select(policy)


def test_priority_configuration_and_select_none_are_explicit():
    engine = _engine(priority_field="salience")
    for priority in ("", False, 5):
        with pytest.raises(ValueError):
            engine.evaluate({"region": "AU"}, priority_field=priority)
        with pytest.raises(ValueError):
            engine.evaluate_batch(
                pl.DataFrame({"region": ["AU"]}), priority_field=priority
            )
        with pytest.raises(ValueError):
            engine.evaluate({"region": "AU"}).select("collect", priority_field=priority)
    with pytest.raises(ValueError):
        engine.evaluate({"region": "AU"}).select(None)
    empty = _engine({"rule_name": ["r"], "region": ["NZ"]})
    for priority in (None, "missing"):
        with pytest.raises(ValueError):
            empty.evaluate(
                {"region": "AU"}, hit_policy="priority", priority_field=priority
            )
        with pytest.raises(ValueError):
            empty.evaluate_batch(
                pl.DataFrame(schema={"region": pl.String}),
                hit_policy="priority",
                priority_field=priority,
            )


def test_effective_priority_controls_inference_and_survives_select():
    data = {
        "rule_name": ["a", "b"],
        "region": ["AU", "AU"],
        "salience": [1, 10],
        "backup": [5, 5],
        "price": [7, 7],
    }
    engine = _engine(data, priority_field="backup")
    result = engine.evaluate({"region": "AU"}, priority_field="salience")
    assert _frame(result.select("priority").best_match)["rule_name"].to_list() == ["b"]
    assert result.select("any").count == 1
    with pytest.raises(HitPolicyViolationError):
        result.select("any", priority_field="backup")
    override = engine.evaluate({"region": "AU"}).select(
        "collect", priority_field="salience"
    )
    assert override.select("any").count == 1
    explicit = _engine(data, output_fields=["price"])
    assert (
        explicit.evaluate({"region": "AU"}).select("any", priority_field="backup").count
        == 1
    )


@pytest.mark.parametrize("policy", ["unique", "any"])
@pytest.mark.parametrize(
    "filters", [{"top_n": 0}, {"top_n": 1}, {"min_specificity": 9}]
)
def test_assertions_precede_every_output_reduction(policy, filters):
    engine = _engine({"rule_name": ["a", "b"], "region": ["AU", "AU"], "price": [1, 2]})
    with pytest.raises(HitPolicyViolationError):
        engine.evaluate({"region": "AU"}, hit_policy=policy, **filters)
    options = {
        ("top_n_per_context" if key == "top_n" else key): value
        for key, value in filters.items()
    }
    with pytest.raises(HitPolicyViolationError):
        engine.evaluate_batch(
            pl.DataFrame({"region": ["AU"]}), hit_policy=policy, **options
        )


def test_output_inference_uses_full_physical_metadata_not_active_projection():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="region", rule_field="country"),
            Dimension(
                dimension_name="age",
                data_type=DataType.INT,
                match_strategy=MatchStrategy.RANGE,
                range_min_field="lo",
                range_max_field="hi",
            ),
            Dimension(
                dimension_name="decision",
                match_strategy=MatchStrategy.CONTEXT_REGEX,
                regex_pattern=".*",
            ),
        ]
    )
    data = {
        "rule_name": ["a", "b"],
        "country": ["AU", UNKNOWN],
        "lo": [0, 10],
        "hi": [100, 90],
        "decision": ["yes", "yes"],
    }
    engine = ExpressionRulesEngine(pl.DataFrame(data), metadata)
    assert (
        engine.evaluate({"region": "AU"}, dimensions=["region"], hit_policy="any").count
        == 1
    )
    assert (
        engine.evaluate_batch(
            pl.DataFrame({"region": ["AU"]}), dimensions=["region"], hit_policy="any"
        ).count
        == 1
    )
    data["decision"] = ["yes", "no"]
    engine = ExpressionRulesEngine(pl.DataFrame(data), metadata)
    with pytest.raises(HitPolicyViolationError):
        engine.evaluate({"region": "AU"}, dimensions=["region"], hit_policy="any")


def test_manual_results_require_truthful_selection_state():
    frame = pl.DataFrame(
        {
            "rule_name": ["a", "b"],
            "region": ["AU", UNKNOWN],
            "price": [5, 5],
            "__rank": [1, 2],
            "__specificity": [1, 0],
            "__rule_index": [0, 1],
        }
    )
    with pytest.raises(ValueError):
        RuleResult(frame, ["region"]).select("first")
    with pytest.raises(ValueError):
        RuleResult(
            frame, ["region"], SelectionInfo(("region",), None, (), False, True)
        ).select("any")
    explicit = RuleResult(
        frame, ["region"], SelectionInfo((), None, ("price",), False, True)
    )
    assert explicit.select("any").count == 1
    for field in ("__rank", "__specificity", "__rule_index"):
        with pytest.raises(ValueError):
            RuleResult(
                frame.drop(field),
                ["region"],
                SelectionInfo((), None, ("price",), False, True),
            ).select("first")
