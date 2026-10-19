"""Deferred policy composition over the same prepared stored retrieval."""
from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash import MountainashDtype
from mountainash.relations import Relation, concat

from mountainash_rules.engines.accumulate.result import ResolutionResult
from mountainash_rules.engines.accumulate.retrieval import (
    View, _PreparedRetrieval, _identity_views, _prepare_query, _stored_schema, _terminal,
)


@dataclass(frozen=True)
class ResolutionOperation:
    """Retain a deferred decision graph; fetched projections are owned locally."""

    _prepared: _PreparedRetrieval
    _views: dict[str, View]

    def fetch(self, include_matches: bool = False) -> ResolutionResult:
        projections = dict(self._views)
        if include_matches:
            projections.update({f"query:{name}": view for name, view in self._prepared.views.items()
                                if name != "requests"})
        local = _terminal(projections, self._prepared.errors).fetch()
        operation = self._prepared.operation
        evidence = {name: local.get(name) for name in (
            "contributors", "candidates", "cell_identity", "cell_identity_boxes", "cell_identity_contributors",
        )}
        for name in ("cell_identity_ordered_bounds", "cell_identity_category_sets", "cell_identity_category_values"):
            evidence[name] = {key[len(name) + 1:]: value for key, value in local.items()
                              if key.startswith(name + ":")} or None
        matches = operation._result({
            "requests": local["requests"],
            **{name[6:]: rows for name, rows in local.items() if name.startswith("query:")},
        }) if include_matches else None
        return ResolutionResult(
            requests=local["requests"].sort("request_order"), status=local["status"],
            requested_outputs=local["requested_outputs"],
            payloads={scope: local[f"payload:{scope}"] for scope in operation._scopes},
            payload_columns={scope: operation._lattice.output_columns[scope] for scope in operation._scopes},
            _metadata_json=operation._lattice._metadata_json, matches=matches, **evidence,
        )


