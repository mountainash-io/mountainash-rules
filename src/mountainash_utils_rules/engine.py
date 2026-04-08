"""ExpressionRulesEngine: single-pass rule evaluation using mountainash-expressions."""

from __future__ import annotations

import functools
import typing as t

from pydantic import BaseModel

import mountainash.expressions as ma
from mountainash.expressions import BaseExpressionAPI
from mountainash.relations import relation

from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import CTX_PREFIX
from mountainash_utils_rules.context import extract_context_values
from mountainash_utils_rules.dimension import DimensionsMetadata
from mountainash_utils_rules.result import RuleResult


class ExpressionRulesEngine:
    """Rule evaluation engine using mountainash-expressions.

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
    ) -> RuleResult:
        """Evaluate rules against a context.

        Args:
            context: Context values as a Pydantic model or dict.
            dimensions: Subset of dimensions to evaluate (default: all).
            top_n: Return only the top N matches by specificity.
            min_specificity: Minimum hard-match count to include.
            include_observability: Include per-dimension ternary columns in result.

        Returns:
            RuleResult with ranked surviving rules.
        """
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names

        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        context_values = extract_context_values(context, active_dims)
        result_df = self._evaluate(
            active_dims=active_dims,
            context_values=context_values,
            top_n=top_n,
            min_specificity=min_specificity,
            include_observability=include_observability,
        )
        return RuleResult(dataframe=result_df, active_dimensions=active_dims)

    def _evaluate(
        self,
        active_dims: list[str],
        context_values: dict[str, t.Any],
        top_n: int | None,
        min_specificity: int | None,
        include_observability: bool,
    ) -> t.Any:
        """Run the single-pass evaluation pipeline via mountainash.relations.Relation."""
        rel = relation(self._rules)

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
        ]
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
            [c.eq(ma.lit(1)) for c in t_cols],
        ).alias("__specificity")
        rel = rel.with_columns(survived, specificity)

        # Step 4: Filter survivors, sort by specificity, add 1-based rank
        rel = (
            rel
            .filter(ma.col("__survived"))
            .sort("__specificity", descending=True)
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )

        # Step 5: Apply optional filters (after ranking, so __rank reflects pre-filter position)
        if min_specificity is not None:
            rel = rel.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))
        if top_n is not None:
            rel = rel.head(top_n)

        # Step 6: Drop temporary and observability columns
        drop_cols = ["__survived"] + [f"{CTX_PREFIX}{d}" for d in active_dims]
        if not include_observability:
            drop_cols += [f"__t_{d}" for d in active_dims]
        rel = rel.drop(*drop_cols)

        return rel.collect().collect()
