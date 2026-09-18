"""Provider-independent exact analysis proofs and portable permission checks.

This module deliberately accepts structural source/cell records.  The accumulator
adapter owns conversion from its private build records; core never imports an
engine implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations, product
from types import MappingProxyType
import typing as t
import uuid

from mountainash_rules.core.codec import (
    _materialize_json,
    canonical_bytes,
    content_id,
    validate_id,
)
from mountainash_rules.core.contracts import (
    AnalysisInput,
    ContextContract,
    ContractBinding,
    CoverageRequirement,
    DiagnosticRule,
    DomainDefinition,
    Finding,
    OperationBudget,
    ReportCheck,
    ResolutionProfile,
    Scope,
    SemanticVersions,
    ValidatedBuildInput,
    ValidationBundle,
    ValidationReport,
    WarningApproval,
    Witness,
    WitnessRequest,
    _scope_covers,
    _scope_covers_nonprofiles,
    _scope_refs,
)
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.reasoner import Reasoner
from mountainash_rules.core.scalar import decode_scalar, encode_scalar


def _freeze_mapping(value: t.Mapping[str, t.Any]) -> t.Mapping[str, t.Any]:
    return MappingProxyType({key: _freeze(item) for key, item in sorted(value.items())})


def _freeze(value: t.Any) -> t.Any:
    if isinstance(value, t.Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _uuid(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a canonical UUID")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a canonical UUID") from exc
    return value


def _predicate(graph: PredicateGraph, identifier: str, name: str) -> str:
    validate_id(identifier, "predicate")
    if identifier not in graph.nodes:
        raise ValueError(f"{name} is unavailable from the predicate graph")
    return identifier


@dataclass(frozen=True, slots=True)
class StructuralSource:
    """A normalized source predicate retained independently of an engine row."""

    source_id: str
    predicate_id: str
    origins: tuple[t.Mapping[str, t.Any], ...] = ()

    def __post_init__(self) -> None:
        _uuid(self.source_id, "source_id")
        validate_id(self.predicate_id, "predicate")
        object.__setattr__(
            self, "origins", tuple(_materialize_json(origin) for origin in self.origins)
        )


@dataclass(frozen=True, slots=True)
class StructuralCell:
    """One canonical cell and its semantic contributors/outputs."""

    cell_id: str
    predicate_id: str
    contributors: tuple[str, ...]
    outputs: t.Mapping[str, t.Any]

    def __post_init__(self) -> None:
        validate_id(self.cell_id, "cell")
        validate_id(self.predicate_id, "predicate")
        contributors = tuple(
            _uuid(value, "cell contributor") for value in self.contributors
        )
        if not contributors or contributors != tuple(sorted(set(contributors))):
            raise ValueError(
                "cell contributors must be a non-empty sorted unique UUID tuple"
            )
        if not isinstance(self.outputs, t.Mapping):
            raise ValueError("cell outputs must be a mapping")
        if any(not isinstance(name, str) or not name for name in self.outputs):
            raise ValueError("cell output names must be non-empty strings")
        object.__setattr__(self, "contributors", contributors)
        object.__setattr__(self, "outputs", _freeze_mapping(self.outputs))


@dataclass(frozen=True, slots=True)
class AnalysisGeometry:
    """The exact normalized sources and selected canonical cells for one partition."""

    graph: PredicateGraph
    compilation_domain: DomainDefinition
    provider_domains: t.Mapping[str, DomainDefinition]
    partition_identity: t.Mapping[str, t.Any]
    sources: tuple[StructuralSource, ...]
    cells: tuple[StructuralCell, ...]
    global_compilation_domain: DomainDefinition | None = None
    routing: t.Mapping[str, t.Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.graph, PredicateGraph):
            raise TypeError("graph must be a PredicateGraph")
        if not isinstance(self.compilation_domain, DomainDefinition):
            raise TypeError("compilation_domain must be a DomainDefinition")
        _predicate(
            self.graph,
            self.compilation_domain.predicate_id,
            "compilation domain predicate",
        )
        if tuple(self.compilation_domain.fields) != tuple(
            self.graph.fields[name] for name in sorted(self.graph.fields)
        ):
            raise ValueError("compilation domain fields disagree with graph fields")
        global_domain = self.global_compilation_domain or self.compilation_domain
        if not isinstance(global_domain, DomainDefinition):
            raise TypeError("global_compilation_domain must be a DomainDefinition")
        _predicate(
            self.graph,
            global_domain.predicate_id,
            "global compilation domain predicate",
        )
        if tuple(global_domain.fields) != tuple(
            self.graph.fields[name] for name in sorted(self.graph.fields)
        ):
            raise ValueError(
                "global compilation domain fields disagree with graph fields"
            )
        if (
            not isinstance(self.provider_domains, t.Mapping)
            or not self.provider_domains
        ):
            raise ValueError("provider_domains must be a non-empty mapping")
        domains: dict[str, DomainDefinition] = {}
        for name, domain in self.provider_domains.items():
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(domain, DomainDefinition)
            ):
                raise ValueError(
                    "provider domains require labels and DomainDefinition values"
                )
            if name != domain.domain_id:
                raise ValueError("provider domain label must equal domain_id")
            _predicate(self.graph, domain.predicate_id, "provider domain predicate")
            if tuple(domain.fields) != tuple(
                self.graph.fields[field] for field in sorted(self.graph.fields)
            ):
                raise ValueError("provider domain fields disagree with graph fields")
            if not Reasoner(self.graph).is_empty(
                Reasoner(self.graph).difference(
                    domain.predicate_id, global_domain.predicate_id
                )
            ):
                raise ValueError("provider domain exceeds global compilation domain")
            domains[name] = domain
        if not isinstance(self.partition_identity, t.Mapping):
            raise TypeError("partition_identity must be a mapping")
        partition = _freeze_mapping(self.partition_identity)
        if set(partition) != {"routing_id", "key_values"}:
            raise ValueError("partition_identity must be a complete routing identity")
        validate_id(t.cast(str, partition["routing_id"]), "routing")
        sources = tuple(sorted(self.sources, key=lambda item: item.source_id))
        if len({item.source_id for item in sources}) != len(sources):
            raise ValueError("structural sources must have unique UUIDs")
        cells = tuple(sorted(self.cells, key=lambda item: item.cell_id))
        if len({item.cell_id for item in cells}) != len(cells):
            raise ValueError("structural cells must have unique IDs")
        source_ids = {item.source_id for item in sources}
        for source in sources:
            _predicate(self.graph, source.predicate_id, "source predicate")
        for cell in cells:
            _predicate(self.graph, cell.predicate_id, "cell predicate")
            if not set(cell.contributors) <= source_ids:
                raise ValueError("cell contributors must resolve to structural sources")
        object.__setattr__(self, "provider_domains", _freeze_mapping(domains))
        object.__setattr__(self, "partition_identity", partition)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "cells", cells)
        routing = _freeze_mapping(self.routing) if self.routing is not None else None
        if routing is not None:
            _routing_fields(self.graph, routing)
        object.__setattr__(self, "global_compilation_domain", global_domain)
        object.__setattr__(self, "routing", routing)

    def with_sources(self, sources: tuple[StructuralSource, ...]) -> AnalysisGeometry:
        """Return the same immutable geometry with a differently traversed source input."""
        return replace(self, sources=sources)


@dataclass(frozen=True, slots=True)
class RegionEvidence:
    predicate_id: str
    source_ids: tuple[str, ...] = ()
    cell_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceProof:
    duplicates: tuple[RegionEvidence, ...]
    overlaps: tuple[RegionEvidence, ...]
    unreachable_source_ids: tuple[str, ...]
    coverage_gaps: tuple[RegionEvidence, ...]
    cell_excess: tuple[RegionEvidence, ...]
    cell_gaps: tuple[RegionEvidence, ...]


@dataclass(frozen=True, slots=True)
class ProfileState:
    presence: tuple[str, ...]
    dont_care: tuple[str, ...]
    effective_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileOutcome:
    kind: str
    cell_id: str | None
    contributors: tuple[str, ...]
    values: tuple[tuple[str, t.Any], ...]


@dataclass(frozen=True, slots=True)
class ProfileCounterexample:
    state: ProfileState
    outcomes: tuple[ProfileOutcome, ProfileOutcome]
    contexts: tuple[t.Mapping[str, t.Any], t.Mapping[str, t.Any]]


@dataclass(frozen=True, slots=True)
class ProfileProof:
    profile_id: str
    states: tuple[ProfileState, ...]
    counterexamples: tuple[ProfileCounterexample, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class WitnessVerification:
    admission_predicate_id: str
    effective_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutingEvidence:
    state: ProfileState
    predicate_id: str
    partition_key_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RoutingProof:
    reachable_partition_key_indices: tuple[int, ...]
    no_route: tuple[RoutingEvidence, ...]
    ambiguous: tuple[RoutingEvidence, ...]


def _clipped_sources(
    geometry: AnalysisGeometry,
) -> tuple[tuple[StructuralSource, str], ...]:
    return tuple(
        (
            source,
            geometry.graph.and_(
                geometry.compilation_domain.predicate_id, source.predicate_id
            ),
        )
        for source in geometry.sources
    )


def _routing_fields(
    graph: PredicateGraph, routing: t.Mapping[str, t.Any]
) -> dict[str, str]:
    if routing.get("semantics") != "exact-key-1" or not isinstance(
        routing.get("key_dimensions"), t.Sequence
    ):
        raise ValueError("routing must be an exact-key-1 payload")
    fields: dict[str, str] = {}
    for dimension in routing["key_dimensions"]:
        if not isinstance(dimension, t.Mapping):
            raise ValueError("routing key dimension must be a mapping")
        name = dimension.get("dimension_name")
        field = dimension.get("context_field")
        if (
            not isinstance(name, str)
            or not isinstance(field, str)
            or field not in graph.fields
        ):
            raise ValueError("routing key dimension has an unavailable context field")
        if dimension.get("data_type") != graph.fields[field].data_type.value:
            raise ValueError("routing key field type disagrees with graph")
        fields[name] = field
    if len(fields) != len(routing["key_dimensions"]):
        raise ValueError("routing key dimensions must be unique")
    if not isinstance(routing.get("partition_keys"), t.Sequence):
        raise ValueError("routing partition keys must be an array")
    return fields


def _routing_entries(
    geometry: AnalysisGeometry,
    key_values: t.Iterable[t.Mapping[str, t.Any]],
    supplied: set[str],
) -> tuple[tuple[str, t.Any], int] | None:
    routing = t.cast(t.Mapping[str, t.Any], geometry.routing)
    dimensions = _routing_fields(geometry.graph, routing)
    terms: list[tuple[str, t.Any]] = []
    specificity = 0
    for item in key_values:
        if not isinstance(item, t.Mapping) or not isinstance(
            item.get("match"), t.Mapping
        ):
            raise ValueError("routing key entry is malformed")
        name = item.get("dimension_name")
        field = dimensions.get(name)
        match = item["match"]
        if field is None or match.get("kind") not in {"value", "wildcard"}:
            raise ValueError("routing key entry is unavailable")
        if match["kind"] == "value":
            if field not in supplied:
                return None
            terms.append((field, decode_scalar(dict(match["value"]))))
            specificity += 1
    return tuple(terms), specificity


def _routing_match(
    geometry: AnalysisGeometry, entries: tuple[tuple[str, t.Any], int] | None
) -> str:
    if entries is None:
        return geometry.graph.false
    terms, _specificity = entries
    return geometry.graph.and_(
        *(geometry.graph.eq(field, value) for field, value in terms)
    )


def _routing_admission(geometry: AnalysisGeometry, state: ProfileState) -> str:
    if geometry.routing is None:
        return geometry.graph.true
    supplied = set(state.presence)
    selected = geometry.partition_identity["key_values"]
    if not isinstance(selected, tuple):
        selected = tuple(selected)
    selected_entries = _routing_entries(geometry, selected, supplied)
    selected_match = _routing_match(geometry, selected_entries)
    if selected_entries is None:
        return selected_match
    selected_specificity = selected_entries[1]
    competitors: list[str] = []
    found_selected = False
    for key_values in geometry.routing["partition_keys"]:
        entries = _routing_entries(geometry, key_values, supplied)
        if entries is None:
            continue
        if canonical_bytes({"key_values": list(key_values)}) == canonical_bytes(
            {"key_values": list(selected)}
        ):
            found_selected = True
            continue
        if entries[1] >= selected_specificity:
            competitors.append(_routing_match(geometry, entries))
    if not found_selected:
        raise ValueError("selected routing key is absent from routing registry")
    return geometry.graph.and_(
        selected_match, geometry.graph.not_(geometry.graph.or_(*competitors))
    )


def _region(
    predicate_id: str, sources: t.Iterable[str] = (), cells: t.Iterable[str] = ()
) -> RegionEvidence:
    return RegionEvidence(predicate_id, tuple(sorted(sources)), tuple(sorted(cells)))


def prove_source_geometry(
    geometry: AnalysisGeometry,
    *,
    coverage_requirements: t.Iterable[CoverageRequirement] = (),
    scope: Scope | None = None,
) -> SourceProof:
    """Prove source and cell geometry, keeping policy coverage distinct from cell loss."""
    reasoner = Reasoner(geometry.graph)
    clipped = _clipped_sources(geometry)
    duplicates: list[RegionEvidence] = []
    overlaps: list[RegionEvidence] = []
    unreachable: list[str] = []

    for source, predicate_id in clipped:
        if reasoner.is_empty(predicate_id):
            unreachable.append(source.source_id)
    for (left, left_predicate), (right, right_predicate) in combinations(clipped, 2):
        overlap = reasoner.intersect(left_predicate, right_predicate)
        if not reasoner.is_empty(overlap):
            pair = (left.source_id, right.source_id)
            overlaps.append(_region(overlap, pair))
            if reasoner.equivalent(left_predicate, right_predicate):
                duplicates.append(_region(overlap, pair))
    source_union = geometry.graph.or_(*(predicate for _, predicate in clipped))
    cell_union = geometry.graph.or_(
        *(
            geometry.graph.and_(
                geometry.compilation_domain.predicate_id, cell.predicate_id
            )
            for cell in geometry.cells
        )
    )
    source_cell_gap = reasoner.difference(source_union, cell_union)
    cell_gaps: list[RegionEvidence] = []
    if not reasoner.is_empty(source_cell_gap):
        affected = tuple(
            source.source_id
            for source, predicate in clipped
            if not reasoner.is_empty(reasoner.intersect(source_cell_gap, predicate))
        )
        cell_gaps.append(_region(source_cell_gap, affected))
    gaps: list[RegionEvidence] = []
    for requirement in coverage_requirements:
        if not isinstance(requirement, CoverageRequirement):
            raise TypeError(
                "coverage_requirements must contain CoverageRequirement records"
            )
        domain = geometry.provider_domains.get(requirement.domain_ref)
        if domain is None:
            raise ValueError("coverage requirement domain is unavailable")
        _predicate(
            geometry.graph, requirement.region_predicate_id, "coverage region predicate"
        )
        if scope is not None and not _scope_covers(scope, requirement.scope):
            continue
        required = geometry.graph.and_(
            domain.predicate_id, requirement.region_predicate_id
        )
        if not reasoner.is_empty(
            reasoner.difference(required, geometry.compilation_domain.predicate_id)
        ):
            raise ValueError(
                "coverage requirement region exceeds selected compilation domain"
            )
        gap = reasoner.difference(required, source_union)
        if not reasoner.is_empty(gap):
            affected = tuple(
                source.source_id
                for source, predicate in clipped
                if not reasoner.is_empty(reasoner.intersect(gap, predicate))
            )
            gaps.append(_region(gap, affected))
    excess = reasoner.difference(cell_union, source_union)
    cell_excess: list[RegionEvidence] = []
    if not reasoner.is_empty(excess):
        affected_cells = tuple(
            cell.cell_id
            for cell in geometry.cells
            if not reasoner.is_empty(reasoner.intersect(excess, cell.predicate_id))
        )
        cell_excess.append(_region(excess, cells=affected_cells))
    return SourceProof(
        tuple(duplicates),
        tuple(overlaps),
        tuple(unreachable),
        tuple(gaps),
        tuple(cell_excess),
        tuple(cell_gaps),
    )


def prove_routing(
    geometry: AnalysisGeometry,
    contract: ContextContract,
    *,
    guard_fields: t.Iterable[str] = (),
) -> RoutingProof:
    """Prove reachable selected keys plus neutral no-route and tie-ambiguity regions."""
    _contract_fields(geometry, contract)
    if geometry.routing is None:
        return RoutingProof((), (), ())
    routing_fields = tuple(_routing_fields(geometry.graph, geometry.routing).values())
    guards = set(guard_fields)
    contract_fields = {field.name: field for field in contract.fields}
    if not guards <= set(contract_fields):
        raise ValueError("routing guards must be contract fields")
    optional = tuple(
        field
        for field in routing_fields
        if not contract_fields[field].required and field not in guards
    )
    state_count = 1 << len(optional)
    budget = geometry.graph.budget
    budget.reserve(
        "max_work",
        state_count * (len(geometry.routing["partition_keys"]) ** 2 + 1),
        phase="routing.preflight",
        units="routing states",
        partition_identity=geometry.partition_identity,
        domain_ref=contract.domain_ref,
    )
    budget.reserve(
        "max_live_bytes",
        state_count * (128 + 64 * len(routing_fields)),
        phase="routing.preflight",
        units="routing evidence bytes",
        partition_identity=geometry.partition_identity,
        domain_ref=contract.domain_ref,
    )
    domain = geometry.provider_domains[contract.domain_ref].predicate_id
    budget.reserve(
        "max_output_bytes",
        state_count * (128 + 96 * len(routing_fields)),
        phase="routing.preflight",
        units="routing evidence bytes",
        partition_identity=geometry.partition_identity,
        domain_ref=contract.domain_ref,
    )
    reachable: set[int] = set()
    no_route: list[RoutingEvidence] = []
    ambiguous: list[RoutingEvidence] = []
    keys = geometry.routing["partition_keys"]
    for selected in product((False, True), repeat=len(optional)):
        presence = set(guards) | {
            field.name for field in contract.fields if field.required
        }
        presence.update(field for field, include in zip(optional, selected) if include)
        state = ProfileState(tuple(sorted(presence)), (), tuple(sorted(presence)))
        entries = [
            _routing_entries(geometry, key_values, presence) for key_values in keys
        ]
        matches = [_routing_match(geometry, entry) for entry in entries]
        effective_matches: list[str] = []
        for index, entry in enumerate(entries):
            higher_matches = [
                matches[other]
                for other, competitor in enumerate(entries)
                if competitor is not None
                and entry is not None
                and competitor[1] > entry[1]
            ]
            effective_matches.append(
                geometry.graph.and_(
                    domain,
                    matches[index],
                    geometry.graph.not_(geometry.graph.or_(*higher_matches)),
                )
            )
        for index, match in enumerate(effective_matches):
            if not Reasoner(geometry.graph).is_empty(match):
                reachable.add(index)
        no_match = geometry.graph.and_(
            domain, geometry.graph.not_(geometry.graph.or_(*matches))
        )
        if not Reasoner(geometry.graph).is_empty(no_match):
            no_route.append(RoutingEvidence(state, no_match, ()))
        for left, right in combinations(range(len(entries)), 2):
            if (
                entries[left] is None
                or entries[right] is None
                or entries[left][1] != entries[right][1]
            ):
                continue
            higher_matches = [
                matches[index]
                for index, entry in enumerate(entries)
                if entry is not None and entry[1] > entries[left][1]
            ]
            overlap = geometry.graph.and_(
                domain,
                matches[left],
                matches[right],
                geometry.graph.not_(geometry.graph.or_(*higher_matches)),
            )
            if not Reasoner(geometry.graph).is_empty(overlap):
                ambiguous.append(RoutingEvidence(state, overlap, (left, right)))
    return RoutingProof(tuple(sorted(reachable)), tuple(no_route), tuple(ambiguous))


def _profile(contract: ContextContract, profile_id: str) -> ResolutionProfile:
    if not isinstance(contract, ContextContract):
        raise TypeError("contract must be a ContextContract")
    try:
        return next(item for item in contract.profiles if item.profile_id == profile_id)
    except StopIteration as exc:
        raise ValueError("profile_id is not declared by contract") from exc


def _contract_fields(
    geometry: AnalysisGeometry, contract: ContextContract
) -> dict[str, t.Any]:
    domain = geometry.provider_domains.get(contract.domain_ref)
    if domain is None:
        raise ValueError(
            "contract provider domain is unavailable from selected geometry"
        )
    declared = {field.name: field for field in domain.fields}
    for field in contract.fields:
        expected = declared.get(field.name)
        if expected is None or (field.data_type, field.timezone) != (
            expected.data_type,
            expected.timezone,
        ):
            raise ValueError("contract field type disagrees with provider domain")
        graph_field = geometry.graph.fields.get(field.name)
        if graph_field is None or (graph_field.data_type, graph_field.timezone) != (
            field.data_type,
            field.timezone,
        ):
            raise ValueError("contract field type disagrees with predicate graph")
    return {field.name: field for field in contract.fields}


def _profile_states(
    contract: ContextContract,
    profile: ResolutionProfile,
    dimension_fields: t.Mapping[str, str],
    guard_fields: t.Iterable[str],
    routing_fields: t.Iterable[str],
) -> tuple[ProfileState, ...]:
    if set(dimension_fields) != set(profile.dimensions):
        raise ValueError("dimension_fields must map every and only profile dimension")
    fields = {field.name: field for field in contract.fields}
    if not set(dimension_fields.values()) <= set(fields):
        raise ValueError("profile dimensions must resolve to contract fields")
    guards = frozenset(guard_fields)
    routing = frozenset(routing_fields)
    if not guards <= set(fields) or not routing <= set(fields):
        raise ValueError("guard and routing fields must be contract fields")
    optional = tuple(
        field.name
        for field in contract.fields
        if not field.required and field.name not in guards
    )
    masks = tuple(profile.allow_dont_care)
    output: list[ProfileState] = []
    for selected in product((False, True), repeat=len(optional)):
        presence = set(guards) | {
            field.name for field in contract.fields if field.required
        }
        presence.update(name for name, include in zip(optional, selected) if include)
        for masked in product((False, True), repeat=len(masks)):
            dont_care = frozenset(
                name for name, include in zip(masks, masked) if include
            )
            effective = set(guards) | (routing & presence)
            for dimension, field in dimension_fields.items():
                if field in presence and dimension not in dont_care:
                    effective.add(field)
            output.append(
                ProfileState(
                    tuple(sorted(presence)),
                    tuple(sorted(dont_care)),
                    tuple(sorted(effective)),
                )
            )
    return tuple(sorted(output, key=lambda item: (item.presence, item.dont_care)))


def _profile_preflight(
    geometry: AnalysisGeometry,
    contract: ContextContract,
    profile: ResolutionProfile,
    guard_fields: t.Iterable[str],
) -> None:
    guards = frozenset(guard_fields)
    field_count = len(contract.fields)
    mask_count = len(profile.allow_dont_care)
    optional = sum(
        not field.required and field.name not in guards for field in contract.fields
    )
    state_count = 1 << (optional + mask_count)
    outcomes = len(geometry.cells) + 1
    pair_count = state_count * outcomes * (outcomes - 1) // 2
    state_bytes = 128 + 32 * (field_count + mask_count)
    pair_bytes = 256 + 96 * field_count + 2 * state_bytes
    budget = geometry.graph.budget
    scope = {
        "partition_identity": geometry.partition_identity,
        "domain_ref": contract.domain_ref,
        "profile_id": profile.profile_id,
    }
    budget.reserve(
        "max_work",
        state_count * (field_count + mask_count + 1)
        + outcomes
        + pair_count * (2 * field_count + 1),
        phase="profile.preflight",
        units="profile state and outcome work",
        **scope,
    )
    budget.reserve(
        "max_live_bytes",
        state_count * state_bytes + pair_count * pair_bytes,
        phase="profile.preflight",
        units="profile bytes",
        **scope,
    )
    budget.reserve(
        "max_output_bytes",
        state_count * state_bytes + pair_count * pair_bytes,
        phase="profile.preflight",
        units="profile evidence bytes",
        **scope,
    )


class _DualGraph:
    """A renamed-copy self-composition graph sharing the operation budget."""

    def __init__(self, source: PredicateGraph) -> None:
        self.source = source
        fields = []
        self.names: dict[tuple[str, str], str] = {}
        for side in ("left", "right"):
            for name, declaration in source.fields.items():
                renamed = f"__e4a_{side}_{name}"
                self.names[side, name] = renamed
                fields.append(declaration.model_copy(update={"name": renamed}))
        self.graph = PredicateGraph(fields, budget=source.budget)
        self.cache: dict[tuple[str, str], str] = {}

    def predicate(self, side: str, identifier: str) -> str:
        key = (side, identifier)
        if key in self.cache:
            return self.cache[key]
        node = _materialize_json(self.source.nodes[identifier])
        op = node["op"]
        if op in {"and", "or"}:
            node["args"] = [self.predicate(side, child) for child in node["args"]]
        elif op == "not":
            node["arg"] = self.predicate(side, node["arg"])
        elif op in {"eq", "in", "interval", "language"}:
            node["field"] = self.names[side, node["field"]]
            if op == "language":
                node["language_id"] = self.graph.add_language(
                    self.source.languages[node["language_id"]]
                )
        elif op == "compare":
            node["left"] = self.names[side, node["left"]]
            node["right"] = self.names[side, node["right"]]
        result = self.graph.add(node)
        self.cache[key] = result
        return result

    def equalities(self, fields: t.Iterable[str]) -> tuple[str, ...]:
        return tuple(
            self.graph.compare(
                self.names["left", field], self.names["right", field], "eq"
            )
            for field in fields
        )

    def context(
        self, side: str, witness: t.Mapping[str, t.Any]
    ) -> t.Mapping[str, t.Any]:
        return _freeze_mapping(
            {field: witness[self.names[side, field]] for field in self.source.fields}
        )


def _typed_value(value: t.Any) -> tuple[str, str, t.Any]:
    """Keep output scalar runtime types in the exact outcome-equivalence key."""
    value_type = type(value)
    return value_type.__module__, value_type.__qualname__, value


def _outcome_signature(
    cell: StructuralCell, profile: ResolutionProfile
) -> ProfileOutcome:
    missing = set(profile.output_fields) - set(cell.outputs)
    if missing:
        raise ValueError(
            f"canonical cell omits profile output fields: {sorted(missing)}"
        )
    values = tuple(
        (name, _typed_value(cell.outputs[name])) for name in profile.output_fields
    )
    if profile.provenance == "none":
        contributors: tuple[str, ...] = ()
        cell_id: str | None = None
    elif profile.provenance == "contributors":
        contributors, cell_id = cell.contributors, None
    else:
        contributors, cell_id = cell.contributors, cell.cell_id
    return ProfileOutcome("hit", cell_id, contributors, values)


def prove_profile(
    geometry: AnalysisGeometry,
    contract: ContextContract,
    profile_id: str,
    *,
    dimension_fields: t.Mapping[str, str],
    guard_fields: t.Iterable[str] = (),
) -> ProfileProof:
    """Run exact profile self-composition over permitted presence, routing and masks."""
    profile = _profile(contract, profile_id)
    guards = tuple(guard_fields)
    _contract_fields(geometry, contract)
    routing_fields = (
        tuple(_routing_fields(geometry.graph, geometry.routing).values())
        if geometry.routing
        else ()
    )
    _profile_preflight(geometry, contract, profile, guards)
    states = _profile_states(
        contract, profile, dimension_fields, guards, routing_fields
    )
    domain = geometry.provider_domains[contract.domain_ref].predicate_id
    dual = _DualGraph(geometry.graph)
    reasoner = Reasoner(dual.graph)
    cells = tuple(sorted(geometry.cells, key=lambda item: item.cell_id))
    outcomes: list[tuple[ProfileOutcome, str]] = [
        (_outcome_signature(cell, profile), cell.predicate_id) for cell in cells
    ]
    counterexamples: list[ProfileCounterexample] = []
    for state in states:
        routed_domain = geometry.graph.and_(domain, _routing_admission(geometry, state))
        union = geometry.graph.or_(*(cell.predicate_id for cell in cells))
        no_hit = Reasoner(geometry.graph).difference(routed_domain, union)
        state_outcomes = outcomes + (
            []
            if Reasoner(geometry.graph).is_empty(no_hit)
            else [(ProfileOutcome("no_match", None, (), ()), no_hit)]
        )
        equalities = dual.equalities(state.effective_fields)
        for index, (left_outcome, left_predicate) in enumerate(state_outcomes):
            for right_outcome, right_predicate in state_outcomes[index:]:
                if left_outcome == right_outcome:
                    continue
                query = dual.graph.and_(
                    dual.predicate("left", routed_domain),
                    dual.predicate("right", routed_domain),
                    dual.predicate("left", left_predicate),
                    dual.predicate("right", right_predicate),
                    *equalities,
                )
                witness = reasoner.witness(query)
                if witness is not None:
                    dual.graph.budget.reserve(
                        "max_witnesses", 1, phase="profile.witness", units="witnesses"
                    )
                    counterexamples.append(
                        ProfileCounterexample(
                            state,
                            (left_outcome, right_outcome),
                            (
                                dual.context("left", witness),
                                dual.context("right", witness),
                            ),
                        )
                    )
    return ProfileProof(
        profile.profile_id,
        states,
        tuple(counterexamples),
        not (profile.promise == "definite_outcome" and counterexamples),
    )


def _fact_predicate(
    graph: PredicateGraph, values: t.Mapping[str, t.Mapping[str, t.Any]]
) -> str:
    terms = []
    for field, encoded in values.items():
        if field not in graph.fields:
            raise ValueError(f"unknown witness field {field!r}")
        terms.append(graph.eq(field, decode_scalar(dict(encoded))))
    return graph.and_(*terms)


def _validate_contract_scalar(field: t.Any, encoded: t.Mapping[str, t.Any]) -> None:
    value = decode_scalar(dict(encoded))
    if value is None:
        raise ValueError("witness scalar must be concrete")
    encode_scalar(value, field.data_type, timezone=field.timezone, context=True)


def _completion_outcome(
    geometry: AnalysisGeometry, profile: ResolutionProfile, domain: str, complete: str
) -> ProfileOutcome:
    hits = [
        _outcome_signature(cell, profile)
        for cell in geometry.cells
        if not Reasoner(geometry.graph).is_empty(
            geometry.graph.and_(domain, complete, cell.predicate_id)
        )
    ]
    if not hits:
        return ProfileOutcome("no_match", None, (), ())
    if len(set(hits)) != 1:
        raise ValueError("witness completion has non-canonical projected outcomes")
    return hits[0]


def verify_witness(
    geometry: AnalysisGeometry,
    contract: ContextContract,
    witness: Witness,
    *,
    dimension_fields: t.Mapping[str, str],
    guard_fields: t.Iterable[str] = (),
    finding: Finding | None = None,
) -> WitnessVerification:
    """Verify typed source-region or profile outcome evidence against canonical geometry."""
    if not isinstance(witness, Witness):
        raise TypeError("witness must be a Witness")
    fields = _contract_fields(geometry, contract)
    domain = geometry.provider_domains[contract.domain_ref].predicate_id
    if witness.profile_ref is None or witness.request is None:
        if (
            finding is None
            or witness.profile_ref is not None
            or witness.request is not None
        ):
            raise ValueError("source witness requires its referenced finding")
        if witness.kind != "point" or finding.region_predicate_id is None:
            raise ValueError("source witness requires a point region example")
        _predicate(
            geometry.graph, finding.region_predicate_id, "finding region predicate"
        )
        for context in witness.contexts:
            if set(context) != set(fields):
                raise ValueError(
                    "witness completion must be a complete provider-domain field map"
                )
            for name, encoded in context.items():
                _validate_contract_scalar(fields[name], encoded)
            complete = _fact_predicate(geometry.graph, context)
            if Reasoner(geometry.graph).is_empty(geometry.graph.and_(domain, complete)):
                raise ValueError("witness completion is outside the provider domain")
            if Reasoner(geometry.graph).is_empty(
                geometry.graph.and_(finding.region_predicate_id, complete)
            ):
                raise ValueError("witness completion is outside the finding region")
        return WitnessVerification(domain, tuple(sorted(fields)))
    if finding is not None:
        raise ValueError("profile witness must not carry a source finding")
    if witness.profile_ref["contract_id"] != contract.contract_id:
        raise ValueError("witness contract does not match")
    profile = _profile(contract, witness.profile_ref["profile_id"])
    routing_fields = (
        tuple(_routing_fields(geometry.graph, geometry.routing).values())
        if geometry.routing
        else ()
    )
    states = _profile_states(
        contract, profile, dimension_fields, guard_fields, routing_fields
    )
    request = witness.request
    classified = set(request.provided_values) | set(request.unavailable_fields)
    if classified != set(fields):
        raise ValueError("witness request must classify every contract field")
    if any(
        field.required and field.name not in request.provided_values
        for field in contract.fields
    ):
        raise ValueError("witness request omits a required supplied field")
    if not set(request.dont_care) <= set(profile.allow_dont_care):
        raise ValueError("witness request has an unauthorized mask")
    for name, encoded in request.provided_values.items():
        if name not in fields:
            raise ValueError("witness request has an unknown supplied field")
        _validate_contract_scalar(fields[name], encoded)
    admission = geometry.graph.and_(
        domain, _fact_predicate(geometry.graph, request.provided_values)
    )
    if Reasoner(geometry.graph).is_empty(admission):
        raise ValueError("witness full supplied facts fail provider admission")
    presence = tuple(sorted(request.provided_values))
    selected = [
        state
        for state in states
        if state.presence == presence and state.dont_care == tuple(request.dont_care)
    ]
    if len(selected) != 1:
        raise ValueError("witness request state is not permitted by the profile")
    state = selected[0]
    routed = geometry.graph.and_(domain, _routing_admission(geometry, state))
    outcomes: list[ProfileOutcome] = []
    for context in witness.contexts:
        if set(context) != set(fields):
            raise ValueError(
                "witness completion must be a complete provider-domain field map"
            )
        for name, encoded in context.items():
            _validate_contract_scalar(fields[name], encoded)
        complete = _fact_predicate(geometry.graph, context)
        if Reasoner(geometry.graph).is_empty(geometry.graph.and_(domain, complete)):
            raise ValueError("witness completion is outside the provider domain")
        if Reasoner(geometry.graph).is_empty(geometry.graph.and_(routed, complete)):
            raise ValueError("witness completion is not eligible for selected routing")
        for field in state.effective_fields:
            if (
                field not in request.provided_values
                or context[field] != request.provided_values[field]
            ):
                raise ValueError(
                    "witness completion disagrees with effective supplied facts"
                )
        outcomes.append(_completion_outcome(geometry, profile, routed, complete))
    if witness.kind == "pair" and outcomes[0] == outcomes[1]:
        raise ValueError(
            "profile pair witness must establish distinct projected outcomes"
        )
    return WitnessVerification(admission, state.effective_fields)


@dataclass(frozen=True, slots=True)
class CanonicalMaterial:
    """Canonical IDs recomputed by the adapter from the actual build/analysis input."""

    analysis_input_id: str
    source_bundle_id: str
    metadata_id: str
    aggregates_id: str
    routing_id: str
    domain_ids: t.Mapping[str, str]
    semantic_versions: SemanticVersions
    artifact_id: str | None = None
    selected_scope: Scope | None = None

    def __post_init__(self) -> None:
        for value, kind in (
            (self.analysis_input_id, "analysis-input"),
            (self.source_bundle_id, "source-bundle"),
            (self.metadata_id, "metadata"),
            (self.aggregates_id, "aggregates"),
            (self.routing_id, "routing"),
        ):
            validate_id(value, kind)
        if self.artifact_id is not None:
            validate_id(self.artifact_id, "artifact")
        for name, identifier in self.domain_ids.items():
            if not isinstance(name, str) or not name:
                raise ValueError("canonical material domain labels must be non-empty")
            validate_id(identifier, "domain")
        if not isinstance(self.semantic_versions, SemanticVersions):
            raise TypeError("semantic_versions must be SemanticVersions")
        if self.selected_scope is not None and not isinstance(
            self.selected_scope, Scope
        ):
            raise TypeError("selected_scope must be a Scope")
        object.__setattr__(self, "domain_ids", _freeze_mapping(self.domain_ids))


@dataclass(frozen=True, slots=True)
class BuildPermission:
    source_report_id: str
    approval_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BindingPermission:
    binding_id: str
    source_report_id: str
    compiled_report_id: str


_SOURCE_CHECKS = frozenset(
    {
        "source_schema",
        "source_identity",
        "source_predicates",
        "source_overlaps",
        "routing",
        "coverage",
        "profiles",
    }
)
_COMPILED_CHECKS = frozenset(
    {
        "cell_nonempty",
        "cell_disjointness",
        "source_union",
        "source_membership",
        "output_folds",
        "profile_consistency",
    }
)


def _records(
    bundle: ValidationBundle, name: str, cls: type[t.Any]
) -> tuple[t.Any, ...]:
    if not isinstance(bundle, ValidationBundle) or not isinstance(
        bundle.validation, t.Mapping
    ):
        raise TypeError("bundle must be a ValidationBundle with validation records")
    values = bundle.validation.get(name)
    if not isinstance(values, tuple) or any(
        not isinstance(value, cls) for value in values
    ):
        raise ValueError(
            f"bundle validation.{name} must be a typed immutable record tuple"
        )
    return values


def _policy_scope(scope: Scope, selected_scope: Scope) -> Scope | None:
    """Return the policy portion relevant to a selected authorization scope."""
    scope_payload = scope.model_dump(mode="json")
    selected_payload = selected_scope.model_dump(mode="json")
    selected_partitions = _scope_refs(selected_payload["partition_refs"])
    partitions = tuple(
        reference
        for reference in scope_payload["partition_refs"]
        if canonical_bytes(reference) in selected_partitions
    )
    domains = tuple(
        domain
        for domain in scope_payload["domain_refs"]
        if domain in selected_scope.domain_refs
    )
    if not partitions or not domains:
        return None
    if not scope.profile_refs:
        profiles: tuple[t.Mapping[str, str], ...] = ()
    elif not selected_scope.profile_refs:
        profiles = tuple(scope_payload["profile_refs"])
    else:
        selected_profiles = _scope_refs(selected_payload["profile_refs"])
        profiles = tuple(
            reference
            for reference in scope_payload["profile_refs"]
            if canonical_bytes(reference) in selected_profiles
        )
        if not profiles:
            return None
    return Scope(
        partition_refs=partitions,
        domain_refs=domains,
        profile_refs=profiles,
    )


def _check_covers_requirement(check_scope: Scope, requirement_scope: Scope) -> bool:
    """Keep global check evidence distinct from explicit profile authorization."""
    return bool(check_scope.profile_refs) == bool(
        requirement_scope.profile_refs
    ) and _scope_covers(check_scope, requirement_scope)


def _authorization_scope(policy_scope: Scope, selected_scope: Scope) -> Scope:
    """Require explicit profile checks to enumerate every bound profile."""
    if policy_scope.profile_refs and selected_scope.profile_refs:
        return selected_scope
    return policy_scope


def _policy_covers_scope(policy_scope: Scope, target_scope: Scope) -> bool:
    """Apply global policy findings to profiles without treating them as wildcards."""
    if not _scope_covers_nonprofiles(policy_scope, target_scope):
        return False
    if not target_scope.profile_refs:
        return not policy_scope.profile_refs
    return not policy_scope.profile_refs or _scope_refs(
        policy_scope.profile_refs
    ) >= _scope_refs(target_scope.profile_refs)


def _material_matches(analysis: AnalysisInput, material: CanonicalMaterial) -> None:
    if analysis.id != material.analysis_input_id:
        raise ValueError("canonical analysis input does not match selected material")
    for field, expected, actual, label in (
        (
            "source_bundle_digest",
            analysis.source_bundle_digest,
            material.source_bundle_id,
            "source bundle",
        ),
        ("metadata_digest", analysis.metadata_digest, material.metadata_id, "metadata"),
        (
            "aggregate_digest",
            analysis.aggregate_digest,
            material.aggregates_id,
            "aggregates",
        ),
        ("routing_digest", analysis.routing_digest, material.routing_id, "routing"),
    ):
        if expected != actual:
            raise ValueError(
                f"canonical {label} does not match selected analysis input"
            )
    if analysis.domain_digests != material.domain_ids:
        raise ValueError("canonical domain IDs do not match selected analysis input")
    if analysis.semantic_versions != material.semantic_versions:
        raise ValueError(
            "canonical semantic versions do not match selected analysis input"
        )


def _diagnostic_for(analysis: AnalysisInput, finding: Finding) -> DiagnosticRule:
    matches = [
        rule
        for rule in analysis.validation_policy.diagnostic_rules
        if rule.stage == finding.stage
        and rule.check_id == finding.check_id
        and rule.code == finding.code
        and _policy_covers_scope(rule.scope, finding.scope)
    ]
    if len(matches) != 1:
        raise ValueError("finding has no unique applicable diagnostic rule")
    rule = matches[0]
    if rule.severity != finding.severity:
        raise ValueError("finding severity disagrees with diagnostic rule")
    if len(finding.witnesses) > rule.max_witnesses:
        raise ValueError("finding exceeds diagnostic witness limit")
    if any(witness.kind != rule.witness_kind for witness in finding.witnesses):
        raise ValueError("finding witness kind disagrees with diagnostic rule")
    return rule


def _report_is_authorizable(
    report: ValidationReport,
    findings: t.Mapping[str, Finding],
    *,
    analysis: AnalysisInput,
    required_scope: Scope,
    stage: str,
) -> tuple[Finding, ...]:
    if stage == "source":
        validate_source_report_semantics(report)
    elif stage == "compiled":
        validate_compiled_report_semantics(report, analysis)
    if report.stage != stage:
        raise ValueError("report has the wrong validation stage")
    if not _scope_covers(report.scope, required_scope):
        raise ValueError("report scope does not cover the selected input")
    mandatory = _SOURCE_CHECKS if stage == "source" else _COMPILED_CHECKS
    requirements = [(identifier, required_scope) for identifier in mandatory]
    for check in analysis.validation_policy.required_checks:
        if check.stage != stage:
            continue
        scope = _policy_scope(check.scope, required_scope)
        if scope is not None:
            requirements.append(
                (check.check_id, _authorization_scope(scope, required_scope))
            )
    for check_id, scope in requirements:
        if not any(
            check.check_id == check_id and _check_covers_requirement(check.scope, scope)
            for check in report.checks
        ):
            raise ValueError(
                "report omits a mandatory or policy-scoped validation check"
            )
    resolved: list[Finding] = []
    for check in report.checks:
        coverage_scopes = [
            scope
            for requirement in analysis.validation_policy.coverage_requirements
            if (scope := _policy_scope(requirement.scope, required_scope)) is not None
        ]
        vacuous_coverage = check.check_id == "coverage" and not any(
            _policy_covers_scope(scope, check.scope) for scope in coverage_scopes
        )
        if check.status == "not_required":
            if not vacuous_coverage or not check.complete:
                raise ValueError("report not_required status is not policy-proven")
        elif check.status not in {"passed", "findings"} or not check.complete:
            raise ValueError("incomplete or unsupported report check cannot authorize")
        for identifier in check.finding_ids:
            finding = findings.get(identifier)
            if (
                finding is None
                or finding.analysis_input_id != report.analysis_input_id
                or finding.stage != stage
            ):
                raise ValueError("report has an unresolved or foreign finding")
            if finding.check_id != check.check_id or not _scope_covers(
                check.scope, finding.scope
            ):
                raise ValueError("report finding/check scope mismatch")
            if finding.check_id == "coverage":
                coverage = [
                    requirement
                    for requirement in analysis.validation_policy.coverage_requirements
                    if (scope := _policy_scope(requirement.scope, required_scope))
                    is not None
                    and _policy_covers_scope(scope, finding.scope)
                ]
                if coverage and any(
                    requirement.severity != finding.severity for requirement in coverage
                ):
                    raise ValueError("coverage finding severity disagrees with policy")
            _diagnostic_for(analysis, finding)
            resolved.append(finding)
    if set(item.id for item in resolved) != set(report.finding_ids):
        raise ValueError("report finding IDs disagree with check findings")
    if any(item.severity == "error" for item in resolved):
        raise ValueError("error findings cannot be approved through")
    if stage == "compiled" and any(item.severity != "error" for item in resolved):
        raise ValueError("compiled findings must be errors")
    return tuple(resolved)


def _approve(
    report: ValidationReport,
    analysis: AnalysisInput,
    findings: tuple[Finding, ...],
    approvals: t.Mapping[str, WarningApproval],
    approval_ids: t.Iterable[str],
) -> tuple[str, ...]:
    selected = tuple(approval_ids)
    if len(selected) != len(set(selected)):
        raise ValueError("approval IDs must be unique")
    warnings = {item.id: item for item in findings if item.severity == "warning"}
    covered: set[str] = set()
    for identifier in selected:
        approval = approvals.get(identifier)
        if approval is None:
            raise ValueError("selected approval is unresolved")
        if approval.report_id != report.id or approval.analysis_input_id != analysis.id:
            raise ValueError("approval does not target the selected report/input")
        for warning_id in approval.warning_ids:
            warning = warnings.get(warning_id)
            if warning is None or not _scope_covers(approval.scope, warning.scope):
                raise ValueError(
                    "approval does not cover an actual scoped report warning"
                )
            covered.add(warning_id)
    if set(warnings) != covered:
        raise ValueError("selected report warnings require scoped approval")
    return tuple(sorted(selected))


def _selected_analysis(bundle: ValidationBundle, analysis_id: str) -> AnalysisInput:
    analyses = {
        item.id: item for item in _records(bundle, "analysis_inputs", AnalysisInput)
    }
    try:
        return analyses[analysis_id]
    except KeyError as exc:
        raise ValueError("selected analysis input is unresolved") from exc


def _analysis_contracts(
    bundle: ValidationBundle, analysis: AnalysisInput
) -> tuple[ContextContract, ...]:
    """Resolve the immutable contracts named by an analysis input."""
    envelopes = {
        envelope.get("id"): envelope
        for envelope in bundle.context_contracts
        if isinstance(envelope, t.Mapping)
    }
    contracts: list[ContextContract] = []
    for reference in analysis.contracts:
        contract_id = reference.get("contract_id")
        digest = reference.get("contract_digest")
        envelope = envelopes.get(digest)
        if not isinstance(contract_id, str) or not isinstance(envelope, t.Mapping):
            raise ValueError("analysis contract evidence is unresolved")
        payload = envelope.get("payload")
        if not isinstance(payload, t.Mapping) or "contract" not in payload:
            raise ValueError("analysis contract envelope is malformed")
        contract = ContextContract.model_validate(payload["contract"])
        if contract.contract_id != contract_id:
            raise ValueError("analysis contract identity is stale")
        contracts.append(contract)
    return tuple(contracts)


def _source_permission(
    bundle: ValidationBundle,
    analysis: AnalysisInput,
    report_id: str,
    approval_ids: t.Iterable[str],
    required_scope: Scope,
) -> BuildPermission:
    reports = {item.id: item for item in _records(bundle, "reports", ValidationReport)}
    findings = {item.id: item for item in _records(bundle, "findings", Finding)}
    approvals = {
        item.id: item for item in _records(bundle, "approvals", WarningApproval)
    }
    report = reports.get(report_id)
    if report is None or report.analysis_input_id != analysis.id:
        raise ValueError(
            "selected source report is unresolved or belongs to another input"
        )
    findings_for_report = _report_is_authorizable(
        report,
        findings,
        analysis=analysis,
        required_scope=required_scope,
        stage="source",
    )
    return BuildPermission(
        report.id,
        _approve(report, analysis, findings_for_report, approvals, approval_ids),
    )


def validate_build_permission(
    value: ValidatedBuildInput, material: CanonicalMaterial
) -> BuildPermission:
    """Authorize a build only from the selected complete source report and approvals."""
    if not isinstance(value, ValidatedBuildInput) or not isinstance(
        material, CanonicalMaterial
    ):
        raise TypeError(
            "build permission requires ValidatedBuildInput and CanonicalMaterial"
        )
    if material.selected_scope is None:
        raise ValueError("canonical material must carry the selected analysis scope")
    analysis = _selected_analysis(value.bundle, value.analysis_input_id)
    _material_matches(analysis, material)
    validate_source_policy(
        analysis,
        _analysis_contracts(value.bundle, analysis),
        partition_refs=material.selected_scope.partition_refs,
        source_counts=(0,) * len(material.selected_scope.partition_refs),
    )
    return _source_permission(
        value.bundle,
        analysis,
        value.source_report_id,
        value.approval_ids,
        material.selected_scope,
    )


def _bound_contract(
    binding: ContractBinding, bundle: ValidationBundle
) -> ContextContract:
    for envelope in bundle.context_contracts:
        if envelope.get("id") != binding.contract_digest:
            continue
        payload = envelope.get("payload")
        if not isinstance(payload, t.Mapping) or "contract" not in payload:
            raise ValueError("binding contract envelope is malformed")
        contract = ContextContract.model_validate(payload["contract"])
        if contract.contract_id != binding.contract_id:
            raise ValueError("binding contract identity is stale")
        return contract
    raise ValueError("binding contract identity is stale")


def validate_contract_binding(
    binding: ContractBinding, bundle: ValidationBundle, material: CanonicalMaterial
) -> BindingPermission:
    """Check immutable artifact/input/report/profile permission for one binding."""
    if not isinstance(binding, ContractBinding) or not isinstance(
        material, CanonicalMaterial
    ):
        raise TypeError(
            "binding permission requires ContractBinding and CanonicalMaterial"
        )
    if material.selected_scope is None:
        raise ValueError("canonical material must carry the selected analysis scope")
    bindings = _records(bundle, "bindings", ContractBinding)
    if binding not in bindings:
        raise ValueError("binding is absent from supplied evidence")
    same_pair = [
        item
        for item in bindings
        if item.artifact_id == binding.artifact_id
        and item.contract_id == binding.contract_id
    ]
    if len(same_pair) != 1:
        raise ValueError("competing active artifact/contract bindings are forbidden")
    if material.artifact_id is None or binding.artifact_id != material.artifact_id:
        raise ValueError("binding artifact does not match canonical material")
    analysis = _selected_analysis(bundle, binding.analysis_input_id)
    _material_matches(analysis, material)
    partitions = tuple(
        {"routing_id": bundle.routing["id"], "key_values": keys}
        for keys in bundle.routing["payload"]["partition_keys"]
    )
    validate_source_policy(
        analysis,
        _analysis_contracts(bundle, analysis),
        partition_refs=partitions,
        source_counts=(0,) * len(partitions),
    )
    if binding.semantic_versions.content != analysis.semantic_versions:
        raise ValueError("binding content semantic versions are stale")
    if binding.domain_digest != material.domain_ids.get(binding.domain_ref):
        raise ValueError("binding domain identity is stale")
    contracts = {
        item["contract_id"]: item["contract_digest"] for item in analysis.contracts
    }
    if contracts.get(binding.contract_id) != binding.contract_digest:
        raise ValueError("binding contract identity is stale")
    contract = _bound_contract(binding, bundle)
    if contract.domain_ref != binding.domain_ref:
        raise ValueError("binding contract domain does not match binding domain")
    expected_profiles = {
        profile.profile_id: content_id(
            "profile",
            {
                "schema_version": 1,
                "contract_id": contract.contract_id,
                "profile": profile.model_dump(mode="json", exclude_none=True),
            },
        )
        for profile in contract.profiles
    }
    if any(
        expected_profiles.get(pair["profile_id"]) != pair["profile_digest"]
        for pair in binding.authorized_profiles
    ):
        raise ValueError("binding profile identity is stale")
    selected_scope = material.selected_scope.model_dump(mode="json")
    binding_scope = Scope(
        partition_refs=selected_scope["partition_refs"],
        domain_refs=selected_scope["domain_refs"],
        profile_refs=[
            {"contract_id": binding.contract_id, "profile_id": pair["profile_id"]}
            for pair in binding.authorized_profiles
        ],
    )
    if not _scope_covers(material.selected_scope, binding_scope):
        raise ValueError("binding scope widens selected analysis scope")
    source = _source_permission(
        bundle, analysis, binding.source_report_id, binding.approval_ids, binding_scope
    )
    reports = {item.id: item for item in _records(bundle, "reports", ValidationReport)}
    findings = {item.id: item for item in _records(bundle, "findings", Finding)}
    compiled = reports.get(binding.compiled_report_id)
    if (
        compiled is None
        or compiled.analysis_input_id != analysis.id
        or compiled.artifact_id != binding.artifact_id
    ):
        raise ValueError("compiled report does not match binding artifact/input")
    _report_is_authorizable(
        compiled,
        findings,
        analysis=analysis,
        required_scope=binding_scope,
        stage="compiled",
    )
    return BindingPermission(binding.id, source.source_report_id, compiled.id)


_SOURCE_CODES = {
    "source_predicates": frozenset({"unreachable_source"}),
    "source_overlaps": frozenset(
        {"source_overlap", "duplicate_source", "singleton_boundary_overlap"}
    ),
    "routing": frozenset({"routing_gap", "routing_ambiguity"}),
    "coverage": frozenset({"coverage_gap"}),
    "profiles": frozenset({"profile_counterexample"}),
}


def validate_source_report_semantics(report: ValidationReport) -> None:
    """Admit only the completed source-analysis-1 report interpretation."""
    if not isinstance(report, ValidationReport):
        raise TypeError("report must be a ValidationReport")
    if report.stage != "source":
        raise ValueError("report must be a source report")
    if report.validator.get("semantic_version") != "source-analysis-1":
        raise ValueError("report is not source-analysis-1 evidence")
    if (
        not isinstance(report.validator.get("validator_id"), str)
        or not report.validator["validator_id"]
    ):
        raise ValueError("report validator label is invalid")
    if {check.check_id for check in report.checks} != _SOURCE_CHECKS:
        raise ValueError("source-analysis-1 report has an unsupported check catalogue")
    if any(
        check.status not in {"passed", "findings", "not_required"} or not check.complete
        for check in report.checks
    ):
        raise ValueError("source-analysis-1 report contains incomplete check evidence")


_COMPILED_PRODUCER_VERSION = "compiled-analysis-1"


def _compiled_policy_checks(analysis: AnalysisInput, selected_scope: Scope) -> tuple[tuple[str, Scope], ...]:
    """Resolve the closed compiled-check obligations for one artifact scope."""
    requirements: list[tuple[str, Scope]] = [(identifier, selected_scope) for identifier in sorted(_COMPILED_CHECKS)]
    if any(rule.stage == "compiled" for rule in analysis.validation_policy.diagnostic_rules):
        raise ValueError("compiled diagnostic findings are unsupported by this producer")
    for requirement in analysis.validation_policy.required_checks:
        if requirement.stage != "compiled":
            continue
        if requirement.check_id not in _COMPILED_CHECKS:
            raise ValueError("compiled policy requires an unsupported check")
        policy_scope = _policy_scope(requirement.scope, selected_scope)
        if policy_scope is not None:
            requirements.append((requirement.check_id, _authorization_scope(policy_scope, selected_scope)))
    unique = {(check_id, canonical_bytes(scope.model_dump(mode="json"))): (check_id, scope) for check_id, scope in requirements}
    return tuple(unique[key] for key in sorted(unique, key=lambda item: (item[0], item[1])))


def validate_compiled_report_semantics(report: ValidationReport, analysis: AnalysisInput) -> None:
    """Admit only this producer's complete, clean compiled evidence."""
    if not isinstance(report, ValidationReport) or not isinstance(analysis, AnalysisInput):
        raise TypeError("compiled report semantics require report and analysis input")
    if report.stage != "compiled":
        raise ValueError("report must be compiled evidence")
    if report.validator.get("semantic_version") != _COMPILED_PRODUCER_VERSION:
        raise ValueError("report is not supported compiled-analysis-1 evidence")
    if report.finding_ids or any(check.finding_ids for check in report.checks):
        raise ValueError("compiled-analysis-1 evidence must be clean")
    if any(check.status != "passed" or not check.complete for check in report.checks):
        raise ValueError("compiled-analysis-1 evidence must be complete and clean")
    requirements = _compiled_policy_checks(analysis, report.scope)
    actual = {(check.check_id, canonical_bytes(check.scope.model_dump(mode="json"))) for check in report.checks}
    expected = {(identifier, canonical_bytes(scope.model_dump(mode="json"))) for identifier, scope in requirements}
    if actual != expected:
        raise ValueError("compiled-analysis-1 report has an unsupported check catalogue")


