"""AccumulatorEngine: builds a lattice of maximal consistent rule combinations."""

from __future__ import annotations

import math
import typing as t
import weakref

import polars as pl  # allow: empty-build schema seed pending backend-agnostic empty-frame support

import mountainash.expressions as ma
from mountainash.relations import relation, concat

from pydantic import BaseModel

from mountainash_rules.engines.accumulator.compiler import AccumulatorCompiler
from mountainash_rules.engines.accumulator.result import AccumulatorResult
from mountainash_rules.engines.accumulator.aggregate import Aggregate
from mountainash_rules.core.constants import (
    DimensionRole,
    HitPolicy,
    MatchStrategy,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine
from mountainash_rules.engines.accumulator.lattice import Lattice
from mountainash_rules.engines.accumulator.primes import (
    _INT64_MAX,
    LatticeWidthExceededError,
    checked_multiply,
    get_prime,
)

if t.TYPE_CHECKING:
    from mountainash_rules.engines.accumulator.lattice import LatticeIndex


class AccumulatorEngine:
    """Builds a lattice of maximal consistent rule combinations.

    The build phase:
    1. Partition rules by context-key values (if any CONTEXT_KEY dimensions)
    2. Assign each rule a unique prime number
    3. Create singleton anchor combinations (level 0)
    4. Recursively expand: cross-join current level with rules, filter compatible,
       coalesce dimensions, accumulate aggregates
    5. Remove dominated combinations (frontier filter)
    """

    def __init__(
        self,
        dimension_metadata: DimensionsMetadata,
        aggregates: list[Aggregate] | None = None,
    ) -> None:
        self._metadata = dimension_metadata
        self._aggregates = aggregates or []
        self._compiler = AccumulatorCompiler()
        self._apply_engines: weakref.WeakKeyDictionary[Lattice, ExpressionRulesEngine] = (
            weakref.WeakKeyDictionary()
        )

        # Separate context-key dims from constraint dims
        self._context_key_dims: list[Dimension] = []
        self._constraint_dims: list[Dimension] = []
        for dim in dimension_metadata.dimensions:
            if dim.role == DimensionRole.CONTEXT_KEY:
                self._context_key_dims.append(dim)
            else:
                self._constraint_dims.append(dim)

        # Pre-compile expressions for constraint dimensions
        self._compatible_exprs = {
            dim.dimension_name: self._compiler.compile_compatible(dim)
            for dim in self._constraint_dims
        }
        self._coalesce_exprs = {
            dim.dimension_name: self._compiler.compile_coalesce(dim)
            for dim in self._constraint_dims
        }
        self._coalesce_na_exprs = {
            dim.dimension_name: self._compiler.compile_coalesce_na_flag(dim)
            for dim in self._constraint_dims
        }

    def build(
        self,
        rules: t.Any,
        partition_key: dict[str, t.Any] | None = None,
    ) -> Lattice:
        """Build the lattice of maximal consistent rule combinations.

        Args:
            rules: DataFrame of rules (polars or anything relation() accepts).
            partition_key: Values for CONTEXT_KEY dimensions to filter by.
                Required if any dimensions have role=CONTEXT_KEY.

        Returns:
            Lattice containing the outermost (non-dominated) combinations.
        """
        # Validate partition key
        if self._context_key_dims and not partition_key:
            raise ValueError(
                "partition_key is required when CONTEXT_KEY dimensions are present. "
                f"Expected keys: {[d.dimension_name for d in self._context_key_dims]}"
            )

        rel = relation(rules)

        # Step 1: Partition — filter rules by context-key values
        if partition_key:
            for dim in self._context_key_dims:
                key_val = partition_key.get(dim.dimension_name)
                if key_val is not None:
                    rel = rel.filter(
                        ma.col(dim.resolved_rule_field).eq(ma.lit(key_val))
                    )

        # Materialize to polars for prime injection
        rules_pl = rel.to_polars()
        n_rules = len(rules_pl)

        if n_rules == 0:
            # Return an empty lattice that still carries the composed schema
            empty = rules_pl.with_columns(
                pl.Series("__prime", [], dtype=pl.Int64)
            )
            anchor = self._create_anchor(empty)
            return Lattice(
                dataframe=anchor.collect(),
                metadata=self._metadata,
                aggregates=self._aggregates,
                partition_key=partition_key,
            )

        # Step 2: Assign primes
        primes = [get_prime(i) for i in range(n_rules)]
        rules_pl = rules_pl.with_columns(pl.Series("__prime", primes))

        # Tier 1: if the product of ALL assigned primes fits int64, no
        # combination can ever overflow — skip per-level verification.
        overflow_possible = math.prod(primes) > _INT64_MAX

        # Step 3: Create anchor (level 0) — each rule is a singleton combination
        anchor = self._create_anchor(rules_pl)

        # Step 4: Recursive iteration — breadth-first expansion
        # Prepare the RHS rules relation (just the original rule columns + __prime)
        rhs_rules = relation(rules_pl)

        all_levels = [anchor]
        current_level = anchor

        for level_num in range(1, n_rules):
            new_combos = self._expand_level(
                current_level, rhs_rules, level_num,
                overflow_possible=overflow_possible,
                partition_key=partition_key,
            )
            if new_combos is None:
                break
            all_levels.append(new_combos)
            current_level = new_combos

        # Combine all levels
        all_combos = concat(all_levels)

        # Step 5: Frontier filter — remove dominated combinations
        result = self._frontier_filter(all_combos)

        return Lattice(
            dataframe=result.collect(),
            metadata=self._metadata,
            aggregates=self._aggregates,
            partition_key=partition_key,
        )

    def _rule_fields(self) -> list[str]:
        """All rule-side column names used by constraint dimensions."""
        fields: list[str] = []
        for dim in self._constraint_dims:
            if dim.match_strategy == MatchStrategy.RANGE:
                fields.append(dim.range_min_field)
                fields.append(dim.range_max_field)
            else:
                fields.append(dim.resolved_rule_field)
        return fields

    def _co_fields(self) -> list[str]:
        """All coalesced (co_) column names."""
        fields: list[str] = []
        for dim in self._constraint_dims:
            if dim.match_strategy == MatchStrategy.RANGE:
                fields.append(f"co_{dim.range_min_field}")
                fields.append(f"co_{dim.range_max_field}")
            else:
                fields.append(f"co_{dim.resolved_rule_field}")
        return fields

    def _na_flag_fields(self) -> list[str]:
        """All NA flag column names."""
        fields: list[str] = []
        for dim in self._constraint_dims:
            if dim.match_strategy == MatchStrategy.RANGE:
                fields.append(f"co_{dim.dimension_name}_na")
            else:
                fields.append(f"co_{dim.resolved_rule_field}_na")
        return fields

    def _agg_fields(self) -> list[str]:
        """All aggregate column names."""
        return [f"__agg_{agg.column_name}" for agg in self._aggregates]

    def _tracking_columns(self) -> list[str]:
        """All tracking column names."""
        return ["__prime", "__prime_product", "__level"] + self._agg_fields()

    def _create_anchor(self, rules_pl: pl.DataFrame) -> t.Any:
        """Create level-0 singleton combinations from rules."""
        # Start with all original columns plus __prime
        rel = relation(rules_pl)

        # Add coalesced columns (co_ prefix) — initially copies of rule columns
        co_exprs = []
        for dim in self._constraint_dims:
            if dim.match_strategy == MatchStrategy.RANGE:
                co_exprs.append(
                    ma.col(dim.range_min_field).alias(f"co_{dim.range_min_field}")
                )
                co_exprs.append(
                    ma.col(dim.range_max_field).alias(f"co_{dim.range_max_field}")
                )
            else:
                field = dim.resolved_rule_field
                co_exprs.append(ma.col(field).alias(f"co_{field}"))

        # Add NA flag columns
        na_exprs = []
        for dim in self._constraint_dims:
            if dim.match_strategy == MatchStrategy.RANGE:
                sentinel = unknown_sentinel_for(dim.data_type)
                na_exprs.append(
                    ma.col(dim.range_min_field).eq(ma.lit(sentinel))
                    .__and__(ma.col(dim.range_max_field).eq(ma.lit(sentinel)))
                    .cast(int)
                    .alias(f"co_{dim.dimension_name}_na")
                )
            else:
                field = dim.resolved_rule_field
                sentinel = unknown_sentinel_for(dim.data_type)
                na_exprs.append(
                    ma.col(field).eq(ma.lit(sentinel))
                    .cast(int)
                    .alias(f"co_{field}_na")
                )

        # Add tracking columns
        tracking_exprs = [
            ma.col("__prime").alias("__prime_product"),
            ma.lit(0).alias("__level"),
        ]

        # Add aggregate columns
        agg_exprs = [
            ma.col(agg.column_name).alias(f"__agg_{agg.column_name}")
            for agg in self._aggregates
        ]

        all_exprs = co_exprs + na_exprs + tracking_exprs + agg_exprs
        rel = rel.with_columns(*all_exprs)

        return rel

    def _expand_level(
        self,
        current_level: t.Any,
        rhs_rules: t.Any,
        level_num: int,
        overflow_possible: bool = False,
        partition_key: dict[str, t.Any] | None = None,
    ) -> t.Any | None:
        """Expand current level by cross-joining with rules and filtering compatible pairs."""
        # Cross-join current level with RHS rules
        joined = current_level.join(rhs_rules, how="cross", suffix="_rhs")

        # Guard 1: canonical ordering — last-added prime must increase
        guard1 = ma.col("__prime").lt(ma.col("__prime_rhs"))

        # Guard 2: candidate not already in combination
        guard2 = ma.col("__prime_product").mod(ma.col("__prime_rhs")).ne(ma.lit(0))

        # All dimensions must be compatible
        compat_exprs = list(self._compatible_exprs.values())

        all_guards = guard1.__and__(guard2)
        for expr in compat_exprs:
            all_guards = all_guards.__and__(expr)

        # Materialise each level: without this the lazy plan re-derives every
        # prior level on each expansion, which is exponential in level depth.
        filtered = relation(joined.filter(all_guards).collect())

        # Check if any new combinations were produced
        count = filtered.count_rows()
        if count == 0:
            return None

        if overflow_possible:
            self._check_overflow(filtered, level_num, partition_key)

        # Coalesce dimensions
        coalesce_all = []
        for dim in self._constraint_dims:
            coalesce_all.extend(self._coalesce_exprs[dim.dimension_name])

        # Coalesce NA flags
        na_flag_all = [
            self._coalesce_na_exprs[dim.dimension_name]
            for dim in self._constraint_dims
        ]

        # Update tracking columns
        tracking = [
            ma.col("__prime_rhs").alias("__prime"),
            ma.col("__prime_product").mul(ma.col("__prime_rhs")).alias("__prime_product"),
            ma.lit(level_num).alias("__level"),
        ]

        # Accumulate aggregates
        agg_updates = []
        for agg in self._aggregates:
            agg_col = f"__agg_{agg.column_name}"
            if agg.operation == "sum":
                agg_updates.append(
                    ma.col(agg_col).add(ma.col(f"{agg.column_name}_rhs")).alias(agg_col)
                )
            else:
                raise ValueError(f"Unsupported aggregate operation: {agg.operation}")

        all_updates = coalesce_all + na_flag_all + tracking + agg_updates
        updated = filtered.with_columns(*all_updates)

        # Select only the columns we need for the next iteration
        keep_cols = self._columns_to_keep(current_level)
        updated = updated.select(*[ma.col(c) for c in keep_cols])

        return updated

    def _check_overflow(
        self,
        filtered: t.Any,
        level_num: int,
        partition_key: dict[str, t.Any] | None,
    ) -> None:
        """Raise LatticeWidthExceededError if any pending multiply overflows int64.

        Screen with two aggregates (exact Python-int arithmetic on the maxima
        is conservative); only a suspect level pays the exact per-row check,
        which materialises just the two tracking columns.
        """
        maxima = filtered.select(
            ma.col("__prime_product").max().alias("__max_pp"),
            ma.col("__prime_rhs").max().alias("__max_prhs"),
        ).to_dict()
        if maxima["__max_pp"][0] * maxima["__max_prhs"][0] <= _INT64_MAX:
            return
        pairs = filtered.select(
            ma.col("__prime_product"), ma.col("__prime_rhs")
        ).to_polars()
        for pp, prhs in zip(pairs["__prime_product"], pairs["__prime_rhs"]):
            try:
                checked_multiply(pp, prhs)
            except OverflowError as exc:
                raise LatticeWidthExceededError(
                    f"Prime-product overflow at level {level_num} "
                    f"(clique size {level_num + 1}) for partition "
                    f"{partition_key!r}: {exc} "
                    f"Split the partition with a CONTEXT_KEY dimension or "
                    f"reduce the mutually compatible rule clique."
                ) from exc

    def _columns_to_keep(self, level_rel: t.Any) -> list[str]:
        """Columns to retain after each expansion step."""
        return level_rel.columns

    def _frontier_filter(self, all_combos: t.Any) -> t.Any:
        """Remove dominated combinations.

        A combination is dominated if another combination in the same
        fingerprint namespace (all co_ columns equal) has a prime product
        that is a strict superset (super % sub == 0 AND super != sub).
        """
        co_cols = self._co_fields()
        na_cols = self._na_flag_fields()
        fingerprint_cols = co_cols + na_cols

        # If no constraint dims, nothing to filter
        if not fingerprint_cols:
            return all_combos

        # Self-join on fingerprint columns to find domination
        # Add a sub column for the join
        combos_sub = all_combos.with_columns(
            ma.col("__prime_product").alias("__pp_sub")
        )

        combos_super = all_combos.select(
            *[ma.col(c) for c in fingerprint_cols],
            ma.col("__prime_product").alias("__pp_super"),
        )

        # Join on fingerprint columns
        joined = combos_sub.join(
            combos_super,
            on=fingerprint_cols,
            how="inner",
            suffix="_dom",
        )

        # Find dominated: super % sub == 0 AND super != sub
        dominated = joined.filter(
            ma.col("__pp_super").mod(ma.col("__pp_sub")).eq(ma.lit(0))
            .__and__(ma.col("__pp_super").ne(ma.col("__pp_sub")))
        ).select(ma.col("__pp_sub").alias("__prime_product")).unique()

        # Anti-join to keep non-dominated
        result = combos_sub.join(
            dominated,
            on="__prime_product",
            how="anti",
        ).drop("__pp_sub")

        return result

    def build_all(
        self,
        rules: t.Any,
    ) -> list[Lattice]:
        """Build lattices for all partitions found in the rules.

        Groups rules by CONTEXT_KEY dimension values and builds a Lattice
        for each unique partition.

        Returns:
            List of Lattice objects, one per partition.
        """
        if not self._context_key_dims:
            return [self.build(rules)]

        rel = relation(rules)
        rules_pl = rel.to_polars()

        # Get unique partition key combinations
        key_fields = [d.resolved_rule_field for d in self._context_key_dims]
        key_names = [d.dimension_name for d in self._context_key_dims]
        unique_keys = rules_pl.select(key_fields).unique()

        lattices = []
        for row in unique_keys.iter_rows(named=True):
            partition_key = {
                name: row[field]
                for name, field in zip(key_names, key_fields)
            }
            lattice = self.build(rules, partition_key=partition_key)
            lattices.append(lattice)

        return lattices

    def _build_apply_metadata(self) -> DimensionsMetadata:
        """Create a DimensionsMetadata that remaps CONSTRAINT dims to co_ columns.

        The lattice stores coalesced values in co_-prefixed columns. This method
        builds dimension metadata that points the filter engine at those columns
        while keeping context field names as the original dimension names (since
        the context comes from the user, not the lattice).
        """
        dims: list[Dimension] = []
        for d in self._constraint_dims:
            if d.match_strategy == MatchStrategy.RANGE:
                dims.append(Dimension(
                    dimension_name=d.dimension_name,
                    context_field=d.resolved_context_field,
                    match_strategy=d.match_strategy,
                    data_type=d.data_type,
                    range_min_field=f"co_{d.range_min_field}",
                    range_max_field=f"co_{d.range_max_field}",
                    range_min_inclusive=d.range_min_inclusive,
                    range_max_inclusive=d.range_max_inclusive,
                ))
            else:
                dims.append(Dimension(
                    dimension_name=d.dimension_name,
                    context_field=d.resolved_context_field,
                    rule_field=f"co_{d.resolved_rule_field}",
                    match_strategy=d.match_strategy,
                    data_type=d.data_type,
                ))
        return DimensionsMetadata(
            dimensions=dims,
            hit_policy=HitPolicy.COLLECT,  # never inherit a table policy here
        )

    def apply(
        self,
        lattice: Lattice,
        context: t.Any,
        dimensions: list[str] | None = None,
    ) -> AccumulatorResult:
        """Apply a context to a lattice to find matching combinations.

        Builds remapped metadata that points at the co_ columns in the lattice,
        creates an ExpressionRulesEngine with the lattice as rules, evaluates
        the context, and wraps the result in an AccumulatorResult.

        Args:
            lattice: A pre-built Lattice from build() or build_all().
            context: A Pydantic model or dict with context values.
            dimensions: Optional subset of dimensions to evaluate.

        Returns:
            AccumulatorResult wrapping the matching combinations.
        """
        filter_engine = self._filter_engine_for(lattice)
        filter_result = filter_engine.evaluate(context, dimensions=dimensions)
        return AccumulatorResult(
            dataframe=filter_result.survivors,
            active_dimensions=filter_result.active_dimensions,
            aggregates=self._aggregates,
            lattice=lattice,
        )

    def _filter_engine_for(self, lattice: Lattice) -> ExpressionRulesEngine:
        """Memoised apply-phase filter engine, keyed on lattice identity."""
        engine = self._apply_engines.get(lattice)
        if engine is None:
            engine = ExpressionRulesEngine(
                rules=lattice.combinations,
                dimension_metadata=self._build_apply_metadata(),
            )
            self._apply_engines[lattice] = engine
        return engine

    def index(self, lattices: list[Lattice]) -> LatticeIndex:
        """Build a partition-key routing index over pre-built lattices."""
        from mountainash_rules.engines.accumulator.lattice import LatticeIndex
        return LatticeIndex(self, lattices, self._context_key_dims)

    def _extract_partition_key(self, context: t.Any) -> tuple:
        """Extract the partition key tuple from a context object."""
        if isinstance(context, BaseModel):
            raw = context.model_dump()
        elif isinstance(context, dict):
            raw = context
        else:
            raise TypeError(
                f"Context must be a BaseModel or dict, got {type(context).__name__}"
            )
        return tuple(
            raw[d.resolved_context_field]
            for d in self._context_key_dims
        )

    def apply_auto(
        self,
        lattices: list[Lattice],
        context: t.Any,
        dimensions: list[str] | None = None,
    ) -> AccumulatorResult:
        """Select the correct lattice by partition key and apply the context.

        Args:
            lattices: List of Lattice objects from build_all().
            context: A Pydantic model or dict with context values.
            dimensions: Optional subset of dimensions to evaluate.

        Returns:
            AccumulatorResult wrapping the matching combinations.

        Raises:
            KeyError: If no lattice matches the partition key from the context.

        Note:
            Convenience wrapper; hot paths should hold a LatticeIndex.
        """
        return self.index(lattices).apply(context, dimensions=dimensions)
