"""Explicit source assurance preserves qualified identities and exact predicates."""

import mountainash as ma
import pytest

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from tests.conftest import build_backend_df


def range_metadata(*, upper_closed=True, partition=None):
    return DimensionsMetadata(
        uuid_field="uuid", partition_field=partition,
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
            range_max_inclusive=upper_closed,
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )


def kinds(report):
    return report.findings.select("kind", "severity").sort("kind").to_dicts()


@pytest.mark.parametrize("closed", [False, True])
def test_touching_sources_report_only_an_included_boundary(backend_name, closed):
    from mountainash_rules.engines.accumulate.sources import check_sources

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 10], "maximum": [10, 20], "amount": [5, 7],
    }))
    report = check_sources(rules, range_metadata(upper_closed=closed))
    assert kinds(report) == ([{"kind": "singleton_boundary_overlap", "severity": "warning"}] if closed else [])
    if closed:
        assert report.subjects.select("source_uuid").sort("source_uuid").to_dicts() == [
            {"source_uuid": "A"}, {"source_uuid": "B"},
        ]
        assert report.witnesses["x"].select("value").to_dicts() == [{"value": 10}]


def test_duplicate_uuid_is_scoped_to_partition(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "A"], "market": ["EU", "US"],
        "minimum": [0, 0], "maximum": [10, 10], "amount": [5, 7],
    }))
    metadata = range_metadata(partition="market")
    assert kinds(check_sources(rules, metadata)) == []
    duplicate = rules.with_columns(ma.lit("EU").alias("market"))
    report = check_sources(duplicate, metadata)
    assert {row["kind"] for row in kinds(report) if row["severity"] == "error"} == {"duplicate_identity"}


def test_shared_field_conjunction_finds_discrete_contradiction_not_marginal_overlap(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(dimension_name="gt", context_field="x", rule_field="minimum", match_strategy="greater_than", data_type="int", rule_wildcard_field="no_min"),
        Dimension(dimension_name="lt", context_field="x", rule_field="maximum", match_strategy="less_than", data_type="int", rule_wildcard_field="no_max"),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B", "empty"], "minimum": [8, 0, 0], "maximum": [10, 5, 1],
        "no_min": [False, False, False], "no_max": [False, False, False], "amount": [5, 7, 0],
    }))
    report = check_sources(rules, metadata)
    assert kinds(report) == [{"kind": "contradiction", "severity": "error"}]
    assert report.subjects.select("source_uuid").to_dicts() == [{"source_uuid": "empty"}]


def test_alias_inconsistency_is_an_explicit_source_finding(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "left": ["ANY"], "right": ["ANY"], "amount": [0],
    }))
    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(dimension_name="a", context_field="region", rule_field="left", data_type="str", match_strategy="exact", wildcard_values=["ANY"]),
        Dimension(dimension_name="b", context_field="region", rule_field="right", data_type="str", match_strategy="exact"),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    report = check_sources(rules, metadata)
    assert {row["kind"] for row in kinds(report)} == {"inconsistent_wildcard"}


def test_null_contribution_is_not_a_no_match_or_zero(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    connection = None
    if backend_name == "ibis-duckdb":
        import ibis
        import polars as pl

        connection = ibis.duckdb.connect()
        source = pl.DataFrame({
            "uuid": ["bad", "zero"], "minimum": [0, 20], "maximum": [10, 30],
            "amount": [None, 0],
        }, schema_overrides={"amount": pl.Int64})
        native = connection.create_table("source", source)
    else:
        native = build_backend_df(backend_name, {
            "uuid": ["bad", "zero"], "minimum": [0, 20], "maximum": [10, 30],
            "amount": [None, 0],
        })
    rules = ma.relation(native)
    report = check_sources(rules, range_metadata())
    assert kinds(report) == [{"kind": "invalid_value", "severity": "error"}]
    assert report.subjects.select("source_uuid").to_dicts() == [{"source_uuid": "bad"}]
    if connection is not None:
        connection.disconnect()




def test_source_overlap_report_distinguishes_containment_and_partial_overlap(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["outer", "inner", "left", "right"],
        "minimum": [0, 2, 20, 25], "maximum": [10, 8, 30, 35], "amount": [1, 2, 3, 4],
    }))
    report = check_sources(rules, range_metadata())

    assert {row["kind"] for row in kinds(report)} == {
        "containment_overlap", "partial_overlap",
    }
