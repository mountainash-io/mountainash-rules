"""Author declarations retain interpretation across JSON/YAML transport."""

import datetime as dt
from decimal import Decimal

import pytest
from mountainash import DecimalDtype

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.scalar import decode_scalar, encode_scalar


def decimal_dimension(**overrides):
    return Dimension(
        **{
            "dimension_name": "quantity",
            "data_type": "decimal",
            "match_strategy": "range",
            "range_min_field": "lower",
            "range_max_field": "upper",
            "numeric_dtype": DecimalDtype(10, 3),
            "rounding": "half_to_even",
            **overrides,
        }
    )


@pytest.mark.parametrize("rounding", ["half_to_even", "half_away_from_zero"])
def test_decimal_dimension_round_trip_retains_authored_interpretation(rounding):
    metadata = DimensionsMetadata(
        dimensions=[
            decimal_dimension(
                rounding=rounding,
                range_min_unbounded_field="open_lower",
                range_max_unbounded_field="open_upper",
            )
        ]
    )
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml()).dimensions[0]
    assert restored.numeric_dtype == DecimalDtype(10, 3)
    assert restored.rounding == rounding
    assert restored.range_min_field == "lower"
    assert restored.range_max_field == "upper"
    assert restored.range_min_unbounded_field == "open_lower"
    assert restored.range_max_unbounded_field == "open_upper"


@pytest.mark.parametrize(
    "overrides",
    [
        {"numeric_dtype": None},
        {"rounding": None},
        {"rounding": "to_zero"},
        {"numeric_dtype": DecimalDtype(17, 3)},
    ],
)
def test_decimal_ordering_requires_restricted_explicit_declaration(overrides):
    with pytest.raises(ValueError):
        decimal_dimension(**overrides)


@pytest.mark.parametrize("strategy", ["range", "greater_than", "less_than"])
def test_float_ordered_declarations_are_rejected_including_yaml(strategy):
    declaration = {
        "dimension_name": "quantity",
        "data_type": "float",
        "match_strategy": strategy,
        "range_min_field": "lower",
        "range_max_field": "upper",
    }
    with pytest.raises(ValueError):
        Dimension(**declaration)
    import yaml

    with pytest.raises(ValueError):
        DimensionsMetadata.from_yaml(yaml.safe_dump({"dimensions": [declaration]}))


@pytest.mark.parametrize(
    "value, record",
    [
        ("0", {"type": "str", "value": "0"}),
        (0, {"type": "int", "value": "0"}),
        (False, {"type": "bool", "value": False}),
        (0.5, {"type": "float", "value": "3fe0000000000000"}),
        (None, {"type": "null", "value": None}),
        (Decimal("0.00450"), {"type": "decimal", "value": "0.00450"}),
        (dt.date(1, 1, 1), {"type": "date", "value": "0001-01-01"}),
        (
            dt.datetime(2026, 1, 2),
            {
                "type": "datetime",
                "value": "2026-01-02T00:00:00.000000",
                "timezone": "naive",
            },
        ),
        (
            dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc),
            {
                "type": "datetime",
                "value": "2026-01-02T00:00:00.000000Z",
                "timezone": "utc",
            },
        ),
    ],
)
def test_marker_round_trip_preserves_original_type_and_exact_value(value, record):
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="x",
                wildcard_values=[value],
                rule_wildcard_field="any_x",
            )
        ]
    )
    assert metadata.model_dump(mode="json")["dimensions"][0]["wildcard_values"] == [
        record
    ]
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml()).dimensions[0]
    assert type(restored.wildcard_values[0]) is type(value)
    assert restored.wildcard_values[0] == value
    assert restored.rule_wildcard_field == "any_x"


def test_no_markers_and_explicit_null_remain_distinct():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="a"),
            Dimension(dimension_name="b", wildcard_values=[None]),
        ]
    )
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml())
    assert restored.dimensions[0].wildcard_values == []
    assert restored.dimensions[1].wildcard_values == [None]


def test_alias_marker_differences_do_not_gate_authoring():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="a", context_field="region", wildcard_values=["ANY"]
            ),
            Dimension(
                dimension_name="b", context_field="region", wildcard_values=[None]
            ),
        ]
    )
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml())
    assert restored.dimensions[0].wildcard_values == ["ANY"]
    assert restored.dimensions[1].wildcard_values == [None]