def produce_clean_compiled_report(
    analysis: AnalysisInput, artifact_id: str, scope: Scope, *, budget: OperationBudget
) -> ValidationReport:
    """Record proved checks, charging construction and publication before allocation."""
    from mountainash_rules.core.codec import _bounded_json_size, _make_exact_envelope

    if not isinstance(analysis, AnalysisInput) or not isinstance(scope, Scope):
        raise TypeError("compiled report requires AnalysisInput and Scope")
    validate_id(artifact_id, "artifact")
    scope_size = _bounded_json_size(scope, budget, phase="compiled.report", counter="max_live_bytes")
    policy_size = _bounded_json_size(analysis.validation_policy, budget, phase="compiled.report", counter="max_live_bytes")
    count = len(_COMPILED_CHECKS) + len(analysis.validation_policy.coverage_requirements)
    workspace = 4 * (4096 + count * (1024 + scope_size) + policy_size)
    budget.reserve("max_live_bytes", workspace, phase="compiled.report", units="report construction bytes")
    try:
        checks = tuple(ReportCheck(check_id=check_id, scope=check_scope, status="passed", complete=True, finding_ids=()) for check_id, check_scope in _compiled_policy_checks(analysis, scope))
        payload = {"schema_version": 1, "analysis_input_id": analysis.id, "stage": "compiled", "artifact_id": artifact_id, "validator": {"validator_id": "mountainash-rules", "semantic_version": _COMPILED_PRODUCER_VERSION}, "scope": scope.model_dump(mode="json"), "checks": [check.model_dump(mode="json") for check in checks], "finding_ids": []}
        envelope = _make_exact_envelope("report", payload, budget=budget)
        report = ValidationReport(id=envelope["id"], **envelope["payload"])
        validate_compiled_report_semantics(report, analysis)
        return report
    finally:
        budget.release("max_live_bytes", workspace)


