"""Provider-neutral exact proof and permission-checking controls."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from mountainash_rules.core.codec import canonical_bytes, content_id
from mountainash_rules.core.reasoner import Reasoner

from mountainash_rules.core.contracts import (
    AnalysisInput,
    BindingVersions,
    ContextContract,
    ContextField,
    ContractBinding,
    DomainDefinition,
    CoverageRequirement,
    DiagnosticRule,
    DomainField,
    ExactLimits,
    ExactResourceError,
    Finding,
    OperationBudget,
    RequiredCheck,
    ReportCheck,
    ResolutionProfile,
    Scope,
    SemanticVersions,
    ValidatedBuildInput,
    ValidationBundle,
    ValidationPolicy,
    ValidationReport,
    WarningApproval,
    Witness,
    WitnessRequest,
)
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.scalar import encode_scalar
from mountainash_rules.core.validation import (
    AnalysisGeometry,
    CanonicalMaterial,
    StructuralCell,
    StructuralSource,
    produce_source_report,
    validate_build_permission,
    validate_contract_binding,
    prove_profile,
    prove_routing,
    prove_source_geometry,
    verify_witness,
)

_LIMITS = ExactLimits.model_validate(
    {
        "language": {
            "max_input_bytes": 100_000,
            "max_nesting": 64,
            "max_nfa_states": 4096,
            "max_states": 4096,
            "max_transitions": 65_536,
            "max_work": 100_000,
        },
        "max_input_bytes": 10_000_000,
        "max_output_bytes": 10_000_000,
        "max_work": 10**12,
        "max_live_bytes": 100_000_000,
        "max_predicate_nodes": 100_000,
        "max_dfa_states": 100_000,
        "max_dfa_transitions": 1_000_000,
        "max_theory_states": 1_000_000,
        "max_regions": 100_000,
        "max_scopes": 10_000,
        "max_source_scope_edges": 100_000,
        "max_contributor_edges": 100_000,
        "max_word_rows": 100_000,
        "max_numeric_bits": 100_000,
        "max_witnesses": 100_000,
    }
)


def _graph(*names: str) -> PredicateGraph:
    return PredicateGraph(
        [DomainField(name=name, data_type="int") for name in names],
        budget=OperationBudget(_LIMITS, "validation-test"),
    )


def _domain(
    graph: PredicateGraph, predicate_id: str, domain_id: str = "provider"
) -> DomainDefinition:
    return DomainDefinition(
        schema_version=1,
        domain_id=domain_id,
        fields=tuple(graph.fields.values()),
        predicate_id=predicate_id,
    )


def _source(identifier: str, predicate_id: str) -> StructuralSource:
    return StructuralSource(source_id=identifier, predicate_id=predicate_id)


def _cell(
    identifier: str, predicate_id: str, contributors: tuple[str, ...], **outputs: int
) -> StructuralCell:
    return StructuralCell(
        cell_id=identifier,
        predicate_id=predicate_id,
        contributors=contributors,
        outputs=outputs,
    )


def test_source_proof_uses_reasoner_for_complete_deterministic_duplicate_overlap_and_gap_regions():
    """Changing traversal must not discard any source pair or uncovered region."""
    graph = _graph("x")
    domain = _domain(
        graph,
        graph.interval("x", 0, 2, lower_closed=True, upper_closed=True),
        "compiled",
    )
    a = "00000000-0000-0000-0000-000000000001"
    b = "00000000-0000-0000-0000-000000000002"
    c = "00000000-0000-0000-0000-000000000003"
    dead = "00000000-0000-0000-0000-000000000004"
    sources = (
        _source(a, graph.eq("x", 0)),
        _source(b, graph.eq("x", 0)),
        _source(c, graph.interval("x", 0, 1, lower_closed=True, upper_closed=True)),
        _source(dead, graph.eq("x", 9)),
    )
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=domain,
        provider_domains=MappingProxyType({"compiled": domain}),
        partition_identity=MappingProxyType(
            {"routing_id": "routing:1:" + "a" * 64, "key_values": ()}
        ),
        sources=sources,
        cells=(_cell("cell:1:" + "1" * 64, graph.eq("x", 0), (a, b, c), amount=10),),
    )

    first = prove_source_geometry(geometry)
    reordered = prove_source_geometry(geometry.with_sources(tuple(reversed(sources))))

    assert first == reordered
    assert len(first.cell_gaps) == 1
    assert first.cell_gaps[0].source_ids == (c,)
    assert Reasoner(graph).equivalent(first.cell_gaps[0].predicate_id, graph.eq("x", 1))
    with pytest.raises(AttributeError):
        first.overlaps[0].source_ids += (dead,)


def test_structural_cell_allows_an_exact_cell_without_aggregates():
    """Exact contributor geometry remains valid without declared outputs."""
    graph = _graph("x")
    cell = _cell("cell:1:" + "0" * 64, graph.eq("x", 0), ("00000000-0000-0000-0000-000000000001",))
    assert cell.outputs == {}


def test_geometry_rejects_provider_domain_wider_than_compilation_geometry():
    """A selected provider domain cannot claim contexts absent from compilation."""
    graph = _graph("x")
    compilation = _domain(graph, graph.eq("x", 0), "compiled")
    provider = _domain(graph, graph.or_(graph.eq("x", 0), graph.eq("x", 1)), "provider")

    with pytest.raises(ValueError, match="provider domain"):
        AnalysisGeometry(
            graph=graph,
            compilation_domain=compilation,
            provider_domains={"provider": provider},
            partition_identity={
                "routing_id": "routing:1:" + "a" * 64,
                "key_values": (),
            },
            sources=(),
            cells=(),
        )


def test_source_proof_reports_required_region_missing_from_sources_not_just_cells():
    """Configured coverage is measured from its required region, independently of cell loss."""
    graph = _graph("x")
    domain = _domain(
        graph,
        graph.interval("x", 0, 1, lower_closed=True, upper_closed=True),
        "provider",
    )
    source = "00000000-0000-0000-0000-000000000005"
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=domain,
        provider_domains={"provider": domain},
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(_source(source, graph.eq("x", 0)),),
        cells=(_cell("cell:1:" + "5" * 64, graph.eq("x", 0), (source,), amount=10),),
    )
    requirement = CoverageRequirement(
        requirement_id="whole-provider",
        domain_ref="provider",
        region_predicate_id=graph.true,
        scope=_scope(),
        severity="error",
    )

    proof = prove_source_geometry(geometry, coverage_requirements=(requirement,))

    assert len(proof.coverage_gaps) == 1
    assert Reasoner(graph).equivalent(
        proof.coverage_gaps[0].predicate_id, graph.eq("x", 1)
    )


def test_geometry_allows_global_provider_domain_when_global_compilation_domain_covers_it():
    """A partition-local compilation domain does not narrow the global provider declaration."""
    graph = _graph("x")
    global_domain = _domain(
        graph, graph.interval("x", 0, 1, lower_closed=True, upper_closed=True), "global"
    )
    provider = _domain(graph, global_domain.predicate_id, "provider")

    geometry = AnalysisGeometry(
        graph=graph,
        global_compilation_domain=global_domain,
        compilation_domain=_domain(graph, graph.eq("x", 0), "partition"),
        provider_domains={"provider": provider},
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(),
        cells=(),
    )

    assert geometry.provider_domains["provider"] == provider


def test_profile_proof_quantifies_all_presence_masks_and_implicit_no_match_from_canonical_cells():
    """A definite profile cannot certify only its fully supplied request form."""
    graph = _graph("x")
    provider = _domain(
        graph, graph.interval("x", 0, 2, lower_closed=True, upper_closed=True)
    )
    a = "00000000-0000-0000-0000-000000000011"
    b = "00000000-0000-0000-0000-000000000012"
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=provider,
        provider_domains=MappingProxyType({"provider": provider}),
        partition_identity=MappingProxyType(
            {"routing_id": "routing:1:" + "a" * 64, "key_values": ()}
        ),
        sources=(_source(a, graph.eq("x", 0)), _source(b, graph.eq("x", 1))),
        cells=(
            _cell("cell:1:" + "2" * 64, graph.eq("x", 0), (a,), amount=10),
            _cell("cell:1:" + "3" * 64, graph.eq("x", 1), (b,), amount=20),
        ),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="preview",
        fields=(ContextField(name="x", data_type="int", required=False),),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="strict",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=("x",),
                allow_dont_care=("x",),
                promise="definite_outcome",
                on_unresolved="reject",
            ),
        ),
    )

    proof = prove_profile(geometry, contract, "strict", dimension_fields={"x": "x"})

    assert not proof.complete
    assert proof.counterexamples
    assert {state.presence for state in proof.states} == {(), ("x",)}
    assert {state.dont_care for state in proof.states} == {(), ("x",)}
    assert any(
        {outcome.kind for outcome in item.outcomes} == {"hit", "no_match"}
        for item in proof.counterexamples
    )


def test_profile_proof_enumerates_optional_routing_key_absence_and_keeps_guards_required():
    """An optional key has an absent/default state without making an unrelated guard optional."""
    graph = _graph("region", "tenant")
    provider = _domain(
        graph,
        graph.and_(
            graph.interval("region", 0, 1, lower_closed=True, upper_closed=True),
            graph.eq("tenant", 7),
        ),
    )
    source = "00000000-0000-0000-0000-000000000013"
    geometry = AnalysisGeometry(
        graph=graph,
        global_compilation_domain=provider,
        compilation_domain=provider,
        provider_domains={"provider": provider},
        routing={
            "schema_version": 1,
            "semantics": "exact-key-1",
            "key_dimensions": [
                {
                    "dimension_name": "region",
                    "context_field": "region",
                    "rule_field": "region",
                    "match_strategy": "exact_key",
                    "data_type": "int",
                    "role": "context_key",
                    "range_min_field": None,
                    "range_max_field": None,
                    "range_min_inclusive": None,
                    "range_max_inclusive": None,
                    "regex_pattern": None,
                }
            ],
            "partition_keys": [
                [],
                [
                    {
                        "dimension_name": "region",
                        "match": {"kind": "value", "value": encode_scalar(0, "int")},
                    }
                ],
            ],
        },
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(
            _source(source, graph.and_(graph.eq("region", 0), graph.eq("tenant", 7))),
        ),
        cells=(
            _cell(
                "cell:1:" + "6" * 64,
                graph.and_(graph.eq("region", 0), graph.eq("tenant", 7)),
                (source,),
                amount=10,
            ),
        ),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="routing",
        fields=(
            ContextField(name="region", data_type="int", required=False),
            ContextField(name="tenant", data_type="int", required=False),
        ),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="resolve",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=("region",),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )

    proof = prove_profile(
        geometry,
        contract,
        "resolve",
        dimension_fields={"region": "region"},
        guard_fields=("tenant",),
    )

    assert any(state.presence == ("tenant",) for state in proof.states)
    assert all("tenant" in state.presence for state in proof.states)


def test_routing_rejects_a_selected_wildcard_when_a_more_specific_key_owns_the_value():
    """A wildcard partition cannot claim a supplied value owned by a more-specific key."""
    graph = _graph("tenant", "region")
    provider = _domain(
        graph,
        graph.and_(
            graph.eq("tenant", 1),
            graph.interval("region", 1, 2, lower_closed=True, upper_closed=True),
        ),
    )
    source = "00000000-0000-0000-0000-000000000014"
    routing = {
        "schema_version": 1,
        "semantics": "exact-key-1",
        "key_dimensions": [
            {
                "dimension_name": "region",
                "context_field": "region",
                "rule_field": "region",
                "match_strategy": "exact_key",
                "data_type": "int",
                "role": "context_key",
                "range_min_field": None,
                "range_max_field": None,
                "range_min_inclusive": None,
                "range_max_inclusive": None,
                "regex_pattern": None,
            },
            {
                "dimension_name": "tenant",
                "context_field": "tenant",
                "rule_field": "tenant",
                "match_strategy": "exact_key",
                "data_type": "int",
                "role": "context_key",
                "range_min_field": None,
                "range_max_field": None,
                "range_min_inclusive": None,
                "range_max_inclusive": None,
                "regex_pattern": None,
            },
        ],
        "partition_keys": [
            [
                {"dimension_name": "region", "match": {"kind": "wildcard"}},
                {
                    "dimension_name": "tenant",
                    "match": {"kind": "value", "value": encode_scalar(1, "int")},
                },
            ],
            [
                {
                    "dimension_name": "region",
                    "match": {"kind": "value", "value": encode_scalar(2, "int")},
                },
                {
                    "dimension_name": "tenant",
                    "match": {"kind": "value", "value": encode_scalar(1, "int")},
                },
            ],
        ],
    }
    geometry = AnalysisGeometry(
        graph=graph,
        global_compilation_domain=provider,
        compilation_domain=provider,
        provider_domains={"provider": provider},
        routing=routing,
        partition_identity={
            "routing_id": "routing:1:" + "a" * 64,
            "key_values": routing["partition_keys"][0],
        },
        sources=(
            _source(source, graph.and_(graph.eq("tenant", 1), graph.eq("region", 1))),
        ),
        cells=(
            _cell(
                "cell:1:" + "8" * 64,
                graph.and_(graph.eq("tenant", 1), graph.eq("region", 1)),
                (source,),
                amount=10,
            ),
        ),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="specificity",
        fields=(
            ContextField(name="region", data_type="int", required=True),
            ContextField(name="tenant", data_type="int", required=True),
        ),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="resolve",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=(),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )
    context = {"tenant": encode_scalar(1, "int"), "region": encode_scalar(2, "int")}
    witness = Witness(
        kind="pair",
        contexts=(context, context),
        profile_ref={"contract_id": "specificity", "profile_id": "resolve"},
        request=WitnessRequest(
            provided_values=context, unavailable_fields=(), dont_care=()
        ),
    )

    with pytest.raises(ValueError, match="selected routing"):
        verify_witness(geometry, contract, witness, dimension_fields={})


def test_routing_proof_preserves_no_route_and_equal_specificity_ambiguity_regions():
    """Routing evidence distinguishes an absent optional key from a tied concrete overlap."""
    graph = _graph("tenant", "region")
    provider = _domain(
        graph,
        graph.and_(
            graph.eq("tenant", 1),
            graph.interval("region", 1, 2, lower_closed=True, upper_closed=True),
        ),
    )
    routing = {
        "schema_version": 1,
        "semantics": "exact-key-1",
        "key_dimensions": [
            {
                "dimension_name": "region",
                "context_field": "region",
                "rule_field": "region",
                "match_strategy": "exact_key",
                "data_type": "int",
                "role": "context_key",
                "range_min_field": None,
                "range_max_field": None,
                "range_min_inclusive": None,
                "range_max_inclusive": None,
                "regex_pattern": None,
            },
            {
                "dimension_name": "tenant",
                "context_field": "tenant",
                "rule_field": "tenant",
                "match_strategy": "exact_key",
                "data_type": "int",
                "role": "context_key",
                "range_min_field": None,
                "range_max_field": None,
                "range_min_inclusive": None,
                "range_max_inclusive": None,
                "regex_pattern": None,
            },
        ],
        "partition_keys": [
            [
                {
                    "dimension_name": "region",
                    "match": {"kind": "value", "value": encode_scalar(2, "int")},
                }
            ],
            [
                {
                    "dimension_name": "tenant",
                    "match": {"kind": "value", "value": encode_scalar(1, "int")},
                }
            ],
        ],
    }
    geometry = AnalysisGeometry(
        graph=graph,
        global_compilation_domain=provider,
        compilation_domain=provider,
        provider_domains={"provider": provider},
        routing=routing,
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": []},
        sources=(),
        cells=(),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="routing-proof",
        fields=(
            ContextField(name="region", data_type="int", required=False),
            ContextField(name="tenant", data_type="int", required=False),
        ),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="resolve",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=(),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )

    proof = prove_routing(geometry, contract)

    assert any(evidence.state.presence == () for evidence in proof.no_route)
    assert any(evidence.partition_key_indices == (0, 1) for evidence in proof.ambiguous)


def test_profile_proof_rejects_contract_field_type_incompatible_with_provider_domain():
    """Profile reasoning must use the declared provider contract type, not graph coercion."""
    graph = _graph("x")
    provider = _domain(graph, graph.eq("x", 0))
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=provider,
        provider_domains={"provider": provider},
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(),
        cells=(),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="wrong-type",
        fields=(ContextField(name="x", data_type="float", required=False),),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="resolve",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=("x",),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )

    with pytest.raises(ValueError, match="type"):
        prove_profile(geometry, contract, "resolve", dimension_fields={"x": "x"})


def test_profile_preflight_rejects_optional_state_enumeration_before_materializing_it():
    """A profile state-space cap raises a typed resource error instead of allocating candidates."""
    limits = _LIMITS.model_copy(update={"max_work": 1_000_000, "max_live_bytes": 65536})
    graph = PredicateGraph(
        [DomainField(name=f"x{index:02}", data_type="int") for index in range(12)],
        budget=OperationBudget(limits, "profile-preflight"),
    )
    domain = _domain(graph, graph.true)
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=domain,
        provider_domains={"provider": domain},
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(),
        cells=(),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="optional-space",
        fields=tuple(
            ContextField(name=f"x{index:02}", data_type="int", required=False)
            for index in range(12)
        ),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="resolve",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=(),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )

    with pytest.raises(ExactResourceError) as error:
        prove_profile(geometry, contract, "resolve", dimension_fields={})

    assert error.value.phase == "profile.preflight"


def test_profile_resource_exhaustion_propagates_instead_of_creating_an_approvable_partial_proof():
    """A capped witness ledger aborts profile proof rather than returning an empty result."""
    limits = _LIMITS.model_copy(update={"max_witnesses": 0})
    graph = PredicateGraph(
        [DomainField(name="x", data_type="int")],
        budget=OperationBudget(limits, "profile-cap"),
    )
    domain = _domain(
        graph, graph.interval("x", 0, 1, lower_closed=True, upper_closed=True)
    )
    source = "00000000-0000-0000-0000-000000000019"
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=domain,
        provider_domains={"provider": domain},
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(_source(source, graph.eq("x", 0)),),
        cells=(_cell("cell:1:" + "9" * 64, graph.eq("x", 0), (source,), amount=10),),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="cap",
        fields=(ContextField(name="x", data_type="int", required=False),),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="strict",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=("x",),
                allow_dont_care=("x",),
                promise="definite_outcome",
                on_unresolved="reject",
            ),
        ),
    )
    with pytest.raises(ExactResourceError):
        prove_profile(geometry, contract, "strict", dimension_fields={"x": "x"})


def test_witness_checks_full_supplied_admission_before_masks_then_effective_pair_facts():
    """A masked supplied fact may use a third admission completion, but not an invalid one."""
    graph = _graph("x", "y")
    provider_predicate = graph.or_(
        graph.and_(graph.eq("x", 0), graph.eq("y", 0)),
        graph.and_(graph.eq("x", 0), graph.eq("y", 1)),
        graph.and_(graph.eq("x", 0), graph.eq("y", 2)),
    )
    provider = _domain(graph, provider_predicate)
    source = "00000000-0000-0000-0000-000000000021"
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=provider,
        provider_domains=MappingProxyType({"provider": provider}),
        partition_identity=MappingProxyType(
            {"routing_id": "routing:1:" + "a" * 64, "key_values": ()}
        ),
        sources=(_source(source, graph.and_(graph.eq("x", 0), graph.eq("y", 1))),),
        cells=(
            _cell(
                "cell:1:" + "4" * 64,
                graph.and_(graph.eq("x", 0), graph.eq("y", 1)),
                (source,),
                amount=10,
            ),
        ),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="preview",
        fields=(
            ContextField(name="x", data_type="int", required=True),
            ContextField(name="y", data_type="int", required=True),
        ),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="masked",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=("x", "y"),
                allow_dont_care=("y",),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )

    def scalar(value):
        return encode_scalar(value, graph.fields["x"].data_type)

    witness = Witness(
        kind="pair",
        contexts=(
            {"x": scalar(0), "y": scalar(1)},
            {"x": scalar(0), "y": scalar(0)},
        ),
        profile_ref={"contract_id": "preview", "profile_id": "masked"},
        request=WitnessRequest(
            provided_values={"x": scalar(0), "y": scalar(2)},
            unavailable_fields=(),
            dont_care=("y",),
        ),
    )
    verified = verify_witness(
        geometry, contract, witness, dimension_fields={"x": "x", "y": "y"}
    )
    assert verified.admission_predicate_id == graph.and_(
        provider_predicate, graph.eq("x", 0), graph.eq("y", 2)
    )
    assert verified.effective_fields == ("x",)

    invalid = witness.model_copy(
        update={
            "request": witness.request.model_copy(
                update={
                    "provided_values": {"x": scalar(1), "y": scalar(2)},
                }
            )
        }
    )
    with pytest.raises(ValueError, match="admission"):
        verify_witness(
            geometry, contract, invalid, dimension_fields={"x": "x", "y": "y"}
        )


def test_profile_pair_witness_requires_distinct_projected_outcomes():
    """A profile-counterexample pair cannot reuse one completion for both outcomes."""
    graph = _graph("x")
    provider = _domain(
        graph, graph.interval("x", 0, 1, lower_closed=True, upper_closed=True)
    )
    source = "00000000-0000-0000-0000-000000000022"
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=provider,
        provider_domains={"provider": provider},
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(_source(source, graph.eq("x", 0)),),
        cells=(_cell("cell:1:" + "7" * 64, graph.eq("x", 0), (source,), amount=10),),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="pair",
        fields=(ContextField(name="x", data_type="int", required=True),),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="resolve",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=("x",),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )
    scalar = encode_scalar(0, "int")
    witness = Witness(
        kind="pair",
        contexts=({"x": scalar}, {"x": scalar}),
        profile_ref={"contract_id": "pair", "profile_id": "resolve"},
        request=WitnessRequest(
            provided_values={"x": scalar}, unavailable_fields=(), dont_care=()
        ),
    )

    with pytest.raises(ValueError, match="distinct"):
        verify_witness(geometry, contract, witness, dimension_fields={"x": "x"})


def test_source_region_witness_must_satisfy_its_recorded_region():
    """A source-region example outside the finding's predicate is not reproducible evidence."""
    graph = _graph("x")
    provider = _domain(
        graph, graph.interval("x", 0, 1, lower_closed=True, upper_closed=True)
    )
    geometry = AnalysisGeometry(
        graph=graph,
        compilation_domain=provider,
        provider_domains={"provider": provider},
        partition_identity={"routing_id": "routing:1:" + "a" * 64, "key_values": ()},
        sources=(),
        cells=(),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="source",
        fields=(ContextField(name="x", data_type="int", required=True),),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="source",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=(),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )
    finding_payload = {
        "schema_version": 1,
        "analysis_input_id": _id("analysis-input"),
        "stage": "source",
        "check_id": "coverage",
        "code": "coverage_gap",
        "severity": "error",
        "scope": _scope().model_dump(mode="json"),
        "source_ids": [],
        "cell_ids": [],
        "region_predicate_id": graph.eq("x", 1),
        "witnesses": [],
        "witnesses_complete": True,
    }
    finding = Finding(id=_id("finding", finding_payload), **finding_payload)
    witness = Witness(
        kind="point",
        contexts=({"x": encode_scalar(0, "int")},),
        profile_ref=None,
        request=None,
    )

    with pytest.raises(ValueError, match="region"):
        verify_witness(
            geometry, contract, witness, dimension_fields={}, finding=finding
        )


