"""Native point applicability over captured sources, without cell construction."""

from __future__ import annotations

import typing as t
from datetime import date, datetime, timezone
from decimal import Decimal

import mountainash.expressions as ma
from mountainash import MountainashDtype, materialization_scope
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.compiler import DimensionCompiler, rule_binding_names
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.contracts import DomainDefinition, DomainField
from mountainash_rules.core.numeric import exact_literal
from mountainash_rules.engines.accumulate.domain import (
    FieldRestriction,
    ValueConstraint,
    feasible_assignments,
)
from mountainash_rules.engines.accumulate.sources import KEYS, SourceBundle


def matched_sources(
    sources: SourceBundle, requests: Relation, scopes: t.Sequence[str],
    *, point_keys: t.Sequence[str] = ("request_id",),
) -> Relation:
    """Return distinct qualified sources for normalized complete request points.

    Callers provide effective point bindings under the shared compiler names.
    Partial-domain alternatives must be refined before entering this function;
    a missing observation is not evidence that all conditional sources apply.
    """
    metadata = sources.metadata
    rules = sources.identities.filter(ma.col("scope").is_in(list(scopes)))
    for dim in metadata.dimensions:
        state = sources.states[dim.dimension_name]
        if dim.match_strategy is MatchStrategy.RANGE:
            lower, lower_state = rule_binding_names(dim, "min")
            upper, upper_state = rule_binding_names(dim, "max")
            bindings = [ma.col("lower").alias(lower), ma.col("upper").alias(upper),
                ma.col("lower_unbounded").alias(lower_state), ma.col("upper_unbounded").alias(upper_state)]
        else:
            value, wildcard = rule_binding_names(dim)
            bindings = [ma.col("value").alias(value), ma.col("is_wildcard").alias(wildcard)]
        rules = rules.join(state.select(*KEYS, *bindings), on=list(KEYS), how="inner")
    joined = requests.join(rules, on="partition", how="inner")
    compiler = DimensionCompiler()
    matches = ma.lit(True)
    for dim in metadata.dimensions:
        predicate = compiler.compile_dimension(dim).ge(0)
        residual_name = f"__ctx_residual_{dim.dimension_name}"
        if dim.match_strategy is not MatchStrategy.RANGE and residual_name in requests.columns:
            _, wildcard = rule_binding_names(dim)
            residual_matches = ma.col(wildcard) | ma.lit(
                dim.match_strategy in (MatchStrategy.NOT_EQUAL, MatchStrategy.SET_EXCLUSION)
            )
            predicate = ma.when(ma.col(residual_name).ge(0)).then(residual_matches).otherwise(predicate)
        matches = matches & predicate
    # Request IDs and qualified source/state keys are admitted as unique.
    # These one-to-one state joins and scalar predicates preserve that grain.
    return joined.filter(matches).select(*point_keys, *KEYS)


def _predicate_fields(domain: DomainDefinition, nodes: t.Mapping[str, t.Any]) -> set[str]:
    """Read only the authored predicate graph to find fields that can affect Q."""
    fields: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        visited.add(identifier)
        payload = nodes.get(identifier)
        if payload is None or not isinstance(payload.get("node"), t.Mapping):
            return
        node = t.cast(t.Mapping[str, t.Any], payload["node"])
        op = node.get("op")
        if op in {"eq", "in", "interval"}:
            fields.add(t.cast(str, node["field"]))
        elif op == "compare":
            fields.update((t.cast(str, node["left"]), t.cast(str, node["right"])))
        elif op in {"and", "or"}:
            for child in node["args"]:
                visit(child)
        elif op == "not":
            visit(node["arg"])
        else:
            fields.update(item.name for item in domain.fields)

    visit(domain.predicate_id)
    return fields