def _scope(
    partitions: t.Iterable[t.Mapping[str, t.Any]],
    domains: t.Iterable[str],
    profiles: t.Iterable[t.Mapping[str, str]] = (),
) -> Scope:
    partition_values = tuple(
        sorted((_materialize_json(item) for item in partitions), key=canonical_bytes)
    )
    profile_values = tuple(
        sorted((_materialize_json(item) for item in profiles), key=canonical_bytes)
    )
    return Scope(
        partition_refs=partition_values,
        domain_refs=tuple(sorted(set(domains))),
        profile_refs=profile_values,
    )


def _rule_for(policy: t.Any, check_id: str, code: str, scope: Scope) -> DiagnosticRule:
    matches = [
        rule
        for rule in policy.diagnostic_rules
        if rule.stage == "source"
        and rule.check_id == check_id
        and rule.code == code
        and _policy_covers_scope(rule.scope, scope)
    ]
    if len(matches) != 1:
        raise ValueError("source diagnostic activation has no unique policy rule")
    return matches[0]


def _validate_source_rule(rule: DiagnosticRule) -> None:
    codes = _SOURCE_CODES.get(rule.check_id)
    if codes is None or rule.code not in codes:
        raise ValueError("source policy declares an unsupported diagnostic code")
    expected = {
        "unreachable_source": {"none"},
        "source_overlap": {"none", "point"},
        "duplicate_source": {"none", "point"},
        "singleton_boundary_overlap": {"none", "point"},
        "routing_gap": {"none"},
        "routing_ambiguity": {"none"},
        "coverage_gap": {"none", "point"},
        "profile_counterexample": {"none", "pair"},
    }[rule.code]
    if rule.witness_kind not in expected:
        raise ValueError("source diagnostic has an incompatible witness kind")
    if rule.code in {"routing_gap", "routing_ambiguity"} and rule.severity != "error":
        raise ValueError("routing diagnostics must be errors")
    if rule.code == "singleton_boundary_overlap" and rule.severity not in {
        "warning",
        "error",
    }:
        raise ValueError("boundary diagnostics must be at least warnings")


