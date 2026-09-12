"""ExpressionRulesEngine: single-pass rule evaluation using mountainash."""

from __future__ import annotations

import functools
import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import concat, relation

import dataclasses

from mountainash_rules.core.compiler import DimensionCompiler
from mountainash_rules.core.constants import (
    CTX_PREFIX,
    DataType,
    HitPolicy,
    MatchStrategy,
)
from mountainash_rules.core.context import (
    _absent_context_value,
    _context_literal,
    _nullable_bool,
    extract_context_values,
)
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.batch_result import BatchRuleResult
from mountainash_rules.core.hit_policy import (
    HitPolicyViolationError,
    SelectionInfo,
    apply_cardinality,
    check_assertions,
    check_policy_config,
    check_priority,
    default_output_fields,
    first_per_context,
    normalize_policy,
    ordering_keys,
    selection_info_from_metadata,
    selection_is_truncated,
    validate_output_fields,
)
from mountainash_rules.core.result import ExplainResult, RuleResult
from mountainash_rules.core.set_wildcard import validate_set_columns


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

    Explicit output_fields may be supplied only on the expressions-only path.
    Metadata-backed engines use DimensionsMetadata.output_fields instead.
    """

    def __init__(
        self,
        rules: t.Any,
        dimension_metadata: DimensionsMetadata | None = None,
        dimension_expressions: dict[str, BaseExpressionAPI] | None = None,
        *,
        output_fields: list[str] | None = None,
    ) -> None:
        if dimension_metadata and dimension_expressions:
            raise ValueError(
                "Provide dimension_metadata or dimension_expressions, not both"
            )
        if not dimension_metadata and not dimension_expressions:
            raise ValueError(
                "Must provide either dimension_metadata or dimension_expressions"
            )

        if dimension_metadata is not None and output_fields is not None:
            raise ValueError("Configure output_fields on dimension_metadata, not both")
        if output_fields is not None and (
            not isinstance(output_fields, list) or not output_fields
        ):
            raise ValueError("output_fields must be a nonempty list of field names")
        self._rule_columns = relation(rules).columns
        declared = (
            dimension_metadata.output_fields
            if dimension_metadata is not None
            else output_fields
            if output_fields is not None
            else []
        )
        self._output_fields = validate_output_fields(declared, self._rule_columns)
        self._expressions: dict[str, BaseExpressionAPI]
        self._metadata: DimensionsMetadata | None

        if dimension_metadata:
            compiler = DimensionCompiler()
            self._expressions = compiler.compile_dimensions(dimension_metadata)
            self._metadata = dimension_metadata
        else:
            self._expressions = t.cast(
                dict[str, BaseExpressionAPI], dimension_expressions
            )
            self._metadata = None

        self._rules = rules
        self._set_dims_validated = False

    def _selection_config(
        self,
        policy: HitPolicy | str | None,
        priority_field: str | None,
        observability: bool,
    ) -> tuple[HitPolicy, SelectionInfo]:
        if policy is None:
            policy = (
                self._metadata.hit_policy
                if self._metadata is not None
                else HitPolicy.COLLECT
            )
        policy = normalize_policy(policy)
        info = selection_info_from_metadata(
            self._metadata,
            priority_field,
            observability,
            output_fields=self._output_fields,
        )
        check_policy_config(self._rule_columns, policy, info)
        return policy, info

    def _validate_set_rules_once(self) -> None:
        """Reject set rule lists that embed the reserved sentinel — once, portably.

        Called from BOTH scoring entry points (single and batch) because
        evaluate_batch does not route through _scored_relation. metadata is None
        when the engine was built from dimension_expressions (no Dimension objects
        to inspect), so skip that case.
        """
        if self._metadata is None or self._set_dims_validated:
            return
        set_dims = [
            d
            for d in self._metadata.dimensions
            if d.match_strategy
            in (MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION)
        ]
        validate_set_columns(relation(self._rules), set_dims)
        self._set_dims_validated = True

    def evaluate(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
        top_n: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        hit_policy: HitPolicy | str | None = None,
        priority_field: str | None = None,
    ) -> RuleResult:
        """Evaluate rules against a context.

        Args:
            context: Context values as a Pydantic model or dict.
            dimensions: Subset of dimensions to evaluate (default: all).
            top_n: Return at most N remaining rows in applied-policy order.
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

        hit_policy, info = self._selection_config(
            hit_policy, priority_field, include_observability
        )

        context_values = extract_context_values(
            context, active_dims, metadata=self._metadata
        )
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

    def explain(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
    ) -> ExplainResult:
        """Score every rule against a context without filtering or ranking.

        Returns all rules with __t_<dim> ternaries, __survived, and
        __specificity. Hit policies are not consulted — explain answers
        "why did/didn't each rule match", not "which rule wins".
        """
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names
        if not active_dims:
            raise ValueError("explain requires at least one active dimension")
        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        context_values = extract_context_values(
            context, active_dims, metadata=self._metadata
        )
        rel = self._scored_relation(active_dims, context_values)
        rel = rel.drop(*[f"{CTX_PREFIX}{d}" for d in active_dims])
        return ExplainResult(
            dataframe=rel.collect(),
            active_dimensions=active_dims,
        )

    _BATCH_RESERVED = (
        "__context_id",
        "__global_idx",
        "__grp_base",
        "__rule_index",
        "__rank",
        "__specificity",
        "__survived",
    )

    def _check_reserved(self, rel: t.Any, what: str) -> None:
        """Raise if a user-supplied frame collides with engine columns."""
        colliding = [
            c
            for c in rel.columns
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
            rel = rel.with_columns(ma.col(context_id_field).alias("__context_id"))

        available = set(rel.columns)
        ctx_exprs: list[t.Any] = [ma.col("__context_id")]
        for name in active_dims:
            dim = self._metadata.get_dimension(name) if self._metadata else None
            field = dim.resolved_context_field if dim is not None else name
            data_type = dim.data_type if dim is not None else None
            alias = f"{CTX_PREFIX}{name}"
            if field in available and data_type is DataType.BOOL:
                # Preserve nulls, including a present all-null/empty column.
                expr = _nullable_bool(ma.col(field))
            else:
                absent = _context_literal(_absent_context_value(data_type), data_type)
                expr = (
                    ma.coalesce(ma.col(field), absent) if field in available else absent
                )
            ctx_exprs.append(expr.alias(alias))
        return rel.select(*ctx_exprs)

    def evaluate_batch(
        self,
        contexts: t.Any,
        *,
        context_id_field: str | None = None,
        dimensions: list[str] | None = None,
        hit_policy: HitPolicy | str | None = None,
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
        hit_policy, info = self._selection_config(
            hit_policy, priority_field, include_observability
        )

        prepared = self._prepare_contexts(contexts, active_dims, context_id_field)
        if chunk_size is None:
            result_df = self._evaluate_batch_frame(
                prepared,
                active_dims,
                hit_policy,
                info,
                top_n_per_context,
                min_specificity,
                include_observability,
            )
        else:
            prepared_pl = prepared.to_polars()
            frames = []
            violations: list[HitPolicyViolationError] = []
            for start in range(0, len(prepared_pl), chunk_size):
                chunk = prepared_pl.slice(start, chunk_size)
                try:
                    frames.append(
                        self._evaluate_batch_frame(
                            relation(chunk),
                            active_dims,
                            hit_policy,
                            info,
                            top_n_per_context,
                            min_specificity,
                            include_observability,
                        )
                    )
                except HitPolicyViolationError as exc:
                    violations.append(exc)
            if violations:
                combined = concat([relation(v.offending) for v in violations]).sort(
                    "__context_id"
                )
                ids = combined.to_dict()["__context_id"]
                raise HitPolicyViolationError(
                    hit_policy,
                    combined.collect(),
                    f"hit_policy={hit_policy.value} violated for context ids {ids[:20]}"
                    + (" (truncated)" if len(ids) > 20 else ""),
                )
            result_df = (
                concat([relation(f) for f in frames])
                .sort("__context_id", "__rank")
                .collect()
            )
        return BatchRuleResult(
            dataframe=result_df,
            active_dimensions=active_dims,
            context_id_field=context_id_field or "__context_id",
            selection_info=dataclasses.replace(
                info,
                truncated=selection_is_truncated(
                    hit_policy, top_n_per_context, min_specificity
                ),
            ),
        )

    def _conform_to_rules_backend(self, prepared: t.Any) -> t.Any:
        """Rehost the prepared contexts in the rules frame's backend.

        The cross-join requires both sides in one backend; contexts arrive
        as whatever the caller built (typically polars). Detection and
        conversion go through mountainash only.
        """
        from mountainash.core.backend_detection import (
            CONST_BACKEND,
            identify_backend,
        )

        backend = identify_backend(self._rules)
        if backend is CONST_BACKEND.IBIS:
            return relation(prepared.to_ibis())
        if backend in (CONST_BACKEND.NARWHALS, CONST_BACKEND.PANDAS):
            module = type(self._rules).__module__
            impl = str(getattr(self._rules, "implementation", "")).lower()
            if "pandas" in module or "pandas" in impl:
                return relation(prepared.to_pandas())
            return relation(prepared.to_polars())
        return relation(prepared.to_polars())

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
        self._validate_set_rules_once()
        rules_rel = relation(self._rules)
        self._check_reserved(rules_rel, "Rules")
        rules_rel = rules_rel.with_row_index(name="__rule_index")

        joined = rules_rel.join(self._conform_to_rules_backend(prepared), how="cross")

        # Ternary, survival, specificity — same expressions as _evaluate
        dim_columns = [self._expressions[d].name.alias(f"__t_{d}") for d in active_dims]
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
        check_priority(joined, hit_policy, info)

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
        joined = joined.drop("__global_idx", "__grp_base")

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
                    hit_policy,
                    offenders.collect(),
                    f"hit_policy=unique violated for context ids "
                    f"{sorted(ids)[:20]}" + (" (truncated)" if len(ids) > 20 else ""),
                )
        elif hit_policy == HitPolicy.ANY:
            outputs = default_output_fields(joined.columns, info)
            comparison = (
                joined.select(
                    ma.col("__context_id"), *[ma.col(c) for c in outputs]
                ).unique()
                if outputs
                else joined
            )
            disagree = (
                comparison.group_by("__context_id")
                .agg(ma.col("__context_id").count().alias("__n"))
                .filter(ma.col("__n").gt(ma.lit(1)))
            )
            if disagree.count_rows() > 0:
                if not outputs:
                    raise ValueError(
                        "hit_policy=any requires nonempty output_fields "
                        "when more than one rule survives a context"
                    )
                ids = disagree.to_dict()["__context_id"]
                raise HitPolicyViolationError(
                    hit_policy,
                    disagree.collect(),
                    f"hit_policy=any violated for context ids {sorted(ids)[:20]}"
                    + (" (truncated)" if len(ids) > 20 else ""),
                )

        if min_specificity is not None:
            joined = joined.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))
        if top_n_per_context is not None:
            if min_specificity is None:
                joined = joined.filter(ma.col("__rank").le(ma.lit(top_n_per_context)))
            else:
                joined = joined.sort("__context_id", "__rank").with_row_index(
                    name="__global_idx"
                )
                bases = joined.group_by("__context_id").agg(
                    ma.col("__global_idx").min().alias("__grp_base")
                )
                joined = (
                    joined.join(bases, on="__context_id", how="inner")
                    .filter(
                        ma.col("__global_idx")
                        .sub(ma.col("__grp_base"))
                        .lt(ma.lit(top_n_per_context))
                    )
                    .drop("__global_idx", "__grp_base")
                )
        if hit_policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY):
            joined = first_per_context(joined)

        drop_cols = ["__survived"] + [f"{CTX_PREFIX}{d}" for d in active_dims]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        return joined.drop(*drop_cols).sort("__context_id", "__rank").collect()

    def _scored_relation(
        self, active_dims: list[str], context_values: dict[str, t.Any]
    ) -> t.Any:
        """Rules frame scored against a context: ternaries + __survived + __specificity, unfiltered."""
        self._validate_set_rules_once()
        # Steps 0-3: reserved-column guard + row index, bind ctx literals,
        # ternary columns, survival + specificity. Steps 4-7 (filter, rank,
        # assertions, cardinality, drop) stay in the callers.
        rel = relation(self._rules)
        self._check_reserved(rel, "Rules")
        rel = rel.with_row_index(name="__rule_index")

        ctx_columns = []
        for name, value in context_values.items():
            dim = (
                self._metadata.get_dimension(name)
                if self._metadata is not None
                else None
            )
            literal = _context_literal(
                value, dim.data_type if dim is not None else None
            )
            ctx_columns.append(literal.alias(f"{CTX_PREFIX}{name}"))
        rel = rel.with_columns(*ctx_columns)

        dim_columns = (
            [
                self._expressions[dim_name].name.alias(f"__t_{dim_name}")
                for dim_name in active_dims
            ]
            if self._expressions
            else []
        )
        rel = rel.with_columns(*dim_columns)

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
        return rel.with_columns(survived, specificity)

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
        rel = self._scored_relation(active_dims, context_values).filter(
            ma.col("__survived")
        )
        check_priority(rel, hit_policy, info)

        # Step 4: Filter survivors, apply the policy's ordering, add 1-based rank
        keys = ordering_keys(hit_policy, info.priority_field)
        rel = (
            rel.sort(*[k for k, _ in keys], descending=[d for _, d in keys])
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )

        # Step 5: Assertions over the FULL survivor set (pre-truncation)
        check_assertions(rel, hit_policy, info)

        # Step 6: Optional filters (after ranking, so __rank reflects
        # pre-filter position), then policy cardinality
        truncated = selection_is_truncated(hit_policy, top_n, min_specificity)
        if min_specificity is not None:
            rel = rel.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))
        if top_n is not None:
            rel = rel.head(top_n)
        rel = apply_cardinality(rel, hit_policy)

        # Step 7: Drop temporary and observability columns
        drop_cols = ["__survived"] + [f"{CTX_PREFIX}{d}" for d in active_dims]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        rel = rel.drop(*drop_cols).sort("__rank")

        return rel.collect(), truncated