def _id(kind: str, payload: dict[str, object] | None = None) -> str:
    return content_id(kind, payload) if payload is not None else f"{kind}:1:" + "a" * 64


def _scope() -> Scope:
    return Scope(
        partition_refs=({"routing_id": _id("routing"), "key_values": []},),
        domain_refs=("provider",),
        profile_refs=({"contract_id": "provider", "profile_id": "profile"},),
    )


def _contract_envelope(
    *, domain_ref: str = "provider"
) -> tuple[dict[str, object], ContextContract]:
    contract = ContextContract(
        schema_version=1,
        contract_id="provider",
        fields=(ContextField(name="x", data_type="int", required=True),),
        domain_ref=domain_ref,
        profiles=(
            ResolutionProfile(
                profile_id="profile",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=("x",),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )
    return {
        "id": _id("contract"),
        "payload": {"schema_version": 1, "contract": contract.model_dump(mode="json")},
    }, contract


def _permission_fixture():
    """Return actual typed records with a manually assembled foreign approval case."""
    versions = SemanticVersions(
        scalar="scalar-1",
        predicate="predicate-1",
        language="language-1",
        numeric="numeric-1",
        canonical="canonical-json-1",
        normalization="normalization-2",
    )
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="policy",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=tuple(
            sorted(
                (
                    DiagnosticRule(
                        stage="source",
                        check_id="source_overlaps",
                        code="source_overlap",
                        scope=_scope(),
                        severity="warning",
                        witness_kind="none",
                        max_witnesses=0,
                    ),
                    DiagnosticRule(
                        stage="source",
                        check_id="routing",
                        code="routing_gap",
                        scope=_scope(),
                        severity="error",
                        witness_kind="none",
                        max_witnesses=0,
                    ),
                    DiagnosticRule(
                        stage="source",
                        check_id="routing",
                        code="routing_ambiguity",
                        scope=_scope(),
                        severity="error",
                        witness_kind="none",
                        max_witnesses=0,
                    ),
                    DiagnosticRule(
                        stage="source",
                        check_id="profiles",
                        code="profile_counterexample",
                        scope=_scope(),
                        severity="warning",
                        witness_kind="none",
                        max_witnesses=0,
                    ),
                ),
                key=lambda item: canonical_bytes(item.model_dump(mode="json")),
            )
        ),
    )
    analysis_payload = {
        "schema_version": 1,
        "ruleset_id": "rules",
        "source_id_field": "source_id",
        "source_bundle_digest": _id("source-bundle"),
        "compilation_domain_ref": "provider",
        "domain_digests": {"provider": _id("domain")},
        "metadata_digest": _id("metadata"),
        "aggregate_digest": _id("aggregates"),
        "routing_digest": _id("routing"),
        "contracts": [{"contract_id": "provider", "contract_digest": _id("contract")}],
        "validation_policy": policy.model_dump(mode="json"),
        "semantic_versions": versions.model_dump(mode="json"),
    }
    analysis = AnalysisInput(
        id=_id("analysis-input", analysis_payload), **analysis_payload
    )
    scope = _scope()
    warning_payload = {
        "schema_version": 1,
        "analysis_input_id": analysis.id,
        "stage": "source",
        "check_id": "source_overlaps",
        "code": "source_overlap",
        "severity": "warning",
        "scope": scope.model_dump(mode="json"),
        "source_ids": (),
        "cell_ids": [],
        "region_predicate_id": None,
        "witnesses": [],
        "witnesses_complete": True,
    }
    warning = Finding(id=_id("finding", warning_payload), **warning_payload)
    source_checks = tuple(
        ReportCheck(
            check_id=check,
            scope=scope,
            status="findings" if check == "source_overlaps" else "passed",
            complete=True,
            finding_ids=(warning.id,) if check == "source_overlaps" else (),
        )
        for check in (
            "coverage",
            "profiles",
            "routing",
            "source_identity",
            "source_overlaps",
            "source_predicates",
            "source_schema",
        )
    )

    def report(
        *,
        suffix: str,
        checks: tuple[ReportCheck, ...],
        stage: str,
        artifact_id: str | None = None,
    ) -> ValidationReport:
        payload = {
            "schema_version": 1,
            "analysis_input_id": analysis.id,
            "stage": stage,
            "artifact_id": artifact_id,
            "validator": {
                "validator_id": "validator",
                "semantic_version": "source-analysis-1" if stage == "source" else "compiled-analysis-1",
            },
            "scope": scope.model_dump(mode="json"),
            "checks": [item.model_dump(mode="json") for item in checks],
            "finding_ids": sorted(
                identifier for item in checks for identifier in item.finding_ids
            ),
        }
        # The suffix changes validator identity without changing the warning.
        payload["validator"]["validator_id"] += suffix
        return ValidationReport(id=_id("report", payload), **payload)

    source_one = report(suffix="one", checks=source_checks, stage="source")
    source_two = report(suffix="two", checks=source_checks, stage="source")
    compiled_checks = tuple(
        ReportCheck(
            check_id=check, scope=scope, status="passed", complete=True, finding_ids=()
        )
        for check in (
            "cell_disjointness",
            "cell_nonempty",
            "output_folds",
            "profile_consistency",
            "source_membership",
            "source_union",
        )
    )
    compiled = report(
        suffix="compiled",
        checks=compiled_checks,
        stage="compiled",
        artifact_id=_id("artifact"),
    )

    def approval(report_id: str) -> WarningApproval:
        payload = {
            "schema_version": 1,
            "analysis_input_id": analysis.id,
            "report_id": report_id,
            "authority_ref": "owner",
            "actor_ref": "actor",
            "decision": "approve_warnings",
            "scope": scope.model_dump(mode="json"),
            "warning_ids": [warning.id],
        }
        return WarningApproval(id=_id("approval", payload), **payload)

    approval_one, approval_two = approval(source_one.id), approval(source_two.id)
    contract_envelope, _contract = _contract_envelope()
    bundle = ValidationBundle.model_construct(
        schema_version=1,
        metadata={},
        aggregates={},
        routing={
            "id": analysis.routing_digest,
            "payload": {
                "schema_version": 1, "semantics": "exact-key-1",
                "key_dimensions": [], "partition_keys": [[]],
            },
        },
        context_contracts=(contract_envelope,),
        predicates={},
        validation=MappingProxyType(
            {
                "analysis_inputs": (analysis,),
                "findings": (warning,),
                "reports": (source_one, source_two, compiled),
                "approvals": (approval_one, approval_two),
                "bindings": (),
            }
        ),
    )
    material = CanonicalMaterial(
        analysis_input_id=analysis.id,
        source_bundle_id=analysis.source_bundle_digest,
        metadata_id=analysis.metadata_digest,
        aggregates_id=analysis.aggregate_digest,
        routing_id=analysis.routing_digest,
        domain_ids=analysis.domain_digests,
        semantic_versions=analysis.semantic_versions,
        artifact_id=compiled.artifact_id,
        selected_scope=_scope(),
    )
    return (
        analysis,
        source_one,
        source_two,
        compiled,
        approval_one,
        approval_two,
        bundle,
        material,
    )


def test_permission_gate_requires_policy_scoped_check_and_matching_diagnostic_rule():
    """A generic check or unruled warning cannot satisfy a profile-specific policy obligation."""
    (
        analysis,
        source_one,
        _source_two,
        _compiled,
        approval_one,
        _approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    base_scope = _scope().model_dump(mode="json")
    profile_scope = Scope(
        partition_refs=base_scope["partition_refs"],
        domain_refs=base_scope["domain_refs"],
        profile_refs=({"contract_id": "provider", "profile_id": "p1"},),
    )
    required_policy = analysis.validation_policy.model_copy(
        update={
            "required_checks": (
                RequiredCheck(stage="source", check_id="profiles", scope=profile_scope),
            ),
        }
    )
    missing_diagnostic_policy = required_policy.model_copy(
        update={"diagnostic_rules": ()}
    )
    selected_analysis = analysis.model_copy(
        update={"validation_policy": missing_diagnostic_policy}
    )
    policy_bundle = bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bundle.validation),
                    "analysis_inputs": (selected_analysis,),
                }
            )
        }
    )
    value = ValidatedBuildInput.model_construct(
        schema_version=1,
        analysis_input_id=analysis.id,
        source_report_id=source_one.id,
        approval_ids=(approval_one.id,),
        bundle=policy_bundle,
    )

    with pytest.raises(ValueError, match="policy|required|diagnostic"):
        validate_build_permission(value, material)


