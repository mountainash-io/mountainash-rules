"""Tests for strict exact-accumulator contract declarations."""

import json
from pathlib import Path

from decimal import Decimal

import pytest

from mountainash_rules.core.codec import content_id
from mountainash_rules.core.constants import DataType
from mountainash_rules.core.contracts import (
    ContextContract,
    ContextField,
    DomainDefinition,
    ExactLimits,
    ExactResourceError,
    Finding,
    OperationBudget,
    OutcomeRecord,
    ReportCheck,
    Scope,
    ValidationPolicy,
    ValidationReport,
    ValidationBundle,
    WarningApproval,
)
from mountainash_rules.core.codec import (
    decode_validation_bundle,
    encode_validation_bundle,
)
from mountainash_rules.engines.accumulator.analysis import attach_warning_approvals


LIMITS = {
    "language": {
        "max_input_bytes": 1,
        "max_nesting": 1,
        "max_nfa_states": 1,
        "max_states": 1,
        "max_transitions": 1,
        "max_work": 1,
    },
    "max_input_bytes": 100,
    "max_output_bytes": 100,
    "max_work": 100,
    "max_live_bytes": 100,
    "max_predicate_nodes": 100,
    "max_dfa_states": 100,
    "max_dfa_transitions": 100,
    "max_theory_states": 100,
    "max_regions": 100,
    "max_scopes": 100,
    "max_source_scope_edges": 100,
    "max_contributor_edges": 100,
    "max_word_rows": 100,
    "max_numeric_bits": 100,
    "max_witnesses": 100,
}


def test_limits_require_complete_non_boolean_bounded_counters():
    """Removing a ceiling, accepting bools, or overflowing must be rejected."""
    assert ExactLimits.model_validate(LIMITS).max_regions == 100

    for field, value in (("max_work", True), ("max_work", -1), ("max_work", 2**63)):
        payload = {**LIMITS, field: value}
        with pytest.raises(ValueError):
            ExactLimits.model_validate(payload)

    missing = dict(LIMITS)
    del missing["max_witnesses"]
    with pytest.raises(ValueError):
        ExactLimits.model_validate(missing)


def test_budget_never_refunds_cumulative_work_but_releases_live_state():
    """Refunding cumulative work would permit an over-budget operation."""
    budget = OperationBudget(ExactLimits.model_validate(LIMITS), "build")
    budget.reserve("max_work", 75, phase="normalize", units="steps")
    budget.release("max_work", 75)
    with pytest.raises(ExactResourceError) as failure:
        budget.reserve("max_work", 26, phase="normalize", units="steps")
    assert failure.value.observed == 75
    assert failure.value.requested == 101

    budget.reserve("max_live_bytes", 75, phase="normalize", units="bytes")
    budget.release("max_live_bytes", 75)
    budget.reserve("max_live_bytes", 100, phase="normalize", units="bytes")


def test_budget_reports_checked_counter_overflow_as_resource_exhaustion():
    """An overflowing valid reservation is an exhausted operation, not malformed input."""
    budget = OperationBudget(
        ExactLimits.model_validate({**LIMITS, "max_work": 2**63 - 1}), "build"
    )
    budget.reserve("max_work", 2**63 - 1, phase="normalize", units="steps")
    with pytest.raises(ExactResourceError) as failure:
        budget.reserve("max_work", 1, phase="normalize", units="steps")
    assert failure.value.observed == 2**63 - 1
    assert failure.value.requested == 2**63


