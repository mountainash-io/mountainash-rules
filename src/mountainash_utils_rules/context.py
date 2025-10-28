from typing import List,Type,Dict

from mountainash_utils_rules.constants import RuleConstants
from mountainash_utils_rules.dimension import Dimension

class ContextHelper:

    ALLOWED_CONTEXT_TYPES: List[Type] = [str, int, float, bool, type(None)]        

    @classmethod
    def get_all_context_values(cls, context, dimensions: List[Dimension]) -> Dict[str, str|int|float]:
        """
        Extract all context values for the given dimensions in a single batch operation.
        This eliminates redundant context value extraction across multiple strategy calls.

        Args:
            context: The context object
            dimensions (List[Dimension]): List of dimension objects

        Returns:
            Dict[str, str|int|float]: Dictionary mapping dimension names to their context values
        """
        context_values = {}
        
        for dimension in dimensions:
            try:
                context_value = cls.get_context_value(context=context, dimension=dimension)
                context_values[dimension.dimension_name] = context_value
            except Exception:
                # If extraction fails for any dimension, use appropriate default
                dimension_type = dimension.get_dimension_data_type()
                if dimension_type is str:
                    context_values[dimension.dimension_name] = RuleConstants.NOT_SET
                elif dimension_type in [int, float, bool]:
                    context_values[dimension.dimension_name] = RuleConstants.NOT_SET_NUMERIC
                else:
                    context_values[dimension.dimension_name] = RuleConstants.NOT_SET
        
        return context_values

    @classmethod
    def get_context_value(cls, context, dimension: Dimension) -> str|int|float:
        """
        Get the value of the context field for a given dimension.

        We want to be somewhat flexible and forgiving with the context values, so we will return a string representation of the value if it is not a string, int or float.
        This is more likely to be defined at runtime, so we will not enforce strict typing here.
        If the context value is invalid or none, we will set the NOT_SET flag

        Args:
            context: The context object
            dimension (Dimension): The dimension object

        Returns:
            str|int|float: The value of the context field

        """

        dimension_type: Type = dimension.get_dimension_data_type()
        context_fieldname = dimension.get_dimension_context_fieldname()
        context_type = type(getattr(context, context_fieldname))

        if context_type not in cls.ALLOWED_CONTEXT_TYPES:
            context_value = RuleConstants.NOT_SET

        elif context_type is str:
            context_value = getattr(context, dimension.get_dimension_context_fieldname(), RuleConstants.NOT_SET)

        elif context_type in [int, float]:
            context_value = getattr(context, dimension.get_dimension_context_fieldname(), RuleConstants.NOT_SET_NUMERIC)

        elif context_type in [bool]:
            context_value = int(getattr(context, dimension.get_dimension_context_fieldname(), RuleConstants.NOT_SET_NUMERIC))

        # Use dimension types otherwise - ie is None
        elif dimension_type is str:
            context_value = RuleConstants.NOT_SET
        elif dimension_type in [int, float, bool]:
            context_value = RuleConstants.NOT_SET_NUMERIC
        
        else:
            context_value = RuleConstants.NOT_SET

        return context_value
    

    @classmethod
    def check_context_and_dimension_types_match(cls, context, dimension: Dimension) -> bool:
        """
        Check if the context and dimension types match.

        Args:
            context: The context object
            dimension (Dimension): The dimension object

        Returns:
            bool: True if the types match, False otherwise
        """

        dimension_type = dimension.get_dimension_data_type()
        context_type = type(getattr(context, dimension.get_dimension_context_fieldname()))

        return dimension_type == context_type