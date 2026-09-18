"""Native exact additional-binding lifecycle regressions.

Every portable binding fixture is produced by the real analysis/gate/build path.
"""

from __future__ import annotations


import pytest

import mountainash_rules as rules
from mountainash_rules import Lattice
from tests.accumulator.source_analysis_fixtures import (
    approve,
    case,
    contract,
    gate,
    predicate,
    row,
    scalar,
)


_ROWS = (
    {**row(1, 0, 9, amount=10), "other": 1, "label": "first"},
    {**row(2, 10, 20, amount=20), "other": 2, "label": "second"},
)


def _provider_contract(contract_id, *, promise, domain_ref="P"):
    """Construct an explicitly named resolve profile for one provider domain."""
    payload = contract(
        promise=promise,
        required=False,
        provenance="contributors",
        masked=True,
        contract_id=contract_id,
    ).model_dump(mode="json", exclude_none=True)
    payload["domain_ref"] = domain_ref
    return rules.ContextContract.model_validate(payload)


def _provider_domain(kwargs, *, domain_id="P", upper=9):
    region = predicate(
        {
            "op": "interval",
            "field": "x",
            "lower": scalar(0),
            "upper": scalar(upper),
            "lower_closed": True,
            "upper_closed": True,
        }
    )
    if not any(item["id"] == region["id"] for item in kwargs["predicates"]):
        kwargs["predicates"].append(region)
    kwargs["domains"].append(
        rules.DomainDefinition(
            schema_version=1,
            domain_id=domain_id,
            fields=kwargs["domains"][0].fields,
            predicate_id=region["id"],
        )
    )


def _build(contract_definition, *, provider_upper=None, rows=_ROWS):
    """Produce a complete bound lattice without manufacturing proof records."""
    kwargs, _ = case(contracts=[contract_definition])
    kwargs["source_label_field"] = "label"
    kwargs["aggregates"].append(
        rules.Aggregate(
            column_name="other",
            output_name="pricing.other",
            data_type="int",
            numeric_semantics="numeric-1",
        )
    )
    if provider_upper is not None:
        _provider_domain(kwargs, upper=provider_upper)

    analyzed = rules.analyze_sources(rows, **kwargs)
    approvals = [
        approve(analyzed, [finding.id], scope=finding.scope)
        for finding in analyzed.validation["findings"]
    ]
    if approvals:
        analyzed = rules.attach_warning_approvals(
            analyzed, approvals, limits=kwargs["limits"]
        )
    validated = gate(rows, kwargs, analyzed, approvals)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    return engine, engine.build(rows, validation=validated), kwargs["limits"]


@pytest.fixture
def additional_binding():
    """Two real builds with identical executable input and different contracts.

    Contracts are intentionally absent from the artifact identity.  The second
    build therefore supplies a real B binding/evidence closure for the first
    artifact, while retaining provider domain P and its compiled report.
    """
    contract_a = _provider_contract(
        "contract-a", promise="allow_unresolved", domain_ref="D"
    )
    engine_a, original, limits = _build(contract_a)

    contract_b = _provider_contract("contract-b", promise="definite_outcome")
    _, b_artifact, _ = _build(contract_b, provider_upper=9)
    (binding_b,) = b_artifact.bindings
    (binding_a,) = original.bindings
    evidence_b = b_artifact._evidence
    provider_domain = next(
        envelope["payload"]
        for envelope in evidence_b.predicates["domains"]
        if envelope["payload"]["domain_id"] == "P"
    )

    assert original.artifact_id == b_artifact.artifact_id
    assert tuple(binding.contract_id for binding in b_artifact.bindings) == (
        "contract-b",
    )
    assert tuple(evidence_b.validation["bindings"]) == (binding_b,)
    assert provider_domain["predicate_id"] in {
        envelope["id"] for envelope in evidence_b.predicates["predicates"]
    }

    return {
        "binding_a": binding_a,
        "binding_b": binding_b,
        "contract_a": contract_a,
        "contract_b": contract_b,
        "engine_a": engine_a,
        "evidence_b": evidence_b,
        "limits": limits,
        "original": original,
    }


def _attached(fixture):
    return fixture["original"].with_binding(
        fixture["binding_b"], evidence=fixture["evidence_b"], limits=fixture["limits"]
    )


def _variant_evidence(fixture, binding):
    """Use a real, separately built binding closure as portable input data."""
    _, artifact, _ = _build(binding, provider_upper=9)
    return artifact.bindings[0], artifact._evidence


def _annotated_binding_evidence(evidence, binding, limits):
    """Rehash an annotated binding so rejection tests use structurally valid evidence."""
    payload = binding.model_dump(mode="json", exclude={"id"})
    payload["annotations"] = {"review_note": "different normalized record"}
    envelope = rules.make_exact_envelope("binding", payload, limits=limits)
    annotated = rules.ContractBinding(id=envelope["id"], **envelope["payload"])
    import json

    bundle_payload = json.loads(rules.encode_validation_bundle(evidence, limits=limits))
    bundle_payload["validation"]["bindings"] = [
        annotated.model_dump(mode="json", exclude_none=True)
    ]
    return annotated, rules.ValidationBundle.model_validate(bundle_payload)