@pytest.mark.parametrize("missing_field", ["uuid", "partition", "scope"])
@pytest.mark.parametrize("row_count", [0, 1, 2])
def test_source_check_reports_missing_identity_column(backend_name, missing_field, row_count):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = range_metadata(partition="partition" if missing_field == "partition" else None)
    if missing_field == "scope":
        metadata.outcome_scope_field = "scope"
        metadata.outcome_scopes = {"default": metadata.aggregates}
        metadata.aggregates = []
    data = {
        "uuid": ["A", "B"], "minimum": [0, 0], "maximum": [10, 10],
        "amount": [1, 1],
    }
    data.pop(missing_field, None)
    rules = ma.relation(build_backend_df(backend_name, data)).head(row_count)
    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "invalid_value", "severity": "error"}]
    assert report.subjects.to_dicts() == []
    checks = {check.name: check for check in report.checks}
    assert checks["source_identity"].outcome == "findings"
    assert checks["source_identity"].finding_count == 1
    for name in ("duplicate_identity", "source_contradictions", "source_overlaps"):
        assert checks[name].outcome == "not_run"
        assert checks[name].finding_count == checks[name].emitted_count == 0
    assert checks["contribution_admission"].outcome == (
        "not_run" if missing_field == "scope" else "passed"
    )
    assert sum(check.emitted_count for check in report.checks) == 1


@pytest.mark.parametrize("partition", [None, 1, "north"])
def test_missing_identity_preserves_independent_contribution_findings(backend_name, partition):
    from mountainash_rules.engines.accumulate.sources import check_sources

    data = {"minimum": [0], "maximum": [10], "amount": ["invalid"]}
    if partition is not None:
        data["region"] = [partition]
    rules = ma.relation(build_backend_df(backend_name, data))
    report = check_sources(
        rules, range_metadata(partition="region" if partition is not None else None),
    )

    assert kinds(report) == [
        {"kind": "invalid_value", "severity": "error"},
        {"kind": "invalid_value", "severity": "error"},
    ]
    assert report.subjects.to_dicts() == []
    if partition is not None:
        assert report.findings.filter(
            ~ma.col("partition").is_null(),
        ).select("partition").to_dicts() == [{"partition": partition}]
    checks = {check.name: check for check in report.checks}
    assert checks["contribution_admission"].outcome == "findings"
    assert checks["contribution_admission"].finding_count == 1
    assert checks["duplicate_identity"].outcome == "not_run"
    assert sum(check.emitted_count for check in report.checks) == 2


def test_source_overlap_uses_full_joint_predicate_not_marginal_range(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = range_metadata()
    metadata.dimensions.append(Dimension(
        dimension_name="region", context_field="region", rule_field="region_rule",
        data_type="str", match_strategy="exact",
    ))
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15],
        "region_rule": ["north", "south"], "amount": [1, 2],
    }))
    report = check_sources(rules, metadata)

    assert not [row for row in kinds(report) if row["kind"].endswith("overlap")]
def test_source_overlap_intersects_with_declared_domain(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.scalar import encode_scalar
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.engines.accumulate.sources import check_sources

    node = {
        "schema_version": 1,
        "node": {
            "op": "interval", "field": "x",
            "lower": encode_scalar(0, "int"), "upper": encode_scalar(5, "int"),
            "lower_closed": True, "upper_closed": True,
        },
    }
    identifier = content_id("predicate", node)
    metadata = range_metadata()
    metadata.domain = DomainDefinition(
        schema_version=1, domain_id="D", predicate_id=identifier,
        fields=(DomainField(name="x", data_type="int"),),
    )
    metadata.domain_nodes = {identifier: node}
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [10, 12], "maximum": [20, 18], "amount": [1, 2],
    }))
    report = check_sources(rules, metadata)

    assert not [row for row in kinds(report) if row["kind"].endswith("overlap")]
