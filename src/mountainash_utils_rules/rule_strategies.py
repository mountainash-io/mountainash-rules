

from typing import  Any

import ibis
import ibis.expr.types as ir
from ibis.common.deferred import Deferred
from ibis.common.exceptions import IbisTypeError


from mountainash_data import BaseDataFrame, DataFrameFactory
import re
from pydantic import BaseModel
from enum import Enum
from abc import ABC, abstractmethod

from mountainash_utils_rules.constants import RuleType, RuleConstants
from mountainash_utils_rules.metadata import RuleMetadata, MetadataManager, DimensionMetadata

# import operator 






class RuleTypeStrategy(ABC):

    rule_type: RuleType

    @abstractmethod
    def apply_match_filter(self, 
                           rules: BaseDataFrame, 
                           dimension: DimensionMetadata, 
                           context_value: Any) -> BaseDataFrame:
        pass


    def apply_filter_rule_unknown(self, 
                                    rules: BaseDataFrame,  
                                    dimension: DimensionMetadata) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        # rule_type = self.get_dimension_rule_type()

        if self.rule_type == RuleType.RANGE:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_range_min_field()
        else:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()

        rules = rules.mutate(

            filter_rule_unknown = ibis.ifelse(condition=ibis._[dimension_rule_fieldname].cast('string') == ibis.literal(RuleConstants.UNKNOWN), 
                                true_expr=ibis.literal(RuleConstants.PRIME_TRUE), 
                                false_expr=ibis.literal(RuleConstants.PRIME_UNKNOWN) ),
        )

        return rules


    def apply_filter_context_unknown(self, 
                                        rules: BaseDataFrame,  
                                        context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        #cast the context value to aplain python string
        context_value = str(context_value)            

        if context_value == RuleConstants.UNKNOWN:
            rules = rules.mutate(
                filter_context_unknown = ibis.literal(RuleConstants.PRIME_TRUE)
            )
        else:
            rules = rules.mutate(
                filter_context_unknown = ibis.literal(RuleConstants.PRIME_UNKNOWN)
            )

        return rules



class ExactMatchStrategy(RuleTypeStrategy):


    rule_type: RuleType = RuleType.EXACT

    def apply_match_filter(self, 
                                   rules: BaseDataFrame,  
                                   dimension: DimensionMetadata,  
                                   context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        target_type: str = dimension.get_dimension_data_type()
        dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()

        


        try:
            # self._log_context_cast_warning(dimension=dimension, context_value=context_value, context_type=type(context_value))

            context_value_cast = ibis.literal(context_value).cast(target_type)
        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(RuleConstants.PRIME_FALSE))
            return rules


        #Filter 3 is a direct comparison of the context value to the rule value

        try:
            rules = rules.mutate(
                filter_match = ibis.ifelse(condition= ibis._[dimension_rule_fieldname].cast(target_type) == context_value_cast, 
                                    true_expr=ibis.literal(RuleConstants.PRIME_TRUE), 
                                    false_expr=ibis.literal(RuleConstants.PRIME_FALSE) )
            )

            return rules

        except (Exception,IbisTypeError):
            raise ValueError(f"Could not cast rule field {dimension_rule_fieldname} to {target_type} in _apply_filter_exact_match() for dimension {dimension.dimension_name}")            
        


class RegexMatchStrategy(RuleTypeStrategy):

    rule_type: RuleType = RuleType.REGEX

    def apply_match_filter(self, 
                           rules: BaseDataFrame,  
                           dimension: DimensionMetadata,  
                           context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """



        try:
            context_value_cast = ibis.literal(value=context_value).cast("string")
        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(RuleConstants.PRIME_FALSE))
            return rules

        try:

            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()

            rules = rules.mutate(
                context_value = context_value_cast
            ).mutate(
                filter_match = ibis.ifelse(
                    condition= ibis._.context_value.re_search(ibis._[dimension_rule_fieldname]),
                    true_expr=ibis.literal(RuleConstants.PRIME_TRUE),
                    false_expr=ibis.literal(RuleConstants.PRIME_FALSE)
                )
            ).drop( columns="context_value")

        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(RuleConstants.PRIME_FALSE))

        return rules



class RangeMatchStrategy(RuleTypeStrategy):

    rule_type: RuleType = RuleType.RANGE

    def apply_match_filter(self, 
                           rules: BaseDataFrame,  
                           dimension: DimensionMetadata,  
                           context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        target_type: str = dimension.get_dimension_data_type()
        
        try:
            context_value_cast = ibis.literal(context_value).cast(target_type)
        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(RuleConstants.PRIME_FALSE))
            return rules

        try:

            min_field: str = dimension.get_dimension_rule_range_min_field()
            max_field: str = dimension.get_dimension_rule_range_max_field()

            min_inclusive: bool = dimension.get_dimension_rule_range_min_inclusive()
            max_inclusive: bool = dimension.get_dimension_rule_range_max_inclusive()

            #Use the ibis deferred operators
            min_op = Deferred.__le__ if min_inclusive else Deferred.__lt__
            max_op = Deferred.__ge__ if max_inclusive else Deferred.__gt__


            condition = (
                (ibis._[min_field].isnull() | min_op(ibis._[min_field], context_value_cast)) &
                (ibis._[max_field].isnull() | max_op(ibis._[max_field], context_value_cast))
            )


            rules = rules.mutate(
                filter_match = ibis.ifelse(
                    condition=condition,
                    true_expr=ibis.literal(RuleConstants.PRIME_TRUE),
                    false_expr=ibis.literal(RuleConstants.PRIME_FALSE)
                )
            )


        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(RuleConstants.PRIME_FALSE))

        return rules


# Rule Type Factory
class RuleTypeFactory:
    
    @staticmethod
    def get_rule_strategy_class(rule_type: RuleType) -> RuleTypeStrategy:
        if rule_type == RuleType.EXACT:
            return ExactMatchStrategy()
        elif rule_type == RuleType.REGEX:
            return RegexMatchStrategy()
        elif rule_type == RuleType.RANGE:
            return RangeMatchStrategy()
        else:
            raise ValueError(f"Invalid rule type: {rule_type}")