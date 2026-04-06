"""ExpressionRulesEngine: single-pass rule evaluation using mountainash-expressions."""

from __future__ import annotations

import typing as t

import polars as pl
from pydantic import BaseModel

from mountainash.expressions import BaseExpressionAPI

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
        # Determine which dimensions to evaluate
        all_dim_names = list(self._expressions.keys())
        active_dims = dimensions if dimensions else all_dim_names

        # Validate requested dimensions exist
        for dim_name in active_dims:
            if dim_name not in self._expressions:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        # Extract context values
        context_values = extract_context_values(context, active_dims)

        # Bind context values as literal columns
        augmented = self._bind_context(self._rules, context_values)

        # Evaluate all dimensions in a single pass
        result_df = self._evaluate(augmented, active_dims)

        # Apply filters
        if min_specificity is not None:
            result_df = result_df.filter(pl.col("__specificity") >= min_specificity)

        if top_n is not None:
            result_df = result_df.head(top_n)

        # Optionally strip observability columns
        if not include_observability:
            t_cols = [f"__t_{d}" for d in active_dims]
            result_df = result_df.drop([c for c in t_cols if c in result_df.columns])

        return RuleResult(dataframe=result_df, active_dimensions=active_dims)

    def _bind_context(self, rules: t.Any, context_values: dict[str, t.Any]) -> t.Any:
        """Add context values as literal columns to the rules DataFrame."""
        ctx_columns = [
            pl.lit(value).alias(f"{CTX_PREFIX}{name}")
            for name, value in context_values.items()
        ]
        return rules.with_columns(ctx_columns)

    def _evaluate(self, augmented_df: t.Any, active_dims: list[str]) -> t.Any:
        """Run the single-pass evaluation pipeline."""
        # Step 1: Compile each dimension expression into a named ternary column
        dim_columns = [
            self._expressions[dim_name]
                .name.alias(f"__t_{dim_name}")
                .compile(augmented_df, booleanizer=None)
            for dim_name in active_dims
        ]

        # Step 2: Apply all ternary columns at once
        result = augmented_df.with_columns(dim_columns)

        # Step 3: Compute survival and specificity
        t_col_refs = [pl.col(f"__t_{d}") for d in active_dims]

        result = result.with_columns(
            pl.min_horizontal(*t_col_refs).ge(0).alias("__survived"),
            pl.sum_horizontal(*[c.eq(1).cast(pl.Int32) for c in t_col_refs]).alias("__specificity"),
        )

        # Step 4: Filter survivors, rank, clean up
        ctx_columns = [f"{CTX_PREFIX}{d}" for d in active_dims]

        result = (
            result
            .filter(pl.col("__survived"))
            .sort("__specificity", descending=True)
            .with_row_index("__rank", offset=1)
            .drop(["__survived"] + ctx_columns)
        )

        return result
