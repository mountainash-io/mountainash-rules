"""Public Phase 3 source evidence conformance (F01–F13)."""

import json
import os
import subprocess
import sys

import pytest
import mountainash_rules as rules

from tests.accumulator.source_analysis_fixtures import (
    approve,
    case,
    contract,
    envelope,
    gate,
    limits,
    ordered,
    row,
    string_case,
)


def test_f01_clean_public_source_handoff():
    assert hasattr(rules, "analyze_sources"), "Phase 3 public analysis is missing"
    kwargs, _ = case()
    rows = [row(1)]
    bundle = rules.analyze_sources(iter(rows), **kwargs)
    report = bundle.validation["reports"][0]
    assert {check.check_id for check in report.checks} == {
        "source_schema",
        "source_identity",
        "source_predicates",
        "source_overlaps",
        "routing",
        "coverage",
        "profiles",
    }
    assert all(check.complete for check in report.checks)
    assert report.finding_ids == ()
    assert report.stage == "source" and report.artifact_id is None
    assert gate(iter(rows), kwargs, bundle).approval_ids == ()


def test_e6_private_build_gate_reuses_selected_source_evidence_for_clean_compilation():
    """One caller budget admits source evidence and emits clean artifact evidence."""
    from mountainash_rules.core.contracts import OperationBudget
    from mountainash_rules.engines.accumulator import analysis as build_gate

    kwargs, _ = case()
    rows = [row(1)]
    source_bundle = rules.analyze_sources(rows, **kwargs)
    validation = gate(rows, kwargs, source_bundle)
    budget = OperationBudget(kwargs["limits"], "e6-private-build-gate")
    preparation = build_gate.prepare_build(
        rows,
        validation=validation,
        metadata=kwargs["metadata"],
        aggregates=kwargs["aggregates"],
        budget=budget,
    )
    analysis = build_gate._analyze_partition(
        preparation.prepared,
        key_values=preparation.material.selected_scope.partition_refs[0]["key_values"],
    )
    evidence = build_gate.produce_compiled_evidence(
        preparation, analysis, budget=budget
    )
    reports = {report.id: report for report in evidence.validation["reports"]}
    compiled = next(report for report in reports.values() if report.stage == "compiled")
    assert reports[validation.source_report_id].id == preparation.source_report.id
    assert compiled.artifact_id == analysis.artifact_id
    assert {check.check_id for check in compiled.checks} == {
        "cell_nonempty",
        "cell_disjointness",
        "source_union",
        "source_membership",
        "output_folds",
        "profile_consistency",
    }
    assert all(check.status == "passed" and check.complete for check in compiled.checks)
    assert evidence.validation["approvals"] == source_bundle.validation["approvals"]
    assert evidence.validation["bindings"] == ()


def test_compiled_evidence_rejects_cell_geometry_outside_declared_domain():
    from dataclasses import replace
    from mountainash_rules.core.contracts import OperationBudget
    from mountainash_rules.engines.accumulator import analysis as build_gate

    kwargs, _ = case()
    rows = [row(1, rules.UNKNOWN_NUMERIC, rules.UNKNOWN_NUMERIC)]
    source_bundle = rules.analyze_sources(rows, **kwargs)
    validation = gate(rows, kwargs, source_bundle)
    budget = OperationBudget(kwargs["limits"], "build")
    preparation = build_gate.prepare_build(
        rows,
        validation=validation,
        metadata=kwargs["metadata"],
        aggregates=kwargs["aggregates"],
        budget=budget,
    )
    analysis = build_gate._analyze_partition(preparation.prepared, key_values=[])
    corrupt = replace(
        analysis,
        cells=tuple(
            replace(cell, predicate_id=preparation.prepared.graph.true)
            for cell in analysis.cells
        ),
    )
    with pytest.raises(ValueError):
        build_gate.produce_compiled_evidence(preparation, corrupt, budget=budget)


def test_compiled_evidence_obeys_remaining_operation_output_budget():
    from mountainash_rules.core.contracts import OperationBudget
    from mountainash_rules.engines.accumulator import analysis as build_gate

    kwargs, _ = case()
    rows = [row(1)]
    source_bundle = rules.analyze_sources(rows, **kwargs)
    validation = gate(rows, kwargs, source_bundle)
    budget = OperationBudget(kwargs["limits"], "build")
    preparation = build_gate.prepare_build(
        rows,
        validation=validation,
        metadata=kwargs["metadata"],
        aggregates=kwargs["aggregates"],
        budget=budget,
    )
    analysis = build_gate._analyze_partition(preparation.prepared, key_values=[])
    budget.limits = budget.limits.model_copy(update={"max_output_bytes": 0})
    with pytest.raises(rules.ExactResourceError) as failure:
        build_gate.produce_compiled_evidence(preparation, analysis, budget=budget)
    assert failure.value.counter == "max_output_bytes"
    assert failure.value.operation == "build"


@pytest.mark.parametrize("extruded", [False, True])
def test_f02_boundary_review_is_independent_of_permission(extruded):
    kwargs, _ = case(extra_field=extruded)
    rows = [row(1, 0, 10), row(2, 10, 20)]
    bundle = rules.analyze_sources(rows, **kwargs)
    findings = bundle.validation["findings"]
    assert [f.code for f in findings] == ["singleton_boundary_overlap"]
    finding = findings[0]
    assert finding.source_ids == tuple(r["id"] for r in rows)
    assert finding.witnesses[0].contexts[0]["x"] == {"type": "int", "value": "10"}
    loaded = rules.decode_validation_bundle(
        rules.encode_validation_bundle(bundle, limits=limits()),
        limits=limits(),
    )
    with pytest.raises(ValueError):
        gate(rows, kwargs, loaded)
    decision = approve(loaded, [finding.id])
    attached = rules.attach_warning_approvals(loaded, [decision], limits=limits())
    assert attached.validation["findings"] == loaded.validation["findings"]
    assert loaded.validation["approvals"] == ()
    assert gate(rows, kwargs, attached, [decision]).approval_ids == (decision.id,)
    assert rules.encode_validation_bundle(
        rules.attach_warning_approvals(attached, [decision], limits=limits()),
        limits=limits(),
    ) == rules.encode_validation_bundle(attached, limits=limits())


