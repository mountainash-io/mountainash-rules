"""
Real Data Integration Tests for Phase 4 

This test module validates that the real data infrastructure works correctly
with all Mountain Ash engines, ensuring 100% real testing without mock objects.

Key Features:
- Tests real BaseDataFrame creation with DataFrameFactory
- Validates real engine initialization with business rule data  
- Ensures real context evaluation with mathematical verification
- No Mock() objects - 100% production-ready testing
"""

import pytest
import time
import statistics
from typing import List, Dict, Any

from mountainash_utils_rules import (
    RulesEngine,
    DimensionsMetadata, 
    Dimension, 
    MatchStrategy,
    create_ultra_performance_engine,
    create_performance_optimized_engine
)
from mountainash_utils_rules.constants import RuleConstants, RuleTrinaryFlags
from mountainash_dataframes.utils.dataframe_filters import FilterCondition as fc

import sys
import os
sys.path.append(os.path.dirname(__file__))
from real_data_infrastructure import (
    RealRuleDatasets,
    RealContextModels,
    RealBusinessDataGenerator,
    RealDataFrameFactory,
    RealMathematicalValidator
)


class TestRealDataIntegration:
    """Integration tests for real data infrastructure with all engines."""
    
    def test_real_customer_rules_dataframe_creation(self):
        """Test creation of real customer rules BaseDataFrame."""
        # Create real customer rules
        customer_rules = RealDataFrameFactory.create_customer_rules_dataframe()
        
        # Validate real BaseDataFrame properties
        assert customer_rules is not None, "Customer rules DataFrame should be created"
        assert customer_rules.count() == 8, "Should have 8 real customer segmentation rules"
        
        # Validate real rule structure
        rule_names = customer_rules.get_column_as_list('rule_name')
        assert 'premium_customer_high_value' in rule_names, "Should contain real premium customer rule"
        assert 'vip_customer_exclusive' in rule_names, "Should contain real VIP customer rule"
        
        # Validate real data types
        annual_spends = customer_rules.get_column_as_list('annual_spend_min')
        assert all(isinstance(spend, int) for spend in annual_spends), "Annual spend should be real integers"
        
    def test_real_product_rules_dataframe_creation(self):
        """Test creation of real product rules BaseDataFrame."""
        # Create real product rules
        product_rules = RealDataFrameFactory.create_product_rules_dataframe()
        
        # Validate real BaseDataFrame properties
        assert product_rules is not None, "Product rules DataFrame should be created"
        assert product_rules.count() == 8, "Should have 8 real product pricing rules"
        
        # Validate real rule structure
        categories = product_rules.get_column_as_list('category')
        assert 'ELECTRONICS' in categories, "Should contain real electronics category"
        assert 'SOFTWARE' in categories, "Should contain real software category"
        
    def test_real_financial_rules_dataframe_creation(self):
        """Test creation of real financial rules BaseDataFrame."""
        # Create real financial rules
        financial_rules = RealDataFrameFactory.create_financial_rules_dataframe()
        
        # Validate real BaseDataFrame properties
        assert financial_rules is not None, "Financial rules DataFrame should be created"
        assert financial_rules.count() == 8, "Should have 8 real financial risk rules"
        
        # Validate real rule structure
        risk_categories = financial_rules.get_column_as_list('risk_category')
        assert 'HIGH' in risk_categories, "Should contain real high-risk category"
        assert 'SUSPICIOUS' in risk_categories, "Should contain real suspicious category"


