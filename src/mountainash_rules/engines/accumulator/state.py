"""Immutable semantic and narrow relational state owned by an exact artifact."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import typing as t

import mountainash.expressions as ma

from mountainash_rules.core.contracts import OperationBudget
from mountainash_rules.engines.accumulator.compiler import ExactAnalysis
from mountainash_rules.engines.accumulator.tables import (
    make_relation,
    relation_host_bytes,
    relation_native_bytes,
    storage_type,
)


@dataclass(frozen=True, slots=True)
class CompiledState:
    analysis: ExactAnalysis
    layout: t.Any
    cells: t.Any
    contributors: t.Any
    labels: t.Any
    native_relations: t.Mapping[str, t.Any]
    columns: t.Mapping[str, tuple]
    retained_counts: t.Mapping[str, int]
    retained_bytes: int
    retained_prepared_bytes: int


def materialize_state(
    analysis, layout, *, budget: OperationBudget, restored_relations=None
):
    """Publish no state until every typed relation has materialized."""
    aggregates = tuple(
        sorted(analysis.prepared.aggregates, key=lambda a: a.output_name)
    )
    budget.reserve(
        "max_live_bytes",
        1024 + len(analysis.cells) * (768 + 256 * len(aggregates)),
        phase="artifact.rows",
        units="cell row construction bytes",
    )
    cell_columns = [
        ("__cell_id", "utf8", False),
        ("__predicate_id", "utf8", False),
        ("__contributor_set_id", "utf8", False),
    ] + [
        (f"__out_{i}", storage_type(a.data_type, a.timezone), False)
        for i, a in enumerate(aggregates)
    ]
    cell_rows = [
        dict(
            __cell_id=c.cell_id,
            __predicate_id=c.predicate_id,
            __contributor_set_id=c.contributor_set_id,
            **{
                f"__out_{i}": c.outputs[a.output_name] for i, a in enumerate(aggregates)
            },
        )
        for c in analysis.cells
    ]
    memberships = {c.contributor_set_id: c.contributors for c in analysis.cells}
    edge_count = sum(map(len, memberships.values()))
    budget.reserve(
        "max_contributor_edges",
        edge_count,
        phase="artifact.membership",
        units="unique contributor edges",
    )
    budget.reserve(
        "max_live_bytes",
        edge_count * 512 + len(cell_rows) * 512,
        phase="artifact.rows",
        units="semantic row dictionaries",
    )
    edges = [
        dict(__contributor_set_id=identifier, __source_id=source)
        for identifier, sources in sorted(memberships.items())
        for source in sources
    ]
    edge_columns = [
        ("__contributor_set_id", "utf8", False),
        ("__source_id", "utf8", False),
    ]
    native = (
        dict(restored_relations)
        if restored_relations is not None
        else {
            "lattice.parquet": make_relation(cell_rows, cell_columns, budget=budget),
            "contributors.parquet": make_relation(edges, edge_columns, budget=budget),
        }
    )
    columns = {
        "lattice.parquet": tuple(cell_columns),
        "contributors.parquet": tuple(edge_columns),
    }
    from mountainash_rules.core.scalar import decode_scalar

    prepared = analysis.prepared
    declarations = {item.column_name: item for item in aggregates}
    source_names = tuple(sorted(declarations))
    routing = prepared.routing["payload"]["key_dimensions"]
    source_columns = [("__source_id", "utf8", False), ("__predicate_id", "utf8", False)]
    for i, key in enumerate(routing):
        field = prepared.graph.fields[key["context_field"]]
        source_columns.extend(
            [
                (f"__key_kind_{i}", "utf8", False),
                (
                    f"__key_value_{i}",
                    storage_type(field.data_type, field.timezone),
                    True,
                ),
            ]
        )
    source_columns.extend(
        (
            f"__value_{i}",
            storage_type(declarations[name].data_type, declarations[name].timezone),
            False,
        )
        for i, name in enumerate(source_names)
    )
    budget.reserve(
        "max_live_bytes",
        len(prepared.sources) * (512 + 256 * len(source_columns)),
        phase="artifact.sources",
        units="source row construction bytes",
    )
    source_rows = []
    for source in prepared.sources:
        row = {"__source_id": source.source_id, "__predicate_id": source.predicate_id}
        for i, key in enumerate(source.routing_values):
            match = key["match"]
            row[f"__key_kind_{i}"] = match["kind"]
            row[f"__key_value_{i}"] = (
                decode_scalar(dict(match["value"]))
                if match["kind"] == "value"
                else None
            )
        row.update(
            {
                f"__value_{i}": source.contributions[name]
                for i, name in enumerate(source_names)
            }
        )
        source_rows.append(row)
    if restored_relations is None:
        native["sources.parquet"] = make_relation(
            source_rows, source_columns, budget=budget
        )
    columns["sources.parquet"] = tuple(source_columns)
    label_columns = [("source_id", "utf8", False), ("source_label", "utf8", True)]
    label_rows = [
        {
            "source_id": source.source_id,
            "source_label": analysis.prepared.source_labels.get(source.source_id),
        }
        for source in analysis.prepared.sources
    ]
    labels = make_relation(label_rows, label_columns, budget=budget)
    # Narrow layout rows are immutable; no wide pivot is needed for inspection.
    semantic_relations = [
        (cell_rows, cell_columns),
        (edges, edge_columns),
        (label_rows, label_columns),
    ]
    native_relations = [
        (cell_rows, cell_columns),
        (edges, edge_columns),
        (source_rows, source_columns),
        (label_rows, label_columns),
    ]
    for name in ("scopes", "scope_keys", "source_maps", "vectors", "words"):
        rows = getattr(layout, name)
        schema = layout.columns[name]
        if restored_relations is None:
            native[f"{name}.parquet"] = make_relation(rows, schema, budget=budget)
        columns[f"{name}.parquet"] = tuple(schema)
        semantic_relations.append((rows, schema))
        native_relations.append((rows, schema))

    source_semantic_columns = [
        ("source_id", "utf8", False),
        ("predicate_id", "utf8", False),
        ("content_id", "utf8", False),
        ("routing_values", "opaque", False),
        ("contributions", "opaque", False),
        ("origins", "opaque", False),
    ]

    def source_semantic_rows():
        for source in prepared.sources:
            yield {
                "source_id": source.source_id,
                "predicate_id": source.predicate_id,
                "content_id": source.content_id,
                "routing_values": source.routing_values,
                "contributions": source.contributions,
                "origins": source.origins,
            }

    cells = native["lattice.parquet"].select(
        ma.col("__cell_id").alias("cell_id"),
        ma.col("__predicate_id").alias("predicate_id"),
        ma.col("__contributor_set_id").alias("contributor_set_id"),
        *[ma.col(f"__out_{i}").alias(a.output_name) for i, a in enumerate(aggregates)],
    )
    contributors = native["contributors.parquet"].select(
        ma.col("__contributor_set_id").alias("contributor_set_id"),
        ma.col("__source_id").alias("source_id"),
    )
    # Semantic records and the narrow native tables are both retained.  Their
    # lazy projections share the native tables and therefore add no buffer.
    retained = (
        1024
        + 128 * len(columns)
        + 128 * len(analysis.sources)
        + sum(relation_host_bytes(rows, schema) for rows, schema in semantic_relations)
        + sum(relation_native_bytes(rows, schema) for rows, schema in native_relations)
    )
    prepared_bytes = relation_host_bytes(
        source_semantic_rows(), source_semantic_columns
    )
    retained_counts = MappingProxyType(
        {
            "max_regions": len(analysis.cells),
            "max_contributor_edges": edge_count,
            "max_scopes": len(layout.scopes),
            "max_source_scope_edges": len(layout.source_maps),
            "max_word_rows": len(layout.words),
        }
    )
    return CompiledState(
        analysis,
        layout,
        cells,
        contributors,
        labels,
        MappingProxyType(native),
        MappingProxyType(columns),
        retained_counts,
        retained,
        prepared_bytes,
    )
