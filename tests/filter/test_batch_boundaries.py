"""Filter construction, projection, limit and typed-empty regressions (B05-B12)."""

import polars as pl
import pytest

import mountainash.expressions as ma

from mountainash_rules import (
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    HitPolicy,
    HitPolicyViolationError,
)


def _metadata():
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="region"),
            Dimension(dimension_name="channel"),
        ],
        priority_field="price",
        output_fields=["price"],
    )


def _rules():
    return pl.DataFrame(
        {
            "rule_name": ["r1", "r2"],
            "region": ["AU", "AU"],
            "channel": ["BROKER", "DIRECT"],
            "price": [1.0, 2.0],
        }
    )


def _engine(rules=None):
    return ExpressionRulesEngine(
        _rules() if rules is None else rules, dimension_metadata=_metadata()
    )


def _contexts():
    return pl.DataFrame({"cid": [7], "region": ["AU"], "channel": ["BROKER"]})


def _invoke(engine, operation, **kwargs):
    if operation == "evaluate_batch":
        return engine.evaluate_batch(_contexts(), **kwargs)
    return getattr(engine, operation)({"region": "AU", "channel": "BROKER"}, **kwargs)


@pytest.mark.parametrize("chunk_size", [None, 1, 2])
@pytest.mark.parametrize("observability", [True, False])
@pytest.mark.parametrize("id_field", [None, "cid"])
def test_empty_contexts_preserve_schema_and_result_state(
    chunk_size, observability, id_field
):
    engine = _engine()
    contexts = _contexts()
    options = {"include_observability": observability, "context_id_field": id_field}
    full = engine.evaluate_batch(contexts, **options)
    empty = engine.evaluate_batch(contexts.head(0), chunk_size=chunk_size, **options)
    assert isinstance(empty.survivors, pl.DataFrame)
    assert empty.count == 0
    assert empty.survivors.schema == full.survivors.schema
    assert empty.active_dimensions == full.active_dimensions
    assert empty.context_id_field == full.context_id_field
    assert empty.for_context(7).select("collect").count == 0


@pytest.mark.parametrize("policy", list(HitPolicy))
@pytest.mark.parametrize("empty_contexts", [False, True])
def test_empty_rules_preserve_schema_for_all_policies(policy, empty_contexts):
    rules = _rules()
    contexts = _contexts()
    full = _engine(rules).evaluate_batch(contexts, hit_policy=policy)
    engine = _engine(rules.head(0))
    batch = engine.evaluate_batch(
        contexts.head(0) if empty_contexts else contexts,
        hit_policy=policy,
        chunk_size=1,
    )
    assert batch.count == 0
    assert batch.survivors.schema == full.survivors.schema
    single = engine.evaluate({"region": "AU", "channel": "BROKER"}, hit_policy=policy)
    assert single.count == 0
    assert (
        single.survivors.schema
        == _engine(rules)
        .evaluate({"region": "AU", "channel": "BROKER"}, hit_policy=policy)
        .survivors.schema
    )


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
@pytest.mark.parametrize("empty", [False, True])
def test_chunk_size_requires_positive_python_integer(value, empty):
    engine = _engine()
    contexts = _contexts().head(0) if empty else _contexts()
    with pytest.raises(ValueError, match="chunk_size"):
        engine.evaluate_batch(contexts, chunk_size=value)


@pytest.mark.parametrize(
    "operation,parameter",
    [
        ("evaluate", "top_n"),
        ("evaluate", "min_specificity"),
        ("evaluate_batch", "top_n_per_context"),
        ("evaluate_batch", "min_specificity"),
    ],
)
@pytest.mark.parametrize("value", [-1, True, 1.0, "1"])
def test_numeric_limits_reject_invalid_values(operation, parameter, value):
    engine = _engine()
    kwargs = {parameter: value}
    with pytest.raises(ValueError, match=parameter):
        _invoke(engine, operation, **kwargs)


