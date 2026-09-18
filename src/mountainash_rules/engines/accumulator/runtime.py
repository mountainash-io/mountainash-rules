"""Operation-owned exact request reasoning over immutable compiled artifacts."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel

from mountainash_rules.core.codec import _bounded_json_size
from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.context import classify_exact_context
from mountainash_rules.core.contracts import (
    ContextContract,
    DomainDefinition,
    Issue,
    OperationBudget,
    OutcomeRecord,
)
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.core.reasoner import Reasoner
from mountainash_rules.core.scalar import encode_scalar
from mountainash_rules.engines.accumulator.result import AccumulatorResult


def select_permission(lattice, contract_id, profile_id):
    """Permissions belong to this exact immutable view, not engine construction."""
    lattice._require_exact()
    if type(contract_id) is not str or type(profile_id) is not str:
        raise ValueError("contract_id and profile_id must be explicit strings")
    matches = [
        binding
        for binding in lattice.bindings
        if binding.contract_id == contract_id
        and binding.artifact_id == lattice.artifact_id
    ]
    if len(matches) != 1:
        raise ValueError("Requested artifact/contract binding is unavailable")
    binding = matches[0]
    if not any(
        item["profile_id"] == profile_id for item in binding.authorized_profiles
    ):
        raise ValueError("Requested profile is not authorized by this binding")
    envelope = next(
        (
            item
            for item in lattice._evidence.context_contracts
            if item["id"] == binding.contract_digest
        ),
        None,
    )
    if envelope is None:
        raise ValueError("Bound context contract is unavailable")
    contract = ContextContract.model_validate(dict(envelope["payload"]["contract"]))
    profile = next(
        (item for item in contract.profiles if item.profile_id == profile_id), None
    )
    if profile is None:
        raise ValueError("Requested profile is absent from its contract")
    domain_envelope = next(
        (
            item
            for item in lattice._evidence.predicates["domains"]
            if item["id"] == binding.domain_digest
        ),
        None,
    )
    if domain_envelope is None:
        raise ValueError("Bound context domain is unavailable")
    domain = DomainDefinition.model_validate(dict(domain_envelope["payload"]))
    return binding, contract, profile, domain


def invalid_result(
    contract_id,
    profile_id,
    issues,
    *,
    budget,
    lattice=None,
    binding=None,
    observations=None,
    output_fields=(),
):
    outcome = OutcomeRecord(
        status="invalid_context",
        reason=issues[0].code,
        binding_id=binding.id if binding is not None else None,
        contract_id=contract_id,
        profile_id=profile_id,
        values=None,
        cell_id=None,
        contributor_ids=None,
        may_have_no_match=None,
        observations=observations,
        issues=tuple(issues),
    )
    size = _bounded_json_size(
        outcome, budget, phase="apply.invalid", counter="max_output_bytes"
    )
    budget.reserve(
        "max_output_bytes",
        size,
        phase="apply.invalid",
        units="normalized invalid outcome bytes",
    )
    budget.reserve(
        "max_live_bytes",
        4 * size,
        phase="apply.invalid",
        units="retained normalized invalid outcome",
    )
    return AccumulatorResult(
        outcome, lattice=lattice, candidate_ids=None, output_fields=tuple(output_fields)
    )


class EvaluationSession:
    """One ledger and private graph per actual compiled state for an entire call."""

    def __init__(self, budget: OperationBudget):
        self.budget = budget
        self._graphs = {}
        self._evidence = set()
        self._prepared = set()

    def graph(self, lattice):
        state = lattice._require_exact()
        evidence_key = id(lattice._evidence)
        if evidence_key not in self._evidence:
            size = _bounded_json_size(
                lattice._evidence,
                self.budget,
                phase="apply.evidence",
                counter="max_live_bytes",
            )
            self.budget.reserve(
                "max_live_bytes",
                4 * size,
                phase="apply.evidence",
                units="retained evidence and permission models",
            )
            self._evidence.add(evidence_key)
        key = (id(state), id(lattice._execution_graph))
        prepared_key = id(state.analysis.prepared)
        if prepared_key not in self._prepared:
            self.budget.reserve(
                "max_live_bytes",
                state.retained_prepared_bytes,
                phase="apply.sources",
                units="shared immutable source registry",
            )
            self._prepared.add(prepared_key)
        if key in self._graphs:
            return self._graphs[key]
        original = lattice._execution_graph
        size = _bounded_json_size(
            original.nodes, self.budget, phase="apply.graph", counter="max_live_bytes"
        )
        self.budget.reserve(
            "max_live_bytes",
            size * 4 + state.retained_bytes,
            phase="apply.graph",
            units="retained compiled state and graph copy",
        )
        for counter, amount in state.retained_counts.items():
            self.budget.reserve(
                counter,
                amount,
                phase="apply.state",
                units="retained compiled execution state",
            )
        graph = PredicateGraph(original.fields.values(), budget=self.budget)
        self.budget.reserve(
            "max_predicate_nodes",
            max(0, len(original.nodes) - 2),
            phase="apply.graph",
            units="compiled predicate nodes",
        )
        # Payloads and DFA handles are immutable. Only the operation's dictionaries
        # and newly interned request facts can change.
        graph._nodes.update(original.nodes)
        for language in original.languages.values():
            graph.add_language(language)
        self._graphs[key] = graph
        return graph

    def admit(self, lattice, context, contract, profile, dont_care):
        if isinstance(context, BaseModel):
            values = (
                (name, getattr(context, name)) for name in context.model_fields_set
            )
        elif isinstance(context, t.Mapping):
            values = context.items()
        else:
            raise ValueError("Context must be a mapping or Pydantic model")
        size = 256
        for name, value in values:
            self.budget.reserve(
                "max_work", 1, phase="apply.context", units="supplied fields"
            )
            size += 128 + (4 * len(name) if isinstance(name, str) else 64)
            size += 4 * len(value) if isinstance(value, str) else 64
        masks = (
            ()
            if dont_care is None
            else dont_care
            if isinstance(dont_care, t.Sequence)
            and not isinstance(dont_care, (str, bytes))
            else (dont_care,)
        )
        for mask in masks:
            self.budget.reserve(
                "max_work", 1, phase="apply.mask", units="request mask entries"
            )
            size += 128 + (4 * len(mask) if isinstance(mask, str) else 64)
            # Check the growing bound before the classifier allocates its set.
            self.budget.check(
                "max_input_bytes", size, phase="apply.mask", units="request mask bytes"
            )
        self.budget.reserve(
            "max_input_bytes",
            size,
            phase="apply.context",
            units="supplied context bytes",
        )
        self.budget.reserve(
            "max_live_bytes",
            4 * size,
            phase="apply.context",
            units="classification and outcome bytes",
        )
        return classify_exact_context(
            context,
            lattice._metadata,
            contract,
            profile,
            dont_care=() if dont_care is None else dont_care,
        )

    def resolve(
        self,
        lattice,
        context,
        *,
        contract_id,
        profile_id,
        dont_care=None,
        permission=None,
        classified=None,
        project_candidates=True,
    ):
        binding, contract, profile, domain = (
            select_permission(lattice, contract_id, profile_id)
            if permission is None
            else permission
        )
        graph = self.graph(lattice)
        classified = classified or self.admit(
            lattice, context, contract, profile, dont_care
        )
        observations = dict(classified.observations)
        issues = list(classified.issues)
        mandatory = [
            dim
            for dim in lattice._metadata.dimensions
            if dim.match_strategy is MatchStrategy.CONTEXT_REGEX
        ]
        for dim in mandatory:
            field = dim.resolved_context_field
            if field not in classified.provided_values and not any(
                i.field == field for i in issues
            ):
                issues.append(
                    Issue(
                        field=field,
                        dimension=dim.dimension_name,
                        code="missing_required",
                        message="A concrete mandatory field is required",
                    )
                )
        if issues:
            return invalid_result(
                contract_id,
                profile_id,
                issues,
                budget=self.budget,
                lattice=lattice,
                binding=binding,
                observations=observations,
                output_fields=profile.output_fields,
            )

        reasoner = Reasoner(graph)
        raw_facts = graph.and_(
            *(
                graph.eq(name, value)
                for name, value in classified.provided_values.items()
            )
        )
        if not issues:
            from mountainash_rules.engines.accumulator.analysis import (
                _evidence_regex_options,
            )

            for dim in mandatory:
                if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
                    guard = graph.lower_dimension(
                        dim,
                        {},
                        regex_options=_evidence_regex_options(lattice._evidence),
                    )
                    if reasoner.is_empty(graph.and_(guard, raw_facts)):
                        issues.append(
                            Issue(
                                field=dim.resolved_context_field,
                                dimension=dim.dimension_name,
                                code="guard_failed",
                                message="Supplied context fails its mandatory guard",
                            )
                        )
        if not issues and reasoner.is_empty(graph.and_(domain.predicate_id, raw_facts)):
            issues.append(
                Issue(
                    field=None,
                    dimension=None,
                    code="domain_contradiction",
                    message="Supplied facts contradict the bound joint domain",
                )
            )
        if issues:
            return invalid_result(
                contract_id,
                profile_id,
                issues,
                budget=self.budget,
                lattice=lattice,
                binding=binding,
                observations=observations,
                output_fields=profile.output_fields,
            )

        analysis = lattice._state.analysis
        facts = graph.and_(
            *(
                graph.eq(name, value)
                for name, value in classified.effective_values.items()
            )
        )
        query = graph.and_(analysis.domain.predicate_id, domain.predicate_id, facts)
        if reasoner.is_empty(query):
            return invalid_result(
                contract_id,
                profile_id,
                [
                    Issue(
                        field=None,
                        dimension=None,
                        code="domain_contradiction",
                        message="Supplied facts contradict the selected partition domain",
                    )
                ],
                budget=self.budget,
                lattice=lattice,
                binding=binding,
                observations=observations,
                output_fields=profile.output_fields,
            )
        candidates = []
        for cell in analysis.cells:
            self.budget.reserve(
                "max_work", 1, phase="apply.candidates", units="cell feasibility checks"
            )
            if not reasoner.is_empty(graph.and_(query, cell.predicate_id)):
                self.budget.reserve(
                    "max_live_bytes",
                    256,
                    phase="apply.candidates",
                    units="candidate reference",
                )
                candidates.append(cell)
        covered = graph.or_(*(cell.predicate_id for cell in candidates))
        may_have_no_match = not reasoner.is_empty(reasoner.difference(query, covered))
        if project_candidates:
            from mountainash_rules.engines.accumulator.tables import (
                relation_host_bytes,
                relation_native_bytes,
                storage_type,
            )

            outputs = {item.output_name: item for item in lattice._aggregates}
            columns = [
                (name, "utf8", False)
                for name in ("cell_id", "predicate_id", "contributor_set_id")
            ]
            columns.extend(
                (
                    name,
                    storage_type(outputs[name].data_type, outputs[name].timezone),
                    False,
                )
                for name in profile.output_fields
            )
            edge_columns = [("cell_id", "utf8", False), ("source_id", "utf8", False)]

            def cell_rows():
                for cell in candidates:
                    yield {
                        "cell_id": cell.cell_id,
                        "predicate_id": cell.predicate_id,
                        "contributor_set_id": cell.contributor_set_id,
                        **{name: cell.outputs[name] for name in profile.output_fields},
                    }

            def edge_rows():
                for cell in candidates:
                    for source in cell.contributors:
                        yield {"cell_id": cell.cell_id, "source_id": source}

            native = relation_native_bytes(
                cell_rows(), columns
            ) + relation_native_bytes(edge_rows(), edge_columns)
            host = relation_host_bytes(cell_rows(), columns) + relation_host_bytes(
                edge_rows(), edge_columns
            )
            self.budget.reserve(
                "max_contributor_edges",
                sum(len(cell.contributors) for cell in candidates),
                phase="apply.projections",
                units="public contributor rows",
            )
            self.budget.reserve(
                "max_output_bytes",
                native,
                phase="apply.projections",
                units="public candidate relations",
            )
            self.budget.reserve(
                "max_live_bytes",
                host + 3 * native,
                phase="apply.projections",
                units="public candidate transfer buffers",
            )
        values = None
        cell_id = None
        contributor_ids = None
        if profile.mode == "candidates":
            status, reason = "candidates", "candidate_query"
        elif not candidates:
            status, reason = "no_match", "no_rule_matches"
        else:
            first = candidates[0]
            same_outputs = all(
                all(
                    cell.outputs[name] == first.outputs[name]
                    for name in profile.output_fields
                )
                for cell in candidates
            )
            same_contributors = all(
                cell.contributors == first.contributors for cell in candidates
            )
            provenance = (
                profile.provenance == "none"
                or (profile.provenance == "contributors" and same_contributors)
                or (profile.provenance == "cell" and len(candidates) == 1)
            )
            if not may_have_no_match and same_outputs and provenance:
                status, reason = "decision", "established"
                outputs = {item.output_name: item for item in lattice._aggregates}
                values = {
                    name: encode_scalar(
                        first.outputs[name],
                        outputs[name].data_type,
                        timezone=outputs[name].timezone,
                    )
                    for name in profile.output_fields
                }
                cell_id = first.cell_id if len(candidates) == 1 else None
                contributor_ids = first.contributors if same_contributors else None
            else:
                status = {
                    "return": "unresolved",
                    "withhold": "no_match",
                    "reject": "rejected",
                }[profile.on_unresolved]
                reason = "insufficient_context"
        outcome = OutcomeRecord(
            status=status,
            reason=reason,
            binding_id=binding.id,
            contract_id=contract_id,
            profile_id=profile_id,
            values=values,
            cell_id=cell_id,
            contributor_ids=contributor_ids,
            may_have_no_match=may_have_no_match,
            observations=observations,
            issues=(),
        )
        size = _bounded_json_size(
            outcome, self.budget, phase="apply.outcome", counter="max_output_bytes"
        )
        self.budget.reserve(
            "max_output_bytes",
            size,
            phase="apply.outcome",
            units="normalized outcome bytes",
        )
        self.budget.reserve(
            "max_live_bytes",
            4 * size,
            phase="apply.outcome",
            units="retained normalized outcome",
        )
        return AccumulatorResult(
            outcome,
            lattice=lattice,
            candidate_ids=tuple(cell.cell_id for cell in candidates),
            output_fields=profile.output_fields,
        )