def test_domain_and_contract_records_freeze_nested_collections_and_validate_profiles():
    """Mutable nested policy data or incoherent profile modes would alter a binding."""
    domain = DomainDefinition.model_validate(
        {
            "schema_version": 1,
            "domain_id": "pricing",
            "fields": [
                {"name": "at", "data_type": "datetime", "timezone": "utc"},
                {"name": "region", "data_type": "str"},
            ],
            "predicate_id": "predicate:1:" + "a" * 64,
        }
    )
    assert domain.fields[0].data_type is DataType.DATETIME
    with pytest.raises((TypeError, ValueError)):
        domain.fields += domain.fields

    contract = ContextContract.model_validate(
        {
            "schema_version": 1,
            "contract_id": "preview",
            "fields": [
                {"name": "region", "data_type": "str", "required": False},
            ],
            "domain_ref": "pricing",
            "profiles": [
                {
                    "profile_id": "lookup",
                    "mode": "resolve",
                    "output_fields": ["amount.sum"],
                    "provenance": "none",
                    "dimensions": ["region"],
                    "allow_dont_care": ["region"],
                    "promise": "allow_unresolved",
                    "on_unresolved": "withhold",
                }
            ],
        }
    )
    assert contract.profiles[0].on_unresolved == "withhold"
    assert isinstance(contract.profiles, tuple)

    invalid = contract.model_dump(mode="json")
    invalid["profiles"][0]["mode"] = "candidates"
    with pytest.raises(ValueError, match="candidate"):
        ContextContract.model_validate(invalid)


def test_context_field_rejects_non_datetime_timezone_and_contract_rejects_duplicate_names():
    """Type/field aliases cannot silently produce two physical field definitions."""
    with pytest.raises(ValueError):
        ContextField.model_validate(
            {
                "name": "region",
                "data_type": "str",
                "required": True,
                "timezone": "utc",
            }
        )

    with pytest.raises(ValueError, match="Duplicate"):
        ContextContract.model_validate(
            {
                "schema_version": 1,
                "contract_id": "duplicate-fields",
                "fields": [
                    {"name": "region", "data_type": "str", "required": True},
                    {"name": "region", "data_type": "str", "required": False},
                ],
                "domain_ref": "pricing",
                "profiles": [
                    {
                        "profile_id": "lookup",
                        "mode": "candidates",
                        "output_fields": [],
                        "provenance": "none",
                        "dimensions": [],
                        "allow_dont_care": [],
                        "promise": "candidate_only",
                    }
                ],
            }
        )


def test_frozen_evidence_serializes_and_roundtrips_without_mutable_backdoors():
    from mountainash_rules.core.contracts import Scope, WitnessRequest

    request = WitnessRequest(
        provided_values={"x": {"type": "int", "value": "1"}},
        unavailable_fields=(),
        dont_care=(),
    )
    scope = Scope(
        partition_refs=({"routing_id": "routing:1:" + "a" * 64, "key_values": []},),
        domain_refs=("pricing",),
        profile_refs=(),
    )
    for record in (request, scope):
        wire = record.model_dump(mode="json")
        assert type(record).model_validate(wire) == record
    wire["partition_refs"][0]["key_values"].append({"changed": True})
    assert scope.partition_refs[0]["key_values"] == ()
    with pytest.raises(TypeError):
        request.provided_values["x"]["value"] = "2"


def _id(kind: str) -> str:
    return f"{kind}:1:" + "a" * 64


def _scope(
    *, partition_refs: tuple[dict[str, object], ...] | None = None
) -> dict[str, object]:
    return {
        "partition_refs": partition_refs
        or ({"routing_id": _id("routing"), "key_values": []},),
        "domain_refs": ["pricing"],
        "profile_refs": [],
    }


def test_outcome_records_require_exact_state_specific_wire_values():
    """A decision cannot carry null/native values or omit its exact analysis state."""
    base = {
        "status": "decision",
        "reason": "established",
        "binding_id": _id("binding"),
        "contract_id": "pricing",
        "profile_id": "lookup",
        "values": {"amount.sum": {"type": "int", "value": "1"}},
        "cell_id": None,
        "contributor_ids": None,
        "may_have_no_match": False,
        "observations": None,
        "issues": [],
    }
    assert OutcomeRecord.model_validate(base).values == base["values"]

    for changed in (
        {"values": {"amount.sum": {"type": "int", "value": None}}},
        {"binding_id": None},
        {"may_have_no_match": 1},
        {
            "status": "invalid_context",
            "reason": "invalid_type",
            "values": None,
            "may_have_no_match": None,
        },
    ):
        with pytest.raises(ValueError):
            OutcomeRecord.model_validate({**base, **changed})


