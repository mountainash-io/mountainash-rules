"""Stateless Phase 3 source-evidence orchestration."""

from __future__ import annotations

import json
import sys
from types import MappingProxyType
import typing as t

from mountainash_rules.core.codec import (
    _bounded_json_size,
    _materialize_json,
    canonical_bytes,
)
from mountainash_rules.core.constants import DimensionRole, MatchStrategy
from mountainash_rules.core.contracts import (
    ContextContract,
    DomainDefinition,
    ExactLimits,
    OperationBudget,
    ValidatedBuildInput,
    ValidationBundle,
    ValidationPolicy,
    WarningApproval,
)
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.language import RegexOptions
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.validation import (
    _material_matches,
    produce_source_report,
    validate_build_permission,
    validate_source_policy,
    validate_source_report_semantics,
    verify_source_point,
    verify_witness,
)
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.engines.accumulator.compiler import (
    analysis_geometry,
    analyze_sources as _analyze_partition,
    prepare_analysis_input,
    prepare_sources,
)


def _snapshot_metadata(
    metadata: DimensionsMetadata, budget: OperationBudget
) -> DimensionsMetadata:
    if not isinstance(metadata, DimensionsMetadata):
        raise TypeError("metadata must be DimensionsMetadata")
    size = _bounded_json_size(metadata, budget, phase="metadata.snapshot")
    budget.reserve(
        "max_input_bytes",
        size,
        phase="metadata.snapshot",
        units="maximum canonical metadata bytes",
    )
    budget.reserve(
        "max_live_bytes",
        size,
        phase="metadata.snapshot",
        units="retained metadata snapshot bytes",
    )
    # A reconstructed instance catches post-construction list reassignment.
    return DimensionsMetadata.model_validate(metadata.model_dump(mode="python"))


def _domain_registry(
    domains: t.Sequence[DomainDefinition], compilation_domain_ref: str
) -> dict[str, DomainDefinition]:
    if not isinstance(domains, t.Sequence) or any(
        not isinstance(domain, DomainDefinition) for domain in domains
    ):
        raise TypeError("domains must contain DomainDefinition records")
    registry = {domain.domain_id: domain for domain in domains}
    if len(registry) != len(domains) or compilation_domain_ref not in registry:
        raise ValueError("domains must uniquely contain compilation_domain_ref")
    return registry


