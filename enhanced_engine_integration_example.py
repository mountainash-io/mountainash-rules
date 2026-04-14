"""
Example: Enhanced Rules Engine with One-Shot Ternary Evaluation

This demonstrates how the Enhanced TernaryRuleProcessor could be integrated
into the main RulesEngine to provide dramatic performance improvements while
maintaining full compatibility with the existing API.
"""

from typing import List, Optional
from pydantic import BaseModel

from mountainash_dataframes import BaseDataFrame
from mountainash_utils_rules.dimension import DimensionsMetadata, MetadataManager
from mountainash_utils_rules.rule_manager import RuleManager
from mountainash_utils_rules.context import ContextHelper
from mountainash_utils_rules.enhanced_ternary_processor import EnhancedTernaryRuleProcessor


class EnhancedRulesEngine:
    """
    Enhanced Rules Engine with one-shot ternary evaluation.
    
    This engine provides a drop-in replacement for the original RulesEngine
    with dramatic performance improvements:
    - Reduces M+2 mutate() operations to 2-3 operations total
    - Eliminates intermediate column materialization  
    - Better query optimization through single complex expression
    - Maintains full API compatibility
    """

    def __init__(self,
                 rules: BaseDataFrame,
                 dimension_metadata: Optional[DimensionsMetadata] = None):
        
        # Initialize same components as original engine
        self.rule_manager = RuleManager(rules=rules)
        self.metadata_manager = MetadataManager(
            rules=self.rule_manager.rules,
            dimension_metadata=dimension_metadata
        )
        
        # Initialize enhanced ternary processor
        self.ternary_processor: Optional[EnhancedTernaryRuleProcessor] = None

    def apply_context_rules_engine(self,
                                 context: BaseModel,
                                 dimension_names: List[str] | str,
                                 keep_all: bool = True) -> BaseDataFrame:
        """
        Apply rules engine with one-shot ternary evaluation.
        
        Performance comparison:
        - Original: M+2 mutate() operations (M dimensions + init + priority)
        - Enhanced: 2-3 mutate() operations total
        
        Args:
            context: Pydantic model containing context values
            dimension_names: Dimension names to evaluate
            keep_all: Whether to keep all rules or only matching ones
            
        Returns:
            BaseDataFrame with evaluation results and 'keep' column
        """
        
        # Step 1: Same validation as original engine
        if isinstance(dimension_names, str):
            dimension_names = [dimension_names]
        
        if len(dimension_names) == 0:
            raise ValueError("No dimension names specified.")
        
        # Step 2: Get active dimensions (same as original)
        active_dimension_names = self.metadata_manager.get_active_dimension_names(
            context=context,
            rules=self.rule_manager.get_rules(),
            dimension_names=dimension_names
        )
        active_dimensions = self.metadata_manager.get_dimensions_list(
            dimension_names=active_dimension_names
        )
        
        # Step 3: Extract context values (same optimization as original)  
        context_values = ContextHelper.get_all_context_values(
            context=context,
            dimensions=active_dimensions
        )
        
        # Step 4: Initialize ternary processor if needed
        if self.ternary_processor is None:
            self.ternary_processor = EnhancedTernaryRuleProcessor(
                rules=self.rule_manager.get_rules(),
                dimensions=active_dimensions
            )
        
        # Step 5: ONE-SHOT EVALUATION - This is the key improvement!
        # Instead of M+2 mutate() calls, we do 1 complex evaluation
        result = self.ternary_processor.evaluate_context_one_shot(context_values)
        
        # Step 6: Apply filtering if requested (same as original)
        if not keep_all:
            # This would use the mountainash-dataframes filter syntax
            result = result.filter(result.keep == True)
        
        return result


# Example usage showing the performance improvement
def demonstrate_performance_improvement():
    """
    Example showing how the enhanced engine reduces complexity.
    """
    import polars as pl
    from mountainash_data import DataFrameFactory
    from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
    from mountainash_utils_rules.constants import MatchStrategy
    
    # Create sample rules
    rules_df = pl.DataFrame({
        "rule_name": ["rule_1", "rule_2", "rule_3"],
        "DIM_1": ["A", "B", "C"],
        "DIM_2_MIN": [0, 10, 20],
        "DIM_2_MAX": [9, 19, 29],
        "DIM_3": ["X.*", "Y.*", "Z.*"]
    })
    rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(
        rules_df, ibis_backend_schema="polars"
    )
    
    # Define dimensions
    dimension_metadata = DimensionsMetadata(dimensions=[
        Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int, 
                 range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
        Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    ])
    
    # Create context
    class Context(BaseModel):
        DIM_1: str
        DIM_2: int
        DIM_3: str
    
    context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
    
    # Performance comparison:
    print("=== Performance Comparison ===")
    print("Original Engine:")
    print("- Step 1: initialize_rule_flags() - 1 mutate()")
    print("- Step 2: For each dimension (3x):")
    print("  - apply_filter_rule_unknown() - 1 mutate()")  
    print("  - apply_filter_context_unknown() - 1 mutate()")
    print("  - apply_match_filter() - 1 mutate()")
    print("  - apply_dimension_filter_flags() - 1 mutate()")
    print("- Step 3: calculate_rule_priority() - 1 mutate()")
    print("- TOTAL: 1 + (3×4) + 1 = 14 mutate() operations")
    print()
    
    print("Enhanced Engine:")
    print("- Step 1: Build complex ternary expression")
    print("- Step 2: Single evaluation with metrics - 1 mutate()")
    print("- Step 3: Priority calculation - 1 mutate()")
    print("- TOTAL: 2 mutate() operations")
    print()
    print("Performance improvement: 14 → 2 operations (7x reduction)")


if __name__ == "__main__":
    demonstrate_performance_improvement()