@pytest.mark.parametrize("operation", ["evaluate", "evaluate_batch"])
@pytest.mark.parametrize("policy", ["unique", "any"])
@pytest.mark.parametrize("reduction", ["zero_limit", "high_threshold"])
def test_assertions_precede_valid_empty_reductions(operation, policy, reduction):
    engine = _engine(_rules().with_columns(pl.lit("BROKER").alias("channel")))
    parameter = "top_n_per_context" if operation == "evaluate_batch" else "top_n"
    options = {parameter: 0} if reduction == "zero_limit" else {"min_specificity": 3}
    with pytest.raises(HitPolicyViolationError):
        _invoke(engine, operation, hit_policy=policy, **options)


@pytest.mark.parametrize("operation", ["evaluate", "evaluate_batch"])
def test_valid_numeric_boundaries_select_remaining_rows(operation):
    engine = _engine()
    parameter = "top_n_per_context" if operation == "evaluate_batch" else "top_n"
    selected = _invoke(engine, operation, min_specificity=0, **{parameter: 1})
    assert selected.survivors["rule_name"].to_list() == ["r1"]
    for options in ({parameter: 0}, {"min_specificity": 3}):
        empty = _invoke(engine, operation, **options)
        assert empty.count == 0
        assert empty.survivors.schema == selected.survivors.schema


@pytest.mark.parametrize("operation", ["evaluate", "explain", "evaluate_batch"])
@pytest.mark.parametrize(
    "projection",
    [[], ["region", "region"], "region", ("region",), [1], ["missing", None]],
)
def test_projection_shape_is_validated_before_scoring(operation, projection):
    engine = _engine()
    with pytest.raises(ValueError, match="dimensions"):
        _invoke(engine, operation, dimensions=projection)


@pytest.mark.parametrize("operation", ["evaluate", "explain", "evaluate_batch"])
def test_unknown_well_formed_dimension_retains_key_error(operation):
    engine = _engine()
    with pytest.raises(KeyError, match="missing"):
        _invoke(engine, operation, dimensions=["missing"])


@pytest.mark.parametrize("operation", ["evaluate", "explain", "evaluate_batch"])
def test_projection_order_and_omission_control_scoring(operation):
    engine = _engine()
    all_dims = _invoke(engine, operation, dimensions=None)
    reversed_dims = _invoke(engine, operation, dimensions=["channel", "region"])
    subset = _invoke(engine, operation, dimensions=["region"])
    assert all_dims.active_dimensions == ["region", "channel"]
    assert reversed_dims.active_dimensions == ["channel", "region"]
    frame = reversed_dims.frame if operation == "explain" else reversed_dims.survivors
    assert [c for c in frame.columns if c.startswith("__t_")] == [
        "__t_channel",
        "__t_region",
    ]
    assert subset.survivors["rule_name"].to_list() == ["r1", "r2"]
    assert subset.survivors["__specificity"].to_list() == [1, 1]


def test_filter_rejects_empty_metadata_without_restricting_shared_serialization():
    metadata = DimensionsMetadata.from_yaml(DimensionsMetadata(dimensions=[]).to_yaml())
    rules = _rules()
    with pytest.raises(ValueError, match="dimension"):
        ExpressionRulesEngine(rules, dimension_metadata=metadata)


@pytest.mark.parametrize(
    "mode", ["neither", "empty_expressions", "both_with_empty_expressions"]
)
def test_constructor_requires_exactly_one_nonempty_mode(mode):
    rules = _rules()
    options = {}
    if mode != "neither":
        options["dimension_expressions"] = {}
    if mode == "both_with_empty_expressions":
        options["dimension_metadata"] = _metadata()
    with pytest.raises(ValueError):
        ExpressionRulesEngine(rules, **options)


def test_empty_batches_do_not_skip_configuration_or_reserved_column_validation():
    contexts = _contexts().head(0)
    reserved = _engine(_rules().with_columns(pl.lit(0).alias("__rank")))
    with pytest.raises(ValueError, match="__rank"):
        reserved.evaluate_batch(contexts, chunk_size=1)
    expressions = {"region": ma.col("region")}
    # ANY requires an explicit expressions-only output schema even without rows.
    unconfigured = ExpressionRulesEngine(_rules(), dimension_expressions=expressions)
    with pytest.raises(ValueError, match="output_fields"):
        unconfigured.evaluate_batch(contexts, hit_policy="any", chunk_size=1)
