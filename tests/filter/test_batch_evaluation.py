"""Tests for evaluate_batch and accumulator apply caching."""

import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules import (
    AccumulatorEngine,
    Dimension,
    DimensionRole,
    DimensionsMetadata,
    ExpressionRulesEngine,
    HitPolicy,
    MatchStrategy,
)
from tests.conftest import ALL_BACKENDS, build_backend_df


def _metadata():
    return DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="region"),
            Dimension(
                dimension_name="amount",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field="amt_min",
                range_max_field="amt_max",
            ),
            Dimension(
                dimension_name="code",
                match_strategy=MatchStrategy.PREFIX,
                context_field="product_code",
            ),
        ]
    )


def _rules():
    return pl.DataFrame(
        {
            "rule_name": ["au_low", "au_high", "nz_any", "prefix_x"],
            "region": ["AU", "AU", "NZ", "<NA>"],
            "amt_min": [0, 100, -999999999, -999999999],
            "amt_max": [99, 999, -999999999, -999999999],
            "code": ["<NA>", "<NA>", "<NA>", "X-"],
            "price": [1.0, 2.0, 3.0, 4.0],
        }
    )


def _engine():
    return ExpressionRulesEngine(rules=_rules(), dimension_metadata=_metadata())


def test_reserved_column_in_contexts_raises():
    engine = _engine()
    contexts = pl.DataFrame({"__rank": [1], "region": ["AU"]})
    with pytest.raises(ValueError, match="__rank"):
        engine.evaluate_batch(contexts)


class TestEvaluateBatchAgreement:
    """Core invariant: batch == per-context evaluate(), row for row."""

    def _contexts(self):
        return pl.DataFrame(
            {
                "region": ["AU", "AU", "NZ", "XX", None],
                "amount": [50, 500, None, 10, 10],
                "product_code": ["X-1", "Y-9", "X-2", "X-3", None],
            }
        )

    def test_batch_agrees_with_single_context_evaluation(self):
        engine = _engine()
        batch = engine.evaluate_batch(self._contexts())
        surv = pl.DataFrame(relation(batch.survivors).to_polars())

        for i, ctx in enumerate(self._contexts().to_dicts()):
            single = engine.evaluate({k: v for k, v in ctx.items() if v is not None})
            single_rows = relation(single.survivors).to_polars()
            batch_rows = surv.filter(pl.col("__context_id") == i).sort("__rank")
            assert (
                batch_rows["rule_name"].to_list() == single_rows["rule_name"].to_list()
            ), f"context {i}"
            assert (
                batch_rows["__specificity"].to_list()
                == single_rows["__specificity"].to_list()
            ), f"context {i}"
            assert (
                batch_rows["__rank"].to_list() == single_rows["__rank"].to_list()
            ), f"context {i}"

    def test_best_matches_one_row_per_surviving_context(self):
        engine = _engine()
        batch = engine.evaluate_batch(self._contexts(), min_specificity=1)
        best = relation(batch.best_matches).to_polars()
        assert best["__context_id"].to_list() == batch.matched_context_ids
        for row in best.to_dicts():
            retained = relation(
                batch.for_context(row["__context_id"]).survivors
            ).to_polars()
            assert row["__rank"] == min(retained["__rank"])


class TestBatchAccessors:
    def _batch(self):
        contexts = pl.DataFrame(
            {
                "region": ["AU", "XX"],
                "amount": [50, 1],
                "product_code": ["X-1", "Q"],
            }
        )
        return _engine().evaluate_batch(contexts), contexts

    def test_counts_per_context(self):
        batch, _ = self._batch()
        counts = relation(batch.counts_per_context).to_polars()
        # context 0 (AU/50/X-1): au_low + prefix_x survive; context 1: none
        assert dict(zip(counts["__context_id"], counts["__n"])) == {0: 2}

    def test_matched_and_unmatched(self):
        batch, contexts = self._batch()
        assert batch.matched_context_ids == [0]
        assert batch.unmatched_context_ids(contexts) == [1]

    def test_for_context_returns_rule_result(self):
        batch, _ = self._batch()
        single = batch.for_context(0)
        assert single.count == 2
        assert single.explain("au_low")["region"] == 1


class TestBatchHitPolicies:
    def _contexts(self):
        return pl.DataFrame(
            {
                "region": ["AU", "NZ"],
                "amount": [50, 500],
                "product_code": ["X-1", "Z"],
            }
        )

    def test_first_keeps_one_row_per_context(self):
        batch = _engine().evaluate_batch(self._contexts(), hit_policy=HitPolicy.FIRST)
        surv = relation(batch.survivors).to_polars()
        assert surv.group_by("__context_id").len()["len"].to_list() == [1, 1]

    def test_unique_violation_lists_context_ids(self):
        from mountainash_rules.core.hit_policy import HitPolicyViolationError

        with pytest.raises(HitPolicyViolationError) as exc_info:
            _engine().evaluate_batch(self._contexts(), hit_policy=HitPolicy.UNIQUE)
        assert "0" in str(exc_info.value)  # context 0 has >1 survivor

    def test_top_n_per_context_truncates_per_context_not_globally(self):
        batch = _engine().evaluate_batch(self._contexts(), top_n_per_context=1)
        surv = relation(batch.survivors).to_polars()
        assert (surv["__rank"] <= 1).all()
        assert surv["__context_id"].n_unique() == 2


