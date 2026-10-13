"""Request-local witnesses cover exact contributor alternatives without a global frontier."""

import mountainash as ma
import pytest

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.codec import content_id
from mountainash_rules.core.contracts import DomainDefinition, DomainField
from mountainash_rules.core.constants import DataType
from mountainash_rules.engines.accumulate.domain import FieldRestriction, ValueConstraint
from tests.conftest import build_backend_df


def _domain(node, fields):
    identifier = content_id("predicate", node)
    definition = DomainDefinition(
        schema_version=1, domain_id="D", predicate_id=identifier, fields=tuple(fields),
    )
    return definition, {identifier: node}


def _missing_effective(dimensions):
    native_types = {
        DataType.BOOL: ma.MountainashDtype.BOOL,
        DataType.INT: ma.MountainashDtype.I64,
        DataType.STR: ma.MountainashDtype.STRING,
    }
    effective = {}
    for dim in dimensions:
        field = dim.resolved_context_field
        effective.setdefault(field, FieldRestriction(
            data_type=dim.data_type,
            values=(ValueConstraint(
                ma.lit(None, dtype=native_types[dim.data_type]), False, True, ma.lit(True),
            ),),
        ))
    return effective


def _bundle(backend_name, dimensions, rows, *, domain=None, domain_nodes=None):
    from mountainash_rules.engines.accumulate.sources import capture_sources

    metadata = DimensionsMetadata(
        uuid_field="uuid", dimensions=dimensions,
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        domain=domain, domain_nodes=domain_nodes or {},
    )
    rules = ma.relation(build_backend_df(backend_name, rows))
    return capture_sources(rules, metadata)


def _matches(sources, assignments, value_columns, residual_columns):
    from mountainash_rules.engines.accumulate.applicability import matched_sources

    point_columns = []
    point_keys = ["request_id"]
    for dim in sources.metadata.dimensions:
        field = dim.resolved_context_field
        value_column = value_columns[field]
        residual_column = residual_columns[field]
        point_columns.extend([
            ma.col(value_column).alias(f"__ctx_value_{dim.dimension_name}"),
            ma.lit(True).alias(f"__ctx_present_{dim.dimension_name}"),
            ma.col(residual_column).alias(f"__ctx_residual_{dim.dimension_name}"),
        ])
        point_keys.extend((value_column, residual_column))
    points = assignments.with_columns(*point_columns)
    return matched_sources(
        sources, points, ("default",), point_keys=point_keys,
    )


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_logical_points_collapse_universal_optional_boolean_axes(backend_name):
    from mountainash_rules.engines.accumulate.applicability import logical_source_points

    dimensions = [Dimension(
        dimension_name=f"flag{index}", data_type="bool", rule_wildcard_field=f"any{index}",
    ) for index in range(16)]
    row = {"uuid": "universal", "amount": 1}
    row.update({f"flag{index}": False for index in range(16)})
    row.update({f"any{index}": True for index in range(16)})
    sources = _bundle(backend_name, dimensions, {name: [value] for name, value in row.items()})
    requests = ma.relation(build_backend_df(backend_name, {
        "request_id": [7], "partition": ["default"],
    }))

    assignments, values, residuals = logical_source_points(
        sources, requests, _missing_effective(dimensions), (), "default",
    )

    assert assignments.count_rows() == 1
    assert set(values) == set(residuals) == {dim.resolved_context_field for dim in dimensions}
    assert _matches(sources, assignments, values, residuals).select("source_uuid").to_dicts() == [
        {"source_uuid": "universal"},
    ]


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_logical_points_preserve_certain_baseline_and_exclusive_sources(backend_name):
    from mountainash_rules.engines.accumulate.applicability import logical_source_points

    dimension = Dimension(
        dimension_name="region", data_type="str", rule_wildcard_field="any_region",
    )
    predicate = {
        "schema_version": 1,
        "node": {
            "op": "in", "field": "region",
            "values": [
                {"type": "str", "value": "EU"},
                {"type": "str", "value": "US"},
            ],
        },
    }
    declared, nodes = _domain(predicate, [DomainField(name="region", data_type="str")])
    sources = _bundle(backend_name, [dimension], {
        "uuid": ["base", "eu", "us"], "region": ["EU", "EU", "US"],
        "any_region": [True, False, False], "amount": [1, 2, 3],
    }, domain=declared, domain_nodes=nodes)
    requests = ma.relation(build_backend_df(backend_name, {
        "request_id": [11], "partition": ["default"],
    }))

    assignments, values, residuals = logical_source_points(
        sources, requests, _missing_effective([dimension]), (declared,), "default",
    )
    assert assignments.select(values["region"]).unique().count_rows() == 2
    matches = _matches(sources, assignments, values, residuals).select(
        "request_id", ma.col(values["region"]).alias("region"), "source_uuid",
    ).to_dicts()
    grouped = {}
    for row in matches:
        grouped.setdefault(row["region"], set()).add(row["source_uuid"])

    assert grouped == {"EU": {"base", "eu"}, "US": {"base", "us"}}


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_logical_points_keep_exact_no_match_alternative(backend_name):
    from mountainash_rules.engines.accumulate.applicability import logical_source_points

    dimension = Dimension(dimension_name="region", data_type="str")
    sources = _bundle(backend_name, [dimension], {
        "uuid": ["eu"], "region": ["EU"], "amount": [5],
    })
    requests = ma.relation(build_backend_df(backend_name, {
        "request_id": [4], "partition": ["default"],
    }))
    effective = {
        "region": FieldRestriction(
            data_type=DataType.STR,
            values=(ValueConstraint(ma.lit("APAC"), False, True, ma.lit(False)),),
        ),
    }

    assignments, values, residuals = logical_source_points(
        sources, requests, effective, (), "default",
    )

    assert assignments.count_rows() == 1
    assert assignments.select(values["region"]).to_dicts() == [{values["region"]: "APAC"}]
    assert _matches(sources, assignments, values, residuals).count_rows() == 0


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_logical_points_respect_correlated_domain_comparisons(backend_name):
    from mountainash_rules.engines.accumulate.applicability import logical_source_points

    dimensions = [
        Dimension(dimension_name="x", data_type="int"),
        Dimension(dimension_name="y", data_type="int"),
    ]
    predicate = {
        "schema_version": 1,
        "node": {"op": "compare", "left": "x", "relation": "lt", "right": "y"},
    }
    declared, nodes = _domain(predicate, [
        DomainField(name="x", data_type="int"), DomainField(name="y", data_type="int"),
    ])
    sources = _bundle(backend_name, dimensions, {
        "uuid": ["impossible", "valid"], "x": [1, 0], "y": [0, 1], "amount": [2, 3],
    }, domain=declared, domain_nodes=nodes)
    requests = ma.relation(build_backend_df(backend_name, {
        "request_id": [9], "partition": ["default"],
    }))

    assignments, values, residuals = logical_source_points(
        sources, requests, _missing_effective(dimensions), (declared,), "default",
    )

    assert _matches(sources, assignments, values, residuals).select("source_uuid").unique().to_dicts() == [
        {"source_uuid": "valid"},
    ]
