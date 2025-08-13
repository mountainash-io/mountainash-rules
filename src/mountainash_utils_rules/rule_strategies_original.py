from abc import ABC, abstractmethod

import ibis
from ibis.common.deferred import Deferred
from ibis.common.exceptions import IbisTypeError

from pydantic import BaseModel

from mountainash_dataframes import BaseDataFrame
from mountainash_dataframes.utils.expression_builders import TernaryExpressionBuilder
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
                           context_value: str|int|float) -> BaseDataFrame:
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
                                        context_value: str|int|float) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object
            context_value (str|int|float): The pre-extracted context value

        Returns:
            BaseDataFrame: The rules table with the filter rule applied

        """

        # PHASE 1 OPTIMIZATION: Use pre-extracted context value instead of extracting again
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
                                   context_value: str|int|float) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object
            context_value (str|int|float): The pre-extracted context value

        Returns:
            BaseDataFrame: The rules table with the filter rule applied
        """

        try:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()

            if dimension.get_dimension_data_type() == str:
                # PHASE 1 OPTIMIZATION: Use pre-extracted context value
                rules = rules.mutate(
                    filter_match = ibis.ifelse(
                                        ibis.literal(value=context_value) == ibis.literal(value=RuleConstants.NOT_SET) ,
                                        RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                        ibis.ifelse(
                                            ibis._[dimension_rule_fieldname] == ibis.literal(value=context_value),
                                            RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                            RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                            )
                                    ))

            else:
                # PHASE 1 OPTIMIZATION: Use pre-extracted context value
                rules = rules.mutate(
                    filter_match = ibis.ifelse(
                                        ibis.literal(value=context_value) == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC),
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
                           context_value: str|int|float) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object
            context_value (str|int|float): The pre-extracted context value

        Returns:
            BaseDataFrame: The rules table with the filter rule applied
        """

        try:
            dimension_rule_fieldname: str = dimension.get_dimension_rule_fieldname()

            if dimension.get_dimension_data_type() == str:
                # PHASE 1 OPTIMIZATION: Use pre-extracted context value, eliminate temporary column
                # NOTE: Using Python regex fallback for SQLite backend compatibility
                import re

                # Extract patterns and context for regex evaluation
                patterns_df = rules.to_pandas()
                results = []

                for _, row in patterns_df.iterrows():
                    pattern = row[dimension_rule_fieldname]

                    if context_value == RuleConstants.NOT_SET:
                        results.append(RuleTrinaryFlags.PRIME_UNKNOWN)
                    elif pattern == RuleConstants.UNKNOWN or pattern is None:
                        results.append(RuleTrinaryFlags.PRIME_UNKNOWN)
                    else:
                        try:
                            # Use Python regex matching
                            match_result = re.match(pattern, context_value) is not None
                            flag = RuleTrinaryFlags.PRIME_TRUE if match_result else RuleTrinaryFlags.PRIME_FALSE
                            results.append(flag)
                        except Exception:
                            results.append(RuleTrinaryFlags.PRIME_UNKNOWN)

                # Update the original rules object by adding the computed filter_match column
                # Create dynamic case statement for all rows
                import ibis
                case_expr = ibis.case()

                for i, (_, row) in enumerate(patterns_df.iterrows()):
                    case_expr = case_expr.when(
                        ibis._['rule_name'] == ibis.literal(row['rule_name']),
                        ibis.literal(results[i])
                    )

                rules = rules.mutate(
                    filter_match = case_expr.else_(RuleTrinaryFlags.PRIME_UNKNOWN_IBIS()).end()
                )
            else:
                # PHASE 1 OPTIMIZATION: Use pre-extracted context value, eliminate temporary column
                rules = rules.mutate(
                    filter_match =
                            ibis.ifelse(
                                    ibis.literal(value=context_value) == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC) ,
                                    RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                    ibis.ifelse(
                                        ibis._[dimension_rule_fieldname].contains(ibis.literal(value=context_value)),
                                        RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                        RuleTrinaryFlags.PRIME_FALSE_IBIS()
                                    ))
                )

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
                           context_value: str|int|float) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.

        Args:
            rules (BaseDataFrame): The rules table
            dimension (Dimension): The dimension object
            context_value (str|int|float): The pre-extracted context value

        Returns:
            BaseDataFrame: The rules table with the filter rule applied
        """

        try:
            min_field: str = dimension.get_dimension_rule_range_min_field()
            max_field: str = dimension.get_dimension_rule_range_max_field()

            min_inclusive: bool = dimension.get_dimension_rule_range_min_inclusive()
            max_inclusive: bool = dimension.get_dimension_rule_range_max_inclusive()

            #Use the ibis deferred operators
            min_op = Deferred.__le__ if min_inclusive else Deferred.__lt__
            max_op = Deferred.__ge__ if max_inclusive else Deferred.__gt__

            # PHASE 1 OPTIMIZATION: Use pre-extracted context value directly in condition
            condition = (
                (ibis._[min_field].isnull() | min_op(ibis._[min_field], ibis.literal(value=context_value))) &
                (ibis._[max_field].isnull() | max_op(ibis._[max_field], ibis.literal(value=context_value)))
            )

            if dimension.get_dimension_data_type() == str:
                # PHASE 1 OPTIMIZATION: Eliminate temporary column creation
                rules = rules.mutate(
                    filter_match =
                        ibis.ifelse(
                                ibis.literal(value=context_value) == ibis.literal(value=RuleConstants.NOT_SET),
                                RuleTrinaryFlags.PRIME_UNKNOWN_IBIS(),
                                ibis.ifelse(
                                    condition,
                                    RuleTrinaryFlags.PRIME_TRUE_IBIS(),
                                    RuleTrinaryFlags.PRIME_FALSE_IBIS()
                            ))
                )

            else:
                # PHASE 1 OPTIMIZATION: Eliminate temporary column creation
                rules = rules.mutate(
                    filter_match =
                        ibis.ifelse(
                                ibis.literal(value=context_value) == ibis.literal(value=RuleConstants.NOT_SET_NUMERIC),
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