def _decision_boundaries(
    sources: SourceBundle,
    decisions: Relation,
    current: Relation,
    scope: str,
) -> dict[str, Relation]:
    """Bound only source predicates selected by the current logical branch."""
    selected = concat([
        decisions.select("__branch_id", "partition", "scope", "source_uuid"),
        current.select("__branch_id", "partition", "scope", "source_uuid"),
    ]).unique(subset=["__branch_id", "partition", "scope", "source_uuid"])
    by_field: dict[str, list[Relation]] = {}
    for dim in sources.metadata.dimensions:
        if dim.match_strategy is MatchStrategy.CONTEXT_REGEX:
            continue
        state = sources.states[dim.dimension_name].filter(ma.col("scope").eq(scope))
        field = dim.resolved_context_field
        joined = selected.join(
            state, on=["partition", "scope", "source_uuid"], how="inner",
        )
        if dim.match_strategy is MatchStrategy.RANGE:
            pieces = [
                joined.filter(~ma.col(f"{side}_unbounded")).select(
                    "__branch_id", ma.col(side).alias("value"),
                )
                for side in ("lower", "upper")
            ]
        else:
            values = joined.filter(~ma.col("is_wildcard")).select(
                "__branch_id", "value",
            )
            if dim.match_strategy in (
                MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
            ):
                values = values.explode("value")
            pieces = [values]
        by_field.setdefault(field, []).extend(pieces)
    boundaries = {}
    for field, pieces in by_field.items():
        values = concat(pieces).unique(subset=["__branch_id", "value"])
        if values.head(1).count_rows():
            boundaries[field] = values
    return boundaries


def _dimension_timezone(sources: SourceBundle, dim: t.Any) -> str | None:
    if dim.data_type is not DataType.DATETIME:
        return None
    state = sources.states[dim.dimension_name]
    column = "lower" if dim.match_strategy is MatchStrategy.RANGE else "value"
    native = state.filter(ma.lit(False)).to_polars().schema[column]
    native = getattr(native, "inner", native)
    return "utc" if getattr(native, "time_zone", None) is not None else "naive"


def _witness_literal(
    dim: t.Any, domain_field: DomainField | None, sources: SourceBundle,
) -> t.Any:
    if dim.data_type is DataType.DECIMAL:
        dtype = dim.numeric_dtype or (
            domain_field.numeric_dtype if domain_field is not None else None
        )
        if dtype is None:
            raise ValueError(f"Decimal field {dim.resolved_context_field!r} lacks a declaration")
        return ma.lit(Decimal(0), dtype=dtype)
    if dim.data_type is DataType.DATETIME:
        timezone_name = (
            domain_field.timezone if domain_field is not None
            else _dimension_timezone(sources, dim)
        )
        instant = datetime(2000, 1, 1, tzinfo=timezone.utc) if timezone_name == "utc" else datetime(2000, 1, 1)
        return exact_literal(instant)
    value = {
        DataType.BOOL: False,
        DataType.INT: 0,
        DataType.FLOAT: 0.0,
        DataType.STR: "",
        DataType.DATE: date(2000, 1, 1),
    }[dim.data_type]
    return exact_literal(value)


