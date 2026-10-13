"""Request-targeted canonical geometry acceptance."""

import mountainash as ma

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from tests.conftest import build_backend_df


def _targeted_geometry(backend_name, rules_data, metadata, target_rows):
    from mountainash_rules.engines.accumulate.build import construct_targeted_geometry
    from mountainash_rules.engines.accumulate.sources import capture_sources

    rules = ma.relation(build_backend_df(backend_name, rules_data, "rules"))
    targets = ma.relation(build_backend_df(backend_name, target_rows, "targets"))
    return construct_targeted_geometry(capture_sources(rules, metadata), targets)


def _targets(*memberships):
    return {
        "partition": ["default"] * len(memberships),
        "scope": ["default"] * len(memberships),
        "target_id": [target for target, _ in memberships],
        "source_uuid": [source for _, source in memberships],
    }


def _range_metadata():
    return DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )


def test_targeted_geometry_keeps_authoritative_boundaries_for_a_only_membership(backend_name):
    geometry = _targeted_geometry(
        backend_name,
        {"uuid": ["A", "B"], "minimum": [0, 5], "maximum": [20, 10],
         "amount": [10, 5]},
        _range_metadata(),
        _targets(("only-a", "A")),
    )

    assert geometry.cells.columns == [
        "partition", "scope", "cell_id", "identity_version", "contributor_set_id",
    ]
    assert geometry.cells.count_rows() == 2
    assert geometry.cells.select("identity_version").unique().to_dicts() == [
        {"identity_version": 1}
    ]
    assert geometry.cells.select("contributor_set_id").unique().to_dicts() == [
        {"contributor_set_id": "only-a"}
    ]
    assert geometry.contributors.sort("source_uuid").to_dicts() == [
        {"partition": "default", "scope": "default",
         "contributor_set_id": "only-a", "source_uuid": "A"}
    ]
    bounds = geometry.cell_ordered_bounds["x"].sort("lower_value").to_dicts()
    assert [(row["lower_value"], row["upper_value"],
             row["lower_inclusive"], row["upper_inclusive"])
            for row in bounds] == [(0, 5, True, False), (10, 20, False, True)]


def test_targeted_geometry_keeps_a_and_b_membership_without_other_memberships(backend_name):
    geometry = _targeted_geometry(
        backend_name,
        {"uuid": ["A", "B"], "minimum": [0, 5], "maximum": [20, 10],
         "amount": [10, 5]},
        _range_metadata(),
        _targets(("only-a", "A"), ("a-and-b", "A"), ("a-and-b", "B")),
    )

    cells = geometry.cells.group_by("contributor_set_id").agg(
        ma.col("cell_id").count().alias("cell_count")
    ).sort("contributor_set_id").to_dicts()
    assert cells == [
        {"contributor_set_id": "a-and-b", "cell_count": 1},
        {"contributor_set_id": "only-a", "cell_count": 2},
    ]
    assert geometry.cells.select("cell_id").unique().count_rows() == 3
    assert geometry.contributors.sort("contributor_set_id", "source_uuid").to_dicts() == [
        {"partition": "default", "scope": "default",
         "contributor_set_id": "a-and-b", "source_uuid": "A"},
        {"partition": "default", "scope": "default",
         "contributor_set_id": "a-and-b", "source_uuid": "B"},
        {"partition": "default", "scope": "default",
         "contributor_set_id": "only-a", "source_uuid": "A"},
    ]
    assert geometry.cell_boxes.count_rows() == 3


def test_targeted_geometry_composes_mixed_ranges_and_categories(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name="code", data_type="str", match_strategy="exact"),
            Dimension(
                dimension_name="x", data_type="int", match_strategy="range",
                range_min_field="minimum", range_max_field="maximum",
            ),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    geometry = _targeted_geometry(
        backend_name,
        {"uuid": ["A", "B"], "code": ["north", "north"],
         "minimum": [0, 5], "maximum": [20, 10], "amount": [10, 5]},
        metadata,
        _targets(("only-a", "A")),
    )

    assert geometry.cells.count_rows() == 2
    assert set(geometry.cell_ordered_bounds) == {"x"}
    assert set(row["value"] for row in geometry.cell_category_values["code"].to_dicts()) == {
        "north",
    }