def test_three_aliases_detect_exact_not_equal_contradiction(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="first", context_field="region", rule_field="first_rule",
            data_type="str", match_strategy="exact",
        ),
        Dimension(
            dimension_name="excluded", context_field="region", rule_field="excluded_rule",
            data_type="str", match_strategy="not_equal",
        ),
        Dimension(
            dimension_name="last", context_field="region", rule_field="last_rule",
            data_type="str", match_strategy="exact",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "first_rule": ["north"], "excluded_rule": ["north"],
        "last_rule": ["north"], "amount": [1],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "contradiction", "severity": "error"}]


def test_exact_only_semantic_duplicate_sources_are_reported(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="region", data_type="str", match_strategy="exact",
            rule_field="region_rule",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "region_rule": ["north", "north"], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "duplicate_source", "severity": "warning"}]
    assert report.subjects.select("source_uuid").sort("source_uuid").to_dicts() == [
        {"source_uuid": "A"}, {"source_uuid": "B"},
    ]


def test_set_membership_overlap_is_intersected_natively(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="region", data_type="str", match_strategy="set_membership",
            rule_field="members",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "members": [["north", "south"], ["south", "west"]],
        "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "partial_overlap", "severity": "warning"}]


def test_set_exclusions_compare_as_cofinite_predicates(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="region", data_type="str", match_strategy="set_exclusion",
            rule_field="excluded",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "excluded": [["north"], ["north"]], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "duplicate_source", "severity": "warning"}]


def test_source_overlap_respects_joint_field_comparison_domain(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.engines.accumulate.sources import check_sources

    node = {
        "schema_version": 1,
        "node": {"op": "compare", "left": "x", "relation": "lt", "right": "y"},
    }
    identifier = content_id("predicate", node)
    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="x_min", range_max_field="x_max",
        ),
        Dimension(
            dimension_name="y", data_type="int", match_strategy="range",
            range_min_field="y_min", range_max_field="y_max",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    metadata.domain = DomainDefinition(
        schema_version=1, domain_id="D", predicate_id=identifier,
        fields=(DomainField(name="x", data_type="int"), DomainField(name="y", data_type="int")),
    )
    metadata.domain_nodes = {identifier: node}
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "x_min": [10, 12], "x_max": [20, 18],
        "y_min": [0, 1], "y_max": [5, 4], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert not [row for row in kinds(report) if row["kind"].endswith("overlap")]
def test_unbounded_integer_ranges_overlap_without_sentinel_arithmetic(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    dimension = Dimension(
        dimension_name="x", data_type="int", match_strategy="range",
        range_min_field="minimum", range_max_field="maximum",
        range_min_unbounded_field="minimum_unbounded",
        range_max_unbounded_field="maximum_unbounded",
    )
    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[dimension],
        aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 20],
        "minimum_unbounded": [True, False], "maximum_unbounded": [False, True],
        "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "partial_overlap", "severity": "warning"}]


def test_date_range_boundary_overlap_retains_native_witness(backend_name):
    import datetime as dt

    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="day", data_type="date", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [dt.date(2024, 1, 1), dt.date(2024, 1, 3)],
        "maximum": [dt.date(2024, 1, 3), dt.date(2024, 1, 5)], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "singleton_boundary_overlap", "severity": "warning"}]
    assert report.witnesses["day"].select("value").to_dicts() == [
        {"value": dt.date(2024, 1, 3)},
    ]


