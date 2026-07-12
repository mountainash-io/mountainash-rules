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


import polars as pl

from mountainash_rules.constants import UNKNOWN_DATE
from mountainash_rules.engine import ExpressionRulesEngine


def _effective_dated_metadata():
    return DimensionsMetadata(dimensions=[
        Dimension(
            dimension_name="asof", match_strategy=MatchStrategy.RANGE,
            data_type=DataType.DATE,
            range_min_field="eff_from", range_max_field="eff_to",
        ),
    ])


class TestTemporalRange:
    def _rules(self):
        return pl.DataFrame({
            "rule_name": ["current", "expired", "open_ended"],
            "eff_from": [
                datetime.date(2026, 1, 1),
                datetime.date(2024, 1, 1),
                datetime.date(2026, 6, 1),
            ],
            "eff_to": [
                datetime.date(2026, 12, 31),
                datetime.date(2024, 12, 31),
                UNKNOWN_DATE,  # no expiry
            ],
        })

    def test_filter_engine_matches_by_date(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({"asof": datetime.date(2026, 7, 12)})
        assert result.count == 2  # current + open_ended, not expired

    def test_sentinel_bound_is_unconstrained_ternary_zero(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({"asof": datetime.date(2027, 6, 1)})
        # only open_ended survives; its sentinel max yields UNKNOWN (0)
        assert result.explain("open_ended") == {"asof": 0}

    def test_missing_date_context_is_unknown(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({})
        assert result.count == 3  # NOT_SET_DATE -> all UNKNOWN wildcards

    def test_accumulator_combines_overlapping_date_ranges(self):
        from mountainash.relations import relation
        from mountainash_rules.accumulator_engine import AccumulatorEngine
        engine = AccumulatorEngine(dimension_metadata=_effective_dated_metadata())
        rules = pl.DataFrame({
            "rule_name": ["A", "B"],
            "eff_from": [datetime.date(2026, 1, 1), datetime.date(2026, 6, 1)],
            "eff_to": [datetime.date(2026, 12, 31), UNKNOWN_DATE],
        })
        lattice = engine.build(rules)
        rows = relation(lattice.combinations).to_dict()
        assert 6 in set(rows["__prime_product"])


class TestTemporalBackendRegression:
    def test_duckdb_backend_stores_and_compares_sentinel_dates(self):
        from tests.conftest import build_backend_df
        rules = build_backend_df("ibis-duckdb", {
            "rule_name": ["r"],
            "eff_from": [datetime.date(2026, 1, 1)],
            "eff_to": [UNKNOWN_DATE],
        }, table_name="temporal_rules_tmp")
        engine = ExpressionRulesEngine(
            rules=rules, dimension_metadata=_effective_dated_metadata()
        )
        result = engine.evaluate({"asof": datetime.date(2026, 7, 12)})
        assert result.count == 1


class TestRegexSplit:
    def test_regex_forbids_regex_pattern(self):
        with pytest.raises(ValueError, match="context_regex"):
            Dimension(
                dimension_name="x", match_strategy=MatchStrategy.REGEX,
                regex_pattern="^A.*",
            )

    def test_context_regex_requires_pattern(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(dimension_name="x", match_strategy=MatchStrategy.CONTEXT_REGEX)

    def test_per_row_regex_matches_per_rule(self):
        from mountainash_rules.constants import UNKNOWN
        rules = pl.DataFrame({
            "rule_name": ["au", "nz", "any"],
            "x": ["^AU-", "^NZ-", UNKNOWN],
        })
        md = DimensionsMetadata(dimensions=[
            Dimension(dimension_name="x", match_strategy=MatchStrategy.REGEX),
        ])
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=md)
        result = engine.evaluate({"x": "AU-1234"})
        assert result.count == 2  # au (match) + any (sentinel wildcard)
        assert result.explain("au") == {"x": 1}
        assert result.explain("any") == {"x": 0}


class TestBoolDimensions:
    def _md(self):
        return DimensionsMetadata(dimensions=[
            Dimension(dimension_name="active", data_type=DataType.BOOL),
        ])

    def test_bool_exact_match(self):
        rules = pl.DataFrame({"rule_name": ["on", "off", "any"],
                              "active": [True, False, None]})
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=self._md())
        result = engine.evaluate({"active": True})
        assert result.count == 2
        assert result.explain("on") == {"active": 1}
        assert result.explain("any") == {"active": 0}

    def test_missing_bool_context_preserved_as_null_wildcard(self):
        rules = pl.DataFrame({"rule_name": ["on"], "active": [True]})
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=self._md())
        result = engine.evaluate({})
        assert result.count == 1
        assert result.explain("on") == {"active": 0}