def test_scope_and_policy_reject_noncanonical_semantic_sets():
    """Partition keys and policy arrays cannot create alternate semantic hashes."""
    valid_scope = _scope()
    assert Scope.model_validate(valid_scope).partition_refs[0]["key_values"] == ()

    with pytest.raises(ValueError):
        Scope.model_validate(
            _scope(
                partition_refs=(
                    {
                        "routing_id": _id("routing"),
                        "key_values": [{"unexpected": True}],
                    },
                )
            )
        )
    with pytest.raises(ValueError):
        Scope.model_validate(
            _scope(
                partition_refs=(
                    {"routing_id": _id("routing"), "key_values": []},
                    {"routing_id": _id("routing"), "key_values": []},
                )
            )
        )

    policy = {
        "schema_version": 1,
        "policy_id": "standard",
        "required_checks": [
            {"stage": "source", "check_id": "z", "scope": valid_scope},
            {"stage": "source", "check_id": "a", "scope": valid_scope},
        ],
        "coverage_requirements": [],
        "diagnostic_rules": [],
    }
    with pytest.raises(ValueError):
        ValidationPolicy.model_validate(policy)

    diagnostics = {
        **policy,
        "required_checks": [],
        "diagnostic_rules": [
            {
                "stage": "source",
                "check_id": "z",
                "code": "z",
                "scope": valid_scope,
                "severity": "error",
                "witness_kind": "none",
                "max_witnesses": 0,
            },
            {
                "stage": "source",
                "check_id": "a",
                "code": "a",
                "scope": valid_scope,
                "severity": "error",
                "witness_kind": "none",
                "max_witnesses": 0,
            },
        ],
    }
    with pytest.raises(ValueError):
        ValidationPolicy.model_validate(diagnostics)


def test_evidence_models_reject_coercion_and_keep_hash_significant_state_immutable():
    """Wire Booleans and nested report metadata must not be coerced or mutable."""
    with pytest.raises(ValueError):
        ReportCheck.model_validate(
            {
                "check_id": "source_schema",
                "scope": _scope(),
                "status": "passed",
                "complete": 1,
                "finding_ids": [],
            }
        )

    report_payload = {
        "schema_version": 1,
        "analysis_input_id": _id("analysis-input"),
        "stage": "source",
        "artifact_id": None,
        "validator": {"validator_id": "rules", "semantic_version": "v1"},
        "scope": _scope(),
        "checks": [],
        "finding_ids": [],
    }
    report = ValidationReport.model_validate(
        {
            **report_payload,
            "id": content_id("report", report_payload),
        }
    )
    with pytest.raises(TypeError):
        report.validator["validator_id"] = "changed"


def test_finding_requires_labels_canonical_witnesses_and_its_own_content_id():
    """Mutable/fake evidence must not retain a valid finding identity."""
    payload = {
        "schema_version": 1,
        "analysis_input_id": _id("analysis-input"),
        "stage": "source",
        "check_id": "source_overlap",
        "code": "source_overlap",
        "severity": "warning",
        "scope": _scope(),
        "source_ids": [],
        "cell_ids": [],
        "region_predicate_id": None,
        "witnesses": [],
        "witnesses_complete": True,
    }
    record = Finding.model_validate(
        {
            **payload,
            "id": content_id("finding", payload),
        }
    )
    assert record.id == content_id("finding", payload)
    with pytest.raises(ValueError):
        Finding.model_validate({**payload, "id": _id("finding")})
    with pytest.raises(ValueError):
        Finding.model_validate(
            {
                **payload,
                "id": content_id("finding", {**payload, "check_id": ""}),
                "check_id": "",
            }
        )


def test_evidence_annotations_are_frozen_and_excluded_from_semantic_identity():
    """Diagnostic decimals survive immutably without changing a finding's digest."""
    payload = {
        "schema_version": 1,
        "analysis_input_id": _id("analysis-input"),
        "stage": "source",
        "check_id": "source_overlap",
        "code": "source_overlap",
        "severity": "warning",
        "scope": _scope(),
        "source_ids": [],
        "cell_ids": [],
        "region_predicate_id": None,
        "witnesses": [],
        "witnesses_complete": True,
    }
    record = Finding.model_validate(
        {
            "id": content_id("finding", payload),
            **payload,
            "annotations": {"precision": Decimal("0.50")},
        }
    )

    assert record.id == content_id("finding", payload)
    assert record.annotations["precision"] == Decimal("0.50")
    with pytest.raises(TypeError):
        record.annotations["precision"] = Decimal("1")
    with pytest.raises(ValueError, match="annotations"):
        Finding.model_validate(
            {
                "id": content_id("finding", payload),
                **payload,
                "annotations": None,
            }
        )


