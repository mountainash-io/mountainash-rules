
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




class MatchStrategy(Enum):
    EXACT = "EXACT"
    RANGE = "RANGE"
    REGEX = "REGEX"
    # WILDCARD = "WILDCARD"
    # FUZZY = "FUZZY"

class Dimension(BaseModel):

    name: str
    context_field: Optional[str] = None
    rule_field: Optional[str] = None

    rule_type: MatchStrategy = MatchStrategy.EXACT
    data_type: str = "string"  # Default to string, but can be int, float, date, bool etc.
    
    valid_values: List[Any] = []  # List of possible values for the dimension
    
    range_min_field: Optional[str] = None  # Minimum value for the dimension
    range_max_field: Optional[str] = None   # Maximum value for the dimension
    range_min_inclusive: bool = True  # Whether the minimum value is inclusive
    range_max_inclusive: bool = True  # Whether the maximum value is inclusive


class DimensionsMetadata(BaseModel):
    dimensions: List[Dimension]


class RulesEngine:

    #TODO: Create separate classes for handling and validating: rules, context and metadata

    UNKNOWN = "<NA>"
    NOT_SET = "<NOT_SET>"

    # Flags for Prime Filtering
    PRIME_TRUE = 2
    PRIME_FALSE = 3
    PRIME_UNKNOWN = 5

    # ALLOWED_CONTEXT_TYPES = (ir.IntegerScalar, ir.FloatingScalar, ir.BooleanScalar, ir.StringScalar)
    ALLOWED_CONTEXT_TYPES = (str, int, float, bool, type(None))


    def __init__(self, 
                    rules: BaseDataFrame, 
                    rule_metadata: DimensionsMetadata):

        #Prepare Rules
        self.rules: BaseDataFrame
        self._init_rules(rules)


        #Prepare Metadata
        self.rule_metadata: DimensionsMetadata = rule_metadata
        self.lookup_rule_metadata: Optional[Dict[str, Dimension]] = None
        self._init_rule_metadata(rule_metadata)


        #Tracability of intermediate values during each
        self.intermediate_values = {}
        self.warnings = {}


    def _init_rules(self, rules: BaseDataFrame):
        """
        Validate the dimensions in the rule metadata.
        """
        if rules is None:
            raise ValueError("No rules specified.")

        if not isinstance(rules, BaseDataFrame):
            raise ValueError("Rules must be a BaseDataFrame")

        # Convert the rules to a backend that supports window functions        
        if rules.ibis_backend_schema in ["polars"]:
            rules = rules.convert_backend_schema(new_backend_schema="sqlite")

        if rules.count() == int(0):
            raise ValueError("No rules specified.")

        self.rules = rules

 

    def _init_rule_metadata(self, rule_metadata: Optional[DimensionsMetadata] = None):
        """
        Validate the dimensions in the rule metadata.
        """

        if rule_metadata is not None:

            #validate the rule metadata
            for dimension in rule_metadata.dimensions:

                if dimension.rule_type == MatchStrategy.RANGE:
                    if dimension.range_min_field is None or dimension.range_max_field is None:
                        raise ValueError(f"Dimension {dimension.name} is of type RANGE but no min/max fields are specified.")
                elif dimension.rule_type in { #MatchStrategy.WILDCARD, 
                                             MatchStrategy.REGEX, MatchStrategy.EXACT }:
                    continue
                else:
                    raise ValueError(f"Dimension {dimension.name} has an invalid rule type: {dimension.rule_type}")

            #validate names are unique:
            dimension_names = [dimension.name for dimension in rule_metadata.dimensions]
            if len(dimension_names) != len(set(dimension_names)):
                raise ValueError("Dimension names must be unique.")

            #If we get this far, set up the dimensions lookup!
            self.lookup_rule_metadata: Optional[Dict[str, Dimension]] = {dimension.name: dimension for dimension in rule_metadata.dimensions}

    def get_dimension_attribute(self, 
                                dimension_name:str, 
                                attribute: str, 
                                default_value: Any) -> Any:
        """
        Get the field name for the context for a given dimension.
        """

        if self.lookup_rule_metadata:
            dimension: Optional[Dimension] = self.lookup_rule_metadata.get(dimension_name, None)

            if dimension is not None:
                value = getattr(dimension, attribute, default_value)
                if value is not None:
                    return value
                
        return default_value



    def get_dimension_context_fieldname(self, dimension_name:str) -> str:
        """
        Get the field name for the context for a given dimension.
        """

        return self.get_dimension_attribute(dimension_name=dimension_name, attribute="context_field", default_value=dimension_name)


    def get_dimension_rule_fieldname(self, dimension_name:str) -> str:
        """
        Get the field name for the rule_field for a given dimension.
        """

        rule_type = self.get_dimension_rule_type(dimension_name=dimension_name)

        if rule_type == MatchStrategy.RANGE:
            return self.get_dimension_rule_range_min_field(dimension_name=dimension_name)
        else:
            return self.get_dimension_attribute(dimension_name=dimension_name, attribute="rule_field", default_value=dimension_name)


    def get_dimension_rule_type(self, dimension_name:str) -> MatchStrategy:
        """
        Get the field name for the rule_type for a given dimension.
        """

        return self.get_dimension_attribute(dimension_name=dimension_name, attribute="rule_type", default_value=MatchStrategy.EXACT)


    def get_dimension_data_type(self, dimension_name:str) -> str:
        """
        Get the field name for the data_type for a given dimension.
        """

        return self.get_dimension_attribute(dimension_name=dimension_name, attribute="data_type", default_value="string")


    def get_dimension_rule_range_min_field(self, dimension_name:str) -> str:
        """
        Get the field name for the range_min_field for a given dimension.
        """

        range_min_field = self.get_dimension_attribute(dimension_name=dimension_name, attribute="range_min_field", default_value=None)

        if range_min_field is None:
            return self.get_dimension_rule_fieldname(dimension_name=dimension_name)
        else:
            return range_min_field


    def get_dimension_rule_range_max_field(self, dimension_name:str) -> str:
        """
        Get the field name for the range_max_field for a given dimension.
        """

        range_max_field = self.get_dimension_attribute(dimension_name=dimension_name, attribute="range_max_field", default_value=None)

        if range_max_field is None:
            return self.get_dimension_rule_fieldname(dimension_name=dimension_name)
        else:
            return range_max_field


    def get_dimension_rule_range_min_inclusive(self, dimension_name:str) -> bool:
        """
        Get the field name for the range_min_inclusive for a given dimension.
        """

        return self.get_dimension_attribute(dimension_name=dimension_name, attribute="range_min_inclusive", default_value=True)



    def get_dimension_rule_range_max_inclusive(self, dimension_name:str) -> bool:
        """
        Get the field name for the range_max_inclusive for a given dimension.
        """

        return self.get_dimension_attribute(dimension_name=dimension_name, attribute="range_max_inclusive", default_value=True)




    def get_active_dimension_names(self, 
                              context: BaseModel, 
                              rules: BaseDataFrame,
                              dimension_names: List[str]
                              ) -> List[str]:

        if dimension_names == []:
            raise ValueError("No dimension names specified") 



        #The fields the rule metadata asks for:
        expected_rule_fields:   Dict[str,str] = {dimension_name: self.get_dimension_rule_fieldname(dimension_name=dimension_name) for dimension_name in dimension_names}
        expected_context_fields: Dict[str,str] = {dimension_name: self.get_dimension_context_fieldname(dimension_name=dimension_name) for dimension_name in dimension_names}


        #The fields that actually exist
        actual_rule_fields:    Dict[str,str] = {dimension_name: fieldname
                                                    for dimension_name, fieldname in expected_rule_fields.items() 
                                                    if fieldname in rules.get_column_names()}


        actual_context_fields: Dict[str,str] = {dimension_name: fieldname
                                                    for dimension_name, fieldname in expected_context_fields.items() 
                                                    if getattr(context, fieldname, self.NOT_SET) is not self.NOT_SET}
        

        #find the dimensions that have their fields active in the rules and the context
        active_context_dimensions =    [dimension_name for dimension_name in dimension_names if dimension_name in actual_context_fields]
        active_rule_dimensions =       [dimension_name for dimension_name in dimension_names if dimension_name in actual_rule_fields]

        #find the common elements in the context and the rules
        active_dimensions = list(set(active_context_dimensions).union(set(active_rule_dimensions)))

        #find the dimensions that are not in all sources:
        missing_dimensions = set(dimension_names) - set(active_dimensions)

        if missing_dimensions:
            print(f"Warning: Dimensons requested in rules_meatadata, but are missing in rules or context: {missing_dimensions}")
    
        if active_dimensions == []:
            raise ValueError("No active dimensions found in rules or context")        

        return active_dimensions


    ##################
    # Apply Filters

    def _apply_filter_rule_unknown(self, 
                                    rules: BaseDataFrame,  
                                    dimension_name: str) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        rule_type = self.get_dimension_rule_type(dimension_name=dimension_name)

        if rule_type == MatchStrategy.RANGE:
            dimension_rule_fieldname: str = self.get_dimension_rule_range_min_field(dimension_name=dimension_name)
        else:
            dimension_rule_fieldname: str = self.get_dimension_rule_fieldname(dimension_name=dimension_name)



        rules = rules.mutate(

            filter_rule_unknown = ibis.ifelse(condition=ibis._[dimension_rule_fieldname].cast('string') == ibis.literal(self.UNKNOWN), 
                                true_expr=ibis.literal(self.PRIME_TRUE), 
                                false_expr=ibis.literal(self.PRIME_UNKNOWN) ),
        )

        return rules


    def _apply_filter_context_unknown(self, 
                                        rules: BaseDataFrame,  
                                        context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        #cast the context value to aplain python string
        context_value = str(context_value)            

        if context_value == self.UNKNOWN:
            rules = rules.mutate(
                filter_context_unknown = ibis.literal(self.PRIME_TRUE)
            )
        else:
            rules = rules.mutate(
                filter_context_unknown = ibis.literal(self.PRIME_UNKNOWN)
            )

        return rules


    def _log_context_cast_warning(self, dimension_name: str, context_value: Any, context_type: Type, target_type: str) -> None:
        """
        Log a warning for a context value that is not of the correct type.
        """
        if dimension_name not in self.warnings:
            self.warnings[dimension_name] = {}

        self.warnings[dimension_name]["context_cast"] = f"Context value {context_value} of type {context_type} has been cast to {target_type} for dimension {dimension_name}"


    def _apply_filter_exact_match(self, 
                                   rules: BaseDataFrame,  
                                   dimension_name: str,  
                                   context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        target_type: str = self.get_dimension_data_type(dimension_name=dimension_name)
        dimension_rule_fieldname: str = self.get_dimension_rule_fieldname(dimension_name=dimension_name)

        


        try:
            self._log_context_cast_warning(dimension_name=dimension_name, context_value=context_value, context_type=type(context_value), target_type=target_type)

            context_value_cast = ibis.literal(context_value).cast(target_type)
        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))
            return rules


        #Filter 3 is a direct comparison of the context value to the rule value

        try:
            rules = rules.mutate(
                filter_match = ibis.ifelse(condition= ibis._[dimension_rule_fieldname].cast(target_type) == context_value_cast, 
                                    true_expr=ibis.literal(self.PRIME_TRUE), 
                                    false_expr=ibis.literal(self.PRIME_FALSE) )
            )

            return rules

        except (Exception,IbisTypeError):
            raise ValueError(f"Could not cast rule field {dimension_rule_fieldname} to {target_type} in _apply_filter_exact_match() for dimension {dimension_name}")            
        

    # def _apply_filter_fuzzy_match(self, 
    #                                rules: BaseDataFrame,  
    #                                dimension_name: str,  
    #                                context_value: Any) -> BaseDataFrame:
    #     """
    #     Apply a filter rule to the rules table to check for a wildcard value.
    #     """

    #     target_type: str = self.get_dimension_data_type(dimension_name=dimension_name)
    #     dimension_rule_fieldname: str = self.get_dimension_rule_fieldname(dimension_name=dimension_name)

        
    #     try:
    #         context_value_cast = ibis.literal(context_value).cast(target_type)
    #     except Exception:
    #         rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))
    #         return rules


    #     #Filter 3 is a direct comparison of the context value to the rule value

    #     try:
    #         rules = rules.mutate(
    #             filter_match = ibis.ifelse(condition= ibis._[dimension_rule_fieldname].cast(target_type) == context_value_cast, 
    #                                 true_expr=ibis.literal(self.PRIME_TRUE), 
    #                                 false_expr=ibis.literal(self.PRIME_FALSE) )
    #         )

    #         return rules

    #     except Exception:
    #         raise ValueError(f"Could not cast rule field {dimension_rule_fieldname} to {target_type} in _apply_filter_exact_match() for dimension {dimension_name}")            
        


    # def _convert_wildcard_string_to_regex(self, pattern: str) -> str:
    #     """Convert a wildcard pattern to a regex pattern."""
    #     regex =  '^' + re.escape(pattern).replace(r'\*', '.*').replace(r'\?', '.') + '$'
    #     return regex


    # def _apply_filter_wildcard_match(self, rules: BaseDataFrame,  dimension_name: str,  context_value: Any) -> BaseDataFrame:
    #     """
    #     Apply a filter rule to the rules table to check for a wildcard value.
    #     """

    #     # def wildcard_to_regex(pattern):
    #     #     return '^' + re.escape(pattern).replace(r'\*', '.*').replace(r'\?', '.') + '$'
        

    #     try:           
    #         # context_value_cast = ibis.literal(self._convert_wildcard_string_to_regex(context_value)).cast(target_type='string')
    #         context_value_cast = ibis.literal(context_value).cast('string')
    #     except (Exception,IbisTypeError):
    #         print(f"Error in context_value {context_value} casting for {dimension_name}")

    #         rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))
    #         return rules


    #     try:
    #         dimension_rule_fieldname: str = self.get_dimension_rule_fieldname(dimension_name=dimension_name)

    #         rules = rules.mutate(
    #             context_value = context_value_cast,
    #             rule_regex = ibis._[dimension_rule_fieldname].re_replace(r'\*', '.*').re_replace(r'\?', '.')
    #         ).mutate(                
    #             filter_match = ibis.ifelse(
    #                 condition= ibis._.context_value.re_search( ibis.literal('^') + ibis._.rule_regex  + ibis.literal('$') ),
    #                 true_expr=ibis.literal(value=self.PRIME_TRUE),
    #                 false_expr=ibis.literal(value=self.PRIME_FALSE)
    #             )
    #         )

    #         # rule_check = rules.mutate(regex= wildcard_to_regex(ibis._[dimension_rule_fieldname]))
    #         print(rules.select("rule_regex").as_dict())

    #         rules = rules.drop( columns="rule_regex")

    #     except (Exception,IbisTypeError) as e:
    #         print(f"Error in wildcard match for {dimension_name}: {e}")
    #         rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))

    #     return rules


    def _apply_filter_regex_match(self, rules: BaseDataFrame,  dimension_name: str,  context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """



        try:
            context_value_cast = ibis.literal(value=context_value).cast("string")
        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))
            return rules

        try:

            dimension_rule_fieldname: str = self.get_dimension_rule_fieldname(dimension_name=dimension_name)


            rules = rules.mutate(
                context_value = context_value_cast
            ).mutate(
                filter_match = ibis.ifelse(
                    condition= ibis._.context_value.re_search(ibis._[dimension_rule_fieldname]),
                    true_expr=ibis.literal(self.PRIME_TRUE),
                    false_expr=ibis.literal(self.PRIME_FALSE)
                )
            ).drop( columns="context_value")

        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))

        return rules


    def _apply_filter_range_match(self, rules: BaseDataFrame,  dimension_name: str,  context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        target_type: str = self.get_dimension_data_type(dimension_name=dimension_name)
        
        try:
            context_value_cast = ibis.literal(context_value).cast(target_type)
        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))
            return rules

        try:

            min_field: str = self.get_dimension_rule_range_min_field(dimension_name=dimension_name)
            max_field: str = self.get_dimension_rule_range_max_field(dimension_name=dimension_name)

            min_inclusive: bool = self.get_dimension_rule_range_min_inclusive(dimension_name=dimension_name)
            max_inclusive: bool = self.get_dimension_rule_range_max_inclusive(dimension_name=dimension_name)

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
                    true_expr=ibis.literal(self.PRIME_TRUE),
                    false_expr=ibis.literal(self.PRIME_FALSE)
                )
            )


        except (Exception,IbisTypeError):
            rules = rules.mutate(filter_match = ibis.literal(self.PRIME_FALSE))

        return rules

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


    def _initialize_rule_flags(self, rules: BaseDataFrame) -> BaseDataFrame:
        """
        Initialize the rule flags for the rules table.
        """
        rules = rules.mutate(
            cumu_dimension_count=            ibis.literal(0),    
            # rule_softmatch_count=       ibis.literal(0),
            # context_softmatch_count=    ibis.literal(0),
            # dual_softmatch_count =      ibis.literal(0),
            cumu_soft_match_count =          ibis.literal(0),
            cumu_hard_match_count=           ibis.literal(0),
            dropped=                    ibis.null(),
            dropped_by_dimension=                 ibis.null(),
            # filter_all_false=           ibis.literal(False),
            # filter_all_true=            ibis.literal(True)
        )

        return rules




    def _apply_dimension_filter_flags(self, 
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
            dimension_any_false =     ibis._.dimension_filter_product % self.PRIME_FALSE == ibis.literal(0),
            dimension_any_true =      ibis._.dimension_filter_product % self.PRIME_TRUE  == ibis.literal(0),
        ).mutate(

            #Match Flags
            cumu_dimension_count=     ibis._.cumu_dimension_count   + ibis.literal(1).cast("int8"),
            cumu_soft_match_count=    ibis._.cumu_soft_match_count  + ibis.or_( ibis._.filter_rule_unknown % self.PRIME_TRUE == 0 , ibis._.filter_context_unknown % self.PRIME_TRUE == 0 ).cast("int8"),
            cumu_hard_match_count=    ibis._.cumu_hard_match_count  + (ibis._.filter_match % self.PRIME_TRUE == 0).cast("int8"),

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


    def _calculate_rule_priority(self, rules: BaseDataFrame) -> BaseDataFrame:
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

    def _validate_context_types(self, context: BaseModel, active_dimensions: List[str]) -> None:
        """
        Validate the types of the context fields.
        """

        context_types = {dimension_name: type(getattr(context, self.get_dimension_context_fieldname(dimension_name=dimension_name))) for dimension_name in active_dimensions}

        for dimension_name, fieldtype in context_types.items():
            if fieldtype not in self.ALLOWED_CONTEXT_TYPES:
                raise TypeError(f"Context Field {dimension_name} is of type {fieldtype}, but only {self.ALLOWED_CONTEXT_TYPES} are allowed.")


    def apply_context_rules_engine(self,
                                        context: BaseModel, 
                                        dimension_names: List[str]|str,
                                        keep_all: bool=True
                                        ) -> BaseDataFrame:
                
        #Make a copy of the rules        
        rules = self.rules


        # Validate Dimension names
        if isinstance(dimension_names, str):
            dimension_names = [dimension_names]
        
        if len(dimension_names) == 0:
            raise ValueError("No dimension names specified.")

        # Get the active dimensions - whose fields are in the rules and context
        active_dimensions = self.get_active_dimension_names(context=context, rules=rules, dimension_names=dimension_names)

        # Validate context
        # if not isinstance(context, BaseModel):
        #     raise ValueError("Context must be a Pydantic BaseModel")
        
        #get the types of each context field

        self._validate_context_types(context=context, active_dimensions=active_dimensions)


        # Initialization - add flags and counters to the rules
        rules = self._initialize_rule_flags(rules)

        # Apply Rules
        for dimension_name in active_dimensions:

            rule_type = self.get_dimension_rule_type(dimension_name=dimension_name)

            context_value = getattr(context, self.get_dimension_context_fieldname(dimension_name=dimension_name), self.UNKNOWN)

            rules = self._apply_filter_rule_unknown(rules=rules, dimension_name=dimension_name)
            rules = self._apply_filter_context_unknown(rules=rules, context_value=context_value)

            if rule_type == MatchStrategy.EXACT:
                rules = self._apply_filter_exact_match(rules=rules, dimension_name=dimension_name, context_value=context_value)
            # elif rule_type == MatchStrategy.FUZZY:
            #     rules = self._apply_filter_fuzzy_match(rules=rules, dimension_name=dimension_name, context_value=context_value)
            elif rule_type == MatchStrategy.REGEX:
                rules = self._apply_filter_regex_match(rules=rules, dimension_name=dimension_name, context_value=context_value)
            # elif rule_type == MatchStrategy.WILDCARD:
            #     rules = self._apply_filter_wildcard_match(rules=rules, dimension_name=dimension_name, context_value=context_value)
            elif rule_type == MatchStrategy.RANGE:
                rules = self._apply_filter_range_match(rules=rules, dimension_name=dimension_name, context_value=context_value)
            else:
                raise ValueError(f"Invalid rule type for dimension {dimension_name}")
            
            
            rules = self._apply_dimension_filter_flags(rules=rules, dimension_name=dimension_name)

            #Store intermediate state
            self._save_dimension_intermediate_values(rules=rules, dimension_name=dimension_name)

            #If we have dropped all fields, then we can stop
            if rules.filter(ibis._.dropped).count() == rules.count():
                break

        rules = self._calculate_rule_priority(rules)

        rules = rules.mutate(keep= ibis._.dropped.isnull())

        if keep_all:
            return rules #.order_by('priority')
        else:
            return rules.filter(ibis._.keep) #.order_by('priority')