def validate_source_policy(
    analysis: AnalysisInput,
    contracts: t.Sequence[ContextContract],
    *,
    partition_refs: t.Iterable[t.Mapping[str, t.Any]],
    source_counts: t.Sequence[int],
    ordered_fields: t.Iterable[str] = (),
) -> None:
    """Admit every activated source-policy obligation before proof geometry.

    ``source_counts`` is the prepared routing-registry-aligned source census.
    It activates source predicates and overlap obligations without requesting
    partition geometry; routing, profile, and coverage obligations are
    preflighted for every selected contract and scope.
    """
    if not isinstance(analysis, AnalysisInput):
        raise TypeError("analysis must be an AnalysisInput")
    if not isinstance(source_counts, t.Sequence) or isinstance(
        source_counts, str | bytes
    ):
        raise TypeError("source_counts must be an aligned integer sequence")
    partitions = tuple(_materialize_json(item) for item in partition_refs)
    counts = tuple(source_counts)
    if not partitions:
        raise ValueError("source policy requires the full routing registry")
    if len(partitions) != len(counts) or any(
        type(count) is not int or count < 0 for count in counts
    ):
        raise ValueError(
            "source_counts must align with partitions as non-negative ints"
        )
    contract_records = tuple(contracts)
    if any(not isinstance(contract, ContextContract) for contract in contract_records):
        raise TypeError("contracts must contain ContextContract records")
    if len({contract.contract_id for contract in contract_records}) != len(
        contract_records
    ):
        raise ValueError("contracts must have unique contract IDs")
    if any(
        contract.domain_ref not in analysis.domain_digests
        for contract in contract_records
    ):
        raise ValueError("contract names an unavailable source-policy domain")
    profile_refs = tuple(
        {"contract_id": contract.contract_id, "profile_id": profile.profile_id}
        for contract in sorted(contract_records, key=lambda item: item.contract_id)
        for profile in contract.profiles
    )
    full_scope = _scope(partitions, analysis.domain_digests, profile_refs)
    policy = analysis.validation_policy

    def validate_scope(scope: Scope) -> None:
        if not _scope_covers(full_scope, scope):
            raise ValueError("source policy scope exceeds the current registry")

    for rule in policy.diagnostic_rules:
        if rule.stage == "source":
            _validate_source_rule(rule)
            validate_scope(rule.scope)
    for check in policy.required_checks:
        if check.stage == "source":
            if check.check_id not in _SOURCE_CHECKS:
                raise ValueError("source policy requires an unsupported check")
            validate_scope(check.scope)
            if (
                contract_records
                and check.check_id in {"routing", "profiles"}
                and not check.scope.profile_refs
            ):
                raise ValueError(
                    "contract routing/profile checks require explicit profile scope"
                )

    coverage_severities: dict[tuple[bytes, str], str] = {}
    for requirement in policy.coverage_requirements:
        validate_scope(requirement.scope)
        if requirement.domain_ref not in analysis.domain_digests:
            raise ValueError("coverage requirement names an unavailable domain")
        effective_scope = _scope(
            requirement.scope.partition_refs,
            (requirement.domain_ref,),
            requirement.scope.profile_refs,
        )
        key = (
            canonical_bytes(effective_scope.model_dump(mode="json")),
            requirement.region_predicate_id,
        )
        previous = coverage_severities.setdefault(key, requirement.severity)
        if previous != requirement.severity:
            raise ValueError("equivalent coverage requirements disagree on severity")

    ordered = frozenset(ordered_fields)
    for partition, count in zip(partitions, counts, strict=True):
        partition_scope = _scope((partition,), (analysis.compilation_domain_ref,))
        if count:
            _rule_for(
                policy, "source_predicates", "unreachable_source", partition_scope
            )
        if count > 1:
            _rule_for(policy, "source_overlaps", "source_overlap", partition_scope)
            _rule_for(policy, "source_overlaps", "duplicate_source", partition_scope)
            if ordered:
                _rule_for(
                    policy,
                    "source_overlaps",
                    "singleton_boundary_overlap",
                    partition_scope,
                )
        for requirement in policy.coverage_requirements:
            target_scope = _scope(
                (partition,),
                (requirement.domain_ref,),
                requirement.scope.profile_refs,
            )
            if _scope_covers(requirement.scope, target_scope):
                rule = _rule_for(policy, "coverage", "coverage_gap", target_scope)
                if rule.severity != requirement.severity:
                    raise ValueError(
                        "coverage diagnostic severity disagrees with requirement"
                    )

    for contract in contract_records:
        routing_scope = _scope(
            partitions,
            (contract.domain_ref,),
            (
                {
                    "contract_id": contract.contract_id,
                    "profile_id": profile.profile_id,
                }
                for profile in contract.profiles
            ),
        )
        _rule_for(policy, "routing", "routing_gap", routing_scope)
        _rule_for(policy, "routing", "routing_ambiguity", routing_scope)
        for partition in partitions:
            for profile in contract.profiles:
                if profile.promise == "candidate_only":
                    continue
                profile_scope = _scope(
                    (partition,),
                    (contract.domain_ref,),
                    (
                        {
                            "contract_id": contract.contract_id,
                            "profile_id": profile.profile_id,
                        },
                    ),
                )
                rule = _rule_for(
                    policy, "profiles", "profile_counterexample", profile_scope
                )
                if profile.promise == "definite_outcome" and rule.severity != "error":
                    raise ValueError("definite-outcome counterexamples must be errors")


