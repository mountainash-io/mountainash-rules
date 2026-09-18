"""Authored test domains/policies and explicit scoped warning decisions."""

from dataclasses import dataclass
import typing as t

import mountainash_rules as rules
from mountainash_rules.core.codec import canonical_bytes
from mountainash_rules.core.scalar import encode_scalar
from mountainash_rules.engines.accumulator.compiler import _dimension_record
from tests.accumulator.source_analysis_fixtures import (
    approve,
    envelope,
    limits,
    ordered,
    predicate,
)


def uuid(number):
    return f"00000000-0000-0000-0000-{number:012d}"


def declarations(
    dimensions, aggregates=(), *, contracts=(), domain_node=None, partition_keys=((),)
):
    fields = {}
    for dimension in dimensions:
        fields[dimension.resolved_context_field] = rules.DomainField(
            name=dimension.resolved_context_field,
            data_type=dimension.data_type,
            **(
                {"timezone": "naive"}
                if dimension.data_type == rules.DataType.DATETIME
                else {}
            ),
        )
    root = predicate(domain_node or {"op": "true"})
    domain = rules.DomainDefinition(
        schema_version=1,
        domain_id="D",
        fields=[fields[name] for name in sorted(fields)],
        predicate_id=root["id"],
    )
    keys = sorted(
        (d for d in dimensions if d.role == rules.DimensionRole.CONTEXT_KEY),
        key=lambda d: d.dimension_name,
    )
    typed_keys = []
    for values in partition_keys:
        entries = []
        for d, value in zip(keys, values, strict=True):
            wildcard = value is None or value == rules.unknown_sentinel_for(d.data_type)
            entries.append(
                {
                    "dimension_name": d.dimension_name,
                    "match": {"kind": "wildcard"}
                    if wildcard
                    else {
                        "kind": "value",
                        "value": encode_scalar(
                            value,
                            d.data_type,
                            timezone="naive"
                            if d.data_type == rules.DataType.DATETIME
                            else None,
                        ),
                    },
                }
            )
        typed_keys.append(entries)
    typed_keys.sort(key=lambda x: canonical_bytes({"keys": x}))
    routing = envelope(
        "routing",
        dict(
            schema_version=1,
            semantics="exact-key-1",
            key_dimensions=[_dimension_record(d, routing=True) for d in keys],
            partition_keys=typed_keys,
        ),
    )
    partitions = [
        {"routing_id": routing["id"], "key_values": key} for key in typed_keys
    ]
    scope = rules.Scope(partition_refs=partitions, domain_refs=["D"], profile_refs=[])
    diagnostics = [
        rules.DiagnosticRule(
            stage="source",
            check_id=check,
            code=code,
            scope=scope,
            severity="warning",
            witness_kind="none",
            max_witnesses=0,
        )
        for check, code in [
            ("source_predicates", "unreachable_source"),
            ("source_overlaps", "source_overlap"),
            ("source_overlaps", "duplicate_source"),
            ("source_overlaps", "singleton_boundary_overlap"),
        ]
    ]
    for contract in contracts:
        contract_scope = rules.Scope(
            partition_refs=partitions,
            domain_refs=["D"],
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
                            partition_refs=partitions,
                            domain_refs=["D"],
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
                        max_witnesses=0,
                    )
                )
    kwargs = dict(
        metadata=rules.DimensionsMetadata(
            dimensions=list(dimensions), context_contracts=list(contracts)
        ),
        aggregates=list(aggregates),
        ruleset_id="runtime-fixture",
        source_id_field="id",
        compilation_domain_ref="D",
        domains=[domain],
        predicates=[root],
        languages=[],
        routing=routing,
        validation_policy=rules.ValidationPolicy(
            schema_version=1,
            policy_id="runtime-fixture",
            required_checks=[],
            coverage_requirements=[],
            diagnostic_rules=ordered(diagnostics),
        ),
        limits=limits(),
    )
    if any(
        d.match_strategy
        in (rules.MatchStrategy.REGEX, rules.MatchStrategy.CONTEXT_REGEX)
        for d in dimensions
    ):
        kwargs["regex_options"] = rules.RegexOptions()
    return kwargs


def gate(rows, kwargs, *, decisions=()):
    """Each decision names one diagnostic code and its exact source UUID set."""
    bundle = rules.analyze_sources(rows, **kwargs)
    expected = set((code, tuple(sorted(sources))) for code, sources in decisions)
    findings = tuple(bundle.validation["findings"])
    actual = {(f.code, tuple(f.source_ids)) for f in findings}
    assert actual == expected, (actual, expected, rows)
    assert all(f.severity == "warning" for f in findings)
    approvals = [approve(bundle, [f.id], scope=f.scope) for f in findings]
    if approvals:
        bundle = rules.attach_warning_approvals(
            bundle, approvals, limits=kwargs["limits"]
        )
    report = bundle.validation["reports"][0]
    return rules.validate_build_input(
        rows,
        bundle=bundle,
        analysis_input_id=report.analysis_input_id,
        source_report_id=report.id,
        approvals=approvals,
        **kwargs,
    )


def build(
    rows, kwargs, *, decisions=(), segmentation_dimensions=(), partition_key=None
):
    validation = gate(rows, kwargs, decisions=decisions)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"],
        kwargs["aggregates"],
        segmentation_dimensions=segmentation_dimensions,
        limits=kwargs["limits"],
    )
    return engine, engine.build(
        rows, validation=validation, partition_key=partition_key
    )


def memberships(lattice):
    edges = {}
    for row in lattice.contributors.to_dicts():
        edges.setdefault(row["contributor_set_id"], set()).add(row["source_id"])
    return {
        row["cell_id"]: frozenset(edges[row["contributor_set_id"]])
        for row in lattice.combinations.to_dicts()
    }
