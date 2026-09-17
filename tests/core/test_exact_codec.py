"""Tests for canonical exact-accumulator codec and strict evidence containers."""

import mountainash_rules.core.codec as codec
import pytest

from mountainash_rules.core.codec import (
    canonical_bytes,
    content_id,
    decode_json,
    validate_envelope,
    validate_id,
)
from mountainash_rules.core.contracts import (
    ExactLimits,
    ExactResourceError,
    OperationBudget,
    ValidationBundle,
)
from mountainash_rules.core.language import StringLanguage


def test_canonical_bytes_and_content_id_match_uuid_native_vector():
    """Changing key order, whitespace, or the domain separator must change identity."""
    payload = {
        "schema_version": 1,
        "source_ids": [
            "87b551cf-b3b2-55d6-bdd2-f682dce7f709",
            "ab189c70-349d-593e-9b9a-2e0ca1cc3aa5",
        ],
    }
    expected = (
        b'{"schema_version":1,"source_ids":['
        b'"87b551cf-b3b2-55d6-bdd2-f682dce7f709",'
        b'"ab189c70-349d-593e-9b9a-2e0ca1cc3aa5"]}'
    )
    assert canonical_bytes(payload) == expected
    assert content_id("contributor-set", payload) == (
        "contributor-set:1:6e3f91efb0306a84d3d558941cb0c6f3c5b7e4c7c5f5287ba253773deed09ea8"
    )


def test_json_decoder_rejects_duplicate_keys_and_nonfinite_numbers():
    """Permitting ambiguous or non-JSON values would make hashes non-reproducible."""
    with pytest.raises(ValueError, match="Duplicate"):
        decode_json(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError):
        decode_json(b'{"value":NaN}')


def test_typed_ids_require_exact_kind_version_and_digest():
    """Accepting a raw digest or cross-kind ID would allow unresolved references."""
    payload = {
        "schema_version": 1,
        "source_ids": ["87b551cf-b3b2-55d6-bdd2-f682dce7f709"],
    }
    identifier = content_id("contributor-set", payload)
    assert validate_id(identifier, "contributor-set") == identifier
    for invalid in (
        identifier.replace("contributor-set", "cell", 1),
        identifier.upper(),
        "a" * 64,
    ):
        with pytest.raises(ValueError):
            validate_id(invalid, "contributor-set")


def test_envelope_rejects_unknown_payload_fields_and_wrong_digest():
    """A permissive envelope could turn unrecognized semantics into an accepted ID."""
    payload = {
        "schema_version": 1,
        "source_ids": ["87b551cf-b3b2-55d6-bdd2-f682dce7f709"],
    }
    envelope = {"id": content_id("contributor-set", payload), "payload": payload}
    assert validate_envelope(envelope, "contributor-set") == envelope

    with pytest.raises(ValueError, match="Unknown"):
        validate_envelope(
            {
                "id": envelope["id"],
                "payload": {**payload, "ignored": True},
            },
            "contributor-set",
        )
    with pytest.raises(ValueError, match="digest"):
        validate_envelope(
            {
                "id": envelope["id"],
                "payload": {
                    "schema_version": 1,
                    "source_ids": ["ab189c70-349d-593e-9b9a-2e0ca1cc3aa5"],
                },
            },
            "contributor-set",
        )