def test_f03_public_immutable_envelopes_flow_through_analysis_and_approval():
    kwargs, _ = case()
    predicate_envelope = rules.make_exact_envelope(
        "predicate", kwargs["predicates"][0]["payload"], limits=limits()
    )
    kwargs["predicates"] = (predicate_envelope,)
    rows = [row(1, 0, 10), row(2, 10, 20)]

    bundle = rules.analyze_sources(rows, **kwargs)
    finding = bundle.validation["findings"][0]
    approval_envelope = rules.make_exact_envelope(
        "approval",
        {
            "schema_version": 1,
            "analysis_input_id": bundle.validation["reports"][0].analysis_input_id,
            "report_id": bundle.validation["reports"][0].id,
            "authority_ref": "immutable-envelope-review",
            "actor_ref": "fixture-reviewer",
            "decision": "approve_warnings",
            "scope": finding.scope.model_dump(mode="json"),
            "warning_ids": [finding.id],
        },
        limits=limits(),
    )
    approval = rules.WarningApproval.model_validate(
        {"id": approval_envelope["id"], **approval_envelope["payload"]}
    )
    attached = rules.attach_warning_approvals(bundle, [approval], limits=limits())

    assert gate(rows, kwargs, attached, [approval]).approval_ids == (approval.id,)


def test_f03_all_duplicate_pairs_gaps_and_unreachable_sources_survive_permutation():
    kwargs, _ = case(coverage=True, witnesses=0)
    rows = [row(1, 0, 5), row(2, 0, 5), row(3, 0, 5), row(4, 30, 40)]
    first = rules.analyze_sources(rows, **kwargs)
    second = rules.analyze_sources(reversed(rows), **kwargs)

    def logical(bundle):
        return sorted(
            (f.code, f.source_ids, f.region_predicate_id)
            for f in bundle.validation["findings"]
        )

    assert logical(first) == logical(second)
    findings = first.validation["findings"]
    assert sum(f.code == "duplicate_source" for f in findings) == 3
    assert sum(f.code == "source_overlap" for f in findings) == 3
    assert sum(f.code == "coverage_gap" for f in findings) == 1
    assert [f.source_ids for f in findings if f.code == "unreachable_source"] == [
        (rows[3]["id"],)
    ]
    assert all(not f.witnesses for f in findings)


@pytest.mark.parametrize(
    "segmentation,reverse", [((), False), (("x",), False), (("x",), True)]
)
def test_source_diagnostics_are_invariant_under_scoped_physical_construction(
    segmentation, reverse
):
    from mountainash_rules.core.contracts import OperationBudget
    from mountainash_rules.core.validation import produce_source_report
    from mountainash_rules.engines.accumulator.analysis import _prepare
    from mountainash_rules.engines.accumulator.compiler import (
        analyze_sources,
        analysis_geometry,
    )
    from mountainash_rules.engines.accumulator.layout import (
        discover_scoped,
        materialize_layout,
        validate_layout,
    )

    kwargs, _ = case(coverage=True, witnesses=0)
    rows = [row(1, 0, 5), row(2, 0, 5), row(3, 0, 5), row(4, 30, 40)]
    public = rules.analyze_sources(rows, **kwargs)
    options = {key: value for key, value in kwargs.items() if key != "limits"}
    options.setdefault("source_label_field", None)
    options.setdefault("regex_options", None)
    budget = OperationBudget(kwargs["limits"], "physical-source-conformance")
    (
        prepared,
        domains,
        contracts,
        _,
        partitions,
        counts,
        current,
        _,
        dimension_fields,
        guards,
        ordered_fields,
    ) = _prepare(rows, budget=budget, **options)
    order = tuple(item["id"] for item in (list(reversed(rows)) if reverse else rows))
    discovery = discover_scoped(
        prepared, key_values=[], segmentation_fields=segmentation, order=order
    )
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)
    validate_layout(analysis, layout, budget=budget)
    geometry = analysis_geometry(analysis, provider_domains=domains)
    findings, _ = produce_source_report(
        current,
        lambda partition: geometry,
        contracts,
        dimension_fields=dimension_fields,
        partition_refs=partitions,
        source_counts=counts,
        guard_fields=guards,
        ordered_fields=ordered_fields,
    )
    assert sorted(
        (f.code, f.source_ids, f.region_predicate_id) for f in findings
    ) == sorted(
        (f.code, f.source_ids, f.region_predicate_id)
        for f in public.validation["findings"]
    )


def test_f04_invalid_sources_and_false_definite_promise_cannot_be_approved():
    kwargs, _ = case()
    for rows in (
        [row(1), row(1)],
        [{**row(1), "amount": None}],
        [{**row(1), "id": "not-a-uuid"}],
    ):
        with pytest.raises(ValueError):
            rules.analyze_sources(rows, **kwargs)
    kwargs, _ = case(contracts=[contract(promise="definite_outcome")])
    rows = [row(1, 0, 10)]
    bundle = rules.analyze_sources(rows, **kwargs)
    findings = bundle.validation["findings"]
    assert any(
        f.code == "profile_counterexample" and f.severity == "error" for f in findings
    )
    with pytest.raises(ValueError):
        gate(rows, kwargs, bundle)


def test_f05_current_inputs_are_recomputed_but_row_order_is_harmless():
    kwargs, _ = case()
    rows = [row(1, 0, 9), row(2, 10, 20)]
    bundle = rules.analyze_sources(rows, **kwargs)
    assert (
        gate(reversed(rows), kwargs, bundle).source_report_id
        == bundle.validation["reports"][0].id
    )
    for changes in ({"amount": 2}, {"hi": 8}):
        with pytest.raises(ValueError):
            gate([{**rows[0], **changes}, rows[1]], kwargs, bundle)
    policy = kwargs["validation_policy"].model_dump(mode="json")
    policy["policy_id"] = "revised"
    with pytest.raises(ValueError):
        gate(
            rows,
            {**kwargs, "validation_policy": rules.ValidationPolicy(**policy)},
            bundle,
        )
    changed, _ = case(contracts=[contract(required=True, masked=False)])
    with pytest.raises(ValueError):
        gate(rows, changed, bundle)


def test_f08_candidates_and_empty_sources_have_complete_nonfabricated_reports():
    kwargs, _ = case(contracts=[contract(mode="candidates", promise="candidate_only")])
    bundle = rules.analyze_sources([row(1, 0, 5), row(2, 10, 20)], **kwargs)
    assert not bundle.validation["findings"]
    assert gate([row(1, 0, 5), row(2, 10, 20)], kwargs, bundle).approval_ids == ()
    empty = rules.analyze_sources([], **kwargs)
    assert not empty.validation["findings"]
    assert gate([], kwargs, empty).approval_ids == ()


def test_f10_whole_operation_input_limit_fails_without_partial_evidence():
    kwargs, _ = case()
    with pytest.raises(rules.ExactResourceError):
        rules.analyze_sources(
            [row(1)], **{**kwargs, "limits": limits(max_input_bytes=1)}
        )


