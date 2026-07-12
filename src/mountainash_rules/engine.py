"""ExpressionRulesEngine: single-pass rule evaluation using mountainash."""

from __future__ import annotations

import functools
import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import relation

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
from mountainash_rules.hit_policy import (
    SelectionInfo,
    apply_cardinality,
    check_assertions,
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