class TestRealEngineIntegration:
    """Test real engine integration with business rule scenarios."""
    
    @pytest.fixture
    def real_customer_rules(self):
        """Fixture providing real customer segmentation rules."""
        return RealDataFrameFactory.create_customer_rules_dataframe()
    
    @pytest.fixture
    def real_customer_dimensions(self):
        """Fixture providing real customer dimension metadata."""
        return DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="customer_tier", 
                match_strategy=MatchStrategy.EXACT, 
                data_type=str
            ),
            Dimension(
                dimension_name="annual_spend", 
                match_strategy=MatchStrategy.RANGE, 
                data_type=int,
                range_min_field="annual_spend_min", 
                range_max_field="annual_spend_max"
            ),
            Dimension(
                dimension_name="region", 
                match_strategy=MatchStrategy.REGEX, 
                data_type=str,
                regex_field="region_pattern"
            )
        ])
    
    def test_standard_engine_real_customer_segmentation(self, real_customer_rules, real_customer_dimensions):
        """Test standard RulesEngine with real customer segmentation scenarios."""
        # Create real engine
        engine = RulesEngine(
            rules=real_customer_rules, 
            dimension_metadata=real_customer_dimensions
        )
        
        # Test real premium customer scenario
        premium_context = RealContextModels.CustomerContext(
            customer_tier="PREMIUM",
            annual_spend=25000,
            region="US-WEST-001"
        )
        
        result = engine.apply_context_rules_engine(
            premium_context, 
            ["customer_tier", "annual_spend", "region"]
        )
        
        # Real mathematical validation
        matching_rules = result.filter(filter_condition=fc.eq("keep", True))
        assert matching_rules.count() == 1, "Should match exactly 1 premium rule"
        
        matched_rule_name = matching_rules.get_first_row_as_dict()['rule_name']
        assert matched_rule_name == "premium_customer_high_value", f"Expected premium rule, got {matched_rule_name}"
        
        # Validate real prime-based ternary logic
        validator = RealMathematicalValidator()
        assert validator.validate_rule_matches(
            premium_context, result, ["premium_customer_high_value"]
        ), "Mathematical validation should pass"
    
    def test_standard_engine_real_vip_customer_scenario(self, real_customer_rules, real_customer_dimensions):
        """Test standard engine with real VIP customer scenario."""
        engine = RulesEngine(
            rules=real_customer_rules, 
            dimension_metadata=real_customer_dimensions
        )
        
        # Test real VIP customer scenario  
        vip_context = RealContextModels.CustomerContext(
            customer_tier="VIP",
            annual_spend=75000,
            region="GLOBAL-VIP-001"
        )
        
        result = engine.apply_context_rules_engine(
            vip_context, 
            ["customer_tier", "annual_spend", "region"]
        )
        
        # Real mathematical validation
        matching_rules = result.filter(filter_condition=fc.eq("keep", True))
        assert matching_rules.count() == 1, "Should match exactly 1 VIP rule"
        
        matched_rule_name = matching_rules.get_first_row_as_dict()['rule_name']
        assert matched_rule_name == "vip_customer_exclusive", f"Expected VIP rule, got {matched_rule_name}"
    
    def test_multiple_real_customer_scenarios(self, real_customer_rules, real_customer_dimensions):
        """Test engine with multiple real customer scenarios."""
        engine = RulesEngine(
            rules=real_customer_rules, 
            dimension_metadata=real_customer_dimensions
        )
        
        # Generate realistic customer scenarios
        real_scenarios = RealBusinessDataGenerator.generate_customer_scenarios(12)
        
        successful_evaluations = 0
        for scenario in real_scenarios:
            try:
                result = engine.apply_context_rules_engine(
                    scenario, 
                    ["customer_tier", "annual_spend", "region"]
                )
                
                # Validate real evaluation
                assert result is not None, "Result should not be None"
                assert result.count() == 8, "Should evaluate all 8 rules"
                
                # Count successful matches
                matching_count = result.filter(filter_condition=fc.eq("keep", True)).count()
                if matching_count > 0:
                    successful_evaluations += 1
                    
            except Exception as e:
                pytest.fail(f"Real scenario evaluation failed: {e}")
        
        # Validate that most scenarios produce matches
        success_rate = successful_evaluations / len(real_scenarios)
        assert success_rate >= 0.7, f"Success rate {success_rate:.2%} should be at least 70%"