def test_permission_gate_accepts_only_policy_vacuous_not_required_coverage():
    """A coverage check is authorizable as not_required exactly when policy has no requirement."""
    (
        analysis,
        source_one,
        _source_two,
        _compiled,
        approval_one,
        _approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    checks = tuple(
        check.model_copy(update={"status": "not_required"})
        if check.check_id == "coverage"
        else check
        for check in source_one.checks
    )
    vacuous = source_one.model_copy(update={"checks": checks})
    vacuous_bundle = bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bundle.validation),
                    "reports": tuple(
                        vacuous if report.id == source_one.id else report
                        for report in bundle.validation["reports"]
                    ),
                }
            )
        }
    )
    value = ValidatedBuildInput.model_construct(
        schema_version=1,
        analysis_input_id=analysis.id,
        source_report_id=vacuous.id,
        approval_ids=(approval_one.id,),
        bundle=vacuous_bundle,
    )

    permission = validate_build_permission(value, material)

    assert permission.source_report_id == vacuous.id


def test_permission_gate_requires_selected_report_scoped_approval_and_fresh_canonical_material():
    """A same-warning approval under another report cannot authorize a stale build."""
    (
        analysis,
        source_one,
        source_two,
        _compiled,
        approval_one,
        approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    foreign = ValidatedBuildInput.model_construct(
        schema_version=1,
        analysis_input_id=analysis.id,
        source_report_id=source_one.id,
        approval_ids=(approval_two.id,),
        bundle=bundle,
    )
    with pytest.raises(ValueError, match="report"):
        validate_build_permission(foreign, material)

    selected = foreign.model_copy(update={"approval_ids": (approval_one.id,)})
    permission = validate_build_permission(selected, material)
    assert permission.source_report_id == source_one.id
    assert permission.approval_ids == (approval_one.id,)

    stale = material.__class__(
        analysis_input_id=material.analysis_input_id,
        source_bundle_id=_id("source-bundle", {"changed": True}),
        metadata_id=material.metadata_id,
        aggregates_id=material.aggregates_id,
        routing_id=material.routing_id,
        domain_ids=material.domain_ids,
        semantic_versions=material.semantic_versions,
        artifact_id=material.artifact_id,
        selected_scope=material.selected_scope,
    )
    with pytest.raises(ValueError, match="source bundle"):
        validate_build_permission(selected, stale)


def test_binding_gate_rejects_competing_active_binding_and_incomplete_compiled_proof():
    """Binding permission requires both complete reports and a unique artifact/contract pair."""
    (
        analysis,
        source_one,
        _source_two,
        compiled,
        approval_one,
        _approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    _envelope, contract = _contract_envelope()
    profile_model = contract.profiles[0]
    profile = {
        "profile_id": profile_model.profile_id,
        "profile_digest": content_id(
            "profile",
            {
                "schema_version": 1,
                "contract_id": contract.contract_id,
                "profile": profile_model.model_dump(mode="json"),
            },
        ),
    }
    binding_payload = {
        "schema_version": 1,
        "artifact_id": material.artifact_id,
        "analysis_input_id": analysis.id,
        "contract_id": "provider",
        "contract_digest": _id("contract"),
        "domain_ref": "provider",
        "domain_digest": material.domain_ids["provider"],
        "authorized_profiles": [profile],
        "source_report_id": source_one.id,
        "compiled_report_id": compiled.id,
        "approval_ids": [approval_one.id],
        "semantic_versions": BindingVersions(
            content=analysis.semantic_versions,
            binding="binding-1",
            checker="binding-checker-1",
        ).model_dump(mode="json"),
    }
    binding = ContractBinding(id=_id("binding", binding_payload), **binding_payload)
    bound_bundle = bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bundle.validation),
                    "bindings": (binding,),
                }
            )
        }
    )
    evidence = validate_contract_binding(binding, bound_bundle, material)
    assert evidence.binding_id == binding.id

    mismatched_envelope, _mismatched_contract = _contract_envelope(domain_ref="other")
    mismatched_bundle = bound_bundle.model_copy(
        update={"context_contracts": (mismatched_envelope,)}
    )
    with pytest.raises(ValueError):
        validate_contract_binding(binding, mismatched_bundle, material)

    competing_payload = {
        **binding_payload,
        "authorized_profiles": [
            {
                "profile_id": "other",
                "profile_digest": _id("profile", {"profile": "other"}),
            }
        ],
    }
    competing = ContractBinding(
        id=_id("binding", competing_payload), **competing_payload
    )
    conflict_bundle = bound_bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bound_bundle.validation),
                    "bindings": (binding, competing),
                }
            )
        }
    )
    with pytest.raises(ValueError, match="active"):
        validate_contract_binding(binding, conflict_bundle, material)

    incomplete = compiled.model_copy(
        update={
            "checks": tuple(
                item.model_copy(
                    update={"status": "resource_exhausted", "complete": False}
                )
                if item.check_id == "profile_consistency"
                else item
                for item in compiled.checks
            )
        }
    )
    incomplete_bundle = bound_bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bound_bundle.validation),
                    "reports": (source_one, incomplete),
                }
            )
        }
    )
    with pytest.raises(ValueError):
        validate_contract_binding(binding, incomplete_bundle, material)


