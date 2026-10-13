"""Context-free native construction of disjoint accumulator regions."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

from types import MappingProxyType
from uuid import uuid4

import mountainash.expressions as ma
from mountainash import materialization_scope
from mountainash.relations import Relation, concat

from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.accumulate.domain import (
    FieldRestriction, Interval, ValueConstraint, feasible_assignments,
    filter_feasible_pairs, intersect_intervals,
)
from mountainash_rules.engines.accumulate.errors import UnsupportedDomainError
from mountainash_rules.engines.accumulate.folds import fold_contributions
from mountainash_rules.engines.accumulate.geometry import (
    _membership_labels, _own, construct_ordered_geometry,
)
from mountainash_rules.engines.accumulate.lattice import Lattice, TableDescriptor
from mountainash_rules.engines.accumulate.sources import SourceBundle, capture_sources


@dataclass(frozen=True, slots=True)
class TargetedGeometry:
    """Owned canonical geometry for an exact set of requested memberships."""

    cells: Relation
    cell_boxes: Relation
    contributors: Relation
    cell_ordered_bounds: t.Mapping[str, Relation]
    cell_category_sets: t.Mapping[str, Relation]
    cell_category_values: t.Mapping[str, Relation]


def _source_timezone(dimension, sources, domain_field=None):
    if domain_field is not None:
        return domain_field.timezone
    if dimension.data_type.value != "datetime":
        return None
    field = (
        dimension.range_min_field
        if dimension.match_strategy is MatchStrategy.RANGE
        else dimension.resolved_rule_field
    )
    dtype = sources.rules.schema[field]
    if hasattr(dtype, "time_zone"):
        return "naive" if dtype.time_zone is None else "utc"
    inner = getattr(dtype, "inner", None)
    if inner is not None and hasattr(inner, "time_zone"):
        return "naive" if inner.time_zone is None else "utc"
    raise ValueError(f"Cannot determine source timezone for {field!r}")

def _ordered_field_states(dimensions, sources, keys):
    groups = {}
    for dimension in dimensions:
        groups.setdefault(dimension.resolved_context_field, []).append(dimension)
    combined_dimensions = []
    combined_states = {}
    for aliases in groups.values():
        first = aliases[0]
        combined = None
        for index, dimension in enumerate(aliases):
            state = sources.states[dimension.dimension_name]
            if dimension.match_strategy is MatchStrategy.RANGE:
                normalized = state.select(
                    *keys, "source_uuid", "lower", "upper",
                    "lower_unbounded", "upper_unbounded",
                    ma.lit(dimension.range_min_inclusive).alias("lower_inclusive"),
                    ma.lit(dimension.range_max_inclusive).alias("upper_inclusive"),
                )
            elif dimension.match_strategy is MatchStrategy.GREATER_THAN:
                normalized = state.select(
                    *keys, "source_uuid",
                    ma.col("value").alias("lower"), ma.col("value").alias("upper"),
                    ma.col("is_wildcard").alias("lower_unbounded"),
                    ma.lit(True).alias("upper_unbounded"),
                    ma.lit(False).alias("lower_inclusive"),
                    ma.lit(False).alias("upper_inclusive"),
                )
            else:
                normalized = state.select(
                    *keys, "source_uuid",
                    ma.col("value").alias("lower"), ma.col("value").alias("upper"),
                    ma.lit(True).alias("lower_unbounded"),
                    ma.col("is_wildcard").alias("upper_unbounded"),
                    ma.lit(False).alias("lower_inclusive"),
                    ma.lit(False).alias("upper_inclusive"),
                )
            if combined is None:
                combined = normalized
                continue
            right_names = {
                name: f"__{name}_{index}"
                for name in (
                    "lower", "upper", "lower_unbounded", "upper_unbounded",
                    "lower_inclusive", "upper_inclusive",
                )
            }
            joined = combined.join(
                normalized.rename(right_names), on=[*keys, "source_uuid"], how="inner"
            )
            interval = intersect_intervals(
                Interval(
                    ma.col("lower"), ma.col("upper"), ma.col("lower_inclusive"),
                    ma.col("upper_inclusive"), ma.col("lower_unbounded"),
                    ma.col("upper_unbounded"),
                ),
                Interval(
                    ma.col(f"__lower_{index}"), ma.col(f"__upper_{index}"),
                    ma.col(f"__lower_inclusive_{index}"),
                    ma.col(f"__upper_inclusive_{index}"),
                    ma.col(f"__lower_unbounded_{index}"),
                    ma.col(f"__upper_unbounded_{index}"),
                ),
            )
            combined = joined.select(
                *keys, "source_uuid", interval.lower.alias("lower"),
                interval.upper.alias("upper"),
                interval.lower_unbounded.alias("lower_unbounded"),
                interval.upper_unbounded.alias("upper_unbounded"),
                interval.lower_closed.alias("lower_inclusive"),
                interval.upper_closed.alias("upper_inclusive"),
            )
        synthetic = first.model_copy(update={
            "match_strategy": MatchStrategy.RANGE,
            "range_min_inclusive": True,
            "range_max_inclusive": True,
        })
        combined_dimensions.append(synthetic)
        combined_states[synthetic.dimension_name] = combined
    return tuple(combined_dimensions), combined_states


def _domain_value_relations(metadata, field, keys, identities):
    from mountainash_rules.core.scalar import decode_scalar

    domains = [metadata.domain] if metadata.domain is not None else []
    pending = [domain.predicate_id for domain in domains]
    visited = set()
    encoded_values = []
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        payload = metadata.domain_nodes.get(identifier)
        if payload is None:
            continue
        node = payload["node"]
        if node["op"] == "eq" and node["field"] == field:
            encoded_values.append(node["value"])
        elif node["op"] == "in" and node["field"] == field:
            encoded_values.extend(node["values"])
        pending.extend(node.get("args", ()))
        if "arg" in node:
            pending.append(node["arg"])
    return [
        identities.select(
            *keys, ma.lit(decode_scalar(dict(value), allow_reserved=True))
            .alias("__candidate_value")
        )
        for value in encoded_values
    ]

def _domain_boundary_relations(metadata, field, keys, identities):
    from mountainash_rules.core.scalar import decode_scalar

    if metadata.domain is None:
        return []
    declaration = next(
        (item for item in metadata.domain.fields if item.name == field), None,
    )

    def boundary(value):
        expression = ma.lit(decode_scalar(dict(value), allow_reserved=True))
        if declaration is None:
            return expression
        if declaration.data_type.value == "decimal":
            return expression.cast(
                declaration.numeric_dtype, rounding=declaration.rounding,
            )
        return expression.cast(declaration.data_type.python_type)

    pending = [metadata.domain.predicate_id]
    visited = set()
    values = []
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        node = metadata.domain_nodes[identifier]["node"]
        if node["op"] in {"eq", "in"} and node["field"] == field:
            encoded = [node["value"]] if node["op"] == "eq" else node["values"]
            values.extend(encoded)
        elif node["op"] == "interval" and node["field"] == field:
            values.extend(value for value in (node["lower"], node["upper"]) if value is not None)
        pending.extend(node.get("args", ()))
        if "arg" in node:
            pending.append(node["arg"])
    return [
        identities.select(*keys, boundary(value).alias("value"))
        for value in values
    ]

def _finite_domain_values(metadata, field):
    from mountainash_rules.core.scalar import decode_scalar

    def constrain(identifier):
        node = metadata.domain_nodes[identifier]["node"]
        op = node["op"]
        if op == "eq":
            return {
                node["field"]: (decode_scalar(dict(node["value"]), allow_reserved=True),)
            }
        if op == "in":
            return {
                node["field"]: tuple(
                    decode_scalar(dict(value), allow_reserved=True)
                    for value in node["values"]
                )
            }
        children = node.get("args", ())
        if op == "and":
            result = {}
            for child in children:
                for name, values in constrain(child).items():
                    result[name] = (
                        tuple(value for value in result[name] if value in values)
                        if name in result else values
                    )
            return result
        if op == "or" and children:
            branches = [constrain(child) for child in children]
            common = set.intersection(*(set(branch) for branch in branches))
            return {
                name: tuple(dict.fromkeys(
                    value for branch in branches for value in branch[name]
                ))
                for name in common
            }
        return {}

    domains = [metadata.domain] if metadata.domain is not None else []
    restrictions = []
    for domain in domains:
        values = constrain(domain.predicate_id)
        if field in values:
            restrictions.append(values[field])
    if not restrictions:
        return None
    values = restrictions[0]
    for restriction in restrictions[1:]:
        values = tuple(value for value in values if value in restriction)
    return values

def _domain_interval_groups(metadata, field):
    if metadata.domain is None:
        return None

    def complement(node):
        op = node["op"]
        if op == "eq" and node["field"] == field:
            value = node["value"]
            return [[(None, value, True, False)], [(value, None, False, True)]]
        if op == "in" and node["field"] == field:
            groups = [()]
            for value in node["values"]:
                alternatives = [
                    (None, value, True, False),
                    (value, None, False, True),
                ]
                groups = [
                    (*group, interval)
                    for group in groups for interval in alternatives
                ]
            return groups
        if op == "interval" and node["field"] == field:
            pieces = []
            if node["lower"] is not None:
                pieces.append([(
                    None, node["lower"], True, not node["lower_closed"],
                )])
            if node["upper"] is not None:
                pieces.append([(
                    node["upper"], None, not node["upper_closed"], True,
                )])
            return pieces
        if op == "and":
            return [
                group for child in node["args"] for group in complement(
                    metadata.domain_nodes[child]["node"]
                )
            ]
        if op == "or":
            groups = [()]
            for child in node["args"]:
                child_groups = complement(metadata.domain_nodes[child]["node"])
                groups = [
                    (*left, *right)
                    for left in groups for right in child_groups
                ]
            return groups
        if op == "not":
            return visit(node["arg"]) or [()]
        return [()]

    def visit(identifier):
        node = metadata.domain_nodes[identifier]["node"]
        op = node["op"]
        if op == "eq" and node["field"] == field:
            value = node["value"]
            return [[(value, value, True, True)]]
        if op == "in" and node["field"] == field:
            return [[(value, value, True, True)] for value in node["values"]]
        if op == "interval" and node["field"] == field:
            return [[(
                node["lower"], node["upper"],
                node["lower_closed"], node["upper_closed"],
            )]]
        if op == "not":
            return complement(metadata.domain_nodes[node["arg"]]["node"])
        if op == "and":
            groups = [()]
            for child in node["args"]:
                child_groups = visit(child)
                if child_groups is None:
                    continue
                groups = [
                    (*left, *right)
                    for left in groups for right in child_groups
                ]
            return groups
        if op == "or":
            branches = [visit(child) for child in node["args"]]
            if any(branch is None for branch in branches):
                return None
            return [group for branch in branches for group in branch]
        return None

    return visit(metadata.domain.predicate_id)


def _clip_ordered_states_to_domain(state, dimension, metadata, keys):
    groups = _domain_interval_groups(metadata, dimension.resolved_context_field)
    if groups is None:
        return state
    from mountainash_rules.core.scalar import decode_scalar

    clipped = []
    for group in groups:
        interval = Interval(
            ma.col("lower"), ma.col("upper"),
            ma.col("lower_inclusive"), ma.col("upper_inclusive"),
            ma.col("lower_unbounded"), ma.col("upper_unbounded"),
        )
        for lower, upper, lower_closed, upper_closed in group:
            def literal(encoded, fallback):
                if encoded is None:
                    return fallback
                value = decode_scalar(dict(encoded), allow_reserved=True)
                expression = ma.lit(value)
                if dimension.data_type.value == "decimal":
                    expression = expression.cast(
                        dimension.numeric_dtype, rounding=dimension.rounding,
                    )
                return expression

            other = Interval(
                literal(lower, ma.col("lower")),
                literal(upper, ma.col("upper")),
                ma.lit(lower_closed), ma.lit(upper_closed),
                ma.lit(lower is None), ma.lit(upper is None),
            )
            interval = intersect_intervals(interval, other)
        clipped.append(state.filter(interval.nonempty).select(
            *keys, "source_uuid", interval.lower.alias("lower"),
            interval.upper.alias("upper"),
            interval.lower_unbounded.alias("lower_unbounded"),
            interval.upper_unbounded.alias("upper_unbounded"),
            interval.lower_closed.alias("lower_inclusive"),
            interval.upper_closed.alias("upper_inclusive"),
        ))
    if not clipped:
        return state.filter(ma.lit(False))
    return concat(clipped, distinct=True)

def _ordered_boundaries(metadata, dimensions, keys, identities, region_keys=None):
    boundaries = {}
    for dimension in dimensions:
        relations = _domain_boundary_relations(
            metadata, dimension.resolved_context_field, keys, identities,
        )
        if not relations:
            continue
        values = concat(relations, distinct=True).select(
            *keys, ma.col("value").alias("__point")
        ).unique(subset=[*keys, "__point"])
        if region_keys is not None:
            values = region_keys.join(values, on=list(keys), how="inner").select(
                *region_keys.columns, "__point"
            ).unique(subset=[*region_keys.columns, "__point"])
        boundaries[dimension.dimension_name] = values
    return boundaries

def _prune_regions_to_domain(
    cells, cell_boxes, bounds_by_field, category_sets_by_field,
    category_values_by_field, contributors, dimensions, metadata, sources, keys,
):
    if metadata.domain is None:
        return cells, cell_boxes, bounds_by_field, category_sets_by_field, category_values_by_field, contributors

    domain_fields = {field.name: field for field in metadata.domain.fields}
    dimensions_by_field = {}
    for dimension in dimensions:
        dimensions_by_field.setdefault(dimension.resolved_context_field, []).append(
            dimension
        )
    regions = cell_boxes.join(
        cells.select(*keys, "cell_id", "contributor_set_id"),
        on=[*keys, "cell_id"], how="inner",
    )
    pair_key = "__domain_region_pair_id"
    while pair_key in regions.columns:
        pair_key = f"_{pair_key}"
    regions = _own(regions.with_row_index(name=pair_key))
    restrictions = {}
    for index, field in enumerate(dict.fromkeys(
        [*bounds_by_field, *category_sets_by_field]
    )):
        aliases = dimensions_by_field[field]
        dimension = aliases[0]
        declaration = domain_fields.get(field)
        numeric_dtype = dimension.numeric_dtype or (
            declaration.numeric_dtype if declaration is not None else None
        )
        timezone = _source_timezone(dimension, sources, declaration)
        rounding = dimension.rounding or (
            declaration.rounding if declaration is not None else None
        )
        intervals = ()
        if field in bounds_by_field:
            names = {
                name: f"__domain_{index}_{name}"
                for name in (
                    "lower_state", "lower_value", "lower_inclusive",
                    "upper_state", "upper_value", "upper_inclusive",
                )
            }
            bounds = bounds_by_field[field].rename(names)
            regions = regions.join(
                bounds, on=[*keys, "cell_id", "box_id"], how="inner"
            )
            intervals = (Interval(
                lower=ma.col(names["lower_value"]),
                upper=ma.col(names["upper_value"]),
                lower_closed=ma.col(names["lower_inclusive"]),
                upper_closed=ma.col(names["upper_inclusive"]),
                lower_unbounded=ma.col(names["lower_state"]).eq("unbounded"),
                upper_unbounded=ma.col(names["upper_state"]).eq("unbounded"),
            ),)
        values = ()
        if field in category_sets_by_field:
            kind_name = f"__domain_{index}_kind"
            kinds = category_sets_by_field[field].select(
                *keys, "cell_id", "box_id",
                ma.col("membership_kind").alias(kind_name),
            )
            regions = regions.join(
                kinds, on=[*keys, "cell_id", "box_id"], how="inner"
            )
            members = regions.select(pair_key, *keys, "cell_id", "box_id").join(
                category_values_by_field[field],
                on=[*keys, "cell_id", "box_id"], how="inner",
            ).select(pair_key, "value")
            cofinite = ma.col(kind_name).eq("cofinite")
            values = (
                ValueConstraint(
                    values=members, is_list=True, allowed=True, inactive=cofinite,
                ),
                ValueConstraint(
                    values=members, is_list=True, allowed=False, inactive=~cofinite,
                ),
            )
        restrictions[field] = FieldRestriction(
            data_type=dimension.data_type, intervals=intervals, values=values,
            numeric_dtype=numeric_dtype, timezone=timezone, rounding=rounding,
        )
    regions = _own(regions)
    feasible = feasible_assignments(
        regions, metadata, {}, restrictions, pair_key=pair_key,
    )[0]
    valid_regions = _own(
        feasible.select(pair_key).unique(subset=[pair_key])
    )
    retained = _own(regions.join(valid_regions, on=pair_key, how="inner"))
    cell_boxes = retained.select(*keys, "cell_id", "box_id")
    valid_cells = cell_boxes.select(*keys, "cell_id").unique(
        subset=[*keys, "cell_id"]
    )
    cells = cells.join(valid_cells, on=[*keys, "cell_id"], how="inner")
    valid_boxes = cell_boxes
    bounds_by_field = {
        field: relation.join(
            valid_boxes, on=[*keys, "cell_id", "box_id"], how="inner"
        )
        for field, relation in bounds_by_field.items()
    }
    category_sets_by_field = {
        field: relation.join(
            valid_boxes, on=[*keys, "cell_id", "box_id"], how="inner"
        )
        for field, relation in category_sets_by_field.items()
    }
    category_values_by_field = {
        field: relation.join(
            valid_boxes, on=[*keys, "cell_id", "box_id"], how="inner"
        )
        for field, relation in category_values_by_field.items()
    }
    active_sets = cells.select(*keys, "contributor_set_id").unique(
        subset=[*keys, "contributor_set_id"]
    )
    contributors = contributors.join(
        active_sets, on=[*keys, "contributor_set_id"], how="inner"
    )
    return (
        cells, cell_boxes, bounds_by_field, category_sets_by_field,
        category_values_by_field, contributors,
    )

def _fold_outputs(
    contributors: Relation,
    sources: SourceBundle,
    metadata: DimensionsMetadata,
    keys: tuple[str, ...],
) -> dict[str, Relation]:
    scopes = metadata.outcome_scopes or {"default": metadata.aggregates}
    outputs = {}
    for scope, aggregates in scopes.items():
        members = contributors.filter(ma.col("scope").eq(scope))
        joined = members.join(
            sources.payloads[scope], on=["partition", "scope", "source_uuid"], how="inner"
        ).select(
            *keys, "contributor_set_id",
            *[ma.col(f"a{index:04d}") for index in range(1, len(aggregates) + 1)],
        )
        outputs[scope] = fold_contributions(
            joined, aggregates, keys=[*keys, "contributor_set_id"],
        )
    return outputs


def build(rules: Relation, metadata: DimensionsMetadata) -> Lattice:
    """Capture sources and build typed cells, memberships and native folds."""
    sources = capture_sources(rules, metadata)
    metadata = sources.metadata
    dimensions = tuple(metadata.dimensions)
    keys = ("partition", "scope")
    bounds_by_field = {}
    category_sets_by_field = {}
    category_values_by_field = {}

    if not dimensions:
        key_rows = sources.identities.select(*keys).unique(subset=list(keys))
        cells = _own(key_rows.with_row_index(name="cell_id")).with_columns(
            ma.col("cell_id").alias("contributor_set_id")
        )
        cell_boxes = cells.with_columns(ma.col("cell_id").alias("box_id")).select(
            *keys, "cell_id", "box_id"
        )
        contributors = sources.identities.join(cells, on=list(keys), how="inner").select(
            *keys, "contributor_set_id", "source_uuid"
        ).unique(subset=[*keys, "contributor_set_id", "source_uuid"])
    elif dimensions and all(
        dimension.match_strategy in {
            MatchStrategy.EXACT, MatchStrategy.EXACT_KEY, MatchStrategy.NOT_EQUAL,
            MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
        }
        for dimension in dimensions
    ):
        field_dimensions: dict[str, list[Dimension]] = {}
        for dimension in dimensions:
            field_dimensions.setdefault(dimension.resolved_context_field, []).append(
                dimension
            )
        candidates = sources.identities.select(*keys).unique(subset=list(keys))
        category_fields = {}
        finite_values = {}
        for index, (field, aliases) in enumerate(field_dimensions.items()):
            value_relations = []
            wildcard_keys = []
            for dimension in aliases:
                state = sources.states[dimension.dimension_name]
                if dimension.match_strategy in {
                    MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION
                }:
                    values = state.select(
                        *keys, ma.col("value").alias("__candidate_value")
                    ).explode("__candidate_value").select(
                        *keys, "__candidate_value"
                    )
                else:
                    values = state.filter(~ma.col("is_wildcard")).select(
                        *keys, ma.col("value").alias("__candidate_value")
                    )
                value_relations.append(values)
                wildcard_keys.append(
                    state.filter(ma.col("is_wildcard")).select(*keys)
                )
            value_relations.extend(
                _domain_value_relations(metadata, field, keys, sources.identities)
            )
            named = concat(value_relations).unique(
                subset=[*keys, "__candidate_value"]
            )
            finite_domain_values = _finite_domain_values(metadata, field)
            residual_required = (
                finite_domain_values is None
                and any(
                    dimension.match_strategy in {
                        MatchStrategy.NOT_EQUAL, MatchStrategy.SET_EXCLUSION
                    }
                    for dimension in aliases
                )
            )
            if finite_domain_values is not None:
                residual_keys = sources.identities.filter(ma.lit(False)).select(*keys)
            elif residual_required:
                residual_keys = sources.identities.select(*keys).unique(
                    subset=list(keys)
                )
            else:
                residual_keys = concat(wildcard_keys).unique(subset=list(keys))
            anchors = named.group_by(*keys).agg(
                ma.col("__candidate_value").min().alias("__candidate_value")
            )
            residual = residual_keys.join(anchors, on=list(keys), how="left").with_columns(
                ma.lit(True).alias("__residual")
            )
            categories = concat([
                named.with_columns(ma.lit(False).alias("__residual")),
                residual,
            ])
            value_name = f"__category_{index}"
            residual_name = f"__residual_{index}"
            categories = categories.select(
                *keys, ma.col("__candidate_value").alias(value_name),
                ma.col("__residual").alias(residual_name),
            )
            candidates = candidates.join(categories, on=list(keys), how="inner")
            category_fields[field] = (value_name, residual_name, index)
            finite_values[field] = categories.filter(
                ~ma.col(residual_name)
            ).select(*keys, ma.col(value_name).alias("value"))
        candidates = _own(candidates.with_row_index(name="fragment_id"))
        pair_relation = candidates.join(
            sources.identities, on=list(keys), how="inner"
        )
        source_restrictions: dict[str, list[ValueConstraint]] = {
            field: [] for field in field_dimensions
        }
        for index, dimension in enumerate(dimensions):
            state = sources.states[dimension.dimension_name].rename({
                "value": f"__source_value_{index}",
                "is_wildcard": f"__source_inactive_{index}",
            })
            pair_relation = pair_relation.join(
                state, on=[*keys, "source_uuid"], how="inner"
            )
            source_restrictions[dimension.resolved_context_field].append(
                ValueConstraint(
                    values=ma.col(f"__source_value_{index}"),
                    is_list=dimension.match_strategy in {
                        MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION
                    },
                    allowed=dimension.match_strategy in {
                        MatchStrategy.EXACT, MatchStrategy.EXACT_KEY,
                        MatchStrategy.SET_MEMBERSHIP,
                    },
                    inactive=ma.col(f"__source_inactive_{index}"),
                )
            )
        pair_key = "__category_source_pair_id"
        while pair_key in pair_relation.columns:
            pair_key = f"_{pair_key}"
        pair_relation = _own(pair_relation.with_row_index(name=pair_key))
        field_descriptors = {}
        domain_fields = {
            declaration.name: declaration
            for domain in ([metadata.domain] if metadata.domain is not None else [])
            + list(metadata.context_domains)
            for declaration in domain.fields
        }
        for field, aliases in field_dimensions.items():
            dimension = aliases[0]
            domain_field = domain_fields.get(field)
            field_descriptors[field] = FieldRestriction(
                data_type=dimension.data_type,
                numeric_dtype=dimension.numeric_dtype or (
                    domain_field.numeric_dtype if domain_field is not None else None
                ),
                timezone=_source_timezone(dimension, sources, domain_field),
                rounding=dimension.rounding or (
                    domain_field.rounding if domain_field is not None else None
                ),
            )
        left = {
            field: FieldRestriction(
                data_type=descriptor.data_type,
                numeric_dtype=descriptor.numeric_dtype,
                timezone=descriptor.timezone,
                rounding=descriptor.rounding,
                values=tuple(source_restrictions[field]),
            )
            for field, descriptor in field_descriptors.items()
        }
        right = {}
        for field, (value_name, residual_name, _) in category_fields.items():
            descriptor = field_descriptors[field]
            excluded = pair_relation.select(pair_key, *keys).join(
                finite_values[field], on=list(keys), how="inner"
            ).select(pair_key, "value")
            right[field] = FieldRestriction(
                data_type=descriptor.data_type,
                numeric_dtype=descriptor.numeric_dtype,
                timezone=descriptor.timezone,
                rounding=descriptor.rounding,
                values=(
                    ValueConstraint(
                        values=ma.col(value_name), is_list=False, allowed=True,
                        inactive=ma.col(residual_name),
                    ),
                    ValueConstraint(
                        values=excluded, is_list=True, allowed=False,
                        inactive=~ma.col(residual_name),
                    ),
                ),
            )
        feasible = filter_feasible_pairs(
            pair_relation, metadata, left, right, pair_key=pair_key,
        )
        contributor_edges = feasible.select(*keys, "fragment_id", "source_uuid")
        candidates = candidates.join(
            contributor_edges.select(*keys, "fragment_id").unique(
                subset=[*keys, "fragment_id"]
            ),
            on=[*keys, "fragment_id"], how="inner",
        )
        membership = _membership_labels(
            candidates.select(*keys, "fragment_id"), contributor_edges, keys=keys
        )
        category_geometry = candidates.join(
            membership, on=[*keys, "fragment_id"], how="inner"
        ).with_columns(
            ma.col("fragment_id").alias("cell_id"),
            ma.col("fragment_id").alias("box_id"),
        )
        cells = category_geometry.select(*keys, "cell_id", "contributor_set_id")
        cell_boxes = category_geometry.select(*keys, "cell_id", "box_id")
        for field, (value_name, residual_name, _) in category_fields.items():
            category_sets_by_field[field] = category_geometry.select(
                *keys, "cell_id", "box_id",
                ma.when(ma.col(residual_name)).then("cofinite")
                .otherwise("finite").alias("membership_kind"),
                ma.col(residual_name).alias("residual_class_included"),
            )
            finite_child = category_geometry.filter(
                ~ma.col(residual_name)
            ).select(
                *keys, "cell_id", "box_id", ma.col(value_name).alias("value")
            )
            residual_child = category_geometry.filter(
                ma.col(residual_name)
            ).select(*keys, "cell_id", "box_id").join(
                finite_values[field], on=list(keys), how="inner"
            ).select(*keys, "cell_id", "box_id", "value")
            category_values_by_field[field] = concat(
                [finite_child, residual_child], distinct=True
            )
        contributors = contributor_edges.join(
            membership, on=[*keys, "fragment_id"], how="inner"
        ).select(*keys, "contributor_set_id", "source_uuid")
    elif (
        any(dimension.match_strategy in {
            MatchStrategy.EXACT, MatchStrategy.EXACT_KEY, MatchStrategy.NOT_EQUAL,
            MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
        } for dimension in dimensions)
        and any(dimension.match_strategy in {
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        } for dimension in dimensions)
        and all(dimension.match_strategy in {
            MatchStrategy.EXACT, MatchStrategy.EXACT_KEY, MatchStrategy.NOT_EQUAL,
            MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        } for dimension in dimensions)
    ):
        category_dimensions = tuple(
            dimension for dimension in dimensions
            if dimension.match_strategy not in {
                MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
            }
        )
        range_aliases = tuple(
            dimension for dimension in dimensions
            if dimension.match_strategy in {
                MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
            }
        )
        range_dimensions, normalized_range_states = _ordered_field_states(
            range_aliases, sources, keys,
        )
        field_dimensions = {}
        for dimension in category_dimensions:
            field_dimensions.setdefault(dimension.resolved_context_field, []).append(
                dimension
            )
        candidates = sources.identities.select(*keys).unique(subset=list(keys))
        category_fields = {}
        finite_values = {}
        for index, (field, aliases) in enumerate(field_dimensions.items()):
            value_relations = []
            wildcard_keys = []
            for dimension in aliases:
                state = sources.states[dimension.dimension_name]
                if dimension.match_strategy in {
                    MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION
                }:
                    values = state.select(
                        *keys, ma.col("value").alias("__candidate_value")
                    ).explode("__candidate_value").select(
                        *keys, "__candidate_value"
                    )
                else:
                    values = state.filter(~ma.col("is_wildcard")).select(
                        *keys, ma.col("value").alias("__candidate_value")
                    )
                value_relations.append(values)
                wildcard_keys.append(
                    state.filter(ma.col("is_wildcard")).select(*keys)
                )
            value_relations.extend(
                _domain_value_relations(metadata, field, keys, sources.identities)
            )
            named = concat(value_relations).unique(
                subset=[*keys, "__candidate_value"]
            )
            finite_domain_values = _finite_domain_values(metadata, field)
            residual_required = (
                finite_domain_values is None
                and any(
                    dimension.match_strategy in {
                        MatchStrategy.NOT_EQUAL, MatchStrategy.SET_EXCLUSION
                    }
                    for dimension in aliases
                )
            )
            if finite_domain_values is not None:
                residual_keys = sources.identities.filter(ma.lit(False)).select(*keys)
            elif residual_required:
                residual_keys = sources.identities.select(*keys).unique(
                    subset=list(keys)
                )
            else:
                residual_keys = concat(wildcard_keys).unique(subset=list(keys))
            anchors = named.group_by(*keys).agg(
                ma.col("__candidate_value").min().alias("__candidate_value")
            )
            categories = concat([
                named.with_columns(ma.lit(False).alias("__residual")),
                residual_keys.join(anchors, on=list(keys), how="left").with_columns(
                    ma.lit(True).alias("__residual")
                ),
            ])
            value_name, residual_name = f"__category_{index}", f"__residual_{index}"
            categories = categories.select(
                *keys, ma.col("__candidate_value").alias(value_name),
                ma.col("__residual").alias(residual_name),
            )
            candidates = candidates.join(categories, on=list(keys), how="inner")
            category_fields[field] = (value_name, residual_name, index)
            finite_values[field] = categories.filter(
                ~ma.col(residual_name)
            ).select(*keys, ma.col(value_name).alias("value"))
        candidates = _own(candidates.with_row_index(name="fragment_id"))
        candidate_pairs = candidates.join(
            sources.identities, on=list(keys), how="inner"
        )
        source_constraints: dict[str, list[ValueConstraint]] = {
            field: [] for field in field_dimensions
        }
        for index, dimension in enumerate(category_dimensions):
            state = sources.states[dimension.dimension_name].rename({
                "value": f"__source_value_{index}",
                "is_wildcard": f"__source_inactive_{index}",
            })
            candidate_pairs = candidate_pairs.join(
                state, on=[*keys, "source_uuid"], how="inner"
            )
            source_constraints[dimension.resolved_context_field].append(
                ValueConstraint(
                    values=ma.col(f"__source_value_{index}"),
                    is_list=dimension.match_strategy in {
                        MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION
                    },
                    allowed=dimension.match_strategy in {
                        MatchStrategy.EXACT, MatchStrategy.EXACT_KEY,
                        MatchStrategy.SET_MEMBERSHIP,
                    },
                    inactive=ma.col(f"__source_inactive_{index}"),
                )
            )
        pair_key = "__mixed_category_pair_id"
        while pair_key in candidate_pairs.columns:
            pair_key = f"_{pair_key}"
        candidate_pairs = _own(candidate_pairs.with_row_index(name=pair_key))
        domain_fields = {
            declaration.name: declaration
            for domain in ([metadata.domain] if metadata.domain is not None else [])
            + list(metadata.context_domains)
            for declaration in domain.fields
        }
        descriptors = {}
        for field, aliases in field_dimensions.items():
            dimension = aliases[0]
            domain_field = domain_fields.get(field)
            descriptors[field] = FieldRestriction(
                data_type=dimension.data_type,
                numeric_dtype=dimension.numeric_dtype or (
                    domain_field.numeric_dtype if domain_field is not None else None
                ),
                timezone=_source_timezone(dimension, sources, domain_field),
                rounding=dimension.rounding or (
                    domain_field.rounding if domain_field is not None else None
                ),
            )
        left = {
            field: FieldRestriction(
                data_type=descriptor.data_type,
                numeric_dtype=descriptor.numeric_dtype,
                timezone=descriptor.timezone,
                rounding=descriptor.rounding,
                values=tuple(source_constraints[field]),
            )
            for field, descriptor in descriptors.items()
        }
        right = {}
        for field, (value_name, residual_name, _) in category_fields.items():
            descriptor = descriptors[field]
            excluded = candidate_pairs.select(pair_key, *keys).join(
                finite_values[field], on=list(keys), how="inner"
            ).select(pair_key, "value")
            right[field] = FieldRestriction(
                data_type=descriptor.data_type,
                numeric_dtype=descriptor.numeric_dtype,
                timezone=descriptor.timezone,
                rounding=descriptor.rounding,
                values=(
                    ValueConstraint(
                        values=ma.col(value_name), is_list=False, allowed=True,
                        inactive=ma.col(residual_name),
                    ),
                    ValueConstraint(
                        values=excluded, is_list=True, allowed=False,
                        inactive=~ma.col(residual_name),
                    ),
                ),
            )
        metadata_for_categories = (
            metadata if set(domain_fields) <= set(field_dimensions)
            else metadata.model_copy(update={"domain": None, "context_domains": []})
        )
        feasible = filter_feasible_pairs(
            candidate_pairs, metadata_for_categories, left, right,
            pair_key=pair_key,
        )
        category_value_fields = [
            value_name for value_name, _, _ in category_fields.values()
        ]
        category_residual_fields = [
            residual_name for _, residual_name, _ in category_fields.values()
        ]
        category_sources = _own(feasible.select(
            *keys, "fragment_id", "source_uuid",
            *category_value_fields, *category_residual_fields,
        ))
        range_states = {}
        for dimension in range_dimensions:
            state = _clip_ordered_states_to_domain(
                normalized_range_states[dimension.dimension_name],
                dimension, metadata, keys,
            )
            range_states[dimension.dimension_name] = _own(
                category_sources.join(
                    state, on=[*keys, "source_uuid"], how="inner"
                ).select(
                    *keys, *category_value_fields, *category_residual_fields,
                    "source_uuid", "lower", "upper", "lower_unbounded", "upper_unbounded",
                    "lower_inclusive", "upper_inclusive",
                )
            )
        geometry_keys = (
            *keys, *category_value_fields, *category_residual_fields
        )
        domain_boundaries = {
            name: _own(boundary)
            for name, boundary in _ordered_boundaries(
                metadata, range_dimensions, keys, sources.identities,
                region_keys=category_sources.select(*geometry_keys).unique(
                    subset=list(geometry_keys)
                ),
            ).items()
        }
        geometry = construct_ordered_geometry(
            range_states, range_dimensions, keys=geometry_keys,
            boundaries=domain_boundaries,
        )
        category_geometry = geometry.cell_boxes
        cells = geometry.cells.select(*keys, "cell_id", "contributor_set_id")
        cell_boxes = category_geometry.select(*keys, "cell_id", "box_id")
        for field_name, relation in geometry.ordered_bounds.items():
            dimension = next(
                item for item in range_dimensions if item.dimension_name == field_name
            )
            bounds_by_field[dimension.resolved_context_field] = relation.select(
                *keys, "cell_id", "box_id", "lower_state", "lower_value",
                "lower_inclusive", "upper_state", "upper_value", "upper_inclusive",
            )
        for field, (value_name, residual_name, _) in category_fields.items():
            category_sets_by_field[field] = category_geometry.select(
                *keys, "cell_id", "box_id",
                ma.when(ma.col(residual_name)).then("cofinite")
                .otherwise("finite").alias("membership_kind"),
                ma.col(residual_name).alias("residual_class_included"),
            )
            finite_child = category_geometry.filter(
                ~ma.col(residual_name)
            ).with_columns(ma.col(value_name).alias("value")).select(
                *keys, "cell_id", "box_id", "value"
            )
            residual_child = category_geometry.filter(
                ma.col(residual_name)
            ).select(*keys, "cell_id", "box_id").join(
                finite_values[field], on=list(keys), how="inner"
            ).select(*keys, "cell_id", "box_id", "value")
            category_values_by_field[field] = concat(
                [finite_child, residual_child], distinct=True
            )
        contributors = geometry.contributors.select(
            *keys, "contributor_set_id", "source_uuid"
        )
    elif dimensions and all(
        dimension.match_strategy in {
            MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN
        }
        for dimension in dimensions
    ) and len({dimension.resolved_context_field for dimension in dimensions}) == 1:
        greater = []
        lesser = []
        for dimension in dimensions:
            state = sources.states[dimension.dimension_name]
            if dimension.match_strategy is MatchStrategy.GREATER_THAN:
                greater.append(state.select(
                    *keys, "source_uuid", ma.col("value").alias("lower"),
                    ma.col("is_wildcard").alias("lower_unbounded"),
                ))
            else:
                lesser.append(state.select(
                    *keys, "source_uuid", ma.col("value").alias("upper"),
                    ma.col("is_wildcard").alias("upper_unbounded"),
                ))
        lower_state = None
        upper_state = None
        if greater:
            lower_state = concat(greater).group_by(
                *keys, "source_uuid"
            ).agg(
                ma.col("lower").max().alias("lower"),
                ma.col("lower_unbounded").all().alias("lower_unbounded"),
            )
        if lesser:
            upper_state = concat(lesser).group_by(
                *keys, "source_uuid"
            ).agg(
                ma.col("upper").min().alias("upper"),
                ma.col("upper_unbounded").all().alias("upper_unbounded"),
            )
        if lower_state is not None and upper_state is not None:
            interval_state = lower_state.join(
                upper_state, on=[*keys, "source_uuid"], how="inner",
            )
        elif lower_state is not None:
            interval_state = lower_state.with_columns(
                ma.col("lower").alias("upper"),
                ma.lit(True).alias("upper_unbounded"),
            )
        else:
            assert upper_state is not None
            interval_state = upper_state.with_columns(
                ma.col("upper").alias("lower"),
                ma.lit(True).alias("lower_unbounded"),
            )
        dimension = dimensions[0].model_copy(update={
            "match_strategy": MatchStrategy.RANGE,
            "range_min_inclusive": False,
            "range_max_inclusive": False,
        })
        interval_state = _clip_ordered_states_to_domain(
            interval_state, dimension, metadata, keys,
        )
        geometry = construct_ordered_geometry(
            {dimension.dimension_name: interval_state}, (dimension,), keys=keys,
            boundaries=_ordered_boundaries(
                metadata, (dimension,), keys, sources.identities,
            ),
        )
        cells = geometry.cells
        cell_boxes = geometry.cell_boxes
        bounds_by_field[dimension.resolved_context_field] = next(
            iter(geometry.ordered_bounds.values())
        )
        contributors = geometry.contributors
    elif dimensions and all(
        dimension.match_strategy in {
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        } for dimension in dimensions
    ):
        ordered_dimensions, ordered_states = _ordered_field_states(
            dimensions, sources, keys,
        )
        ordered_states = {
            dimension.dimension_name: _clip_ordered_states_to_domain(
                ordered_states[dimension.dimension_name],
                dimension, metadata, keys,
            )
            for dimension in ordered_dimensions
        }
        domain_boundaries = _ordered_boundaries(
            metadata, ordered_dimensions, keys, sources.identities,
        )
        geometry = construct_ordered_geometry(
            ordered_states, ordered_dimensions, keys=keys,
            boundaries=domain_boundaries,
        )
        cells = geometry.cells
        cell_boxes = geometry.cell_boxes
        bounds_by_field.update({
            dimension.resolved_context_field:
                geometry.ordered_bounds[dimension.dimension_name]
            for dimension in ordered_dimensions
        })
        contributors = geometry.contributors
    else:
        raise UnsupportedDomainError(
            "Native construction does not support this strategy combination yet"
        )

    (
        cells, cell_boxes, bounds_by_field, category_sets_by_field,
        category_values_by_field, contributors,
    ) = _prune_regions_to_domain(
        cells, cell_boxes, bounds_by_field, category_sets_by_field,
        category_values_by_field, contributors, dimensions, metadata, sources, keys,
    )
    cells = cells.select(
        *keys, "cell_id", ma.lit(1).cast("int64").alias("identity_version"),
        "contributor_set_id",
    )
    outputs = _fold_outputs(contributors, sources, metadata, keys)
    records: list[tuple[str, str | None, str | None, tuple[str, ...], Relation]] = [
        ("cells", None, None, (*keys, "cell_id"), cells),
        ("cell_boxes", None, None, (*keys, "cell_id", "box_id"), cell_boxes),
    ]
    for field, relation in bounds_by_field.items():
        records.append(("cell_ordered_bounds", None, field,
                        (*keys, "cell_id", "box_id"), relation))
    for field, relation in category_sets_by_field.items():
        records.append(("cell_category_sets", None, field,
                        (*keys, "cell_id", "box_id"), relation))
    for field, relation in category_values_by_field.items():
        records.append(("cell_category_values", None, field,
                        (*keys, "cell_id", "box_id", "value"), relation))
    records.append(("contributors", None, None,
                    (*keys, "contributor_set_id", "source_uuid"), contributors))
    for scope, relation in outputs.items():
        records.append(("outputs", scope, None,
                        (*keys, "contributor_set_id"), relation))
    source_roles_start = len(records)
    source_rule_key = tuple(
        field for field in (metadata.partition_field, metadata.outcome_scope_field,
                            metadata.uuid_field) if field is not None
    )
    records.append(("source_rules", None, None, source_rule_key, sources.rules))
    for field, relation in sources.states.items():
        records.append(("source_state", None, field, (*keys, "source_uuid"), relation))
    for scope, relation in sources.payloads.items():
        records.append(("source_payloads", scope, None, (*keys, "source_uuid"), relation))

    with materialization_scope() as owner:
        owned = [owner.detach(relation) for relation in owner.capture(
            *(record[4] for record in records)
        )]
    cursor = source_roles_start
    source_rules = owned[cursor]
    cursor += 1
    frozen_states = {field: owned[cursor + index]
                     for index, field in enumerate(sources.states)}
    cursor += len(sources.states)
    frozen_payloads = {scope: owned[cursor + index]
                       for index, scope in enumerate(sources.payloads)}
    frozen_sources = SourceBundle(
        rules=source_rules, states=MappingProxyType(frozen_states),
        payloads=MappingProxyType(frozen_payloads), identities=sources.identities,
        _metadata_json=sources._metadata_json,
    )

    tables = {}
    table_descriptors = []
    output_columns: dict[str, dict[str, str]] = {}
    scopes = metadata.outcome_scopes or {"default": metadata.aggregates}
    for index, (role, descriptor_scope, descriptor_field, key, _) in enumerate(records, 1):
        table_id = f"t{index:04d}"
        relation = owned[index - 1]
        tables[table_id] = relation
        table_descriptors.append(TableDescriptor(
            table_id=table_id, role=role, schema=tuple(relation.schema.items()),
            key=key, scope=descriptor_scope, field=descriptor_field,
        ))
        if role == "outputs":
            assert descriptor_scope is not None
            output_columns[descriptor_scope] = {
                aggregate.resolved_output_name: f"v{slot:04d}"
                for slot, aggregate in enumerate(scopes[descriptor_scope], 1)
            }
    return Lattice(
        tables=tables, table_descriptors=tuple(table_descriptors), sources=frozen_sources,
        geometry_namespace=uuid4(), _metadata_json=sources._metadata_json,
        output_columns=output_columns,
    )


def _validate_target_memberships(sources, targets):
    keys = ("partition", "scope")
    required = (*keys, "target_id", "source_uuid")
    missing = [field for field in required if field not in targets.columns]
    if missing:
        raise ValueError(f"targets require columns {required!r}; missing {missing!r}")
    targets = targets.select(*required)
    if not targets.count_rows():
        raise ValueError("targets must contain at least one source membership")
    if targets.filter(
        ma.col("partition").is_null() | ma.col("scope").is_null()
        | ma.col("target_id").is_null() | ma.col("source_uuid").is_null()
    ).count_rows():
        raise ValueError("target membership keys must be nonnull")
    if targets.count_rows() != targets.unique(subset=list(required)).count_rows():
        raise ValueError("target memberships must be unique")
    if targets.join(
        sources.identities, on=[*keys, "source_uuid"], how="anti",
    ).count_rows():
        raise ValueError("target membership references an unknown source")
    return targets


def _target_candidate_ids(feasible, targets, keys, fragment_field, *, exact):
    target_keys = [*keys, fragment_field]
    expected = targets.group_by(*keys).agg(
        ma.col("source_uuid").n_unique().alias("__required_count")
    )
    matched = feasible.join(
        targets, on=[*keys, "source_uuid"], how="inner",
    ).group_by(*target_keys).agg(
        ma.col("source_uuid").n_unique().alias("__matched_count")
    ).join(expected, on=list(keys), how="inner")
    if exact:
        actual = feasible.group_by(*target_keys).agg(
            ma.col("source_uuid").n_unique().alias("__actual_count")
        )
        matched = matched.join(actual, on=target_keys, how="inner").filter(
            ma.col("__matched_count").eq(ma.col("__required_count"))
            & ma.col("__actual_count").eq(ma.col("__required_count"))
        )
    else:
        matched = matched.filter(
            ma.col("__matched_count").eq(ma.col("__required_count"))
        )
    return matched.select(*target_keys)


def _targeted_dimensions(metadata):
    dimensions = list(metadata.dimensions)
    fields = {dimension.resolved_context_field for dimension in dimensions}
    referenced = set()
    if metadata.domain is not None:
        pending = [metadata.domain.predicate_id]
        visited = set()
        while pending:
            identifier = pending.pop()
            if identifier in visited:
                continue
            visited.add(identifier)
            payload = metadata.domain_nodes.get(identifier)
            if payload is None:
                continue
            node = payload["node"]
            referenced.update(
                node[name] for name in ("field", "left", "right")
                if name in node
            )
            pending.extend(node.get("args", ()))
            if "arg" in node:
                pending.append(node["arg"])
        for declaration in metadata.domain.fields:
            if declaration.name not in referenced or declaration.name in fields:
                continue
            ordered = declaration.data_type.value != "float" and (
                declaration.data_type.is_numeric
                or declaration.data_type.is_temporal
            )
            name = f"__domain_{declaration.name}"
            while any(d.dimension_name == name for d in dimensions):
                name = f"_{name}"
            dimensions.append(Dimension(
                dimension_name=name, context_field=declaration.name,
                data_type=declaration.data_type,
                numeric_dtype=declaration.numeric_dtype,
                rounding=declaration.rounding,
                match_strategy=(
                    MatchStrategy.RANGE if ordered else MatchStrategy.EXACT
                ),
                range_min_field=f"{name}_lower" if ordered else None,
                range_max_field=f"{name}_upper" if ordered else None,
            ))
            fields.add(declaration.name)
    return tuple(dimensions)


def _domain_axis_state(identities, dimension, ordered):
    dtype = (
        dimension.numeric_dtype if dimension.data_type.value == "decimal"
        else dimension.data_type.python_type
    )
    null = ma.lit(None).cast(dtype)
    keys = ("partition", "scope")
    state = identities.select(*keys, "source_uuid")
    if ordered:
        return state.with_columns(
            null.alias("lower"), null.alias("upper"),
            ma.lit(True).alias("lower_unbounded"),
            ma.lit(True).alias("upper_unbounded"),
            ma.lit(False).alias("lower_inclusive"),
            ma.lit(False).alias("upper_inclusive"),
        )
    return state.with_columns(
        null.alias("value"), ma.lit(True).alias("is_wildcard"),
    )


def _targeted_category_candidates(
    sources, metadata, dimensions, category_states, targets, *, exact_membership,
):
    keys = ("partition", "scope")
    target_keys = (*keys, "target_id")
    field_dimensions = {}
    for dimension in dimensions:
        field_dimensions.setdefault(
            dimension.resolved_context_field, []
        ).append(dimension)

    candidates = sources.identities.select(*keys).unique(subset=list(keys))
    category_fields = {}
    finite_values = {}
    for index, (field, aliases) in enumerate(field_dimensions.items()):
        values = []
        wildcard_keys = []
        for dimension in aliases:
            state = category_states[dimension.dimension_name]
            if dimension.match_strategy in {
                MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
            }:
                values.append(
                    state.select(
                        *keys, ma.col("value").alias("__candidate_value")
                    ).explode("__candidate_value").select(*keys, "__candidate_value")
                )
            else:
                values.append(state.filter(~ma.col("is_wildcard")).select(
                    *keys, ma.col("value").alias("__candidate_value")
                ))
            wildcard_keys.append(
                state.filter(ma.col("is_wildcard")).select(*keys)
            )
        values.extend(_domain_value_relations(
            metadata, field, keys, sources.identities,
        ))
        named = concat(values).unique(subset=[*keys, "__candidate_value"])
        domain_values = _finite_domain_values(metadata, field)
        residual_required = domain_values is None and any(
            dimension.match_strategy in {
                MatchStrategy.NOT_EQUAL, MatchStrategy.SET_EXCLUSION,
            }
            for dimension in aliases
        )
        if domain_values is not None:
            residual_keys = sources.identities.filter(ma.lit(False)).select(*keys)
        elif residual_required:
            residual_keys = sources.identities.select(*keys).unique(
                subset=list(keys)
            )
        else:
            residual_keys = concat(wildcard_keys).unique(subset=list(keys))
        anchors = named.group_by(*keys).agg(
            ma.col("__candidate_value").min().alias("__candidate_value")
        )
        categories = concat([
            named.with_columns(ma.lit(False).alias("__residual")),
            residual_keys.join(anchors, on=list(keys), how="left").with_columns(
                ma.lit(True).alias("__residual")
            ),
        ])
        value_name, residual_name = f"__category_{index}", f"__residual_{index}"
        categories = categories.select(
            *keys, ma.col("__candidate_value").alias(value_name),
            ma.col("__residual").alias(residual_name),
        )
        candidates = candidates.join(categories, on=list(keys), how="inner")
        category_fields[field] = (value_name, residual_name)
        finite_values[field] = categories.filter(
            ~ma.col(residual_name)
        ).select(*keys, ma.col(value_name).alias("value"))

    candidates = _own(candidates.join(
        targets.select(*target_keys).unique(subset=list(target_keys)),
        on=list(keys), how="inner",
    ).with_row_index(name="fragment_id"))
    candidate_pairs = candidates.join(
        sources.identities, on=list(keys), how="inner",
    )
    source_restrictions = {field: [] for field in field_dimensions}
    for index, dimension in enumerate(dimensions):
        state = category_states[dimension.dimension_name].rename({
            "value": f"__source_value_{index}",
            "is_wildcard": f"__source_inactive_{index}",
        })
        candidate_pairs = candidate_pairs.join(
            state, on=[*keys, "source_uuid"], how="inner",
        )
        source_restrictions[dimension.resolved_context_field].append(
            ValueConstraint(
                values=ma.col(f"__source_value_{index}"),
                is_list=dimension.match_strategy in {
                    MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
                },
                allowed=dimension.match_strategy in {
                    MatchStrategy.EXACT, MatchStrategy.EXACT_KEY,
                    MatchStrategy.SET_MEMBERSHIP,
                },
                inactive=ma.col(f"__source_inactive_{index}"),
            )
        )
    pair_key = "__target_category_pair_id"
    while pair_key in candidate_pairs.columns:
        pair_key = f"_{pair_key}"
    candidate_pairs = _own(candidate_pairs.with_row_index(name=pair_key))
    declarations = {
        field.name: field
        for field in (() if metadata.domain is None else metadata.domain.fields)
    }
    descriptors = {}
    for field, aliases in field_dimensions.items():
        dimension = aliases[0]
        declaration = declarations.get(field)
        descriptors[field] = FieldRestriction(
            data_type=dimension.data_type,
            numeric_dtype=dimension.numeric_dtype or (
                declaration.numeric_dtype if declaration is not None else None
            ),
            timezone=_source_timezone(dimension, sources, declaration),
            rounding=dimension.rounding or (
                declaration.rounding if declaration is not None else None
            ),
        )
    left = {
        field: FieldRestriction(
            data_type=descriptor.data_type,
            numeric_dtype=descriptor.numeric_dtype,
            timezone=descriptor.timezone,
            rounding=descriptor.rounding,
            values=tuple(source_restrictions[field]),
        )
        for field, descriptor in descriptors.items()
    }
    right = {}
    for field, (value_name, residual_name) in category_fields.items():
        descriptor = descriptors[field]
        excluded = candidate_pairs.select(pair_key, *keys).join(
            finite_values[field], on=list(keys), how="inner",
        ).select(pair_key, "value")
        right[field] = FieldRestriction(
            data_type=descriptor.data_type,
            numeric_dtype=descriptor.numeric_dtype,
            timezone=descriptor.timezone,
            rounding=descriptor.rounding,
            values=(
                ValueConstraint(
                    values=ma.col(value_name), is_list=False, allowed=True,
                    inactive=ma.col(residual_name),
                ),
                ValueConstraint(
                    values=excluded, is_list=True, allowed=False,
                    inactive=~ma.col(residual_name),
                ),
            ),
        )
    domain_fields = set(declarations)
    category_field_names = set(field_dimensions)
    candidate_metadata = (
        metadata if domain_fields <= category_field_names
        else metadata.model_copy(update={"domain": None, "context_domains": []})
    )
    feasible = filter_feasible_pairs(
        candidate_pairs, candidate_metadata, left, right, pair_key=pair_key,
    ).select(
        *target_keys, "fragment_id", "source_uuid",
        *[name for pair in category_fields.values() for name in pair],
    )
    valid = _target_candidate_ids(
        feasible, targets, target_keys, "fragment_id",
        exact=exact_membership,
    )
    category_regions = candidates.join(
        valid, on=[*target_keys, "fragment_id"], how="inner",
    )
    category_sources = feasible.join(
        valid, on=[*target_keys, "fragment_id"], how="inner",
    ).unique()
    geometry_keys = [
        *target_keys,
        *[name for pair in category_fields.values() for name in pair],
    ]
    category_targets = category_regions.select(*geometry_keys).unique(
        subset=geometry_keys
    ).join(targets, on=list(target_keys), how="inner").select(
        *geometry_keys, "source_uuid",
    ).unique(subset=[*geometry_keys, "source_uuid"])
    return (
        category_regions, category_sources, category_targets,
        category_fields, finite_values, geometry_keys,
    )


def construct_targeted_geometry(sources: SourceBundle, targets: Relation) -> TargetedGeometry:
    """Construct owned cells for exact requested source memberships only."""
    targets = _own(_validate_target_memberships(sources, targets))
    base_keys = ("partition", "scope")
    target_keys = (*base_keys, "target_id")
    target_ids = targets.select(*target_keys).unique(subset=list(target_keys))
    dimensions = _targeted_dimensions(sources.metadata)
    ordered_dimensions = tuple(
        dimension for dimension in dimensions
        if dimension.match_strategy in {
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        }
    )
    category_dimensions = tuple(
        dimension for dimension in dimensions if dimension not in ordered_dimensions
    )
    supported_categories = {
        MatchStrategy.EXACT, MatchStrategy.EXACT_KEY, MatchStrategy.NOT_EQUAL,
        MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
    }
    unsupported = [
        dimension.match_strategy.value for dimension in category_dimensions
        if dimension.match_strategy not in supported_categories
    ]
    if unsupported:
        raise UnsupportedDomainError(
            f"Targeted geometry does not support strategies {sorted(set(unsupported))!r}"
        )

    category_regions: Relation
    category_sources: Relation
    category_targets = targets
    category_fields = {}
    finite_values = {}
    geometry_keys = list(target_keys)
    if category_dimensions:
        (
            category_regions, category_sources, category_targets,
            category_fields, finite_values, geometry_keys,
        ) = _targeted_category_candidates(
            sources, sources.metadata, category_dimensions,
            {
                dimension.dimension_name: (
                    sources.states[dimension.dimension_name]
                    if dimension.dimension_name in sources.states
                    else _domain_axis_state(
                        sources.identities, dimension, ordered=False,
                    )
                )
                for dimension in category_dimensions
            },
            targets,
            exact_membership=not ordered_dimensions,
        )

    bounds_by_field = {}
    category_sets_by_field = {}
    category_values_by_field = {}
    if ordered_dimensions:
        actual_dimensions_in_sources = tuple(
            dimension for dimension in ordered_dimensions
            if dimension.dimension_name in sources.states
        )
        actual_dimensions, actual_states = _ordered_field_states(
            actual_dimensions_in_sources, sources, base_keys,
        ) if actual_dimensions_in_sources else ((), {})
        synthetic = tuple(
            dimension for dimension in ordered_dimensions
            if dimension.dimension_name not in sources.states
        )
        normalized_states = dict(actual_states)
        for dimension in synthetic:
            normalized_states[dimension.dimension_name] = _domain_axis_state(
                sources.identities, dimension, ordered=True,
            )
        normalized_dimensions = (*actual_dimensions, *synthetic)

        region_keys = tuple(geometry_keys)
        range_states = {}
        for dimension in normalized_dimensions:
            state = normalized_states[dimension.dimension_name]
            if category_dimensions:
                active = category_sources.select(
                    *region_keys, "source_uuid",
                ).unique(subset=[*region_keys, "source_uuid"])
                state = active.join(
                    state, on=[*base_keys, "source_uuid"], how="inner",
                ).select(
                    *region_keys, "source_uuid", "lower", "upper",
                    "lower_unbounded", "upper_unbounded",
                    "lower_inclusive", "upper_inclusive",
                )
                state = _clip_ordered_states_to_domain(
                    state, dimension, sources.metadata, region_keys,
                )
            else:
                state = _clip_ordered_states_to_domain(
                    state, dimension, sources.metadata, base_keys,
                ).join(target_ids, on=list(base_keys), how="inner")
            range_states[dimension.dimension_name] = _own(state)

        boundaries = {
            name: _own(boundary)
            for name, boundary in _ordered_boundaries(
                sources.metadata, normalized_dimensions, target_keys, target_ids,
                region_keys=(
                    category_regions.select(*region_keys).unique(
                        subset=list(region_keys)
                    ) if category_dimensions else None
                ),
            ).items()
        }
        geometry = construct_ordered_geometry(
            range_states, normalized_dimensions, keys=region_keys,
            boundaries=boundaries,
            target_memberships=category_targets,
        )
        cells = geometry.cells
        cell_boxes = geometry.cell_boxes
        contributors = geometry.contributors
        for dimension in normalized_dimensions:
            bounds_by_field[dimension.resolved_context_field] = (
                geometry.ordered_bounds[dimension.dimension_name].select(
                    *region_keys, "cell_id", "box_id", "lower_state",
                    "lower_value", "lower_inclusive", "upper_state",
                    "upper_value", "upper_inclusive",
                )
            )
    elif category_dimensions:
        category_geometry = _own(category_regions.with_row_index(name="cell_id")).with_columns(
            ma.col("cell_id").alias("box_id"),
            ma.col("target_id").alias("contributor_set_id"),
        )
        cells = category_geometry.select(
            *geometry_keys, "cell_id", "contributor_set_id",
        )
        cell_boxes = category_geometry.select(*geometry_keys, "cell_id", "box_id")
        contributors = targets.select(
            *target_keys, ma.col("target_id").alias("contributor_set_id"), "source_uuid",
        )
    else:
        actual = sources.identities.join(target_ids, on=list(base_keys), how="inner")
        actual = actual.with_columns(ma.lit(0).alias("__fragment_id"))
        valid = _target_candidate_ids(
            actual, targets, target_keys, "__fragment_id", exact=True,
        )
        active_ids = target_ids.join(
            valid.select(*target_keys).unique(subset=list(target_keys)),
            on=list(target_keys), how="inner",
        )
        if sources.metadata.domain is not None:
            pair_key = "__target_zero_axis_id"
            zero_regions = _own(active_ids.with_row_index(name=pair_key))
            active_ids = active_ids.join(
                feasible_assignments(
                    zero_regions, sources.metadata, {}, {}, pair_key=pair_key,
                )[0].select(*target_keys).unique(subset=list(target_keys)),
                on=list(target_keys), how="inner",
            )
        cells = _own(active_ids.with_row_index(name="cell_id"))
        cell_boxes = cells.select(
            *target_keys, "cell_id", ma.col("cell_id").alias("box_id"),
        )
        contributors = targets

    if category_dimensions and ordered_dimensions:
        category_geometry = cell_boxes
    if category_dimensions:
        for field, (value_name, residual_name) in category_fields.items():
            category_sets_by_field[field] = category_geometry.select(
                *target_keys, "cell_id", "box_id",
                ma.when(ma.col(residual_name)).then("cofinite")
                .otherwise("finite").alias("membership_kind"),
                ma.col(residual_name).alias("residual_class_included"),
            )
            finite_child = category_geometry.filter(
                ~ma.col(residual_name)
            ).select(
                *target_keys, "cell_id", "box_id",
                ma.col(value_name).alias("value"),
            )
            residual_child = category_geometry.filter(
                ma.col(residual_name)
            ).select(*target_keys, "cell_id", "box_id").join(
                finite_values[field], on=list(base_keys), how="inner",
            ).select(*target_keys, "cell_id", "box_id", "value")
            category_values_by_field[field] = concat(
                [finite_child, residual_child], distinct=True,
            )

    if sources.metadata.domain is not None and (bounds_by_field or category_sets_by_field):
        (
            cells, cell_boxes, bounds_by_field, category_sets_by_field,
            category_values_by_field, contributors,
        ) = _prune_regions_to_domain(
            cells, cell_boxes, bounds_by_field, category_sets_by_field,
            category_values_by_field, contributors, dimensions,
            sources.metadata, sources, target_keys,
        )

    active_ids = cells.select(*target_keys).unique(subset=list(target_keys))
    contributors = targets.join(
        active_ids, on=list(target_keys), how="inner",
    ).select(
        *base_keys, ma.col("target_id").alias("contributor_set_id"), "source_uuid",
    ).unique(subset=[*base_keys, "contributor_set_id", "source_uuid"])
    cells = cells.select(
        *base_keys, "cell_id",
        ma.lit(1).cast("int64").alias("identity_version"),
        ma.col("target_id").alias("contributor_set_id"),
    ).unique()
    cell_boxes = cell_boxes.select(
        *base_keys, "cell_id", "box_id",
    ).unique(subset=[*base_keys, "cell_id", "box_id"])
    bounds_by_field = {
        field: relation.select(
            *base_keys, "cell_id", "box_id", "lower_state", "lower_value",
            "lower_inclusive", "upper_state", "upper_value", "upper_inclusive",
        ).unique()
        for field, relation in bounds_by_field.items()
    }
    category_sets_by_field = {
        field: relation.select(
            *base_keys, "cell_id", "box_id", "membership_kind",
            "residual_class_included",
        ).unique()
        for field, relation in category_sets_by_field.items()
    }
    category_values_by_field = {
        field: relation.select(
            *base_keys, "cell_id", "box_id", "value",
        ).unique(subset=[*base_keys, "cell_id", "box_id", "value"])
        for field, relation in category_values_by_field.items()
    }
    return TargetedGeometry(
        cells=_own(cells),
        cell_boxes=_own(cell_boxes),
        contributors=_own(contributors),
        cell_ordered_bounds=MappingProxyType({
            field: _own(relation) for field, relation in bounds_by_field.items()
        }),
        cell_category_sets=MappingProxyType({
            field: _own(relation) for field, relation in category_sets_by_field.items()
        }),
        cell_category_values=MappingProxyType({
            field: _own(relation) for field, relation in category_values_by_field.items()
        }),
    )