def test_source_origins_require_canonical_identity_and_json_annotations():
    """Duplicate origins and nonportable annotations cannot enter frozen evidence."""
    metadata = {
        "schema_version": 1,
        "dimensions": [],
        "regex_semantics": None,
        "normalization_semantics": "normalization-2",
    }
    aggregates = {"schema_version": 1, "declarations": []}
    routing = {
        "schema_version": 1,
        "semantics": "exact-key-1",
        "key_dimensions": [],
        "partition_keys": [[]],
    }
    predicate = {"schema_version": 1, "node": {"op": "true"}}
    origin = {
        "source_id": "87b551cf-b3b2-55d6-bdd2-f682dce7f709",
        "dimension_name": "region",
        "predicate_id": content_id("predicate", predicate),
        "authored_values": {},
    }
    source_bundle = {
        "schema_version": 1,
        "ruleset_id": "pricing",
        "sources": [
            {
                "source_id": origin["source_id"],
                "content_id": _id("source-content"),
            }
        ],
    }
    bundle = {
        "schema_version": 1,
        "metadata": {"id": content_id("metadata", metadata), "payload": metadata},
        "aggregates": {
            "id": content_id("aggregates", aggregates),
            "payload": aggregates,
        },
        "routing": {"id": content_id("routing", routing), "payload": routing},
        "context_contracts": [],
        "predicates": {
            "schema_version": 1,
            "predicates": [
                {"id": content_id("predicate", predicate), "payload": predicate}
            ],
            "languages": [],
            "domains": [],
            "source_bundles": [
                {
                    "id": content_id("source-bundle", source_bundle),
                    "payload": source_bundle,
                }
            ],
            "source_origins": [origin],
            "source_labels": {},
        },
        "validation": {
            "schema_version": 1,
            "analysis_inputs": [],
            "findings": [],
            "reports": [],
            "approvals": [],
            "bindings": [],
        },
    }
    assert (
        ValidationBundle.model_validate(bundle).predicates["source_origins"][0][
            "dimension_name"
        ]
        == "region"
    )
    with pytest.raises(ValueError):
        ValidationBundle.model_validate(
            {
                **bundle,
                "predicates": {
                    **bundle["predicates"],
                    "source_origins": [origin, origin],
                },
            }
        )
    with pytest.raises(ValueError):
        ValidationBundle.model_validate(
            {
                **bundle,
                "predicates": {
                    **bundle["predicates"],
                    "source_origins": [{**origin, "annotations": {"bad": object()}}],
                },
            }
        )
    with pytest.raises(ValueError, match="annotations"):
        ValidationBundle.model_validate(
            {
                **bundle,
                "predicates": {
                    **bundle["predicates"],
                    "source_origins": [{**origin, "annotations": None}],
                },
            }
        )


_FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures"


def _fixture_payload(name: str) -> dict[str, object]:
    return json.loads((_FIXTURE_DIRECTORY / name).read_text())


def _rehash_record(kind: str, record: dict[str, object]) -> dict[str, object]:
    payload = {key: value for key, value in record.items() if key != "id"}
    record["id"] = content_id(
        kind, {key: value for key, value in payload.items() if key != "annotations"}
    )
    return record


def _rehash_report_and_approvals(payload: dict[str, object]) -> None:
    validation = payload["validation"]
    assert isinstance(validation, dict)
    report = validation["reports"][0]
    assert isinstance(report, dict)
    old_report_id = report["id"]
    _rehash_record("report", report)
    for approval in validation["approvals"]:
        assert isinstance(approval, dict)
        if approval["report_id"] == old_report_id:
            approval["report_id"] = report["id"]
            _rehash_record("approval", approval)