class TestRealPerformanceValidation:
    """Test real performance characteristics with business data."""
    
    @pytest.fixture
    def real_performance_dataset(self):
        """Create realistic performance testing dataset."""
        return {
            'rules': RealDataFrameFactory.create_customer_rules_dataframe(),
            'dimensions': DimensionsMetadata(dimensions=[
                Dimension(
                    dimension_name="customer_tier", 
                    match_strategy=MatchStrategy.EXACT, 
                    data_type=str
                ),
                Dimension(
                    dimension_name="annual_spend", 
                    match_strategy=MatchStrategy.RANGE, 
                    data_type=int,
                    range_min_field="annual_spend_min", 
                    range_max_field="annual_spend_max"
                ),
                Dimension(
                    dimension_name="region", 
                    match_strategy=MatchStrategy.REGEX, 
                    data_type=str,
                    regex_field="region_pattern"
                )
            ]),
            'contexts': RealBusinessDataGenerator.generate_customer_scenarios(50)
        }
    
    def test_real_performance_standard_engine(self, real_performance_dataset):
        """Test real performance characteristics of standard engine."""
        # Create real standard engine
        standard_engine = RulesEngine(
            rules=real_performance_dataset['rules'],
            dimension_metadata=real_performance_dataset['dimensions']
        )
        
        # Real performance measurement
        execution_times = []
        contexts = real_performance_dataset['contexts'][:10]  # Use subset for unit test
        
        for _ in range(3):  # Multiple runs for statistical validity
            start_time = time.time()
            
            for context in contexts:
                result = standard_engine.apply_context_rules_engine(
                    context, 
                    ["customer_tier", "annual_spend", "region"]
                )
                # Force evaluation
                actual_count = result.count()
                assert actual_count == 8, "Should evaluate all rules"
            
            execution_time = (time.time() - start_time) * 1000  # Convert to ms
            execution_times.append(execution_time)
        
        # Real statistical analysis
        avg_time = statistics.mean(execution_times)
        std_dev = statistics.stdev(execution_times) if len(execution_times) > 1 else 0
        
        # Validate reasonable performance
        assert avg_time < 1000, f"Average time {avg_time:.2f}ms should be under 1 second"
        assert std_dev / avg_time < 0.5, "Performance should be consistent"
        
        print(f"Standard Engine Performance: {avg_time:.2f}ms ± {std_dev:.2f}ms")
    
    def test_real_mathematical_prime_validation(self):
        """Test real mathematical validation of prime-based ternary logic."""
        validator = RealMathematicalValidator()
        
        # Test real prime number validation
        real_flags = [
            RuleTrinaryFlags.PRIME_TRUE,    # 2
            RuleTrinaryFlags.PRIME_FALSE,   # 3
            RuleTrinaryFlags.PRIME_UNKNOWN  # 5
        ]
        
        assert validator.validate_prime_ternary_logic(real_flags), "Prime flags should be mathematically valid"
        
        # Test invalid flags
        invalid_flags = [1, 4, 6, 8, 9, 10]  # Non-prime or non-ternary numbers
        assert not validator.validate_prime_ternary_logic(invalid_flags), "Invalid flags should be rejected"
        
    def test_real_performance_comparison_validation(self):
        """Test real performance comparison mathematical validation."""
        validator = RealMathematicalValidator()
        
        # Real performance comparison scenario
        baseline_time = 100.0  # ms
        optimized_time = 25.0  # ms (75% improvement)
        
        validation_result = validator.validate_performance_improvement(
            baseline_time, 
            optimized_time, 
            expected_improvement=0.5  # 50% minimum improvement
        )
        
        assert validation_result['valid'], "Performance improvement should be mathematically valid"
        assert validation_result['improvement_percentage'] == 75.0, "Should calculate 75% improvement"
        assert validation_result['speedup_factor'] == 4.0, "Should calculate 4x speedup"
        assert validation_result['meets_expectation'], "Should meet 50% improvement expectation"


class TestRealEdgeCases:
    """Test real edge cases with genuine business data patterns."""
    
    def test_real_unknown_value_handling(self):
        """Test real unknown value handling with business scenarios."""
        # Create real rules with unknown patterns
        rules = RealDataFrameFactory.create_customer_rules_dataframe()
        dimensions = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="customer_tier", 
                match_strategy=MatchStrategy.EXACT, 
                data_type=str
            ),
            Dimension(
                dimension_name="annual_spend", 
                match_strategy=MatchStrategy.RANGE, 
                data_type=int,
                range_min_field="annual_spend_min", 
                range_max_field="annual_spend_max"
            ),
            Dimension(
                dimension_name="region", 
                match_strategy=MatchStrategy.REGEX, 
                data_type=str,
                regex_field="region_pattern"
            )
        ])
        
        engine = RulesEngine(rules=rules, dimension_metadata=dimensions)
        
        # Test context with unknown values
        unknown_context = RealContextModels.CustomerContext(
            customer_tier=RuleConstants.UNKNOWN,
            annual_spend=RuleConstants.UNKNOWN_NUMERIC,
            region=RuleConstants.UNKNOWN
        )
        
        result = engine.apply_context_rules_engine(
            unknown_context, 
            ["customer_tier", "annual_spend", "region"]
        )
        
        # Should match all rules when all values are unknown
        matching_rules = result.filter(filter_condition=fc.eq("keep", True))
        assert matching_rules.count() == 8, "All rules should match when context is unknown"
    
    def test_real_empty_context_handling(self):
        """Test handling of contexts with missing fields."""
        rules = RealDataFrameFactory.create_customer_rules_dataframe()
        dimensions = DimensionsMetadata(dimensions=[
            Dimension(
                dimension_name="customer_tier", 
                match_strategy=MatchStrategy.EXACT, 
                data_type=str
            )
        ])
        
        engine = RulesEngine(rules=rules, dimension_metadata=dimensions)
        
        # Test with minimal context
        minimal_context = RealContextModels.CustomerContext(
            customer_tier="PREMIUM",
            annual_spend=25000,  # Not used in evaluation
            region="US-WEST"     # Not used in evaluation
        )
        
        result = engine.apply_context_rules_engine(
            minimal_context, 
            ["customer_tier"]  # Only evaluate customer_tier
        )
        
        # Should evaluate successfully
        assert result is not None, "Should handle minimal context"
        assert result.count() == 8, "Should evaluate all rules"
        
        # Should match premium rule
        matching_rules = result.filter(filter_condition=fc.eq("keep", True))
        assert matching_rules.count() == 1, "Should match premium customer rule"


if __name__ == "__main__":
    # Run integration tests for manual validation
    pytest.main([__file__, "-v"])