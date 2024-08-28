

from typing import List,Type

import ibis
import ibis.expr.types as ir
from ibis.common.deferred import Deferred
from ibis.common.exceptions import IbisTypeError


from mountainash_data import BaseDataFrame, DataFrameFactory
import re
from pydantic import BaseModel
from enum import Enum
from mountainash_utils_rules.constants import RuleType, RuleConstants
from mountainash_utils_rules.metadata import DimensionMetadata


# import operator 

# Context Manager
class ContextManager:
                             
    def __init__(self):

        self.ALLOWED_CONTEXT_TYPES: List[Type] = [str, int, float, bool, type(None)]


    def validate_context(self, context: BaseModel, active_dimensions: List[DimensionMetadata]) -> None:
        """
        Validate the types of the context fields.
        """

        # Validate context
        if not isinstance(context, BaseModel):
            raise ValueError("Context must be a Pydantic BaseModel")


        context_types = {dimension.dimension_name: type(getattr(context, dimension.get_dimension_context_fieldname())) for dimension in active_dimensions}

        for dimension_name, fieldtype in context_types.items():
            if fieldtype not in self.ALLOWED_CONTEXT_TYPES:
                raise TypeError(f"Context Field {dimension_name} is of type {fieldtype}, but only {self.ALLOWED_CONTEXT_TYPES} are allowed.")

