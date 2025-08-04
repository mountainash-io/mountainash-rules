"""Tests for mountainash_utils_rules.constants module."""

import pytest
import ibis
from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags


class TestMatchStrategy:
    """Test suite for MatchStrategy enum."""

    def test_match_strategy_enum_values(self):
        """Test that all MatchStrategy enum values are correct."""
        assert MatchStrategy.EXACT == "EXACT"
        assert MatchStrategy.RANGE == "RANGE"
        assert MatchStrategy.REGEX == "REGEX"

    def test_match_strategy_enum_membership(self):
        """Test MatchStrategy enum membership."""
        assert MatchStrategy.EXACT in MatchStrategy
        assert MatchStrategy.RANGE in MatchStrategy
        assert MatchStrategy.REGEX in MatchStrategy

    def test_match_strategy_enum_count(self):
        """Test that MatchStrategy has expected number of values."""
        assert len(list(MatchStrategy)) == 3

    def test_match_strategy_enum_equality(self):
        """Test MatchStrategy enum equality comparisons."""
        assert MatchStrategy.EXACT == MatchStrategy.EXACT
        assert MatchStrategy.EXACT != MatchStrategy.RANGE
        assert MatchStrategy.RANGE != MatchStrategy.REGEX

    def test_match_strategy_enum_string_representation(self):
        """Test MatchStrategy string representations."""
        assert str(MatchStrategy.EXACT) == "MatchStrategy.EXACT"
        assert str(MatchStrategy.RANGE) == "MatchStrategy.RANGE"
        assert str(MatchStrategy.REGEX) == "MatchStrategy.REGEX"

    def test_match_strategy_enum_iteration(self):
        """Test iteration over MatchStrategy enum."""
        strategies = list(MatchStrategy)
        expected = [MatchStrategy.EXACT, MatchStrategy.RANGE, MatchStrategy.REGEX]
        assert strategies == expected


class TestRuleConstants:
    """Test suite for RuleConstants class."""

    def test_rule_constants_string_values(self):
        """Test RuleConstants string constant values."""
        assert RuleConstants.UNKNOWN == "<NA>"
        assert RuleConstants.NOT_SET == "<NOT_SET>"

    def test_rule_constants_numeric_values(self):
        """Test RuleConstants numeric constant values."""
        assert RuleConstants.UNKNOWN_NUMERIC == -999999999
        assert RuleConstants.NOT_SET_NUMERIC == -999999998

    def test_rule_constants_numeric_values_are_different(self):
        """Test that numeric constants are different values."""
        assert RuleConstants.UNKNOWN_NUMERIC != RuleConstants.NOT_SET_NUMERIC

    def test_unknown_ibis_method(self):
        """Test RuleConstants.UNKNOWN_IBIS() method."""
        result = RuleConstants.UNKNOWN_IBIS()
        assert isinstance(result, ibis.Scalar)
        # Verify the literal value is correct
        assert result.op().value == RuleConstants.UNKNOWN

    def test_not_set_ibis_method(self):
        """Test RuleConstants.NOT_SET_IBIS() method."""
        result = RuleConstants.NOT_SET_IBIS()
        assert isinstance(result, ibis.Scalar)
        assert result.op().value == RuleConstants.NOT_SET

    def test_unknown_numeric_ibis_method(self):
        """Test RuleConstants.UNKNOWN_NUMERIC_IBIS() method."""
        result = RuleConstants.UNKNOWN_NUMERIC_IBIS()
        assert isinstance(result, ibis.Scalar)
        assert result.op().value == RuleConstants.UNKNOWN_NUMERIC

    def test_not_set_numeric_ibis_method(self):
        """Test RuleConstants.NOT_SET_NUMERIC_IBIS() method."""
        result = RuleConstants.NOT_SET_NUMERIC_IBIS()
        assert isinstance(result, ibis.Scalar)
        assert result.op().value == RuleConstants.NOT_SET_NUMERIC

    def test_all_ibis_methods_return_different_values(self):
        """Test that all Ibis methods return different literal values."""
        unknown = RuleConstants.UNKNOWN_IBIS()
        not_set = RuleConstants.NOT_SET_IBIS()
        unknown_numeric = RuleConstants.UNKNOWN_NUMERIC_IBIS()
        not_set_numeric = RuleConstants.NOT_SET_NUMERIC_IBIS()

        # Extract the literal values for comparison
        values = [
            unknown.op().value,
            not_set.op().value,
            unknown_numeric.op().value,
            not_set_numeric.op().value
        ]

        # All values should be unique
        assert len(set(values)) == 4

    def test_ibis_methods_are_class_methods(self):
        """Test that Ibis methods can be called as class methods."""
        # These should not raise errors when called on the class
        RuleConstants.UNKNOWN_IBIS()
        RuleConstants.NOT_SET_IBIS()
        RuleConstants.UNKNOWN_NUMERIC_IBIS()
        RuleConstants.NOT_SET_NUMERIC_IBIS()

    def test_rule_constants_immutability(self):
        """Test that RuleConstants values behave as constants."""
        # These are class attributes, so they should be accessible
        original_unknown = RuleConstants.UNKNOWN
        original_not_set = RuleConstants.NOT_SET
        original_unknown_numeric = RuleConstants.UNKNOWN_NUMERIC
        original_not_set_numeric = RuleConstants.NOT_SET_NUMERIC

        # Values should remain consistent
        assert RuleConstants.UNKNOWN == original_unknown
        assert RuleConstants.NOT_SET == original_not_set
        assert RuleConstants.UNKNOWN_NUMERIC == original_unknown_numeric
        assert RuleConstants.NOT_SET_NUMERIC == original_not_set_numeric