def _source_point(
    geometry: AnalysisGeometry, domain: DomainDefinition, predicate_id: str
) -> Witness:
    """Encode one exact source-region example without inventing a contract."""
    witness = Reasoner(geometry.graph).witness(
        geometry.graph.and_(domain.predicate_id, predicate_id)
    )
    if witness is None:
        raise ValueError("cannot encode a point for an empty source finding region")
    context = {
        name: encode_scalar(
            witness[name], field.data_type, timezone=field.timezone, context=True
        )
        for name, field in geometry.graph.fields.items()
    }
    return Witness(kind="point", contexts=(context,), profile_ref=None, request=None)


def verify_source_point(
    geometry: AnalysisGeometry, finding: Finding, witness: Witness
) -> None:
    """Replay a contract-free source point against its named full domain."""
    if finding.region_predicate_id is None or witness.kind != "point":
        raise ValueError("source point replay requires a point finding and witness")
    if len(finding.scope.domain_refs) != 1:
        raise ValueError("source point findings require one explicit domain")
    domain = geometry.provider_domains.get(finding.scope.domain_refs[0])
    if domain is None:
        raise ValueError("source point domain is unavailable from geometry")
    if len(witness.contexts) != 1:
        raise ValueError("source point witness must have one context")
    context = witness.contexts[0]
    if set(context) != set(domain.fields[i].name for i in range(len(domain.fields))):
        raise ValueError("source point must supply every domain field")
    for field in domain.fields:
        _validate_contract_scalar(field, context[field.name])
    complete = _fact_predicate(geometry.graph, context)
    reasoner = Reasoner(geometry.graph)
    if reasoner.is_empty(reasoner.intersect(domain.predicate_id, complete)):
        raise ValueError("source point lies outside its named domain")
    if reasoner.is_empty(reasoner.intersect(finding.region_predicate_id, complete)):
        raise ValueError("source point lies outside its finding region")
    source_predicates = {
        source.source_id: source.predicate_id for source in geometry.sources
    }
    if finding.code in {
        "source_overlap",
        "duplicate_source",
        "singleton_boundary_overlap",
    }:
        if len(finding.source_ids) != 2 or any(
            identifier not in source_predicates for identifier in finding.source_ids
        ):
            raise ValueError("overlap point must name its two current sources")
        if any(
            reasoner.is_empty(reasoner.intersect(source_predicates[source], complete))
            for source in finding.source_ids
        ):
            raise ValueError("overlap point does not satisfy every named source")
    elif finding.code == "coverage_gap" and any(
        not reasoner.is_empty(reasoner.intersect(source.predicate_id, complete))
        for source in geometry.sources
    ):
        raise ValueError("coverage point is covered by a source")

    if reasoner.is_empty(
        reasoner.intersect(geometry.compilation_domain.predicate_id, complete)
    ):
        raise ValueError("source point lies outside its named partition")


