"""Tests for Dimension model validation."""

import pytest

from mountainash_rules import ContextField
from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata


class TestStringStrategyValidation:
    @pytest.mark.parametrize(
        "strategy", [MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS, MatchStrategy.REGEX, MatchStrategy.CONTEXT_REGEX]
    )
    def test_string_strategies_reject_non_string_fields(self, strategy):
        with pytest.raises(ValueError, match=f"{strategy.value} requires STR"):
            DimensionsMetadata(
                fields=(ContextField(name="x", data_type="int"),),
                dimensions=(Dimension(
                    dimension_name="x", context_field="x",
                    match_strategy=strategy,
                    regex_pattern="^x" if strategy is MatchStrategy.CONTEXT_REGEX else None,
                ),),
            )

    @pytest.mark.parametrize(
        "strategy", [MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS]
    )
    def test_string_strategies_accept_string_fields(self, strategy):
        metadata = DimensionsMetadata(
            fields=(ContextField(name="x", data_type="str"),),
            dimensions=(Dimension(
                dimension_name="x", context_field="x", match_strategy=strategy
            ),),
        )
        assert metadata.dimensions[0].match_strategy == strategy

class TestRegexPatternValidation:
    def test_regex_with_pattern_ok(self):
        d = Dimension(
            dimension_name="code", context_field="code",
            match_strategy=MatchStrategy.CONTEXT_REGEX,
            regex_pattern="^foo",
        )
        assert d.regex_pattern == "^foo"

    def test_regex_without_pattern_raises(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="code", context_field="code",
                match_strategy=MatchStrategy.CONTEXT_REGEX,
            )

    def test_regex_empty_pattern_raises(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="code", context_field="code",
                match_strategy=MatchStrategy.CONTEXT_REGEX,
                regex_pattern="",
            )

    def test_regex_pattern_forbidden_on_non_regex(self):
        with pytest.raises(ValueError, match="regex_pattern"):
            Dimension(
                dimension_name="x", context_field="x",
                match_strategy=MatchStrategy.EXACT,
                regex_pattern="^foo",
            )


class TestSetStrategyValidation:
    def test_set_membership_accepts_any_type(self):
        d = Dimension(
            dimension_name="x", context_field="x",
            match_strategy=MatchStrategy.SET_MEMBERSHIP,
        )
        assert d.match_strategy == MatchStrategy.SET_MEMBERSHIP

    def test_set_exclusion_accepts_any_type(self):
        d = Dimension(
            dimension_name="x", context_field="x",
            match_strategy=MatchStrategy.SET_EXCLUSION,
        )
        assert d.match_strategy == MatchStrategy.SET_EXCLUSION


class TestDimensionMetadataValidation:
    def test_dimension_requires_declared_context_field(self):
        with pytest.raises(ValueError, match="Undeclared context field"):
            DimensionsMetadata(
                fields=(),
                dimensions=(Dimension(dimension_name="x", context_field="x"),),
            )

    def test_range_requires_both_rule_endpoints(self):
        from mountainash_rules import ContextField

        with pytest.raises(ValueError, match="requires both endpoints"):
            DimensionsMetadata(
                fields=(ContextField(name="x", data_type="int"),),
                dimensions=(Dimension(
                    dimension_name="x", context_field="x",
                    match_strategy=MatchStrategy.RANGE, range_min_field="min",
                ),),
            )

    def test_declared_field_binds_dimension_type(self):
        field = ContextField(name="x", data_type="int")
        metadata = DimensionsMetadata(
            fields=(field,),
            dimensions=(Dimension(dimension_name="x", context_field="x"),),
        )
        assert metadata.dimensions[0].field == field


@pytest.mark.parametrize("strategy", [MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION])
def test_boolean_sets_are_declarable_without_broadening_filter(strategy):
    import polars as pl
    from mountainash_rules import ExpressionRulesEngine

    dimension = Dimension(
        dimension_name="flags", context_field="flags", match_strategy=strategy
    )
    metadata = DimensionsMetadata(
        fields=(ContextField(name="flags", data_type="bool", wildcard=None, wildcard_declared=True),),
        dimensions=(dimension,),
    )
    with pytest.raises(ValueError):
        ExpressionRulesEngine(pl.DataFrame({"flags": [[False], [True]]}), metadata)
