"""Deterministic source declarations and explicit review decisions; never proof results."""

import json
from collections.abc import Mapping

import mountainash_rules as rules


def limits(**overrides):
    values = dict(
        language=dict(
            max_input_bytes=100_000,
            max_nesting=64,
            max_nfa_states=4096,
            max_states=4096,
            max_transitions=65_536,
            max_work=1_000_000,
        ),
        max_input_bytes=10_000_000,
        max_output_bytes=10_000_000,
        max_work=10**12,
        max_live_bytes=100_000_000,
        max_predicate_nodes=100_000,
        max_dfa_states=100_000,
        max_dfa_transitions=1_000_000,
        max_theory_states=1_000_000,
        max_regions=100_000,
        max_scopes=10_000,
        max_source_scope_edges=100_000,
        max_contributor_edges=100_000,
        max_word_rows=100_000,
        max_numeric_bits=100_000,
        max_witnesses=100_000,
    )
    values.update(overrides)
    return rules.ExactLimits(**values)


def ordered(records):
    return sorted(
        records,
        key=lambda item: json.dumps(
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    )


def _mutable(value):
    if isinstance(value, Mapping):
        return {key: _mutable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable(item) for item in value]
    return value


def envelope(kind, payload):
    return _mutable(rules.make_exact_envelope(kind, payload, limits=limits()))


def scalar(value):
    return {"type": "int", "value": str(value)}


def predicate(node):
    return envelope("predicate", {"schema_version": 1, "node": node})


def row(number, lo=0, hi=20, amount=1):
    return {
        "id": f"00000000-0000-0000-0000-{number:012d}",
        "lo": lo,
        "hi": hi,
        "amount": amount,
    }


def case(*, contracts=(), coverage=False, witnesses=1, extra_field=False):
    fields = [rules.DomainField(name="x", data_type="int")]
    if extra_field:
        fields.append(rules.DomainField(name="y", data_type="int"))
    region = predicate(
        dict(
            op="interval",
            field="x",
            lower=scalar(0),
            upper=scalar(20),
            lower_closed=True,
            upper_closed=True,
        )
    )
    domain = rules.DomainDefinition(
        schema_version=1,
        domain_id="D",
        fields=fields,
        predicate_id=region["id"],
    )
    routing = envelope(
        "routing",
        dict(
            schema_version=1,
            semantics="exact-key-1",
            key_dimensions=[],
            partition_keys=[[]],
        ),
    )
    partition = {"routing_id": routing["id"], "key_values": []}
    profile_refs = ordered(
        [
            {"contract_id": contract.contract_id, "profile_id": profile.profile_id}
            for contract in contracts
            for profile in contract.profiles
        ]
    )
    scope = rules.Scope(partition_refs=[partition], domain_refs=["D"], profile_refs=[])
    full_scope = rules.Scope(
        partition_refs=[partition],
        domain_refs=["D"],
        profile_refs=profile_refs,
    )
    diagnostics = [
        rules.DiagnosticRule(
            stage="source",
            check_id=check,
            code=code,
            scope=scope,
            severity="warning",
            witness_kind=kind,
            max_witnesses=witnesses if kind != "none" else 0,
        )
        for check, code, kind in [
            ("source_predicates", "unreachable_source", "none"),
            ("source_overlaps", "source_overlap", "point"),
            ("source_overlaps", "duplicate_source", "point"),
            ("source_overlaps", "singleton_boundary_overlap", "point"),
        ]
    ]
    requirements = []
    if coverage:
        requirements.append(
            rules.CoverageRequirement(
                requirement_id="whole",
                domain_ref="D",
                region_predicate_id=region["id"],
                scope=scope,
                severity="warning",
            )
        )
        diagnostics.append(
            rules.DiagnosticRule(
                stage="source",
                check_id="coverage",
                code="coverage_gap",
                scope=scope,
                severity="warning",
                witness_kind="point",
                max_witnesses=witnesses,
            )
        )
    for contract in contracts:
        contract_scope = rules.Scope(
            partition_refs=[partition],
            domain_refs=[contract.domain_ref],
            profile_refs=ordered(
                [
                    {"contract_id": contract.contract_id, "profile_id": p.profile_id}
                    for p in contract.profiles
                ]
            ),
        )
        for code in ("routing_gap", "routing_ambiguity"):
            diagnostics.append(
                rules.DiagnosticRule(
                    stage="source",
                    check_id="routing",
                    code=code,
                    scope=contract_scope,
                    severity="error",
                    witness_kind="none",
                    max_witnesses=0,
                )
            )
        for profile in contract.profiles:
            if profile.mode == "resolve":
                diagnostics.append(
                    rules.DiagnosticRule(
                        stage="source",
                        check_id="profiles",
                        code="profile_counterexample",
                        scope=rules.Scope(
                            partition_refs=[partition],
                            domain_refs=[contract.domain_ref],
                            profile_refs=[
                                {
                                    "contract_id": contract.contract_id,
                                    "profile_id": profile.profile_id,
                                }
                            ],
                        ),
                        severity="error"
                        if profile.promise == "definite_outcome"
                        else "warning",
                        witness_kind="pair",
                        max_witnesses=witnesses,
                    )
                )
    metadata = rules.DimensionsMetadata(
        dimensions=[
            rules.Dimension(
                dimension_name="x",
                data_type="int",
                match_strategy="range",
                range_min_field="lo",
                range_max_field="hi",
            )
        ],
        context_contracts=list(contracts),
    )
    policy = rules.ValidationPolicy(
        schema_version=1,
        policy_id="explicit-review",
        required_checks=[],
        coverage_requirements=requirements,
        diagnostic_rules=ordered(diagnostics),
    )
    return dict(
        metadata=metadata,
        aggregates=[
            rules.Aggregate(
                column_name="amount",
                output_name="pricing.total",
                data_type="int",
                numeric_semantics="numeric-1",
            )
        ],
        ruleset_id="pricing",
        source_id_field="id",
        compilation_domain_ref="D",
        domains=[domain],
        predicates=[region],
        languages=[],
        routing=routing,
        validation_policy=policy,
        limits=limits(),
    ), full_scope


def string_case(strategy):
    """A source declaration whose string strategy lowers to a graph language."""
    kwargs, scope = case()
    domain_predicate = predicate({"op": "true"})
    kwargs["domains"] = [
        rules.DomainDefinition(
            schema_version=1,
            domain_id="D",
            fields=[rules.DomainField(name="name", data_type="str")],
            predicate_id=domain_predicate["id"],
        )
    ]
    dimension = rules.Dimension(
        dimension_name="name",
        data_type="str",
        match_strategy=strategy,
        regex_pattern="^pre" if strategy == "context_regex" else None,
    )
    kwargs["metadata"] = rules.DimensionsMetadata(dimensions=[dimension])
    kwargs["predicates"] = [domain_predicate]
    if strategy in {"regex", "context_regex"}:
        kwargs["regex_options"] = rules.RegexOptions()
    return kwargs, scope


def contract(
    *,
    promise="allow_unresolved",
    required=False,
    provenance="none",
    mode="resolve",
    masked=True,
    contract_id="client",
):
    return rules.ContextContract(
        schema_version=1,
        contract_id=contract_id,
        domain_ref="D",
        fields=[rules.ContextField(name="x", data_type="int", required=required)],
        profiles=[
            rules.ResolutionProfile(
                profile_id="quote",
                mode=mode,
                output_fields=["pricing.total"],
                provenance=provenance,
                dimensions=["x"],
                allow_dont_care=["x"] if masked else [],
                promise=promise,
                on_unresolved="return" if mode == "resolve" else None,
            )
        ],
    )


def approve(bundle, warning_ids, *, report=None, scope=None):
    """An explicit caller decision over the supplied IDs, never automatic approval."""
    report = report or bundle.validation["reports"][0]
    payload = dict(
        schema_version=1,
        analysis_input_id=report.analysis_input_id,
        report_id=report.id,
        authority_ref="fixture-review-policy",
        actor_ref="fixture-reviewer",
        decision="approve_warnings",
        scope=(scope or report.scope).model_dump(mode="json"),
        warning_ids=sorted(warning_ids),
    )
    record = envelope("approval", payload)
    return rules.WarningApproval.model_validate(
        {"id": record["id"], **record["payload"]}
    )


def gate(rows, kwargs, bundle, approvals=()):
    report = bundle.validation["reports"][0]
    return rules.validate_build_input(
        rows,
        bundle=bundle,
        analysis_input_id=report.analysis_input_id,
        source_report_id=report.id,
        approvals=approvals,
        **kwargs,
    )


def partition_case(*, default=True, profile_coverage=True):
    kwargs, _ = case()
    key_dimension = dict(
        dimension_name="key",
        context_field="key",
        rule_field="key",
        match_strategy="exact_key",
        data_type="int",
        role="context_key",
        range_min_field=None,
        range_max_field=None,
        range_min_inclusive=None,
        range_max_inclusive=None,
        regex_pattern=None,
    )
    keys = [[{"dimension_name": "key", "match": {"kind": "value", "value": scalar(1)}}]]
    if default:
        keys.append([{"dimension_name": "key", "match": {"kind": "wildcard"}}])
    routing = envelope(
        "routing",
        dict(
            schema_version=1,
            semantics="exact-key-1",
            key_dimensions=[key_dimension],
            partition_keys=ordered(keys),
        ),
    )
    key_region = predicate(
        dict(
            op="interval",
            field="key",
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
                key_region["id"],
                kwargs["predicates"][0]["id"],
            ],
        )
    )
    kwargs["predicates"] += [key_region, region]
    fields = [
        rules.DomainField(name="key", data_type="int"),
        rules.DomainField(name="x", data_type="int"),
    ]
    kwargs["domains"] = [
        rules.DomainDefinition(
            schema_version=1,
            domain_id="D",
            fields=fields,
            predicate_id=region["id"],
        )
    ]
    contracts = [
        rules.ContextContract(
            schema_version=1,
            contract_id=name,
            domain_ref="D",
            fields=[
                rules.ContextField(name="key", data_type="int", required=required),
                rules.ContextField(name="x", data_type="int", required=True),
            ],
            profiles=[
                rules.ResolutionProfile(
                    profile_id="quote",
                    mode="candidates",
                    output_fields=["pricing.total"],
                    provenance="none",
                    dimensions=["x"],
                    allow_dont_care=[],
                    promise="candidate_only",
                )
            ],
        )
        for name, required in (("optional", False), ("required", True))
    ]
    kwargs["metadata"] = rules.DimensionsMetadata(
        dimensions=[
            rules.Dimension(
                dimension_name="key",
                data_type="int",
                match_strategy="exact_key",
                role="context_key",
            ),
            *kwargs["metadata"].dimensions,
        ],
        context_contracts=contracts,
    )
    kwargs["routing"] = routing
    partitions = ordered(
        [{"routing_id": routing["id"], "key_values": key} for key in keys]
    )
    profiles = [
        {"contract_id": c.contract_id, "profile_id": "quote"} for c in contracts
    ]
    global_scope = rules.Scope(
        partition_refs=partitions, domain_refs=["D"], profile_refs=[]
    )
    full_scope = rules.Scope(
        partition_refs=partitions, domain_refs=["D"], profile_refs=profiles
    )
    diagnostic_rules = [
        rules.DiagnosticRule(
            stage="source",
            check_id=check,
            code=code,
            scope=global_scope,
            severity="warning",
            witness_kind="none",
            max_witnesses=0,
        )
        for check, code in [
            ("source_predicates", "unreachable_source"),
            ("source_overlaps", "source_overlap"),
            ("source_overlaps", "duplicate_source"),
            ("source_overlaps", "singleton_boundary_overlap"),
            ("coverage", "coverage_gap"),
        ]
    ]
    for c in contracts:
        scope = rules.Scope(
            partition_refs=partitions,
            domain_refs=["D"],
            profile_refs=[{"contract_id": c.contract_id, "profile_id": "quote"}],
        )
        diagnostic_rules += [
            rules.DiagnosticRule(
                stage="source",
                check_id="routing",
                code=code,
                scope=scope,
                severity="error",
                witness_kind="none",
                max_witnesses=0,
            )
            for code in ("routing_gap", "routing_ambiguity")
        ]
    required_checks = [
        rules.RequiredCheck(stage="source", check_id="coverage", scope=global_scope)
    ]
    coverage = [
        rules.CoverageRequirement(
            requirement_id="global",
            domain_ref="D",
            region_predicate_id=region["id"],
            scope=global_scope,
            severity="warning",
        )
    ]
    if profile_coverage:
        required_checks.append(
            rules.RequiredCheck(stage="source", check_id="coverage", scope=full_scope)
        )
        coverage.append(
            rules.CoverageRequirement(
                requirement_id="profiles",
                domain_ref="D",
                region_predicate_id=region["id"],
                scope=full_scope,
                severity="warning",
            )
        )
    kwargs["validation_policy"] = rules.ValidationPolicy(
        schema_version=1,
        policy_id="partition-review",
        required_checks=ordered(required_checks),
        coverage_requirements=coverage,
        diagnostic_rules=ordered(diagnostic_rules),
    )
    return kwargs
