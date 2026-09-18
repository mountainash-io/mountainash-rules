"""Observable exact runtime lifecycle and evidence-bound source controls."""

import pytest
import mountainash_rules as rules

from tests.accumulator.source_analysis_fixtures import case, contract, limits, row


def build_input(rows, *, contracts=()):
    kwargs, _ = case(contracts=contracts)
    bundle = rules.analyze_sources(rows, **kwargs)
    report = bundle.validation["reports"][0]
    validated = rules.validate_build_input(
        rows,
        bundle=bundle,
        analysis_input_id=report.analysis_input_id,
        source_report_id=report.id,
        approvals=[],
        **kwargs,
    )
    return kwargs, validated


def test_bound_build_exposes_semantic_cells_and_requested_decision():
    rows = [row(1, 0, 20, 7)]
    kwargs, validated = build_input(
        rows,
        contracts=[contract(promise="definite_outcome", required=True, masked=False)],
    )
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    )
    lattice = engine.build(rows, validation=validated)
    assert lattice.artifact_kind == "exact_cells"
    assert lattice.count == 1
    result = engine.apply(lattice, {"x": 5}, contract_id="client", profile_id="quote")
    assert result.status == "decision"
    assert result.reason == "established"
    assert result.values == {"pricing.total": 7}
    assert result.outcome.values == {"pricing.total": {"type": "int", "value": "7"}}
    assert result.may_have_no_match is False
    assert result.contributor_ids == (rows[0]["id"],)
    assert result.candidate_cells.to_dicts() == [
        {
            "cell_id": result.cell_id,
            "predicate_id": lattice.combinations["predicate_id"][0],
            "contributor_set_id": lattice.combinations["contributor_set_id"][0],
            "pricing.total": 7,
        }
    ]
    assert result.candidate_contributors.to_dicts() == [
        {
            "cell_id": result.cell_id,
            "source_id": rows[0]["id"],
        }
    ]
    expected_lineage = [
        dict(
            output_name="pricing.total",
            column_name="amount",
            operation="sum",
            source_id=rows[0]["id"],
            source_label=None,
        )
    ]
    assert result.lineage.to_dicts() == expected_lineage
    assert result.candidate_lineage(result.cell_id).to_dicts() == expected_lineage
    assert lattice.lineage(result.cell_id).to_dicts() == expected_lineage


def test_build_rechecks_actual_source_content():
    rows = [row(1)]
    kwargs, validated = build_input(rows)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    )
    with pytest.raises(ValueError):
        engine.build([row(1, amount=2)], validation=validated)


def test_unbound_artifact_is_inspectable_not_serveable():
    rows = [row(1)]
    kwargs, validated = build_input(rows)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    )
    lattice = engine.build(rows, validation=validated)
    assert lattice.count == 1
    with pytest.raises(ValueError):
        engine.apply(lattice, {"x": 5}, contract_id="client", profile_id="quote")


@pytest.mark.parametrize("count", [0, 1, 2])
def test_initial_bindings_preserve_each_declared_contract(count):
    contracts = [
        contract(contract_id=f"client-{i}", required=True, masked=False)
        for i in range(count)
    ]
    rows = [row(1, amount=0)]
    kwargs, validated = build_input(rows, contracts=contracts)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    )
    lattice = engine.build(rows, validation=validated)
    assert {binding.contract_id for binding in lattice.bindings} == {
        c.contract_id for c in contracts
    }
    assert lattice.contributors.to_dicts() == [
        {
            "contributor_set_id": lattice.combinations["contributor_set_id"][0],
            "source_id": rows[0]["id"],
        }
    ]


def test_segmentation_preserves_cell_identity_and_source_free_domain():
    rows = [row(1, 3, 8, 9), row(2, 12, 15, 4)]
    kwargs, validated = build_input(rows)
    direct = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    ).build(rows, validation=validated)
    segmented = rules.AccumulatorEngine(
        kwargs["metadata"],
        kwargs["aggregates"],
        segmentation_dimensions=["x"],
        limits=limits(),
    ).build(list(reversed(rows)), validation=validated)
    assert direct.artifact_id == segmented.artifact_id
    assert direct.combinations.to_dicts() == segmented.combinations.to_dicts()
    assert {edge["source_id"] for edge in segmented.contributors.to_dicts()} == {
        r["id"] for r in rows
    }