def test_utc_datetime_range_boundary_overlap_retains_native_witness(backend_name):
    import datetime as dt

    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="instant", data_type="datetime", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    start = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
    boundary = dt.datetime(2024, 1, 3, tzinfo=dt.timezone.utc)
    end = dt.datetime(2024, 1, 5, tzinfo=dt.timezone.utc)
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [start, boundary], "maximum": [boundary, end],
        "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "singleton_boundary_overlap", "severity": "warning"}]
    assert report.witnesses["instant"].select("value").to_dicts() == [
        {"value": boundary},
    ]

def test_three_aliases_detect_set_intersection_removed_by_exclusion(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="first", context_field="region", rule_field="first_set",
            data_type="str", match_strategy="set_membership",
        ),
        Dimension(
            dimension_name="second", context_field="region", rule_field="second_set",
            data_type="str", match_strategy="set_membership",
        ),
        Dimension(
            dimension_name="excluded", context_field="region", rule_field="excluded_set",
            data_type="str", match_strategy="set_exclusion",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "first_set": [["north", "south"]],
        "second_set": [["south", "west"]], "excluded_set": [["south"]],
        "amount": [1],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "contradiction", "severity": "error"}]


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("north", "south", "partial_overlap"),
        ("north", "north", "duplicate_source"),
    ],
)
def test_not_equal_sources_overlap_as_cofinite_regions(backend_name, left, right, expected):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="region", data_type="str", match_strategy="not_equal",
            rule_field="excluded",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "excluded": [left, right], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": expected, "severity": "warning"}]

def test_decimal_source_overlap_retains_declared_precision(backend_name):
    from decimal import Decimal

    from mountainash import DecimalDtype
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="price", data_type="decimal", match_strategy="range",
            numeric_dtype=DecimalDtype(8, 2), rounding="half_to_even",
            range_min_field="minimum", range_max_field="maximum",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [Decimal("1.00"), Decimal("2.00")],
        "maximum": [Decimal("2.00"), Decimal("3.00")], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "singleton_boundary_overlap", "severity": "warning"}]
    assert report.witnesses["price"].select("value").to_dicts() == [
        {"value": Decimal("2.00")},
    ]

def test_range_overlap_respects_active_not_equal_alias_predicate(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        ),
        Dimension(
            dimension_name="exact_region", context_field="region", rule_field="exact_region",
            rule_wildcard_field="exact_wild", data_type="str", match_strategy="exact",
        ),
        Dimension(
            dimension_name="excluded_region", context_field="region", rule_field="excluded_region",
            rule_wildcard_field="excluded_wild", data_type="str", match_strategy="not_equal",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 2], "maximum": [10, 8],
        "exact_region": ["north", "unused"], "exact_wild": [False, True],
        "excluded_region": ["unused", "north"], "excluded_wild": [True, False],
        "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert not [row for row in kinds(report) if row["kind"].endswith("overlap")]

def test_unknown_declared_scope_is_a_source_finding(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    aggregate = Aggregate(column_name="amount", data_type="int")
    metadata = DimensionsMetadata(
        uuid_field="uuid", outcome_scope_field="scope",
        outcome_scopes={"default": [aggregate]},
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "scope": ["unknown"], "minimum": [0], "maximum": [1],
        "amount": [1],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "invalid_value", "severity": "error"}]


def test_incorrect_rule_flag_dtype_is_a_finding_not_an_exception(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="region", data_type="str", match_strategy="exact",
            rule_field="region_rule", rule_wildcard_field="wildcard",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "region_rule": ["north"], "wildcard": [1], "amount": [1],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "invalid_value", "severity": "error"}]


