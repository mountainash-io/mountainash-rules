"""Tests for evaluate_batch and accumulator apply caching."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.constants import (
    NOT_SET,
    NOT_SET_NUMERIC,
    HitPolicy,
    MatchStrategy,
)
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine


def _metadata():
    return DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region"),
        Dimension(
            dimension_name="amount", match_strategy=MatchStrategy.RANGE,
            data_type=int, range_min_field="amt_min", range_max_field="amt_max",
        ),
        Dimension(dimension_name="code", match_strategy=MatchStrategy.PREFIX,
                  context_field="product_code"),
    ])


def _rules():
    return pl.DataFrame({
        "rule_name": ["au_low", "au_high", "nz_any", "prefix_x"],
        "region": ["AU", "AU", "NZ", "<NA>"],
        "amt_min": [0, 100, -999999999, -999999999],
        "amt_max": [99, 999, -999999999, -999999999],
        "code": ["<NA>", "<NA>", "<NA>", "X-"],
        "price": [1.0, 2.0, 3.0, 4.0],
    })


def _engine():
    return ExpressionRulesEngine(rules=_rules(), dimension_metadata=_metadata())


class TestPrepareContexts:
    def test_projects_exactly_id_plus_ctx_columns(self):
        contexts = pl.DataFrame({
            "region": ["AU", "NZ"],
            "amount": [50, 500],
            "product_code": ["X-1", "Y-2"],
            "irrelevant": ["a", "b"],
        })
        engine = _engine()
        prepared = engine._prepare_contexts(
            contexts, ["region", "amount", "code"], None
        )
        assert sorted(prepared.columns) == sorted(
            ["__context_id", "__ctx_region", "__ctx_amount", "__ctx_code"]
        )

    def test_missing_column_gets_typed_sentinel(self):
        contexts = pl.DataFrame({"region": ["AU"]})
        prepared = _engine()._prepare_contexts(
            contexts, ["region", "amount"], None
        )
        row = prepared.to_dict()
        assert row["__ctx_amount"][0] == NOT_SET_NUMERIC

    def test_null_value_gets_typed_sentinel(self):
        contexts = pl.DataFrame({"region": ["AU", None]})
        prepared = _engine()._prepare_contexts(contexts, ["region"], None)
        row = prepared.to_dict()
        assert row["__ctx_region"][1] == NOT_SET

    def test_supplied_id_copied_and_validated(self):
        contexts = pl.DataFrame({"cid": ["a", "b"], "region": ["AU", "NZ"]})
        prepared = _engine()._prepare_contexts(contexts, ["region"], "cid")
        row = prepared.to_dict()
        assert row["__context_id"] == ["a", "b"]

    def test_duplicate_supplied_ids_raise(self):
        contexts = pl.DataFrame({"cid": ["a", "a"], "region": ["AU", "NZ"]})
        with pytest.raises(ValueError, match="unique"):
            _engine()._prepare_contexts(contexts, ["region"], "cid")

    def test_reserved_column_in_contexts_raises(self):
        contexts = pl.DataFrame({"__rank": [1], "region": ["AU"]})
        with pytest.raises(ValueError, match="__rank"):
            _engine()._prepare_contexts(contexts, ["region"], None)