def test_f13_missing_or_ambiguous_active_policy_fails_even_on_clean_data():
    kwargs, _ = case()
    policy = kwargs["validation_policy"].model_dump(mode="json")
    policy["diagnostic_rules"] = [
        rule for rule in policy["diagnostic_rules"] if rule["code"] != "source_overlap"
    ]
    bad = rules.ValidationPolicy(**policy)
    rows = [row(1, 0, 9), row(2, 10, 20)]
    with pytest.raises(ValueError):
        rules.analyze_sources(rows, **{**kwargs, "validation_policy": bad})
    policy = kwargs["validation_policy"].model_dump(mode="json")
    duplicate = next(
        rule.copy()
        for rule in policy["diagnostic_rules"]
        if rule["code"] == "source_overlap"
    )
    duplicate["max_witnesses"] = 2
    policy["diagnostic_rules"] = ordered(policy["diagnostic_rules"] + [duplicate])
    with pytest.raises(ValueError):
        rules.analyze_sources(
            rows, **{**kwargs, "validation_policy": rules.ValidationPolicy(**policy)}
        )


def test_f13_mutated_metadata_is_revalidated_and_returned_evidence_is_frozen():
    kwargs, _ = case()
    rows = [row(1)]
    bundle = rules.analyze_sources(rows, **kwargs)
    saved = rules.encode_validation_bundle(bundle, limits=limits())
    rows[0]["amount"] = 99
    kwargs["predicates"][0]["payload"]["node"]["upper"]["value"] = "99"
    assert rules.encode_validation_bundle(bundle, limits=limits()) == saved
    kwargs, _ = case()
    invalid = contract()
    kwargs["metadata"].context_contracts = [invalid, invalid]
    for operation in (
        lambda: rules.analyze_sources([row(1)], **kwargs),
        lambda: gate([row(1)], kwargs, bundle),
    ):
        with pytest.raises(ValueError):
            operation()


def _rehash(kind, payload):
    payload.pop("id", None)
    value = envelope(kind, payload)
    return {"id": value["id"], **value["payload"]}


def _decode(payload):
    return rules.decode_validation_bundle(
        json.dumps(payload, separators=(",", ":")).encode(),
        limits=limits(),
    )


def test_f06_approval_cannot_transfer_between_reports_with_same_warning():
    kwargs, _ = case()
    rows = [row(1, 0, 10), row(2, 10, 20)]
    original = rules.analyze_sources(rows, **kwargs)
    payload = json.loads(rules.encode_validation_bundle(original, limits=limits()))
    first = original.validation["reports"][0]
    alternate = first.model_dump(mode="json", exclude={"annotations"})
    alternate["validator"]["validator_id"] = "another-approved-producer"
    second = _rehash("report", alternate)
    payload["validation"]["reports"] = sorted(
        [payload["validation"]["reports"][0], second],
        key=lambda r: r["id"],
    )
    bundle = _decode(payload)
    report_two = next(r for r in bundle.validation["reports"] if r.id == second["id"])
    approval = approve(bundle, first.finding_ids, report=report_two)
    with pytest.raises(ValueError):
        rules.validate_build_input(
            rows,
            bundle=bundle,
            analysis_input_id=first.analysis_input_id,
            source_report_id=first.id,
            approvals=[approval],
            **kwargs,
        )
    accepted = rules.validate_build_input(
        rows,
        bundle=bundle,
        analysis_input_id=first.analysis_input_id,
        source_report_id=second["id"],
        approvals=[approval],
        **kwargs,
    )
    assert accepted.source_report_id == second["id"]


def _replace_finding(payload, index, altered):
    old = payload["validation"]["findings"][index]["id"]
    replacement = _rehash("finding", altered)
    payload["validation"]["findings"][index] = replacement
    payload["validation"]["findings"].sort(key=lambda f: f["id"])
    report = payload["validation"]["reports"][0]
    report["finding_ids"] = sorted(
        replacement["id"] if f == old else f for f in report["finding_ids"]
    )
    for check in report["checks"]:
        check["finding_ids"] = sorted(
            replacement["id"] if f == old else f for f in check["finding_ids"]
        )
    payload["validation"]["reports"] = [_rehash("report", report)]
    return _decode(payload)


def test_f07_rehashed_source_witness_outside_region_is_rejected_at_current_input_gate():
    kwargs, _ = case()
    rows = [row(1, 0, 10), row(2, 10, 20)]
    bundle = rules.analyze_sources(rows, **kwargs)
    payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
    finding = payload["validation"]["findings"][0]
    finding["witnesses"][0]["contexts"][0]["x"]["value"] = "9"
    altered = _replace_finding(payload, 0, finding)
    approval = approve(altered, altered.validation["reports"][0].finding_ids)
    with pytest.raises(ValueError):
        gate(rows, kwargs, altered, [approval])


def test_f07_masked_pair_full_supplied_admission_precedes_effective_mask():
    kwargs, _ = case(contracts=[contract(required=True)])
    rows = [row(1, 0, 10)]
    bundle = rules.analyze_sources(rows, **kwargs)
    pair = next(
        f for f in bundle.validation["findings"] if f.code == "profile_counterexample"
    )
    assert pair.witnesses and pair.witnesses[0].request.dont_care == ("x",)
    decision = approve(bundle, [pair.id])
    assert gate(rows, kwargs, bundle, [decision]).approval_ids == (decision.id,)
    for value, accepted in (("7", True), ("99", False)):
        payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
        index = next(
            i
            for i, f in enumerate(payload["validation"]["findings"])
            if f["id"] == pair.id
        )
        finding = payload["validation"]["findings"][index]
        finding["witnesses"][0]["request"]["provided_values"]["x"]["value"] = value
        altered = _replace_finding(payload, index, finding)
        decision = approve(altered, altered.validation["reports"][0].finding_ids)
        if accepted:
            assert gate(rows, kwargs, altered, [decision]).approval_ids == (
                decision.id,
            )
        else:
            with pytest.raises(ValueError):
                gate(rows, kwargs, altered, [decision])


def test_f11_incomplete_evidence_loads_but_never_grants_permission():
    kwargs, _ = case()
    rows = [row(1)]
    bundle = rules.analyze_sources(rows, **kwargs)
    payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
    report = payload["validation"]["reports"][0]
    report["checks"][0]["complete"] = False
    report["checks"][0]["status"] = "unsupported"
    payload["validation"]["reports"] = [_rehash("report", report)]
    historical = _decode(payload)
    assert historical.validation["reports"][0].checks[0].complete is False
    with pytest.raises(ValueError):
        gate(rows, kwargs, historical)


