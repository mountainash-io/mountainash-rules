"""Public exact accumulator outcome wrappers."""

from types import MappingProxyType

import pytest
from mountainash.relations import relation

import mountainash_rules as rules
from mountainash_rules.core.contracts import InvalidContextError, UnresolvedContextError
from tests.accumulator.exact_runtime_fixtures import build, declarations, uuid
from tests.accumulator.source_analysis_fixtures import contract


def _resolve_artifact(*, on_unresolved="return"):
    dimensions = [
        rules.Dimension(
            dimension_name="x",
            data_type="int",
            match_strategy="range",
            range_min_field="lo",
            range_max_field="hi",
        )
    ]
    provider = contract(
        promise="allow_unresolved",
        required=False,
        masked=False,
    ).model_copy(
        update={
            "profiles": (
                rules.ResolutionProfile(
                    profile_id="quote",
                    mode="resolve",
                    output_fields=["pricing.total"],
                    provenance="contributors",
                    dimensions=["x"],
                    allow_dont_care=[],
                    promise="allow_unresolved",
                    on_unresolved=on_unresolved,
                ),
            ),
        }
    )
    kwargs = declarations(
        dimensions,
        [
            rules.Aggregate(
                column_name="amount",
                output_name="pricing.total",
                data_type="int",
                numeric_semantics="numeric-1",
            )
        ],
        contracts=[provider],
    )
    return build(
        [{"id": uuid(1), "lo": 0, "hi": 20, "amount": 7}],
        kwargs,
        decisions=[("profile_counterexample", ())],
    )


def _candidate_artifact():
    dimensions = [
        rules.Dimension(
            dimension_name="x",
            data_type="int",
            match_strategy="range",
            range_min_field="lo",
            range_max_field="hi",
        )
    ]
    provider = rules.ContextContract(
        schema_version=1,
        contract_id="inspect",
        domain_ref="D",
        fields=[rules.ContextField(name="x", data_type="int", required=False)],
        profiles=[
            rules.ResolutionProfile(
                profile_id="cells",
                mode="candidates",
                output_fields=[],
                provenance="none",
                dimensions=["x"],
                allow_dont_care=[],
                promise="candidate_only",
            )
        ],
    )
    kwargs = declarations(dimensions, contracts=[provider])
    return build([{"id": uuid(2), "lo": 0, "hi": 20}], kwargs)


class TestAccumulatorResult:
    def test_decision_projects_exact_candidates_and_definite_lineage(self):
        engine, lattice = _resolve_artifact()

        result = engine.apply(
            lattice, {"x": 5}, contract_id="client", profile_id="quote"
        )

        cell = lattice.combinations.to_dicts()[0]
        assert result.status == "decision"
        assert result.reason == "established"
        assert result.values == {"pricing.total": 7}
        assert isinstance(result.values, MappingProxyType)
        assert result.candidate_cells.to_dicts() == [
            {
                "cell_id": result.cell_id,
                "predicate_id": cell["predicate_id"],
                "contributor_set_id": cell["contributor_set_id"],
                "pricing.total": 7,
            }
        ]
        assert result.candidate_contributors.to_dicts() == [
            {"cell_id": result.cell_id, "source_id": uuid(1)}
        ]
        assert (
            result.lineage.to_dicts()
            == result.candidate_lineage(result.cell_id).to_dicts()
        )

    def test_candidate_mode_keeps_empty_lineage_typed_without_definite_provenance(self):
        engine, lattice = _candidate_artifact()

        result = engine.apply(
            lattice, {"x": 5}, contract_id="inspect", profile_id="cells"
        )

        candidate = result.candidate_cells.to_dicts()[0]["cell_id"]
        assert result.status == "candidates"
        assert result.lineage is None
        assert result.candidate_contributors.to_dicts() == [
            {"cell_id": candidate, "source_id": uuid(2)}
        ]
        assert result.candidate_lineage(candidate).to_dicts() == []
        assert result.candidate_lineage(candidate).collect().columns == [
            "output_name",
            "column_name",
            "operation",
            "source_id",
            "source_label",
        ]
        with pytest.raises(KeyError):
            result.candidate_lineage(uuid(99))

    def test_invalid_context_preserves_normalized_record_without_candidate_analysis(
        self,
    ):
        engine, lattice = _resolve_artifact()

        with pytest.raises(InvalidContextError) as failure:
            engine.apply(
                lattice, {"x": "not-an-int"}, contract_id="client", profile_id="quote"
            )

        result = failure.value.result
        assert failure.value.outcome is result.outcome
        assert result.status == "invalid_context"
        assert result.candidate_cells is None
        assert result.candidate_contributors is None
        assert result.lineage is None
        with pytest.raises(KeyError):
            result.candidate_lineage(uuid(1))

    def test_rejected_request_retains_exact_candidate_inspection(self):
        engine, lattice = _resolve_artifact(on_unresolved="reject")

        with pytest.raises(UnresolvedContextError) as failure:
            engine.apply(lattice, {}, contract_id="client", profile_id="quote")

        result = failure.value.result
        candidate = result.candidate_cells.to_dicts()[0]["cell_id"]
        assert failure.value.outcome is result.outcome
        assert result.status == "rejected"
        assert result.lineage is None
        assert result.candidate_contributors.to_dicts() == [
            {"cell_id": candidate, "source_id": uuid(1)}
        ]
        assert result.candidate_lineage(candidate).to_dicts()[0]["source_id"] == uuid(1)


class TestAccumulatorBatchResult:
    def test_records_outcomes_and_context_lookup_preserve_input_identity_and_order(
        self,
    ):
        engine, lattice = _resolve_artifact()
        index = engine.index([lattice])

        batch = index.apply_batch(
            relation({"request_id": ["second", "first"], "x": [5, 5]}),
            contract_id="client",
            profile_id="quote",
            context_id_field="request_id",
        )

        assert batch.context_ids.to_dicts() == [
            {"__context_id": "second"},
            {"__context_id": "first"},
        ]
        assert list(batch.records) == ["second", "first"]
        assert [row["__context_id"] for row in batch.outcomes.to_dicts()] == [
            "second",
            "first",
        ]
        assert [row["values"] for row in batch.outcomes.to_dicts()] == [
            '{"pricing.total":{"type":"int","value":"7"}}'
        ] * 2
        assert batch.for_context("first").outcome is batch.records["first"]
        with pytest.raises(KeyError):
            batch.for_context("unknown")

    def test_native_id_dtype_survives_candidate_projections(self):
        import polars as pl

        engine, lattice = _resolve_artifact()
        contexts = pl.DataFrame(
            {"request_id": [9, 2], "x": [5, 5]},
            schema={"request_id": pl.Int16, "x": pl.Int64},
        )
        batch = engine.index([lattice]).apply_batch(
            contexts,
            contract_id="client",
            profile_id="quote",
            context_id_field="request_id",
            chunk_size=1,
        )
        for projected in (
            batch.context_ids,
            batch.outcomes,
            batch.candidate_cells,
            batch.candidate_contributors,
        ):
            frame = projected.to_polars()
            assert frame.schema["__context_id"] == pl.Int16
            assert frame["__context_id"].to_list() == [9, 2]
