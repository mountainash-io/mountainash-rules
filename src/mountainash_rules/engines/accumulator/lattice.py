import itertools
import pathlib
import typing as t

from mountainash.relations import Relation, relation

from mountainash_rules.core.constants import (
    DataType,
    MatchStrategy,
)


class Lattice:
    """Immutable exact artifact view, or explicitly inspection-only flat data."""

    def __init__(self, dataframe, metadata, aggregates, partition_key):
        self._df = dataframe
        self._metadata = metadata.model_copy(deep=True)
        self._aggregates = tuple(aggregates)
        self._partition_key = dict(partition_key) if partition_key is not None else None
        self._state = None
        self._evidence = None

    @classmethod
    def _from_exact(cls, state, metadata, evidence):
        from mountainash_rules.core.scalar import decode_scalar
        from mountainash_rules.core.constants import unknown_sentinel_for

        analysis = state.analysis
        key_types = {d.dimension_name: d.data_type for d in metadata.dimensions}
        keys = {
            key["dimension_name"]: (
                decode_scalar(dict(key["match"]["value"]))
                if key["match"]["kind"] == "value"
                else (
                    None
                    if key_types[key["dimension_name"]] is DataType.BOOL
                    else unknown_sentinel_for(key_types[key["dimension_name"]])
                )
            )
            for key in analysis.partition_identity["key_values"]
        }
        result = cls(None, metadata, analysis.prepared.aggregates, keys or None)
        result._state = state
        result._evidence = evidence
        result._execution_graph = state.analysis.prepared.graph
        return result

    def _require_exact(self):
        if self._state is None:
            raise ValueError("Flat/legacy lattices are inspection-only")
        return self._state

    @property
    def artifact_kind(self):
        return "exact_cells" if self._state is not None else None

    @property
    def artifact_id(self):
        return self._require_exact().analysis.artifact_id

    @property
    def partition_identity(self):
        return self._require_exact().analysis.partition_identity

    @property
    def combinations(self):
        if self._state is not None:
            return self._state.cells.collect()
        return self._df.clone() if hasattr(self._df, "clone") else self._df.copy()

    @property
    def count(self):
        return (
            len(self._state.analysis.cells)
            if self._state
            else relation(self._df).count_rows()
        )

    @property
    def partition_key(self):
        return dict(self._partition_key) if self._partition_key is not None else None

    @property
    def metadata(self):
        return self._metadata.model_copy(deep=True)

    @property
    def aggregates(self):
        return list(self._aggregates)

    @property
    def contributors(self):
        return self._require_exact().contributors

    @property
    def bindings(self):
        self._require_exact()
        return self._evidence.validation["bindings"]

    def _lineage(self, source_ids, output_fields=None):
        import mountainash.expressions as ma
        from mountainash_rules.engines.accumulator.aggregate import lineage_relation

        state = self._require_exact()
        selected = state.labels.filter(ma.col("source_id").is_in(list(source_ids)))
        declarations = (
            self._aggregates
            if output_fields is None
            else tuple(
                item for item in self._aggregates if item.output_name in output_fields
            )
        )
        return lineage_relation(declarations, selected)

    def lineage(self, cell_id):
        state = self._require_exact()
        cell = next(
            (cell for cell in state.analysis.cells if cell.cell_id == cell_id), None
        )
        if cell is None:
            raise KeyError(cell_id)
        return self._lineage(cell.contributors)

    def save(self, dir_path: "str | pathlib.Path", *, limits) -> "pathlib.Path":
        """Atomically publish a bounded self-contained native or inspection snapshot."""
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.engines.accumulator import persistence

        return persistence.save(self, dir_path, budget=OperationBudget(limits, "save"))

    @classmethod
    def load(cls, dir_path: "str | pathlib.Path", *, limits) -> "Lattice":
        """Restore validated exact state, or bounded legacy inspection only."""
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.engines.accumulator import persistence

        return persistence.load(dir_path, budget=OperationBudget(limits, "load"))

    def with_binding(self, binding, *, evidence, limits) -> "Lattice":
        """Validate complete portable evidence and return an immutable binding view."""
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.engines.accumulator import persistence

        return persistence.with_binding(
            self,
            binding,
            evidence=evidence,
            budget=OperationBudget(limits, "with_binding"),
        )


