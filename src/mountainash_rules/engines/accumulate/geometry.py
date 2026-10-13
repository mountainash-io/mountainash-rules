"""Native composition, suffix merging and face-connected box normalization."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma
from mountainash import materialization_scope
from mountainash.relations import Relation, concat, relation

from mountainash_rules.core.dimension import Dimension
from mountainash_rules.engines.accumulate.pieces import ordered_pieces, piece_coverage


@dataclass(frozen=True, slots=True)
class OrderedGeometry:
    cells: Relation
    cell_boxes: Relation
    ordered_bounds: t.Mapping[str, Relation]
    contributors: Relation


def _own(query: Relation) -> Relation:
    with materialization_scope() as owner:
        snapshot = owner.snapshot(relation(query.collect()))
        return owner.detach(snapshot)


def _label_components(
    nodes: Relation,
    edges: Relation,
    *,
    keys: tuple[str, ...],
    node_field: str,
    neighbor_field: str,
    label_field: str = "component_label",
) -> Relation:
    """Propagate minimum physical labels natively until scalar convergence."""
    labels = _own(nodes.select(
        *keys, ma.col(node_field).alias("__node")
    ).unique(subset=[*keys, "__node"]).with_columns(
        ma.col("__node").alias("__label")
    ))
    forward = edges.select(
        *keys, ma.col(node_field).alias("__node"), ma.col(neighbor_field).alias("__neighbor")
    )
    reverse = edges.select(
        *keys, ma.col(neighbor_field).alias("__node"), ma.col(node_field).alias("__neighbor")
    )
    neighbors = _own(concat([forward, reverse], distinct=True))
    while True:
        propagated = neighbors.join(
            labels, left_on=[*keys, "__neighbor"], right_on=[*keys, "__node"], how="inner"
        ).select(*keys, ma.col("__node").alias("__node"), "__label")
        candidates = concat([
            labels.select(*keys, "__node", "__label"), propagated,
        ])
        revised = candidates.group_by(*keys, "__node").agg(
            ma.col("__label").min().alias("__label")
        )
        revised = _own(revised)
        changed = revised.join(
            labels, on=[*keys, "__node"], how="inner", suffix="_old"
        ).filter(ma.col("__label").ne(ma.col("__label_old"))).count_rows()
        labels = revised
        if not changed:
            break
    return labels.with_columns(ma.col("__node").alias(node_field)).select(
        *keys, node_field, ma.col("__label").alias(label_field)
    )

def _target_piece_coverage(coverage, targets, keys):
    required = targets.group_by(*keys).agg(
        ma.col("source_uuid").n_unique().alias("__required_count")
    )
    covered = coverage.join(
        targets, on=[*keys, "source_uuid"], how="inner",
    ).group_by(*keys, "piece_id").agg(
        ma.col("source_uuid").n_unique().alias("__covered_count")
    )
    return coverage.join(
        covered.join(required, on=list(keys), how="inner").filter(
            ma.col("__covered_count").eq(ma.col("__required_count"))
        ).select(*keys, "piece_id"),
        on=[*keys, "piece_id"], how="inner",
    )


def _exact_target_fragments(fragments, fragment_edges, targets, keys):
    fragment_key = [*keys, "fragment_id"]
    actual = fragment_edges.group_by(*fragment_key).agg(
        ma.col("source_uuid").n_unique().alias("__actual_count")
    )
    required = targets.group_by(*keys).agg(
        ma.col("source_uuid").n_unique().alias("__required_count")
    )
    matched = fragment_edges.join(
        targets, on=[*keys, "source_uuid"], how="inner",
    ).group_by(*fragment_key).agg(
        ma.col("source_uuid").n_unique().alias("__matched_count")
    )
    valid = actual.join(matched, on=fragment_key, how="inner").join(
        required, on=list(keys), how="inner",
    ).filter(
        ma.col("__actual_count").eq(ma.col("__required_count"))
        & ma.col("__matched_count").eq(ma.col("__required_count"))
    ).select(*fragment_key)
    fragments = fragments.join(valid, on=fragment_key, how="inner")
    fragment_edges = fragment_edges.join(valid, on=fragment_key, how="inner")
    return fragments, fragment_edges


def _membership_labels(
    fragments: Relation,
    contributor_edges: Relation,
    *,
    keys: tuple[str, ...],
) -> Relation:
    """Assign one native local label to every exactly equal UUID set."""
    left = fragments.select(
        *[ma.col(key).alias(f"__left_{index}") for index, key in enumerate(keys)],
        ma.col("fragment_id").alias("__left_id"),
    )
    right = fragments.select(
        *[ma.col(key).alias(f"__right_{index}") for index, key in enumerate(keys)],
        ma.col("fragment_id").alias("__right_id"),
    )
    cross = left.join(right, how="cross").filter(
        *[ma.col(f"__left_{index}").eq(ma.col(f"__right_{index}"))
          for index, _ in enumerate(keys)],
        ma.col("__left_id").le(ma.col("__right_id")),
    )
    pair_columns = [*[f"__left_{index}" for index, _ in enumerate(keys)],
                    "__left_id", "__right_id"]
    pairs = _own(cross.select(*pair_columns))

    left_edges = contributor_edges.with_columns(
        ma.col("fragment_id").alias("__left_id")
    ).drop("fragment_id")
    right_edges = contributor_edges.with_columns(
        ma.col("fragment_id").alias("__right_id")
    ).drop("fragment_id")
    left_pairs = _own(pairs.join(
        left_edges,
        left_on=[*[f"__left_{index}" for index, _ in enumerate(keys)], "__left_id"],
        right_on=[*keys, "__left_id"], how="inner",
    ))
    right_pairs = _own(pairs.join(
        right_edges,
        left_on=[*[f"__left_{index}" for index, _ in enumerate(keys)], "__right_id"],
        right_on=[*keys, "__right_id"], how="inner",
    ))
    pair_keys = [*[f"__left_{index}" for index, _ in enumerate(keys)],
                 "__left_id", "__right_id"]
    member_key = [*pair_keys, "source_uuid"]
    left_only = left_pairs.join(right_pairs, on=member_key, how="anti").select(
        *pair_keys
    ).unique(subset=pair_keys)
    right_only = right_pairs.join(left_pairs, on=member_key, how="anti").select(
        *pair_keys
    ).unique(subset=pair_keys)
    equal_pairs = _own(pairs.join(left_only, on=pair_keys, how="anti").join(
        right_only, on=pair_keys, how="anti"
    ))
    forward = equal_pairs.select(
        *[ma.col(f"__left_{index}").alias(key) for index, key in enumerate(keys)],
        ma.col("__left_id").alias("__node"), ma.col("__right_id").alias("__neighbor"),
    )
    labels = _label_components(
        fragments.with_columns(ma.col("fragment_id").alias("__node")).select(*keys, "__node"),
        forward, keys=keys, node_field="__node", neighbor_field="__neighbor",
        label_field="contributor_set_id",
    )
    return labels.select(
        *keys, ma.col("__node").alias("fragment_id"), "contributor_set_id"
    )


def _interval_overlap(left: str, right: str, prefix_left: str, prefix_right: str) -> ma.BaseExpressionAPI:
    left_lower = ma.col(f"{prefix_left}{left}_lower")
    left_upper = ma.col(f"{prefix_left}{left}_upper")
    right_lower = ma.col(f"{prefix_right}{right}_lower")
    right_upper = ma.col(f"{prefix_right}{right}_upper")
    left_low_unbounded = ma.col(f"{prefix_left}{left}_lower_unbounded")
    left_high_unbounded = ma.col(f"{prefix_left}{left}_upper_unbounded")
    right_low_unbounded = ma.col(f"{prefix_right}{right}_lower_unbounded")
    right_high_unbounded = ma.col(f"{prefix_right}{right}_upper_unbounded")
    left_before = (
        left_high_unbounded | right_low_unbounded | left_upper.gt(right_lower)
        | (left_upper.eq(right_lower)
           & ma.col(f"{prefix_left}{left}_upper_inclusive")
           & ma.col(f"{prefix_right}{right}_lower_inclusive"))
    )
    right_before = (
        right_high_unbounded | left_low_unbounded | right_upper.gt(left_lower)
        | (right_upper.eq(left_lower)
           & ma.col(f"{prefix_right}{right}_upper_inclusive")
           & ma.col(f"{prefix_left}{left}_lower_inclusive"))
    )
    return (left_before & right_before).fill_null(False)


def _pair_geometry(
    boxes: Relation,
    dimensions: tuple[Dimension, ...],
    *,
    keys: tuple[str, ...],
    membership_field: str,
    touching_axis: str,
) -> Relation:
    left_names = [*[f"__lk_{i}" for i, _ in enumerate(keys)], "__lm", "__li"]
    right_names = [*[f"__rk_{i}" for i, _ in enumerate(keys)], "__rm", "__ri"]
    left_expr = [*[ma.col(key).alias(name) for key, name in zip(keys, left_names)],
                 ma.col(membership_field).alias("__lm"), ma.col("box_id").alias("__li")]
    right_expr = [*[ma.col(key).alias(name) for key, name in zip(keys, right_names)],
                  ma.col(membership_field).alias("__rm"), ma.col("box_id").alias("__ri")]
    for index, _ in enumerate(dimensions):
        for endpoint in ("lower", "upper", "lower_inclusive", "upper_inclusive",
                         "lower_unbounded", "upper_unbounded"):
            left_expr.append(ma.col(f"__g{index}_{endpoint}").alias(f"__l{index}_{endpoint}"))
            right_expr.append(ma.col(f"__g{index}_{endpoint}").alias(f"__r{index}_{endpoint}"))
    left = boxes.select(*left_expr)
    right = boxes.select(*right_expr)
    cross = left.join(right, how="cross")
    conditions = [ma.col(a).eq(ma.col(b)) for a, b in zip(left_names[:len(keys)], right_names[:len(keys)])]
    conditions.extend([
        ma.col("__lm").eq(ma.col("__rm")), ma.col("__li").lt(ma.col("__ri")),
    ])
    axis_index = next(i for i, dim in enumerate(dimensions) if dim.dimension_name == touching_axis)
    low_touch = ma.col(f"__l{axis_index}_upper").eq(ma.col(f"__r{axis_index}_lower")) & (
        ma.col(f"__l{axis_index}_upper_inclusive") | ma.col(f"__r{axis_index}_lower_inclusive")
    )
    high_touch = ma.col(f"__r{axis_index}_upper").eq(ma.col(f"__l{axis_index}_lower")) & (
        ma.col(f"__r{axis_index}_upper_inclusive") | ma.col(f"__l{axis_index}_lower_inclusive")
    )
    conditions.append(low_touch | high_touch)
    for index, dim in enumerate(dimensions):
        if dim.dimension_name != touching_axis:
            for endpoint in ("lower", "upper", "lower_inclusive", "upper_inclusive",
                             "lower_unbounded", "upper_unbounded"):
                left_value = ma.col(f"__l{index}_{endpoint}")
                right_value = ma.col(f"__r{index}_{endpoint}")
                equal = left_value.eq(right_value).fill_null(False)
                if endpoint in {"lower", "upper"}:
                    unbounded = "lower_unbounded" if endpoint == "lower" else "upper_unbounded"
                    equal = equal | (
                        ma.col(f"__l{index}_{unbounded}")
                        & ma.col(f"__r{index}_{unbounded}")
                    )
                conditions.append(equal)
    return cross.filter(*conditions).select(
        *[ma.col(name).alias(key) for name, key in zip(left_names[:len(keys)], keys)],
        ma.col("__lm").alias(membership_field),
        ma.col("__li").alias("left_id"), ma.col("__ri").alias("right_id"),
    ).unique(subset=[*keys, membership_field, "left_id", "right_id"])


def _merge_axis(
    boxes: Relation,
    dimensions: tuple[Dimension, ...],
    axis: Dimension,
    *,
    keys: tuple[str, ...],
    membership_field: str,
) -> Relation:
    pair_edges = _pair_geometry(
        boxes, dimensions, keys=keys, membership_field=membership_field,
        touching_axis=axis.dimension_name,
    )
    nodes = boxes.select(*keys, membership_field, "box_id")
    graph = pair_edges.with_columns(
        ma.col("left_id").alias("box_id"),
        ma.col("right_id").alias("__neighbor"),
    ).select(*keys, membership_field, "box_id", "__neighbor")
    labels = _label_components(
        nodes, graph, keys=(*keys, membership_field), node_field="box_id",
        neighbor_field="__neighbor", label_field="__merge_label",
    )
    tagged = boxes.join(labels, on=[*keys, membership_field, "box_id"], how="inner")
    axis_index = next(i for i, dim in enumerate(dimensions) if dim.dimension_name == axis.dimension_name)
    low = f"__g{axis_index}_lower"
    high = f"__g{axis_index}_upper"
    low_inc = f"__g{axis_index}_lower_inclusive"
    high_inc = f"__g{axis_index}_upper_inclusive"
    low_unbounded = f"__g{axis_index}_lower_unbounded"
    high_unbounded = f"__g{axis_index}_upper_unbounded"
    group_keys = [*keys, membership_field, "__merge_label"]
    for index, dim in enumerate(dimensions):
        if dim.dimension_name != axis.dimension_name:
            group_keys.extend(f"__g{index}_{endpoint}" for endpoint in (
                "lower", "upper", "lower_inclusive", "upper_inclusive",
                "lower_unbounded", "upper_unbounded",
            ))
    extents = tagged.group_by(*group_keys).agg(
        ma.col(low).min().alias("__new_lower"), ma.col(high).max().alias("__new_upper"),
        ma.col(low_unbounded).any().alias("__new_lower_unbounded"),
        ma.col(high_unbounded).any().alias("__new_upper_unbounded"),
    )
    lower_edge = tagged.join(
        extents.select(*group_keys, ma.col("__new_lower").alias("__edge")),
        left_on=[*group_keys, low], right_on=[*group_keys, "__edge"], how="inner",
    ).group_by(*group_keys).agg(ma.col(low_inc).any().alias("__new_lower_inclusive"))
    upper_edge = tagged.join(
        extents.select(*group_keys, ma.col("__new_upper").alias("__edge")),
        left_on=[*group_keys, high], right_on=[*group_keys, "__edge"], how="inner",
    ).group_by(*group_keys).agg(ma.col(high_inc).any().alias("__new_upper_inclusive"))
    merged = extents.join(lower_edge, on=group_keys, how="left").join(
        upper_edge, on=group_keys, how="left"
    ).with_columns(
        ma.col("__new_lower_inclusive").fill_null(False),
        ma.col("__new_upper_inclusive").fill_null(False),
    )
    for endpoint, value, flag in (
        ("lower", "__new_lower", "__new_lower_unbounded"),
        ("upper", "__new_upper", "__new_upper_unbounded"),
        ("lower_inclusive", "__new_lower_inclusive", None),
        ("upper_inclusive", "__new_upper_inclusive", None),
        ("lower_unbounded", "__new_lower_unbounded", None),
        ("upper_unbounded", "__new_upper_unbounded", None),
    ):
        target = f"__g{axis_index}_{endpoint}"
        expression = ma.col(value) if flag is None else ma.col(value)
        if endpoint in {"lower", "upper"}:
            expression = ma.when(ma.col(f"__new_{endpoint}_unbounded")).then(
                ma.lit(None)
            ).otherwise(ma.col(value))
        merged = merged.with_columns(expression.alias(target))
    keep = [*keys, membership_field,
            *[f"__g{i}_{endpoint}" for i, dim in enumerate(dimensions)
              for endpoint in ("lower", "upper", "lower_inclusive", "upper_inclusive",
                               "lower_unbounded", "upper_unbounded")]]
    return merged.select(*keep).unique(subset=keep).with_row_index(name="box_id")



def construct_ordered_geometry(
    states: t.Mapping[str, Relation],
    dimensions: t.Sequence[Dimension],
    *,
    keys: t.Sequence[str],
    boundaries: t.Mapping[str, Relation] | None = None,
    target_memberships: Relation | None = None,
) -> OrderedGeometry:
    """Compose viable source pieces, canonicalize suffixes and label components."""
    keys = tuple(keys)
    ordered = tuple(sorted(dimensions, key=lambda dim: dim.resolved_context_field))
    if not ordered or any(dim.match_strategy.value != "range" for dim in ordered):
        raise ValueError("construct_ordered_geometry requires ordered range dimensions")
    axis_pieces = {}
    partials: Relation | None = None
    piece_keys = []
    for index, dim in enumerate(ordered):
        state = states[dim.dimension_name]
        if "lower_inclusive" not in state.columns:
            state = state.with_columns(
                ma.lit(dim.range_min_inclusive).alias("lower_inclusive"),
                ma.lit(dim.range_max_inclusive).alias("upper_inclusive"),
            )
        pieces = _own(ordered_pieces(
            state, keys=keys, boundaries=(boundaries or {}).get(dim.dimension_name),
            lower_inclusive=dim.range_min_inclusive,
            upper_inclusive=dim.range_max_inclusive,
        ))
        coverage = piece_coverage(
            pieces, state, keys=keys,
            lower_inclusive=dim.range_min_inclusive,
            upper_inclusive=dim.range_max_inclusive,
        )
        if target_memberships is not None:
            coverage = _target_piece_coverage(
                coverage, target_memberships, keys,
            )
        coverage = coverage.with_columns(
            ma.col("piece_id").alias(f"__piece_{index}")
        ).select(*keys, "source_uuid", f"__piece_{index}")
        piece_keys.append(f"__piece_{index}")
        axis_pieces[dim.dimension_name] = (index, pieces)
        partials = _own(
            coverage if partials is None else partials.join(
                coverage, on=[*keys, "source_uuid"], how="inner"
            )
        )
    assert partials is not None
    fragments = _own(partials.select(*keys, *piece_keys).unique(
        subset=[*keys, *piece_keys]
    ).with_row_index(name="fragment_id"))
    fragment_edges = _own(partials.join(
        fragments, on=[*keys, *piece_keys], how="inner"
    ).select(*keys, "fragment_id", "source_uuid").unique(
        subset=[*keys, "fragment_id", "source_uuid"]
    ))
    if target_memberships is not None:
        fragments, fragment_edges = _exact_target_fragments(
            fragments, fragment_edges, target_memberships, keys,
        )
    membership = _own(_membership_labels(fragments, fragment_edges, keys=keys))
    boxes = _own(fragments.with_columns(ma.col("fragment_id").alias("box_id")).join(
        membership, on=[*keys, "fragment_id"], how="inner"
    ).select(*keys, "box_id", "contributor_set_id", *piece_keys))
    for dim in ordered:
        index, pieces = axis_pieces[dim.dimension_name]
        coordinates = pieces.with_columns(
            ma.col("piece_id").alias(f"__piece_{index}")
        ).select(
            *keys, f"__piece_{index}",
            ma.col("lower").alias(f"__g{index}_lower"),
            ma.col("upper").alias(f"__g{index}_upper"),
            ma.col("lower_inclusive").alias(f"__g{index}_lower_inclusive"),
            ma.col("upper_inclusive").alias(f"__g{index}_upper_inclusive"),
            ma.col("lower_unbounded").alias(f"__g{index}_lower_unbounded"),
            ma.col("upper_unbounded").alias(f"__g{index}_upper_unbounded"),
        )
        boxes = _own(boxes.join(
            coordinates, on=[*keys, f"__piece_{index}"], how="inner"
        ))
    for dim in reversed(ordered):
        boxes = _own(_merge_axis(
            boxes, ordered, dim, keys=keys, membership_field="contributor_set_id",
        ))

    left_names = [*[f"__lk_{i}" for i, _ in enumerate(keys)], "__lm", "__li"]
    right_names = [*[f"__rk_{i}" for i, _ in enumerate(keys)], "__rm", "__ri"]
    left_expr = [*[ma.col(key).alias(name) for key, name in zip(keys, left_names)],
                 ma.col("contributor_set_id").alias("__lm"), ma.col("box_id").alias("__li")]
    right_expr = [*[ma.col(key).alias(name) for key, name in zip(keys, right_names)],
                  ma.col("contributor_set_id").alias("__rm"), ma.col("box_id").alias("__ri")]
    for index, _ in enumerate(ordered):
        for endpoint in ("lower", "upper", "lower_inclusive", "upper_inclusive",
                         "lower_unbounded", "upper_unbounded"):
            left_expr.append(ma.col(f"__g{index}_{endpoint}").alias(f"__l{index}_{endpoint}"))
            right_expr.append(ma.col(f"__g{index}_{endpoint}").alias(f"__r{index}_{endpoint}"))
    left = boxes.select(*left_expr)
    right = boxes.select(*right_expr)
    cross = left.join(right, how="cross")
    conditions = [ma.col(a).eq(ma.col(b)) for a, b in zip(left_names[:len(keys)], right_names[:len(keys)])]
    conditions.extend([ma.col("__lm").eq(ma.col("__rm")), ma.col("__li").lt(ma.col("__ri"))])
    face_conditions = []
    for axis_index, dim in enumerate(ordered):
        touch = (
            ma.col(f"__l{axis_index}_upper").eq(ma.col(f"__r{axis_index}_lower"))
            & (ma.col(f"__l{axis_index}_upper_inclusive")
               | ma.col(f"__r{axis_index}_lower_inclusive"))
        ) | (
            ma.col(f"__r{axis_index}_upper").eq(ma.col(f"__l{axis_index}_lower"))
            & (ma.col(f"__r{axis_index}_upper_inclusive")
               | ma.col(f"__l{axis_index}_lower_inclusive"))
        )
        face_conditions.append(touch)
    # A face is shared only when every other coordinate has a nonempty overlap.
    connected = ma.lit(False)
    for axis_index in range(len(ordered)):
        branch = face_conditions[axis_index]
        for other_index in range(len(ordered)):
            if other_index != axis_index:
                branch = branch & _interval_overlap(
                    str(other_index), str(other_index), "__l", "__r"
                )
        connected = connected | branch
    conditions.append(connected.fill_null(False))
    face_edges = cross.filter(*conditions).select(
        *[ma.col(name).alias(key) for name, key in zip(left_names[:len(keys)], keys)],
        ma.col("__lm").alias("contributor_set_id"),
        ma.col("__li").alias("left_id"), ma.col("__ri").alias("right_id"),
    ).unique(subset=[*keys, "contributor_set_id", "left_id", "right_id"])
    box_nodes = boxes.select(*keys, "contributor_set_id", "box_id")
    face_graph = face_edges.with_columns(
        ma.col("left_id").alias("box_id"),
        ma.col("right_id").alias("__neighbor"),
    ).select(*keys, "contributor_set_id", "box_id", "__neighbor")
    component_labels = _label_components(
        box_nodes, face_graph, keys=(*keys, "contributor_set_id"),
        node_field="box_id", neighbor_field="__neighbor", label_field="cell_id",
    )
    labeled_boxes = boxes.join(
        component_labels, on=[*keys, "contributor_set_id", "box_id"], how="inner"
    )
    cells = labeled_boxes.select(*keys, "cell_id", "contributor_set_id").unique(
        subset=[*keys, "cell_id", "contributor_set_id"]
    )
    cell_boxes = labeled_boxes.select(*keys, "cell_id", "box_id")
    members = membership.join(
        fragment_edges, on=[*keys, "fragment_id"], how="inner"
    ).select(*keys, "contributor_set_id", "source_uuid").unique(
        subset=[*keys, "contributor_set_id", "source_uuid"]
    )
    ordered_bounds = {}
    for index, dim in enumerate(ordered):
        ordered_bounds[dim.dimension_name] = labeled_boxes.with_columns(
            ma.when(ma.col(f"__g{index}_lower_unbounded")).then("unbounded")
            .otherwise("bounded").alias("lower_state"),
            ma.when(ma.col(f"__g{index}_upper_unbounded")).then("unbounded")
            .otherwise("bounded").alias("upper_state"),
            ma.when(ma.col(f"__g{index}_lower_unbounded")).then(ma.lit(None))
            .otherwise(ma.col(f"__g{index}_lower")).alias("lower_value"),
            ma.when(ma.col(f"__g{index}_upper_unbounded")).then(ma.lit(None))
            .otherwise(ma.col(f"__g{index}_upper")).alias("upper_value"),
        ).select(
            *keys, "cell_id", "box_id", "lower_state", "lower_value",
            ma.col(f"__g{index}_lower_inclusive").alias("lower_inclusive"),
            "upper_state", "upper_value",
            ma.col(f"__g{index}_upper_inclusive").alias("upper_inclusive"),
        )
    return OrderedGeometry(cells, cell_boxes, ordered_bounds, members)
