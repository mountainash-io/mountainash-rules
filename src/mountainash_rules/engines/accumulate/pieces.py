"""Backend-native elementary pieces for ordered predicate refinement."""

from __future__ import annotations

import typing as t

import mountainash.expressions as ma
from mountainash.relations import Relation, concat


def _source_projection(
    states: Relation,
    *,
    keys: tuple[str, ...],
    source_uuid_field: str,
    lower_field: str,
    upper_field: str,
    lower_unbounded_field: str,
    upper_unbounded_field: str,
    lower_inclusive_field: str | None = None,
    upper_inclusive_field: str | None = None,
) -> Relation:
    expressions = [
        *keys, ma.col(source_uuid_field).alias("source_uuid"),
        ma.col(lower_field).alias("__source_lower"),
        ma.col(upper_field).alias("__source_upper"),
        ma.col(lower_unbounded_field).alias("__source_lower_unbounded"),
        ma.col(upper_unbounded_field).alias("__source_upper_unbounded"),
    ]
    if lower_inclusive_field is not None:
        expressions.append(
            ma.col(lower_inclusive_field).alias("__source_lower_inclusive")
        )
    if upper_inclusive_field is not None:
        expressions.append(
            ma.col(upper_inclusive_field).alias("__source_upper_inclusive")
        )
    return states.select(*expressions)