def _relink_reviewed_finding(
    payload: dict[str, object], finding: dict[str, object]
) -> None:
    validation = payload["validation"]
    assert isinstance(validation, dict)
    old_finding_id = finding["id"]
    _rehash_record("finding", finding)
    report = validation["reports"][0]
    assert isinstance(report, dict)
    report["finding_ids"] = [
        finding["id"] if identifier == old_finding_id else identifier
        for identifier in report["finding_ids"]
    ]
    for check in report["checks"]:
        assert isinstance(check, dict)
        check["finding_ids"] = [
            finding["id"] if identifier == old_finding_id else identifier
            for identifier in check["finding_ids"]
        ]
    for approval in validation["approvals"]:
        assert isinstance(approval, dict)
        approval["warning_ids"] = [
            finding["id"] if identifier == old_finding_id else identifier
            for identifier in approval["warning_ids"]
        ]
        _rehash_record("approval", approval)
    _rehash_report_and_approvals(payload)


def _transport_limits() -> ExactLimits:
    from tests.accumulator.source_analysis_fixtures import limits

    return limits()


def test_retained_source_bundles_roundtrip_exactly():
    """Clean, reviewed, and incomplete historical evidence stay transport-inspectable."""
    limits = _transport_limits()
    for name in (
        "exact_source_clean.json",
        "exact_source_reviewed.json",
        "exact_source_error.json",
    ):
        encoded = (_FIXTURE_DIRECTORY / name).read_bytes()
        decoded = decode_validation_bundle(encoded, limits=limits)
        assert encode_validation_bundle(decoded, limits=limits) == encoded


def test_transport_rejects_unresolved_analysis_source_bundle_and_source_membership():
    """Evidence source UUIDs and their owning source bundle are internal closure."""
    missing_bundle = _fixture_payload("exact_source_clean.json")
    validation = missing_bundle["validation"]
    assert isinstance(validation, dict)
    analysis = validation["analysis_inputs"][0]
    assert isinstance(analysis, dict)
    old_analysis_id = analysis["id"]
    analysis["source_bundle_digest"] = _id("source-bundle")
    _rehash_record("analysis-input", analysis)
    report = validation["reports"][0]
    assert isinstance(report, dict)
    report["analysis_input_id"] = analysis["id"]
    _rehash_report_and_approvals(missing_bundle)
    with pytest.raises(ValueError):
        decode_validation_bundle(
            json.dumps(missing_bundle, separators=(",", ":")).encode(),
            limits=_transport_limits(),
        )
    assert old_analysis_id != analysis["id"]

    for container, member in (("source_origins", "source_id"), ("source_labels", None)):
        foreign_member = _fixture_payload("exact_source_clean.json")
        predicates = foreign_member["predicates"]
        assert isinstance(predicates, dict)
        foreign_source_id = "00000000-0000-0000-0000-000000000099"
        if member is None:
            predicates[container] = {foreign_source_id: "foreign"}
        else:
            origin = predicates[container][0]
            assert isinstance(origin, dict)
            origin[member] = foreign_source_id
        with pytest.raises(ValueError):
            decode_validation_bundle(
                json.dumps(foreign_member, separators=(",", ":")).encode(),
                limits=_transport_limits(),
            )


def test_transport_rejects_foreign_evidence_scope_predicate_and_check_links():
    """Rehashed evidence must remain resolved against its own analysis material."""
    for scope_field, value in (
        ("domain_refs", ["foreign"]),
        (
            "partition_refs",
            [{"routing_id": _id("routing"), "key_values": []}],
        ),
    ):
        foreign_scope = _fixture_payload("exact_source_clean.json")
        validation = foreign_scope["validation"]
        assert isinstance(validation, dict)
        report = validation["reports"][0]
        assert isinstance(report, dict)
        scope = report["scope"]
        assert isinstance(scope, dict)
        scope[scope_field] = value
        _rehash_report_and_approvals(foreign_scope)
        with pytest.raises(ValueError):
            decode_validation_bundle(
                json.dumps(foreign_scope, separators=(",", ":")).encode(),
                limits=_transport_limits(),
            )

    for field, value in (
        ("region_predicate_id", _id("predicate")),
        ("check_id", "source_predicates"),
    ):
        foreign_link = _fixture_payload("exact_source_reviewed.json")
        validation = foreign_link["validation"]
        assert isinstance(validation, dict)
        finding = validation["findings"][0]
        assert isinstance(finding, dict)
        finding[field] = value
        _relink_reviewed_finding(foreign_link, finding)
        with pytest.raises(ValueError):
            decode_validation_bundle(
                json.dumps(foreign_link, separators=(",", ":")).encode(),
                limits=_transport_limits(),
            )