def _prepare(
    rows: t.Iterable[t.Mapping[str, t.Any]],
    *,
    metadata: DimensionsMetadata,
    aggregates: t.Sequence[Aggregate],
    ruleset_id: str,
    source_id_field: str,
    compilation_domain_ref: str,
    domains: t.Sequence[DomainDefinition],
    predicates: t.Sequence[t.Mapping[str, t.Any]],
    languages: t.Sequence[t.Mapping[str, t.Any]],
    routing: t.Mapping[str, t.Any],
    validation_policy: ValidationPolicy,
    budget: OperationBudget,
    source_label_field: str | None,
    regex_options: RegexOptions | None,
) -> tuple[
    t.Any,
    dict[str, DomainDefinition],
    tuple[ContextContract, ...],
    t.Callable[[t.Mapping[str, t.Any]], t.Any],
    tuple[t.Mapping[str, t.Any], ...],
    tuple[int, ...],
    t.Any,
    t.Any,
    dict[str, str],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Perform the common strict current-input preparation under one budget."""
    if not isinstance(validation_policy, ValidationPolicy):
        raise TypeError("validation_policy must be ValidationPolicy")
    snapshot = _snapshot_metadata(metadata, budget)
    registry = _domain_registry(domains, compilation_domain_ref)
    if not isinstance(predicates, t.Sequence) or not isinstance(languages, t.Sequence):
        raise TypeError("predicates and languages must be envelope sequences")
    graph = PredicateGraph(registry[compilation_domain_ref].fields, budget=budget)
    # This admits every submitted graph node and language before any output closure.
    graph.decode(tuple(predicates), tuple(languages))
    prepared = prepare_sources(
        rows,
        graph=graph,
        domain=registry[compilation_domain_ref],
        metadata=snapshot,
        aggregates=aggregates,
        ruleset_id=ruleset_id,
        source_id_field=source_id_field,
        source_label_field=source_label_field,
        routing=routing,
        regex_options=regex_options,
    )
    # Context regex guards become part of the actual compilation entry.
    registry = dict(registry)
    registry[compilation_domain_ref] = prepared.domain
    contracts = tuple(
        sorted(snapshot.context_contracts, key=lambda item: item.contract_id)
    )
    analysis_input, material = prepare_analysis_input(
        prepared,
        compilation_domain_ref=compilation_domain_ref,
        domains=registry,
        contracts=contracts,
        validation_policy=validation_policy,
    )
    partition_refs = tuple(
        {
            "routing_id": prepared.routing["id"],
            "key_values": [dict(key) for key in keys],
        }
        for keys in prepared.routing["payload"]["partition_keys"]
    )
    source_counts_by_routing: dict[bytes, int] = {}
    for source in prepared.sources:
        key = canonical_bytes({"key_values": source.routing_values})
        source_counts_by_routing[key] = source_counts_by_routing.get(key, 0) + 1
    source_counts = tuple(
        source_counts_by_routing.get(
            canonical_bytes({"key_values": item["key_values"]}), 0
        )
        for item in partition_refs
    )

    def geometry_for(partition_ref: t.Mapping[str, t.Any]) -> t.Any:
        partition = _analyze_partition(
            prepared,
            key_values=t.cast(
                t.Sequence[t.Mapping[str, t.Any]], partition_ref["key_values"]
            ),
        )
        return analysis_geometry(partition, provider_domains=registry)

    dimension_fields = {
        dimension.dimension_name: dimension.resolved_context_field
        for dimension in snapshot.dimensions
        if dimension.role is DimensionRole.CONSTRAINT
        and dimension.match_strategy is not MatchStrategy.CONTEXT_REGEX
    }
    guard_fields = tuple(
        dimension.resolved_context_field
        for dimension in snapshot.dimensions
        if dimension.match_strategy is MatchStrategy.CONTEXT_REGEX
    )
    ordered_fields = tuple(
        dimension.resolved_context_field
        for dimension in snapshot.dimensions
        if dimension.match_strategy
        in {MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN}
    )
    return (
        prepared,
        registry,
        contracts,
        geometry_for,
        partition_refs,
        source_counts,
        analysis_input,
        material,
        dimension_fields,
        guard_fields,
        ordered_fields,
    )


def _contract_envelopes(
    contracts: t.Sequence[ContextContract], budget: OperationBudget
) -> tuple[t.Mapping[str, t.Any], ...]:
    from mountainash_rules.core.codec import _make_exact_envelope

    return tuple(
        _make_exact_envelope(
            "contract",
            {
                "schema_version": 1,
                "contract": contract.model_dump(mode="json", exclude_none=True),
            },
            budget=budget,
        )
        for contract in sorted(contracts, key=lambda item: item.contract_id)
    )


def _retained_predicate_envelopes(
    prepared: t.Any,
    domains: t.Mapping[str, DomainDefinition],
    analysis_input: t.Any,
    findings: t.Sequence[t.Any],
    budget: OperationBudget,
) -> tuple[tuple[t.Mapping[str, t.Any], ...], tuple[t.Mapping[str, t.Any], ...]]:
    """Serialize only the predicate and language closure referenced by evidence."""
    from mountainash_rules.core.codec import _make_exact_envelope

    roots = {domain.predicate_id for domain in domains.values()}
    roots.update(
        origin["predicate_id"]
        for source in prepared.sources
        for origin in source.origins
    )
    roots.update(
        finding.region_predicate_id
        for finding in findings
        if finding.region_predicate_id is not None
    )
    roots.update(
        requirement.region_predicate_id
        for requirement in analysis_input.validation_policy.coverage_requirements
    )
    retained: set[str] = set()
    languages: set[str] = set()
    pending = list(roots)
    while pending:
        identifier = pending.pop()
        if identifier in retained:
            continue
        node = prepared.graph.nodes.get(identifier)
        if node is None:
            raise ValueError("retained evidence references an unavailable predicate")
        retained.add(identifier)
        if node["op"] in {"and", "or"}:
            pending.extend(node["args"])
        elif node["op"] == "not":
            pending.append(node["arg"])
        elif node["op"] == "language":
            languages.add(node["language_id"])
    predicate_envelopes = tuple(
        _make_exact_envelope(
            "predicate",
            {
                "schema_version": 1,
                "node": _materialize_json(prepared.graph.nodes[item]),
            },
            budget=budget,
        )
        for item in sorted(retained)
    )
    language_envelopes = tuple(
        _make_exact_envelope(
            "language",
            json.loads(
                prepared.graph.languages[item].to_json(
                    limits=prepared.graph.language_limits
                )
            ),
            budget=budget,
        )
        for item in sorted(languages)
    )
    return predicate_envelopes, language_envelopes


def _bundle(
    prepared: t.Any,
    *,
    domains: t.Mapping[str, DomainDefinition],
    contracts: t.Sequence[ContextContract],
    analysis_input: t.Any,
    findings: t.Sequence[t.Any],
    report: t.Any,
    budget: OperationBudget,
) -> ValidationBundle:
    """Build the frozen source-only portable bundle from admitted material."""
    from mountainash_rules.core.codec import (
        _admit_validation_bundle,
        _make_exact_envelope,
    )

    predicate_envelopes, language_envelopes = _retained_predicate_envelopes(
        prepared, domains, analysis_input, findings, budget
    )
    domain_envelopes = tuple(
        _make_exact_envelope(
            "domain", domain.model_dump(mode="json", exclude_none=True), budget=budget
        )
        for _, domain in sorted(domains.items())
    )
    source_bundle = _make_exact_envelope(
        "source-bundle",
        {
            "schema_version": 1,
            "ruleset_id": prepared.ruleset_id,
            "sources": [
                {"source_id": source.source_id, "content_id": source.content_id}
                for source in prepared.sources
            ],
        },
        budget=budget,
    )
    origins = sorted(
        (
            _materialize_json(origin)
            for source in prepared.sources
            for origin in source.origins
        ),
        key=lambda origin: (origin["source_id"], origin["dimension_name"]),
    )
    payload = {
        "schema_version": 1,
        "metadata": _materialize_json(prepared.metadata),
        "aggregates": _materialize_json(prepared.aggregate_record),
        "routing": _materialize_json(prepared.routing),
        "context_contracts": _materialize_json(_contract_envelopes(contracts, budget)),
        "predicates": {
            "schema_version": 1,
            "predicates": _materialize_json(
                sorted(predicate_envelopes, key=lambda item: item["id"])
            ),
            "languages": _materialize_json(
                sorted(language_envelopes, key=lambda item: item["id"])
            ),
            "domains": _materialize_json(
                sorted(domain_envelopes, key=lambda item: item["id"])
            ),
            "source_bundles": _materialize_json([source_bundle]),
            "source_origins": origins,
            "source_labels": _materialize_json(prepared.source_labels),
        },
        "validation": {
            "schema_version": 1,
            "analysis_inputs": (analysis_input,),
            "findings": tuple(sorted(findings, key=lambda item: item.id)),
            "reports": (report,),
            "approvals": (),
            "bindings": (),
        },
    }
    bundle = ValidationBundle.model_validate(payload, context={"budget": budget})
    return _admit_validation_bundle(bundle, budget=budget)


def analyze_sources(
    rows: t.Iterable[t.Mapping[str, t.Any]],
    *,
    metadata: DimensionsMetadata,
    aggregates: t.Sequence[Aggregate],
    ruleset_id: str,
    source_id_field: str,
    compilation_domain_ref: str,
    domains: t.Sequence[DomainDefinition],
    predicates: t.Sequence[t.Mapping[str, t.Any]],
    languages: t.Sequence[t.Mapping[str, t.Any]],
    routing: t.Mapping[str, t.Any],
    validation_policy: ValidationPolicy,
    limits: ExactLimits,
    source_label_field: str | None = None,
    regex_options: RegexOptions | None = None,
) -> ValidationBundle:
    """Produce all source diagnostics from actual prepared partition geometry."""
    if not isinstance(limits, ExactLimits):
        raise TypeError("limits must be ExactLimits")
    if not isinstance(validation_policy, ValidationPolicy):
        raise TypeError("validation_policy must be ValidationPolicy")
    budget = OperationBudget(limits, "analyze_sources")
    (
        prepared,
        registry,
        contracts,
        geometry_for,
        partition_refs,
        source_counts,
        analysis_input,
        _material,
        dimension_fields,
        guard_fields,
        ordered_fields,
    ) = _prepare(
        rows,
        metadata=metadata,
        aggregates=aggregates,
        ruleset_id=ruleset_id,
        source_id_field=source_id_field,
        compilation_domain_ref=compilation_domain_ref,
        domains=domains,
        predicates=predicates,
        languages=languages,
        routing=routing,
        validation_policy=validation_policy,
        budget=budget,
        source_label_field=source_label_field,
        regex_options=regex_options,
    )
    findings, report = produce_source_report(
        analysis_input,
        geometry_for,
        contracts,
        dimension_fields=dimension_fields,
        partition_refs=partition_refs,
        source_counts=source_counts,
        guard_fields=guard_fields,
        ordered_fields=ordered_fields,
    )
    return _bundle(
        prepared,
        domains=registry,
        contracts=contracts,
        analysis_input=analysis_input,
        findings=findings,
        report=report,
        budget=budget,
    )


def _attach(
    bundle: ValidationBundle,
    approvals: t.Sequence[WarningApproval],
    budget: OperationBudget,
) -> ValidationBundle:
    from mountainash_rules.core.codec import _admit_validation_bundle
    from mountainash_rules.core.contracts import (
        _validate_approval_links,
        _validate_scope_material,
    )

    admitted = _admit_validation_bundle(bundle, budget=budget)
    if not isinstance(approvals, t.Sequence) or any(
        not isinstance(item, WarningApproval) for item in approvals
    ):
        raise TypeError("approvals must contain WarningApproval records")

    phase = "approval_attachment"
    for approval in approvals:
        size = _bounded_json_size(approval, budget, phase=phase)
        budget.reserve(
            "max_input_bytes",
            size,
            phase=phase,
            units="submitted approval canonical bytes",
        )

    validation = admitted.validation
    prior_count = len(validation["approvals"])
    supplied_count = len(approvals)
    partition_keys = admitted.routing["payload"]["partition_keys"]
    contract_count = len(admitted.context_contracts)
    profile_count = sum(
        len(envelope["payload"]["contract"]["profiles"])
        for envelope in admitted.context_contracts
    )
    routing_key_bytes = sum(
        _bounded_json_size(
            {"key_values": key_values},
            budget,
            phase=phase,
            counter="max_live_bytes",
        )
        for key_values in partition_keys
    )
    reference_bytes = sys.getsizeof((None,)) - sys.getsizeof(())
    dict_entry_bytes = sys.getsizeof({None: None})
    set_entry_bytes = sys.getsizeof({None})
    result_bytes = (
        len(validation) * dict_entry_bytes
        + sys.getsizeof(())
        + (prior_count + supplied_count) * reference_bytes
        + sys.getsizeof(admitted)
        + sys.getsizeof(admitted.__dict__)
        + sys.getsizeof(admitted.__pydantic_fields_set__)
        + sys.getsizeof(MappingProxyType({}))
    )
    state_bytes = (
        8 * sys.getsizeof({})
        + (
            2 * (prior_count + supplied_count)
            + len(validation["analysis_inputs"])
            + len(validation["reports"])
            + len(validation["findings"])
            + contract_count
        )
        * dict_entry_bytes
        + sys.getsizeof(set())
        + len(partition_keys) * set_entry_bytes
        + contract_count * sys.getsizeof(frozenset())
        + profile_count * set_entry_bytes
        + sys.getsizeof([])
        + (prior_count + supplied_count) * reference_bytes
        + sys.getsizeof(())
        + (prior_count + supplied_count) * reference_bytes
        + routing_key_bytes
        + result_bytes
    )
    state = budget.reserve(
        "max_live_bytes",
        state_bytes,
        phase=phase,
        units="approval indexes, result references, and routing key bytes",
    )
    result_retained = False
    try:
        supplied = {item.id: item for item in approvals}
        if len(supplied) != supplied_count:
            raise ValueError("supplied approvals must have unique IDs")
        existing = {item.id: item for item in validation["approvals"]}
        added = tuple(
            approval
            for identifier, approval in supplied.items()
            if identifier not in existing
        )
        for identifier, approval in supplied.items():
            prior = existing.get(identifier)
            if prior is not None and prior != approval:
                raise ValueError("conflicting approval ID")
        if not added:
            return admitted

        for approval in added:
            existing[approval.id] = approval
        analyses = {item.id: item for item in validation["analysis_inputs"]}
        reports = {item.id: item for item in validation["reports"]}
        findings = {item.id: item for item in validation["findings"]}
        routing_partition_keys = {
            canonical_bytes({"key_values": key_values}) for key_values in partition_keys
        }
        profiles_by_contract = {
            envelope["payload"]["contract"]["contract_id"]: frozenset(
                profile["profile_id"]
                for profile in envelope["payload"]["contract"]["profiles"]
            )
            for envelope in admitted.context_contracts
        }
        merged = tuple(sorted(existing.values(), key=lambda item: item.id))

        scope_bytes = 0
        for approval in added:
            approval_scope_size = _bounded_json_size(
                approval.scope,
                budget,
                phase=phase,
                counter="max_live_bytes",
            )
            scope_bytes += approval_scope_size * (1 + len(approval.warning_ids))
            for finding_id in approval.warning_ids:
                finding = findings.get(finding_id)
                if finding is not None:
                    scope_bytes += _bounded_json_size(
                        finding.scope,
                        budget,
                        phase=phase,
                        counter="max_live_bytes",
                    )
        scopes = budget.reserve(
            "max_live_bytes",
            scope_bytes,
            phase=phase,
            units="scope and profile canonical validation bytes",
        )
        try:
            for approval in added:
                analysis = analyses.get(approval.analysis_input_id)
                if analysis is None:
                    raise ValueError(
                        "WarningApproval refers to unresolved AnalysisInput"
                    )
                _validate_scope_material(
                    approval.scope,
                    analysis,
                    routing_partition_keys,
                    profiles_by_contract,
                    "WarningApproval scope",
                )
            _validate_approval_links(added, reports, findings)
        finally:
            budget.release(scopes.counter, scopes.amount)

        result_validation = {**validation, "approvals": merged}
        result_payload = {
            "schema_version": admitted.schema_version,
            "metadata": admitted.metadata,
            "aggregates": admitted.aggregates,
            "routing": admitted.routing,
            "context_contracts": admitted.context_contracts,
            "predicates": admitted.predicates,
            "validation": result_validation,
        }
        output_size = _bounded_json_size(
            result_payload,
            budget,
            phase=phase,
            counter="max_output_bytes",
        )
        budget.reserve(
            "max_output_bytes",
            output_size,
            phase=phase,
            units="retained changed validation bundle canonical bytes",
        )
        result = ValidationBundle.model_construct(
            schema_version=admitted.schema_version,
            metadata=admitted.metadata,
            aggregates=admitted.aggregates,
            routing=admitted.routing,
            context_contracts=admitted.context_contracts,
            predicates=admitted.predicates,
            validation=MappingProxyType(result_validation),
        )
        result_retained = True
        return result
    finally:
        # A returned view remains live during a caller's subsequent permission
        # work. Only scratch capacity is released on successful attachment.
        budget.release(
            state.counter, state.amount - (result_bytes if result_retained else 0)
        )


def attach_warning_approvals(
    bundle: ValidationBundle,
    approvals: t.Sequence[WarningApproval],
    *,
    limits: ExactLimits,
) -> ValidationBundle:
    """Store explicit review decisions without requiring current source material."""
    if not isinstance(limits, ExactLimits):
        raise TypeError("limits must be ExactLimits")
    return _attach(
        bundle, approvals, OperationBudget(limits, "attach_warning_approvals")
    )


def _replay_report(
    bundle: ValidationBundle,
    report: t.Any,
    geometry_for: t.Callable[[t.Mapping[str, t.Any]], t.Any],
    contracts: t.Mapping[str, ContextContract],
    dimension_fields: t.Mapping[str, str],
    guard_fields: t.Iterable[str],
) -> None:
    findings = {item.id: item for item in bundle.validation["findings"]}
    grouped: dict[bytes, list[t.Any]] = {}
    for identifier in report.finding_ids:
        finding = findings[identifier]
        if finding.witnesses:
            grouped.setdefault(
                canonical_bytes(finding.scope.partition_refs[0]), []
            ).append(finding)
    for encoded_partition, partition_findings in grouped.items():
        partition = partition_findings[0].scope.partition_refs[0]
        geometry = geometry_for(partition)
        if canonical_bytes(geometry.partition_identity) != encoded_partition:
            raise ValueError(
                "report finding partition is unavailable from current inputs"
            )
        for finding in partition_findings:
            for witness in finding.witnesses:
                if witness.profile_ref is None:
                    verify_source_point(geometry, finding, witness)
                    continue
                contract = contracts.get(witness.profile_ref["contract_id"])
                if contract is None:
                    raise ValueError(
                        "report witness contract is unavailable from current inputs"
                    )
                profile = next(
                    (
                        item
                        for item in contract.profiles
                        if item.profile_id == witness.profile_ref["profile_id"]
                    ),
                    None,
                )
                if profile is None:
                    raise ValueError(
                        "report witness profile is unavailable from current inputs"
                    )
                try:
                    profile_dimension_fields = {
                        name: dimension_fields[name] for name in profile.dimensions
                    }
                except KeyError as exc:
                    raise ValueError(
                        "report witness profile dimension is unavailable from current inputs"
                    ) from exc
                verify_witness(
                    geometry,
                    contract,
                    witness,
                    dimension_fields=profile_dimension_fields,
                    guard_fields=guard_fields,
                )


def validate_build_input(
    rows: t.Iterable[t.Mapping[str, t.Any]],
    *,
    bundle: ValidationBundle,
    analysis_input_id: str,
    source_report_id: str,
    approvals: t.Sequence[WarningApproval],
    metadata: DimensionsMetadata,
    aggregates: t.Sequence[Aggregate],
    ruleset_id: str,
    source_id_field: str,
    compilation_domain_ref: str,
    domains: t.Sequence[DomainDefinition],
    predicates: t.Sequence[t.Mapping[str, t.Any]],
    languages: t.Sequence[t.Mapping[str, t.Any]],
    routing: t.Mapping[str, t.Any],
    validation_policy: ValidationPolicy,
    limits: ExactLimits,
    source_label_field: str | None = None,
    regex_options: RegexOptions | None = None,
) -> ValidatedBuildInput:
    """Recompute material, replay retained examples, and invoke the real gate."""
    if not isinstance(limits, ExactLimits):
        raise TypeError("limits must be ExactLimits")
    if not isinstance(validation_policy, ValidationPolicy):
        raise TypeError("validation_policy must be ValidationPolicy")
    from mountainash_rules.core.codec import _admit_validation_bundle

    budget = OperationBudget(limits, "validate_build_input")
    admitted = _admit_validation_bundle(bundle, budget=budget)
    reports = {item.id: item for item in admitted.validation["reports"]}
    report = reports.get(source_report_id)
    if report is None or report.analysis_input_id != analysis_input_id:
        raise ValueError("selected source report is unresolved or foreign")
    validate_source_report_semantics(report)
    (
        prepared,
        _registry,
        contracts,
        geometry_for,
        partition_refs,
        source_counts,
        _current_input,
        material,
        dimension_fields,
        guard_fields,
        ordered_fields,
    ) = _prepare(
        rows,
        metadata=metadata,
        aggregates=aggregates,
        ruleset_id=ruleset_id,
        source_id_field=source_id_field,
        compilation_domain_ref=compilation_domain_ref,
        domains=domains,
        predicates=predicates,
        languages=languages,
        routing=routing,
        validation_policy=validation_policy,
        budget=budget,
        source_label_field=source_label_field,
        regex_options=regex_options,
    )
    analysis = next(
        (
            item
            for item in admitted.validation["analysis_inputs"]
            if item.id == analysis_input_id
        ),
        None,
    )
    if analysis is None:
        raise ValueError("selected analysis input is unresolved")
    _material_matches(analysis, material)
    validate_source_policy(
        analysis,
        contracts,
        partition_refs=partition_refs,
        source_counts=source_counts,
        ordered_fields=ordered_fields,
    )
    prepared.graph.decode(
        tuple(admitted.predicates["predicates"]),
        tuple(admitted.predicates["languages"]),
    )
    _replay_report(
        admitted,
        report,
        geometry_for,
        {item.contract_id: item for item in contracts},
        dimension_fields,
        guard_fields,
    )
    attached = _attach(admitted, approvals, budget)
    value = ValidatedBuildInput.model_validate(
        {
            "schema_version": 1,
            "analysis_input_id": analysis_input_id,
            "source_report_id": source_report_id,
            "approval_ids": tuple(sorted(item.id for item in approvals)),
            "bundle": attached,
        },
        context={"budget": budget},
    )
    validate_build_permission(value, material)
    return value