def _profile_witness(
    geometry: AnalysisGeometry,
    contract: ContextContract,
    profile: ResolutionProfile,
    counterexample: ProfileCounterexample,
) -> Witness:
    fields = {field.name: field for field in contract.fields}
    first, second = counterexample.contexts
    provided = {
        name: encode_scalar(
            first[name],
            fields[name].data_type,
            timezone=fields[name].timezone,
            context=True,
        )
        for name in counterexample.state.presence
    }
    unavailable = tuple(sorted(set(fields) - set(provided)))
    contexts = tuple(
        {
            name: encode_scalar(
                context[name],
                field.data_type,
                timezone=field.timezone,
                context=True,
            )
            for name, field in fields.items()
        }
        for context in (first, second)
    )
    return Witness(
        kind="pair",
        contexts=contexts,
        profile_ref={
            "contract_id": contract.contract_id,
            "profile_id": profile.profile_id,
        },
        request=WitnessRequest(
            provided_values=provided,
            unavailable_fields=unavailable,
            dont_care=counterexample.state.dont_care,
        ),
    )


def _finding(
    analysis: AnalysisInput,
    rule: DiagnosticRule,
    *,
    scope: Scope,
    source_ids: t.Iterable[str] = (),
    region_predicate_id: str | None = None,
    witnesses: t.Iterable[Witness] = (),
) -> Finding:
    retained = tuple(witnesses)
    if len(retained) > rule.max_witnesses:
        retained = retained[: rule.max_witnesses]
    payload = {
        "schema_version": 1,
        "analysis_input_id": analysis.id,
        "stage": "source",
        "check_id": rule.check_id,
        "code": rule.code,
        "severity": rule.severity,
        "scope": scope.model_dump(mode="json"),
        "source_ids": sorted(source_ids),
        "cell_ids": [],
        "region_predicate_id": region_predicate_id,
        "witnesses": [item.model_dump(mode="json") for item in retained],
        "witnesses_complete": False,
    }
    return Finding(id=content_id("finding", payload), **payload)


