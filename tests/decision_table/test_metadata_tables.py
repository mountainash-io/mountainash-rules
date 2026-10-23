from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from mountainash_rules import ContextField
from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


def test_metadata_rejects_conflicting_context_field_declarations():
    field = ContextField(name="region", data_type=DataType.STR, wildcard="*")
    other = ContextField(name="region", data_type=DataType.STR, wildcard="?")
    with pytest.raises(ValueError, match="conflicting"):
        DimensionsMetadata(fields=(field, other), dimensions=[])


def test_context_field_dimension_and_aggregate_flat_table_round_trip():
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(
            ContextField(name="when", data_type=DataType.DATETIME, datetime_unit="ms", timezone="utc", wildcard=datetime(2000, 1, 1, tzinfo=timezone.utc)),
            ContextField(name="price", data_type=DataType.DECIMAL, precision=8, scale=2, rounding="half_to_even", wildcard=Decimal("-999.99")),
            ContextField(name="active", data_type=DataType.BOOL, wildcard=None, wildcard_declared=True, truthy={True: 1, False: -1}),
        ),
        dimensions=(
            Dimension(dimension_name="when", match_strategy=MatchStrategy.RANGE, context_field="when", rule_field="when", range_min_field="start", range_max_field="end"),
            Dimension(dimension_name="active", context_field="active", match_strategy=MatchStrategy.EXACT),
        ),
        outputs=(Aggregate(scope="fees", output_name="fee", contribution_column="charge", operation=AggregateOp.SUM, data_type=DataType.DECIMAL, precision=8, scale=2, rounding="half_to_even"),),
    )
    tables = metadata.to_tables()
    assert tables["ruleset"].columns == ["uuid_column", "scope_column"]
    assert tables["fields"].columns == ["name", "data_type", "precision", "scale", "rounding", "datetime_unit", "timezone", "wildcard", "wildcard_declared"]
    assert tables["dimensions"].columns == ["dimension_name", "context_field", "rule_field", "match_strategy", "range_min_field", "range_max_field", "regex_pattern"]
    assert tables["outputs"].columns == ["scope", "output_name", "contribution_column", "operation", "data_type", "precision", "scale", "rounding", "timezone"]
    assert tables["truthy"].columns == ["context_field", "value", "outcome"]
    assert tables["ruleset"].to_dicts() == [{"uuid_column": "rule_id", "scope_column": "scope"}]
    assert {row["name"] for row in tables["fields"].to_dicts()} == {"when", "price", "active"}
    assert tables["outputs"].to_dicts() == [{"scope": "fees", "output_name": "fee", "contribution_column": "charge", "operation": "sum", "data_type": "decimal", "precision": 8, "scale": 2, "rounding": "half_to_even", "timezone": None}]
    assert tables["truthy"].to_dicts() == [{"context_field": "active", "value": "true", "outcome": 1}, {"context_field": "active", "value": "false", "outcome": -1}]
    assert DimensionsMetadata.from_tables(tables) == metadata


def test_canonical_wildcard_text_is_typed_by_field():
    metadata = DimensionsMetadata(fields=(
        ContextField(name="day", data_type=DataType.DATE, wildcard=date(2026, 1, 2)),
        ContextField(name="flag", data_type=DataType.BOOL, wildcard=True),
        ContextField(name="price", data_type=DataType.DECIMAL, precision=5, scale=2, rounding="half_to_even", wildcard=Decimal("12.30")),
    ), dimensions=[])
    rows = {row["name"]: row["wildcard"] for row in metadata.to_tables()["fields"].to_dicts()}
    assert rows == {"day": "2026-01-02", "flag": "true", "price": "12.30"}


def test_empty_metadata_has_five_tables_and_null_ruleset_columns():
    tables = DimensionsMetadata(fields=(), dimensions=()).to_tables()
    assert set(tables) == {"ruleset", "fields", "dimensions", "outputs", "truthy"}
    assert tables["ruleset"].to_dicts() == [{"uuid_column": None, "scope_column": None}]
    assert all(
        tables[name].count_rows() == 0
        for name in ("fields", "dimensions", "outputs", "truthy")
    )