def test_decimal_scalar_codec_does_not_quantize_to_interpretation():
    value = Decimal("0.0045")
    record = encode_scalar(
        value, "decimal", numeric_dtype=DecimalDtype(10, 3), rounding="half_to_even"
    )
    assert record == {"type": "decimal", "value": "0.0045"}
    assert (
        decode_scalar(
            record, numeric_dtype=DecimalDtype(10, 3), rounding="half_to_even"
        )
        == value
    )


def test_fractional_product_keeps_contribution_and_output_declarations_separate():
    from mountainash_rules.core.aggregate import Aggregate

    aggregate = Aggregate(
        column_name="factor",
        operation="product",
        data_type="decimal",
        numeric_dtype=DecimalDtype(10, 6),
        output_numeric_dtype=DecimalDtype(12, 3),
        output_rounding="half_away_from_zero",
    )
    metadata = DimensionsMetadata(
        dimensions=[], aggregates=[aggregate], uuid_field="source_id"
    )
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml())
    declaration = restored.aggregates[0]
    assert declaration.resolved_output_name == "factor"
    assert declaration.numeric_dtype == DecimalDtype(10, 6)
    assert declaration.output_numeric_dtype == DecimalDtype(12, 3)
    assert declaration.output_rounding == "half_away_from_zero"
    assert restored.uuid_field == "source_id"


@pytest.mark.parametrize(
    "changes",
    [
        {"data_type": "float", "numeric_dtype": None},
        {"numeric_dtype": None},
        {"output_numeric_dtype": None},
        {"output_rounding": None},
    ],
)
def test_fractional_product_requires_complete_authored_conversion(changes):
    from mountainash_rules.core.aggregate import Aggregate

    with pytest.raises(ValueError):
        Aggregate(
            **{
                "column_name": "factor",
                "operation": "product",
                "data_type": "decimal",
                "numeric_dtype": DecimalDtype(10, 6),
                "output_numeric_dtype": DecimalDtype(12, 3),
                "output_rounding": "half_to_even",
                **changes,
            }
        )


def test_scoped_aggregate_transport_retains_independent_types():
    from mountainash_rules.core.aggregate import Aggregate

    metadata = DimensionsMetadata(
        dimensions=[],
        partition_field="bank",
        outcome_scope_field="kind",
        outcome_scopes={
            "fee": [
                Aggregate(
                    column_name="value",
                    data_type="decimal",
                    numeric_dtype=DecimalDtype(20, 2),
                )
            ],
            "label": [Aggregate(column_name="value", data_type="str", operation="min")],
        },
    )
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml())
    assert restored.partition_field == "bank"
    assert restored.outcome_scope_field == "kind"
    assert restored.outcome_scopes["fee"][0].numeric_dtype == DecimalDtype(20, 2)
    assert restored.outcome_scopes["label"][0].data_type == "str"


@pytest.mark.parametrize("scope", ["bad.name", ""])
def test_wide_scope_names_remain_unambiguous(scope):
    from mountainash_rules.core.aggregate import Aggregate

    with pytest.raises(ValueError):
        DimensionsMetadata(
            dimensions=[],
            outcome_scope_field="kind",
            outcome_scopes={scope: [Aggregate(column_name="value", data_type="int")]},
        )


def test_domain_graph_and_provider_profile_survive_metadata_only_reload():
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition

    node = {
        "schema_version": 1,
        "node": {
            "op": "interval",
            "field": "quantity",
            "lower": {"type": "decimal", "value": "0.0045"},
            "upper": {"type": "decimal", "value": "10"},
            "lower_closed": True,
            "upper_closed": False,
        },
    }
    predicate_id = content_id("predicate", node)
    field = {
        "name": "quantity",
        "data_type": "decimal",
        "numeric_dtype": DecimalDtype(10, 3),
        "rounding": "half_to_even",
    }
    domain = DomainDefinition(
        schema_version=1, domain_id="D", fields=[field], predicate_id=predicate_id
    )
    metadata = DimensionsMetadata(
        dimensions=[decimal_dimension()],
        domain=domain,
        domain_nodes={predicate_id: node},
        context_domains=[
            DomainDefinition(
                schema_version=1,
                domain_id="P",
                fields=[field],
                predicate_id=predicate_id,
            )
        ],
        context_contracts=[
            {
                "schema_version": 1,
                "contract_id": "provider",
                "domain_ref": "P",
                "fields": [{**field, "required": True}],
                "profiles": [
                    {
                        "profile_id": "resolve",
                        "mode": "resolve",
                        "output_fields": ["amount"],
                        "provenance": "none",
                        "dimensions": ["quantity"],
                        "allow_dont_care": [],
                        "promise": "allow_unresolved",
                        "on_unresolved": "return",
                    }
                ],
            }
        ],
    )
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml())
    assert restored.domain.fields[0].numeric_dtype == DecimalDtype(10, 3)
    assert restored.domain.fields[0].rounding == "half_to_even"
    assert restored.domain_nodes[predicate_id]["node"]["lower"]["value"] == "0.0045"
    assert restored.context_contracts[0].domain_ref == "P"
    assert restored.context_domains[0].domain_id == "P"
    assert restored.context_domains[0].fields[0].numeric_dtype == DecimalDtype(10, 3)
    assert restored.context_contracts[0].profiles[0].on_unresolved == "return"