def test_targeted_geometry_retains_cofinite_category_residual(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="code", context_field="code", rule_field="excluded",
            data_type="str", match_strategy="not_equal",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    geometry = _targeted_geometry(
        backend_name,
        {"uuid": ["A"], "excluded": ["blocked"], "amount": [10]},
        metadata,
        _targets(("only-a", "A")),
    )

    assert geometry.cells.count_rows() == 1
    category_set = geometry.cell_category_sets["code"].to_dicts()[0]
    assert category_set["membership_kind"] == "cofinite"
    assert category_set["residual_class_included"] is True
    assert geometry.cell_category_values["code"].select("value").to_dicts() == [
        {"value": "blocked"}
    ]


def test_targeted_geometry_matches_equivalent_open_range_and_threshold_endpoints(backend_name):
    range_metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
            range_min_inclusive=False, range_max_inclusive=False,
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    threshold_metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name="above", context_field="x", rule_field="minimum",
                      data_type="int", match_strategy="greater_than"),
            Dimension(dimension_name="below", context_field="x", rule_field="maximum",
                      data_type="int", match_strategy="less_than"),
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    targets = _targets(("target", "A"))
    range_geometry = _targeted_geometry(
        backend_name,
        {"uuid": ["A"], "minimum": [5], "maximum": [10], "amount": [1]},
        range_metadata, targets,
    )
    threshold_geometry = _targeted_geometry(
        backend_name,
        {"uuid": ["A"], "minimum": [5], "maximum": [10], "amount": [1]},
        threshold_metadata, targets,
    )

    def semantic_bounds(geometry):
        return sorted(
            (row["lower_state"], row["lower_value"], row["lower_inclusive"],
             row["upper_state"], row["upper_value"], row["upper_inclusive"])
            for row in geometry.cell_ordered_bounds["x"].to_dicts()
        )

    assert range_geometry.cells.count_rows() == threshold_geometry.cells.count_rows() == 1
    assert semantic_bounds(range_geometry) == semantic_bounds(threshold_geometry)


def test_targeted_geometry_preserves_domain_only_unary_not_or_holes(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.core.scalar import encode_scalar

    def interval(lower, upper):
        record = {
            "schema_version": 1,
            "node": {
                "op": "interval", "field": "x",
                "lower": encode_scalar(lower, "int", allow_reserved=True),
                "upper": encode_scalar(upper, "int", allow_reserved=True),
                "lower_closed": True, "upper_closed": True,
            },
        }
        return content_id("predicate", record), record

    low_id, low = interval(0, 2)
    high_id, high = interval(8, 10)
    union = {"schema_version": 1, "node": {"op": "or", "args": sorted([low_id, high_id])}}
    union_id = content_id("predicate", union)
    root = {"schema_version": 1, "node": {"op": "not", "arg": union_id}}
    root_id = content_id("predicate", root)
    metadata = DimensionsMetadata(
        uuid_field="uuid", dimensions=[],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        domain=DomainDefinition(
            schema_version=1, domain_id="D", predicate_id=root_id,
            fields=[DomainField(name="x", data_type="int")],
        ),
        domain_nodes={low_id: low, high_id: high, union_id: union, root_id: root},
    )
    geometry = _targeted_geometry(
        backend_name,
        {"uuid": ["A"], "amount": [1]}, metadata,
        _targets(("only-a", "A")),
    )

    assert set(geometry.cell_ordered_bounds) == {"x"}
    assert geometry.cells.count_rows() == 3
    bounds = geometry.cell_ordered_bounds["x"].to_dicts()
    assert len(bounds) == 3
    assert all(row["lower_inclusive"] is False for row in bounds if row["lower_state"] == "bounded")
    assert all(row["upper_inclusive"] is False for row in bounds if row["upper_state"] == "bounded")
    assert sorted(
        row["upper_value"] for row in bounds if row["upper_state"] == "bounded"
    ) == [0, 8]
    assert sorted(
        row["lower_value"] for row in bounds if row["lower_state"] == "bounded"
    ) == [2, 10]
