"""ExpressionRulesEngine: single-pass rule evaluation using mountainash."""

from __future__ import annotations

import functools
import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import Relation, concat, relation

import dataclasses

from mountainash_rules.core.compiler import DimensionCompiler, normalize_rule_relation
from mountainash_rules.core.constants import (
    CTX_PREFIX,
    BooleanCoercion,
    HitPolicy,
)
from mountainash_rules.core.context import (
    _validate_boolean_coercion,
    bind_context_columns,
    extract_context_values,
)
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.core.numeric import exact_literal
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
        boolean_coercion: BooleanCoercion = BooleanCoercion.NONE,
    ) -> None:
        _validate_boolean_coercion(boolean_coercion)
        self._boolean_coercion = boolean_coercion
        if dimension_metadata is not None and dimension_expressions is not None:
            raise ValueError(
                "Provide dimension_metadata or dimension_expressions, not both"
            )
        if dimension_metadata is None and dimension_expressions is None:
            raise ValueError(
                "Must provide either dimension_metadata or dimension_expressions"
            )
        configured = (
            dimension_metadata.dimensions
            if dimension_metadata is not None
            else dimension_expressions
        )
        if not configured:
            raise ValueError("Filter engines require at least one dimension")

        if output_fields is not None and (
            not isinstance(output_fields, list) or not output_fields
        ):
            raise ValueError("output_fields must be a nonempty list of field names")
        rules_rel = rules if isinstance(rules, Relation) else relation(rules)
        self._rule_columns = rules_rel.columns
        self._output_fields = validate_output_fields(
            output_fields or [], self._rule_columns
        )
        self._expressions: dict[str, BaseExpressionAPI]
        self._metadata: DimensionsMetadata | None

        if dimension_metadata is not None:
            compiler = DimensionCompiler()
            self._expressions = compiler.compile_dimensions(dimension_metadata)
            self._metadata = dimension_metadata
        else:
            self._expressions = t.cast(
                dict[str, BaseExpressionAPI], dimension_expressions
            )
            self._metadata = None

        self._rules = rules

    def _active_dimensions(self, dimensions: list[str] | None) -> list[str]:
        """Validate the entire projection before constructing expressions."""
        if dimensions is None:
            return list(self._expressions)
        if (
            not isinstance(dimensions, list)
            or not dimensions
            or any(not isinstance(name, str) for name in dimensions)
        ):
            raise ValueError("dimensions must be a nonempty list of dimension names")
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("dimensions must not contain duplicate names")
        for name in dimensions:
            if name not in self._expressions:
                raise KeyError(f"Dimension '{name}' not found in expressions")
        return dimensions

    @staticmethod
    def _validate_limit(name: str, value: int | None, minimum: int = 0) -> None:
        """Validate public integer limits without coercion or Boolean acceptance."""
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < minimum
        ):
            raise ValueError(f"{name} must be a Python integer >= {minimum} or None")

    @staticmethod
    def _survival_expression(t_cols: list[BaseExpressionAPI]) -> BaseExpressionAPI:
        """Require every ternary >= 0 without a horizontal minimum reduction.

        Polars 1.44 can collapse identical horizontal-minimum inputs to one
        element. Conjunction preserves row shape and the same survival rule.
        """
        return functools.reduce(
            lambda a, b: a.and_(b), (c.ge(ma.lit(0)) for c in t_cols)
        ).alias("__survived")

    def _selection_config(
        self,
        policy: HitPolicy | str | None,
        priority_field: str | None,
        observability: bool,
        output_fields: list[str] | None = None,
    ) -> tuple[HitPolicy, SelectionInfo]:
        if policy is None:
            policy = HitPolicy.COLLECT
        policy = normalize_policy(policy)
        info = selection_info_from_metadata(
            self._metadata,
            priority_field,
            observability,
            output_fields=output_fields if output_fields is not None else self._output_fields,
        )
        check_policy_config(self._rule_columns, policy, info)
        return policy, info

    def _normalized_rules(self, active_dims: list[str]) -> t.Any:
        rel = self._rules if isinstance(self._rules, Relation) else relation(self._rules)
        self._check_reserved(rel, "Rules")
        if self._metadata is not None:
            rel = normalize_rule_relation(
                rel,
                [self._metadata.get_dimension(name) for name in active_dims],
            )
        return rel

    @staticmethod
    def _drop_bindings(rel: t.Any) -> t.Any:
        return rel.drop(
            *[
                name
                for name in rel.columns
                if name.startswith(
                    (
                        CTX_PREFIX,
                        "__rule_value_",
                        "__rule_wildcard_",
                        "__rule_min_",
                        "__rule_max_",
                    )
                )
            ]
        )

    def evaluate(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
        top_n: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        hit_policy: HitPolicy | str | None = None,
        priority_field: str | None = None,
        output_fields: list[str] | None = None,
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
        active_dims = self._active_dimensions(dimensions)
        self._validate_limit("top_n", top_n)
        self._validate_limit("min_specificity", min_specificity)

        hit_policy, info = self._selection_config(
            hit_policy, priority_field, include_observability, output_fields
        )

        context_values = extract_context_values(
            context,
            active_dims,
            metadata=self._metadata,
            boolean_coercion=self._boolean_coercion,
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
        active_dims = self._active_dimensions(dimensions)

        context_values = extract_context_values(
            context,
            active_dims,
            metadata=self._metadata,
            boolean_coercion=self._boolean_coercion,
        )
        rel = self._scored_relation(active_dims, context_values)
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
            if c in self._BATCH_RESERVED
            or c.startswith(
                (
                    "__t_",
                    CTX_PREFIX,
                    "__rule_value_",
                    "__rule_wildcard_",
                    "__rule_min_",
                    "__rule_max_",
                )
            )
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
        """Classify original per-row availability before conversion or aliasing."""
        rel = contexts if isinstance(contexts, Relation) else relation(contexts)
        self._check_reserved(rel, "Contexts")
        if context_id_field is None:
            rel = rel.with_row_index(name="__context_id")
        else:
            rel = rel.with_columns(ma.col(context_id_field).alias("__context_id"))
        if self._metadata is not None:
            rel = bind_context_columns(
                rel,
                [self._metadata.get_dimension(name) for name in active_dims],
                boolean_coercion=self._boolean_coercion,
            )
        else:
            rel = rel.with_columns(
                *[
                    (ma.col(name) if name in rel.columns else ma.lit(None)).alias(
                        CTX_PREFIX + name
                    )
                    for name in active_dims
                ]
            )
        return rel.select(
            "__context_id",
            *[name for name in rel.columns if name.startswith(CTX_PREFIX)],
        )

    def evaluate_batch(
        self,
        contexts: t.Any,
        *,
        context_id_field: str | None = None,
        dimensions: list[str] | None = None,
        hit_policy: HitPolicy | str | None = None,
        priority_field: str | None = None,
        output_fields: list[str] | None = None,
        top_n_per_context: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        chunk_size: int | None = None,
    ) -> BatchRuleResult:
        """Evaluate every context row against every rule in one pass."""
        active_dims = self._active_dimensions(dimensions)
        self._validate_limit("top_n_per_context", top_n_per_context)
        self._validate_limit("min_specificity", min_specificity)
        self._validate_limit("chunk_size", chunk_size, minimum=1)
        if context_id_field is not None and (
            not isinstance(context_id_field, str) or not context_id_field
        ):
            raise ValueError("context_id_field must be a nonempty string or None")
        selection = self._selection_config(
            hit_policy, priority_field, include_observability, output_fields
        )

        prepared = self._prepare_contexts(contexts, active_dims, context_id_field)
        return self._evaluate_prepared_batch(
            prepared,
            active_dims,
            selection,
            context_id_field=context_id_field,
            top_n_per_context=top_n_per_context,
            min_specificity=min_specificity,
            include_observability=include_observability,
            chunk_size=chunk_size,
        )

    def _evaluate_prepared_batch(
        self,
        prepared: t.Any,
        active_dims: list[str],
        selection: tuple[HitPolicy, SelectionInfo],
        *,
        context_id_field: str | None = None,
        top_n_per_context: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        chunk_size: int | None = None,
    ) -> BatchRuleResult:
        """Evaluate already admitted bindings without reclassifying converted values."""
        selected_policy, info = selection
        if chunk_size is None:
            result_df = self._evaluate_batch_frame(
                prepared,
                active_dims,
                selected_policy,
                info,
                top_n_per_context,
                min_specificity,
                include_observability,
            )
        else:
            result_df = self._evaluate_batch_chunked(
                prepared,
                active_dims,
                selection,
                chunk_size,
                top_n_per_context=top_n_per_context,
                min_specificity=min_specificity,
                include_observability=include_observability,
            )
        return BatchRuleResult(
            dataframe=result_df,
            active_dimensions=active_dims,
            context_id_field=context_id_field or "__context_id",
            selection_info=dataclasses.replace(
                info,
                truncated=selection_is_truncated(
                    selected_policy, top_n_per_context, min_specificity
                ),
            ),
        )

    def _evaluate_batch_chunked(
        self,
        prepared: t.Any,
        active_dims: list[str],
        selection: tuple[HitPolicy, SelectionInfo],
        chunk_size: int,
        *,
        top_n_per_context: int | None,
        min_specificity: int | None,
        include_observability: bool,
    ) -> t.Any:
        """Evaluate prepared contexts in row-count chunks.

        Runs each chunk through `_evaluate_batch_frame` independently, deferring
        any HitPolicyViolationError until every chunk has been evaluated so the
        combined, sorted offending set (not just the first chunk's) is reported.
        """
        hit_policy, info = selection
        count = prepared.count_rows()
        if count == 0:
            return self._evaluate_batch_frame(
                prepared,
                active_dims,
                hit_policy,
                info,
                top_n_per_context,
                min_specificity,
                include_observability,
            )
        frames = []
        violations: list[HitPolicyViolationError] = []
        for start in range(0, count, chunk_size):
            chunk = prepared.slice(start, chunk_size)
            try:
                frames.append(
                    self._evaluate_batch_frame(
                        chunk,
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
            self._raise_batch_violation(hit_policy, combined.collect(), ids)
        return (
            concat([relation(f) for f in frames])
            .sort("__context_id", "__rank")
            .collect()
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
        rules_rel = self._normalized_rules(active_dims).with_row_index(
            name="__rule_index"
        )

        joined = rules_rel.join(prepared, how="cross")

        # Ternary, survival, specificity — same expressions as _evaluate
        dim_columns = [self._expressions[d].name.alias(f"__t_{d}") for d in active_dims]
        joined = joined.with_columns(*dim_columns)
        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        survived = self._survival_expression(t_cols)
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

        self._assert_batch_policy(joined, hit_policy, info)

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

        joined = self._drop_bindings(joined)
        drop_cols = ["__survived"]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        return joined.drop(*drop_cols).sort("__context_id", "__rank").collect()

    @staticmethod
    def _raise_batch_violation(
        hit_policy: HitPolicy, offending: t.Any, ids: t.Sequence[t.Any]
    ) -> t.NoReturn:
        """Raise HitPolicyViolationError with a bounded, sorted context-id list."""
        sorted_ids = sorted(ids)
        message = (
            f"hit_policy={hit_policy.value} violated for context ids "
            f"{sorted_ids[:20]}"
        )
        if len(sorted_ids) > 20:
            message += " (truncated)"
        raise HitPolicyViolationError(hit_policy, offending, message)

    def _assert_batch_policy(
        self, joined: t.Any, hit_policy: HitPolicy, info: SelectionInfo
    ) -> None:
        """Enforce UNIQUE/ANY hit-policy assertions over full per-context survivor sets.

        Runs pre-truncation, over the complete survivor set for every context.
        """
        if hit_policy == HitPolicy.UNIQUE:
            offenders = (
                joined.group_by("__context_id")
                .agg(ma.col("__rank").count().alias("__n"))
                .filter(ma.col("__n").gt(ma.lit(1)))
            )
            if offenders.count_rows() > 0:
                ids = offenders.to_dict()["__context_id"]
                self._raise_batch_violation(hit_policy, offenders.collect(), ids)
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
                self._raise_batch_violation(hit_policy, disagree.collect(), ids)

    def _scored_relation(
        self, active_dims: list[str], context_values: dict[str, t.Any]
    ) -> t.Any:
        """Rules frame scored against a context: ternaries + __survived + __specificity, unfiltered."""
        rel = self._normalized_rules(active_dims).with_row_index(name="__rule_index")
        if self._metadata is not None:
            rel = bind_context_columns(
                rel,
                [self._metadata.get_dimension(name) for name in active_dims],
                values=context_values,
                boolean_coercion=self._boolean_coercion,
            )
        else:
            rel = rel.with_columns(
                *[
                    exact_literal(context_values.get(name)).alias(CTX_PREFIX + name)
                    for name in active_dims
                ]
            )

        dim_columns = [
            self._expressions[dim_name].name.alias(f"__t_{dim_name}")
            for dim_name in active_dims
        ]
        rel = rel.with_columns(*dim_columns)

        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        survived = self._survival_expression(t_cols)
        specificity = functools.reduce(
            lambda a, b: a.add(b),
            [c.eq(ma.lit(1)).cast(int) for c in t_cols],
        ).alias("__specificity")
        return self._drop_bindings(rel.with_columns(survived, specificity))

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
        drop_cols = ["__survived"]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        rel = rel.drop(*drop_cols).sort("__rank")

        return rel.collect(), truncated
