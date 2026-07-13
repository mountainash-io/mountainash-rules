import typing as t

from mountainash.relations import relation

from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.dimension import DimensionsMetadata


class Lattice:
    def __init__(
        self,
        dataframe: t.Any,
        metadata: DimensionsMetadata,
        aggregates: list[Aggregate],
        partition_key: dict | None,
    ) -> None:
        self._df = dataframe
        self._metadata = metadata
        self._aggregates = aggregates
        self._partition_key = partition_key

    @property
    def combinations(self) -> t.Any:
        """The outermost rule combinations frame.

        Callers must not mutate the returned frame; apply-phase engines are
        cached against this object's identity.
        """
        return self._df

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def partition_key(self) -> dict | None:
        return self._partition_key

    @property
    def is_composed(self) -> bool:
        """True when this lattice came out of AccumulatorEngine.build().

        Keyed on the __prime_product tracking column, which only the build
        phase creates; importers strip tracking columns, so imported
        lattices are always flat.
        """
        return "__prime_product" in relation(self._df).columns

    @property
    def metadata(self) -> DimensionsMetadata:
        return self._metadata

    @property
    def aggregates(self) -> list[Aggregate]:
        return self._aggregates


class LatticeIndex:
    """Partition-key routing over a set of built lattices, built once."""

    def __init__(self, engine, lattices: list["Lattice"], context_key_dims) -> None:
        self._engine = engine
        self._context_key_dims = list(context_key_dims)
        self._map: dict[tuple, Lattice] = {}
        for lattice in lattices:
            if lattice.partition_key is not None:
                key = tuple(
                    lattice.partition_key[d.dimension_name]
                    for d in self._context_key_dims
                )
            else:
                key = ()
            self._map[key] = lattice

    def apply(self, context, dimensions=None):
        key = self._engine._extract_partition_key(context)
        if key not in self._map:
            raise KeyError(f"No lattice for partition key {key!r}")
        return self._engine.apply(self._map[key], context, dimensions=dimensions)

    def apply_batch(self, contexts, **kwargs):
        """Partition contexts by CONTEXT_KEY fields; evaluate_batch per lattice."""
        import mountainash.expressions as ma
        from mountainash.relations import concat

        rel = relation(contexts)
        key_fields = [d.resolved_context_field for d in self._context_key_dims]
        if not key_fields:
            (single,) = self._map.values()
            return self._engine._filter_engine_for(single).evaluate_batch(
                contexts, **kwargs
            )

        # Synthesise a GLOBAL context id before partitioning: per-partition
        # batches would otherwise restart ids at 0 and collide after concat.
        if not kwargs.get("context_id_field"):
            rel = rel.with_row_index(name="__lattice_ctx_id")
            kwargs["context_id_field"] = "__lattice_ctx_id"

        combos = rel.select(*[ma.col(f) for f in key_fields]).unique().to_dict()
        frames = []
        n = len(combos[key_fields[0]])
        for i in range(n):
            key = tuple(combos[f][i] for f in key_fields)
            if key not in self._map:
                raise KeyError(f"No lattice for partition key {key!r}")
            part = rel
            for f, v in zip(key_fields, key):
                part = part.filter(ma.col(f).eq(ma.lit(v)))
            engine = self._engine._filter_engine_for(self._map[key])
            frames.append(relation(engine.evaluate_batch(
                part.collect(), **kwargs
            ).survivors))
        merged = concat(frames).collect()

        from mountainash_rules.core.batch_result import BatchRuleResult
        return BatchRuleResult(
            dataframe=merged,
            active_dimensions=[
                d.dimension_name for d in self._engine._constraint_dims
            ],
            context_id_field=kwargs["context_id_field"],
        )
