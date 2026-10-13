"""Native disjoint construction and reusable native contribution folds."""

import mountainash as ma
import pytest

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from tests.conftest import build_backend_df


def range_metadata():
    return DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )


def test_build_partitions_overlapping_ranges_and_folds_distinct_contributors(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15],
        "amount": [10, 5],
    }, "rules"))
    lattice = build(rules, range_metadata())

    assert lattice.cells.count_rows() == 3
    assert lattice.cells.select("identity_version").unique().to_dicts() == [
        {"identity_version": 1}
    ]
    memberships = lattice.cells.join(
        lattice.contributors,
        on=["partition", "scope", "contributor_set_id"],
    ).select("cell_id", "source_uuid").sort("cell_id", "source_uuid").to_dicts()
    assert sorted(sorted(row["source_uuid"] for row in memberships
                         if row["cell_id"] == cell["cell_id"])
                  for cell in lattice.cells.to_dicts()) == [["A"], ["A", "B"], ["B"]]
    assert sorted(row["v0001"] for row in lattice.outputs["default"].to_dicts()) == [5, 10, 15]
    assert len(lattice.cell_boxes.to_dicts()) == 3
    assert len(lattice.cell_ordered_bounds["x"].to_dicts()) == 3
    assert lattice.geometry_namespace is not None
    assert lattice.sources.rules.schema == rules.schema
    assert lattice.metadata.dimensions[0].dimension_name == "x"
    assert {descriptor.role for descriptor in lattice.table_descriptors} >= {
        "cells", "cell_boxes", "cell_ordered_bounds", "contributors",
        "outputs", "source_rules", "source_state", "source_payloads",
    }
    assert len(lattice.tables) == len(lattice.table_descriptors)


def test_build_normalizes_overlapping_two_axis_regions_as_compound_boxes(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name="x", data_type="int", match_strategy="range",
                      range_min_field="xmin", range_max_field="xmax"),
            Dimension(dimension_name="y", data_type="int", match_strategy="range",
                      range_min_field="ymin", range_max_field="ymax"),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "xmin": [0, 5], "xmax": [10, 15],
        "ymin": [0, 5], "ymax": [10, 15], "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 3
    assert len(lattice.cell_boxes.to_dicts()) == 5
    assert set(lattice.cell_ordered_bounds) == {"x", "y"}



def test_build_composes_three_range_axes_without_losing_aliases(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name=name, data_type="int", match_strategy="range",
                      range_min_field=f"{name}_min", range_max_field=f"{name}_max")
            for name in ("x", "y", "z")
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"],
        "x_min": [0, 5], "x_max": [10, 15],
        "y_min": [0, 5], "y_max": [10, 15],
        "z_min": [0, 5], "z_max": [10, 15],
        "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 3
    assert len(lattice.cell_boxes.to_dicts()) == 7
    assert set(lattice.cell_ordered_bounds) == {"x", "y", "z"}

def test_build_retains_categorical_regions_and_contributions(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="code", data_type="str",
                              match_strategy="exact")],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "code": ["a", "b"], "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 2
    assert set(lattice.cell_category_sets) == {"code"}
    assert len(lattice.cell_category_values["code"].to_dicts()) == 2
    assert sorted(row["v0001"] for row in lattice.outputs["default"].to_dicts()) == [5, 10]



def test_build_composes_mixed_range_and_exact_dimensions(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name="x", data_type="int", match_strategy="range",
                      range_min_field="minimum", range_max_field="maximum"),
            Dimension(dimension_name="code", data_type="str", match_strategy="exact"),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B", "C"], "minimum": [0, 5, 5], "maximum": [10, 15, 15],
        "code": ["a", "a", "b"], "amount": [10, 5, 7],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 4
    assert set(lattice.cell_ordered_bounds) == {"x"}
    assert set(lattice.cell_category_sets) == {"code"}
    assert len(lattice.cell_category_values["code"].to_dicts()) == 4


