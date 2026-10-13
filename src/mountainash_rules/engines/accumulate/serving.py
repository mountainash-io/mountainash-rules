"""Stored-region point matching and serving over retained accumulator tables."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence

import mountainash.expressions as ma
from mountainash.relations import Relation, concat

from mountainash_rules.engines.accumulate.lattice import Lattice

_KEYS = ("partition", "scope")
_DEFAULT_POINT_KEYS = ("request_id", "__completion_id")


class _StoredGeometry(Protocol):
    @property
    def cells(self) -> Relation: ...

    @property
    def cell_boxes(self) -> Relation: ...

    @property
    def contributors(self) -> Relation: ...

    @property
    def cell_ordered_bounds(self) -> Mapping[str, Relation]: ...

    @property
    def cell_category_sets(self) -> Mapping[str, Relation]: ...

    @property
    def cell_category_values(self) -> Mapping[str, Relation]: ...


def stored_boundaries(
    lattice: _StoredGeometry, requests: Relation, scope: str,
) -> dict[str, Relation]:
    """Return finite values that delimit stored regions for these requests."""
    request_keys = requests.select("request_id", "partition").with_columns(
        ma.lit(scope).alias("scope"),
    )
    result: dict[str, Relation] = {}
    for field, bounds in lattice.cell_ordered_bounds.items():
        lower = bounds.filter(ma.col("lower_state").eq("bounded")).select(
            *_KEYS, ma.col("lower_value").alias("value"),
        )
        upper = bounds.filter(ma.col("upper_state").eq("bounded")).select(
            *_KEYS, ma.col("upper_value").alias("value"),
        )
        values = concat((lower, upper), distinct=True)
        result[field] = request_keys.join(values, on=list(_KEYS), how="inner").select(
            "request_id", "value",
        ).unique(subset=["request_id", "value"])
    for field, values in lattice.cell_category_values.items():
        selected = values.select(*_KEYS, "value")
        per_requests = request_keys.join(selected, on=list(_KEYS), how="inner").select(
            "request_id", "value",
        ).unique(subset=["request_id", "value"])
        if field in result:
            result[field] = concat((result[field], per_requests), distinct=True)
        else:
            result[field] = per_requests
    return result


def _matching_cells(
    geometry: _StoredGeometry, points: Relation, scope: str,
    point_keys: Sequence[str],
) -> Relation:
    """Match points against all stored joint regions, without outputs or sources."""
    point_keys = tuple(point_keys)
    key_columns = tuple(dict.fromkeys((*point_keys, *_KEYS)))
    point_rows = points.drop("scope") if "scope" in points.columns else points
    point_rows = point_rows.with_columns(ma.lit(scope).alias("scope"))
    cells = geometry.cells.select(*_KEYS, "cell_id", "contributor_set_id")
    boxes = geometry.cell_boxes.select(*_KEYS, "cell_id", "box_id")
    candidates = point_rows.join(
        boxes.join(cells, on=[*_KEYS, "cell_id"], how="inner"),
        on=list(_KEYS), how="inner",
    )
    match_columns = tuple(candidates.columns)

    for field, bounds in geometry.cell_ordered_bounds.items():
        join_keys = (*_KEYS, "cell_id", "box_id")
        joined = candidates.join(bounds, on=list(join_keys), how="inner")
        value = ma.col(f"__point_value_{field}")
        concrete = ma.col(f"__point_residual_{field}").eq(-1)
        lower = (
            ma.col("lower_state").eq("unbounded")
            | value.gt(ma.col("lower_value"))
            | (value.eq(ma.col("lower_value")) & ma.col("lower_inclusive"))
        )
        upper = (
            ma.col("upper_state").eq("unbounded")
            | value.lt(ma.col("upper_value"))
            | (value.eq(ma.col("upper_value")) & ma.col("upper_inclusive"))
        )
        candidates = joined.filter(
            concrete & lower & upper,
        ).select(*match_columns)

    for field, sets in geometry.cell_category_sets.items():
        join_keys = (*_KEYS, "cell_id", "box_id")
        with_sets = candidates.join(sets, on=list(join_keys), how="inner")
        residual = ma.col(f"__point_residual_{field}")
        symbolic = with_sets.filter(
            residual.ge(0) & ma.col("residual_class_included").fill_null(False),
        ).select(*match_columns)
        concrete = with_sets.filter(residual.eq(-1)).with_columns(
            ma.col(f"__point_value_{field}").alias("__stored_point_value"),
        )
        members = geometry.cell_category_values[field].select(
            *join_keys, ma.col("value").alias("__stored_region_value"),
        ).unique(subset=[*join_keys, "__stored_region_value"]).with_columns(
            ma.lit(True).alias("__stored_value_found"),
        )
        concrete = concrete.join(
            members,
            left_on=[*join_keys, "__stored_point_value"],
            right_on=[*join_keys, "__stored_region_value"],
            how="left",
        )
        found = ma.col("__stored_value_found").fill_null(False)
        concrete = concrete.filter(
            (ma.col("membership_kind").eq("finite") & found)
            | (ma.col("membership_kind").eq("cofinite") & ~found)
        ).select(*match_columns)
        candidates = concat((symbolic, concrete), distinct=True)

    return candidates.select(*key_columns, "cell_id", "contributor_set_id").unique(
        subset=[*key_columns, "cell_id", "contributor_set_id"],
    )


def point_results(
    lattice: Lattice, points: Relation, scope: str,
    selected_outputs: Sequence[int], *,
    include_contributors: bool,
    point_keys: Sequence[str] = _DEFAULT_POINT_KEYS,
) -> tuple[Relation, Relation, Relation | None]:
    """Read stored output alternatives and requested cell/contributor witnesses."""
    point_keys = tuple(point_keys)
    matches = _matching_cells(lattice, points, scope, point_keys)
    key_columns = tuple(dict.fromkeys((*point_keys, *_KEYS)))
    cell_hits = matches.select(
        *key_columns, "cell_id", "contributor_set_id",
    ).unique(subset=[*key_columns, "cell_id", "contributor_set_id"])
    contributor_hits = None
    if include_contributors:
        contributor_hits = matches.select(
            *key_columns, "contributor_set_id",
        ).join(
            lattice.contributors.select(*_KEYS, "contributor_set_id", "source_uuid"),
            on=[*_KEYS, "contributor_set_id"], how="inner",
        ).select(*key_columns, "source_uuid").unique(
            subset=[*key_columns, "source_uuid"],
        )

    outputs = lattice.outputs[scope]
    output_names = tuple(f"v{position + 1:04d}" for position in selected_outputs)
    missing = matches.select(*_KEYS, "contributor_set_id").unique(
        subset=[*_KEYS, "contributor_set_id"],
    ).join(
        outputs.select(*_KEYS, "contributor_set_id").unique(
            subset=[*_KEYS, "contributor_set_id"],
        ),
        on=[*_KEYS, "contributor_set_id"], how="anti",
    )
    if missing.count_rows():
        raise ValueError("stored output is missing for a matched contributor set")
    if any(name not in outputs.columns for name in output_names):
        raise ValueError("selected stored output column is absent")
    matched_outputs = matches.select(*key_columns, "contributor_set_id").join(
        outputs.select(*_KEYS, "contributor_set_id", *output_names),
        on=[*_KEYS, "contributor_set_id"], how="inner",
    ).select(*key_columns, *output_names)
    point_rows = points.drop("scope") if "scope" in points.columns else points
    point_rows = point_rows.with_columns(ma.lit(scope).alias("scope")).select(*key_columns)
    alternatives = point_rows.join(
        matched_outputs, on=list(key_columns), how="left",
    )
    return alternatives, cell_hits, contributor_hits