def test_permission_gate_requires_partially_overlapping_policy_source_check():
    """A policy check covering part of selected profiles remains mandatory."""
    (
        analysis,
        source_one,
        _source_two,
        _compiled,
        approval_one,
        _approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    base_scope = _scope().model_dump(mode="json")
    selected_scope = Scope(
        partition_refs=base_scope["partition_refs"],
        domain_refs=base_scope["domain_refs"],
        profile_refs=({"contract_id": "provider", "profile_id": "profile"},),
    )
    policy_scope = Scope(
        partition_refs=base_scope["partition_refs"],
        domain_refs=base_scope["domain_refs"],
        profile_refs=(),
    )
    selected_analysis = analysis.model_copy(
        update={
            "validation_policy": analysis.validation_policy.model_copy(
                update={
                    "required_checks": (
                        RequiredCheck(
                            stage="source",
                            check_id="source_overlaps",
                            scope=policy_scope,
                        ),
                    )
                }
            )
        }
    )
    selected_report = source_one.model_copy(
        update={
            "scope": selected_scope,
            "checks": tuple(
                check.model_copy(update={"scope": selected_scope})
                for check in source_one.checks
            ),
        }
    )
    selected_bundle = bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bundle.validation),
                    "analysis_inputs": (selected_analysis,),
                    "reports": (selected_report,),
                }
            )
        }
    )
    selected_material = material.__class__(
        analysis_input_id=material.analysis_input_id,
        source_bundle_id=material.source_bundle_id,
        metadata_id=material.metadata_id,
        aggregates_id=material.aggregates_id,
        routing_id=material.routing_id,
        domain_ids=material.domain_ids,
        semantic_versions=material.semantic_versions,
        artifact_id=material.artifact_id,
        selected_scope=selected_scope,
    )
    value = ValidatedBuildInput.model_construct(
        schema_version=1,
        analysis_input_id=analysis.id,
        source_report_id=selected_report.id,
        approval_ids=(approval_one.id,),
        bundle=selected_bundle,
    )

    with pytest.raises(ValueError, match="mandatory|policy"):
        validate_build_permission(value, selected_material)

    covered_report = selected_report.model_copy(
        update={
            "checks": selected_report.checks
            + (
                ReportCheck(
                    check_id="source_overlaps",
                    scope=policy_scope,
                    status="passed",
                    complete=True,
                    finding_ids=(),
                ),
            )
        }
    )
    covered_bundle = selected_bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(selected_bundle.validation),
                    "reports": (covered_report,),
                }
            )
        }
    )
    covered_value = value.model_copy(
        update={"source_report_id": covered_report.id, "bundle": covered_bundle}
    )

    permission = validate_build_permission(covered_value, selected_material)

    assert permission.source_report_id == covered_report.id