class TestChunking:
    def test_chunked_equals_unchunked(self):
        contexts = pl.DataFrame(
            {
                "region": ["AU"] * 5 + ["NZ"] * 5,
                "amount": list(range(0, 1000, 100)),
                "product_code": [f"X-{i}" for i in range(10)],
            }
        )
        engine = _engine()
        whole = relation(engine.evaluate_batch(contexts).survivors).to_polars()
        chunked = relation(
            engine.evaluate_batch(contexts, chunk_size=3).survivors
        ).to_polars()
        key = ["__context_id", "__rank"]
        assert whole.sort(key).equals(chunked.sort(key))

    def test_unique_violations_accumulate_across_chunks(self):
        from mountainash_rules.core.hit_policy import HitPolicyViolationError

        # contexts 0 and 3 both have 2 survivors; chunk_size=2 puts them
        # in different chunks — both ids must appear in the message
        contexts = pl.DataFrame(
            {
                "region": ["AU", "XX", "XX", "AU"],
                "amount": [50, 1, 1, 50],
                "product_code": ["X-1", "Q", "Q", "X-1"],
            }
        )
        with pytest.raises(HitPolicyViolationError) as exc_info:
            _engine().evaluate_batch(
                contexts, hit_policy=HitPolicy.UNIQUE, chunk_size=2
            )
        msg = str(exc_info.value)
        assert "0" in msg and "3" in msg


class TestApplyCaching:
    def _setup(self):
        md = DimensionsMetadata(
            dimensions=[
                Dimension(dimension_name="segment", role=DimensionRole.CONTEXT_KEY),
                Dimension(dimension_name="region"),
            ]
        )
        rules = pl.DataFrame(
            {
                "rule_name": ["a", "b", "c"],
                "segment": ["retail", "retail", "corp"],
                "region": ["AU", "<NA>", "AU"],
            }
        )
        engine = AccumulatorEngine(dimension_metadata=md)
        return engine, engine.build_all(rules)

    def test_lattice_index_routes_by_partition(self):
        engine, lattices = self._setup()
        index = engine.index(lattices)
        result = index.apply({"segment": "corp", "region": "AU"})
        rows = relation(result.survivors).to_polars()
        assert set(rows["rule_name"]) == {"c"}

    def test_lattice_index_apply_batch(self):
        engine, lattices = self._setup()
        index = engine.index(lattices)
        contexts = pl.DataFrame(
            {
                "segment": ["retail", "corp"],
                "region": ["AU", "AU"],
            }
        )
        batch = index.apply_batch(contexts)
        surv = relation(batch.survivors).to_polars()
        assert surv["__context_id"].n_unique() == 2


class TestBatchBackendSweep:
    """Task 2's agreement oracle across backends (EXACT + RANGE dims only;
    per-row string-match strategies are broken on pandas/narwhals upstream,
    mountainash-io/mountainash#89, and are swept in test_compiler instead)."""

    _RULE_DATA = {
        "rule_name": ["au_low", "au_high", "nz_any"],
        "region": ["AU", "AU", "NZ"],
        "amt_min": [0, 100, -999999999],
        "amt_max": [99, 999, -999999999],
        "price": [1.0, 2.0, 3.0],
    }

    def _md(self):
        return DimensionsMetadata(
            dimensions=[
                Dimension(dimension_name="region"),
                Dimension(
                    dimension_name="amount",
                    match_strategy=MatchStrategy.RANGE,
                    data_type=int,
                    range_min_field="amt_min",
                    range_max_field="amt_max",
                ),
            ]
        )

    @pytest.mark.parametrize("backend_name", ALL_BACKENDS)
    def test_batch_agrees_with_single_context_evaluation(self, backend_name):
        rules = build_backend_df(backend_name, self._RULE_DATA, "batch_sweep_rules")
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=self._md())
        contexts = pl.DataFrame(
            {
                "region": ["AU", "AU", "NZ", "XX", None],
                "amount": [50, 500, None, 10, 10],
            }
        )
        batch = engine.evaluate_batch(contexts)
        surv = pl.DataFrame(relation(batch.survivors).to_polars())

        for i, ctx in enumerate(contexts.to_dicts()):
            single = engine.evaluate({k: v for k, v in ctx.items() if v is not None})
            single_rows = pl.DataFrame(relation(single.survivors).to_polars())
            batch_rows = surv.filter(pl.col("__context_id") == i).sort("__rank")
            assert (
                batch_rows["rule_name"].to_list() == single_rows["rule_name"].to_list()
            ), f"context {i}"
            assert (
                batch_rows["__rank"].to_list() == single_rows["__rank"].to_list()
            ), f"context {i}"