def ordered_pieces(
    states: Relation,
    *,
    keys: t.Sequence[str],
    lower_field: str = "lower",
    upper_field: str = "upper",
    lower_unbounded_field: str = "lower_unbounded",
    upper_unbounded_field: str = "upper_unbounded",
    boundaries: Relation | None = None,
    lower_inclusive: bool = True,
    upper_inclusive: bool = True,
) -> Relation:
    """Create exact adjacent bands, covered endpoint singletons and universal regions.

    Endpoint boundaries and source memberships stay in native relations. There is
    no host boundary list, midpoint, epsilon, or successor arithmetic. Explicit
    ``keys`` let callers refine a request-local relation as well as a whole K.
    """
    keys = tuple(keys)
    required = {*keys, lower_field, upper_field,
                lower_unbounded_field, upper_unbounded_field}
    missing = sorted(required - set(states.columns))
    if missing:
        raise ValueError(f"Missing ordered-state columns: {', '.join(missing)}")

    lows = states.filter(~ma.col(lower_unbounded_field)).select(
        *keys, ma.col(lower_field).alias("__point")
    )
    highs = states.filter(~ma.col(upper_unbounded_field)).select(
        *keys, ma.col(upper_field).alias("__point")
    )
    point_relations = [lows, highs]
    if boundaries is not None:
        point_relations.append(boundaries.select(*keys, "__point"))
    points = concat(point_relations, distinct=True)
    left = points.select(*keys, ma.col("__point").alias("lower"))
    right = points.select(*keys, ma.col("__point").alias("upper"))
    pairs = left.join(right, how="cross").filter(
        *[ma.col(key).eq(ma.col(f"{key}_right")) for key in keys],
        ma.col("lower").lt(ma.col("upper")),
    ).select(*keys, "lower", "upper")

    middle = points.select(*keys, ma.col("__point").alias("__middle"))
    blocked = pairs.join(middle, how="cross").filter(
        *[ma.col(key).eq(ma.col(f"{key}_right")) for key in keys],
        ma.col("lower").lt(ma.col("__middle")),
        ma.col("__middle").lt(ma.col("upper")),
    ).select(*keys, "lower", "upper").unique(subset=[*keys, "lower", "upper"])
    bands = pairs.join(blocked, on=[*keys, "lower", "upper"], how="anti")

    lower_closure_field = (
        "lower_inclusive" if "lower_inclusive" in states.columns else None
    )
    upper_closure_field = (
        "upper_inclusive" if "upper_inclusive" in states.columns else None
    )
    projected = _source_projection(
        states, keys=keys, source_uuid_field="source_uuid", lower_field=lower_field,
        upper_field=upper_field, lower_unbounded_field=lower_unbounded_field,
        upper_unbounded_field=upper_unbounded_field,
        lower_inclusive_field=lower_closure_field,
        upper_inclusive_field=upper_closure_field,
    )
    source_lower_closed = (
        ma.col("__source_lower_inclusive")
        if lower_closure_field is not None else ma.lit(lower_inclusive)
    )
    source_upper_closed = (
        ma.col("__source_upper_inclusive")
        if upper_closure_field is not None else ma.lit(upper_inclusive)
    )
    pair_states = bands.join(projected, on=list(keys), how="inner")
    lower_bad = (
        (~ma.col("__source_upper_unbounded")
         & ma.col("__source_upper").eq(ma.col("lower")))
        | (~ma.col("__source_lower_unbounded")
           & ma.col("__source_lower").eq(ma.col("lower"))
           & ~source_lower_closed)
    ).fill_null(False)
    upper_bad = (
        (~ma.col("__source_lower_unbounded")
         & ma.col("__source_lower").eq(ma.col("upper"))
         & source_lower_closed)
        | (~ma.col("__source_upper_unbounded")
           & ma.col("__source_upper").eq(ma.col("upper"))
           & ~source_upper_closed)
    ).fill_null(False)
    closure = pair_states.group_by(*keys, "lower", "upper").agg(
        lower_bad.any().alias("__lower_bad"), upper_bad.any().alias("__upper_bad")
    )
    band_geometry = closure.select(
        *keys, "lower", "upper",
        (~ma.col("__lower_bad")).alias("lower_inclusive"),
        (~ma.col("__upper_bad")).alias("upper_inclusive"),
        ma.lit(False).alias("lower_unbounded"),
        ma.lit(False).alias("upper_unbounded"),
    ).with_row_index(name="piece_id")
    boundary_pairs = points.join(
        points.rename({"__point": "__other_point"}), how="cross"
    )
    same_group = [
        ma.col(key).eq(ma.col(f"{key}_right")) for key in keys
    ]
    earlier = boundary_pairs.filter(
        *same_group, ma.col("__other_point").lt(ma.col("__point"))
    ).select(*keys, "__point").unique(subset=[*keys, "__point"])
    later = boundary_pairs.filter(
        *same_group, ma.col("__other_point").gt(ma.col("__point"))
    ).select(*keys, "__point").unique(subset=[*keys, "__point"])
    minimums = points.join(earlier, on=[*keys, "__point"], how="anti")
    maximums = points.join(later, on=[*keys, "__point"], how="anti")
    lower_rays = minimums.select(
        *keys, ma.col("__point").alias("lower"), ma.col("__point").alias("upper"),
        ma.lit(False).alias("lower_inclusive"), ma.lit(False).alias("upper_inclusive"),
        ma.lit(True).alias("lower_unbounded"), ma.lit(False).alias("upper_unbounded"),
    )
    upper_rays = maximums.select(
        *keys, ma.col("__point").alias("lower"), ma.col("__point").alias("upper"),
        ma.lit(False).alias("lower_inclusive"), ma.lit(False).alias("upper_inclusive"),
        ma.lit(False).alias("lower_unbounded"), ma.lit(True).alias("upper_unbounded"),
    )
    band_regions = concat([
        band_geometry.drop("piece_id"), lower_rays, upper_rays,
    ]).with_row_index(name="piece_id")

    point_states = points.join(projected, on=list(keys), how="inner")
    point_matches = (
        (ma.col("__source_lower_unbounded")
         | ma.col("__source_lower").lt(ma.col("__point"))
         | (ma.col("__source_lower").eq(ma.col("__point")) & source_lower_closed))
        & (ma.col("__source_upper_unbounded")
           | ma.col("__source_upper").gt(ma.col("__point"))
           | (ma.col("__source_upper").eq(ma.col("__point")) & source_upper_closed))
    ).fill_null(False)
    active_points = point_states.filter(point_matches).select(
        *keys, "__point"
    ).unique(subset=[*keys, "__point"])
    band_coverage = piece_coverage(
        band_regions, states, keys=keys, source_uuid_field="source_uuid",
        lower_field=lower_field, upper_field=upper_field,
        lower_unbounded_field=lower_unbounded_field,
        upper_unbounded_field=upper_unbounded_field,
        lower_inclusive=lower_inclusive, upper_inclusive=upper_inclusive,
    )
    covered_geometry = band_coverage.select(*keys, "piece_id").join(
        band_regions.select(
            *keys, "piece_id", "lower", "upper",
            "lower_inclusive", "upper_inclusive", "lower_unbounded", "upper_unbounded",
        ),
        on=[*keys, "piece_id"], how="inner",
    )
    adjacent_points = concat([
        covered_geometry.filter(
            ma.col("lower_inclusive") & ~ma.col("lower_unbounded")
        ).select(*keys, ma.col("lower").alias("__point")),
        covered_geometry.filter(
            ma.col("upper_inclusive") & ~ma.col("upper_unbounded")
        ).select(*keys, ma.col("upper").alias("__point")),
    ], distinct=True)
    singleton_points = active_points.join(
        adjacent_points, on=[*keys, "__point"], how="anti"
    )
    singletons = singleton_points.select(
        *keys, ma.col("__point").alias("lower"),
        ma.col("__point").alias("upper"), ma.lit(True).alias("lower_inclusive"),
        ma.lit(True).alias("upper_inclusive"), ma.lit(False).alias("lower_unbounded"),
        ma.lit(False).alias("upper_unbounded"),
    )

    universal_states = states.group_by(*keys).agg(
        ma.col(lower_field).min().alias("lower"),
        ma.col(upper_field).max().alias("upper"),
        ma.col(lower_unbounded_field).all().alias("__all_lower_unbounded"),
        ma.col(upper_unbounded_field).all().alias("__all_upper_unbounded"),
    )
    universals = universal_states.filter(
        ma.col("__all_lower_unbounded") & ma.col("__all_upper_unbounded")
    ).select(*keys, "lower", "upper").with_columns(
        ma.lit(True).alias("lower_inclusive"), ma.lit(True).alias("upper_inclusive"),
        ma.lit(True).alias("lower_unbounded"), ma.lit(True).alias("upper_unbounded"),
    )
    return concat([band_regions.drop("piece_id"), singletons, universals]).with_row_index(
        name="piece_id"
    )


