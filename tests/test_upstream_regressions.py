"""Regression tests for upstream bugs.

Each test encodes the exact condition that triggered an upstream bug.
Tests marked xfail(strict=True) will flip to xpass when the fix lands,
forcing removal of the marker.  Tests without xfail are fixed regressions
kept to prevent re-introduction.
"""

from __future__ import annotations

import pandas as pd

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import (
    CTX_PREFIX,
    UNKNOWN,
    UNKNOWN_NUMERIC,
    MatchStrategy,
)
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata


class TestNarwhalsDuplicateLiteralInWithColumns:
    """mountainash-io/mountainash-expressions#77 (FIXED)

    narwhals-pandas: multiple sentinel-aware ternary expressions (compiled
    via DimensionCompiler) applied in a single with_columns() call previously
    generated intermediate columns all named 'literal'. Fixed upstream in
    mountainash-expressions 0.1.1.
    """

    def test_batched_compiled_ternary_expressions_on_narwhals_pandas(self):
        """Compiled EXACT + RANGE dim expressions batched in one with_columns."""
        compiler = DimensionCompiler()
        metadata = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="region",
                match_strategy=MatchStrategy.EXACT,
                data_type=str,
            ),
            Dimension(
                dimension_name="amount",
                match_strategy=MatchStrategy.RANGE,
                data_type=int,
                range_min_field="amount_min",
                range_max_field="amount_max",
            ),
        ])
        expressions = compiler.compile_dimensions(metadata)

        df = pd.DataFrame({
            "rule_name": ["specific", "general"],
            "region": ["AU", UNKNOWN],
            "amount_min": [0, UNKNOWN_NUMERIC],
            "amount_max": [100, UNKNOWN_NUMERIC],
        })

        rel = relation(df)
        rel = rel.with_columns(
            ma.lit("AU").alias(f"{CTX_PREFIX}region"),
            ma.lit(50).alias(f"{CTX_PREFIX}amount"),
        )

        # This is the pattern the engine works around: batched dim columns
        dim_columns = [
            expressions[d].name.alias(f"__t_{d}")
            for d in ["region", "amount"]
        ]
        rel = rel.with_columns(*dim_columns)

        rows = rel.to_dict()
        assert rows["__t_region"] == [1, 0]
        assert rows["__t_amount"] == [1, 0]