def test_binding_gate_requires_broader_policy_compiled_check():
    """A compiled policy check spanning a selected profile cannot be skipped."""
    (
        analysis,
        source_one,
        _source_two,
        compiled,
        approval_one,
        _approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    base_scope = _scope().model_dump(mode="json")
    policy_scope = Scope(
        partition_refs=base_scope["partition_refs"],
        domain_refs=base_scope["domain_refs"],
        profile_refs=(
            {"contract_id": "provider", "profile_id": "other"},
            {"contract_id": "provider", "profile_id": "profile"},
        ),
    )
    selected_analysis = analysis.model_copy(
        update={
            "validation_policy": analysis.validation_policy.model_copy(
                update={
                    "required_checks": (
                        RequiredCheck(
                            stage="compiled",
                            check_id="policy_compiled",
                            scope=policy_scope,
                        ),
                    )
                }
            )
        }
    )
    _envelope, contract = _contract_envelope()
    profile_model = contract.profiles[0]
    profile = {
        "profile_id": profile_model.profile_id,
        "profile_digest": content_id(
            "profile",
            {
                "schema_version": 1,
                "contract_id": contract.contract_id,
                "profile": profile_model.model_dump(mode="json"),
            },
        ),
    }
    binding_payload = {
        "schema_version": 1,
        "artifact_id": material.artifact_id,
        "analysis_input_id": analysis.id,
        "contract_id": "provider",
        "contract_digest": _id("contract"),
        "domain_ref": "provider",
        "domain_digest": material.domain_ids["provider"],
        "authorized_profiles": [profile],
        "source_report_id": source_one.id,
        "compiled_report_id": compiled.id,
        "approval_ids": [approval_one.id],
        "semantic_versions": BindingVersions(
            content=analysis.semantic_versions,
            binding="binding-1",
            checker="binding-checker-1",
        ).model_dump(mode="json"),
    }
    binding = ContractBinding(id=_id("binding", binding_payload), **binding_payload)
    selected_bundle = bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bundle.validation),
                    "analysis_inputs": (selected_analysis,),
                    "bindings": (binding,),
                }
            )
        }
    )

    with pytest.raises(ValueError):
        validate_contract_binding(binding, selected_bundle, material)