def test_approval_attachment_rejects_underscoped_decisions_and_reuses_bundle_body():
    """Approval scope is admitted immediately, while unchanged evidence stays shared."""
    from mountainash_rules.engines.accumulator.analysis import analyze_sources
    from tests.accumulator.source_analysis_fixtures import (
        approve,
        limits as source_limits,
        partition_case,
        row,
    )

    kwargs = partition_case(profile_coverage=False)
    bundle = analyze_sources(
        [{**row(1), "key": 1}, {**row(2, 0, 10), "key": -999999999}],
        **kwargs,
    )
    warning = next(
        finding
        for finding in bundle.validation["findings"]
        if finding.code == "coverage_gap"
    )
    report_scope = bundle.validation["reports"][0].scope.model_dump(mode="json")
    concrete_partition = next(
        partition
        for partition in report_scope["partition_refs"]
        if partition["key_values"][0]["match"]["kind"] == "value"
    )
    under_scoped = approve(
        bundle,
        [warning.id],
        scope=Scope(
            partition_refs=[concrete_partition],
            domain_refs=["D"],
            profile_refs=[],
        ),
    )
    assert isinstance(under_scoped, WarningApproval)
    raw = json.loads(encode_validation_bundle(bundle, limits=source_limits()))
    raw["validation"]["approvals"] = [under_scoped.model_dump(mode="json")]
    with pytest.raises(ValueError):
        decode_validation_bundle(
            json.dumps(raw, separators=(",", ":")).encode(), limits=source_limits()
        )
    with pytest.raises(ValueError):
        attach_warning_approvals(bundle, [under_scoped], limits=source_limits())

    decision = approve(bundle, [warning.id])
    attached = attach_warning_approvals(bundle, [decision], limits=source_limits())
    assert attached.metadata is bundle.metadata
    assert attached.aggregates is bundle.aggregates
    assert attached.routing is bundle.routing
    assert attached.context_contracts is bundle.context_contracts
    assert attached.predicates is bundle.predicates
    assert bundle.validation["approvals"] == ()
    assert (
        attach_warning_approvals(attached, [decision], limits=source_limits())
        == attached
    )


def test_approval_default_partition_does_not_cover_concrete_partition_warning():
    """Exact evidence scope IDs do not inherit routing wildcard geometry."""
    from mountainash_rules import analyze_sources
    from tests.accumulator.source_analysis_fixtures import (
        approve,
        limits as source_limits,
        partition_case,
        row,
    )

    kwargs = partition_case(profile_coverage=False)
    bundle = analyze_sources(
        [{**row(1, 0, 10), "key": 1}, {**row(2), "key": -999999999}],
        **kwargs,
    )
    warning = next(
        finding
        for finding in bundle.validation["findings"]
        if finding.code == "coverage_gap"
        and finding.scope.partition_refs[0]["key_values"][0]["match"]["kind"] == "value"
    )
    report_scope = bundle.validation["reports"][0].scope.model_dump(mode="json")
    default_partition = next(
        partition
        for partition in report_scope["partition_refs"]
        if partition["key_values"][0]["match"]["kind"] == "wildcard"
    )
    default_only = approve(
        bundle,
        [warning.id],
        scope=Scope(
            partition_refs=[default_partition],
            domain_refs=["D"],
            profile_refs=[],
        ),
    )
    with pytest.raises(ValueError):
        attach_warning_approvals(bundle, [default_only], limits=source_limits())