class TestNativeAdditionalBindings:
    def test_attached_binding_keeps_old_view_and_old_engine_behavior(
        self, additional_binding
    ):
        """A missing immutable-view split would leak B into the original artifact."""
        original = additional_binding["original"]
        attached = _attached(additional_binding)

        assert tuple(binding.contract_id for binding in original.bindings) == (
            "contract-a",
        )
        assert {binding.contract_id for binding in attached.bindings} == {
            "contract-a",
            "contract-b",
        }
        assert attached.artifact_id == original.artifact_id
        assert attached.combinations.to_dicts() == original.combinations.to_dicts()
        assert attached.contributors.to_dicts() == original.contributors.to_dicts()
        assert {item.contract_id for item in attached.metadata.context_contracts} == {
            "contract-a",
            "contract-b",
        }

        broad = additional_binding["engine_a"].apply(
            original, {}, contract_id="contract-a", profile_id="quote"
        )
        narrow = additional_binding["engine_a"].apply(
            attached, {}, contract_id="contract-b", profile_id="quote"
        )
        assert (broad.status, broad.reason) == ("unresolved", "insufficient_context")
        assert (narrow.status, narrow.reason, narrow.values) == (
            "decision",
            "established",
            {"pricing.total": 10},
        )
        assert {entry["output_name"] for entry in narrow.lineage.to_dicts()} == {
            "pricing.total"
        }
        with pytest.raises(ValueError, match="binding is unavailable"):
            additional_binding["engine_a"].apply(
                original, {}, contract_id="contract-b", profile_id="quote"
            )

    def test_attached_view_save_load_is_self_contained_with_requested_lineage(
        self, additional_binding, tmp_path
    ):
        """Dropping B closure at save/load would make named B apply unavailable."""
        attached = _attached(additional_binding)
        path = attached.save(tmp_path / "bound", limits=additional_binding["limits"])
        loaded = Lattice.load(path, limits=additional_binding["limits"])
        result = additional_binding["engine_a"].apply(
            loaded, {}, contract_id="contract-b", profile_id="quote"
        )

        assert {binding.contract_id for binding in loaded.bindings} == {
            "contract-a",
            "contract-b",
        }
        assert result.values == {"pricing.total": 10}
        assert {entry["output_name"] for entry in result.lineage.to_dicts()} == {
            "pricing.total"
        }
        assert {
            entry["output_name"] for entry in loaded.lineage(result.cell_id).to_dicts()
        } == {"pricing.total", "pricing.other"}

    def test_competing_binding_for_active_contract_is_rejected(
        self, additional_binding
    ):
        """Selecting a newer same-label binding would make contract apply ambiguous."""
        replacement_payload = _provider_contract(
            "contract-b", promise="allow_unresolved"
        )
        replacement, replacement_evidence = _variant_evidence(
            additional_binding, replacement_payload
        )

        with pytest.raises(ValueError):
            _attached(additional_binding).with_binding(
                replacement,
                evidence=replacement_evidence,
                limits=additional_binding["limits"],
            )

    def test_every_supplied_binding_record_is_validated_before_idempotence(
        self, additional_binding
    ):
        """Skipping validation on reattach would accept unrelated or altered evidence."""
        attached = _attached(additional_binding)
        annotated, annotated_evidence = _annotated_binding_evidence(
            additional_binding["evidence_b"],
            additional_binding["binding_b"],
            additional_binding["limits"],
        )

        assert (
            attached.with_binding(
                additional_binding["binding_b"],
                evidence=additional_binding["evidence_b"],
                limits=additional_binding["limits"],
            )
            is attached
        )
        with pytest.raises(ValueError):
            additional_binding["original"].with_binding(
                additional_binding["binding_a"],
                evidence=additional_binding["evidence_b"],
                limits=additional_binding["limits"],
            )
        with pytest.raises(ValueError):
            additional_binding["original"].with_binding(
                additional_binding["binding_b"],
                evidence=annotated_evidence,
                limits=additional_binding["limits"],
            )
        with pytest.raises(ValueError):
            attached.with_binding(
                additional_binding["binding_b"],
                evidence=attached._evidence,
                limits=additional_binding["limits"],
            )
        assert annotated.annotations == {"review_note": "different normalized record"}

    def test_changed_source_labels_amounts_and_origins_reject_attachment(
        self, additional_binding
    ):
        """Binding a proof for altered authored sources would detach it from this artifact."""
        contract_b = additional_binding["contract_b"]
        label_rows = (
            {**_ROWS[0], "label": "renamed"},
            _ROWS[1],
        )
        amount_rows = (
            {**_ROWS[0], "amount": 11},
            _ROWS[1],
        )
        origin_rows = (
            _ROWS[0],
            {**_ROWS[1], "lo": 11},
        )

        _, label_artifact, _ = _build(contract_b, provider_upper=9, rows=label_rows)
        _, amount_artifact, _ = _build(contract_b, provider_upper=9, rows=amount_rows)
        _, origin_artifact, _ = _build(contract_b, provider_upper=9, rows=origin_rows)

        for artifact in (label_artifact, amount_artifact, origin_artifact):
            with pytest.raises(ValueError):
                additional_binding["original"].with_binding(
                    artifact.bindings[0],
                    evidence=artifact._evidence,
                    limits=additional_binding["limits"],
                )

    def test_conflicting_or_widened_provider_domain_is_rejected(
        self, additional_binding
    ):
        """Accepting a redefined P could silently widen B beyond its proved provider domain."""
        attached = _attached(additional_binding)
        narrow_contract = _provider_contract("contract-c", promise="definite_outcome")
        wider_contract = _provider_contract("contract-d", promise="allow_unresolved")
        _, conflicting, _ = _build(narrow_contract, provider_upper=8)
        _, widened, _ = _build(wider_contract, provider_upper=20)

        with pytest.raises(ValueError):
            attached.with_binding(
                conflicting.bindings[0],
                evidence=conflicting._evidence,
                limits=additional_binding["limits"],
            )
        with pytest.raises(ValueError):
            attached.with_binding(
                widened.bindings[0],
                evidence=widened._evidence,
                limits=additional_binding["limits"],
            )