def test_build_applies_joint_domain_to_generated_cells(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.core.scalar import encode_scalar
    from mountainash_rules.engines.accumulate.build import build

    node = {
        "schema_version": 1,
        "node": {"op": "eq", "field": "code",
                 "value": encode_scalar("a", "str", allow_reserved=True)},
    }
    predicate_id = content_id("predicate", node)
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="code", data_type="str",
                              match_strategy="exact")],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        domain=DomainDefinition(
            schema_version=1, domain_id="D", predicate_id=predicate_id,
            fields=[DomainField(name="code", data_type="str")],
        ),
        domain_nodes={predicate_id: node},
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B", "C"], "code": ["a", "b", "c"], "amount": [10, 5, 1],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 1
    assert lattice.outputs["default"].to_dicts()[0]["v0001"] == 10


def test_build_discards_regions_infeasible_under_joint_order_domain(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.engines.accumulate.build import build

    node = {
        "schema_version": 1,
        "node": {"op": "compare", "left": "x", "relation": "lt", "right": "y"},
    }
    predicate_id = content_id("predicate", node)
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(
                dimension_name=field, data_type="int", match_strategy="range",
                range_min_field=f"{field}_min", range_max_field=f"{field}_max",
            )
            for field in ("x", "y")
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        domain=DomainDefinition(
            schema_version=1, domain_id="D", predicate_id=predicate_id,
            fields=[DomainField(name=field, data_type="int") for field in ("x", "y")],
        ),
        domain_nodes={predicate_id: node},
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["valid", "outside"], "x_min": [0, 2], "x_max": [0, 2],
        "y_min": [1, 1], "y_max": [1, 1], "amount": [7, 9],
    }))

    lattice = build(rules, metadata)

    assert lattice.contributors.select("source_uuid").to_dicts() == [
        {"source_uuid": "valid"},
    ]
    assert lattice.outputs["default"].select("v0001").to_dicts() == [{"v0001": 7}]


def test_build_composes_multiple_exact_fields_under_joint_domain(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.core.scalar import encode_scalar
    from mountainash_rules.engines.accumulate.build import build

    def equality(field, value):
        node = {
            "schema_version": 1,
            "node": {
                "op": "eq", "field": field,
                "value": encode_scalar(value, "str", allow_reserved=True),
            },
        }
        return content_id("predicate", node), node

    region_id, region_node = equality("region", "north")
    market_id, market_node = equality("market", "EU")
    root = {
        "schema_version": 1,
        "node": {"op": "and", "args": sorted([region_id, market_id])},
    }
    root_id = content_id("predicate", root)
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name="region", data_type="str", match_strategy="exact"),
            Dimension(dimension_name="market", data_type="str", match_strategy="exact"),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        domain=DomainDefinition(
            schema_version=1, domain_id="D", predicate_id=root_id,
            fields=[
                DomainField(name="market", data_type="str"),
                DomainField(name="region", data_type="str"),
            ],
        ),
        domain_nodes={region_id: region_node, market_id: market_node, root_id: root},
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B", "C"],
        "region": ["north", "south", "north"],
        "market": ["EU", "EU", "US"],
        "amount": [10, 5, 7],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 1
    assert lattice.outputs["default"].to_dicts()[0]["v0001"] == 10


def test_build_composes_not_equal_sources_as_cofinite_regions(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="excluded", context_field="region", rule_field="excluded",
            data_type="str", match_strategy="not_equal",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "excluded": ["north", "south"], "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 3
    assert sorted(row["v0001"] for row in lattice.outputs["default"].to_dicts()) == [5, 10, 15]


def test_build_intersects_set_membership_and_exclusion_aliases(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(
                dimension_name="included", context_field="region", rule_field="members",
                data_type="str", match_strategy="set_membership",
            ),
            Dimension(
                dimension_name="excluded", context_field="region", rule_field="excluded",
                data_type="str", match_strategy="set_exclusion",
            ),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B", "C"],
        "members": [["north", "south"], ["north", "west"], ["south"]],
        "excluded": [["south"], ["west"], ["north"]],
        "amount": [10, 5, 3],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 2
    assert sorted(row["v0001"] for row in lattice.outputs["default"].to_dicts()) == [3, 15]


def test_build_composes_strict_discrete_threshold_aliases(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(
                dimension_name="above", context_field="x", rule_field="minimum",
                data_type="int", match_strategy="greater_than",
            ),
            Dimension(
                dimension_name="below", context_field="x", rule_field="maximum",
                data_type="int", match_strategy="less_than",
            ),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15],
        "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 3
    assert sorted(row["v0001"] for row in lattice.outputs["default"].to_dicts()) == [5, 10, 15]


def test_build_keeps_exact_key_categories(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name="key", data_type="str", match_strategy="exact_key"),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "key": ["north", "south"], "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 2
    assert sorted(row["v0001"] for row in lattice.outputs["default"].to_dicts()) == [5, 10]


def test_build_keeps_disconnected_regions_with_the_same_membership(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B", "C"], "minimum": [0, 2, 7], "maximum": [10, 3, 8],
        "amount": [10, 5, 7],
    }, "rules"))
    lattice = build(rules, range_metadata())

    assert lattice.cells.count_rows() == 5
    memberships = lattice.cells.join(
        lattice.contributors,
        on=["partition", "scope", "contributor_set_id"],
    )
    assert memberships.filter(ma.col("source_uuid").eq("A")).count_rows() == 5
    a_only = memberships.filter(ma.col("source_uuid").eq("A")).group_by(
        "contributor_set_id"
    ).agg(ma.col("source_uuid").count().alias("members"))
    assert a_only.filter(ma.col("members").eq(3)).count_rows() == 1


