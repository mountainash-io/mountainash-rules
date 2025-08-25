

from typing import List,Optional

import ibis
from pydantic import BaseModel

from mountainash_dataframes import BaseDataFrame
from mountainash_dataframes.utils.expressions import TernaryExpressionBuilder as fc

from mountainash_utils_rules.constants import RuleTrinaryFlags
from mountainash_utils_rules.rule_strategies import MatchStrategyFactory, BaseMatchStrategy
from mountainash_utils_rules.dimension import DimensionsMetadata, MetadataManager, Dimension
from mountainash_utils_rules.observer import ObservabilityManager
from mountainash_utils_rules.rule_manager import RuleManager
from mountainash_utils_rules.context import ContextHelper



class RulesEngine:

    def __init__(self,
                 rules: BaseDataFrame,
                 dimension_metadata: Optional[DimensionsMetadata] = None):

        self.rule_manager = RuleManager(rules=rules)
        self.metadata_manager = MetadataManager(rules = self.rule_manager.rules,
                                                dimension_metadata=dimension_metadata)
        self.observability_manager = ObservabilityManager()



    def initialize_rule_flags(self, rules: BaseDataFrame) -> BaseDataFrame:
        """
        Initialize the rule flags for the rules table.

        Args:
            rules (BaseDataFrame): The rules table

        Returns:
            BaseDataFrame: The rules table with the flags initialized
        """
        rules = rules.mutate(
            cumu_dimension_count=     ibis.literal(value=0),
            cumu_soft_match_count =   ibis.literal(value=0),
            cumu_hard_match_count=    ibis.literal(value=0),
            dropped=                  ibis.null(),
            dropped_by_dimension=     ibis.null(),
        )

        return rules




    def apply_dimension_filter_flags(self,
                                      rules: BaseDataFrame,
                                      dimension: Dimension) -> BaseDataFrame:
        """
        Apply flags to the rules table to indicate the type of match for each dimension.
        PHASE 1 OPTIMIZATION: Simplified boolean logic instead of complex prime arithmetic.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object

        Returns:
            BaseDataFrame: The rules table with the flags applied
        """
        rules = rules.mutate(
            # PHASE 1 OPTIMIZATION: Direct boolean logic instead of prime arithmetic
            # Check if any filter indicates TRUE (rule unknown, context unknown, or direct match)
            dimension_any_true = ibis.or_(
                ibis._.filter_rule_unknown == RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                ibis._.filter_context_unknown == RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()
            ),

            # Check if any filter indicates FALSE (explicit mismatch)
            dimension_any_false = ibis.or_(
                ibis._.filter_rule_unknown == RuleTrinaryFlags.PRIME_FALSE_IBIS(),
                ibis._.filter_context_unknown == RuleTrinaryFlags.PRIME_FALSE_IBIS(),
                ibis._.filter_match == RuleTrinaryFlags.PRIME_FALSE_IBIS()
            ),

            # Match counters using direct boolean operations
            cumu_dimension_count=     ibis._.cumu_dimension_count   + ibis.literal(1).cast("int8"),
            cumu_soft_match_count=    ibis._.cumu_soft_match_count  + ibis.or_(
                                        ibis._.filter_rule_unknown == RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                        ibis._.filter_context_unknown == RuleTrinaryFlags.PRIME_TRUE_IBIS()
                                      ).cast("int8"),
            cumu_hard_match_count=    ibis._.cumu_hard_match_count  + (ibis._.filter_match == RuleTrinaryFlags.PRIME_TRUE_IBIS()).cast("int8"),

        ).mutate(
            # Rule Row Drop Flags - Direct boolean logic
            dropped_by_dimension=   ibis.ifelse( ibis._.dropped.isnull() & ~ibis._.dimension_any_true,
                                                 ibis.literal(value=dimension.dimension_name),
                                                 ibis._.dropped_by_dimension),

            dropped=                ibis.ifelse( ibis._.dropped.isnull() & ~ibis._.dimension_any_true,
                                                 ibis.literal(value=True),
                                                 ibis._.dropped)
        )

        return rules


    def calculate_rule_priority(self, rules: BaseDataFrame) -> BaseDataFrame:
        """
        Calculate the priority of rules based on hard_matches, soft_matches, and rule order.

        Args:
            rules (BaseDataFrame): The rules table

        Returns:
            BaseDataFrame: The rules table with the priority calculated
        """
        rules = rules.mutate(
            row_number=ibis.row_number(), #.over(ibis.window(order_by=[ibis._.rule_name])),
        ).mutate(
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

        """
        Apply the rules engine to the context and return the filtered rules.

        Args:
            context (BaseModel): The context object
            dimension_names (List[str]|str): The dimension names to apply the rules to
            keep_all (bool): Flag to keep all rules or only the ones that pass all filters

        Returns:
            BaseDataFrame: The filtered rules
        """
        #Get a copy of the rules
        rules = self.rule_manager.get_rules()

        # Validate Dimension names
        if isinstance(dimension_names, str):
            dimension_names = [dimension_names]

        if len(dimension_names) == 0:
            raise ValueError("No dimension names specified.")

        # Get the active dimensions - whose fields are in the rules AND context
        #These aren't getting filtered when missing or NOT_SET.
        active_dimension_names: List[str] = self.metadata_manager.get_active_dimension_names(context=context, rules=rules, dimension_names=dimension_names)
        active_dimensions: List[Dimension] = self.metadata_manager.get_dimensions_list(dimension_names=active_dimension_names)

        # PHASE 1 OPTIMIZATION: Extract all context values upfront in a single batch operation
        context_values = ContextHelper.get_all_context_values(context=context, dimensions=active_dimensions)

        # Initialization - add flags and counters to the rules
        rules = self.initialize_rule_flags(rules=rules)

        # dropped_filter = fc.eq("dropped", True)
        keep_filter = fc.eq("keep", True)

        # Apply Rules
        for dimension in active_dimensions:

            #Apply filters
            obj_rule_strategy: BaseMatchStrategy = MatchStrategyFactory.get_rule_strategy_class(match_strategy=dimension.get_dimension_match_strategy())

            # PHASE 1 OPTIMIZATION: Pass pre-extracted context value to eliminate redundant extraction
            context_value = context_values[dimension.dimension_name]

            rules = obj_rule_strategy.apply_filter_rule_unknown(    rules=rules, dimension=dimension)
            rules = obj_rule_strategy.apply_filter_context_unknown( rules=rules, dimension=dimension, context_value=context_value)
            rules = obj_rule_strategy.apply_match_filter(           rules=rules, dimension=dimension, context_value=context_value)
            rules = self.apply_dimension_filter_flags(              rules=rules, dimension=dimension)

            #Store intermediate state
            self.observability_manager.save_dimension_intermediate_values(rules=rules, dimension=dimension)

            #If we have dropped all fields, then we can stop. This may be slow, as it needs a materialisation!
            # if rules.filter(filter_condition=dropped_filter).count() == rules.count():
            #     break

        #Rank rules
        rules = self.calculate_rule_priority(rules)

        #Filter rules
        rules = rules.mutate(keep= ibis._.dropped.isnull())
        if keep_all:
            return rules #.order_by('priority')
        else:
            return rules.filter(filter_condition=keep_filter) #.order_by('priority')
