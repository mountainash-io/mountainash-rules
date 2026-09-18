"""Authored rows retain UUID identity through exact analysis and folds."""

import pytest

from mountainash_rules import (
    Aggregate,
    Dimension,
    DimensionsMetadata,
    DomainDefinition,
    DomainField,
)
from mountainash_rules.core.codec import content_id
from mountainash_rules.core.contracts import ExactLimits, OperationBudget


def _prepare(rows, *, limit_overrides=None):
    from mountainash_rules.engines.accumulator.compiler import prepare_sources
    from mountainash_rules.core.predicates import PredicateGraph

    limits = ExactLimits(
        language=dict(
            max_input_bytes=100000,
            max_nesting=64,
            max_nfa_states=4096,
            max_states=4096,
            max_transitions=65536,
            max_work=1000000,
        ),
        max_input_bytes=10000000,
        max_output_bytes=10000000,
        max_work=10**12,
        max_live_bytes=100000000,
        max_predicate_nodes=100000,
        max_dfa_states=100000,
        max_dfa_transitions=1000000,
        max_theory_states=100000,
        max_regions=100000,
        max_scopes=10000,
        max_source_scope_edges=100000,
        max_contributor_edges=100000,
        max_word_rows=100000,
        max_numeric_bits=100000,
        max_witnesses=100000,
    )
    if limit_overrides is not None:
        limits = limits.model_copy(update=limit_overrides)
    fields = [DomainField(name="x", data_type="int")]
    graph = PredicateGraph(fields, budget=OperationBudget(limits, "analysis"))
    domain = DomainDefinition(
        schema_version=1,
        domain_id="pricing",
        fields=fields,
        predicate_id=graph.interval("x", 0, 20, lower_closed=True, upper_closed=True),
    )
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="x",
                data_type="int",
                match_strategy="range",
                range_min_field="lo",
                range_max_field="hi",
            ),
        ]
    )
    routing = {
        "schema_version": 1,
        "semantics": "exact-key-1",
        "key_dimensions": [],
        "partition_keys": [[]],
    }
    return prepare_sources(
        rows,
        graph=graph,
        domain=domain,
        metadata=metadata,
        aggregates=[
            Aggregate(
                column_name="amount",
                output_name="charge.sum",
                data_type="float",
                numeric_semantics="numeric-1",
            )
        ],
        ruleset_id="pricing",
        source_id_field="id",
        source_label_field="label",
        routing={"id": content_id("routing", routing), "payload": routing},
        regex_options=None,
    )


_ROWS = [
    {
        "id": "00000000-0000-0000-0000-000000000000",
        "label": "base",
        "lo": 0,
        "hi": 20,
        "amount": 1e16,
    },
    {
        "id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "label": None,
        "lo": 5,
        "hi": 10,
        "amount": 1.0,
    },
    {
        "id": "00000000-0000-0000-0000-000000000002",
        "label": "offset",
        "lo": 5,
        "hi": 10,
        "amount": -1e16,
    },
]


def _analyze(prepared):
    from mountainash_rules.engines.accumulator.compiler import analyze_sources

    return analyze_sources(prepared, key_values=[])


def test_source_permutation_preserves_ids_and_exact_once_per_source_folds():
    first = _analyze(_prepare(_ROWS))
    second = _analyze(_prepare(list(reversed(_ROWS))))
    assert [(c.cell_id, dict(c.outputs)) for c in first.cells] == [
        (c.cell_id, dict(c.outputs)) for c in second.cells
    ]
    assert len(first.cells) == 3
    shared = [c for c in first.cells if len(c.contributors) == 3]
    assert len(shared) == 1
    assert shared[0].outputs["charge.sum"] == 1.0
    assert first.artifact_id == second.artifact_id


