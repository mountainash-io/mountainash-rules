

from typing import List, Any,Optional, Dict, Type

import ibis
import ibis.expr.types as ir
from ibis.common.deferred import Deferred
from ibis.common.exceptions import IbisTypeError


from mountainash_data import BaseDataFrame, DataFrameFactory
import re
from pydantic import BaseModel
from enum import Enum
from mountainash_utils_rules.constants import RuleType, RuleConstants

# Tracability Manager
class TracabilityManager:
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
        """
        if dimension_name not in self.warnings:
            self.warnings[dimension_name] = {}

        self.warnings[dimension_name]["context_cast"] = f"Context value {context_value} of type {context_type} has been cast to {target_type} for dimension {dimension_name}"



    def _save_dimension_intermediate_values(self, rules: BaseDataFrame, dimension_name: str) -> None:

        self.intermediate_values[dimension_name] = rules.select([
            'rule_name',
            'dimension_filter_product',
            'dimension_any_false',
            'dimension_any_true',
            'cumu_dimension_count',
            'cumu_soft_match_count',
            'cumu_hard_match_count',
            'dropped',
            'dropped_by_dimension'
        ])        
