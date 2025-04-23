from abc import ABC, abstractmethod

import ibis
import re
from ibis.common.deferred import Deferred
from ibis.common.exceptions import IbisTypeError

from pydantic import BaseModel

from mountainash_data import BaseDataFrame
from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags
from mountainash_utils_rules.dimension import Dimension
from mountainash_utils_rules.context import ContextHelper






class BaseMatchStrategy(ABC):

    """
    Base class for rule matching strategies.

    Attributes:
        match_strategy (MatchStrategy): The match strategy to use


    
    """
    match_strategy: MatchStrategy

    @abstractmethod
    def apply_match_filter(self, 
                           rules: BaseDataFrame, 
                           dimension: Dimension, 
                           context: BaseModel) -> BaseDataFrame:
        pass





    def apply_filter_rule_unknown(self, 
                                    rules: BaseDataFrame,  
                                    dimension: Dimension) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object 

        Returns:
            BaseDataFrame: The rules table with the filter rule applied
        """

        if self.match_strategy == MatchStrategy.RANGE:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_range_min_field()
        else:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()


        if dimension.get_dimension_data_type() == str:

            rules = rules.mutate(

                filter_rule_unknown = ibis.ifelse(ibis._[dimension_rule_fieldname] == RuleConstants.UNKNOWN_IBIS(), 
                                     RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                    RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()),
            )

        else:

            rules = rules.mutate(

                filter_rule_unknown = ibis.ifelse(ibis._[dimension_rule_fieldname].cast(int) == RuleConstants.UNKNOWN_NUMERIC_IBIS(), 
                                     RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                     RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()),
            )

        return rules


    def apply_filter_context_unknown(self, 
                                        rules: BaseDataFrame,
                                        dimension: Dimension,   
                                        context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object 
            context (BaseModel): The context object

        Returns:
            BaseDataFrame: The rules table with the filter rule applied

        """

        try:
            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)
 
        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_context_unknown = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
            return rules

        if context_value in [RuleConstants.UNKNOWN, RuleConstants.UNKNOWN_NUMERIC]:
            rules = rules.mutate(filter_context_unknown = RuleTrinaryFlags.PRIME_TRUE_IBIS())
        else:
            rules = rules.mutate(filter_context_unknown = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())

        return rules



class ExactMatchStrategy(BaseMatchStrategy):
    """
        Rule Strategy for Exact Matching
        Will match the context value exactly to the rule value
    
    """

    match_strategy: MatchStrategy = MatchStrategy.EXACT

    def apply_match_filter(self, 
                                   rules: BaseDataFrame,  
                                   dimension: Dimension,  
                                   context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object 
            context (BaseModel): The context object
        
        Returns:
            BaseDataFrame: The rules table with the filter rule applied
        """



        try:
            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)

        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
            return rules

        try:

            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()

            if dimension.get_dimension_data_type() == str:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value),
                ).mutate(
                    filter_match = ibis.ifelse(
                                        ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET) ,
                                        RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                        ibis.ifelse(
                                            ibis._[dimension_rule_fieldname] == ibis.literal(value=context_value), 
                                            RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                            RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                            ) 
                                    ))

            else:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value),
                ).mutate(
                    filter_match = ibis.ifelse(
                                        ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC),
                                        RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                        ibis.ifelse(
                                            ibis._[dimension_rule_fieldname] == ibis.literal(value=context_value), 
                                            RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                            RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                            ) 
                                    ))


        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
        
        return rules

class RegexMatchStrategy(BaseMatchStrategy):

    """
        Rule Strategy for Regular Expression Matching
        Will match the context value to the regular expression in the rule value
        The rule contains a regular expression, not the context! The context is a real world value.

    """

    match_strategy: MatchStrategy = MatchStrategy.REGEX

    def apply_match_filter(self, 
                           rules: BaseDataFrame,  
                           dimension: Dimension,  
                           context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object 
            context (BaseModel): The context object

        Returns:
            BaseDataFrame: The rules table with the filter rule applied
        """

        try:
            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)

        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
            return rules

        try:

            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()


            if dimension.get_dimension_data_type() == str:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value),
                ).mutate(
                    filter_match = 
                            ibis.ifelse(
                                    ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET) ,
                                    RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                    ibis.ifelse(
                                        ibis._.context_value_ibis.re_search(ibis._[dimension_rule_fieldname]),
                                        RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                        RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                    ))
                ).drop( columns="context_value")
            else:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value),
                ).mutate(
                    filter_match = 
                            ibis.ifelse(
                                    ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC) ,
                                    RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                    ibis.ifelse(
                                        ibis._.context_value_ibis.re_search(ibis._[dimension_rule_fieldname]),
                                        RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                        RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                    ))
                ).drop( columns="context_value")


        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())

        return rules



class RangeMatchStrategy(BaseMatchStrategy):
    """
        Rule Strategy for Range Matching
        Will match the context value to be within the range specified in the rules
    
    """

    match_strategy: MatchStrategy = MatchStrategy.RANGE

    def apply_match_filter(self, 
                           rules: BaseDataFrame,  
                           dimension: Dimension,  
                           context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object 
            context (BaseModel): The context object

        Returns:
            BaseDataFrame: The rules table with the filter rule applied
        """
       
        try:
            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)

        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
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
                (ibis._[min_field].isnull() | min_op(ibis._[min_field], ibis._.context_value_ibis)) &
                (ibis._[max_field].isnull() | max_op(ibis._[max_field], ibis._.context_value_ibis))
            )

            if dimension.get_dimension_data_type() == str:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(context_value),
                ).mutate(
                    filter_match = 
                        ibis.ifelse(
                                ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET),
                                RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                ibis.ifelse(
                                    condition,
                                    RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                    RuleTrinaryFlags.PRIME_FALSE_IBIS()
                            ))
                )

            else:
                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value),
                ).mutate(
                    filter_match = 
                        ibis.ifelse(
                                ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC),
                                RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                ibis.ifelse(
                                    condition,
                                    RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                    RuleTrinaryFlags.PRIME_FALSE_IBIS()
                            ))
                )


        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())

        return rules


# Rule Type Factory
class MatchStrategyFactory:
    

    @staticmethod
    def get_rule_strategy_class(match_strategy: MatchStrategy) -> BaseMatchStrategy:

        """
        Get the rule strategy class based on the match strategy type.

        Args:
            match_strategy (MatchStrategy): The match strategy type
        Returns:
            BaseMatchStrategy: The rule strategy class
        
        """

        if match_strategy == MatchStrategy.EXACT:
            return ExactMatchStrategy()
        elif match_strategy == MatchStrategy.REGEX:
            return RegexMatchStrategy()
        elif match_strategy == MatchStrategy.RANGE:
            return RangeMatchStrategy()
        else:
            raise ValueError(f"Invalid rule type: {match_strategy}")