def test_amount_edits_keep_geometry_but_change_artifact_identity():
    first = _analyze(_prepare(_ROWS))
    edited = [
        {**row, "amount": 2.0 if row["label"] is None else row["amount"]}
        for row in _ROWS
    ]
    second = _analyze(_prepare(edited))
    assert [c.cell_id for c in first.cells] == [c.cell_id for c in second.cells]
    assert first.artifact_id != second.artifact_id
    assert (
        next(c for c in second.cells if len(c.contributors) == 3).outputs["charge.sum"]
        == 2.0
    )


def test_labels_are_preserved_but_do_not_define_semantic_identity():
    original = _prepare(_ROWS)
    relabeled = _prepare([{**row, "label": "same label"} for row in _ROWS])
    assert original.source_bundle_id == relabeled.source_bundle_id
    assert original.source_labels == {_ROWS[0]["id"]: "base", _ROWS[2]["id"]: "offset"}
    assert set(relabeled.source_labels.values()) == {"same label"}


def test_invalid_or_duplicate_source_ids_and_unreachable_nulls_are_not_repaired():
    with pytest.raises(ValueError):
        _prepare(_ROWS + [_ROWS[0]])
    for identifier in (None, "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF", "row-1"):
        with pytest.raises(ValueError):
            _prepare([{**_ROWS[0], "id": identifier}])
    with pytest.raises(ValueError):
        _prepare([{**_ROWS[0], "lo": 30, "hi": 40, "amount": None}])
    with pytest.raises(ValueError):
        _prepare([{key: value for key, value in _ROWS[0].items() if key != "lo"}])


def test_complete_analysis_retains_unreachable_sources_without_edges():
    rows = _ROWS + [
        {
            "id": "00000000-0000-0000-0000-000000000003",
            "label": "unreachable",
            "lo": 30,
            "hi": 40,
            "amount": 0.0,
        }
    ]
    prepared = _prepare(rows)
    analysis = _analyze(prepared)
    assert len(analysis.sources) == 4
    assert all(rows[-1]["id"] not in cell.contributors for cell in analysis.cells)
    assert prepared.source_labels[rows[-1]["id"]] == "unreachable"


def test_prepared_analysis_input_is_order_invariant_and_policy_bound():
    from mountainash_rules.core.contracts import ValidationPolicy
    from mountainash_rules.engines.accumulator.compiler import prepare_analysis_input

    policy = ValidationPolicy(
        schema_version=1,
        policy_id="source",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=(),
    )
    first = _prepare(_ROWS)
    permuted = _prepare(list(reversed(_ROWS)))
    first_input, first_material = prepare_analysis_input(
        first,
        compilation_domain_ref="pricing",
        domains={"pricing": first.domain},
        contracts=(),
        validation_policy=policy,
    )
    other_input, other_material = prepare_analysis_input(
        permuted,
        compilation_domain_ref="pricing",
        domains={"pricing": permuted.domain},
        contracts=(),
        validation_policy=policy,
    )
    assert first_input.source_id_field == "id"
    assert first_input.id == other_input.id
    assert first_material == other_material
    changed_input, changed_material = prepare_analysis_input(
        first,
        compilation_domain_ref="pricing",
        domains={"pricing": first.domain},
        contracts=(),
        validation_policy=policy.model_copy(update={"policy_id": "revised"}),
    )
    assert changed_input.id != first_input.id
    assert changed_material.analysis_input_id != first_material.analysis_input_id


def test_prepared_material_rejects_stale_artifact_from_other_source_amounts():
    from mountainash_rules.core.contracts import ValidationPolicy
    from mountainash_rules.engines.accumulator.compiler import prepare_analysis_input

    original = _prepare(_ROWS)
    artifact = _analyze(original)
    changed = _prepare([{**row, "amount": 2.0} for row in _ROWS])
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="source",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=(),
    )
    with pytest.raises(ValueError):
        prepare_analysis_input(
            changed,
            compilation_domain_ref="pricing",
            domains={"pricing": changed.domain},
            contracts=(),
            validation_policy=policy,
            analysis=artifact,
        )


