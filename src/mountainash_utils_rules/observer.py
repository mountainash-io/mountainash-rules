from typing import Any, Dict, Type

from mountainash_data import BaseDataFrame
from mountainash_utils_rules.dimension import Dimension



# Observability Manager
class ObservabilityManager:
    def __init__(self):
        
        self.intermediate_values = {}
        self.warnings = {}

    def log_intermediate_values(self, dimension_name: str, values: Dict):
        self.intermediate_values[dimension_name] = values

    def log_warning(self, dimension_name: str, warning_type: str, message: str):
        if dimension_name not in self.warnings:
            self.warnings[dimension_name] = {}
        self.warnings[dimension_name][warning_type] = message



    def _log_context_cast_warning(self, dimension_name: str, context_value: Any, context_type: Type, target_type: str) -> None:
        """
        Log a warning for a context value that is not of the correct type.

        Args:
            dimension_name (str): The name of the dimension
            context_value (Any): The context value
            context_type (Type): The type of the context value
            target_type (str): The target type for the context value
        """
        if dimension_name not in self.warnings:
            self.warnings[dimension_name] = {}

        self.warnings[dimension_name]["context_cast"] = f"Context value {context_value} of type {context_type} has been cast to {target_type} for dimension {dimension_name}"



    def save_dimension_intermediate_values(self, rules: BaseDataFrame, dimension: Dimension) -> None:
        
        """
        Save the intermediate values for a dimension.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object
        """

        self.intermediate_values[dimension.dimension_name] = rules.select([
            # 'rule_name',
            'dimension_filter_product',
            'dimension_any_false',
            'dimension_any_true',
            'cumu_dimension_count',
            'cumu_soft_match_count',
            'cumu_hard_match_count',
            'dropped',
            'dropped_by_dimension'
        ])        