def test_validation_bundle_rejects_unknown_duplicate_and_cross_stage_evidence():
    """A selected binding must not accept duplicate records or compiled warning evidence."""
    bundle = {
        "schema_version": 1,
        "metadata": {
            "id": "metadata:1:" + "a" * 64,
            "payload": {
                "schema_version": 1,
                "dimensions": [],
                "regex_semantics": None,
                "normalization_semantics": "normalization-2",
            },
        },
        "aggregates": {
            "id": "aggregates:1:" + "b" * 64,
            "payload": {
                "schema_version": 1,
                "declarations": [],
            },
        },
        "routing": {
            "id": "routing:1:" + "c" * 64,
            "payload": {
                "schema_version": 1,
                "semantics": "exact-key-1",
                "key_dimensions": [],
                "partition_keys": [[]],
            },
        },
        "context_contracts": [],
        "predicates": {
            "schema_version": 1,
            "predicates": [],
            "languages": [],
            "domains": [],
            "source_bundles": [],
            "source_origins": [],
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
    with pytest.raises(ValueError, match="digest"):
        ValidationBundle.model_validate(bundle)

    bundle["metadata"]["id"] = content_id("metadata", bundle["metadata"]["payload"])
    bundle["aggregates"]["id"] = content_id(
        "aggregates", bundle["aggregates"]["payload"]
    )
    bundle["routing"]["id"] = content_id("routing", bundle["routing"]["payload"])
    accepted = ValidationBundle.model_validate(bundle)
    assert accepted.validation["reports"] == ()

    unknown = {**bundle, "extra": None}
    with pytest.raises(ValueError, match="Extra|Unknown"):
        ValidationBundle.model_validate(unknown)

    duplicate = {**bundle, "validation": {**bundle["validation"]}}
    finding = {
        "schema_version": 1,
        "analysis_input_id": "analysis-input:1:" + "a" * 64,
        "stage": "compiled",
        "check_id": "cell_nonempty",
        "code": "invalid_source",
        "severity": "warning",
        "scope": {
            "partition_refs": [
                {"routing_id": "routing:1:" + "a" * 64, "key_values": []}
            ],
            "domain_refs": ["pricing"],
            "profile_refs": [],
        },
        "source_ids": [],
        "cell_ids": [],
        "region_predicate_id": None,
        "witnesses": [],
        "witnesses_complete": True,
    }
    duplicate["validation"]["findings"] = [
        {
            **finding,
            "id": content_id("finding", finding),
        }
    ]
    with pytest.raises(ValueError, match="compiled"):
        ValidationBundle.model_validate(duplicate)


def test_metadata_rejects_nonapplicable_range_flags_instead_of_hashing_them():
    """A non-range dimension cannot carry hidden Boolean range semantics."""
    payload = {
        "schema_version": 1,
        "dimensions": [
            {
                "dimension_name": "region",
                "context_field": "region",
                "rule_field": "region",
                "match_strategy": "exact",
                "data_type": "str",
                "role": "constraint",
                "range_min_field": None,
                "range_max_field": None,
                "range_min_inclusive": True,
                "range_max_inclusive": None,
                "regex_pattern": None,
            }
        ],
        "regex_semantics": None,
        "normalization_semantics": "normalization-2",
    }
    with pytest.raises(ValueError, match="non-range"):
        validate_envelope(
            {"id": content_id("metadata", payload), "payload": payload}, "metadata"
        )


def test_metadata_rejects_strategy_data_type_pairs_rejected_by_dimension():
    """Metadata must not authenticate strategies that Dimension cannot compile."""
    base_dimension = {
        "dimension_name": "value",
        "context_field": "value",
        "rule_field": "value",
        "match_strategy": "exact",
        "data_type": "str",
        "role": "constraint",
        "range_min_field": None,
        "range_max_field": None,
        "range_min_inclusive": None,
        "range_max_inclusive": None,
        "regex_pattern": None,
    }
    for strategy, data_type, range_fields in (
        ("prefix", "int", {}),
        (
            "range",
            "str",
            {
                "range_min_field": "minimum",
                "range_max_field": "maximum",
                "range_min_inclusive": True,
                "range_max_inclusive": True,
            },
        ),
    ):
        payload = {
            "schema_version": 1,
            "dimensions": [
                {
                    **base_dimension,
                    "match_strategy": strategy,
                    "data_type": data_type,
                    **range_fields,
                }
            ],
            "regex_semantics": None,
            "normalization_semantics": "normalization-2",
        }
        with pytest.raises(ValueError):
            validate_envelope(_envelope("metadata", payload), "metadata")


def test_aggregate_transport_allows_reserved_scalars_but_predicates_reject_them():
    """Aggregate carriers preserve reserved spellings without widening predicate facts."""
    reserved = {"type": "int", "value": "-999999999"}
    source_content = {
        "schema_version": 1,
        "ruleset_id": "pricing",
        "source_id": "87b551cf-b3b2-55d6-bdd2-f682dce7f709",
        "predicate_id": _id("predicate"),
        "routing_values": [],
        "contributions": {"amount": reserved},
    }
    artifact = {
        "schema_version": 1,
        "ruleset_id": "pricing",
        "partition_identity": {"routing_id": _id("routing"), "key_values": []},
        "metadata_id": _id("metadata"),
        "aggregates_id": _id("aggregates"),
        "compilation_domain_id": _id("domain"),
        "source_bundle_id": _id("source-bundle"),
        "cells": [{"cell_id": _id("cell"), "outputs": {"amount": reserved}}],
        "semantic_versions": {
            "scalar": "scalar-1",
            "predicate": "predicate-1",
            "language": "language-1",
            "numeric": "numeric-1",
            "canonical": "canonical-json-1",
            "normalization": "normalization-2",
        },
    }

    assert (
        validate_envelope(
            _envelope("source-content", source_content), "source-content"
        )["payload"]
        == source_content
    )
    assert (
        validate_envelope(_envelope("artifact", artifact), "artifact")["payload"]
        == artifact
    )

    predicate = {
        "schema_version": 1,
        "node": {"op": "eq", "field": "amount", "value": reserved},
    }
    with pytest.raises(ValueError, match="Invalid scalar"):
        validate_envelope(_envelope("predicate", predicate), "predicate")


def test_frozen_predicate_payload_has_the_same_canonical_identity():
    from types import MappingProxyType

    node = {"op": "eq", "field": "flag", "value": {"type": "bool", "value": False}}
    frozen = MappingProxyType(
        {
            "schema_version": 1,
            "node": MappingProxyType(
                {**node, "value": MappingProxyType(node["value"])}
            ),
        }
    )
    assert content_id("predicate", frozen) == content_id(
        "predicate", {"schema_version": 1, "node": node}
    )


EXACT_LIMITS = {
    "language": {
        "max_input_bytes": 10_000,
        "max_nesting": 16,
        "max_nfa_states": 100,
        "max_states": 100,
        "max_transitions": 1_000,
        "max_work": 10_000,
    },
    "max_input_bytes": 100_000,
    "max_output_bytes": 100_000,
    "max_work": 100_000,
    "max_live_bytes": 500_000,
    "max_predicate_nodes": 100_000,
    "max_dfa_states": 100_000,
    "max_dfa_transitions": 100_000,
    "max_theory_states": 100_000,
    "max_regions": 100_000,
    "max_scopes": 100_000,
    "max_source_scope_edges": 100_000,
    "max_contributor_edges": 100_000,
    "max_word_rows": 100_000,
    "max_numeric_bits": 100_000,
    "max_witnesses": 100_000,
}


def _budget(**overrides: object) -> OperationBudget:
    return OperationBudget(
        ExactLimits.model_validate({**EXACT_LIMITS, **overrides}),
        "decode",
    )


LANGUAGE_LIMITS = ExactLimits.model_validate(EXACT_LIMITS).language


def _id(kind: str) -> str:
    return f"{kind}:1:" + "a" * 64


def _envelope(kind: str, payload: dict[str, object]) -> dict[str, object]:
    return {"id": content_id(kind, payload), "payload": payload}


def _scope() -> dict[str, object]:
    return {
        "partition_refs": [{"routing_id": _id("routing"), "key_values": []}],
        "domain_refs": ["pricing"],
        "profile_refs": [],
    }


def test_predicate_codec_rejects_noncanonical_boolean_and_membership_forms():
    """Syntactic normalization must not grant alternate IDs to the same predicate."""
    predicate = _id("predicate")
    for node in (
        {"op": "and", "args": [predicate]},
        {"op": "or", "args": [predicate, predicate]},
        {"op": "in", "field": "x", "values": [{"type": "int", "value": "1"}]},
        {
            "op": "in",
            "field": "x",
            "values": [
                {"type": "int", "value": "1"},
                {"type": "int", "value": "1"},
            ],
        },
    ):
        payload = {"schema_version": 1, "node": node}
        with pytest.raises(ValueError):
            validate_envelope(_envelope("predicate", payload), "predicate")


def test_language_envelopes_are_natively_checked_under_explicit_limits():
    """A digest cannot authenticate an arbitrary object as a language graph."""
    payload = decode_json(
        StringLanguage.empty(limits=LANGUAGE_LIMITS).to_json(limits=LANGUAGE_LIMITS)
    )
    envelope = _envelope("language", payload)
    assert validate_envelope(envelope, "language", budget=_budget()) == envelope

    malformed = {**payload, "alphabet": "not-unicode-scalars"}
    with pytest.raises(ValueError):
        validate_envelope(
            _envelope("language", malformed),
            "language",
            budget=_budget(),
        )
    with pytest.raises(ValueError):
        validate_envelope(envelope, "language")


def test_language_input_budget_precedes_canonical_buffer_materialization(
    monkeypatch: pytest.MonkeyPatch,
):
    """Language admission must reject exhausted input budgets before encoding."""
    payload = decode_json(
        StringLanguage.empty(limits=LANGUAGE_LIMITS).to_json(limits=LANGUAGE_LIMITS)
    )

    def fail_if_encoded(value: object) -> str:
        raise AssertionError(
            f"canonical buffer materialized for {type(value).__name__}"
        )

    monkeypatch.setattr(codec, "_canonical", fail_if_encoded)
    with pytest.raises(ExactResourceError) as error:
        validate_envelope(
            {"id": _id("language"), "payload": payload},
            "language",
            budget=_budget(max_input_bytes=0),
        )
    assert error.value.counter == "max_input_bytes"


def test_language_workspace_rejection_releases_live_storage():
    """Native workspace admission leaves the caller's live ledger reusable."""
    payload = decode_json(
        StringLanguage.empty(limits=LANGUAGE_LIMITS).to_json(limits=LANGUAGE_LIMITS)
    )
    budget = _budget(max_live_bytes=10_000)

    with pytest.raises(ExactResourceError) as error:
        validate_envelope(
            _envelope("language", payload),
            "language",
            budget=budget,
        )
    assert error.value.counter == "max_live_bytes"
    budget.reserve(
        "max_live_bytes",
        10_000,
        phase="post_decode",
        units="bytes",
    )


def test_language_native_resource_errors_preserve_the_native_limit():
    """Native parser exhaustion must remain a typed, correctly attributed error."""
    payload = decode_json(
        StringLanguage.empty(limits=LANGUAGE_LIMITS).to_json(limits=LANGUAGE_LIMITS)
    )
    with pytest.raises(ExactResourceError) as error:
        validate_envelope(
            _envelope("language", payload),
            "language",
            budget=_budget(
                language={**EXACT_LIMITS["language"], "max_input_bytes": 0},
            ),
        )
    assert error.value.counter == "max_input_bytes"
    assert error.value.limit == 0


def test_every_envelope_kind_dispatches_to_a_closed_schema():
    """No ID kind may accept a hashed object merely because it carries version one."""
    scope = _scope()
    policy = {
        "schema_version": 1,
        "policy_id": "standard",
        "required_checks": [],
        "coverage_requirements": [],
        "diagnostic_rules": [],
    }
    contract = {
        "schema_version": 1,
        "contract_id": "pricing",
        "fields": [],
        "domain_ref": "pricing",
        "profiles": [
            {
                "profile_id": "candidates",
                "mode": "candidates",
                "output_fields": [],
                "provenance": "none",
                "dimensions": [],
                "allow_dont_care": [],
                "promise": "candidate_only",
            }
        ],
    }
    profile = contract["profiles"][0]
    semantic_versions = {
        "scalar": "scalar-1",
        "predicate": "predicate-1",
        "language": "language-1",
        "numeric": "numeric-1",
        "canonical": "canonical-json-1",
        "normalization": "normalization-2",
    }
    payloads = {
        "contract": {"schema_version": 1, "contract": contract},
        "profile": {"schema_version": 1, "contract_id": "pricing", "profile": profile},
        "analysis-input": {
            "schema_version": 1,
            "ruleset_id": "pricing",
            "source_id_field": "source_id",
            "source_bundle_digest": _id("source-bundle"),
            "compilation_domain_ref": "pricing",
            "domain_digests": {"pricing": _id("domain")},
            "metadata_digest": _id("metadata"),
            "aggregate_digest": _id("aggregates"),
            "routing_digest": _id("routing"),
            "contracts": [
                {"contract_id": "pricing", "contract_digest": _id("contract")}
            ],
            "validation_policy": policy,
            "semantic_versions": semantic_versions,
        },
        "finding": {
            "schema_version": 1,
            "analysis_input_id": _id("analysis-input"),
            "stage": "source",
            "check_id": "source_schema",
            "code": "invalid_source",
            "severity": "error",
            "scope": scope,
            "source_ids": [],
            "cell_ids": [],
            "region_predicate_id": None,
            "witnesses": [],
            "witnesses_complete": True,
        },
        "report": {
            "schema_version": 1,
            "analysis_input_id": _id("analysis-input"),
            "stage": "source",
            "artifact_id": None,
            "validator": {"validator_id": "rules", "semantic_version": "v1"},
            "scope": scope,
            "checks": [],
            "finding_ids": [],
        },
        "approval": {
            "schema_version": 1,
            "analysis_input_id": _id("analysis-input"),
            "report_id": _id("report"),
            "authority_ref": "owner",
            "actor_ref": "operator",
            "decision": "approve_warnings",
            "scope": scope,
            "warning_ids": [_id("finding")],
        },
        "binding": {
            "schema_version": 1,
            "artifact_id": _id("artifact"),
            "analysis_input_id": _id("analysis-input"),
            "contract_id": "pricing",
            "contract_digest": _id("contract"),
            "domain_ref": "pricing",
            "domain_digest": _id("domain"),
            "authorized_profiles": [
                {
                    "profile_id": "candidates",
                    "profile_digest": _id("profile"),
                }
            ],
            "source_report_id": _id("report"),
            "compiled_report_id": _id("report"),
            "approval_ids": [],
            "semantic_versions": {
                "content": semantic_versions,
                "binding": "binding-1",
                "checker": "binding-checker-1",
            },
        },
        "scope": {
            "schema_version": 1,
            "partition_identity": {"routing_id": _id("routing"), "key_values": []},
            "predicate_id": _id("predicate"),
            "keys": [],
        },
        "source-map": {
            "schema_version": 1,
            "scope_id": _id("scope"),
            "allocations": [],
        },
        "vector": {
            "schema_version": 1,
            "scope_id": _id("scope"),
            "map_id": _id("source-map"),
            "cell_id": _id("cell"),
            "predicate_id": _id("predicate"),
            "contributor_set_id": _id("contributor-set"),
        },
    }
    for kind, payload in payloads.items():
        envelope = _envelope(kind, payload)
        assert validate_envelope(envelope, kind) == envelope
        malformed = {
            key: value for key, value in payload.items() if key != "schema_version"
        }
        with pytest.raises(ValueError):
            validate_envelope(_envelope(kind, malformed), kind)


def test_canonical_arrays_do_not_depend_on_python_recursion_depth():
    nested = 0
    for _ in range(1100):
        nested = [nested]
    encoded = b'{"x":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"
    assert canonical_bytes({"x": nested}) == encoded
    assert canonical_bytes(decode_json(encoded)) == encoded
    with pytest.raises(ValueError, match="Malformed"):
        decode_json(b"[" * 1100 + b"0," + b"]" * 1100)
