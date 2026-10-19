"""Direct request-local source matching and typed decision folds."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash import MountainashDtype, materialization_scope
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.contracts import ContextContract, DomainField, ResolutionProfile
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.engines.accumulate.admission import (
    admit_partition, bind_request_controls, project_contexts,
    project_physical_fields, select_scopes,
)
from mountainash_rules.engines.accumulate.applicability import logical_source_points, matched_sources
from mountainash_rules.engines.accumulate.folds import fold_contributions
from mountainash_rules.engines.accumulate.sources import KEYS, SourceBundle, capture_sources
from mountainash_rules.engines.accumulate.domain import feasible_assignments
from mountainash_rules.engines.accumulate.result import QueryResult, ResolutionResult
from mountainash_rules.engines.accumulate.errors import RequestValidationError

if t.TYPE_CHECKING:
    from mountainash_rules.engines.accumulate.lattice import Lattice
    from mountainash_rules.engines.accumulate.decision import ResolutionOperation


def _evidence_options(profile, *, include_cell_key, include_contributors, include_candidates):
    """Include diagnostic requests and evidence required by the selected policy."""
    if profile is not None:
        include_cell_key |= profile.provenance == "cell"
        include_contributors |= profile.provenance in {"contributors", "cell"}
        include_candidates |= profile.mode == "candidates" or profile.on_unresolved == "withhold"
    return include_cell_key, include_contributors, include_candidates


def _decision_status(status: Relation, scope: str, profile, *, include_cell_key: bool) -> Relation:
    """Apply the same establishment and disposition policy to either algorithm."""
    established = ma.col("__outputs_determined").fill_null(False)
    candidate_mode = profile is not None and profile.mode == "candidates"
    if profile is not None and profile.provenance in {"contributors", "cell"}:
        established = established & ma.col("__contributors_determined").fill_null(False)
    if profile is not None and profile.provenance == "cell":
        established = established & ma.col("__cell_determined").fill_null(False)
    if candidate_mode:
        established = ma.lit(False)
    unresolved = (
        {"return": "unresolved", "withhold": "withheld", "reject": "rejected"}[profile.on_unresolved]
        if profile is not None and profile.mode == "resolve" else "unresolved"
    )
    invalid = ma.col("admission_status").eq("invalid")
    return status.select(
        "request_id", ma.lit(scope).alias("scope"),
        ma.when(invalid).then("invalid").when(established).then("resolved").otherwise("unresolved").alias("status"),
        ma.when(invalid).then("rejected").when(established).then("decision")
        .otherwise("candidates" if candidate_mode else unresolved).alias("disposition"),
        ma.when(invalid).then(ma.col("reason_code")).when(established).then("established")
        .otherwise("candidate_query" if candidate_mode else "insufficient_context").alias("reason_code"),
        ma.col("__contributors_determined").alias("contributors_determined"),
        ma.col("__cell_determined").alias("cell_determined"),
        *(["cell_key"] if include_cell_key else []),
    )


@dataclass(frozen=True, slots=True)
class SourceResolutionOperation:
    """Deferred resolution against an eagerly captured immutable source bundle."""

    _sources: SourceBundle
    _contexts: Relation
    _scopes: tuple[str, ...]
    _request_control: Relation | None
    _masks: Relation | None
    _include_cell_key: bool
    _include_contributors: bool
    _include_candidates: bool

    def fetch(self, *, include_matches: bool = False) -> ResolutionResult:
        return _resolve_captured(
            self._sources, self._contexts, self._scopes,
            request_control=self._request_control, masks=self._masks,
            include_cell_key=self._include_cell_key,
            include_contributors=self._include_contributors,
            include_candidates=self._include_candidates,
            include_matches=include_matches,
        )


def fold_request_points(
    sources: SourceBundle,
    points: Relation,
    scope: str,
    aggregates: t.Sequence[Aggregate],
    selected_outputs: t.Sequence[int],
    *,
    point_keys: t.Sequence[str] = ("request_id",),
) -> tuple[Relation, Relation]:
    """Fold each feasible point separately, retaining genuine no-match nulls.

    ``selected_outputs`` contains zero-based declaration positions. Unrequested
    aggregate operations are not evaluated; physical output names remain those
    of the full scope declaration rather than a renumbered profile projection.
    """
    hits = matched_sources(sources, points, (scope,), point_keys=point_keys)
    base = points.select(*point_keys)
    if not selected_outputs:
        return base, hits
    payload = sources.payloads[scope].select(*KEYS, *[
        ma.col(f"a{position + 1:04d}").alias(f"a{index:04d}")
        for index, position in enumerate(selected_outputs, 1)
    ])
    joined = hits.join(payload, on=list(KEYS))
    folded = fold_contributions(
        joined, [aggregates[position] for position in selected_outputs], point_keys,
    ).select(*point_keys, *[
        ma.col(f"v{index:04d}").alias(f"v{position + 1:04d}")
        for index, position in enumerate(selected_outputs, 1)
    ])
    return base.join(folded, on=list(point_keys), how="left"), hits


def outcome_agreement(alternatives: Relation, output_columns: t.Sequence[str]) -> Relation:
    """Count distinct complete output tuples, including the all-null alternative."""
    tuples = alternatives.select("request_id", *output_columns).unique(
        subset=["request_id", *output_columns],
    )
    return tuples.group_by("request_id").agg(
        ma.count_records().eq(1).alias("__outputs_determined"),
    )


def contributor_agreement(points: Relation, hits: Relation, *, point_key: str | t.Sequence[str]) -> Relation:
    """A source set is fixed iff every observed UUID belongs to every completion."""
    completion_keys = [point_key] if isinstance(point_key, str) else list(point_key)
    totals = points.group_by("request_id").agg(ma.count_records().alias("__completion_count"))
    occurrences = hits.select("request_id", *completion_keys, "source_uuid").unique(
        subset=["request_id", *completion_keys, "source_uuid"],
    ).group_by("request_id", "source_uuid").agg(ma.count_records().alias("__source_count"))
    uncertain = occurrences.join(totals, on="request_id").filter(
        ma.col("__source_count").ne(ma.col("__completion_count")),
    ).select("request_id").unique(subset=["request_id"]).with_columns(
        ma.lit(True).alias("__contributors_uncertain"),
    )
    return totals.select("request_id").join(uncertain, on="request_id", how="left").select(
        "request_id", (~ma.col("__contributors_uncertain").fill_null(False)).alias("__contributors_determined"),
    )


def _materialize(owner, query: Relation) -> Relation:
    """Own a placed query result assembled from already-owned input snapshots."""
    return owner.snapshot(relation(query.collect()))


def _field_declarations(sources: SourceBundle, domains=(), fields=()) -> dict[str, DomainField]:
    metadata = sources.metadata
    declarations = {field.name: field for domain in domains for field in domain.fields}
    for field in fields:
        declarations.setdefault(field.name, DomainField(
            name=field.name, data_type=field.data_type, numeric_dtype=field.numeric_dtype,
            rounding=field.rounding, timezone=field.timezone,
        ))
    for dim in metadata.dimensions:
        field = dim.resolved_context_field
        timezone = None
        if dim.data_type is DataType.DATETIME:
            if field in declarations:
                timezone = declarations[field].timezone
            else:
                state = sources.states[dim.dimension_name]
                column = "lower" if dim.match_strategy is MatchStrategy.RANGE else "value"
                native_type = state.collect_schema(allow_execution=False)[column]
                native_type = getattr(native_type, "element", native_type)
                timezone = "utc" if getattr(native_type, "timezone_aware", False) else "naive"
        declaration = DomainField(
            name=field, data_type=dim.data_type, numeric_dtype=dim.numeric_dtype,
            rounding=dim.rounding, timezone=timezone,
        )
        previous = declarations.setdefault(field, declaration)
        if previous != declaration:
            raise ValueError(f"Incompatible physical interpretations for {field!r}")
    return dict(sorted(declarations.items()))


def _point_bindings(metadata, values, residuals=None):
    bindings = []
    for field, value in values.items():
        bindings.extend([
            value.alias(f"__point_value_{field}"),
            (ma.col(residuals[field]) if residuals and field in residuals
             else ma.lit(-1, dtype=MountainashDtype.I64)).alias(f"__point_residual_{field}"),
        ])
    for dim in metadata.dimensions:
        field = dim.resolved_context_field
        bindings.extend([
            values[field].alias(f"__ctx_value_{dim.dimension_name}"),
            ma.lit(True).alias(f"__ctx_present_{dim.dimension_name}"),
            ma.lit("concrete").alias(f"__ctx_origin_{dim.dimension_name}"),
        ])
        bindings.append(
            (ma.col(residuals[field]) if residuals and field in residuals else ma.lit(-1, dtype=MountainashDtype.I64))
            .alias(f"__ctx_residual_{dim.dimension_name}"),
        )
    return bindings


def _empty_payload(sources, requests, scope, aggregates):
    expressions = []
    for index, aggregate in enumerate(aggregates, 1):
        value = ma.col(f"a{index:04d}")
        if aggregate.output_numeric_dtype is not None:
            value = value.cast(aggregate.output_numeric_dtype, rounding=aggregate.output_rounding)
        expressions.append(value.alias(f"v{index:04d}"))
    return requests.filter(ma.lit(False)).select("request_id").join(
        sources.payloads[scope].filter(ma.lit(False)).select(*expressions),
        how="cross",
    )


def _direct_cells(sources, points, hits, scope, owner, requests, effective, domains):
    from mountainash_rules.engines.accumulate.build import construct_targeted_geometry
    from mountainash_rules.engines.accumulate.geometry import _membership_labels
    from mountainash_rules.engines.accumulate.serving import _matching_cells, stored_boundaries

    point_keys = ("request_id", "__completion_id")
    keys = ("partition", "scope")
    fragments = _materialize(owner, points.select(*point_keys, "partition").with_columns(
        ma.lit(scope).alias("scope"),
    ).with_row_index(name="fragment_id"))
    edges = _materialize(owner, hits.join(
        fragments.select(*point_keys, "fragment_id"), on=list(point_keys),
    ).select(*keys, "fragment_id", "source_uuid"))
    if not edges.limit(1).count_rows():
        empty_hits = fragments.filter(ma.lit(False)).select(*point_keys, *keys).with_columns(
            ma.lit(None, dtype=MountainashDtype.I64).alias("cell_id"),
            ma.lit(None, dtype=MountainashDtype.I64).alias("contributor_set_id"),
        )
        return None, empty_hits, points
    labels = _materialize(owner, _membership_labels(fragments, edges, keys=keys))
    targets = _materialize(owner, edges.join(labels, on=[*keys, "fragment_id"]).select(
        *keys, ma.col("contributor_set_id").alias("target_id"), "source_uuid",
    ).unique(subset=[*keys, "target_id", "source_uuid"]))
    geometry = construct_targeted_geometry(sources, targets)
    # Membership witnesses do not distinguish disconnected components. Intersect
    # the requested full cells with Q separately; never clip their identities to Q.
    assignments, values, residuals = feasible_assignments(
        requests, sources.metadata, effective, {}, pair_key="request_id",
        boundaries=stored_boundaries(geometry, requests, scope), domains=domains,
    )
    cell_points = _materialize(owner, assignments.with_columns(*_point_bindings(
        sources.metadata, {field: ma.col(name) for field, name in values.items()}, residuals,
    )).with_row_index(name="__completion_id"))
    return geometry, _materialize(owner, _matching_cells(geometry, cell_points, scope, point_keys)), cell_points


def _resolve_group(
    sources: SourceBundle,
    contexts: Relation,
    working: Relation,
    selected: t.Sequence[str],
    *,
    contract=None,
    profile=None,
    include_contributors: bool = False,
    include_cell_key: bool = False,
    include_candidates: bool = False,
    include_matches: bool = False,
) -> ResolutionResult:
    """Resolve a group with one explicitly selected observation contract."""
    metadata = sources.metadata
    declarations = metadata.outcome_scopes or {"default": metadata.aggregates}
    domains = (metadata.domain,) if metadata.domain is not None else ()
    if contract is not None:
        provider = next(domain for domain in (*domains, *metadata.context_domains)
                        if domain.domain_id == contract.domain_ref)
        if provider not in domains:
            domains += (provider,)
    include_cell_key, include_contributors, include_candidates = _evidence_options(
        profile, include_cell_key=include_cell_key,
        include_contributors=include_contributors, include_candidates=include_candidates,
    )
    need_cells = include_cell_key or include_candidates
    with materialization_scope() as owner:
        working, original, effective = project_physical_fields(
            contexts, working, metadata,
            _field_declarations(sources, domains, contract.fields if contract else ()),
            contract=contract, profile=profile,
        )
        working = _materialize(owner, working)
        if domains:
            feasible, _, _ = feasible_assignments(
                working.filter(ma.col("__admission_reason").is_null()),
                metadata, original, {}, pair_key="request_id", domains=domains,
            )
            allowed = feasible.select("request_id").unique(subset=["request_id"]).with_columns(
                ma.lit(True).alias("__in_domain"),
            )
            working = working.join(allowed, on="request_id", how="left").with_columns(
                ma.when(ma.col("__admission_reason").is_not_null()).then(ma.col("__admission_reason"))
                .when(~ma.col("__in_domain").fill_null(False)).then("outside_domain")
                .otherwise(ma.lit(None, dtype=MountainashDtype.STRING)).alias("__admission_reason"),
            )
            working = _materialize(owner, working)
        requests = working.select(
            "request_id", "request_order",
            ma.when(ma.col("__admission_reason").is_null()).then("admitted").otherwise("invalid").alias("admission_status"),
            ma.col("__admission_reason").alias("reason_code"), "contract_id", "profile_id",
        )
        valid = working.filter(ma.col("__admission_reason").is_null())
        complete = ma.lit(True)
        for field in effective:
            complete = complete & ~effective[field].values[0].inactive
        complete_requests = owner.snapshot(valid.filter(complete))
        partial_requests = owner.snapshot(valid.filter(~complete))
        has_complete = bool(complete_requests.limit(1).count_rows())
        has_partial = bool(partial_requests.limit(1).count_rows())
        statuses, selections, contributors = [], [], []
        payloads, payload_columns = {}, {}
        matched_contributions: dict[str, Relation] = {}
        candidate_rows, identity_rows, identity_boxes, identity_contributors = [], [], [], []
        identity_bounds: dict[str, list[Relation]] = {}
        identity_sets: dict[str, list[Relation]] = {}
        identity_values: dict[str, list[Relation]] = {}
        empty = requests.filter(ma.lit(False)).select("request_id")
        empty_status = empty.with_columns(
            ma.lit(None, dtype=MountainashDtype.STRING).alias("scope"),
            ma.lit(None, dtype=MountainashDtype.STRING).alias("status"),
            ma.lit(None, dtype=MountainashDtype.STRING).alias("disposition"),
            ma.lit(None, dtype=MountainashDtype.STRING).alias("reason_code"),
            ma.lit(None, dtype=MountainashDtype.BOOL).alias("contributors_determined"),
            ma.lit(None, dtype=MountainashDtype.BOOL).alias("cell_determined"),
        )
        if include_cell_key:
            empty_status = empty_status.with_columns(ma.lit(None, dtype=MountainashDtype.I64).alias("cell_key"))
        empty_identity = empty.with_columns(
            ma.lit(None, dtype=MountainashDtype.STRING).alias("partition"),
            ma.lit(None, dtype=MountainashDtype.STRING).alias("scope"),
            ma.lit(None, dtype=MountainashDtype.I64).alias("cell_key"),
            ma.lit(None, dtype=MountainashDtype.I64).alias("identity_version"),
        )
        empty_candidates = empty_identity.select("request_id", "scope", "cell_key").with_columns(
            ma.lit(None, dtype=MountainashDtype.STRING).alias("status"),
        )
        empty_selection = empty.with_columns(
            ma.lit(None, dtype=MountainashDtype.STRING).alias("scope"),
            ma.lit(None, dtype=MountainashDtype.STRING).alias("output_name"),
        )
        empty_contributors = empty.join(
            sources.identities.filter(ma.lit(False)).select("scope", "source_uuid"), how="cross",
        )
        for scope in selected:
            scope_matches: list[Relation] = []
            aggregates = declarations[scope]
            output_names = [f"v{index:04d}" for index in range(1, len(aggregates) + 1)]
            positions = tuple(
                index for index, aggregate in enumerate(aggregates)
                if profile is None or aggregate.resolved_output_name in profile.output_fields
                or f"{scope}.{aggregate.resolved_output_name}" in profile.output_fields
            )
            selected_names = [output_names[index] for index in positions]
            payload_columns[scope] = {
                aggregate.resolved_output_name: name for aggregate, name in zip(aggregates, output_names)
            }
            for aggregate in (aggregates[index] for index in positions):
                selections.append(requests.select(
                    "request_id", ma.lit(scope).alias("scope"),
                    ma.lit(aggregate.resolved_output_name).alias("output_name"),
                ))
            alternatives, certainties, scope_contributors = [], [], []
            cell_certainties, cell_keys = [], []
            point_relations = []
            if has_complete:
                bindings = _point_bindings(metadata, {
                    field: restriction.values[0].values for field, restriction in effective.items()
                })
                point_relations.append(complete_requests.with_columns(
                    *bindings, ma.lit(0, dtype=MountainashDtype.I64).alias("__completion_id"),
                ))
            if has_partial:
                assignments, values, residuals = logical_source_points(
                    sources, partial_requests, effective, domains, scope,
                )
                bindings = _point_bindings(metadata, {
                    field: ma.col(name) for field, name in values.items()
                }, residuals)
                point_relations.append(assignments.with_columns(*bindings).with_row_index(name="__completion_id"))
            if need_cells and len(point_relations) > 1:
                point_columns = ["request_id", "partition", *[
                    f"__ctx_{kind}_{dim.dimension_name}"
                    for dim in metadata.dimensions for kind in ("value", "present", "origin", "residual")
                ], *[
                    f"__point_{kind}_{field}" for field in effective for kind in ("value", "residual")
                ]]
                point_relations = [concat([
                    points.select(*point_columns, ma.col("__completion_id").cast(int).alias("__completion_id"))
                    for points in point_relations
                ])]
            for point_relation in point_relations:
                points = _materialize(owner, point_relation)
                outcomes, hits = fold_request_points(
                    sources, points, scope, aggregates, positions,
                    point_keys=("request_id", "__completion_id"),
                )
                outcomes = _materialize(owner, outcomes)
                if include_contributors or need_cells or include_matches:
                    hits = _materialize(owner, hits)
                alternatives.append(outcomes.select("request_id", *selected_names))
                if include_matches:
                    scope_matches.append(hits.select("request_id", *KEYS).unique().join(
                        sources.payloads[scope], on=list(KEYS),
                    ).select("request_id", *KEYS, *[
                        ma.col(f"a{index:04d}").alias(name)
                        for index, name in enumerate(output_names, 1)
                    ]))
                if include_contributors:
                    certainty = _materialize(owner, contributor_agreement(points, hits, point_key="__completion_id"))
                    certainties.append(certainty)
                    scope_contributors.append(hits.join(
                        certainty.filter(ma.col("__contributors_determined")).select("request_id"),
                        on="request_id",
                    ).select("request_id", "scope", "source_uuid").unique(
                        subset=["request_id", "scope", "source_uuid"],
                    ))
                if need_cells:
                    geometry, cell_hits, cell_points = _direct_cells(
                        sources, points, hits, scope, owner, valid, effective, domains,
                    )
                    cell_outcomes = cell_points.select("request_id", "__completion_id").join(
                        cell_hits.select("request_id", "__completion_id", "cell_id"),
                        on=["request_id", "__completion_id"], how="left",
                    )
                    cell_certainty = _materialize(owner, outcome_agreement(cell_outcomes, ["cell_id"]).select(
                        "request_id", ma.col("__outputs_determined").alias("__cell_determined"),
                    ))
                    cell_certainties.append(cell_certainty)
                    refs = _materialize(owner, cell_hits.select(
                        "request_id", "partition", "scope", "cell_id", "contributor_set_id",
                    ).unique(subset=["request_id", "partition", "scope", "cell_id", "contributor_set_id"]))
                    definite_refs = refs.join(
                        cell_certainty.filter(ma.col("__cell_determined")).select("request_id"),
                        on="request_id",
                    )
                    cell_keys.append(definite_refs.select(
                        "request_id", ma.col("cell_id").cast(int).alias("cell_key"),
                    ))
                    if include_candidates:
                        candidate_rows.append(refs.join(cell_certainty, on="request_id").select(
                            "request_id", "scope", ma.col("cell_id").cast(int).alias("cell_key"),
                            ma.when(ma.col("__cell_determined")).then("certain").otherwise("conditional").alias("status"),
                        ))
                    retained_refs = refs if include_candidates else definite_refs
                    if geometry is not None:
                        identity_rows.append(retained_refs.join(
                            geometry.cells, on=["partition", "scope", "cell_id", "contributor_set_id"],
                        ).select("request_id", "partition", "scope",
                                 ma.col("cell_id").cast(int).alias("cell_key"), "identity_version"))
                        def identity_child(table, *, contributors=False):
                            join_keys = ["partition", "scope", "contributor_set_id" if contributors else "cell_id"]
                            child_columns = [column for column in table.columns if column not in join_keys]
                            return retained_refs.join(table, on=join_keys).select(
                                "request_id", "partition", "scope",
                                ma.col("cell_id").cast(int).alias("cell_key"), *child_columns,
                            )
                        identity_boxes.append(identity_child(geometry.cell_boxes))
                        identity_contributors.append(identity_child(geometry.contributors, contributors=True))
                        for target, tables in (
                            (identity_bounds, geometry.cell_ordered_bounds),
                            (identity_sets, geometry.cell_category_sets),
                            (identity_values, geometry.cell_category_values),
                        ):
                            for field, table in tables.items():
                                target.setdefault(field, []).append(identity_child(table))
            template = _materialize(owner, _empty_payload(sources, requests, scope, aggregates))
            all_outcomes = _materialize(owner, concat(alternatives) if alternatives else template.select("request_id", *selected_names))
            agreement = outcome_agreement(all_outcomes, selected_names)
            status = requests.join(agreement, on="request_id", how="left")
            if certainties:
                status = status.join(concat(certainties), on="request_id", how="left")
            else:
                status = status.with_columns(
                    ma.lit(None, dtype=MountainashDtype.BOOL).alias("__contributors_determined"),
                )
            if cell_certainties:
                status = status.join(concat(cell_certainties), on="request_id", how="left")
            else:
                status = status.with_columns(ma.lit(None, dtype=MountainashDtype.BOOL).alias("__cell_determined"))
            if include_cell_key:
                if cell_keys:
                    status = status.join(concat(cell_keys), on="request_id", how="left")
                else:
                    status = status.with_columns(ma.lit(None, dtype=MountainashDtype.I64).alias("cell_key"))
            scope_status = _materialize(owner, _decision_status(
                status, scope, profile, include_cell_key=include_cell_key,
            ))
            statuses.append(scope_status)
            resolved_values = all_outcomes.join(
                scope_status.filter(ma.col("status").eq("resolved")).select("request_id"),
                on="request_id",
            ).select("request_id", *selected_names).unique(subset=["request_id", *selected_names])
            unrequested = [name for name in output_names if name not in selected_names]
            if unrequested:
                resolved_values = resolved_values.join(
                    template.select("request_id", *unrequested), on="request_id", how="left",
                )
            payloads[scope] = owner.detach(_materialize(owner, resolved_values.select("request_id", *output_names)))
            contributors.extend(scope_contributors)
            if include_matches:
                empty_matches = empty.join(
                    sources.payloads[scope].filter(ma.lit(False)), how="cross",
                ).select("request_id", *KEYS, *[
                    ma.col(f"a{index:04d}").alias(name)
                    for index, name in enumerate(output_names, 1)
                ])
                matched_contributions[scope] = owner.detach(_materialize(
                    owner, (concat(scope_matches) if scope_matches else empty_matches).unique(),
                ))
        def retain(rows, empty_relation):
            return owner.detach(_materialize(owner, concat(rows) if rows else empty_relation))

        def retain_fields(fields):
            return {field: retain(rows, rows[0]) for field, rows in fields.items()}

        retained_requests = owner.detach(_materialize(owner, requests))
        return ResolutionResult(
            requests=retained_requests,
            status=owner.detach(_materialize(owner, concat(statuses) if statuses else empty_status)),
            requested_outputs=owner.detach(_materialize(owner, concat(selections) if selections else empty_selection)),
            payloads=payloads, payload_columns=payload_columns, _metadata_json=sources._metadata_json,
            matches=QueryResult(
                requests=retained_requests, scopes=tuple(selected),
                matches=matched_contributions, payload_columns=payload_columns,
                _metadata_json=sources._metadata_json, version_id=None,
            ) if include_matches else None,
            contributors=owner.detach(_materialize(owner, concat(contributors) if contributors else empty_contributors)) if include_contributors else None,
            candidates=retain(candidate_rows, empty_candidates) if include_candidates else None,
            cell_identity=retain(identity_rows, empty_identity) if need_cells else None,
            cell_identity_boxes=retain(identity_boxes, empty_identity.drop("identity_version").with_columns(
                ma.lit(None, dtype=MountainashDtype.I64).alias("box_id"),
            )) if need_cells else None,
            cell_identity_contributors=retain(identity_contributors, empty_identity.drop("identity_version").with_columns(
                ma.lit(None, dtype=MountainashDtype.STRING).alias("source_uuid"),
            )) if need_cells else None,
            cell_identity_ordered_bounds=retain_fields(identity_bounds) if need_cells else None,
            cell_identity_category_sets=retain_fields(identity_sets) if need_cells else None,
            cell_identity_category_values=retain_fields(identity_values) if need_cells else None,
        )


def _resolve_captured(
    sources: SourceBundle,
    contexts: Relation,
    selected: t.Sequence[str],
    *,
    request_control: Relation | None = None,
    masks: Relation | None = None,
    include_contributors: bool = False,
    include_cell_key: bool = False,
    include_candidates: bool = False,
    include_matches: bool = False,
) -> ResolutionResult:
    """Resolve each request under its explicit profile, or without a contract."""
    metadata = sources.metadata
    if not isinstance(contexts, Relation):
        raise RequestValidationError("contexts must be a relation")
    with materialization_scope() as owner:
        contexts = owner.snapshot(contexts)
        if isinstance(request_control, Relation):
            request_control = owner.snapshot(request_control)
        if isinstance(masks, Relation):
            masks = owner.snapshot(masks)
        working = owner.snapshot(project_contexts(contexts, metadata))
        working = bind_request_controls(working, metadata, request_control, masks)
        working = _materialize(owner, admit_partition(working, sources.identities, metadata))
        groups: list[tuple[ContextContract | None, ResolutionProfile | None, Relation]] = [
            (None, None, working.filter(ma.col("contract_id").is_null())),
        ]
        groups.extend(
            (contract, profile, working.filter(
                ma.col("contract_id").eq(contract.contract_id)
                & ma.col("profile_id").eq(profile.profile_id)
            ))
            for contract in metadata.context_contracts for profile in contract.profiles
        )
        results = [
            _resolve_group(
                sources, contexts, group, selected, contract=contract, profile=profile,
                include_contributors=include_contributors,
                include_cell_key=include_cell_key, include_candidates=include_candidates,
                include_matches=include_matches,
            )
            for contract, profile, group in groups if group.limit(1).count_rows()
        ]
        if not results:
            return _resolve_group(
                sources, contexts, working, selected, include_contributors=include_contributors,
                include_cell_key=include_cell_key, include_candidates=include_candidates,
                include_matches=include_matches,
            )

        def merge(name):
            relations = [getattr(result, name) for result in results
                         if getattr(result, name) is not None]
            if name == "status" and any("cell_key" in rows.columns for rows in relations):
                relations = [
                    rows if "cell_key" in rows.columns else rows.with_columns(
                        ma.lit(None, dtype=MountainashDtype.I64).alias("cell_key"),
                    ) for rows in relations
                ]
            return owner.detach(_materialize(owner, concat(relations))) if relations else None

        def merge_fields(name):
            maps = [getattr(result, name) for result in results if getattr(result, name) is not None]
            if not maps:
                return None
            return {
                field: owner.detach(_materialize(owner, concat([mapping[field] for mapping in maps if field in mapping])))
                for field in sorted({field for mapping in maps for field in mapping})
            }

        retained_requests = owner.detach(_materialize(
            owner, concat([r.requests for r in results]).sort("request_order"),
        ))
        return ResolutionResult(
            requests=retained_requests,
            status=merge("status"), requested_outputs=merge("requested_outputs"),
            payloads={
                scope: owner.detach(_materialize(owner, concat([r.payloads[scope] for r in results])))
                for scope in selected
            },
            payload_columns=results[0].payload_columns, _metadata_json=sources._metadata_json,
            matches=QueryResult(
                requests=retained_requests, scopes=tuple(selected),
                matches={
                    scope: owner.detach(_materialize(owner, concat([
                        t.cast(QueryResult, r.matches).scope(scope) for r in results
                    ]))) for scope in selected
                },
                payload_columns=results[0].payload_columns,
                _metadata_json=sources._metadata_json, version_id=None,
            ) if include_matches else None,
            contributors=merge("contributors"),
            candidates=merge("candidates"), cell_identity=merge("cell_identity"),
            cell_identity_boxes=merge("cell_identity_boxes"),
            cell_identity_contributors=merge("cell_identity_contributors"),
            cell_identity_ordered_bounds=merge_fields("cell_identity_ordered_bounds"),
            cell_identity_category_sets=merge_fields("cell_identity_category_sets"),
            cell_identity_category_values=merge_fields("cell_identity_category_values"),
        )


def resolve(
    rules: Relation, metadata: DimensionsMetadata, contexts: Relation,
    scopes: t.Sequence[str] | None = None, *,
    request_control: Relation | None = None, masks: Relation | None = None,
    include_cell_key: bool = False, include_contributors: bool = False,
    include_candidates: bool = False,
) -> SourceResolutionOperation:
    """Capture sources eagerly and defer direct resolution until fetch."""
    selected = tuple(select_scopes(metadata, scopes))
    sources = capture_sources(rules, metadata)
    return SourceResolutionOperation(
        sources, contexts, selected, request_control, masks,
        include_cell_key, include_contributors, include_candidates,
    )


def resolve_lattice(
    lattice: Lattice, contexts: Relation | t.Sequence[t.Mapping[str, t.Any]],
    scopes: t.Sequence[str] | None = None, *,
    request_control: Relation | None = None, masks: Relation | None = None,
    include_cell_key: bool = False, include_contributors: bool = False,
    include_candidates: bool = False,
) -> ResolutionOperation:
    """Compose decisioning over stored retrieval, with an explicit terminal."""
    from mountainash_rules.engines.accumulate.decision import resolve_stored

    return resolve_stored(
        lattice, contexts, scopes, request_control=request_control, masks=masks,
        include_cell_key=include_cell_key, include_contributors=include_contributors,
        include_candidates=include_candidates,
    )
