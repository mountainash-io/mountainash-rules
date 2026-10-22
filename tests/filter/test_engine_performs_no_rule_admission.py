"""The engine scores the rules it is given; rule admission belongs to prevalidation."""

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


def engine_over_an_unadmitted_rule(backend_name):
    """A region rule column holding an undeclared null: prevalidation's breach, not the engine's."""
    rules = build_backend_df(
        backend_name,
        {"rule_name": ["eu", "undeclared_null", "any"], "region": ["EU", None, "*"]},
        table_name="unadmitted_rules",
    )
    metadata = DimensionsMetadata(
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(Dimension(dimension_name="region", context_field="region", match_strategy=MatchStrategy.EXACT),),
    )
    return ExpressionRulesEngine(rules, metadata)


@pytest.mark.parametrize("backend_name", BACKENDS)
def test_every_entry_point_scores_an_unadmitted_rule_without_raising(backend_name):
    engine = engine_over_an_unadmitted_rule(backend_name)
    expected = {"eu": 1, "any": 0}

    single = relation(engine.evaluate({"region": "EU"}).survivors).to_polars().to_dicts()
    batch = relation(
        engine.evaluate_batch(pl.DataFrame({"cid": [1], "region": ["EU"]}), context_id_field="cid").survivors
    ).to_polars().to_dicts()
    explained = relation(engine.explain({"region": "EU"}).frame).to_polars().to_dicts()

    assert {row["rule_name"]: row["__t_region"] for row in single} == expected
    assert {row["rule_name"]: row["__t_region"] for row in batch} == expected
    assert {row["rule_name"]: row["__t_region"] for row in explained} == {**expected, "undeclared_null": -1}
