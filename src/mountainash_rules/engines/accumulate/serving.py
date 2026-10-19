"""Stored-region point matching and serving over retained accumulator tables."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence

import mountainash.expressions as ma
from mountainash.relations import Relation, concat


_KEYS = ("partition", "scope")


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
        result[field] = request_keys.join(
            values, on=list(_KEYS), how="inner", execute_on="right"
        ).select("request_id", "value").unique(
            subset=["request_id", "value"]
        )
    for field, values in lattice.cell_category_values.items():
        selected = values.select(*_KEYS, "value")
        per_requests = request_keys.join(
            selected, on=list(_KEYS), how="inner", execute_on="right"
        ).select("request_id", "value").unique(
            subset=["request_id", "value"]
        )
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
    from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

    point_keys = tuple(point_keys)
    key_columns = tuple(dict.fromkeys((*point_keys, *_KEYS)))
    point_rows = points.drop("scope") if "scope" in points.columns else points
    point_rows = point_rows.with_columns(ma.lit(scope).alias("scope"))
    cells = geometry.cells.select(*_KEYS, "cell_id", "contributor_set_id")
    boxes = geometry.cell_boxes.select(*_KEYS, "cell_id", "box_id")
    candidates = point_rows.join(
        boxes.join(
            cells, on=[*_KEYS, "cell_id"], how="inner", execute_on="right"
        ),
        on=list(_KEYS), how="inner", execute_on="right",
    )
    match_columns = tuple(candidates.columns)

    for field, bounds in geometry.cell_ordered_bounds.items():
        join_keys = (*_KEYS, "cell_id", "box_id")
        joined = candidates.join(
            bounds, on=list(join_keys), how="inner", execute_on="right"
        )
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
        expression = ma.when(concrete & lower & upper).then(1).otherwise(-1)
        scored = ExpressionRulesEngine._score_relation(
            joined, [field], {field: expression}
        )
        candidates = scored.filter(ma.col("__survived")).select(*match_columns)

    for field, sets in geometry.cell_category_sets.items():
        join_keys = (*_KEYS, "cell_id", "box_id")
        with_sets = candidates.join(
            sets, on=list(join_keys), how="inner", execute_on="right"
        )
        residual = ma.col(f"__point_residual_{field}")
        concrete = with_sets.with_columns(
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
            execute_on="right",
        )
        found = ma.col("__stored_value_found").fill_null(False)
        membership = (
            ma.when(
                ma.col("membership_kind").eq("finite") & found
            ).then(1)
            .when(
                ma.col("membership_kind").eq("cofinite") & ~found
            ).then(1)
            .otherwise(-1)
        )
        expression = (
            ma.when(residual.ge(0))
            .then(
                ma.when(ma.col("residual_class_included").fill_null(False))
                .then(0).otherwise(-1)
            )
            .otherwise(membership)
        )
        scored = ExpressionRulesEngine._score_relation(
            concrete, [field], {field: expression}
        )
        candidates = scored.filter(ma.col("__survived")).select(*match_columns)

    return candidates.select(*key_columns, "cell_id", "contributor_set_id").unique(
        subset=[*key_columns, "cell_id", "contributor_set_id"],
    )