def test_empty_source_report_relations_keep_declared_schemas(backend_name):
    import ibis
    import polars as pl

    from mountainash_rules.engines.accumulate.sources import check_sources

    schema = {
        "uuid": pl.String, "minimum": pl.Int64, "maximum": pl.Int64, "amount": pl.Int64,
    }
    if backend_name == "ibis-duckdb":
        connection = ibis.duckdb.connect()
        native = connection.create_table("empty_sources", pl.DataFrame(schema=schema))
    else:
        connection = None
        native = pl.DataFrame(schema=schema)
    report = check_sources(ma.relation(native), range_metadata())

    assert report.findings.columns == ["finding_id", "severity", "kind", "partition", "scope", "field"]
    assert report.subjects.columns == ["finding_id", "partition", "scope", "source_uuid"]
    assert report.findings.count_rows() == 0
    assert report.subjects.count_rows() == 0
    assert report.witnesses["x"].count_rows() == 0
    if connection is not None:
        connection.disconnect()

def test_source_check_uses_discrete_integer_bounds_without_successor_overflow(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="above", context_field="x", rule_field="minimum",
            data_type="int", match_strategy="greater_than",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["empty"], "minimum": [(1 << 63) - 1], "amount": [0],
    }))
    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "contradiction", "severity": "error"}]
    assert report.subjects.select("source_uuid").to_dicts() == [{"source_uuid": "empty"}]
def test_report_retains_immutable_facts_for_its_captured_dependencies(backend_name):
    import ibis
    import polars as pl

    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = range_metadata()
    source_values = {
        "uuid": ["A", "B"], "minimum": [0, 10], "maximum": [10, 20], "amount": [5, 7],
    }
    if backend_name == "ibis-duckdb":
        connection = ibis.duckdb.connect()
        native = connection.create_table("source", source_values)
    else:
        native = build_backend_df(backend_name, source_values)
    rules = ma.relation(native)
    report = check_sources(rules, metadata)
    structure = report.binding.dependencies["source_structure"][0].relation
    assert structure is not None
    assert report.findings.count_rows() == 1
    overlap_check = next(check for check in report.checks if check.name == "source_overlaps")
    assert (overlap_check.outcome, overlap_check.finding_count, overlap_check.emitted_count) == (
        "findings", 1, 1,
    )
    with pytest.raises((AttributeError, TypeError)):
        overlap_check.finding_count = 0
    metadata.dimensions[0].wildcard_values.append(0)
    if backend_name == "ibis-duckdb":
        connection.raw_sql("UPDATE source SET minimum = 15")
    else:
        native.replace_column(1, pl.Series("minimum", [15, 15]))
    assert structure.select("minimum").sort("minimum").to_dicts() == [
        {"minimum": 0}, {"minimum": 10},
    ]
    assert report.findings.count_rows() == 1
    assert report.binding.metadata.dimensions[0].wildcard_values == []
    if backend_name == "ibis-duckdb":
        connection.disconnect()


def test_source_capture_retains_authored_values_and_separate_state(backend_name):
    from mountainash_rules.engines.accumulate.sources import capture_sources

    metadata = range_metadata()
    metadata.dimensions[0].range_min_unbounded_field = "unbounded"
    metadata.dimensions[0].range_max_unbounded_field = "unbounded"
    raw = ma.relation(build_backend_df(backend_name, {
        "uuid": ["concrete", "wild"], "minimum": [-999999999, 0],
        "maximum": [-999999999, 0], "unbounded": [False, True], "amount": [0, 7],
    }))
    captured = capture_sources(raw, metadata)
    metadata.dimensions[0].wildcard_values.append(-999999999)
    assert captured.rules.select("uuid", "minimum", "unbounded").sort("uuid").to_dicts() == [
        {"uuid": "concrete", "minimum": -999999999, "unbounded": False},
        {"uuid": "wild", "minimum": 0, "unbounded": True},
    ]
    assert captured.states["x"].select(
        "source_uuid", "lower_unbounded", "upper_unbounded",
    ).sort("source_uuid").to_dicts() == [
        {"source_uuid": "concrete", "lower_unbounded": False, "upper_unbounded": False},
        {"source_uuid": "wild", "lower_unbounded": True, "upper_unbounded": True},
    ]
    assert captured.states["x"].filter(ma.col("source_uuid") == "concrete").select("lower").to_dicts() == [
        {"lower": -999999999},
    ]
    assert captured.payloads["default"].select("source_uuid", "a0001").sort("source_uuid").to_dicts() == [
        {"source_uuid": "concrete", "a0001": 0},
        {"source_uuid": "wild", "a0001": 7},
    ]
    assert captured.metadata.dimensions[0].wildcard_values == []