def piece_coverage(
    pieces: Relation,
    states: Relation,
    *,
    keys: t.Sequence[str],
    source_uuid_field: str = "source_uuid",
    lower_field: str = "lower",
    upper_field: str = "upper",
    lower_unbounded_field: str = "lower_unbounded",
    upper_unbounded_field: str = "upper_unbounded",
    lower_inclusive: bool = True,
    upper_inclusive: bool = True,
) -> Relation:
    """Return exact source-containment edges for elementary ordered regions."""
    keys = tuple(keys)
    source = _source_projection(
        states, keys=keys, source_uuid_field=source_uuid_field,
        lower_field=lower_field, upper_field=upper_field,
        lower_unbounded_field=lower_unbounded_field,
        upper_unbounded_field=upper_unbounded_field,
        lower_inclusive_field=(
            "lower_inclusive" if "lower_inclusive" in states.columns else None
        ),
        upper_inclusive_field=(
            "upper_inclusive" if "upper_inclusive" in states.columns else None
        ),
    )
    source_lower_closed = (
        ma.col("__source_lower_inclusive")
        if "lower_inclusive" in states.columns else ma.lit(lower_inclusive)
    )
    source_upper_closed = (
        ma.col("__source_upper_inclusive")
        if "upper_inclusive" in states.columns else ma.lit(upper_inclusive)
    )
    joined = pieces.join(source, on=list(keys), how="inner")
    lower_covered = ma.when(ma.col("lower_unbounded")).then(
        ma.col("__source_lower_unbounded")
    ).otherwise(
        ma.col("__source_lower_unbounded")
        | ma.col("__source_lower").lt(ma.col("lower"))
        | (ma.col("__source_lower").eq(ma.col("lower"))
           & (~ma.col("lower_inclusive") | source_lower_closed))
    )
    upper_covered = ma.when(ma.col("upper_unbounded")).then(
        ma.col("__source_upper_unbounded")
    ).otherwise(
        ma.col("__source_upper_unbounded")
        | ma.col("__source_upper").gt(ma.col("upper"))
        | (ma.col("__source_upper").eq(ma.col("upper"))
           & (~ma.col("upper_inclusive") | source_upper_closed))
    )
    return joined.filter((lower_covered & upper_covered).fill_null(False)).select(
        *keys, "piece_id", "lower", "upper", "source_uuid"
    )