def _authored_boundary_overlap(
    geometry: AnalysisGeometry, source_ids: tuple[str, ...], region: str
) -> bool:
    """Recognize only inclusive authored interval contacts, never clipping artifacts."""
    if len(source_ids) != 2:
        return False
    sources = {source.source_id: source for source in geometry.sources}
    left, right = (sources.get(identifier) for identifier in source_ids)
    if left is None or right is None:
        return False
    left_origins = {origin.get("dimension_name"): origin for origin in left.origins}
    right_origins = {origin.get("dimension_name"): origin for origin in right.origins}
    reasoner = Reasoner(geometry.graph)
    point = reasoner.witness(region)
    if point is None:
        return False
    for name in sorted(set(left_origins) & set(right_origins)):
        left_node = geometry.graph.nodes.get(left_origins[name].get("predicate_id"))
        right_node = geometry.graph.nodes.get(right_origins[name].get("predicate_id"))
        if (
            not isinstance(left_node, t.Mapping)
            or not isinstance(right_node, t.Mapping)
            or left_node.get("op") != right_node.get("op") != "interval"
            or left_node.get("field") != right_node.get("field")
            or left_node.get("field") not in geometry.graph.fields
        ):
            continue
        field = t.cast(str, left_node["field"])
        contacts = (
            (
                left_node.get("upper"),
                right_node.get("lower"),
                left_node.get("upper_closed"),
                right_node.get("lower_closed"),
            ),
            (
                right_node.get("upper"),
                left_node.get("lower"),
                right_node.get("upper_closed"),
                left_node.get("lower_closed"),
            ),
        )
        for upper, lower, upper_closed, lower_closed in contacts:
            if (
                isinstance(upper, t.Mapping)
                and isinstance(lower, t.Mapping)
                and dict(upper) == dict(lower)
                and upper_closed is True
                and lower_closed is True
                and reasoner.is_empty(
                    geometry.graph.and_(
                        region,
                        geometry.graph.not_(geometry.graph.eq(field, point[field])),
                    )
                )
            ):
                return True
    return False


def _union_regions(graph: PredicateGraph, regions: t.Iterable[str]) -> str | None:
    items = tuple(sorted(set(regions)))
    if not items:
        return None
    return graph.or_(*items)