def test_source_capture_rejects_duplicate_identity_without_optional_validation(backend_name):
    from mountainash_rules.engines.accumulate.sources import capture_sources

    raw = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "A"], "minimum": [0, 1], "maximum": [5, 6], "amount": [5, 7],
    }))
    with pytest.raises(ValueError, match="identity"):
        capture_sources(raw, range_metadata())


def test_cross_strategy_aliases_conjoin_before_categorical_overlap(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="exact_region", context_field="region", rule_field="exact",
            rule_wildcard_field="exact_wild", data_type="str", match_strategy="exact",
        ),
        Dimension(
            dimension_name="members", context_field="region", rule_field="members",
            rule_wildcard_field="members_wild", data_type="str",
            match_strategy="set_membership",
        ),
        Dimension(
            dimension_name="excluded", context_field="region", rule_field="excluded",
            rule_wildcard_field="excluded_wild", data_type="str",
            match_strategy="set_exclusion",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "exact": ["north", "north"],
        "exact_wild": [False, False],
        "members": [["north", "south"], ["north", "west"]],
        "members_wild": [False, False],
        "excluded": [["south"], ["west"]], "excluded_wild": [False, False],
        "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "duplicate_source", "severity": "warning"}]


def test_range_and_not_equal_alias_remove_the_only_shared_value(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="range", context_field="x", data_type="int",
            match_strategy="range", range_min_field="minimum",
            range_max_field="maximum",
        ),
        Dimension(
            dimension_name="excluded", context_field="x", data_type="int",
            match_strategy="not_equal", rule_field="excluded",
            rule_wildcard_field="excluded_wild",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 1], "maximum": [2, 1],
        "excluded": [1, 0], "excluded_wild": [False, True], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert not [row for row in kinds(report) if row["kind"].endswith("overlap")]


def test_one_sided_ordered_aliases_report_their_nonempty_intersection(backend_name):
    from mountainash_rules.engines.accumulate.sources import check_sources

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="above", context_field="x", rule_field="minimum",
            data_type="int", match_strategy="greater_than",
        ),
        Dimension(
            dimension_name="below", context_field="x", rule_field="maximum",
            data_type="int", match_strategy="less_than",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15],
        "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [{"kind": "partial_overlap", "severity": "warning"}]


def test_nested_boolean_domain_prunes_correlated_source_overlap(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.core.scalar import encode_scalar
    from mountainash_rules.engines.accumulate.sources import check_sources

    nodes = {}

    def add(node):
        payload = {"schema_version": 1, "node": node}
        identifier = content_id("predicate", payload)
        nodes[identifier] = payload
        return identifier

    x_is_one = add({"op": "eq", "field": "x", "value": encode_scalar(1, "int")})
    y_is_four = add({"op": "eq", "field": "y", "value": encode_scalar(4, "int")})
    x_is_three = add({"op": "eq", "field": "x", "value": encode_scalar(3, "int")})
    x_less_y = add({"op": "compare", "left": "x", "relation": "lt", "right": "y"})
    first = add({"op": "and", "args": [x_is_one, y_is_four]})
    second = add({"op": "and", "args": [x_is_three, x_less_y]})
    excluded = add({"op": "or", "args": [first, second]})
    root = add({"op": "not", "arg": excluded})
    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="x_min", range_max_field="x_max",
        ),
        Dimension(
            dimension_name="y", data_type="int", match_strategy="range",
            range_min_field="y_min", range_max_field="y_max",
        ),
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    metadata.domain = DomainDefinition(
        schema_version=1, domain_id="D", predicate_id=root,
        fields=(DomainField(name="x", data_type="int"), DomainField(name="y", data_type="int")),
    )
    metadata.domain_nodes = nodes
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "x_min": [1, 1], "x_max": [1, 1],
        "y_min": [4, 4], "y_max": [4, 4], "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert not [row for row in kinds(report) if row["kind"].endswith("overlap")]


