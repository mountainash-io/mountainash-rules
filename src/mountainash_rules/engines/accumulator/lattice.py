import pathlib
import typing as t
import yaml

from mountainash.relations import relation

from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import (
    DataType,
    HitPolicy,
    MatchStrategy,
    not_set_sentinel_for,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine


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

    def save(self, dir_path: "str | pathlib.Path") -> "pathlib.Path":
        """Persist this lattice as a snapshot directory (parquet + manifest).

        The manifest is a strict superset of babel's LatticeManifest YAML,
        so babel/service tooling can read it unchanged.
        """
        dir_path = pathlib.Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        relation(self._df).to_polars().write_parquet(dir_path / "lattice.parquet")
        manifest = {
            "dimensions": self._metadata.model_dump(
                mode="json", exclude_defaults=True
            ),
            "aggregates": [
                a.model_dump(mode="json") for a in self._aggregates
            ],
            "partition_key": self._partition_key,
        }
        (dir_path / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        return dir_path

    @classmethod
    def load(cls, dir_path: "str | pathlib.Path") -> "Lattice":
        """Rehydrate a snapshot written by save(). Preserves __agg_* and
        __prime_product verbatim (is_composed round-trips honestly)."""
        import polars as pl  # allow: lattice snapshot parquet read pending backend-agnostic file IO

        dir_path = pathlib.Path(dir_path)
        manifest_path = dir_path / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest.yaml in {dir_path}")
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        return cls(
            dataframe=pl.read_parquet(dir_path / "lattice.parquet"),
            metadata=DimensionsMetadata.model_validate(raw["dimensions"]),
            aggregates=[
                Aggregate.model_validate(a) for a in raw.get("aggregates", [])
            ],
            partition_key=raw.get("partition_key"),
        )


class AmbiguousPartitionError(KeyError):
    """Two or more partitions tie at top specificity for a context.

    Subclasses KeyError so existing partition-miss handlers keep working;
    reachable at runtime only when index() validation was opted out.
    """


class LatticeIndex:
    """Partition-key routing over a set of built lattices, built once.

    Routing uses the same ternary + specificity semantics as the
    constraint layer, evaluated by an embedded ExpressionRulesEngine over
    a meta rules-table (one row per partition). Exact key hits keep an
    O(1) dict fast path.
    """

    def __init__(self, engine, lattices: list["Lattice"], context_key_dims) -> None:
        self._engine = engine
        self._context_key_dims = list(context_key_dims)
        self._lattices = list(lattices)
        if not self._lattices:
            raise ValueError("index() requires at least one lattice")

        self._map: dict[tuple, Lattice] = {}
        for lattice in self._lattices:
            if lattice.partition_key is not None:
                key = tuple(
                    lattice.partition_key[d.dimension_name]
                    for d in self._context_key_dims
                )
            elif self._context_key_dims:
                raise ValueError(
                    "Lattice without a partition_key cannot be indexed "
                    "alongside CONTEXT_KEY dimensions"
                )
            else:
                key = ()
            for d, v in zip(self._context_key_dims, key):
                if (
                    d.data_type is not DataType.BOOL
                    and v == not_set_sentinel_for(d.data_type)
                ):
                    raise ValueError(
                        f"Partition key {key!r} contains the NOT_SET "
                        f"sentinel for dimension '{d.dimension_name}'; "
                        f"NOT_SET is a context-side sentinel and would "
                        f"collide with missing-field routing"
                    )
            if key in self._map:
                raise ValueError(f"Duplicate partition key {key!r}")
            self._map[key] = lattice

        self._meta_engine = (
            self._build_meta_engine() if self._context_key_dims else None
        )

    def _build_meta_engine(self) -> "ExpressionRulesEngine":
        """One meta-rule row per partition; EXACT_KEY per key dim."""
        columns: dict[str, list] = {
            d.dimension_name: [] for d in self._context_key_dims
        }
        columns["__partition_idx"] = []
        for idx, lattice in enumerate(self._lattices):
            for d in self._context_key_dims:
                columns[d.dimension_name].append(
                    lattice.partition_key[d.dimension_name]
                )
            columns["__partition_idx"].append(idx)
        meta_metadata = DimensionsMetadata(
            dimensions=[
                Dimension(
                    dimension_name=d.dimension_name,
                    context_field=d.resolved_context_field,
                    match_strategy=MatchStrategy.EXACT_KEY,
                    data_type=d.data_type,
                )
                for d in self._context_key_dims
            ],
            hit_policy=HitPolicy.COLLECT,
        )
        return ExpressionRulesEngine(
            rules=relation(columns).collect(),
            dimension_metadata=meta_metadata,
        )

    def _route(self, key: tuple) -> "Lattice":
        """Ternary + specificity routing for a normalised key tuple."""
        ctx = {
            d.resolved_context_field: key[i]
            for i, d in enumerate(self._context_key_dims)
        }
        rows = relation(self._meta_engine.evaluate(ctx).survivors).to_dict()
        idxs = rows["__partition_idx"]
        if not idxs:
            raise KeyError(
                f"No lattice for partition key {key!r}; served partition "
                f"keys: {sorted(self._map)!r}"
            )
        top = rows["__specificity"][0]  # survivors are rank-sorted
        tied = [
            i for i, s in zip(idxs, rows["__specificity"]) if s == top
        ]
        if len(tied) > 1:
            tied_keys = [
                tuple(
                    self._lattices[i].partition_key[d.dimension_name]
                    for d in self._context_key_dims
                )
                for i in tied
            ]
            raise AmbiguousPartitionError(
                f"Context key {key!r} ties {len(tied)} partitions at "
                f"specificity {top}: {tied_keys!r}"
            )
        return self._lattices[tied[0]]

    def apply(self, context, dimensions=None):
        key = self._engine._extract_partition_key(context)
        lattice = self._map.get(key)
        if lattice is None:
            lattice = self._route(key)
        return self._engine.apply(lattice, context, dimensions=dimensions)

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
