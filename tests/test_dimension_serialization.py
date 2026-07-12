"""Tests for serialisable dimension metadata: StrEnums, DataType, YAML."""

import warnings

import pytest

from mountainash_rules.constants import DimensionRole, MatchStrategy


class TestStrEnums:
    def test_match_strategy_constructs_from_string(self):
        assert MatchStrategy("range") is MatchStrategy.RANGE

    def test_match_strategy_value_is_stable_string(self):
        assert MatchStrategy.GREATER_THAN.value == "greater_than"

    def test_match_strategy_is_str(self):
        assert isinstance(MatchStrategy.EXACT, str)

    def test_context_regex_member_exists(self):
        assert MatchStrategy("context_regex") is MatchStrategy.CONTEXT_REGEX

    def test_dimension_role_constructs_from_string(self):
        assert DimensionRole("context_key") is DimensionRole.CONTEXT_KEY


import datetime

from mountainash_rules.constants import (
    DataType,
    NOT_SET,
    NOT_SET_DATE,
    NOT_SET_NUMERIC,
    NUMERIC_SENTINELS,
    STRING_SENTINELS,
    TEMPORAL_DATE_SENTINELS,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    not_set_sentinel_for,
    sentinels_for,
    unknown_sentinel_for,
)


class TestDataType:
    def test_values(self):
        assert DataType("int") is DataType.INT
        assert DataType.DATE.value == "date"

    def test_is_numeric(self):
        assert DataType.INT.is_numeric and DataType.FLOAT.is_numeric
        assert not DataType.STR.is_numeric and not DataType.DATE.is_numeric

    def test_is_temporal(self):
        assert DataType.DATE.is_temporal and DataType.DATETIME.is_temporal
        assert not DataType.INT.is_temporal

    def test_python_type(self):
        assert DataType.DATE.python_type is datetime.date
        assert DataType.BOOL.python_type is bool


class TestSentinelSelection:
    def test_temporal_sentinels_are_proleptic_floor(self):
        assert UNKNOWN_DATE == datetime.date(1, 1, 1)
        assert NOT_SET_DATE == datetime.date(1, 1, 2)
        assert UNKNOWN_DATETIME == datetime.datetime(1, 1, 1)

    def test_sentinels_for(self):
        assert sentinels_for(DataType.INT) == NUMERIC_SENTINELS
        assert sentinels_for(DataType.STR) == STRING_SENTINELS
        assert sentinels_for(DataType.DATE) == TEMPORAL_DATE_SENTINELS

    def test_scalar_lookups(self):
        assert not_set_sentinel_for(DataType.FLOAT) == NOT_SET_NUMERIC
        assert not_set_sentinel_for(DataType.STR) == NOT_SET
        assert unknown_sentinel_for(DataType.DATE) == UNKNOWN_DATE


from mountainash_rules.dimension import Dimension, DimensionsMetadata


class TestDataTypeMigration:
    def test_enum_accepted_directly(self):
        d = Dimension(dimension_name="x", data_type=DataType.INT)
        assert d.data_type is DataType.INT

    def test_string_value_accepted(self):
        d = Dimension(dimension_name="x", data_type="float")
        assert d.data_type is DataType.FLOAT

    def test_python_type_accepted_with_deprecation_warning(self):
        with pytest.warns(DeprecationWarning):
            d = Dimension(dimension_name="x", data_type=int)
        assert d.data_type is DataType.INT

    def test_range_accepts_temporal(self):
        d = Dimension(
            dimension_name="eff", match_strategy=MatchStrategy.RANGE,
            data_type=DataType.DATE,
            range_min_field="eff_from", range_max_field="eff_to",
        )
        assert d.data_type is DataType.DATE

    def test_range_rejects_bool(self):
        with pytest.raises(ValueError, match="range"):
            Dimension(
                dimension_name="x", match_strategy=MatchStrategy.RANGE,
                data_type=DataType.BOOL,
                range_min_field="a", range_max_field="b",
            )


class TestYamlRoundTrip:
    def _full_metadata(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="region", valid_values=["AU", "NZ"]),
            Dimension(
                dimension_name="amount", match_strategy=MatchStrategy.RANGE,
                data_type=DataType.INT,
                range_min_field="amt_min", range_max_field="amt_max",
                range_max_inclusive=False,
            ),
            Dimension(
                dimension_name="chan", context_field="channel",
                rule_field="chan_rule", match_strategy=MatchStrategy.PREFIX,
            ),
            Dimension(
                dimension_name="segment", role="context_key",
            ),
        ])

    def test_round_trip_equality(self, tmp_path):
        md = self._full_metadata()
        assert DimensionsMetadata.from_yaml(md.to_yaml()) == md

    def test_yaml_contains_string_values_not_ints(self):
        text = self._full_metadata().to_yaml()
        assert "range" in text
        assert "context_key" in text

    def test_file_round_trip(self, tmp_path):
        md = self._full_metadata()
        p = md.to_yaml_file(tmp_path / "md.yaml")
        assert DimensionsMetadata.from_yaml_file(p) == md