def test_unresolved_domain_graph_is_not_replaced_by_unrestricted_domain():
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition

    predicate_id = content_id(
        "predicate", {"schema_version": 1, "node": {"op": "true"}}
    )
    domain = DomainDefinition(
        schema_version=1, domain_id="D", fields=[], predicate_id=predicate_id
    )
    with pytest.raises(ValueError):
        DimensionsMetadata(dimensions=[], domain=domain)


@pytest.mark.parametrize("precision, scale", [(1, 0), (16, 16)])
def test_decimal_precision_and_scale_boundaries_survive_transport(precision, scale):
    metadata = DimensionsMetadata(
        dimensions=[decimal_dimension(numeric_dtype=DecimalDtype(precision, scale))]
    )
    actual = (
        DimensionsMetadata.from_yaml(metadata.to_yaml()).dimensions[0].numeric_dtype
    )
    assert (actual.precision, actual.scale) == (precision, scale)


def test_joint_domain_comparison_and_old_marker_values_survive_transport():
    from mountainash_rules.core.codec import content_id
    from mountainash_rules.core.contracts import DomainDefinition

    nodes = [
        {
            "schema_version": 1,
            "node": {"op": "compare", "left": "x", "right": "y", "relation": "lt"},
        },
        {
            "schema_version": 1,
            "node": {
                "op": "eq",
                "field": "x",
                "value": {"type": "int", "value": "-999999999"},
            },
        },
    ]
    graph = {content_id("predicate", node): node for node in nodes}
    root = {"schema_version": 1, "node": {"op": "and", "args": sorted(graph)}}
    root_id = content_id("predicate", root)
    graph[root_id] = root
    domain = DomainDefinition(
        schema_version=1,
        domain_id="D",
        predicate_id=root_id,
        fields=[{"name": "x", "data_type": "int"}, {"name": "y", "data_type": "int"}],
    )
    metadata = DimensionsMetadata(dimensions=[], domain=domain, domain_nodes=graph)
    restored = DimensionsMetadata.from_yaml(metadata.to_yaml())
    assert restored.domain_nodes[content_id("predicate", nodes[0])]["node"] == {
        "op": "compare",
        "left": "x",
        "right": "y",
        "relation": "lt",
    }
    assert restored.domain_nodes[content_id("predicate", nodes[1])]["node"][
        "value"
    ] == {
        "type": "int",
        "value": "-999999999",
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"outcome_scope_field": "scope"},
        {"outcome_scope_field": "scope", "outcome_scopes": {"fee": []}},
    ],
)
def test_scope_mapping_requires_nonempty_declared_aggregates(metadata):
    with pytest.raises(ValueError):
        DimensionsMetadata(dimensions=[], **metadata)


def test_decimal_domain_uses_existing_envelope_codec():
    from mountainash_rules.core.codec import content_id, validate_envelope
    from mountainash_rules.core.contracts import DomainDefinition

    root = content_id("predicate", {"schema_version": 1, "node": {"op": "true"}})
    domain = DomainDefinition(
        schema_version=1,
        domain_id="D",
        predicate_id=root,
        fields=[
            {
                "name": "x",
                "data_type": "decimal",
                "numeric_dtype": DecimalDtype(10, 3),
                "rounding": "half_to_even",
            }
        ],
    )
    payload = domain.model_dump(mode="json", exclude_none=True)
    envelope = {"id": content_id("domain", payload), "payload": payload}
    restored = DomainDefinition.model_validate(
        validate_envelope(envelope, "domain")["payload"]
    )
    assert restored.fields[0].numeric_dtype == DecimalDtype(10, 3)
    assert restored.fields[0].rounding == "half_to_even"