def resolve_stored(lattice, contexts, scopes=None, *, request_control=None, masks=None,
                   include_cell_key=False, include_contributors=False, include_candidates=False):
    from mountainash_rules.engines.accumulate.resolve import (
        _decision_status, _evidence_options, outcome_agreement, contributor_agreement,
    )

    prepared = _prepare_query(
        lattice, contexts, scopes, request_control=request_control, masks=masks,
        include_cell_key=include_cell_key, include_contributors=include_contributors, decision=True,
    )
    selected = prepared.operation._scopes
    declarations = lattice.metadata.outcome_scopes or {"default": lattice.metadata.aggregates}
    request_type = prepared.views["requests"][1]["request_id"]
    string, boolean, integer = MountainashDtype.STRING, MountainashDtype.BOOL, MountainashDtype.I64
    cell_schema = _stored_schema(lattice, "cells")
    refs_schema = {"request_id": request_type, **{name: cell_schema[name] for name in (
        "partition", "scope", "cell_id", "contributor_set_id",
    )}}
    show_key = include_cell_key or any(group.profile is not None and group.profile.provenance == "cell"
                                     for group in prepared.groups)
    status_schema = {"request_id": request_type, **{name: string for name in (
        "scope", "status", "disposition", "reason_code",
    )}, "contributors_determined": boolean, "cell_determined": boolean}
    if show_key:
        status_schema["cell_key"] = integer
    selection_schema = {"request_id": request_type, "scope": string, "output_name": string}
    parts: dict[str, list[Relation]] = {}
    schemas: dict[str, dict] = {}

    def add(name: str, rows: Relation, schema: t.Mapping[str, t.Any]) -> None:
        parts.setdefault(name, []).append(rows)
        schemas[name] = dict(schema)

    def empty(schema: t.Mapping[str, t.Any]) -> Relation:
        return prepared.views["requests"][0].filter(ma.lit(False)).select(*[
            ma.col("request_id") if name == "request_id" else ma.lit(None, dtype=dtype).alias(name)
            for name, dtype in schema.items()
        ])

    # Empty scope selection still publishes admission and correctly typed empty decisions.
    add("status", empty(status_schema), status_schema)
    add("requested_outputs", empty(selection_schema), selection_schema)
    for group in prepared.groups:
        profile = group.profile
        want_key, want_contributors, want_candidates = _evidence_options(
            profile, include_cell_key=include_cell_key,
            include_contributors=include_contributors, include_candidates=include_candidates,
        )
        want_cells = want_key or want_candidates
        if not selected:
            if want_contributors:
                schema = {"request_id": request_type, "scope": string,
                          "source_uuid": _stored_schema(lattice, "contributors")["source_uuid"]}
                add("contributors", empty(schema), schema)
            if want_candidates:
                schema = {"request_id": request_type, "scope": string,
                          "cell_key": integer, "status": string}
                add("candidates", empty(schema), schema)
            if want_cells:
                for name, (rows, schema) in _identity_views(lattice, empty(refs_schema), refs_schema).items():
                    add(name, rows, schema)
        for scope in selected:
            aggregates = declarations[scope]
            output_schema = _stored_schema(lattice, "outputs", scope=scope)
            names = [f"v{index:04d}" for index in range(1, len(aggregates) + 1)]
            chosen = [name for name, aggregate in zip(names, aggregates)
                      if profile is None or aggregate.resolved_output_name in profile.output_fields
                      or f"{scope}.{aggregate.resolved_output_name}" in profile.output_fields]
            for name, aggregate in zip(names, aggregates):
                if name in chosen:
                    add("requested_outputs", group.requests.select(
                        "request_id", ma.lit(scope).alias("scope"),
                        ma.lit(aggregate.resolved_output_name).alias("output_name"),
                    ), selection_schema)
            points, matches = group.points[scope], group.matches[scope]
            keys = list(group.point_keys[scope])
            outcomes = points.select(*keys).join(
                matches.select(*keys, *chosen), on=keys, how="left", execute_on="right",
            ).select("request_id", *chosen)
            status = group.requests.join(outcome_agreement(outcomes, chosen), on="request_id", how="left", execute_on="right")
            if want_contributors:
                source_hits = matches.join(lattice.contributors,
                    on=["partition", "scope", "contributor_set_id"], execute_on="right",
                ).select(*keys, "scope", "source_uuid").unique()
                certainty = contributor_agreement(points, source_hits, point_key=keys[1:])
                status = status.join(certainty, on="request_id", how="left", execute_on="right")
                contributors = source_hits.join(
                    certainty.filter(ma.col("__contributors_determined")).select("request_id"),
                    on="request_id", execute_on="right",
                ).select("request_id", "scope", "source_uuid").unique()
                add("contributors", contributors, {"request_id": request_type, "scope": string,
                    "source_uuid": _stored_schema(lattice, "contributors")["source_uuid"]})
            else:
                status = status.with_columns(ma.lit(None, dtype=boolean).alias("__contributors_determined"))
            if want_cells:
                cell_outcomes = points.select(*keys).join(matches.select(*keys, "cell_id"),
                    on=keys, how="left", execute_on="right")
                cell_certainty = outcome_agreement(cell_outcomes, ["cell_id"]).select(
                    "request_id", ma.col("__outputs_determined").alias("__cell_determined"),
                )
                status = status.join(cell_certainty, on="request_id", how="left", execute_on="right")
                refs = matches.select(*refs_schema).unique()
                definite = refs.join(cell_certainty.filter(ma.col("__cell_determined")).select("request_id"),
                    on="request_id", execute_on="right")
                if show_key:
                    status = status.join(definite.select("request_id", ma.col("cell_id").cast(integer).alias("cell_key")),
                        on="request_id", how="left", execute_on="right")
                if want_candidates:
                    candidates = refs.join(cell_certainty, on="request_id", execute_on="right").select(
                        "request_id", "scope", ma.col("cell_id").cast(integer).alias("cell_key"),
                        ma.when(ma.col("__cell_determined")).then("certain").otherwise("conditional").alias("status"),
                    )
                    add("candidates", candidates, {"request_id": request_type, "scope": string,
                        "cell_key": integer, "status": string})
                for name, (rows, schema) in _identity_views(lattice, refs if want_candidates else definite, refs_schema).items():
                    add(name, rows, schema)
            else:
                status = status.with_columns(ma.lit(None, dtype=boolean).alias("__cell_determined"))
                if show_key:
                    status = status.with_columns(ma.lit(None, dtype=integer).alias("cell_key"))
            scope_status = _decision_status(status, scope, profile, include_cell_key=show_key)
            add("status", scope_status, status_schema)
            payload = outcomes.join(scope_status.filter(ma.col("status").eq("resolved")).select("request_id"),
                on="request_id", execute_on="right").unique().select(
                    "request_id", *[ma.col(name) if name in chosen else ma.lit(None, dtype=output_schema[name]).alias(name)
                                    for name in names],
                )
            add(f"payload:{scope}", payload, {"request_id": request_type, **{name: output_schema[name] for name in names}})
    views = {name: (concat(rows), schemas[name]) for name, rows in parts.items()}
    views["requests"] = prepared.views["requests"]
    return ResolutionOperation(prepared, views)