def test_transport_requires_report_scope_to_cover_every_check_scope():
    """A report cannot claim a smaller scope than a retained check."""
    payload = _fixture_payload("exact_source_error.json")
    validation = payload["validation"]
    assert isinstance(validation, dict)
    report = validation["reports"][0]
    assert isinstance(report, dict)
    report["scope"]["profile_refs"] = []
    _rehash_report_and_approvals(payload)

    with pytest.raises(ValueError):
        decode_validation_bundle(
            json.dumps(payload, separators=(",", ":")).encode(),
            limits=_transport_limits(),
        )


def test_transport_rejects_duplicate_logical_domain_before_registry_collapse():
    """Two domain envelopes cannot silently select one logical domain definition."""
    payload = _fixture_payload("exact_source_clean.json")
    predicates = payload["predicates"]
    assert isinstance(predicates, dict)
    domains = predicates["domains"]
    assert isinstance(domains, list)
    duplicate = json.loads(json.dumps(domains[0]))
    assert isinstance(duplicate, dict)
    domain_payload = duplicate["payload"]
    assert isinstance(domain_payload, dict)
    original_predicate_id = domain_payload["predicate_id"]
    predicate_id = next(
        entry["id"]
        for entry in predicates["predicates"]
        if entry["id"] != original_predicate_id
    )
    domain_payload["predicate_id"] = predicate_id
    duplicate["id"] = content_id("domain", domain_payload)
    domains.append(duplicate)

    validation = payload["validation"]
    assert isinstance(validation, dict)
    analysis = validation["analysis_inputs"][0]
    assert isinstance(analysis, dict)
    old_analysis_id = analysis["id"]
    analysis["domain_digests"]["D"] = duplicate["id"]
    _rehash_record("analysis-input", analysis)
    report = validation["reports"][0]
    assert isinstance(report, dict)
    report["analysis_input_id"] = analysis["id"]
    _rehash_report_and_approvals(payload)
    assert old_analysis_id != analysis["id"]

    with pytest.raises(ValueError):
        decode_validation_bundle(
            json.dumps(payload, separators=(",", ":")).encode(),
            limits=_transport_limits(),
        )


def test_transport_resolves_witness_profiles_in_owning_analysis_contracts():
    """A globally retained contract cannot authorize another analysis's witness."""
    payload = _fixture_payload("exact_source_error.json")
    foreign_contract = json.loads(json.dumps(payload["context_contracts"][0]))
    assert isinstance(foreign_contract, dict)
    foreign_payload = foreign_contract["payload"]
    assert isinstance(foreign_payload, dict)
    contract = foreign_payload["contract"]
    assert isinstance(contract, dict)
    contract["contract_id"] = "foreign-client"
    foreign_contract["id"] = content_id("contract", foreign_payload)
    payload["context_contracts"].append(foreign_contract)
    control = decode_validation_bundle(
        json.dumps(payload, separators=(",", ":")).encode(), limits=_transport_limits()
    )
    assert (
        control.validation["findings"][0].witnesses[0].profile_ref["contract_id"]
        == "client"
    )

    validation = payload["validation"]
    assert isinstance(validation, dict)
    finding = validation["findings"][0]
    assert isinstance(finding, dict)
    foreign_profile = {"contract_id": "foreign-client", "profile_id": "quote"}
    finding["witnesses"][0]["profile_ref"] = foreign_profile
    _relink_reviewed_finding(payload, finding)

    with pytest.raises(ValueError):
        decode_validation_bundle(
            json.dumps(payload, separators=(",", ":")).encode(),
            limits=_transport_limits(),
        )