def test_f11_missing_internal_material_and_duplicate_json_keys_fail_transport():
    kwargs, _ = case()
    bundle = rules.analyze_sources([row(1)], **kwargs)
    encoded = rules.encode_validation_bundle(bundle, limits=limits())
    with pytest.raises(ValueError):
        rules.decode_validation_bundle(
            b'{"schema_version":1,' + encoded[1:], limits=limits()
        )
    payload = json.loads(encoded)
    payload["predicates"]["predicates"] = []
    with pytest.raises(ValueError):
        _decode(payload)


def test_f12_fresh_process_review_and_current_input_permission(tmp_path):
    kwargs, _ = case()
    rows = [row(1, 0, 10), row(2, 10, 20)]
    bundle = rules.analyze_sources(rows, **kwargs)
    pending = tmp_path / "pending.json"
    approved = tmp_path / "approved.json"
    pending.write_bytes(rules.encode_validation_bundle(bundle, limits=limits()))
    script = """
import pathlib, sys
import mountainash_rules as r
from tests.accumulator.source_analysis_fixtures import limits, envelope
data = pathlib.Path(sys.argv[1]).read_bytes()
bundle = r.decode_validation_bundle(data, limits=limits())
assert r.encode_validation_bundle(bundle, limits=limits()) == data
report = bundle.validation["reports"][0]
assert len(report.finding_ids) == 1
payload = dict(schema_version=1, analysis_input_id=report.analysis_input_id,
    report_id=report.id, authority_ref="explicit-review", actor_ref="reviewer",
    decision="approve_warnings", scope=report.scope.model_dump(mode="json"),
    warning_ids=[report.finding_ids[0]])
record = envelope("approval", payload)
approval = r.WarningApproval.model_validate({"id": record["id"], **record["payload"]})
attached = r.attach_warning_approvals(bundle, [approval], limits=limits())
pathlib.Path(sys.argv[2]).write_bytes(r.encode_validation_bundle(attached, limits=limits()))
print("review saved without source rows or build")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(pending), str(approved)],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == "review saved without source rows or build"
    saved = approved.read_bytes()
    restored = rules.decode_validation_bundle(saved, limits=limits())
    assert rules.encode_validation_bundle(restored, limits=limits()) == saved
    decision = restored.validation["approvals"][0]
    assert gate(rows, kwargs, restored, [decision]).approval_ids == (decision.id,)
    with pytest.raises(ValueError):
        gate([{**rows[0], "amount": 99}, rows[1]], kwargs, restored, [decision])
    assert approved.read_bytes() == saved


def _integer_region_contains(bundle, identifier, point):
    """Independent finite-point oracle for these authored integer fixtures only."""
    nodes = {e["id"]: e["payload"]["node"] for e in bundle.predicates["predicates"]}

    def contains(identifier):
        node = nodes[identifier]
        op = node["op"]
        if op in {"true", "false"}:
            return op == "true"
        if op == "and":
            return all(contains(child) for child in node["args"])
        if op == "or":
            return any(contains(child) for child in node["args"])
        if op == "not":
            return not contains(node["arg"])
        value = point[node["field"]]
        if op == "eq":
            return value == int(node["value"]["value"])
        if op == "in":
            return value in [int(v["value"]) for v in node["values"]]
        assert op == "interval", op
        lower = node["lower"]
        upper = node["upper"]
        return (
            lower is None
            or value > int(lower["value"])
            or (node["lower_closed"] and value == int(lower["value"]))
        ) and (
            upper is None
            or value < int(upper["value"])
            or (node["upper_closed"] and value == int(upper["value"]))
        )

    return contains(identifier)


def test_f09_default_partition_coverage_keeps_full_region_and_scope_categories():
    from tests.accumulator.source_analysis_fixtures import partition_case

    kwargs = partition_case()
    rows = [
        {**row(1, 0, 10), "key": rules.UNKNOWN_NUMERIC},
        {**row(2, 0, 20), "key": 1},
    ]
    bundle = rules.analyze_sources(rows, **kwargs)
    gaps = [f for f in bundle.validation["findings"] if f.code == "coverage_gap"]
    assert len(gaps) == 2
    assert {bool(f.scope.profile_refs) for f in gaps} == {False, True}
    for finding in gaps:
        assert (
            finding.scope.partition_refs[0]["key_values"][0]["match"]["kind"]
            == "wildcard"
        )
        for key in (1, 2):
            for x in (0, 10, 11, 20):
                assert _integer_region_contains(
                    bundle, finding.region_predicate_id, {"key": key, "x": x}
                ) == (x > 10)
    decision = approve(bundle, [f.id for f in gaps])
    assert gate(rows, kwargs, bundle, [decision]).approval_ids == (decision.id,)
    report = bundle.validation["reports"][0]
    coverage = [c for c in report.checks if c.check_id == "coverage"]
    assert {bool(c.scope.profile_refs) for c in coverage} == {False, True}
    for drop_global in (False, True):
        payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
        changed = payload["validation"]["reports"][0]
        changed["checks"] = [
            c
            for c in changed["checks"]
            if c["check_id"] != "coverage"
            or bool(c["scope"]["profile_refs"]) == drop_global
        ]
        retained = sorted({f for c in changed["checks"] for f in c["finding_ids"]})
        changed["finding_ids"] = retained
        payload["validation"]["reports"] = [_rehash("report", changed)]
        altered = _decode(payload)
        decision = approve(altered, retained)
        with pytest.raises(ValueError):
            gate(rows, kwargs, altered, [decision])


def test_f09_routing_gap_unions_states_but_preserves_contract_identity():
    from tests.accumulator.source_analysis_fixtures import partition_case

    kwargs = partition_case(default=False, profile_coverage=False)
    rows = [{**row(1), "key": 1}]
    bundle = rules.analyze_sources(rows, **kwargs)
    gaps = [f for f in bundle.validation["findings"] if f.code == "routing_gap"]
    assert len(gaps) == 2
    by_contract = {f.scope.profile_refs[0]["contract_id"]: f for f in gaps}
    for key in (1, 2):
        for x in (0, 20):
            assert _integer_region_contains(
                bundle,
                by_contract["optional"].region_predicate_id,
                {"key": key, "x": x},
            )
            assert _integer_region_contains(
                bundle,
                by_contract["required"].region_predicate_id,
                {"key": key, "x": x},
            ) == (key == 2)
    assert all(
        f.severity == "error" and not f.source_ids and not f.witnesses for f in gaps
    )
    with pytest.raises(ValueError):
        gate(rows, kwargs, bundle)


def test_f08_zero_fields_and_empty_source_universe_are_supported():
    kwargs, _ = case()
    from tests.accumulator.source_analysis_fixtures import predicate

    region = predicate({"op": "true"})
    kwargs["metadata"] = rules.DimensionsMetadata(dimensions=[])
    kwargs["domains"] = [
        rules.DomainDefinition(
            schema_version=1,
            domain_id="D",
            fields=[],
            predicate_id=region["id"],
        )
    ]
    kwargs["predicates"] = [region]
    kwargs["validation_policy"] = rules.ValidationPolicy(
        schema_version=1,
        policy_id="empty",
        required_checks=[],
        coverage_requirements=[],
        diagnostic_rules=[],
    )
    bundle = rules.analyze_sources([], **kwargs)
    assert not bundle.validation["findings"]
    assert gate([], kwargs, bundle).approval_ids == ()


@pytest.mark.parametrize(
    "provenance,expected",
    [
        ("none", False),
        ("contributors", True),
        ("cell", True),
    ],
)
def test_f08_equal_outputs_do_not_hide_requested_provenance(provenance, expected):
    kwargs, _ = case(contracts=[contract(provenance=provenance)], witnesses=0)
    rows = [row(1, 0, 9), row(2, 10, 20)]
    bundle = rules.analyze_sources(rows, **kwargs)
    findings = [
        f for f in bundle.validation["findings"] if f.code == "profile_counterexample"
    ]
    assert bool(findings) is expected
    if expected:
        assert not findings[0].witnesses
        with pytest.raises(ValueError):
            gate(rows, kwargs, bundle)
    else:
        assert gate(rows, kwargs, bundle).approval_ids == ()


def test_f08_provider_subset_limits_outcomes_without_repairing_the_domain():
    from tests.accumulator.source_analysis_fixtures import predicate, scalar

    original = contract(promise="definite_outcome")
    definition = original.model_dump(mode="json")
    definition["domain_ref"] = "P"
    provider_contract = rules.ContextContract(**definition)
    kwargs, _ = case(contracts=[provider_contract])
    region = predicate(
        dict(
            op="interval",
            field="x",
            lower=scalar(0),
            upper=scalar(9),
            lower_closed=True,
            upper_closed=True,
        )
    )
    kwargs["predicates"].append(region)
    kwargs["domains"].append(
        rules.DomainDefinition(
            schema_version=1,
            domain_id="P",
            fields=kwargs["domains"][0].fields,
            predicate_id=region["id"],
        )
    )
    rows = [row(1, 0, 9), row(2, 10, 20, amount=99)]
    bundle = rules.analyze_sources(rows, **kwargs)
    assert not bundle.validation["findings"]
    assert gate(rows, kwargs, bundle).approval_ids == ()
    wider = predicate(
        dict(
            op="interval",
            field="x",
            lower=scalar(0),
            upper=scalar(99),
            lower_closed=True,
            upper_closed=True,
        )
    )
    kwargs["predicates"].append(wider)
    kwargs["domains"][1] = rules.DomainDefinition(
        schema_version=1,
        domain_id="P",
        fields=kwargs["domains"][0].fields,
        predicate_id=wider["id"],
    )
    with pytest.raises(ValueError):
        rules.analyze_sources(rows, **kwargs)


def test_f13_attachment_rejects_duplicate_decisions_without_mutating_history():
    kwargs, _ = case()
    bundle = rules.analyze_sources([row(1, 0, 10), row(2, 10, 20)], **kwargs)
    decision = approve(bundle, bundle.validation["reports"][0].finding_ids)
    with pytest.raises(ValueError):
        rules.attach_warning_approvals(bundle, [decision, decision], limits=limits())
    assert bundle.validation["approvals"] == ()


def test_f09_routing_ambiguity_unions_overlapping_presence_states():
    from tests.accumulator.source_analysis_fixtures import (
        partition_case,
        predicate,
        scalar,
    )

    kwargs = partition_case(default=False, profile_coverage=False)
    routing_payload = json.loads(json.dumps(kwargs["routing"]["payload"]))
    second_key = dict(routing_payload["key_dimensions"][0])
    second_key.update(
        dimension_name="tenant", context_field="tenant", rule_field="tenant"
    )
    routing_payload["key_dimensions"].append(second_key)
    routing_payload["partition_keys"] = ordered(
        [
            [
                {
                    "dimension_name": "key",
                    "match": {"kind": "value", "value": scalar(1)},
                },
                {"dimension_name": "tenant", "match": {"kind": "wildcard"}},
            ],
            [
                {"dimension_name": "key", "match": {"kind": "wildcard"}},
                {
                    "dimension_name": "tenant",
                    "match": {"kind": "value", "value": scalar(1)},
                },
            ],
        ]
    )
    routing = envelope("routing", routing_payload)
    kwargs["routing"] = routing
    tenant = predicate(
        dict(
            op="interval",
            field="tenant",
            lower=scalar(1),
            upper=scalar(2),
            lower_closed=True,
            upper_closed=True,
        )
    )
    region = predicate(
        dict(
            op="and",
            args=[
                kwargs["predicates"][1]["id"],
                tenant["id"],
                kwargs["predicates"][0]["id"],
            ],
        )
    )
    kwargs["predicates"] += [tenant, region]
    fields = [
        rules.DomainField(name=name, data_type="int") for name in ("key", "tenant", "x")
    ]
    kwargs["domains"] = [
        rules.DomainDefinition(
            schema_version=1,
            domain_id="D",
            fields=fields,
            predicate_id=region["id"],
        )
    ]
    contracts = []
    for old in kwargs["metadata"].context_contracts:
        payload = old.model_dump(mode="json", exclude_none=True)
        payload["fields"].insert(
            1,
            dict(
                name="tenant", data_type="int", required=old.contract_id == "required"
            ),
        )
        contracts.append(rules.ContextContract(**payload))
    kwargs["metadata"] = rules.DimensionsMetadata(
        dimensions=[
            *kwargs["metadata"].dimensions,
            rules.Dimension(
                dimension_name="tenant",
                data_type="int",
                match_strategy="exact_key",
                role="context_key",
            ),
        ],
        context_contracts=contracts,
    )
    partitions = ordered(
        [
            {"routing_id": routing["id"], "key_values": key}
            for key in routing_payload["partition_keys"]
        ]
    )
    policy = kwargs["validation_policy"].model_dump(mode="json")
    policy["coverage_requirements"] = []
    policy["required_checks"] = []
    for diagnostic in policy["diagnostic_rules"]:
        diagnostic["scope"]["partition_refs"] = partitions
    policy["diagnostic_rules"] = ordered(policy["diagnostic_rules"])
    kwargs["validation_policy"] = rules.ValidationPolicy(**policy)
    rows = [
        {**row(1), "key": 1, "tenant": rules.UNKNOWN_NUMERIC},
        {**row(2), "key": rules.UNKNOWN_NUMERIC, "tenant": 1},
    ]
    bundle = rules.analyze_sources(rows, **kwargs)
    ties = [f for f in bundle.validation["findings"] if f.code == "routing_ambiguity"]
    assert len(ties) == 2
    assert {f.scope.profile_refs[0]["contract_id"] for f in ties} == {
        "required",
        "optional",
    }
    for finding in ties:
        for key in (1, 2):
            for tenant in (1, 2):
                assert _integer_region_contains(
                    bundle,
                    finding.region_predicate_id,
                    {"key": key, "tenant": tenant, "x": 7},
                ) == (key == tenant == 1)


def test_f10_later_partition_cannot_reset_contributor_budget():
    from tests.accumulator.source_analysis_fixtures import partition_case

    single = partition_case(default=False, profile_coverage=False)
    cap = limits(max_contributor_edges=1)
    single["limits"] = cap
    first = {**row(1), "key": 1}
    report = rules.analyze_sources([first], **single).validation["reports"][0]
    assert all(check.complete for check in report.checks)
    multiple = partition_case(profile_coverage=False)
    multiple["limits"] = cap
    with pytest.raises(rules.ExactResourceError) as caught:
        rules.analyze_sources(
            [first, {**row(2), "key": rules.UNKNOWN_NUMERIC}], **multiple
        )
    assert caught.value.counter == "max_contributor_edges"
    assert caught.value.observed == 1


def test_f09_global_coverage_cannot_be_marked_not_required_for_selected_profiles():
    from tests.accumulator.source_analysis_fixtures import partition_case

    kwargs = partition_case(profile_coverage=False)
    rows = [{**row(1), "key": 1}, {**row(2), "key": rules.UNKNOWN_NUMERIC}]
    bundle = rules.analyze_sources(rows, **kwargs)
    assert gate(rows, kwargs, bundle).approval_ids == ()
    payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
    report = payload["validation"]["reports"][0]
    check = next(
        c
        for c in report["checks"]
        if c["check_id"] == "coverage" and c["scope"]["profile_refs"]
    )
    check["status"] = "not_required"
    payload["validation"]["reports"] = [_rehash("report", report)]
    historical = _decode(payload)
    with pytest.raises(ValueError):
        gate(rows, kwargs, historical)


def test_f05_changed_compilation_domain_invalidates_saved_report():
    from tests.accumulator.source_analysis_fixtures import predicate, scalar

    kwargs, _ = case()
    rows = [row(1)]
    bundle = rules.analyze_sources(rows, **kwargs)
    changed = predicate(
        dict(
            op="interval",
            field="x",
            lower=scalar(0),
            upper=scalar(19),
            lower_closed=True,
            upper_closed=True,
        )
    )
    kwargs["predicates"].append(changed)
    kwargs["domains"] = [
        rules.DomainDefinition(
            schema_version=1,
            domain_id="D",
            fields=kwargs["domains"][0].fields,
            predicate_id=changed["id"],
        )
    ]
    with pytest.raises(ValueError):
        gate(rows, kwargs, bundle)


def test_f06_partial_warning_decision_does_not_authorize_remaining_warnings():
    kwargs, _ = case(witnesses=0)
    rows = [row(1, 0, 5), row(2, 0, 5)]
    bundle = rules.analyze_sources(rows, **kwargs)
    findings = bundle.validation["findings"]
    assert {f.code for f in findings} == {"source_overlap", "duplicate_source"}
    decision = approve(bundle, [findings[0].id])
    attached = rules.attach_warning_approvals(bundle, [decision], limits=limits())
    with pytest.raises(ValueError):
        gate(rows, kwargs, attached, [decision])


def test_f06_wrong_partition_scope_cannot_authorize_default_coverage_warning():
    from tests.accumulator.source_analysis_fixtures import partition_case

    kwargs = partition_case(profile_coverage=False)
    rows = [{**row(1), "key": 1}, {**row(2, 0, 10), "key": rules.UNKNOWN_NUMERIC}]
    bundle = rules.analyze_sources(rows, **kwargs)
    warning = next(f for f in bundle.validation["findings"] if f.code == "coverage_gap")
    partitions = bundle.validation["reports"][0].scope.model_dump(mode="json")[
        "partition_refs"
    ]
    concrete = next(
        p for p in partitions if p["key_values"][0]["match"]["kind"] == "value"
    )
    scope = rules.Scope(partition_refs=[concrete], domain_refs=["D"], profile_refs=[])
    decision = approve(bundle, [warning.id], scope=scope)
    with pytest.raises(ValueError):
        gate(rows, kwargs, bundle, [decision])


def test_f11_unknown_source_semantics_remain_inspectable_but_not_authorizable():
    kwargs, _ = case()
    rows = [row(1)]
    bundle = rules.analyze_sources(rows, **kwargs)
    payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
    report = payload["validation"]["reports"][0]
    report["validator"]["semantic_version"] = "source-analysis-999"
    payload["validation"]["reports"] = [_rehash("report", report)]
    historical = _decode(payload)
    assert (
        historical.validation["reports"][0].validator["semantic_version"]
        == "source-analysis-999"
    )
    with pytest.raises(ValueError):
        gate(rows, kwargs, historical)


def test_f02_domain_clipped_interior_singleton_is_not_an_authored_boundary():
    from tests.accumulator.source_analysis_fixtures import predicate

    kwargs, _ = case(witnesses=0)
    singleton = predicate(
        dict(
            op="interval",
            field="x",
            lower={"type": "int", "value": "5"},
            upper={"type": "int", "value": "5"},
            lower_closed=True,
            upper_closed=True,
        )
    )
    kwargs["predicates"] = [singleton]
    kwargs["domains"] = [
        rules.DomainDefinition(
            schema_version=1,
            domain_id="D",
            fields=kwargs["domains"][0].fields,
            predicate_id=singleton["id"],
        )
    ]
    bundle = rules.analyze_sources([row(1, 0, 10), row(2, 0, 10)], **kwargs)
    assert {f.code for f in bundle.validation["findings"]} == {
        "source_overlap",
        "duplicate_source",
    }


def test_f13_unknown_source_checks_cannot_be_self_certified_passed():
    kwargs, scope = case()
    policy = kwargs["validation_policy"].model_dump(mode="json")
    policy["required_checks"] = [
        dict(
            stage="source",
            check_id="unimplemented-proof",
            scope=scope.model_dump(mode="json"),
        ),
    ]
    kwargs["validation_policy"] = rules.ValidationPolicy(**policy)
    with pytest.raises(ValueError):
        rules.analyze_sources([row(1)], **kwargs)


def test_f13_false_definite_promise_cannot_be_downgraded_by_policy():
    kwargs, _ = case(contracts=[contract(promise="definite_outcome")])
    policy = kwargs["validation_policy"].model_dump(mode="json")
    for diagnostic in policy["diagnostic_rules"]:
        if diagnostic["code"] == "profile_counterexample":
            diagnostic["severity"] = "warning"
    policy["diagnostic_rules"] = ordered(policy["diagnostic_rules"])
    kwargs["validation_policy"] = rules.ValidationPolicy(**policy)
    with pytest.raises(ValueError):
        rules.analyze_sources([row(1, 0, 10)], **kwargs)


def test_f12_retained_phase4_handoffs_remain_portable_and_permission_checked():
    from pathlib import Path

    fixtures = Path(__file__).parents[1] / "fixtures"
    kwargs, _ = case()
    clean_bytes = (fixtures / "exact_source_clean.json").read_bytes()
    clean = rules.decode_validation_bundle(clean_bytes, limits=limits())
    assert rules.encode_validation_bundle(clean, limits=limits()) == clean_bytes
    assert gate([row(1)], kwargs, clean).approval_ids == ()
    reviewed_bytes = (fixtures / "exact_source_reviewed.json").read_bytes()
    reviewed = rules.decode_validation_bundle(reviewed_bytes, limits=limits())
    assert rules.encode_validation_bundle(reviewed, limits=limits()) == reviewed_bytes
    rows = [row(1, 0, 10), row(2, 10, 20)]
    decisions = reviewed.validation["approvals"]
    assert gate(rows, kwargs, reviewed, decisions).approval_ids == (decisions[0].id,)
    with pytest.raises(ValueError):
        gate(rows, kwargs, reviewed)
    hard_kwargs, _ = case(contracts=[contract(promise="definite_outcome")])
    error_bytes = (fixtures / "exact_source_error.json").read_bytes()
    historical_error = rules.decode_validation_bundle(error_bytes, limits=limits())
    assert (
        rules.encode_validation_bundle(historical_error, limits=limits()) == error_bytes
    )
    assert any(f.severity == "error" for f in historical_error.validation["findings"])
    with pytest.raises(ValueError):
        gate([row(1, 0, 10)], hard_kwargs, historical_error)


@pytest.mark.parametrize(
    ("strategy", "source_value"),
    [
        ("prefix", "pre"),
        ("suffix", "pre"),
        ("contains", "pre"),
        ("regex", "^pre"),
        ("context_regex", "pre"),
    ],
)
def test_f03_generated_string_languages_are_dependency_closed_and_portable(
    strategy, source_value
):
    kwargs, _ = string_case(strategy)
    rows = [{**row(1), "name": source_value}]

    bundle = rules.analyze_sources(rows, **kwargs)

    nodes = {
        envelope["id"]: envelope["payload"]["node"]
        for envelope in bundle.predicates["predicates"]
    }
    language_ids = {
        node["language_id"] for node in nodes.values() if node["op"] == "language"
    }
    assert language_ids == {
        envelope["id"] for envelope in bundle.predicates["languages"]
    }
    reloaded = rules.decode_validation_bundle(
        rules.encode_validation_bundle(bundle, limits=limits()), limits=limits()
    )
    assert gate(rows, kwargs, reloaded).approval_ids == ()


def test_f03_retained_predicates_exclude_proof_scratch_without_changing_report():
    kwargs, _ = string_case("prefix")
    bundle = rules.analyze_sources([{**row(1), "name": "pre"}], **kwargs)
    nodes = {
        envelope["id"]: envelope["payload"]["node"]
        for envelope in bundle.predicates["predicates"]
    }
    roots = {
        envelope["payload"]["predicate_id"] for envelope in bundle.predicates["domains"]
    }
    roots.update(
        origin["predicate_id"] for origin in bundle.predicates["source_origins"]
    )
    roots.update(
        finding.region_predicate_id
        for finding in bundle.validation["findings"]
        if finding.region_predicate_id is not None
    )
    roots.update(
        requirement.region_predicate_id
        for requirement in bundle.validation["analysis_inputs"][
            0
        ].validation_policy.coverage_requirements
    )
    retained = set()
    pending = list(roots)
    while pending:
        identifier = pending.pop()
        if identifier in retained:
            continue
        retained.add(identifier)
        node = nodes[identifier]
        pending.extend(node.get("args", ()))
        if node["op"] == "not":
            pending.append(node["arg"])
    assert set(nodes) == retained
    assert (
        rules.decode_validation_bundle(
            rules.encode_validation_bundle(bundle, limits=limits()), limits=limits()
        )
        .validation["reports"][0]
        .id
        == bundle.validation["reports"][0].id
    )


def test_f03_subset_profile_dimensions_replay_with_only_selected_mapping():
    selected_profile_contract = contract().model_copy(
        update={
            "fields": (
                *contract().fields,
                rules.ContextField(name="y", data_type="int", required=False),
            )
        }
    )
    kwargs, _ = case(contracts=[selected_profile_contract], extra_field=True)
    metadata = kwargs["metadata"]
    kwargs["metadata"] = rules.DimensionsMetadata(
        dimensions=[
            *metadata.dimensions,
            rules.Dimension(
                dimension_name="y",
                data_type="int",
                match_strategy="range",
                range_min_field="ylo",
                range_max_field="yhi",
            ),
        ],
        context_contracts=metadata.context_contracts,
    )
    rows = [{**row(1), "ylo": 0, "yhi": 20}]

    bundle = rules.analyze_sources(rows, **kwargs)
    reloaded = rules.decode_validation_bundle(
        rules.encode_validation_bundle(bundle, limits=limits()), limits=limits()
    )

    finding = next(
        item
        for item in reloaded.validation["findings"]
        if item.code == "profile_counterexample"
    )
    decision = approve(reloaded, [finding.id])
    assert gate(rows, kwargs, reloaded, [decision]).approval_ids == (decision.id,)


@pytest.mark.parametrize("declaration", ["omitted", "duplicate"])
def test_f03_gate_strictly_admits_current_predicate_sequence(declaration):
    kwargs, _ = case()
    rows = [row(1)]
    bundle = rules.analyze_sources(rows, **kwargs)
    if declaration == "omitted":
        kwargs["predicates"] = []
    else:
        kwargs["predicates"] = [*kwargs["predicates"], kwargs["predicates"][0]]

    with pytest.raises(ValueError):
        gate(rows, kwargs, bundle)


def test_f03_metadata_snapshot_reserves_before_reconstruction():
    kwargs, _ = case()
    kwargs["metadata"].output_fields = ["x" * 1_000]
    kwargs["limits"] = limits(max_input_bytes=100)

    def fail_if_snapshot_reconstructs(*_args, **_kwargs):
        raise AssertionError("metadata copy began before its budget reservation")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            rules.DimensionsMetadata, "model_dump", fail_if_snapshot_reconstructs
        )
        with pytest.raises(rules.ExactResourceError):
            rules.analyze_sources([], **kwargs)


def _rehash_analysis_findings_and_report(payload, policy, alter_finding=None):
    analysis = payload["validation"]["analysis_inputs"][0]
    analysis["validation_policy"] = policy
    analysis = _rehash("analysis-input", analysis)
    payload["validation"]["analysis_inputs"] = [analysis]
    replacements = {}
    findings = []
    for finding in payload["validation"]["findings"]:
        finding["analysis_input_id"] = analysis["id"]
        if alter_finding is not None:
            alter_finding(finding)
        old_id = finding["id"]
        replacement = _rehash("finding", finding)
        replacements[old_id] = replacement["id"]
        findings.append(replacement)
    payload["validation"]["findings"] = sorted(findings, key=lambda item: item["id"])
    report = payload["validation"]["reports"][0]
    report["analysis_input_id"] = analysis["id"]
    report["finding_ids"] = sorted(replacements[item] for item in report["finding_ids"])
    for check in report["checks"]:
        check["finding_ids"] = sorted(
            replacements[item] for item in check["finding_ids"]
        )
    payload["validation"]["reports"] = [_rehash("report", report)]
    return _decode(payload)


def test_f03_gate_revalidates_selected_analysis_diagnostic_catalogue():
    kwargs, scope = case()
    rows = [row(1)]
    bundle = rules.analyze_sources(rows, **kwargs)
    policy = kwargs["validation_policy"].model_dump(mode="json")
    policy["diagnostic_rules"].append(
        rules.DiagnosticRule(
            stage="source",
            check_id="source_predicates",
            code="foreign_diagnostic",
            scope=scope,
            severity="warning",
            witness_kind="none",
            max_witnesses=0,
        ).model_dump(mode="json")
    )
    policy["diagnostic_rules"] = ordered(policy["diagnostic_rules"])
    kwargs["validation_policy"] = rules.ValidationPolicy(**policy)
    payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
    altered = _rehash_analysis_findings_and_report(payload, policy)

    with pytest.raises(ValueError):
        gate(rows, kwargs, altered)


def test_f03_gate_rejects_rehashed_definite_profile_warning_downgrade():
    kwargs, _ = case(contracts=[contract(promise="definite_outcome")])
    rows = [row(1, 0, 10)]
    bundle = rules.analyze_sources(rows, **kwargs)
    policy = kwargs["validation_policy"].model_dump(mode="json")
    for diagnostic in policy["diagnostic_rules"]:
        if diagnostic["code"] == "profile_counterexample":
            diagnostic["severity"] = "warning"
    policy["diagnostic_rules"] = ordered(policy["diagnostic_rules"])
    kwargs["validation_policy"] = rules.ValidationPolicy(**policy)
    payload = json.loads(rules.encode_validation_bundle(bundle, limits=limits()))
    altered = _rehash_analysis_findings_and_report(
        payload,
        policy,
        lambda finding: (
            finding.update({"severity": "warning"})
            if finding["code"] == "profile_counterexample"
            else None
        ),
    )
    finding = next(
        item
        for item in altered.validation["findings"]
        if item.code == "profile_counterexample"
    )
    decision = approve(altered, [finding.id])

    with pytest.raises(ValueError):
        gate(rows, kwargs, altered, [decision])


@pytest.mark.parametrize("check_id", ["routing", "profiles"])
def test_f13_contract_checks_reject_unrepresentable_profileless_requirements(check_id):
    from tests.accumulator.source_analysis_fixtures import partition_case

    kwargs = partition_case(default=False, profile_coverage=False)
    policy = kwargs["validation_policy"].model_dump(mode="json")
    policy["required_checks"].append(
        dict(
            stage="source",
            check_id=check_id,
            scope=policy["required_checks"][0]["scope"],
        )
    )
    policy["required_checks"] = ordered(policy["required_checks"])
    kwargs["validation_policy"] = rules.ValidationPolicy(**policy)
    with pytest.raises(ValueError):
        rules.analyze_sources([{**row(1), "key": 1}], **kwargs)


def test_f09_narrow_routing_requirement_reuses_complete_error_evidence():
    from tests.accumulator.source_analysis_fixtures import (
        envelope,
        partition_case,
        scalar,
    )

    kwargs = partition_case(default=False, profile_coverage=False)
    routing = kwargs["routing"]["payload"]
    routing["partition_keys"].append(
        [{"dimension_name": "key", "match": {"kind": "value", "value": scalar(2)}}]
    )
    kwargs["routing"] = envelope("routing", routing)
    partitions = ordered(
        [
            {"routing_id": kwargs["routing"]["id"], "key_values": keys}
            for keys in routing["partition_keys"]
        ]
    )
    policy = kwargs["validation_policy"].model_dump(mode="json")
    for collection in ("required_checks", "diagnostic_rules", "coverage_requirements"):
        for record in policy[collection]:
            record["scope"]["partition_refs"] = partitions
        policy[collection] = ordered(policy[collection])
    policy["required_checks"].append(
        dict(
            stage="source",
            check_id="routing",
            scope=dict(
                partition_refs=partitions[:1],
                domain_refs=["D"],
                profile_refs=[
                    {"contract_id": c.contract_id, "profile_id": p.profile_id}
                    for c in kwargs["metadata"].context_contracts
                    for p in c.profiles
                ],
            ),
        )
    )
    policy["required_checks"] = ordered(policy["required_checks"])
    kwargs["validation_policy"] = rules.ValidationPolicy(**policy)
    bundle = rules.analyze_sources(
        [{**row(1), "key": 1}, {**row(2), "key": 2}], **kwargs
    )
    assert any(f.code == "routing_gap" for f in bundle.validation["findings"])
    routing_checks = [
        check
        for check in bundle.validation["reports"][0].checks
        if check.check_id == "routing"
    ]
    assert all(check.status == "findings" for check in routing_checks)
