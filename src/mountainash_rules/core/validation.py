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

from mountainash_rules.core.codec import canonical_bytes, content_id, validate_id
from mountainash_rules.core.contracts import (
    AnalysisInput,
    ContextContract,
    ContractBinding,
    CoverageRequirement,
    DiagnosticRule,
    DomainDefinition,
    Finding,
    ResolutionProfile,
    Scope,
    SemanticVersions,
    ValidatedBuildInput,
    ValidationBundle,
    ValidationReport,
    WarningApproval,
    Witness,
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

    def __post_init__(self) -> None:
        _uuid(self.source_id, "source_id")
        validate_id(self.predicate_id, "predicate")


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
        if not isinstance(self.outputs, t.Mapping) or not self.outputs:
            raise ValueError("cell outputs must be a non-empty mapping")
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
        node = dict(self.source.nodes[identifier])
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


def _scope_refs(values: t.Iterable[t.Mapping[str, t.Any]]) -> set[bytes]:
    return {canonical_bytes(dict(value)) for value in values}


def _scope_covers_nonprofiles(outer: Scope, inner: Scope) -> bool:
    return _scope_refs(outer.partition_refs) >= _scope_refs(
        inner.partition_refs
    ) and set(outer.domain_refs) >= set(inner.domain_refs)


def _scope_covers(outer: Scope, inner: Scope) -> bool:
    return _scope_covers_nonprofiles(outer, inner) and _scope_refs(
        outer.profile_refs
    ) >= _scope_refs(inner.profile_refs)


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
            _check_covers_requirement(check.scope, scope) for scope in coverage_scopes
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
