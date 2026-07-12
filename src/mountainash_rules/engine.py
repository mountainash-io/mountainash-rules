"""ExpressionRulesEngine: single-pass rule evaluation using mountainash."""

from __future__ import annotations

import functools
import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import concat, relation

import dataclasses

from mountainash_rules.compiler import DimensionCompiler
from mountainash_rules.constants import (
    CTX_PREFIX,
    NOT_SET,
    HitPolicy,
    not_set_sentinel_for,
)
from mountainash_rules.context import extract_context_values
from mountainash_rules.dimension import DimensionsMetadata
from mountainash_rules.batch_result import BatchRuleResult
from mountainash_rules.hit_policy import (
    HitPolicyViolationError,
    SelectionInfo,
    apply_cardinality,
    check_assertions,
    default_output_fields,
    ordering_keys,
    selection_info_from_metadata,
)
from mountainash_rules.result import RuleResult


class ExpressionRulesEngine:
    """Rule evaluation engine using mountainash.

    Compiles dimension metadata into expression templates at construction time,
    then evaluates contexts against the rules DataFrame in a single-pass
    vectorized operation.

    The engine is backend-agnostic. The DataFrame backend (Polars, Ibis,
    Narwhals-wrapped Pandas/PyArrow) is determined by the type of `rules`
    passed to the constructor. The `RuleResult.survivors` accessor returns
    a DataFrame in the same backend as the input.

    Two construction paths:
    - Convenience: provide dimension_metadata (auto-compiled to expressions)
    - Advanced: provide dimension_expressions directly
    """

    def __init__(
        self,
        rules: t.Any,
        dimension_metadata: DimensionsMetadata | None = None,
        dimension_expressions: dict[str, BaseExpressionAPI] | None = None,
    ) -> None:
        if dimension_metadata and dimension_expressions:
            raise ValueError("Provide dimension_metadata or dimension_expressions, not both")
        if not dimension_metadata and not dimension_expressions:
            raise ValueError("Must provide either dimension_metadata or dimension_expressions")

        if dimension_metadata:
            compiler = DimensionCompiler()
            self._expressions = compiler.compile_dimensions(dimension_metadata)
            self._metadata = dimension_metadata
        else:
            self._expressions = dimension_expressions
            self._metadata = None

        self._rules = rules

    def evaluate(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
        top_n: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        hit_policy: HitPolicy | None = None,
        priority_field: str | None = None,
    ) -> RuleResult:
        """Evaluate rules against a context.

        Args:
            context: Context values as a Pydantic model or dict.
            dimensions: Subset of dimensions to evaluate (default: all).
            top_n: Return only the top N matches by specificity.
            min_specificity: Minimum hard-match count to include.
            include_observability: Include per-dimension ternary columns in result.
            hit_policy: Selection semantics over survivors; None uses the
                metadata's policy (COLLECT on the expressions-only path).
            priority_field: Column ordering PRIORITY selection (overrides
                the metadata's priority_field).

        Returns:
            RuleResult with ranked surviving rules.
        """
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names

        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        if hit_policy is None:
            hit_policy = (
                self._metadata.hit_policy if self._metadata else HitPolicy.COLLECT
            )
        info = selection_info_from_metadata(
            self._metadata, priority_field, include_observability
        )

        context_values = extract_context_values(context, active_dims, metadata=self._metadata)
        result_df, truncated = self._evaluate(
            active_dims=active_dims,
            context_values=context_values,
            top_n=top_n,
            min_specificity=min_specificity,
            include_observability=include_observability,
            hit_policy=hit_policy,
            info=info,
        )
        return RuleResult(
            dataframe=result_df,
            active_dimensions=active_dims,
            selection_info=dataclasses.replace(info, truncated=truncated),
        )

    _BATCH_RESERVED = (
        "__context_id", "__global_idx", "__grp_base",
        "__rule_index", "__rank", "__specificity", "__survived",
    )

    def _check_reserved(self, rel: t.Any, what: str) -> None:
        """Raise if a user-supplied frame collides with engine columns."""
        colliding = [
            c for c in rel.columns
            if c in self._BATCH_RESERVED or c.startswith(("__t_", CTX_PREFIX))
        ]
        if colliding:
            raise ValueError(
                f"{what} frame contains reserved engine columns: {colliding}"
            )

    def _prepare_contexts(
        self,
        contexts: t.Any,
        active_dims: list[str],
        context_id_field: str | None,
    ) -> t.Any:
        """Project contexts to __context_id + __ctx_<dim> columns with sentinels."""
        rel = relation(contexts)
        self._check_reserved(rel, "Contexts")

        if context_id_field is None:
            rel = rel.with_row_index(name="__context_id")
        else:
            total = rel.count_rows()
            distinct = rel.select(ma.col(context_id_field)).unique().count_rows()
            if distinct != total:
                raise ValueError(
                    f"context_id_field '{context_id_field}' must be unique "
                    f"({total} rows, {distinct} distinct)"
                )
            rel = rel.with_columns(
                ma.col(context_id_field).alias("__context_id")
            )

        available = set(rel.columns)
        ctx_exprs: list[t.Any] = [ma.col("__context_id")]
        for name in active_dims:
            dim = self._metadata.get_dimension(name) if self._metadata else None
            field = dim.resolved_context_field if dim is not None else name
            sentinel = (
                not_set_sentinel_for(dim.data_type) if dim is not None else NOT_SET
            )
            alias = f"{CTX_PREFIX}{name}"
            if field in available:
                ctx_exprs.append(
                    ma.coalesce(ma.col(field), ma.lit(sentinel)).alias(alias)
                )
            else:
                ctx_exprs.append(ma.lit(sentinel).alias(alias))
        return rel.select(*ctx_exprs)

    def evaluate_batch(
        self,
        contexts: t.Any,
        *,
        context_id_field: str | None = None,
        dimensions: list[str] | None = None,
        hit_policy: HitPolicy | None = None,
        priority_field: str | None = None,
        top_n_per_context: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        chunk_size: int | None = None,
    ) -> BatchRuleResult:
        """Evaluate every context row against every rule in one pass."""
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names
        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")
        if hit_policy is None:
            hit_policy = (
                self._metadata.hit_policy if self._metadata else HitPolicy.COLLECT
            )
        info = selection_info_from_metadata(
            self._metadata, priority_field, include_observability
        )

        prepared = self._prepare_contexts(contexts, active_dims, context_id_field)
        if chunk_size is None:
            result_df = self._evaluate_batch_frame(
                prepared, active_dims, hit_policy, info,
                top_n_per_context, min_specificity, include_observability,
            )
        else:
            prepared_pl = prepared.to_polars()
            frames = []
            violations: list[HitPolicyViolationError] = []
            for start in range(0, len(prepared_pl), chunk_size):
                chunk = prepared_pl.slice(start, chunk_size)
                try:
                    frames.append(self._evaluate_batch_frame(
                        relation(chunk), active_dims, hit_policy, info,
                        top_n_per_context, min_specificity,
                        include_observability,
                    ))
                except HitPolicyViolationError as exc:
                    violations.append(exc)
            if violations:
                combined = concat([relation(v.offending) for v in violations])
                raise HitPolicyViolationError(
                    hit_policy, combined.collect(),
                    "; ".join(str(v) for v in violations),
                )
            result_df = concat([relation(f) for f in frames]).collect()
        return BatchRuleResult(
            dataframe=result_df,
            active_dimensions=active_dims,
            context_id_field=context_id_field or "__context_id",
            selection_info=info,
        )

    def _evaluate_batch_frame(
        self,
        prepared: t.Any,
        active_dims: list[str],
        hit_policy: HitPolicy,
        info: SelectionInfo,
        top_n_per_context: int | None,
        min_specificity: int | None,
        include_observability: bool,
    ) -> t.Any:
        rules_rel = relation(self._rules)
        self._check_reserved(rules_rel, "Rules")
        rules_rel = rules_rel.with_row_index(name="__rule_index")

        joined = rules_rel.join(prepared, how="cross")

        # Ternary, survival, specificity — same expressions as _evaluate
        dim_columns = [
            self._expressions[d].name.alias(f"__t_{d}") for d in active_dims
        ]
        joined = joined.with_columns(*dim_columns)
        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        survived_inner = t_cols[0] if len(t_cols) == 1 else ma.least(*t_cols)
        survived = survived_inner.ge(ma.lit(0)).alias("__survived")
        specificity = functools.reduce(
            lambda a, b: a.add(b),
            [c.eq(ma.lit(1)).cast(int) for c in t_cols],
        ).alias("__specificity")
        joined = joined.with_columns(survived, specificity)
        joined = joined.filter(ma.col("__survived"))

        # Portable per-context rank: sort, global index, group-min join-back
        keys = ordering_keys(hit_policy, info.priority_field)
        sort_cols = ["__context_id"] + [k for k, _ in keys]
        sort_desc = [False] + [d for _, d in keys]
        joined = joined.sort(*sort_cols, descending=sort_desc)
        joined = joined.with_row_index(name="__global_idx")
        bases = joined.group_by("__context_id").agg(
            ma.col("__global_idx").min().alias("__grp_base")
        )
        joined = joined.join(bases, on="__context_id", how="inner")
        joined = joined.with_columns(
            ma.col("__global_idx")
            .sub(ma.col("__grp_base"))
            .add(ma.lit(1))
            .alias("__rank")
        )

        # Assertions over full per-context survivor sets (pre-truncation)
        if hit_policy == HitPolicy.UNIQUE:
            offenders = (
                joined.group_by("__context_id")
                .agg(ma.col("__rank").count().alias("__n"))
                .filter(ma.col("__n").gt(ma.lit(1)))
            )
            if offenders.count_rows() > 0:
                ids = offenders.to_dict()["__context_id"]
                raise HitPolicyViolationError(
                    hit_policy, offenders.collect(),
                    f"hit_policy=unique violated for context ids "
                    f"{sorted(ids)[:20]}"
                    + (" (truncated)" if len(ids) > 20 else ""),
                )
        elif hit_policy == HitPolicy.ANY:
            outputs = default_output_fields(joined.columns, info)
            if not outputs:
                raise ValueError(
                    "hit_policy=any requires output_fields when no metadata "
                    "is available to infer them"
                )
            disagree = (
                joined.select(
                    ma.col("__context_id"), *[ma.col(c) for c in outputs]
                )
                .unique()
                .group_by("__context_id")
                .agg(ma.col(outputs[0]).count().alias("__n"))
                .filter(ma.col("__n").gt(ma.lit(1)))
            )
            if disagree.count_rows() > 0:
                ids = disagree.to_dict()["__context_id"]
                raise HitPolicyViolationError(
                    hit_policy, disagree.collect(),
                    f"hit_policy=any violated for context ids {sorted(ids)[:20]}",
                )

        if min_specificity is not None:
            joined = joined.filter(
                ma.col("__specificity").ge(ma.lit(min_specificity))
            )
        if top_n_per_context is not None:
            joined = joined.filter(
                ma.col("__rank").le(ma.lit(top_n_per_context))
            )
        if hit_policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY):
            joined = joined.filter(ma.col("__rank").eq(ma.lit(1)))

        drop_cols = ["__survived", "__global_idx", "__grp_base"] + [
            f"{CTX_PREFIX}{d}" for d in active_dims
        ]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        return joined.drop(*drop_cols).collect()

    def _evaluate(
        self,
        active_dims: list[str],
        context_values: dict[str, t.Any],
        top_n: int | None,
        min_specificity: int | None,
        include_observability: bool,
        hit_policy: HitPolicy,
        info: SelectionInfo,
    ) -> tuple[t.Any, bool]:
        """Run the single-pass evaluation pipeline via mountainash.relations.Relation."""
        rel = relation(self._rules)

        # Step 0: Reserved-column guard + stable input row order
        self._check_reserved(rel, "Rules")
        rel = rel.with_row_index(name="__rule_index")

        # Step 1: Bind context values as literal columns
        ctx_columns = [
            ma.lit(value).alias(f"{CTX_PREFIX}{name}")
            for name, value in context_values.items()
        ]
        rel = rel.with_columns(*ctx_columns)

        # Step 2: Apply each dimension expression as a named ternary column
        dim_columns = [
            self._expressions[dim_name].name.alias(f"__t_{dim_name}")
            for dim_name in active_dims
        ] if self._expressions else []
        rel = rel.with_columns(*dim_columns)

        # Step 3: Compute survival and specificity via mountainash expressions
        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        if len(t_cols) == 1:
            survived_inner = t_cols[0]
        else:
            survived_inner = ma.least(*t_cols)
        survived = survived_inner.ge(ma.lit(0)).alias("__survived")

        specificity = functools.reduce(
            lambda a, b: a.add(b),
            [c.eq(ma.lit(1)).cast(int) for c in t_cols],
        ).alias("__specificity")
        rel = rel.with_columns(survived, specificity)

        # Step 4: Filter survivors, apply the policy's ordering, add 1-based rank
        keys = ordering_keys(hit_policy, info.priority_field)
        rel = (
            rel
            .filter(ma.col("__survived"))
            .sort(*[k for k, _ in keys], descending=[d for _, d in keys])
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )

        # Step 5: Assertions over the FULL survivor set (pre-truncation)
        check_assertions(rel, hit_policy, info)

        # Step 6: Optional filters (after ranking, so __rank reflects
        # pre-filter position), then policy cardinality
        truncated = False
        if min_specificity is not None:
            before = rel.count_rows()
            rel = rel.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))
            truncated = truncated or rel.count_rows() < before
        if top_n is not None:
            before = rel.count_rows()
            rel = rel.head(top_n)
            truncated = truncated or before > top_n
        rel = apply_cardinality(rel, hit_policy)

        # Step 7: Drop temporary and observability columns
        drop_cols = ["__survived"] + [f"{CTX_PREFIX}{d}" for d in active_dims]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        rel = rel.drop(*drop_cols)

        return rel.collect(), truncated