def test_three_distinct_wildcards_with_one_label_keep_every_contribution():
    rows = [
        {
            **row,
            "label": "same historical shape",
            "lo": -999999999,
            "hi": -999999999,
            "amount": amount,
        }
        for row, amount in zip(_ROWS, (0.0, 2.0, 5.0))
    ]
    analysis = _analyze(_prepare(rows))
    assert len(analysis.cells) == 1
    assert analysis.cells[0].contributors == tuple(sorted(row["id"] for row in rows))
    assert dict(analysis.cells[0].outputs) == {"charge.sum": 7.0}


def test_prepare_analysis_input_rejects_canonical_result_over_output_capacity():
    from mountainash_rules.core.contracts import ExactResourceError, ValidationPolicy
    from mountainash_rules.engines.accumulator.compiler import prepare_analysis_input

    prepared = _prepare(_ROWS, limit_overrides={"max_output_bytes": 0})
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="source",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=(),
    )
    with pytest.raises(ExactResourceError) as failure:
        prepare_analysis_input(
            prepared,
            compilation_domain_ref="pricing",
            domains={"pricing": prepared.domain},
            contracts=(),
            validation_policy=policy,
        )
    assert failure.value.counter == "max_output_bytes"


def test_prepare_analysis_input_rejects_canonical_result_over_input_capacity():
    from mountainash_rules.core.contracts import ExactResourceError, ValidationPolicy
    from mountainash_rules.engines.accumulator.compiler import prepare_analysis_input

    prepared = _prepare(_ROWS)
    prepared.graph.budget = OperationBudget(
        prepared.graph.budget.limits.model_copy(update={"max_input_bytes": 0}),
        "analysis",
    )
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="source",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=(),
    )
    with pytest.raises(ExactResourceError) as failure:
        prepare_analysis_input(
            prepared,
            compilation_domain_ref="pricing",
            domains={"pricing": prepared.domain},
            contracts=(),
            validation_policy=policy,
        )
    assert failure.value.counter == "max_input_bytes"


def test_prepare_analysis_input_rejects_canonical_result_over_live_capacity():
    from mountainash_rules.core.contracts import ExactResourceError, ValidationPolicy
    from mountainash_rules.engines.accumulator.compiler import prepare_analysis_input

    prepared = _prepare(_ROWS)
    prepared.graph.budget = OperationBudget(
        prepared.graph.budget.limits.model_copy(update={"max_live_bytes": 0}),
        "analysis",
    )
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="source",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=(),
    )
    with pytest.raises(ExactResourceError) as failure:
        prepare_analysis_input(
            prepared,
            compilation_domain_ref="pricing",
            domains={"pricing": prepared.domain},
            contracts=(),
            validation_policy=policy,
        )
    assert failure.value.counter == "max_live_bytes"


def test_prepare_sources_rejects_tiny_input_before_consuming_source_iterable():
    from mountainash_rules.core.contracts import ExactResourceError

    consumed = []

    def rows():
        for row in _ROWS:
            consumed.append(row["id"])
            yield row

    with pytest.raises(ExactResourceError) as failure:
        _prepare(rows(), limit_overrides={"max_input_bytes": 0})
    assert failure.value.counter == "max_input_bytes"
    assert consumed == []


def test_disconnected_cells_share_one_contributor_set_edge_budget():
    analysis = _analyze(_prepare(_ROWS, limit_overrides={"max_contributor_edges": 4}))
    assert sorted(cell.outputs["charge.sum"] for cell in analysis.cells) == [
        1.0,
        1e16,
        1e16,
    ]
    assert len({cell.contributor_set_id for cell in analysis.cells}) == 2


def test_analysis_material_rejects_provider_domain_outside_global_compilation():
    from mountainash_rules.core.contracts import ValidationPolicy
    from mountainash_rules.engines.accumulator.compiler import prepare_analysis_input

    prepared = _prepare(_ROWS)
    wider = DomainDefinition(
        schema_version=1,
        domain_id="wider",
        fields=prepared.domain.fields,
        predicate_id=prepared.graph.interval(
            "x", 0, 21, lower_closed=True, upper_closed=True
        ),
    )
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="source",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=(),
    )
    with pytest.raises(ValueError):
        prepare_analysis_input(
            prepared,
            compilation_domain_ref="pricing",
            domains={"pricing": prepared.domain, "wider": wider},
            contracts=(),
            validation_policy=policy,
        )