def test_binding_gate_accepts_canonical_candidate_profile_digest():
    """Candidate profiles omit their forbidden null field before profile hashing."""
    (
        analysis,
        source_one,
        _source_two,
        compiled,
        approval_one,
        _approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    _envelope, contract = _contract_envelope()
    candidate = ResolutionProfile(
        profile_id="profile",
        mode="candidates",
        output_fields=(),
        provenance="none",
        dimensions=("x",),
        allow_dont_care=(),
        promise="candidate_only",
    )
    candidate_contract = contract.model_copy(update={"profiles": (candidate,)})
    profile = {
        "profile_id": candidate.profile_id,
        "profile_digest": content_id(
            "profile",
            {
                "schema_version": 1,
                "contract_id": candidate_contract.contract_id,
                "profile": candidate.model_dump(mode="json", exclude_none=True),
            },
        ),
    }
    binding_payload = {
        "schema_version": 1,
        "artifact_id": material.artifact_id,
        "analysis_input_id": analysis.id,
        "contract_id": "provider",
        "contract_digest": _id("contract"),
        "domain_ref": "provider",
        "domain_digest": material.domain_ids["provider"],
        "authorized_profiles": [profile],
        "source_report_id": source_one.id,
        "compiled_report_id": compiled.id,
        "approval_ids": [approval_one.id],
        "semantic_versions": BindingVersions(
            content=analysis.semantic_versions,
            binding="binding-1",
            checker="binding-checker-1",
        ).model_dump(mode="json"),
    }
    binding = ContractBinding(id=_id("binding", binding_payload), **binding_payload)
    candidate_envelope = {
        "id": _id("contract"),
        "payload": {
            "schema_version": 1,
            "contract": candidate_contract.model_dump(mode="json"),
        },
    }
    candidate_bundle = bundle.model_copy(
        update={
            "context_contracts": (candidate_envelope,),
            "validation": MappingProxyType(
                {
                    **dict(bundle.validation),
                    "bindings": (binding,),
                }
            ),
        }
    )

    permission = validate_contract_binding(binding, candidate_bundle, material)

    assert permission.binding_id == binding.id


def test_routing_proof_omits_regions_owned_by_higher_specificity_keys():
    """A winner shadows lower keys before reachability and tie evidence are recorded."""
    graph = _graph("region", "channel")
    provider = _domain(
        graph,
        graph.and_(graph.eq("region", 1), graph.eq("channel", 2)),
    )
    dimensions = [
        {
            "dimension_name": name,
            "context_field": name,
            "rule_field": name,
            "match_strategy": "exact_key",
            "data_type": "int",
            "role": "context_key",
            "range_min_field": None,
            "range_max_field": None,
            "range_min_inclusive": None,
            "range_max_inclusive": None,
            "regex_pattern": None,
        }
        for name in ("region", "channel")
    ]

    def value(name, item):
        return {
            "dimension_name": name,
            "match": {"kind": "value", "value": encode_scalar(item, "int")},
        }

    routing = {
        "schema_version": 1,
        "semantics": "exact-key-1",
        "key_dimensions": dimensions,
        "partition_keys": [
            [],
            [value("region", 1)],
            [value("channel", 2)],
            [value("region", 1), value("channel", 2)],
        ],
    }
    geometry = AnalysisGeometry(
        graph=graph,
        global_compilation_domain=provider,
        compilation_domain=provider,
        provider_domains={"provider": provider},
        routing=routing,
        partition_identity={"routing_id": _id("routing"), "key_values": []},
        sources=(),
        cells=(),
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="routing-specificity",
        fields=(
            ContextField(name="channel", data_type="int", required=True),
            ContextField(name="region", data_type="int", required=True),
        ),
        domain_ref="provider",
        profiles=(
            ResolutionProfile(
                profile_id="resolve",
                mode="resolve",
                output_fields=("amount",),
                provenance="none",
                dimensions=(),
                allow_dont_care=(),
                promise="allow_unresolved",
                on_unresolved="return",
            ),
        ),
    )

    proof = prove_routing(geometry, contract)

    assert proof.reachable_partition_key_indices == (3,)
    assert proof.ambiguous == ()


def test_source_report_semantics_accepts_producer_labels_but_rejects_unknown_versions():
    """Producer labels are unauthenticated; semantic-version support is not."""
    from mountainash_rules.core import validation

    assert hasattr(validation, "validate_source_report_semantics")
    (
        _analysis,
        source_one,
        _source_two,
        _compiled,
        _approval_one,
        _approval_two,
        _bundle,
        _material,
    ) = _permission_fixture()

    accepted_payload = source_one.model_dump(mode="json", exclude={"id", "annotations"})
    accepted_payload["validator"] = {
        "validator_id": "foreign-validator",
        "semantic_version": "source-analysis-1",
    }
    accepted = ValidationReport(
        id=_id("report", accepted_payload),
        **accepted_payload,
    )
    validation.validate_source_report_semantics(accepted)

    rejected_payload = accepted.model_dump(mode="json", exclude={"id", "annotations"})
    rejected_payload["validator"] = {
        "validator_id": "foreign-validator",
        "semantic_version": "unsupported-1",
    }
    rejected = ValidationReport(
        id=_id("report", rejected_payload),
        **rejected_payload,
    )

    with pytest.raises(ValueError):
        validation.validate_source_report_semantics(rejected)


def test_permission_gate_rejects_foreign_source_check_even_when_all_mandatory_checks_exist():
    """A completed foreign check cannot expand the closed source-analysis catalogue."""
    (
        analysis,
        source_one,
        _source_two,
        _compiled,
        approval_one,
        _approval_two,
        bundle,
        material,
    ) = _permission_fixture()
    foreign = ReportCheck(
        check_id="foreign_check",
        scope=_scope(),
        status="passed",
        complete=True,
        finding_ids=(),
    )
    payload = source_one.model_dump(mode="json", exclude={"id", "annotations"})
    payload["checks"].append(foreign.model_dump(mode="json"))
    payload["checks"].sort(key=canonical_bytes)
    report = ValidationReport(id=_id("report", payload), **payload)
    approval_payload = approval_one.model_dump(
        mode="json", exclude={"id", "annotations"}
    )
    approval_payload["report_id"] = report.id
    approval = WarningApproval(id=_id("approval", approval_payload), **approval_payload)
    replaced = bundle.model_copy(
        update={
            "validation": MappingProxyType(
                {
                    **dict(bundle.validation),
                    "reports": tuple(
                        report if item.id == source_one.id else item
                        for item in bundle.validation["reports"]
                    ),
                    "approvals": tuple(
                        approval if item.id == approval_one.id else item
                        for item in bundle.validation["approvals"]
                    ),
                }
            )
        }
    )
    value = ValidatedBuildInput.model_construct(
        schema_version=1,
        analysis_input_id=analysis.id,
        source_report_id=report.id,
        approval_ids=(approval.id,),
        bundle=replaced,
    )

    with pytest.raises(ValueError):
        validate_build_permission(value, material)


def test_source_policy_preflight_rejects_unavailable_coverage_before_geometry():
    """All activated coverage policy entries are admitted before geometry."""
    (
        analysis,
        _source_one,
        _source_two,
        _compiled,
        _approval_one,
        _approval_two,
        _bundle,
        _material,
    ) = _permission_fixture()
    scope_payload = _scope().model_dump(mode="json")
    unavailable_scope = Scope(
        partition_refs=scope_payload["partition_refs"],
        domain_refs=("unavailable",),
        profile_refs=(),
    )
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="unavailable-coverage",
        required_checks=(),
        coverage_requirements=(
            CoverageRequirement(
                requirement_id="unavailable",
                domain_ref="unavailable",
                region_predicate_id=_id("predicate"),
                scope=unavailable_scope,
                severity="warning",
            ),
        ),
        diagnostic_rules=(
            DiagnosticRule(
                stage="source",
                check_id="coverage",
                code="coverage_gap",
                scope=unavailable_scope,
                severity="warning",
                witness_kind="none",
                max_witnesses=0,
            ),
        ),
    )
    invalid = analysis.model_copy(update={"validation_policy": policy})

    with pytest.raises(ValueError):
        produce_source_report(
            invalid,
            lambda _partition: pytest.fail("policy preflight requested geometry"),
            (),
            dimension_fields={},
            partition_refs=_scope().partition_refs,
            source_counts=(0,),
        )


