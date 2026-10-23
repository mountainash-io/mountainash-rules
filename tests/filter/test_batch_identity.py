"""Batch evaluation identity behavior and retained-result accessors."""

import polars as pl
import pytest

from mountainash_rules import ContextField, Dimension, DimensionsMetadata, ExpressionRulesEngine


def _engine(rule_cid=False):
    rules = pl.DataFrame({"rule_name": ["au", "nz"], "region": ["AU", "NZ"]})
    if rule_cid:
        rules = rules.with_columns(pl.Series("cid", ["rule-au", "rule-nz"]))
    return ExpressionRulesEngine(
        rules,
        dimension_metadata=DimensionsMetadata(
            fields=(ContextField(name="region", data_type="str", wildcard="<NA>"),),
            dimensions=[Dimension(dimension_name="region", context_field="region")],
        ),
    )


@pytest.mark.parametrize("ids", [[30, 20, 10], ["z", "m", "a"]])
@pytest.mark.parametrize("rule_cid", [False, True])
def test_supplied_ids_remain_canonical_without_overwriting_rule_data(ids, rule_cid):
    engine = _engine(rule_cid)
    contexts = pl.DataFrame({"cid": ids, "region": ["AU", "XX", "NZ"]})
    full = engine.evaluate_batch(contexts, context_id_field="cid")
    chunked = engine.evaluate_batch(contexts, context_id_field="cid", chunk_size=2)
    assert chunked.survivors.equals(full.survivors)
    assert chunked.context_id_field == "cid"
    assert chunked.survivors["__context_id"].dtype == contexts["cid"].dtype
    assert chunked.matched_context_ids == [ids[2], ids[0]]
    assert chunked.unmatched_context_ids(contexts) == [ids[1]]
    assert chunked.for_context(ids[0]).best_match["rule_name"].to_list() == ["au"]
    if rule_cid:
        assert chunked.survivors["cid"].to_list() == ["rule-nz", "rule-au"]
    else:
        assert "cid" not in chunked.survivors.columns


def test_generated_ids_preserve_unmatched_positions_across_chunks():
    engine = _engine()
    contexts = pl.DataFrame({"region": ["AU", "XX", "NZ", "XX", "AU"]})
    full = engine.evaluate_batch(contexts)
    for chunk in (1, 2):
        batch = engine.evaluate_batch(contexts, chunk_size=chunk)
        assert batch.survivors.equals(full.survivors)
        assert batch.matched_context_ids == [0, 2, 4]
        assert batch.unmatched_context_ids(contexts) == [1, 3]
        assert batch.context_id_field == "__context_id"


def test_empty_extracted_views_keep_schema_and_loss_state():
    engine = _engine()
    contexts = pl.DataFrame({"region": ["AU", "XX"]})
    batch = engine.evaluate_batch(contexts, hit_policy="first", chunk_size=1)
    matched = batch.for_context(0)
    for context_id in (1, 99):
        empty = batch.for_context(context_id)
        assert empty.count == 0
        assert empty.survivors.schema == matched.survivors.schema
        with pytest.raises(ValueError):
            empty.select("collect")


def test_match_accessors_describe_retained_rows_after_zero_limit():
    engine = _engine()
    contexts = pl.DataFrame({"cid": [8, 3], "region": ["AU", "NZ"]})
    batch = engine.evaluate_batch(
        contexts, context_id_field="cid", top_n_per_context=0, chunk_size=1
    )
    assert batch.matched_context_ids == []
    assert batch.counts_per_context.to_dict(as_series=False) == {
        "__context_id": [],
        "__n": [],
    }
    assert batch.unmatched_context_ids(contexts) == [3, 8]
