"""Stored serving matches persisted regions and folds without source fallback."""

from dataclasses import replace

import mountainash as ma
from mountainash import materialization_scope

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.engines.accumulate.build import build
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




def test_published_reload_queries_stored_bounds_cells_and_zero_fold(backend_name, tmp_path):
    published = _range_lattice(backend_name).publish()
    destination = tmp_path / "published"
    published.save(destination)
    restored = type(published).load(destination)
    result = restored.query([
        {"request_id": 1, "context": {"x": 5}},
        {"request_id": 2, "context": {"x": 11}},
    ], include_contributors=True, include_cell_key=True).fetch()

    assert restored.published is True
    assert result.scope("default").select("request_id", "v0001").to_dicts() == [
        {"request_id": 1, "v0001": 0},
    ]
    assert result.scope("default").collect_schema()["v0001"] == ma.MountainashDtype.I64
    assert result.contributors.select("request_id", "source_uuid").to_dicts() == [
        {"request_id": 1, "source_uuid": "A"},
    ]
    assert result.cell_identity_ordered_bounds["x"].select("lower_value", "upper_value").to_dicts() == [
        {"lower_value": 0, "upper_value": 10},
    ]


def test_partial_query_preserves_cofinite_residual_without_synthetic_match(backend_name):
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
    result = lattice.query([
        {"request_id": 1, "context": {}},
        {"request_id": 2, "context": {"code": "blocked"}},
        {"request_id": 3, "context": {"code": "open"}},
    ], include_contributors=True).fetch()

    assert result.scope("default").select("request_id", "v0001").sort("request_id").to_dicts() == [
        {"request_id": 1, "v0001": 0},
        {"request_id": 3, "v0001": 0},
    ]
    assert result.contributors.select("request_id", "source_uuid").sort("request_id").to_dicts() == [
        {"request_id": 1, "source_uuid": "A"}, {"request_id": 3, "source_uuid": "A"},
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
    result = corrupted.query([
        {"request_id": 1, "context": {"code": "gold"}},
    ], include_contributors=True).fetch()

    assert result.scope("default").select("v0001").to_dicts() == [{"v0001": 777}]
    assert result.contributors.select("source_uuid").to_dicts() == [{"source_uuid": "A"}]