def test_build_preserves_datetime_bounds_and_extremum_payloads(backend_name):
    from datetime import datetime

    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="when", data_type="datetime", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[
            Aggregate(column_name="created", output_name="created_min",
                      data_type="datetime", operation="min", timezone="naive"),
            Aggregate(column_name="created", output_name="created_max",
                      data_type="datetime", operation="max", timezone="naive"),
        ],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"],
        "minimum": [datetime(2026, 1, 1), datetime(2026, 1, 2)],
        "maximum": [datetime(2026, 1, 4), datetime(2026, 1, 5)],
        "created": [datetime(2025, 12, 31), datetime(2026, 1, 1)],
    }, "rules"))

    lattice = build(rules, metadata)
    bounds = lattice.cell_ordered_bounds["when"].to_dicts()
    assert all(isinstance(row["lower_value"], datetime) for row in bounds)
    assert all(isinstance(row["upper_value"], datetime) for row in bounds)
    assert sorted(row["v0001"] for row in lattice.outputs["default"].to_dicts()) == [
        datetime(2025, 12, 31), datetime(2025, 12, 31), datetime(2026, 1, 1)
    ]
    assert sorted(row["v0002"] for row in lattice.outputs["default"].to_dicts()) == [
        datetime(2025, 12, 31), datetime(2026, 1, 1), datetime(2026, 1, 1)
    ]



@pytest.mark.parametrize("convert_output", [False, True])
def test_build_retains_decimal_bounds_and_rounds_sum_after_folding(backend_name, convert_output):
    from decimal import Decimal

    from mountainash import DecimalDtype
    from mountainash_rules.engines.accumulate.build import build
    from mountainash_rules.engines.accumulate.validate import validate

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="decimal",
            numeric_dtype=DecimalDtype(10, 3), rounding="half_to_even",
            match_strategy="range", range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(
            column_name="amount", data_type="decimal",
            numeric_dtype=DecimalDtype(10, 3),
            output_numeric_dtype=DecimalDtype(10, 2) if convert_output else None,
            output_rounding="half_to_even" if convert_output else None,
        )],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"],
        "minimum": [Decimal("0.004"), Decimal("0.006")],
        "maximum": [Decimal("0.008"), Decimal("0.010")],
        "amount": [Decimal("0.004"), Decimal("0.004")],
    }, "rules"))

    lattice = build(rules, metadata)
    bounds = lattice.cell_ordered_bounds["x"].to_dicts()
    assert all(isinstance(row["lower_value"], Decimal) for row in bounds)
    assert all(isinstance(row["upper_value"], Decimal) for row in bounds)
    assert (Decimal("0.01") if convert_output else Decimal("0.008")) in {
        row["v0001"] for row in lattice.outputs["default"].to_dicts()
    }
    assert validate(lattice, rules).findings.to_dicts() == []