class AmbiguousPartitionError(KeyError):
    """Two or more partitions tie at top specificity for a context.

    Subclasses KeyError so existing partition-miss handlers keep working;
    reachable at runtime only when index() validation was opted out.
    """


class LatticeIndex:
    """Coherent immutable binding views with exact-key routing and ordered batches."""

    def __init__(self, engine, lattices):
        from mountainash_rules.core.contracts import OperationBudget

        self._initialize(engine, lattices, OperationBudget(engine._limits, "index"))

    @classmethod
    def _with_budget(cls, engine, lattices, budget):
        result = cls.__new__(cls)
        result._initialize(engine, lattices, budget)
        return result

    def _initialize(self, engine, lattices, budget):
        from mountainash_rules.core.codec import _bounded_json_size, canonical_bytes
        from mountainash_rules.core.scalar import decode_scalar
        from mountainash_rules.core.reasoner import Reasoner
        from mountainash_rules.core.validation import AnalysisGeometry, prove_routing
        from mountainash_rules.engines.accumulator.runtime import (
            EvaluationSession,
            select_permission,
        )

        if not isinstance(lattices, t.Sequence) or not lattices:
            raise ValueError("index requires a nonempty sequence of exact lattices")
        budget.reserve(
            "max_live_bytes",
            len(lattices) * 1024,
            phase="index.views",
            units="partition view registry",
        )
        self._retained_bytes = len(lattices) * 1024
        self._engine = engine
        self._lattices = tuple(lattices)
        self._key_dims = tuple(
            sorted(engine._context_key_dims, key=lambda dim: dim.dimension_name)
        )
        self._entries = []
        self._exact = {}
        self._permissions = {}
        routing_id = self._lattices[0].partition_identity["routing_id"]
        permission_shape = None
        seen = set()
        session = EvaluationSession(budget)
        graph = session.graph(self._lattices[0])
        region_entries = []
        for lattice in self._lattices:
            engine._check_lattice(lattice)
            identity = lattice.partition_identity
            if identity["routing_id"] != routing_id:
                raise ValueError("Indexed views require one coherent routing identity")
            keys = identity["key_values"]
            permission_count = sum(
                len(binding.authorized_profiles) for binding in lattice.bindings
            )
            capacity = (
                8
                * _bounded_json_size(
                    (
                        keys,
                        lattice._evidence.context_contracts,
                        lattice._evidence.predicates["domains"],
                    ),
                    budget,
                    phase="index.permissions",
                    counter="max_live_bytes",
                )
                * max(1, permission_count)
            )
            budget.reserve(
                "max_live_bytes",
                capacity,
                phase="index.permissions",
                units="routing keys and parsed permission models",
            )
            self._retained_bytes += capacity
            if tuple(key["dimension_name"] for key in keys) != tuple(
                d.dimension_name for d in self._key_dims
            ):
                raise ValueError("Partition key dimensions disagree with the engine")
            key = tuple(
                canonical_bytes(value["match"]["value"])
                if value["match"]["kind"] == "value"
                else None
                for value in keys
            )
            if key in seen:
                raise ValueError("Duplicate partition identity")
            seen.add(key)
            score = sum(value is not None for value in key)
            self._entries.append((key, score, lattice))
            if score == len(key):
                self._exact[key] = lattice
            shape = {}
            for binding in lattice.bindings:
                for authorization in binding.authorized_profiles:
                    labels = (binding.contract_id, authorization["profile_id"])
                    permission = select_permission(lattice, *labels)
                    shape[labels] = (
                        binding.analysis_input_id,
                        binding.contract_digest,
                        binding.domain_digest,
                        authorization["profile_digest"],
                    )
                    self._permissions[(id(lattice), *labels)] = permission
            if permission_shape is None:
                permission_shape = shape
            elif shape != permission_shape:
                raise ValueError(
                    "Indexed views have incoherent contract/profile permissions"
                )
            terms = [
                graph.eq(
                    dim.resolved_context_field,
                    decode_scalar(dict(value["match"]["value"])),
                )
                for dim, value in zip(self._key_dims, keys, strict=True)
                if value["match"]["kind"] == "value"
            ]
            region_entries.append((graph.and_(*terms), score))
        self._entries = tuple(self._entries)
        self._labels = frozenset(permission_shape or ())
        if self._labels:
            template = self._lattices[0]
            prepared = template._state.analysis.prepared
            contracts = {}
            domains = {}
            for labels in self._labels:
                _, contract, _, provider_domain = self._permissions[
                    (id(template), *labels)
                ]
                contracts[contract.contract_id] = contract
                domains[provider_domain.domain_id] = provider_domain
            geometry = AnalysisGeometry(
                graph=graph,
                compilation_domain=prepared.domain,
                provider_domains=domains,
                partition_identity=template.partition_identity,
                sources=(),
                cells=(),
                global_compilation_domain=prepared.domain,
                routing=prepared.routing["payload"],
            )
            guard_fields = tuple(
                d.resolved_context_field
                for d in template._metadata.dimensions
                if d.match_strategy is MatchStrategy.CONTEXT_REGEX
            )
            registry = prepared.routing["payload"]["partition_keys"]
            selected = {
                canonical_bytes(
                    {"key_values": lattice.partition_identity["key_values"]}
                )
                for lattice in self._lattices
            }
            for contract in contracts.values():
                proof = prove_routing(geometry, contract, guard_fields=guard_fields)
                if proof.ambiguous:
                    raise AmbiguousPartitionError(
                        "Bound provider has an admissible partition specificity tie"
                    )
                if proof.no_route or any(
                    canonical_bytes({"key_values": registry[index]}) not in selected
                    for index in proof.reachable_partition_key_indices
                ):
                    raise ValueError(
                        "Indexed views do not cover the bound provider routing states"
                    )
            return
        reasoner = Reasoner(graph)
        domain = self._lattices[0]._state.analysis.prepared.domain.predicate_id
        for (left, left_score), (right, right_score) in itertools.combinations(
            region_entries, 2
        ):
            budget.reserve(
                "max_work", 1, phase="index.ambiguity", units="partition tie proofs"
            )
            if left_score != right_score:
                continue
            higher = graph.or_(
                *(region for region, score in region_entries if score > left_score)
            )
            tie = graph.and_(domain, left, right, graph.not_(higher))
            if not reasoner.is_empty(tie):
                raise AmbiguousPartitionError(
                    "Selected partition views have an admissible specificity tie"
                )

    def _route(self, key, budget):
        exact = self._exact.get(key)
        if exact is not None:
            return exact
        selected = None
        best = -1
        tied = False
        for pattern, score, lattice in self._entries:
            budget.reserve(
                "max_work", 1, phase="apply.route", units="partition comparisons"
            )
            if all(
                expected is None or expected == actual
                for expected, actual in zip(pattern, key, strict=True)
            ):
                if score > best:
                    selected, best, tied = lattice, score, False
                elif score == best:
                    tied = True
        if selected is None:
            raise KeyError("No lattice matches the concrete partition key")
        if tied:
            raise AmbiguousPartitionError(
                "Context ties at maximum partition specificity"
            )
        return selected

    def _apply(
        self,
        context,
        *,
        contract_id,
        profile_id,
        dont_care,
        session,
        project_candidates=True,
    ):
        from mountainash_rules.core.codec import canonical_bytes
        from mountainash_rules.core.contracts import Issue
        from mountainash_rules.core.scalar import encode_scalar
        from mountainash_rules.engines.accumulator.runtime import invalid_result

        if (contract_id, profile_id) not in self._labels:
            raise ValueError(
                "Requested contract/profile is not coherently bound by the index"
            )
        template = self._lattices[0]
        permission = self._permissions[(id(template), contract_id, profile_id)]
        _, contract, profile, _ = permission
        classified = session.admit(template, context, contract, profile, dont_care)
        invalid_keys = [
            dim
            for dim in self._key_dims
            if any(
                issue.field == dim.resolved_context_field for issue in classified.issues
            )
        ]
        if invalid_keys:
            return invalid_result(
                contract_id,
                profile_id,
                classified.issues,
                budget=session.budget,
                observations=dict(classified.observations),
                output_fields=profile.output_fields,
            )
        fields = template._state.analysis.prepared.graph.fields
        key = tuple(
            canonical_bytes(
                encode_scalar(
                    classified.provided_values[dim.resolved_context_field],
                    dim.data_type,
                    timezone=fields[dim.resolved_context_field].timezone,
                )
            )
            if dim.resolved_context_field in classified.provided_values
            else None
            for dim in self._key_dims
        )
        try:
            lattice = self._route(key, session.budget)
        except KeyError:
            from mountainash_rules.core.reasoner import Reasoner

            graph = session.graph(template)
            raw = graph.and_(
                *(
                    graph.eq(name, value)
                    for name, value in classified.provided_values.items()
                )
            )
            if not Reasoner(graph).is_empty(
                graph.and_(permission[3].predicate_id, raw)
            ):
                raise
            issues = (
                *classified.issues,
                Issue(
                    field=None,
                    dimension=None,
                    code="domain_contradiction",
                    message="Supplied facts contradict the bound joint domain before routing",
                ),
            )
            return invalid_result(
                contract_id,
                profile_id,
                issues,
                budget=session.budget,
                observations=dict(classified.observations),
                output_fields=profile.output_fields,
            )
        return session.resolve(
            lattice,
            context,
            contract_id=contract_id,
            profile_id=profile_id,
            dont_care=dont_care,
            classified=classified,
            permission=self._permissions[(id(lattice), contract_id, profile_id)],
            project_candidates=project_candidates,
        )

    def apply(self, context, *, contract_id, profile_id, dont_care=None):
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.engines.accumulator.runtime import EvaluationSession

        budget = OperationBudget(self._engine._limits, "apply")
        budget.reserve(
            "max_live_bytes",
            self._retained_bytes,
            phase="apply.index",
            units="retained routing and permission state",
        )
        session = EvaluationSession(budget)
        return self._apply(
            context,
            contract_id=contract_id,
            profile_id=profile_id,
            dont_care=dont_care,
            session=session,
        ).raise_for_status()

    def apply_batch(
        self,
        contexts,
        *,
        contract_id,
        profile_id,
        context_id_field=None,
        dont_care_field=None,
        chunk_size=None,
    ):
        import mountainash.expressions as ma
        from mountainash_rules.core.context import _validate_context_ids
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.engines.accumulator.result import AccumulatorBatchResult
        from mountainash_rules.engines.accumulator.runtime import EvaluationSession
        from mountainash_rules.engines.accumulator.tables import (
            make_relation,
            native_column_bytes,
            source_rows,
        )

        if (contract_id, profile_id) not in self._labels:
            raise ValueError(
                "Requested contract/profile is not coherently bound by the index"
            )
        if chunk_size is not None and (type(chunk_size) is not int or chunk_size < 1):
            raise ValueError("chunk_size must be a positive integer")
        if context_id_field is not None and (
            type(context_id_field) is not str or not context_id_field
        ):
            raise ValueError("context_id_field must be a nonempty column name")
        if dont_care_field is not None and (
            type(dont_care_field) is not str or not dont_care_field
        ):
            raise ValueError("dont_care_field must be a nonempty column name")
        budget = OperationBudget(self._engine._limits, "apply_batch")
        budget.reserve(
            "max_live_bytes",
            self._retained_bytes,
            phase="apply.index",
            units="retained routing and permission state",
        )
        session = EvaluationSession(budget)
        raw_rows = None
        if isinstance(contexts, t.Sequence) and not isinstance(contexts, (str, bytes)):
            raw_rows = contexts
            if any(not isinstance(row, t.Mapping) for row in raw_rows):
                raise ValueError("Batch rows must be mappings")
            count = len(raw_rows)
            rel = None
        elif isinstance(contexts, t.Mapping):
            sizes = {len(column) for column in contexts.values()}
            if len(sizes) > 1:
                raise ValueError("Context columns have unequal lengths")
            count = next(iter(sizes), 0)
            budget.reserve(
                "max_live_bytes",
                count * (256 + 128 * len(contexts)),
                phase="batch.rows",
                units="original typed row mappings",
            )
            raw_rows = tuple(
                {name: column[index] for name, column in contexts.items()}
                for index in range(count)
            )
            rel = None
        else:
            rel = contexts if isinstance(contexts, Relation) else relation(contexts)
            count = rel.count_rows()
        budget.reserve(
            "max_work", count, phase="batch.identity", units="global context identities"
        )
        budget.reserve(
            "max_live_bytes",
            count * 256,
            phase="batch.identity",
            units="global identity and result references",
        )
        if context_id_field is not None:
            if raw_rows is not None:
                if isinstance(contexts, t.Mapping):
                    if context_id_field not in contexts:
                        raise ValueError("context_id_field is absent from contexts")
                    raw_ids = contexts[context_id_field]
                else:
                    if not raw_rows or any(
                        context_id_field not in row for row in raw_rows
                    ):
                        raise ValueError("context_id_field is absent from contexts")
                    raw_ids = [row[context_id_field] for row in raw_rows]
                size = sum(
                    128 + (4 * len(value) if isinstance(value, str) else 32)
                    for value in raw_ids
                )
                budget.reserve(
                    "max_input_bytes",
                    size,
                    phase="batch.identity",
                    units="native ID bytes",
                )
                budget.reserve(
                    "max_live_bytes",
                    size * 3,
                    phase="batch.identity",
                    units="native ID transfer buffers",
                )
                identities = relation({"__context_id": raw_ids})
                _validate_context_ids(identities, "__context_id")
            else:
                size, _ = native_column_bytes(
                    rel, context_id_field, budget=budget, phase="batch.identity"
                )
                budget.reserve(
                    "max_input_bytes",
                    size,
                    phase="batch.identity",
                    units="native ID bytes",
                )
                budget.reserve(
                    "max_live_bytes",
                    4 * size,
                    phase="batch.identity",
                    units="native ID validation buffers",
                )
                _validate_context_ids(rel, context_id_field)
                identities = rel.select(ma.col(context_id_field).alias("__context_id"))
        else:
            has_reserved_id = (
                "__context_id" in contexts
                if isinstance(contexts, t.Mapping)
                else any("__context_id" in row for row in raw_rows)
                if raw_rows is not None
                else "__context_id" in rel.columns
            )
            if has_reserved_id:
                raise ValueError(
                    "Contexts contain reserved __context_id; specify context_id_field"
                )
            identities = make_relation(
                [{"__context_id": index} for index in range(count)],
                [("__context_id", "int64", False)],
                budget=budget,
            )
        if dont_care_field is not None:
            present = (
                dont_care_field in contexts
                if isinstance(contexts, t.Mapping)
                else any(dont_care_field in row for row in raw_rows)
                if raw_rows is not None
                else dont_care_field in rel.columns
            )
            if not present:
                raise ValueError("dont_care_field is absent from contexts")
        results = []
        width = chunk_size or max(1, count)
        for offset in range(0, count, width):
            rows = (
                raw_rows[offset : offset + width]
                if raw_rows is not None
                else source_rows(rel.slice(offset, width), budget=budget)
            )
            for row in rows:
                results.append(
                    self._apply(
                        row,
                        contract_id=contract_id,
                        profile_id=profile_id,
                        dont_care=row.get(dont_care_field) if dont_care_field else None,
                        session=session,
                        project_candidates=False,
                    )
                )
        profile = self._permissions[(id(self._lattices[0]), contract_id, profile_id)][2]
        return AccumulatorBatchResult(
            tuple(results),
            identities,
            context_id_field=context_id_field or "__context_id",
            output_fields=profile.output_fields,
            template_lattice=self._lattices[0],
            budget=budget,
        )
