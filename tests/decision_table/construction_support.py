"""Shared helpers for the DecisionTable construction tests (stages a-c)."""

from __future__ import annotations

import typing as t

import ibis
import mountainash.expressions as ma
import polars as pl
from mountainash.relations import relation

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine
from mountainash_rules.decision_table.folds import fold_contributions
from mountainash_rules.decision_table.prevalidate import ValidatedRules, prevalidate
from mountainash_rules.decision_table.stages import (
    Boundaries,
    Coverage,
    Pieces,
    stage_a_boundaries,
    stage_b_pieces,
    stage_c_coverage,
)


def rules_relation(backend: str, data: dict | pl.DataFrame, name: str = "rules"):
    """A rules relation on the requested backend (typed through Polars so nulls keep dtypes)."""
    frame = data if isinstance(data, pl.DataFrame) else pl.DataFrame(data)
    if backend == "polars":
        return relation(frame)
    return relation(ibis.duckdb.connect().create_table(name, frame, overwrite=True))


def validated_rules(backend: str, data: dict, metadata: DimensionsMetadata) -> ValidatedRules:
    return prevalidate(rules_relation(backend, data), metadata)


def captured_rules(backend: str, data: dict, metadata: DimensionsMetadata) -> ValidatedRules:
    """Rules captured without prevalidation, for inputs prevalidation does not yet admit."""
    return ValidatedRules(rules_relation(backend, data).snapshot(), metadata, ())


def run_stages(validated: ValidatedRules) -> tuple[Boundaries, Pieces, Coverage]:
    boundaries = stage_a_boundaries(validated)
    pieces = stage_b_pieces(boundaries)
    return boundaries, pieces, stage_c_coverage(boundaries, pieces)


def freeze(value: t.Any) -> t.Any:
    return tuple(freeze(item) for item in value) if isinstance(value, list) else value


def rows(rel, *columns: str) -> list[tuple]:
    """Sorted tuples of ``columns`` (all columns when omitted); lists become tuples."""
    names = columns or tuple(rel.columns)
    out = [tuple(freeze(row[name]) for name in names) for row in rel.to_dicts()]
    return sorted(out, key=lambda row: tuple((value is None, value) for value in row))


def cell_sources(coverage: Coverage) -> dict[tuple, frozenset]:
    """``{(scope, *cell values): rule uuids}`` from the covered cell rows."""
    grouped: dict[tuple, set] = {}
    for row in coverage.cell_coverage.to_dicts():
        key = (row["scope"], *(freeze(row[column]) for column in coverage.cell_columns))
        grouped.setdefault(key, set()).add(row["source_uuid"])
    return {key: frozenset(uuids) for key, uuids in grouped.items()}


def folded_outputs(
    validated: ValidatedRules, coverage: Coverage, output: Aggregate
) -> dict[tuple, t.Any]:
    """Output value of every cell: each contributing rule folded once per cell."""
    metadata = validated.source_metadata
    scope = ma.lit("default") if metadata.scope_column is None else ma.col(metadata.scope_column)
    contributions = validated.rules.select(
        scope.alias("scope"),
        ma.col(metadata.uuid_column).alias("source_uuid"),
        ma.col(output.contribution_column).alias("a0001"),
    )
    joined = coverage.cell_coverage.join(
        contributions, on=["scope", "source_uuid"], how="inner"
    ).filter(ma.col("scope").eq(output.scope or "default"))
    keys = ["scope", *coverage.cell_columns]
    folded = fold_contributions(joined, [output], keys)
    return {
        tuple(freeze(row[key]) for key in keys): row[output.output_name]
        for row in folded.to_dicts()
    }


def entry_metadata(boundaries: Boundaries, source: DimensionsMetadata) -> DimensionsMetadata:
    """Engine metadata over the table columns, derived from the column plans."""
    return DimensionsMetadata(
        fields=source.fields,
        dimensions=tuple(plan.entry_dimension() for plan in boundaries.plans),
    )


def engine_matches(
    boundaries: Boundaries,
    coverage: Coverage,
    source: DimensionsMetadata,
    scope: str,
    context: dict,
) -> dict[tuple, int]:
    """Specificity of every scope cell the existing engine matches for ``context``."""
    cells = (
        coverage.cell_coverage.filter(ma.col("scope").eq(scope))
        .select(*coverage.cell_columns)
        .unique()
    )
    # The engine takes the backend's native frame (a stage relation's own native form).
    engine = ExpressionRulesEngine(rules=cells.collect(), dimension_metadata=entry_metadata(boundaries, source))
    survivors = relation(engine.evaluate(context=context).survivors)
    return {
        tuple(freeze(row[column]) for column in coverage.cell_columns): row["__specificity"]
        for row in survivors.to_dicts()
    }