def test_zero_field_build_has_one_real_contributor_cell(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid", dimensions=[],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 1
    assert lattice.contributors.count_rows() == 2
    assert lattice.outputs["default"].to_dicts()[0]["v0001"] == 15


def test_build_retains_a_closed_singleton_interval(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["only"], "minimum": [5], "maximum": [5], "amount": [4],
    }, "rules"))
    lattice = build(rules, range_metadata())
    bounds = lattice.cell_ordered_bounds["x"].to_dicts()

    assert lattice.cells.count_rows() == 1
    assert bounds[0]["lower_value"] == bounds[0]["upper_value"] == 5
    assert bounds[0]["lower_inclusive"] is True
    assert bounds[0]["upper_inclusive"] is True


def test_build_materializes_all_unbounded_sources_without_boundary_points(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
            range_min_unbounded_field="lo_open", range_max_unbounded_field="hi_open",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 1], "maximum": [10, 11],
        "lo_open": [True, True], "hi_open": [True, True], "amount": [10, 5],
    }, "rules"))

    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 1
    assert lattice.contributors.count_rows() == 2
    assert lattice.outputs["default"].to_dicts()[0]["v0001"] == 15


def test_payload_recomputation_uses_complete_candidate_membership_and_keeps_base(backend_name):
    from mountainash_rules.engines.accumulate.build import build
    from mountainash_rules.engines.accumulate.sources import capture_sources

    original = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15],
        "amount": [10, 5],
    }, "rules"))
    candidate = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15],
        "amount": [20, 5],
    }, "candidate"))
    base = build(original, range_metadata())

    revised = base.recompute_contributions(capture_sources(candidate, range_metadata()))
    assert sorted(row["v0001"] for row in base.outputs["default"].to_dicts()) == [5, 10, 15]
    assert sorted(row["v0001"] for row in revised.outputs["default"].to_dicts()) == [5, 20, 25]
    assert revised.geometry_namespace == base.geometry_namespace
    assert revised.cells.to_dicts() == base.cells.to_dicts()
    payload_descriptor = next(
        descriptor for descriptor in revised.table_descriptors
        if descriptor.role == "source_payloads" and descriptor.scope == "default"
    )
    assert sorted(
        row["a0001"] for row in revised.tables[payload_descriptor.table_id].to_dicts()
    ) == [5, 20]



def test_build_outputs_escape_a_closed_parent_scope(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "minimum": [0], "maximum": [10], "amount": [10],
    }, "rules"))
    with ma.materialization_scope():
        lattice = build(rules, range_metadata())

    assert lattice.cells.count_rows() == 1
    assert lattice.outputs["default"].to_dicts()[0]["v0001"] == 10


def test_failed_candidate_recomputation_leaves_base_snapshot_usable(backend_name):
    import pytest

    from mountainash_rules.engines.accumulate.build import build
    from mountainash_rules.engines.accumulate.sources import SourceBundle, capture_sources

    metadata = range_metadata()
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "minimum": [0], "maximum": [10], "amount": [10],
    }, "rules"))
    base = build(rules, metadata)
    candidate = capture_sources(rules, metadata)
    missing_scope = SourceBundle(
        rules=candidate.rules,
        states=candidate.states,
        payloads={},
        identities=candidate.identities,
        _metadata_json=candidate._metadata_json,
    )

    with pytest.raises(KeyError):
        base.recompute_contributions(missing_scope)
    assert base.outputs["default"].to_dicts()[0]["v0001"] == 10

def test_native_fold_helper_uses_full_distinct_groups_without_empty_seed(backend_name):
    from mountainash_rules.engines.accumulate.folds import fold_contributions

    payload = ma.relation(build_backend_df(backend_name, {
        "partition": ["default", "default", "default"],
        "scope": ["default", "default", "default"],
        "contributor_set_id": ["m1", "m1", "m2"], "a0001": [10, 10, 0],
    }, "payload"))
    folded = fold_contributions(
        payload, [Aggregate(column_name="amount", data_type="int", operation=AggregateOp.SUM)],
        keys=["partition", "scope", "contributor_set_id"],
    )

    assert folded.sort("contributor_set_id").to_dicts() == [
        {"partition": "default", "scope": "default", "contributor_set_id": "m1", "v0001": 20},
        {"partition": "default", "scope": "default", "contributor_set_id": "m2", "v0001": 0},
    ]