def test_joint_order_domain_propagates_through_discrete_allowed_set_holes(backend_name):
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition, DomainField
    from mountainash_rules.engines.accumulate.sources import check_sources

    nodes = {}

    def add(node):
        payload = {"schema_version": 1, "node": node}
        identifier = content_id("predicate", payload)
        nodes[identifier] = payload
        return identifier

    edges = [
        add({"op": "compare", "left": "z", "relation": "le", "right": "x"}),
        add({"op": "compare", "left": "x", "relation": "le", "right": "y"}),
        add({"op": "compare", "left": "y", "relation": "le", "right": "w"}),
    ]
    root = add({"op": "and", "args": edges})
    fields = ("z", "x", "y", "w")
    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[
        Dimension(
            dimension_name=field, data_type="int", match_strategy="set_membership",
            rule_field=f"{field}_values",
        )
        for field in fields
    ], aggregates=[Aggregate(column_name="amount", data_type="int")])
    metadata.domain = DomainDefinition(
        schema_version=1, domain_id="D", predicate_id=root,
        fields=tuple(DomainField(name=field, data_type="int") for field in sorted(fields)),
    )
    metadata.domain_nodes = nodes
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"],
        "z_values": [[3], [3]], "x_values": [[0, 6], [0, 6]],
        "y_values": [[0, 4, 7], [0, 4, 7]], "w_values": [[6], [6]],
        "amount": [1, 2],
    }))

    report = check_sources(rules, metadata)

    assert kinds(report) == [
        {"kind": "contradiction", "severity": "error"},
        {"kind": "contradiction", "severity": "error"},
    ]
    assert report.subjects.select("source_uuid").sort("source_uuid").to_dicts() == [
        {"source_uuid": "A"}, {"source_uuid": "B"},
    ]


@pytest.mark.parametrize("strategy", ["prefix", "suffix", "contains", "regex", "context_regex"])
def test_pattern_strategies_remain_filter_only(backend_name, strategy):
    from mountainash_rules.engines.accumulate.errors import UnsupportedDomainError
    from mountainash_rules.engines.accumulate.sources import capture_sources, check_sources

    options = {"regex_pattern": "^EU$"} if strategy == "context_regex" else {}
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="region", match_strategy=strategy, **options)],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "region": ["EU"], "amount": [5],
    }))
    with pytest.raises(UnsupportedDomainError):
        capture_sources(rules, metadata)
    report = check_sources(rules, metadata)
    assert kinds(report) == [{"kind": "unsupported_strategy", "severity": "error"}]


@pytest.mark.parametrize("dtype", [ma.MountainashDtype.I32, ma.MountainashDtype.U32])
def test_narrow_integer_payload_evidence_can_publish_its_unchanged_artifact(backend_name, dtype):
    from mountainash_rules.engines.accumulate.build import build
    from mountainash_rules.engines.accumulate.sources import check_sources
    from mountainash_rules.engines.accumulate.validate import validate

    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "minimum": [0], "maximum": [10], "amount": [7],
    })).with_columns(ma.col("amount").cast(dtype).alias("amount"))
    metadata = range_metadata()
    lattice = build(rules, metadata)
    source_report = check_sources(rules, metadata)
    artifact_report = validate(lattice, rules)

    published = lattice.publish(
        policy="strict", source_report=source_report, artifact_report=artifact_report,
    )
    assert published.published
    assert published.outputs["default"].select("v0001").to_dicts() == [{"v0001": 7}]
