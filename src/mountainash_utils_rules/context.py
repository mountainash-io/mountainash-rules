from typing import List,Type

from mountainash_utils_rules.constants import RuleConstants
from mountainash_utils_rules.dimension import Dimension

class ContextHelper:

    ALLOWED_CONTEXT_TYPES: List[Type] = [str, int, float, bool, type(None)]        



    @classmethod
    def get_context_value(cls, context, dimension: Dimension) -> str|int|float:
        """
        Get the value of the context field for a given dimension.

        We want to be somewhat flexible and forgiving with the context values, so we will return a string representation of the value if it is not a string, int or float.
        This is more likely to be defined at runtime, so we will not enforce strict typing here.
        If the context value is invalid or none, we will set the NOT_SET flag
        """

        dimension_type: Type = dimension.get_dimension_data_type()
        context_fieldname = dimension.get_dimension_context_fieldname()
        context_type = type(getattr(context, context_fieldname))

        if context_type not in cls.ALLOWED_CONTEXT_TYPES:
            context_value = RuleConstants.NOT_SET
            print(f"1. Context Field {dimension.dimension_name} is of type {context_type}, but only {cls.ALLOWED_CONTEXT_TYPES} are allowed. Value set to {context_value}")

        elif context_type is str:
            context_value = getattr(context, dimension.get_dimension_context_fieldname(), RuleConstants.NOT_SET)
            print(f"2. Context Field {dimension.dimension_name} is of type {context_type}, value set to {context_value}")

        elif context_type in [int, float]:
            context_value = getattr(context, dimension.get_dimension_context_fieldname(), RuleConstants.NOT_SET_NUMERIC)
            print(f"3. Context Field {dimension.dimension_name} is of type {context_type}, value set to {context_value}")

        elif context_type in [bool]:
            context_value = int(getattr(context, dimension.get_dimension_context_fieldname(), RuleConstants.NOT_SET_NUMERIC))
            print(f"4. Context Field {dimension.dimension_name} is of type {context_type}, value set to {context_value}")

        # Use dimension types otherwise - ie is None
        elif dimension_type is str:
            context_value = RuleConstants.NOT_SET
            print(f"5. Context Field {dimension.dimension_name} is of type {context_type}, value set to {context_value} via dimension type: {dimension_type}")
        elif dimension_type in [int, float, bool]:
            context_value = RuleConstants.NOT_SET_NUMERIC
            print(f"6. Context Field {dimension.dimension_name} is of type {context_type}, value set to {context_value} via dimension type: {dimension_type}")
        
        else:
            context_value = RuleConstants.NOT_SET
            print(f"7. Context Field {dimension.dimension_name} is of type {context_type}, value set to {context_value} via dimension type: {dimension_type}")

        return context_value
    

    @classmethod
    def check_context_and_dimension_types_match(cls, context, dimension: Dimension) -> bool:
        """
        Check if the context and dimension types match.
        """

        dimension_type = dimension.get_dimension_data_type()
        context_type = type(getattr(context, dimension.get_dimension_context_fieldname()))

        return dimension_type == context_type