class TestRuleTrinaryFlags:
    """Test suite for RuleTrinaryFlags class."""

    def test_rule_trinary_flags_values(self):
        """Test RuleTrinaryFlags constant values."""
        assert RuleTrinaryFlags.PRIME_TRUE == 2
        assert RuleTrinaryFlags.PRIME_FALSE == 3
        assert RuleTrinaryFlags.PRIME_UNKNOWN == 5

    def test_rule_trinary_flags_are_prime_numbers(self):
        """Test that trinary flag values are prime numbers."""
        def is_prime(n):
            if n < 2:
                return False
            for i in range(2, int(n ** 0.5) + 1):
                if n % i == 0:
                    return False
            return True

        assert is_prime(RuleTrinaryFlags.PRIME_TRUE)
        assert is_prime(RuleTrinaryFlags.PRIME_FALSE)
        assert is_prime(RuleTrinaryFlags.PRIME_UNKNOWN)

    def test_rule_trinary_flags_are_unique(self):
        """Test that all trinary flag values are unique."""
        values = [
            RuleTrinaryFlags.PRIME_TRUE,
            RuleTrinaryFlags.PRIME_FALSE,
            RuleTrinaryFlags.PRIME_UNKNOWN
        ]
        assert len(set(values)) == 3

    def test_prime_true_ibis_method(self):
        """Test RuleTrinaryFlags.PRIME_TRUE_IBIS() method."""
        result = RuleTrinaryFlags.PRIME_TRUE_IBIS()
        assert isinstance(result, ibis.Scalar)
        assert result.op().value == RuleTrinaryFlags.PRIME_TRUE

    def test_prime_false_ibis_method(self):
        """Test RuleTrinaryFlags.PRIME_FALSE_IBIS() method."""
        result = RuleTrinaryFlags.PRIME_FALSE_IBIS()
        assert isinstance(result, ibis.Scalar)
        assert result.op().value == RuleTrinaryFlags.PRIME_FALSE

    def test_prime_unknown_ibis_method(self):
        """Test RuleTrinaryFlags.PRIME_UNKNOWN_IBIS() method."""
        result = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()
        assert isinstance(result, ibis.Scalar)
        assert result.op().value == RuleTrinaryFlags.PRIME_UNKNOWN

    def test_all_ibis_trinary_methods_return_different_values(self):
        """Test that all trinary Ibis methods return different literal values."""
        prime_true = RuleTrinaryFlags.PRIME_TRUE_IBIS()
        prime_false = RuleTrinaryFlags.PRIME_FALSE_IBIS()
        prime_unknown = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()

        # Extract the literal values for comparison
        values = [
            prime_true.op().value,
            prime_false.op().value,
            prime_unknown.op().value
        ]

        # All values should be unique
        assert len(set(values)) == 3

    def test_trinary_ibis_methods_are_class_methods(self):
        """Test that trinary Ibis methods can be called as class methods."""
        # These should not raise errors when called on the class
        RuleTrinaryFlags.PRIME_TRUE_IBIS()
        RuleTrinaryFlags.PRIME_FALSE_IBIS()
        RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()

    def test_rule_trinary_flags_immutability(self):
        """Test that RuleTrinaryFlags values behave as constants."""
        original_true = RuleTrinaryFlags.PRIME_TRUE
        original_false = RuleTrinaryFlags.PRIME_FALSE
        original_unknown = RuleTrinaryFlags.PRIME_UNKNOWN

        # Values should remain consistent
        assert RuleTrinaryFlags.PRIME_TRUE == original_true
        assert RuleTrinaryFlags.PRIME_FALSE == original_false
        assert RuleTrinaryFlags.PRIME_UNKNOWN == original_unknown