def produce_source_report(
    analysis: AnalysisInput,
    geometry_for: t.Callable[[t.Mapping[str, t.Any]], AnalysisGeometry],
    contracts: t.Sequence[ContextContract],
    *,
    dimension_fields: t.Mapping[str, str],
    partition_refs: t.Iterable[t.Mapping[str, t.Any]],
    source_counts: t.Sequence[int],
    guard_fields: t.Iterable[str] = (),
    ordered_fields: t.Iterable[str] = (),
) -> tuple[tuple[Finding, ...], ValidationReport]:
    """Convert complete kernel proofs into the one canonical source report.

    The engine supplies one partition at a time.  The producer retains only
    immutable proof records, so temporary cells and native geometry can be
    released before the next partition is prepared.
    """
    if not isinstance(analysis, AnalysisInput):
        raise TypeError("analysis must be an AnalysisInput")
    if not callable(geometry_for):
        raise TypeError("geometry_for must be callable")
    partition_values = tuple(partition_refs)
    contracts = tuple(contracts)
    validate_source_policy(
        analysis,
        contracts,
        partition_refs=partition_values,
        source_counts=source_counts,
        ordered_fields=ordered_fields,
    )

    def _geometry(partition: t.Mapping[str, t.Any]) -> AnalysisGeometry:
        value = geometry_for(partition)
        if not isinstance(value, AnalysisGeometry):
            raise TypeError("geometry_for must return AnalysisGeometry")
        if canonical_bytes(value.partition_identity) != canonical_bytes(partition):
            raise ValueError("geometry_for returned a mismatched partition")
        return value

    policy = analysis.validation_policy
    partitions = tuple(
        sorted((dict(item) for item in partition_values), key=canonical_bytes)
    )
    contract_pairs = tuple(
        {"contract_id": contract.contract_id, "profile_id": profile.profile_id}
        for contract in sorted(contracts, key=lambda item: item.contract_id)
        for profile in contract.profiles
    )
    full_scope = _scope(partitions, analysis.domain_digests, contract_pairs)
    findings: list[Finding] = []
    by_check: dict[str, list[Finding]] = {name: [] for name in _SOURCE_CHECKS}
    interned_findings: dict[str, Finding] = {}
    ordered = frozenset(ordered_fields)
    coverage_evidence: dict[
        tuple[bytes, str, str], tuple[str | None, Witness | None]
    ] = {}

    def add(
        check_id: str,
        code: str,
        scope: Scope,
        geometry: AnalysisGeometry,
        *,
        source_ids: t.Iterable[str] = (),
        region: str | None = None,
        witness: Witness | None = None,
    ) -> Finding:
        rule = _rule_for(policy, check_id, code, scope)
        examples = () if witness is None or rule.witness_kind == "none" else (witness,)
        item = _finding(
            analysis,
            rule,
            scope=scope,
            source_ids=source_ids,
            region_predicate_id=region,
            witnesses=examples,
        )
        existing = interned_findings.get(item.id)
        if existing is not None:
            return existing
        geometry.graph.budget.reserve(
            "max_output_bytes",
            1024 + 512 * len(examples),
            phase="source-report.finding",
            units="finding output bytes",
        )
        geometry.graph.budget.reserve(
            "max_live_bytes",
            1024 + 512 * len(examples),
            phase="source-report.finding",
            units="retained finding bytes",
        )
        if witness is not None and examples and witness.profile_ref is None:
            verify_source_point(geometry, item, witness)
        interned_findings[item.id] = item
        findings.append(item)
        by_check[check_id].append(item)
        return item

    for partition_index, partition in enumerate(partitions):
        geometry = _geometry(partition)
        partition_scope = _scope(
            (geometry.partition_identity,), (analysis.compilation_domain_ref,)
        )
        if geometry.sources:
            _rule_for(
                policy, "source_predicates", "unreachable_source", partition_scope
            )
        if len(geometry.sources) > 1:
            _rule_for(policy, "source_overlaps", "source_overlap", partition_scope)
            _rule_for(policy, "source_overlaps", "duplicate_source", partition_scope)
            if ordered:
                _rule_for(
                    policy,
                    "source_overlaps",
                    "singleton_boundary_overlap",
                    partition_scope,
                )
        for requirement in policy.coverage_requirements:
            requirement_scope = _scope(
                (geometry.partition_identity,),
                (requirement.domain_ref,),
                requirement.scope.profile_refs,
            )
            if _scope_covers(requirement.scope, requirement_scope):
                rule = _rule_for(policy, "coverage", "coverage_gap", requirement_scope)
                if rule.severity != requirement.severity:
                    raise ValueError(
                        "coverage diagnostic severity disagrees with requirement"
                    )
        proof = prove_source_geometry(geometry)
        if proof.cell_excess or proof.cell_gaps:
            raise ValueError("canonical source/cell geometry invariant failed")
        for source_id in proof.unreachable_source_ids:
            add(
                "source_predicates",
                "unreachable_source",
                partition_scope,
                geometry,
                source_ids=(source_id,),
            )
        duplicate_regions = {
            (item.source_ids, item.predicate_id) for item in proof.duplicates
        }
        for region in proof.overlaps:
            code = "source_overlap"
            # An exact fixed ordered coordinate remains boundary evidence when
            # extruded through other dimensions.  Origins are retained separately
            # by the adapter; this conservative classification never drops overlap.
            if _authored_boundary_overlap(
                geometry, region.source_ids, region.predicate_id
            ):
                code = "singleton_boundary_overlap"
            domain = geometry.provider_domains[analysis.compilation_domain_ref]
            witness = _source_point(geometry, domain, region.predicate_id)
            add(
                "source_overlaps",
                code,
                partition_scope,
                geometry,
                source_ids=region.source_ids,
                region=region.predicate_id,
                witness=witness,
            )
        for ids, predicate_id in duplicate_regions:
            domain = geometry.provider_domains[analysis.compilation_domain_ref]
            add(
                "source_overlaps",
                "duplicate_source",
                partition_scope,
                geometry,
                source_ids=ids,
                region=predicate_id,
                witness=_source_point(geometry, domain, predicate_id),
            )

        source_union = geometry.graph.or_(
            *(
                geometry.graph.and_(
                    geometry.compilation_domain.predicate_id, source.predicate_id
                )
                for source in geometry.sources
            )
        )
        reasoner = Reasoner(geometry.graph)
        for requirement in policy.coverage_requirements:
            if not _scope_covers_nonprofiles(
                requirement.scope,
                _scope((geometry.partition_identity,), (requirement.domain_ref,)),
            ):
                continue
            key = (
                canonical_bytes(geometry.partition_identity),
                requirement.domain_ref,
                requirement.region_predicate_id,
            )
            evidence = coverage_evidence.get(key)
            if evidence is None:
                domain = geometry.provider_domains.get(requirement.domain_ref)
                if domain is None:
                    raise ValueError("coverage requirement names an unavailable domain")
                global_required = geometry.graph.and_(
                    domain.predicate_id, requirement.region_predicate_id
                )
                if not reasoner.is_empty(
                    reasoner.difference(
                        global_required, geometry.global_compilation_domain.predicate_id
                    )
                ):
                    raise ValueError(
                        "coverage requirement region exceeds global compilation domain"
                    )
                required = geometry.graph.and_(
                    domain.predicate_id,
                    requirement.region_predicate_id,
                    geometry.compilation_domain.predicate_id,
                )
                gap = reasoner.difference(required, source_union)
                if reasoner.is_empty(gap):
                    evidence = (None, None)
                else:
                    evidence = (gap, _source_point(geometry, domain, gap))
                coverage_evidence[key] = evidence
            gap, witness = evidence
            if gap is None or witness is None:
                continue
            scope = _scope(
                (geometry.partition_identity,),
                (requirement.domain_ref,),
                requirement.scope.profile_refs,
            )
            add(
                "coverage",
                "coverage_gap",
                scope,
                geometry,
                region=gap,
                witness=witness,
            )
        if partition_index == 0:
            for contract in contracts:
                routing_scope = _scope(
                    partitions,
                    (contract.domain_ref,),
                    (
                        {
                            "contract_id": contract.contract_id,
                            "profile_id": profile.profile_id,
                        }
                        for profile in contract.profiles
                    ),
                )
                _rule_for(policy, "routing", "routing_gap", routing_scope)
                _rule_for(policy, "routing", "routing_ambiguity", routing_scope)
                routing = prove_routing(geometry, contract, guard_fields=guard_fields)
                for code, evidence in (
                    ("routing_gap", routing.no_route),
                    ("routing_ambiguity", routing.ambiguous),
                ):
                    region = _union_regions(
                        geometry.graph, (item.predicate_id for item in evidence)
                    )
                    if region is not None:
                        add("routing", code, routing_scope, geometry, region=region)
        for contract in contracts:
            for profile in contract.profiles:
                if profile.promise == "candidate_only":
                    continue
                profile_scope = _scope(
                    (geometry.partition_identity,),
                    (contract.domain_ref,),
                    (
                        {
                            "contract_id": contract.contract_id,
                            "profile_id": profile.profile_id,
                        },
                    ),
                )
                profile_rule = _rule_for(
                    policy, "profiles", "profile_counterexample", profile_scope
                )
                if (
                    profile.promise == "definite_outcome"
                    and profile_rule.severity != "error"
                ):
                    raise ValueError("definite-outcome counterexamples must be errors")
                profile_dimension_fields = {
                    name: field
                    for name, field in dimension_fields.items()
                    if name in profile.dimensions
                }
                profile_proof = prove_profile(
                    geometry,
                    contract,
                    profile.profile_id,
                    dimension_fields=profile_dimension_fields,
                    guard_fields=guard_fields,
                )
                if not profile_proof.counterexamples:
                    continue
                witness = _profile_witness(
                    geometry, contract, profile, profile_proof.counterexamples[0]
                )
                verify_witness(
                    geometry,
                    contract,
                    witness,
                    dimension_fields=profile_dimension_fields,
                    guard_fields=guard_fields,
                )
                add(
                    "profiles",
                    "profile_counterexample",
                    profile_scope,
                    geometry,
                    witness=witness,
                )

    checks: list[ReportCheck] = []
    for check_id in sorted(_SOURCE_CHECKS):
        report_findings = tuple(sorted((item.id for item in by_check[check_id])))
        status = (
            "findings"
            if report_findings
            else (
                "not_required"
                if check_id == "coverage" and not policy.coverage_requirements
                else "passed"
            )
        )
        checks.append(
            ReportCheck(
                check_id=check_id,
                scope=full_scope,
                status=status,
                complete=True,
                finding_ids=report_findings,
            )
        )
    # Global coverage and explicit-profile coverage remain independent evidence:
    # a profile-bearing check cannot authorize the global obligation (and vice
    # versa), even when their partition/domain members happen to coincide.
    for requirement in policy.coverage_requirements:
        scope = requirement.scope
        if any(
            check.check_id == "coverage" and check.scope == scope for check in checks
        ):
            continue
        scoped = tuple(
            sorted(
                item.id
                for item in by_check["coverage"]
                if _scope_covers(scope, item.scope)
            )
        )
        checks.append(
            ReportCheck(
                check_id="coverage",
                scope=scope,
                status="findings" if scoped else "passed",
                complete=True,
                finding_ids=scoped,
            )
        )
    for requirement in policy.required_checks:
        if requirement.stage != "source" or any(
            check.check_id == requirement.check_id
            and _check_covers_requirement(check.scope, requirement.scope)
            for check in checks
        ):
            continue
        scoped = tuple(
            sorted(
                item.id
                for item in by_check[requirement.check_id]
                if _scope_covers(requirement.scope, item.scope)
            )
        )
        checks.append(
            ReportCheck(
                check_id=requirement.check_id,
                scope=requirement.scope,
                status="findings" if scoped else "passed",
                complete=True,
                finding_ids=scoped,
            )
        )
    checks.sort(
        key=lambda item: (
            item.check_id,
            canonical_bytes(item.scope.model_dump(mode="json")),
        )
    )
    finding_ids = tuple(sorted(item.id for item in findings))
    payload = {
        "schema_version": 1,
        "analysis_input_id": analysis.id,
        "stage": "source",
        "artifact_id": None,
        "validator": {
            "validator_id": "mountainash-rules",
            "semantic_version": "source-analysis-1",
        },
        "scope": full_scope.model_dump(mode="json"),
        "checks": [item.model_dump(mode="json") for item in checks],
        "finding_ids": list(finding_ids),
    }
    report = ValidationReport(id=content_id("report", payload), **payload)
    validate_source_report_semantics(report)
    return tuple(sorted(findings, key=lambda item: item.id)), report
