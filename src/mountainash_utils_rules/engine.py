

from typing import List, Any,Optional, Dict

import ibis
import ibis.expr.types as ir
from ibis.common.deferred import Deferred
from ibis.common.exceptions import IbisTypeError


from mountainash_data import BaseDataFrame, DataFrameFactory
import re
from pydantic import BaseModel
from enum import Enum
# import operator 

from mountainash_utils_rules.constants import RuleType, RuleConstants
from mountainash_utils_rules.context import ContextManager
from mountainash_utils_rules.rule_strategies import ExactMatchStrategy, RangeMatchStrategy, RegexMatchStrategy, RuleTypeFactory, RuleTypeStrategy
from mountainash_utils_rules.metadata import RuleMetadata, MetadataManager, DimensionMetadata
from mountainash_utils_rules.observer import TracabilityManager
from mountainash_utils_rules.rule_manager import RuleManager


class RulesEngine:

    def __init__(self, rules: BaseDataFrame, rule_metadata: RuleMetadata):
        self.rule_manager = RuleManager(rules)
        self.metadata_manager = MetadataManager(rule_metadata)
        self.context_manager = ContextManager()
        self.tracability_manager = TracabilityManager()

    # def apply_context_rules_engine(self, context: BaseModel, dimension_names: List[str]|str, keep_all: bool=True) -> BaseDataFrame:
        # Implementation of apply_context_rules_engine using the other managers



    def initialize_rule_flags(self, rules: BaseDataFrame) -> BaseDataFrame:
        """
        Initialize the rule flags for the rules table.
        """
        rules = rules.mutate(
            cumu_dimension_count=     ibis.literal(0),    
            cumu_soft_match_count =   ibis.literal(0),
            cumu_hard_match_count=    ibis.literal(0),
            dropped=                  ibis.null(),
            dropped_by_dimension=     ibis.null(),
        )

        return rules




    def apply_dimension_filter_flags(self, 
                                      rules: BaseDataFrame, 
                                      dimension_name: str) -> BaseDataFrame:
        """
        Apply flags to the rules table to indicate the type of match for each dimension.
        """
        rules = rules.mutate(
            # Product of prime filters
            dimension_filter_product = ibis._.filter_rule_unknown * ibis._.filter_context_unknown * ibis._.filter_match

        ).mutate(
            #Flag across all 3 filters
            dimension_any_false =     ibis._.dimension_filter_product % RuleConstants.PRIME_FALSE == ibis.literal(0),
            dimension_any_true =      ibis._.dimension_filter_product % RuleConstants.PRIME_TRUE  == ibis.literal(0),
        ).mutate(

            #Match Flags
            cumu_dimension_count=     ibis._.cumu_dimension_count   + ibis.literal(1).cast("int8"),
            cumu_soft_match_count=    ibis._.cumu_soft_match_count  + ibis.or_( ibis._.filter_rule_unknown % RuleConstants.PRIME_TRUE == 0 , ibis._.filter_context_unknown % RuleConstants.PRIME_TRUE == 0 ).cast("int8"),
            cumu_hard_match_count=    ibis._.cumu_hard_match_count  + (ibis._.filter_match % RuleConstants.PRIME_TRUE == 0).cast("int8"),

        ).mutate(
            #Rule Row Drop Flags - The existence of a True gets you through! It is binary at this stage!
            dropped_by_dimension=   ibis.ifelse( condition=ibis._.dropped.isnull() & ~ibis._.dimension_any_true, 
                                                true_expr=ibis.literal(dimension_name), 
                                                false_expr=ibis._.dropped_by_dimension),
            dropped=                ibis.ifelse( condition=ibis._.dropped.isnull() & ~ibis._.dimension_any_true, 
                                                true_expr=ibis.literal(True), 
                                                false_expr=ibis._.dropped)
        )

        return rules


    def calculate_rule_priority(self, rules: BaseDataFrame) -> BaseDataFrame:
        """
        Calculate the priority of rules based on hard_matches, soft_matches, and rule order.
        """
        rules = rules.mutate(
            row_number=ibis.row_number() #.over(ibis.window(order_by=[ibis._.rule_name])),
        )
        
        rules = rules.mutate(
            priority=ibis.row_number().over(ibis.window(
                order_by=[
                    ibis.desc('cumu_hard_match_count'),
                    ibis.desc('cumu_soft_match_count'),
                    'row_number'
                ]
            ))
        )
        
        return rules.drop('row_number')


    def apply_context_rules_engine(self,
                                        context: BaseModel, 
                                        dimension_names: List[str]|str,
                                        keep_all: bool=True
                                        ) -> BaseDataFrame:
                
        #Get a copy of the rules        
        rules = self.rule_manager.get_rules()

        # Validate Dimension names
        if isinstance(dimension_names, str):
            dimension_names = [dimension_names]
        
        if len(dimension_names) == 0:
            raise ValueError("No dimension names specified.")

        # Get the active dimensions - whose fields are in the rules and context
        active_dimension_names: List[str] = self.metadata_manager.get_active_dimension_names(context=context, rules=rules, dimension_names=dimension_names)
        active_dimensions: List[DimensionMetadata] = self.metadata_manager.get_dimensions_list(dimension_names=active_dimension_names)


        # Validate context
        self.context_manager.validate_context(context=context, active_dimensions=active_dimensions)

        # Initialization - add flags and counters to the rules
        rules = self.initialize_rule_flags(rules)

        # Apply Rules
        for dimension_name in active_dimension_names:

            obj_dimension = self.metadata_manager.get_dimension(dimension_name=dimension_name)

            context_value = getattr(context, obj_dimension.get_dimension_context_fieldname(), RuleConstants.UNKNOWN)

            #Apply filters
            obj_rule_strategy: RuleTypeStrategy = RuleTypeFactory.get_rule_strategy_class(rule_type=obj_dimension.get_dimension_rule_type())

            rules = obj_rule_strategy.apply_filter_rule_unknown(rules=rules, dimension=obj_dimension)
            rules = obj_rule_strategy.apply_filter_context_unknown(rules=rules, context_value=context_value)
            rules = obj_rule_strategy.apply_match_filter(rules=rules, dimension=obj_dimension, context_value=context_value)
            rules = self.apply_dimension_filter_flags(rules=rules, dimension_name=dimension_name)

            #Store intermediate state
            self.tracability_manager._save_dimension_intermediate_values(rules=rules, dimension_name=dimension_name)

            #If we have dropped all fields, then we can stop
            if rules.filter(ibis._.dropped).count() == rules.count():
                break

        #Rank rules
        rules = self.calculate_rule_priority(rules)

        #Filter rules
        rules = rules.mutate(keep= ibis._.dropped.isnull())
        if keep_all:
            return rules #.order_by('priority')
        else:
            return rules.filter(ibis._.keep) #.order_by('priority')