def test_source_amount_changes_artifact_not_cell_identity():
    rows = [row(1, amount=3)]
    changed = [row(1, amount=4)]
    kwargs, original = build_input(rows)
    _, newer = build_input(changed)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    )
    a = engine.build(rows, validation=original)
    b = engine.build(changed, validation=newer)
    assert a.artifact_id != b.artifact_id
    assert a.combinations["cell_id"].to_list() == b.combinations["cell_id"].to_list()


def test_mutating_inspection_metadata_does_not_change_artifact():
    rows = [row(1)]
    kwargs, validated = build_input(rows)
    lattice = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    ).build(rows, validation=validated)
    identifier = lattice.artifact_id
    lattice.metadata.dimensions.clear()
    lattice.aggregates.clear()
    assert lattice.artifact_id == identifier
    assert lattice.metadata.dimensions[0].dimension_name == "x"
    assert lattice.aggregates[0].output_name == "pricing.total"


def test_request_mask_bytes_share_the_apply_input_budget():
    rows = [row(1, 0, 20, 7)]
    kwargs, validated = build_input(
        rows, contracts=[contract(required=True, masked=False)]
    )
    lattice = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    ).build(
        rows,
        validation=validated,
    )
    bounded = rules.AccumulatorEngine(
        kwargs["metadata"],
        kwargs["aggregates"],
        limits=limits().model_copy(update={"max_input_bytes": 1024}),
    )
    with pytest.raises(rules.ExactResourceError):
        bounded.apply(
            lattice,
            {"x": 5},
            contract_id="client",
            profile_id="quote",
            dont_care=["x" * 1024],
        )


def test_malformed_row_mask_preserves_valid_batch_siblings():
    rows = [row(1, 0, 20, 7)]
    kwargs, validated = build_input(
        rows, contracts=[contract(required=True, masked=False)]
    )
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    )
    lattice = engine.build(rows, validation=validated)
    batch = engine.index([lattice]).apply_batch(
        [{"id": "valid", "x": 5, "mask": []}, {"id": "invalid", "x": 5, "mask": "x"}],
        contract_id="client",
        profile_id="quote",
        context_id_field="id",
        dont_care_field="mask",
    )
    assert batch.for_context("valid").values == {"pricing.total": 7}
    with pytest.raises(rules.InvalidContextError) as error:
        batch.for_context("invalid")
    assert error.value.outcome.status == "invalid_context"
    assert error.value.outcome.binding_id == lattice.bindings[0].id
    assert any(issue.field == "dont_care" for issue in error.value.outcome.issues)


@pytest.mark.parametrize("ceiling", [{"max_scopes": 0}, {"max_output_bytes": 4000}])
def test_apply_reserves_retained_layout_and_candidate_output(ceiling):
    rows = [row(1, 0, 20, 7)]
    kwargs, validated = build_input(
        rows, contracts=[contract(required=True, masked=False)]
    )
    lattice = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    ).build(
        rows,
        validation=validated,
    )
    bounded = rules.AccumulatorEngine(
        kwargs["metadata"],
        kwargs["aggregates"],
        limits=limits().model_copy(update=ceiling),
    )
    with pytest.raises(rules.ExactResourceError) as error:
        bounded.apply(lattice, {"x": 5}, contract_id="client", profile_id="quote")
    assert error.value.counter == next(iter(ceiling))


def test_invalid_outcome_diagnostics_obey_output_limit():
    rows = [row(1, 0, 20, 7)]
    kwargs, validated = build_input(
        rows, contracts=[contract(required=True, masked=False)]
    )
    lattice = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=limits()
    ).build(
        rows,
        validation=validated,
    )
    bounded = rules.AccumulatorEngine(
        kwargs["metadata"],
        kwargs["aggregates"],
        limits=limits().model_copy(update={"max_output_bytes": 20000}),
    )
    with pytest.raises(rules.ExactResourceError) as error:
        bounded.apply(
            lattice,
            {"x": 5},
            contract_id="client",
            profile_id="quote",
            dont_care=["unknown" * 4096],
        )
    assert error.value.counter == "max_output_bytes"