def test_transport_rejects_binding_owned_by_another_analysis_contract_domain():
    """A binding must use the selected analysis's contract and matching domain."""
    payload = _fixture_payload("exact_source_error.json")
    predicates = payload["predicates"]
    assert isinstance(predicates, dict)
    foreign_domain = json.loads(json.dumps(predicates["domains"][0]))
    assert isinstance(foreign_domain, dict)
    foreign_domain_payload = foreign_domain["payload"]
    assert isinstance(foreign_domain_payload, dict)
    foreign_domain_payload["domain_id"] = "foreign-domain"
    foreign_domain["id"] = content_id("domain", foreign_domain_payload)
    predicates["domains"].append(foreign_domain)

    foreign_contract = json.loads(json.dumps(payload["context_contracts"][0]))
    assert isinstance(foreign_contract, dict)
    foreign_contract_payload = foreign_contract["payload"]
    assert isinstance(foreign_contract_payload, dict)
    foreign_contract_definition = foreign_contract_payload["contract"]
    assert isinstance(foreign_contract_definition, dict)
    foreign_contract_definition["contract_id"] = "foreign-client"
    foreign_contract_definition["domain_ref"] = "foreign-domain"
    foreign_contract["id"] = content_id("contract", foreign_contract_payload)
    payload["context_contracts"].append(foreign_contract)

    validation = payload["validation"]
    assert isinstance(validation, dict)
    analysis = validation["analysis_inputs"][0]
    assert isinstance(analysis, dict)
    source_report = validation["reports"][0]
    assert isinstance(source_report, dict)
    compiled_checks = [
        {
            "check_id": check_id,
            "scope": source_report["scope"],
            "status": "resource_exhausted",
            "complete": False,
            "finding_ids": [],
        }
        for check_id in (
            "cell_disjointness",
            "cell_nonempty",
            "output_folds",
            "profile_consistency",
            "source_membership",
            "source_union",
        )
    ]
    compiled_payload = {
        "schema_version": 1,
        "analysis_input_id": analysis["id"],
        "stage": "compiled",
        "artifact_id": _id("artifact"),
        "validator": source_report["validator"],
        "scope": source_report["scope"],
        "checks": compiled_checks,
        "finding_ids": [],
    }
    compiled = {"id": content_id("report", compiled_payload), **compiled_payload}
    validation["reports"].append(compiled)

    source_contract = payload["context_contracts"][0]
    assert isinstance(source_contract, dict)
    source_contract_payload = source_contract["payload"]
    assert isinstance(source_contract_payload, dict)
    source_contract_definition = source_contract_payload["contract"]
    assert isinstance(source_contract_definition, dict)
    profile = source_contract_definition["profiles"][0]
    binding_payload = {
        "schema_version": 1,
        "artifact_id": compiled["artifact_id"],
        "analysis_input_id": analysis["id"],
        "contract_id": source_contract_definition["contract_id"],
        "contract_digest": source_contract["id"],
        "domain_ref": source_contract_definition["domain_ref"],
        "domain_digest": predicates["domains"][0]["id"],
        "authorized_profiles": [
            {
                "profile_id": profile["profile_id"],
                "profile_digest": content_id(
                    "profile",
                    {
                        "schema_version": 1,
                        "contract_id": source_contract_definition["contract_id"],
                        "profile": profile,
                    },
                ),
            }
        ],
        "source_report_id": source_report["id"],
        "compiled_report_id": compiled["id"],
        "approval_ids": [],
        "semantic_versions": {
            "content": analysis["semantic_versions"],
            "binding": "binding-1",
            "checker": "binding-checker-1",
        },
    }
    binding = {"id": content_id("binding", binding_payload), **binding_payload}
    validation["bindings"] = [binding]
    predicates["domains"].sort(key=lambda entry: entry["id"])
    validation["reports"].sort(key=lambda entry: entry["id"])
    control = decode_validation_bundle(
        json.dumps(payload, separators=(",", ":")).encode(),
        limits=_transport_limits(),
    )
    assert control.validation["bindings"][0].contract_id == "client"

    foreign_profile = foreign_contract_definition["profiles"][0]
    binding["contract_id"] = "foreign-client"
    binding["contract_digest"] = foreign_contract["id"]
    binding["domain_ref"] = "foreign-domain"
    binding["domain_digest"] = foreign_domain["id"]
    binding["authorized_profiles"] = [
        {
            "profile_id": foreign_profile["profile_id"],
            "profile_digest": content_id(
                "profile",
                {
                    "schema_version": 1,
                    "contract_id": "foreign-client",
                    "profile": foreign_profile,
                },
            ),
        }
    ]
    _rehash_record("binding", binding)

    with pytest.raises(ValueError):
        decode_validation_bundle(
            json.dumps(payload, separators=(",", ":")).encode(),
            limits=_transport_limits(),
        )
