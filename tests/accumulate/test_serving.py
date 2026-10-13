"""Stored serving matches persisted regions and folds without source fallback."""

from dataclasses import replace

import mountainash as ma
from mountainash import materialization_scope

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.engines.accumulate.build import build
from mountainash_rules.engines.accumulate.serving import point_results, stored_boundaries
from tests.conftest import build_backend_df


def _range_lattice(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "minimum": [0], "maximum": [10], "amount": [0],
    }, "rules"))
    return build(rules, metadata)


def _points(backend_name, field, values, residuals):
    return ma.relation(build_backend_df(backend_name, {
        "request_id": list(range(1, len(values) + 1)),
        "__completion_id": [0] * len(values),
        "partition": ["default"] * len(values),
        f"__point_value_{field}": values,
        f"__point_residual_{field}": residuals,
    }, "points"))


def test_published_reload_queries_stored_bounds_cells_and_zero_fold(backend_name, tmp_path):
    published = _range_lattice(backend_name).publish()
    destination = tmp_path / "published"
    published.save(destination)
    restored = type(published).load(destination)
    requests = ma.relation(build_backend_df(backend_name, {
        "request_id": [1], "partition": ["default"],
    }, "requests"))
    points = _points(backend_name, "x", [5], [-1])

    boundaries = stored_boundaries(restored, requests, "default")
    alternatives, cell_hits, contributor_hits = point_results(
        restored, points, "default", (0,), include_contributors=True,
    )

    assert restored.published is True
    assert sorted(row["value"] for row in boundaries["x"].to_dicts()) == [0, 10]
    assert alternatives.select("request_id", "__completion_id", "v0001").to_dicts() == [
        {"request_id": 1, "__completion_id": 0, "v0001": 0},
    ]
    assert alternatives.schema["v0001"] == restored.outputs["default"].schema["v0001"]
    assert cell_hits.count_rows() == 1
    assert contributor_hits.select("request_id", "source_uuid").to_dicts() == [
        {"request_id": 1, "source_uuid": "A"},
    ]


def test_partial_points_match_cofinite_residual_and_keep_no_match_typed_null(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="excluded", context_field="code", rule_field="blocked",
            data_type="str", match_strategy="not_equal",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "blocked": ["blocked"], "amount": [0],
    }, "rules"))
    lattice = build(rules, metadata)
    points = _points(
        backend_name, "code", [None, "blocked", "open"], [0, -1, -1],
    )

    alternatives, cell_hits, contributor_hits = point_results(
        lattice, points, "default", (0,), include_contributors=True,
    )

    assert alternatives.select("request_id", "v0001").sort("request_id").to_dicts() == [
        {"request_id": 1, "v0001": 0},
        {"request_id": 2, "v0001": None},
        {"request_id": 3, "v0001": 0},
    ]
    assert alternatives.schema["v0001"] == lattice.outputs["default"].schema["v0001"]
    assert cell_hits.select("request_id").unique().sort("request_id").to_dicts() == [
        {"request_id": 1}, {"request_id": 3},
    ]
    assert contributor_hits.select("request_id", "source_uuid").sort("request_id").to_dicts() == [
        {"request_id": 1, "source_uuid": "A"},
        {"request_id": 3, "source_uuid": "A"},
    ]


def test_aliases_share_stored_physical_axis_and_corrupted_fold_is_not_recomputed(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(
                dimension_name="left", context_field="code", rule_field="left_code",
                data_type="str", match_strategy="exact",
            ),
            Dimension(
                dimension_name="right", context_field="code", rule_field="right_code",
                data_type="str", match_strategy="exact",
            ),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "left_code": ["gold"], "right_code": ["gold"], "amount": [1],
    }, "rules"))
    lattice = build(rules, metadata)
    output_descriptor = next(item for item in lattice.table_descriptors if item.role == "outputs")
    dtype = lattice.outputs["default"].schema["v0001"]
    corrupted_output = lattice.outputs["default"].with_columns(
        ma.lit(777).cast(dtype).alias("v0001"),
    )
    with materialization_scope() as owner:
        corrupted_output = owner.detach(owner.capture(corrupted_output)[0])
    tables = dict(lattice.tables)
    tables[output_descriptor.table_id] = corrupted_output
    corrupted = replace(lattice, tables=tables)
    requests = ma.relation(build_backend_df(backend_name, {
        "request_id": [1], "partition": ["default"],
    }, "requests"))
    points = _points(backend_name, "code", ["gold"], [-1])

    boundaries = stored_boundaries(corrupted, requests, "default")
    alternatives, _, contributor_hits = point_results(
        corrupted, points, "default", (0,), include_contributors=True,
    )

    assert set(boundaries) == {"code"}
    assert boundaries["code"].select("value").to_dicts() == [{"value": "gold"}]
    assert alternatives.select("v0001").to_dicts() == [{"v0001": 777}]
    assert contributor_hits.select("source_uuid").to_dicts() == [{"source_uuid": "A"}]