def test_source_policy_preflight_rejects_downgraded_definite_outcome_before_geometry():
    """Definite-outcome counterexamples are errors before proof work begins."""
    (
        analysis,
        _source_one,
        _source_two,
        _compiled,
        _approval_one,
        _approval_two,
        _bundle,
        _material,
    ) = _permission_fixture()
    _envelope, base = _contract_envelope()
    definite = base.model_copy(
        update={
            "profiles": (
                ResolutionProfile(
                    profile_id="profile",
                    mode="resolve",
                    output_fields=("amount",),
                    provenance="none",
                    dimensions=("x",),
                    allow_dont_care=(),
                    promise="definite_outcome",
                    on_unresolved="reject",
                ),
            )
        }
    )
    policy = ValidationPolicy(
        schema_version=1,
        policy_id="definite",
        required_checks=(),
        coverage_requirements=(),
        diagnostic_rules=tuple(
            sorted(
                (
                    DiagnosticRule(
                        stage="source",
                        check_id="routing",
                        code="routing_gap",
                        scope=_scope(),
                        severity="error",
                        witness_kind="none",
                        max_witnesses=0,
                    ),
                    DiagnosticRule(
                        stage="source",
                        check_id="routing",
                        code="routing_ambiguity",
                        scope=_scope(),
                        severity="error",
                        witness_kind="none",
                        max_witnesses=0,
                    ),
                    DiagnosticRule(
                        stage="source",
                        check_id="profiles",
                        code="profile_counterexample",
                        scope=_scope(),
                        severity="warning",
                        witness_kind="none",
                        max_witnesses=0,
                    ),
                ),
                key=lambda item: canonical_bytes(item.model_dump(mode="json")),
            )
        ),
    )
    invalid = analysis.model_copy(update={"validation_policy": policy})

    with pytest.raises(ValueError):
        produce_source_report(
            invalid,
            lambda _partition: pytest.fail("policy preflight requested geometry"),
            (definite,),
            dimension_fields={"x": "x"},
            partition_refs=_scope().partition_refs,
            source_counts=(0,),
        )
