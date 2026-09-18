"""Exact accumulator construction and contract-bound context resolution."""

from __future__ import annotations

import typing as t

from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import (
    BooleanCoercion,
    DimensionRole,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.engines.accumulator.lattice import Lattice

if t.TYPE_CHECKING:
    pass


class AccumulatorEngine:
    """Build immutable exact cells under source-validation and resource gates."""

    def __init__(
        self,
        dimension_metadata: DimensionsMetadata,
        aggregates: list[Aggregate] | None = None,
        *,
        boolean_coercion: BooleanCoercion = BooleanCoercion.NONE,
        segmentation_dimensions=(),
        limits,
    ):
        from mountainash_rules.core.contracts import ExactLimits
        from mountainash_rules.engines.accumulator.aggregate import validate_aggregates
        from mountainash_rules.engines.accumulator.compiler import _dimension_record

        if boolean_coercion is not BooleanCoercion.NONE:
            raise ValueError(
                "Accumulator boolean_coercion must be BooleanCoercion.NONE"
            )
        if not isinstance(limits, ExactLimits):
            raise ValueError("limits must be ExactLimits")
        if not isinstance(dimension_metadata, DimensionsMetadata):
            raise ValueError("dimension_metadata must be DimensionsMetadata")
        if isinstance(segmentation_dimensions, (str, bytes)) or not isinstance(
            segmentation_dimensions, t.Sequence
        ):
            raise ValueError(
                "segmentation_dimensions must be a dimension-name sequence"
            )
        dimensions = {d.dimension_name: d for d in dimension_metadata.dimensions}
        if any(
            type(name) is not str or name not in dimensions
            for name in segmentation_dimensions
        ):
            raise ValueError("Unknown segmentation dimension")
        if len(set(segmentation_dimensions)) != len(segmentation_dimensions):
            raise ValueError("Repeated segmentation dimensions")
        self._limits = limits
        self._metadata = dimension_metadata.model_copy(deep=True)
        self._dimension_records = tuple(
            _dimension_record(dimension)
            for dimension in sorted(
                self._metadata.dimensions, key=lambda item: item.dimension_name
            )
        )
        self._aggregates = tuple(
            sorted(
                validate_aggregates(aggregates or ()), key=lambda item: item.output_name
            )
        )
        self._segmentation_fields = tuple(
            sorted(
                {
                    dimensions[name].resolved_context_field
                    for name in segmentation_dimensions
                }
            )
        )
        self._context_key_dims = tuple(
            d for d in self._metadata.dimensions if d.role is DimensionRole.CONTEXT_KEY
        )

    def _prepare_build(self, rules, validation, budget):
        from mountainash_rules.engines.accumulator.analysis import prepare_build
        from mountainash_rules.engines.accumulator.tables import source_rows

        return prepare_build(
            source_rows(rules, budget=budget),
            validation=validation,
            metadata=self._metadata,
            aggregates=self._aggregates,
            budget=budget,
        )

    def _build_partition(self, preparation, key_values, budget):
        from mountainash_rules.engines.accumulator.analysis import (
            produce_compiled_evidence,
        )
        from mountainash_rules.engines.accumulator.compiler import analyze_sources
        from mountainash_rules.engines.accumulator.layout import (
            discover_scoped,
            materialize_layout,
            validate_layout,
        )
        from mountainash_rules.engines.accumulator.state import materialize_state

        discovery = discover_scoped(
            preparation.prepared,
            key_values=key_values,
            segmentation_fields=self._segmentation_fields,
        )
        analysis = analyze_sources(
            preparation.prepared, key_values=key_values, overlay=discovery.overlay
        )
        layout = materialize_layout(analysis, discovery)
        validate_layout(analysis, layout, budget=budget)
        evidence = produce_compiled_evidence(preparation, analysis, budget=budget)
        state = materialize_state(analysis, layout, budget=budget)
        return Lattice._from_exact(state, self._metadata, evidence)

    def build(self, rules, *, validation, partition_key=None):
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.core.scalar import decode_scalar

        budget = OperationBudget(self._limits, "build")
        preparation = self._prepare_build(rules, validation, budget)
        if self._context_key_dims:
            names = {d.dimension_name for d in self._context_key_dims}
            if not isinstance(partition_key, dict) or set(partition_key) != names:
                raise ValueError("partition_key must name every context-key dimension")
        elif partition_key not in (None, {}):
            raise ValueError("Unpartitioned build cannot select partition keys")
        wildcards = {
            d.dimension_name: None
            if d.data_type == "bool"
            else unknown_sentinel_for(d.data_type)
            for d in self._context_key_dims
        }
        for keys in preparation.prepared.routing["payload"]["partition_keys"]:
            values = {
                key["dimension_name"]: (
                    decode_scalar(dict(key["match"]["value"]))
                    if key["match"]["kind"] == "value"
                    else wildcards[key["dimension_name"]]
                )
                for key in keys
            }
            if values == (partition_key or {}):
                return self._build_partition(preparation, keys, budget)
        raise ValueError("partition_key is not declared by source validation")

    def build_all(self, rules, *, validation):
        """Prepare once and publish all declared partitions atomically."""
        from mountainash_rules.core.contracts import OperationBudget

        budget = OperationBudget(self._limits, "build_all")
        preparation = self._prepare_build(rules, validation, budget)
        return [
            self._build_partition(preparation, keys, budget)
            for keys in preparation.prepared.routing["payload"]["partition_keys"]
        ]

    def _check_lattice(self, lattice):
        if not isinstance(lattice, Lattice):
            raise ValueError("lattice must be Lattice")
        lattice._require_exact()
        if self._dimension_records != tuple(
            lattice._state.analysis.prepared.metadata["payload"]["dimensions"]
        ):
            raise ValueError("Engine dimensions disagree with the compiled artifact")
        if self._aggregates != tuple(
            sorted(lattice._aggregates, key=lambda item: item.output_name)
        ):
            raise ValueError("Engine aggregates disagree with the compiled artifact")

    def apply(self, lattice, context, *, contract_id, profile_id, dont_care=None):
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.engines.accumulator.runtime import EvaluationSession

        self._check_lattice(lattice)
        session = EvaluationSession(OperationBudget(self._limits, "apply"))
        return session.resolve(
            lattice,
            context,
            contract_id=contract_id,
            profile_id=profile_id,
            dont_care=dont_care,
        ).raise_for_status()

    def index(self, lattices):
        from mountainash_rules.engines.accumulator.lattice import LatticeIndex

        return LatticeIndex(self, lattices)

    def apply_auto(self, lattices, context, *, contract_id, profile_id, dont_care=None):
        from mountainash_rules.core.contracts import OperationBudget
        from mountainash_rules.engines.accumulator.lattice import LatticeIndex
        from mountainash_rules.engines.accumulator.runtime import EvaluationSession

        budget = OperationBudget(self._limits, "apply_auto")
        index = LatticeIndex._with_budget(self, lattices, budget)
        return index._apply(
            context,
            contract_id=contract_id,
            profile_id=profile_id,
            dont_care=dont_care,
            session=EvaluationSession(budget),
        ).raise_for_status()
