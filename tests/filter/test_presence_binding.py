"""PRESENCE scores suppliedness and original nullness, whatever the field's datatype or wildcard."""

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

# rule -> its (ternary, specificity) for an explicit null, a concrete value and an omitted field
SCORES = {
    "null": {"is_null": (1, 1), "is_not_null": (-1, 0), "wildcard": (0, 0)},
    "value": {"is_null": (-1, 0), "is_not_null": (1, 1), "wildcard": (0, 0)},
    "omitted": {"is_null": (0, 0), "is_not_null": (0, 0), "wildcard": (0, 0)},
}
FIELDS = {
    "str_star": (ContextField(name="f", data_type=DataType.STR, wildcard="*"), "*", "EU"),
    "str_null_wildcard": (
        ContextField(name="f", data_type=DataType.STR, wildcard=None, wildcard_declared=True),
        None,
        "EU",
    ),
    "bool": (ContextField(name="f", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True), None, True),
    "int": (ContextField(name="f", data_type=DataType.INT, wildcard=None, wildcard_declared=True), None, 7),
    "float": (ContextField(name="f", data_type=DataType.FLOAT, wildcard=None, wildcard_declared=True), None, 1.5),
}
CONTEXT_KINDS = ("null", "value", "omitted")


def presence_engine(backend_name, field, wildcard_cell):
    rules = build_backend_df(
        backend_name,
        {
            "rule_name": ["is_null", "is_not_null", "wildcard"],
            "required": ["IS_NULL", "IS_NOT_NULL", wildcard_cell],
        },
        table_name="presence_rules",
    )
    metadata = DimensionsMetadata(
        fields=(field,),
        dimensions=(
            Dimension(
                dimension_name="presence",
                context_field="f",
                rule_field="required",
                match_strategy=MatchStrategy.PRESENCE,
            ),
        ),
    )
    return ExpressionRulesEngine(rules, metadata)


def context_for(kind, value):
    return {"null": {"f": None}, "value": {"f": value}, "omitted": {}}[kind]


def surviving(scores):
    return {rule: score for rule, score in scores.items() if score[0] >= 0}


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("kind", CONTEXT_KINDS)
@pytest.mark.parametrize("name", FIELDS)
def test_presence_scores_suppliedness_and_nullness_for_every_field_declaration(backend_name, name, kind):
    field, wildcard_cell, value = FIELDS[name]
    engine = presence_engine(backend_name, field, wildcard_cell)
    context = context_for(kind, value)

    explained = relation(engine.explain(context).frame).to_polars().to_dicts()
    assert {r["rule_name"]: (r["__t_presence"], r["__specificity"]) for r in explained} == SCORES[kind]

    survivors = relation(engine.evaluate(context).survivors).to_polars().to_dicts()
    assert {r["rule_name"]: (r["__t_presence"], r["__specificity"]) for r in survivors} == surviving(SCORES[kind])


@pytest.mark.parametrize("backend_name", BACKENDS)
@pytest.mark.parametrize("name", FIELDS)
def test_batch_presence_agrees_with_single_context_presence(backend_name, name):
    field, wildcard_cell, value = FIELDS[name]
    engine = presence_engine(backend_name, field, wildcard_cell)
    dtype = pl.Series([value]).dtype
    contexts = pl.DataFrame({"cid": ["null", "value"], "f": pl.Series([None, value], dtype=dtype)})

    batch = relation(engine.evaluate_batch(contexts, context_id_field="cid").survivors).to_polars().to_dicts()

    assert {(r["__context_id"], r["rule_name"]): (r["__t_presence"], r["__specificity"]) for r in batch} == {
        (kind, rule): score for kind in ("null", "value") for rule, score in surviving(SCORES[kind]).items()
    }