class TestConstantsIntegration:
    """Integration tests across all constants classes."""

    def test_no_value_conflicts_between_classes(self):
        """Test that there are no value conflicts between different constant classes."""
        # Collect all numeric values from different classes
        rule_numerics = [RuleConstants.UNKNOWN_NUMERIC, RuleConstants.NOT_SET_NUMERIC]
        trinary_numerics = [
            RuleTrinaryFlags.PRIME_TRUE,
            RuleTrinaryFlags.PRIME_FALSE,
            RuleTrinaryFlags.PRIME_UNKNOWN
        ]

        # No numeric values should overlap between classes
        all_numerics = rule_numerics + trinary_numerics
        assert len(set(all_numerics)) == len(all_numerics)

    def test_string_constants_are_distinct(self):
        """Test that string constants are distinct and meaningful."""
        string_constants = [RuleConstants.UNKNOWN, RuleConstants.NOT_SET]

        # All should be different
        assert len(set(string_constants)) == len(string_constants)

        # All should be non-empty strings
        for constant in string_constants:
            assert isinstance(constant, str)
            assert len(constant) > 0

    def test_all_ibis_methods_work_together(self):
        """Test that all Ibis methods from all classes work together."""
        # Test that we can call all Ibis methods without errors
        rule_ibis = [
            RuleConstants.UNKNOWN_IBIS(),
            RuleConstants.NOT_SET_IBIS(),
            RuleConstants.UNKNOWN_NUMERIC_IBIS(),
            RuleConstants.NOT_SET_NUMERIC_IBIS()
        ]

        trinary_ibis = [
            RuleTrinaryFlags.PRIME_TRUE_IBIS(),
            RuleTrinaryFlags.PRIME_FALSE_IBIS(),
            RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()
        ]

        all_ibis = rule_ibis + trinary_ibis

        # All should be Ibis Scalar objects
        for ibis_obj in all_ibis:
            assert isinstance(ibis_obj, ibis.Scalar)

        # All should have distinct literal values
        values = [obj.op().value for obj in all_ibis]
        assert len(set(values)) == len(values)

    def test_constants_maintain_type_consistency(self):
        """Test that constants maintain consistent types."""
        # String constants should be strings
        assert isinstance(RuleConstants.UNKNOWN, str)
        assert isinstance(RuleConstants.NOT_SET, str)

        # Numeric constants should be integers
        assert isinstance(RuleConstants.UNKNOWN_NUMERIC, int)
        assert isinstance(RuleConstants.NOT_SET_NUMERIC, int)
        assert isinstance(RuleTrinaryFlags.PRIME_TRUE, int)
        assert isinstance(RuleTrinaryFlags.PRIME_FALSE, int)
        assert isinstance(RuleTrinaryFlags.PRIME_UNKNOWN, int)