def test_source_report_logical_findings_ignore_internal_normalization_history():
    """F03: real producer diagnostics must survive duplicate/reordered cell fragments."""
    import json
    from mountainash_rules import (
        CoverageRequirement,
        DiagnosticRule,
        Scope,
        ValidationPolicy,
    )
    from mountainash_rules.core.normalization import covered_overlay
    from mountainash_rules.core.reasoner import Reasoner
    from mountainash_rules.core.validation import produce_source_report
    from mountainash_rules.engines.accumulator.compiler import (
        analyze_sources,
        analysis_geometry,
        prepare_analysis_input,
    )

    rows = [
        {**_ROWS[0], "lo": 0, "hi": 5},
        {**_ROWS[1], "lo": 0, "hi": 5},
        {**_ROWS[2], "lo": 30, "hi": 40},
    ]
    signatures = []
    for reverse, duplicate_fragments in ((False, False), (True, False), (True, True)):
        prepared = _prepare(rows)
        scope = Scope(
            partition_refs=[{"routing_id": prepared.routing["id"], "key_values": []}],
            domain_refs=["pricing"],
            profile_refs=[],
        )
        diagnostics = [
            DiagnosticRule(
                stage="source",
                check_id=check,
                code=code,
                scope=scope,
                severity="warning",
                witness_kind="none",
                max_witnesses=0,
            )
            for check, code in (
                ("source_predicates", "unreachable_source"),
                ("source_overlaps", "source_overlap"),
                ("source_overlaps", "duplicate_source"),
                ("source_overlaps", "singleton_boundary_overlap"),
                ("coverage", "coverage_gap"),
            )
        ]
        diagnostics.sort(
            key=lambda d: json.dumps(
                d.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        policy = ValidationPolicy(
            schema_version=1,
            policy_id="history",
            required_checks=[],
            coverage_requirements=[
                CoverageRequirement(
                    requirement_id="whole",
                    domain_ref="pricing",
                    region_predicate_id=prepared.domain.predicate_id,
                    scope=scope,
                    severity="warning",
                ),
                CoverageRequirement(
                    requirement_id="whole-equivalent",
                    domain_ref="pricing",
                    region_predicate_id=prepared.domain.predicate_id,
                    scope=scope,
                    severity="warning",
                ),
            ],
            diagnostic_rules=diagnostics,
        )
        sources = {s.source_id: s.predicate_id for s in prepared.sources}
        order = sorted(sources, reverse=reverse)
        overlay = covered_overlay(
            Reasoner(prepared.graph),
            prepared.domain.predicate_id,
            sources,
            order=order,
        )
        fragments = overlay.fragments * (2 if duplicate_fragments else 1)
        analyzed = analyze_sources(
            prepared, key_values=[], order=order, fragments=fragments
        )
        geometry = analysis_geometry(
            analyzed, provider_domains={"pricing": prepared.domain}
        )
        analysis, _ = prepare_analysis_input(
            prepared,
            compilation_domain_ref="pricing",
            domains={"pricing": prepared.domain},
            contracts=(),
            validation_policy=policy,
        )
        findings, report = produce_source_report(
            analysis,
            lambda partition: geometry,
            (),
            partition_refs=scope.partition_refs,
            source_counts=(len(prepared.sources),),
            dimension_fields={"x": "x"},
            ordered_fields=("x",),
        )
        assert all(check.complete for check in report.checks)
        assert sum(item.code == "coverage_gap" for item in findings) == 1
        signatures.append(
            sorted((f.code, f.source_ids, f.region_predicate_id) for f in findings)
        )
    assert signatures[0] == signatures[1] == signatures[2]
    assert {code for code, _, _ in signatures[0]} == {
        "unreachable_source",
        "source_overlap",
        "duplicate_source",
        "coverage_gap",
    }