def logical_source_points(
    sources: SourceBundle,
    requests: Relation,
    effective: t.Mapping[str, FieldRestriction],
    domains: t.Sequence[DomainDefinition],
    scope: str,
) -> tuple[Relation, dict[str, str], dict[str, str]]:
    """Branch on each request-local source's positive/negative applicability."""
    def own(query: Relation) -> Relation:
        with materialization_scope() as owner:
            snapshot = owner.snapshot(relation(query.collect()))
            return owner.detach(snapshot)

    metadata = sources.metadata
    branch_name = "__branch_id"
    source_uuid_dtype = sources.identities.schema["source_uuid"]
    branches = own(requests.select("request_id", "partition").with_row_index(
        name=branch_name,
    ))
    empty_decisions = branches.select(branch_name, "partition").with_columns(
        ma.lit(scope).alias("scope"),
        ma.lit(None, dtype=source_uuid_dtype).alias("source_uuid"),
        ma.lit(False).alias("applies"),
    ).filter(ma.lit(False))
    decisions = empty_decisions
    requested_partitions = requests.select("partition").unique(subset=["partition"])
    schedule = own(sources.identities.filter(ma.col("scope").eq(scope)).join(
        requested_partitions, on="partition", how="inner",
    ).select("partition", "scope", "source_uuid").unique(
        subset=["partition", "scope", "source_uuid"],
    ).with_row_index(name="__source_order"))
    source_count = schedule.count_rows()

    def assignments_for(
        active_branches: Relation,
        selected_decisions: Relation,
        current_sources: Relation,
    ) -> tuple[Relation, dict[str, str], dict[str, str]]:
        boundaries = _decision_boundaries(
            sources, selected_decisions, current_sources, scope,
        )
        domain_fields = {
            item.name: item for domain in domains for item in domain.fields
        }
        fields_by_domain = {
            domain.predicate_id: _predicate_fields(domain, metadata.domain_nodes)
            for domain in domains
        }
        relevant = set(boundaries)
        for referenced in fields_by_domain.values():
            relevant.update(referenced)
        for field, restriction in effective.items():
            active = bool(restriction.intervals)
            for constraint in restriction.values:
                if requests.filter(
                    ~constraint.inactive.fill_null(False),
                ).head(1).count_rows():
                    active = True
                    break
            if active:
                relevant.add(field)
        scoped_domains = tuple(
            domain.model_copy(update={
                "fields": tuple(
                    item for item in domain.fields
                    if item.name in fields_by_domain[domain.predicate_id]
                ),
            })
            for domain in domains
        )
        used_domain_fields = {
            item.name for domain in scoped_domains for item in domain.fields
        }
        branch_requests = active_branches.select(branch_name, "request_id")
        left = {}
        for field, restriction in effective.items():
            if field not in relevant:
                continue
            constraints = []
            for constraint in restriction.values:
                constraint_values = constraint.values
                if isinstance(constraint_values, Relation):
                    constraint_values = branch_requests.join(
                        constraint_values, on="request_id", how="inner",
                    ).select(
                        branch_name, ma.col("value").alias("value"),
                    )
                constraints.append(ValueConstraint(
                    constraint_values, constraint.is_list, constraint.allowed,
                    constraint.inactive,
                ))
            left[field] = FieldRestriction(
                restriction.data_type, restriction.intervals, tuple(constraints),
                restriction.numeric_dtype, restriction.timezone, restriction.rounding,
            )
        dimensions_by_field: dict[str, t.Any] = {}
        for declared_dimension in metadata.dimensions:
            dimensions_by_field.setdefault(
                declared_dimension.resolved_context_field, declared_dimension,
            )
        for field in relevant:
            if field in left or field in used_domain_fields:
                continue
            dim = dimensions_by_field.get(field)
            if dim is None:
                raise ValueError(f"No typed interpretation for logical field {field!r}")
            descriptor = domain_fields.get(field)
            left[field] = FieldRestriction(
                data_type=dim.data_type,
                numeric_dtype=dim.numeric_dtype or (
                    descriptor.numeric_dtype if descriptor is not None else None
                ),
                timezone=(
                    descriptor.timezone if descriptor is not None
                    else _dimension_timezone(sources, dim)
                ),
                rounding=dim.rounding or (
                    descriptor.rounding if descriptor is not None else None
                ),
            )
        pair_relation = active_branches.join(
            requests, on=["request_id", "partition"], how="inner",
        )
        assignments, values, residuals = feasible_assignments(
            pair_relation, metadata, left, {}, pair_key=branch_name,
            boundaries=boundaries, domains=scoped_domains,
        )
        extra_values = []
        for field, dim in dimensions_by_field.items():
            if field in values:
                continue
            value_name = f"__logical_value_{field}"
            while value_name in assignments.columns or value_name in values.values():
                value_name = f"_{value_name}"
            residual_name = f"__logical_residual_{field}"
            while residual_name in assignments.columns or residual_name == value_name:
                residual_name = f"_{residual_name}"
            extra_values.extend([
                _witness_literal(dim, domain_fields.get(field), sources).alias(value_name),
                ma.lit(-1, dtype=MountainashDtype.I64).alias(residual_name),
            ])
            values[field] = value_name
            residuals[field] = residual_name
        if extra_values:
            assignments = assignments.with_columns(*extra_values)
        context_columns = []
        for dim in metadata.dimensions:
            field = dim.resolved_context_field
            context_columns.extend([
                ma.col(values[field]).alias(f"__ctx_value_{dim.dimension_name}"),
                ma.lit(True).alias(f"__ctx_present_{dim.dimension_name}"),
                ma.col(residuals[field]).alias(
                    f"__ctx_residual_{dim.dimension_name}",
                ),
            ])
        points = (
            assignments.with_columns(*context_columns)
            if context_columns else assignments
        )
        return points, values, residuals

    for source_order in range(source_count):
        targets = branches.join(
            schedule.filter(ma.col("__source_order").eq(source_order)),
            on="partition", how="inner",
        ).select(branch_name, "request_id", "partition", "scope", "source_uuid")
        untouched = branches.join(
            targets.select(branch_name), on=branch_name, how="anti",
        )
        points, _, _ = assignments_for(branches, decisions, targets)
        points = own(points.with_row_index(name="__point_id"))
        point_branches = points.select("__point_id", branch_name)
        matches = own(matched_sources(
            sources, points, (scope,),
            point_keys=(branch_name, "request_id", "__point_id"),
        ))
        required = point_branches.join(
            decisions.filter(ma.col("applies")), on=branch_name, how="inner",
        )
        forbidden = point_branches.join(
            decisions.filter(~ma.col("applies")), on=branch_name, how="inner",
        )
        missing_required = required.join(
            matches,
            on=[branch_name, "__point_id", "source_uuid"], how="anti",
        ).select("__point_id")
        found_forbidden = forbidden.join(
            matches,
            on=[branch_name, "__point_id", "source_uuid"], how="inner",
        ).select("__point_id")
        invalid_points = concat([
            missing_required, found_forbidden,
        ]).unique(subset=["__point_id"])
        viable = points.join(
            invalid_points, on="__point_id", how="anti",
        )
        current_matches = matches.join(
            targets.select(branch_name, "source_uuid"),
            on=[branch_name, "source_uuid"], how="inner",
        ).select("__point_id", branch_name).unique(
            subset=["__point_id", branch_name],
        )
        positive = viable.join(
            current_matches, on=["__point_id", branch_name], how="inner",
        ).select(branch_name).unique(subset=[branch_name])
        negative = viable.join(
            current_matches, on=["__point_id", branch_name], how="anti",
        ).select(branch_name).unique(subset=[branch_name])
        positive_children = targets.join(
            positive, on=branch_name, how="inner",
        ).select(
            ma.col(branch_name).alias("__old_branch"), "request_id", "partition",
            "source_uuid", ma.lit(True).alias("applies"),
        )
        negative_children = targets.join(
            negative, on=branch_name, how="inner",
        ).select(
            ma.col(branch_name).alias("__old_branch"), "request_id", "partition",
            "source_uuid", ma.lit(False).alias("applies"),
        )
        unchanged_children = untouched.select(
            ma.col(branch_name).alias("__old_branch"), "request_id", "partition",
        ).with_columns(
            ma.lit(None, dtype=source_uuid_dtype).alias("source_uuid"),
            ma.lit(None, dtype=MountainashDtype.BOOL).alias("applies"),
        )
        children = own(concat([
            positive_children, negative_children, unchanged_children,
        ]).with_row_index(name="__new_branch"))
        remapped = decisions.join(
            children.select(
                ma.col("__old_branch").alias(branch_name), "__new_branch",
            ),
            on=branch_name, how="inner",
        ).select(
            "__new_branch", "partition", "scope", "source_uuid", "applies",
        ).rename({"__new_branch": branch_name})
        added = children.filter(ma.col("source_uuid").is_not_null()).select(
            ma.col("__new_branch").alias(branch_name), "partition",
            ma.lit(scope).alias("scope"), "source_uuid", "applies",
        )
        decisions = own(concat([remapped, added]))
        branches = children.select(
            ma.col("__new_branch").alias(branch_name), "request_id", "partition",
        )

    empty_current = branches.select(branch_name, "partition").with_columns(
        ma.lit(scope).alias("scope"),
        ma.lit(None, dtype=source_uuid_dtype).alias("source_uuid"),
    ).filter(ma.lit(False))
    points, values, residuals = assignments_for(branches, decisions, empty_current)
    points = own(points.with_row_index(name="__point_id"))
    matches = own(matched_sources(
        sources, points, (scope,),
        point_keys=(branch_name, "request_id", "__point_id"),
    ))
    point_branches = points.select("__point_id", branch_name)
    required = point_branches.join(
        decisions.filter(ma.col("applies")), on=branch_name, how="inner",
    )
    forbidden = point_branches.join(
        decisions.filter(~ma.col("applies")), on=branch_name, how="inner",
    )
    invalid_points = concat([
        required.join(
            matches,
            on=[branch_name, "__point_id", "source_uuid"], how="anti",
        ).select("__point_id"),
        forbidden.join(
            matches,
            on=[branch_name, "__point_id", "source_uuid"], how="inner",
        ).select("__point_id"),
    ]).unique(subset=["__point_id"])
    points = points.join(invalid_points, on="__point_id", how="anti")
    chosen = points.group_by(branch_name).agg(ma.col("__point_id").min().alias("__point_id"))
    points = own(points.join(chosen, on=[branch_name, "__point_id"]).select(
        branch_name, "request_id", "partition", *values.values(), *residuals.values(),
    ))
    return points, values, residuals
