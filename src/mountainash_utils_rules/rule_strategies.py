

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

from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags
from mountainash_utils_rules.dimension import DimensionsMetadata, MetadataManager, Dimension
from mountainash_utils_rules.context import ContextHelper

# import operator 






class BaseMatchStrategy(ABC):

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
        """

        # match_strategy = self.get_dimension_match_strategy()

        if self.match_strategy == MatchStrategy.RANGE:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_range_min_field()
        else:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()


        if dimension.get_dimension_data_type() == str:

            rules = rules.mutate(

                filter_rule_unknown = ibis.ifelse(condition=ibis._[dimension_rule_fieldname] == RuleConstants.UNKNOWN_IBIS(), 
                                    true_expr= RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                    false_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()),
            )

        else:

            rules = rules.mutate(

                filter_rule_unknown = ibis.ifelse(condition=ibis._[dimension_rule_fieldname].cast(int) == RuleConstants.UNKNOWN_NUMERIC_IBIS(), 
                                    true_expr= RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                    false_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()),
            )

        return rules


    def apply_filter_context_unknown(self, 
                                        rules: BaseDataFrame,
                                        dimension: Dimension,   
                                        context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        # context_value = getattr(context, dimension.get_dimension_context_fieldname(), RuleConstants.UNKNOWN)

        try:
            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)
            print("context_unknown 1: ")

        except (Exception,IbisTypeError) as e:
            print(f"context_unknown 2: {e}")
            rules = rules.mutate(filter_context_unknown = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())

            return rules

        #cast the context value to aplain python string
        # context_value = str(context_value)            

        if context_value in [RuleConstants.UNKNOWN, RuleConstants.UNKNOWN_NUMERIC]:
            rules = rules.mutate(filter_context_unknown = RuleTrinaryFlags.PRIME_TRUE_IBIS())
        else:
            rules = rules.mutate(filter_context_unknown = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())


        return rules



class ExactMatchStrategy(BaseMatchStrategy):


    match_strategy: MatchStrategy = MatchStrategy.EXACT

    def apply_match_filter(self, 
                                   rules: BaseDataFrame,  
                                   dimension: Dimension,  
                                   context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        # target_type: str = dimension.get_dimension_data_type()
        dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()



        try:
            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)
            print("EXACT 1: ")

        except (Exception,IbisTypeError) as e:
            print(f"EXACT 2: {e}")
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())

            return rules

        try:

            if dimension.get_dimension_data_type() == str:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value)
                ).mutate(
                    filter_match = ibis.ifelse(
                                    condition= ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET) ,
                                    true_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                    false_expr=    
                                        ibis.ifelse(
                                            condition= ibis._[dimension_rule_fieldname] == ibis._.context_value_ibis, 
                                            true_expr=RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                            false_expr=RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                            ) 
                                    ))
                print("EXACT 3b: ")

            else:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value)
                ).mutate(
                    filter_match = ibis.ifelse(
                                    condition= ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC),
                                    true_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                    false_expr=    
                                        ibis.ifelse(
                                            condition= ibis._[dimension_rule_fieldname] == ibis._.context_value_ibis, 
                                            true_expr=RuleTrinaryFlags.PRIME_TRUE_IBIS(), 
                                            false_expr=RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                            ) 
                                    ))

                print("EXACT 3b: ")

        except (Exception,IbisTypeError) as e:
            print(f"EXACT 4: {e}")
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
        
        return rules

class RegexMatchStrategy(BaseMatchStrategy):

    match_strategy: MatchStrategy = MatchStrategy.REGEX

    def apply_match_filter(self, 
                           rules: BaseDataFrame,  
                           dimension: Dimension,  
                           context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """



        try:

            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)
            print("REGEX 1: ")

        except (Exception,IbisTypeError) as e:
            print(f"REGEX 2: {e}")
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
            return rules

        try:

            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()


            if dimension.get_dimension_data_type() == str:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value)
                ).mutate(
                    filter_match = 
                            ibis.ifelse(
                                condition= ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET) ,
                                true_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                false_expr=                   
                                    ibis.ifelse(
                                        condition= ibis._.context_value_ibis.re_search(ibis._[dimension_rule_fieldname]),
                                        true_expr=RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                        false_expr=RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                    ))
                ).drop( columns="context_value")
                print("REGEX 3a: ")
            else:

                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value)
                ).mutate(
                    filter_match = 
                            ibis.ifelse(
                                condition= ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC) ,
                                true_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                false_expr=                   
                                    ibis.ifelse(
                                        condition= ibis._.context_value_ibis.re_search(ibis._[dimension_rule_fieldname]),
                                        true_expr=RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                        false_expr=RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                    ))
                ).drop( columns="context_value")

                print("REGEX 3b: ")

        except (Exception,IbisTypeError) as e:
            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())
            print(f"REGEX 4: {e}")

        return rules



class RangeMatchStrategy(BaseMatchStrategy):

    match_strategy: MatchStrategy = MatchStrategy.RANGE

    def apply_match_filter(self, 
                           rules: BaseDataFrame,  
                           dimension: Dimension,  
                           context: BaseModel) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """
       
        try:
            context_value = ContextHelper.get_context_value(context=context, dimension=dimension)
            print("RANGE 1: ")

        except (Exception,IbisTypeError) as e:
            print(f"RANGE 2: {e}")
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
                    context_value_ibis = ibis.literal(context_value)
                ).mutate(
                    filter_match = 
                        ibis.ifelse(
                            condition= ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET),
                            true_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                            false_expr=
                                ibis.ifelse(
                                    condition=condition,
                                    true_expr=RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                    false_expr=RuleTrinaryFlags.PRIME_FALSE_IBIS()
                            ))
                )
                print("RANGE 3a: ")

            else:
                rules = rules.mutate(
                    context_value_ibis = ibis.literal(value=context_value)
                ).mutate(
                    filter_match = 
                        ibis.ifelse(
                            condition= ibis._.context_value_ibis == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC),
                            true_expr=RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                            false_expr=
                                ibis.ifelse(
                                    condition=condition,
                                    true_expr=RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                    false_expr=RuleTrinaryFlags.PRIME_FALSE_IBIS()
                            ))
                )

                print("RANGE 3b: ")


        except (Exception,IbisTypeError) as e:
            print(f"RANGE 4: {e}")

            rules = rules.mutate(filter_match = RuleTrinaryFlags.PRIME_UNKNOWN_IBIS())

        return rules


# Rule Type Factory
class MatchStrategyFactory:
    
    @staticmethod
    def get_rule_strategy_class(match_strategy: MatchStrategy) -> BaseMatchStrategy:
        if match_strategy == MatchStrategy.EXACT:
            return ExactMatchStrategy()
        elif match_strategy == MatchStrategy.REGEX:
            return RegexMatchStrategy()
        elif match_strategy == MatchStrategy.RANGE:
            return RangeMatchStrategy()
        else:
            raise ValueError(f"Invalid rule type: {match_strategy}")