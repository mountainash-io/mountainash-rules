"""Tests for strict exact-accumulator contract declarations."""

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
)


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
    origin = {
        "source_id": "87b551cf-b3b2-55d6-bdd2-f682dce7f709",
        "dimension_name": "region",
        "predicate_id": _id("predicate"),
        "authored_values": {},
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
            "predicates": [],
            "languages": [],
            "domains": [],
            "source_bundles": [],
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
