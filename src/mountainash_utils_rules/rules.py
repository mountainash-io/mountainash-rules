
from typing import List, Any,Type
from dataclasses import dataclass

import ibis
import ibis.expr.types as ir
from mountainash_data import BaseDataFrame, DataFrameFactory

# ibis.set_backend(backend="polars")
# from mountainash_data import BaseDataFrame


class RuleType(Enum):
    EXACT = "exact"
    RANGE = "range"
    WILDCARD = "wildcard"

class DimensionMetadata(BaseModel):
    name: str
    context_field: Optional[str] = None
    rule_field: Optional[str] = None

    rule_type: RuleType
    data_type: Type = str  # Default to string, but can be int, float, date, bool etc.
    
    valid_values: List[Any] = []  # List of possible values for the dimension
    
    range_min_field: str = None  # Minimum value for the dimension
    range_max_field: str = None  # Maximum value for the dimension
    range_min_inclusive: bool = True  # Whether the minimum value is inclusive
    range_max_inclusive: bool = True  # Whether the maximum value is inclusive


class RuleMetadata(BaseModel):
    dimensions: List[DimensionMetadata]


class RulesEngine:


    UNKNOWN = "<NA>"
    NOT_SET = "<NOT_SET>"

    # Flags for Prime Filtering
    PRIME_TRUE = 2
    PRIME_FALSE = 3
    PRIME_UNKNOWN = 5

    ALLOWED_CONTEXT_TYPES = (ir.IntegerScalar, ir.FloatingScalar, ir.BooleanScalar, ir.StringScalar)


    def __init__(self, 
                    rules: ir.Table, 
                    rule_metadata: RuleMetadata):

        self.rules: BaseDataFrame = rules
        self.rule_metadata: RuleMetadata = rule_metadata

        self.prepare_rule_metadata(raw_rule_metadata)

        # Create a dictionary of dimension metadata for easy access
        self.lookup_rule_metadata: Optional[Dict[str, DimensionMetadata]] = {dimension.name: dimension for dimension in rule_metadata.dimensions}


    def get_dimension_context_fieldname(self, dimension_name:str) -> List[str]:
        """
        Get the field name for the context for a given dimension.
        """


        if self.lookup_rule_metadata[dimension_name].context_field:
            return self.lookup_rule_metadata[dimension_name].context_field
        else:
            return dimension_name

    def get_dimension_rule_fieldname(self, dimension_name:str) -> List[str]:
        """
        Get the field name for the rule_field for a given dimension.
        """
        if self.lookup_rule_metadata[dimension_name].rule_field:
            return self.lookup_rule_metadata[dimension_name].rule_field
        else:
            return dimension_name


    def get_dimension_rule_type(self, dimension_name:str) -> List[str]:
        """
        Get the field name for the rule_field for a given dimension.
        """
        if self.lookup_rule_metadata[dimension_name].rule_type:
            return self.lookup_rule_metadata[dimension_name].rule_type
        else:
            return RuleType.EXACT

    def get_active_dimension_names(self, 
                              context: dataclass, 
                              rules: BaseDataFrame,
                              dimension_names: List[str]
                              ) -> List[str]:

       #Validate fieldnames

        #identify dimensions in Context and Rules
        actual_context_fields: List[Dict[str,str]] = {dimension_name: self.get_dimension_context_fieldname(dimension_name=dimension_name) for dimension_name in dimension_names if getattr(context, self.get_dimension_context_fieldname(dimension_name=dimension_name), self.NOT_SET) is not self.NOT_SET}
        actual_rule_fields: List[Dict[str,str]] =    {dimension_name: self.get_dimension_rule_fieldname(dimension_name=dimension_name) for dimension_name in dimension_names if self.get_dimension_rule_fieldname(dimension_name=dimension_name) in rules.get_column_names()}

        active_context_dimensions =    [dimension_name for dimension_name in dimension_names if dimension_name in actual_context_fields]
        active_rule_dimensions =       [dimension_name for dimension_name in dimension_names if dimension_name in actual_rule_fields]

        #find the common elements in the context and the rules
        active_dimensions = list(set(active_context_dimensions).union(set(active_rule_dimensions)))

        #find the dimensions that are not in all sources:
        missing_dimensions = set(dimension_names) - set(active_dimensions)

        if missing_dimensions:
            print(f"Warning: Dimensons missing in rules or context: {missing_dimensions}")
    
        if active_dimensions == []:
            raise ValueError("No active dimensions found in rules or context")        

        return active_dimensions


    def prepare_rule_metadata(self, rule_metadata: Optional[RuleMetadata] = None):
        """
        Validate the dimensions in the rule metadata.
        """

        if rule_metadata is not None

        #validate the rule metadata
        for dimension in rule_metadata.dimensions:
            elif dimension.rule_type == RuleType.RANGE:
                if dimension.range_min_field is None or dimension.range_max_field is None:
                    raise ValueError(f"Dimension {dimension.name} is of type RANGE but no min/max fields are specified.")
            elif dimension.rule_type == RuleType.WILDCARD:
                pass
            elif dimension.rule_type == RuleType.EXACT:
                pass
            else:
                raise ValueError(f"Dimension {dimension.name} has an invalid rule type: {dimension.rule_type}")

        #validate names are unique:
        dimension_names = [dimension.name for dimension in rule_metadata.dimensions]
        if len(dimension_names) != len(set(dimension_names)):
            raise ValueError("Dimension names must be unique.")

        #If we get this far, set up the dimensions lookup!
        self.lookup_rule_metadata: Optional[Dict[str, DimensionMetadata]] = {dimension.name: dimension for dimension in rule_metadata.dimensions}



    def _is_context_type_supported(self, context_value: Any) -> bool:
        """
        Check if the column and literal value have compatible types.
        """
        try:
            if isinstance(ibis.literal(context_value), self.ALLOWED_CONTEXT_TYPES):
                return True
            else:
                return False
        except Exception as e:
            return False



    def _initialize_rule_flags(self, rules: BaseDataFrame) -> BaseDataFrame:
        """
        Initialize the rule flags for the rules table.
        """
        rules = rules.mutate(
            dimension_count=            ibis.literal(0),    
            rule_softmatch_count=       ibis.literal(0),
            context_softmatch_count=    ibis.literal(0),
            dual_softmatch_count =      ibis.literal(0),
            any_softmatch_count =       ibis.literal(0),
            match_softmatch_count =     ibis.literal(0),
            hard_match_count=           ibis.literal(0),
            dropped=                    ibis.null(),
            dropped_by=                 ibis.null(),
            filter_all_false=           ibis.literal(False),
            filter_all_true=            ibis.literal(True)
        )

        return rules



    def _apply_filter_rule_wildcard(self, 
                                    rules: BaseDataFrame,  
                                    dimension: DimensionMetadata) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """
        rules = rules.mutate(

            filter1 = ibis.ifelse(condition=ibis._[dimension.name] == ibis.literal(self.UNKNOWN), 
                                true_expr=ibis.literal(self.PRIME_TRUE), 
                                false_expr=ibis.literal(self.PRIME_UNKNOWN) ),
        )

        return rules


    def _apply_filter_context_wildcard(self, 
                                        rules: BaseDataFrame,  
                                        context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """
        if context_value == self.UNKNOWN:
            rules = rules.mutate(
                filter2 = ibis.literal(self.PRIME_TRUE)
            )
        else:
            rules = rules.mutate(
                filter2 = ibis.literal(self.PRIME_UNKNOWN)
            )

        return rules



    def _apply_filter_simple_match(self, 
                                   rules: BaseDataFrame,  
                                   dimension: DimensionMetadata,  
                                   context_value: Any, 
                                   strict_context_types:bool) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """


        context_type_supported = self._is_context_type_supported(context_value)
        context_type = type(context_value)

        #Filter 3 is a direct comparison of the context value to the rule value
        if not context_type_supported:

            if strict_context_types is False:
                rules = rules.mutate(filter3 = ibis.literal(self.PRIME_UNKNOWN))
            else:
                rules = rules.mutate(filter3 = ibis.literal(self.PRIME_FALSE))

        else:

            if strict_context_types is False:
                rules = rules.mutate(
                    filter3 = ibis.ifelse(condition= ibis._[dimension.name].cast(context_type) == ibis.literal(context_value), 
                                        true_expr=ibis.literal(self.PRIME_TRUE), 
                                        false_expr=ibis.literal(self.PRIME_FALSE) )
                )
            else:
                rules = rules.mutate(
                    filter3 = ibis.ifelse(condition= ibis._[dimension.name] == ibis.literal(context_value), 
                                        true_expr=ibis.literal(self.PRIME_TRUE), 
                                        false_expr=ibis.literal(self.PRIME_FALSE) )
                )

        return rules



    @classmethod
    def _apply_filter_regex_match(cls, rules: BaseDataFrame,  dimension: DimensionMetadata,  context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """


        rules = rules.mutate(
            filter3 = ibis.ifelse(
                condition=ibis._[dimension.name].re_search(self.wildcard_to_regex(context_value)),
                true_expr=ibis.literal(self.PRIME_TRUE),
                false_expr=ibis.literal(self.PRIME_FALSE)
            )
        )

        return rules


    @classmethod
    def _apply_filter_range_match(cls, rules: BaseDataFrame,  dimension: str,  context_value: Any) -> BaseDataFrame:
        """
        Apply a filter rule to the rules table to check for a wildcard value.
        """

        rules = rules.mutate(

            filter3 = ibis.ifelse(
                condition=(
                    (ibis._[rules.range_min_value].isnull() | (ibis._[lower_col] <= ibis.literal(context_value))) &
                    (ibis._[context_value].isnull() | (ibis.literal(context_value) <= ibis._[upper_col]))
                ),
                true_expr=ibis.literal(cls.PRIME_TRUE),
                false_expr=ibis.literal(cls.PRIME_FALSE)
            )

        rules = rules.mutate(
            filter3 = ibis.ifelse(
                condition=ibis._[dimension].re_search(self.wildcard_to_regex(context_value)),
                true_expr=ibis.literal(self.PRIME_TRUE),
                false_expr=ibis.literal(self.PRIME_FALSE)
            )
        )

        return rules




    @classmethod
    def _apply_dimension_filter_flags(cls, rules: BaseDataFrame, dimension: str) -> BaseDataFrame:
        """
        Apply flags to the rules table to indicate the type of match for each dimension.
        """
        rules = rules.mutate(
            # Product of prime filters
            filter_product = ibis._.filter1 * ibis._.filter2 * ibis._.filter3

        ).mutate(

            #Flag across all 3 filters
            #How can we have any false when we have all softmatches...
            any_false = ibis._.filter_product % self.PRIME_FALSE == ibis.literal(0),
            any_true =  ibis._.filter_product % self.PRIME_TRUE  == ibis.literal(0),


            #Match Flags
            dimension_count=        ibis._.dimension_count            + ibis.literal(1).cast("int8"),
            rule_softmatch_count=   ibis._.rule_softmatch_count       + (ibis._.filter1 % self.PRIME_TRUE == 0).cast("int8"),
            context_softmatch_count=ibis._.context_softmatch_count    + (ibis._.filter2 % self.PRIME_TRUE == 0).cast("int8"),
            dual_softmatch_count=   ibis._.dual_softmatch_count       + ibis.and_((ibis._.filter1 % self.PRIME_TRUE == 0) & (ibis._.filter2 % self.PRIME_TRUE == 0)).cast("int8"),
            match_softmatch_count=ibis._.match_softmatch_count        + (ibis._.filter3 % self.PRIME_UNKNOWN == 0).cast("int8"),
            # dual_softmatch_count=   ibis._.dual_softmatch_count       + (ibis._.filter1 % self.PRIME_TRUE == 0 and ibis._.filter2 % self.PRIME_TRUE == 0).cast("int8"),
            any_softmatch_count=    ibis._.any_softmatch_count        + ibis.or_((ibis._.filter1 % self.PRIME_TRUE == 0) | (ibis._.filter2 % self.PRIME_TRUE == 0) | (ibis._.filter3 % self.PRIME_UNKNOWN == 0)).cast("int8"),
            # any_softmatch_count=    ibis._.any_softmatch_count       + (ibis._.filter1 % self.PRIME_TRUE == 0 or ibis._.filter2 % self.PRIME_TRUE == 0).cast("int8"),
            hard_match_count=       ibis._.hard_match_count           + (ibis._.filter3 % self.PRIME_TRUE == 0).cast("int8"),

        ).mutate(

            #Rolling Aggregates
            # all_false = ibis._.hard_match_count == 0,
            # all_true = ibis._.hard_match_count == 0,
            all_softmatch = ibis._.any_softmatch_count == ibis._.dimension_count,

        ).mutate(
            #Rule Row Drop Flags - This needs to NOT drop rows that have all softmatches
            dropped_by_dimension=             ibis.ifelse( condition=ibis._.dropped.isnull() & ibis._.any_false, 
                                                true_expr=ibis.literal(dimension), 
                                                false_expr=ibis._.dropped_by),
            dropped=                ibis.ifelse( condition=ibis._.dropped.isnull() & ibis._.any_false, 
                                                true_expr=ibis.literal(True), 
                                                false_expr=ibis._.dropped)
        )

        return rules



    def apply_context_rules_engine(self,
                                        context: dataclass, 
                                        dimension_names: List[Any],
                                        keep_all: bool=True,
                                        strict_context_types: bool = False
                                        ) -> BaseDataFrame:
                

        if not isinstance(self.rules, BaseDataFrame):
            rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(self.rules, ibis_backend_schema = "sqlite")

        if not isinstance(rules, BaseDataFrame):
            raise ValueError("Rules must be a BaseDataFrame")

        # Convert the rules to a backend that supports window functions        
        if rules.ibis_backend_schema in ["polars"]:
            rules = rules.convert_backend_schema("sqlite")


        # Validate Rules
        if rules.count() == int(0):
            raise ValueError("No rules specified.")
        
        active_dimensions = self.get_active_dimension_names(context, rules, dimension_names)


        # Initialization - add flags and counters to the rules
        rules = self._initialize_rule_flags(rules)

        # Apply Rules
        for dimension_name in active_dimensions:

            obj_dimension = self.lookup_rule_metadata[dimension_name]

            context_value = getattr(context, self.get_dimension_context_fieldname(dimension_name=dimension_name), self.UNKNOWN)

            rules = self._apply_filter_rule_wildcard(rules=rules, dimension=obj_dimension)
            rules = self._apply_filter_context_wildcard(rules=rules, context_value=obj_dimension)
            rules = self._apply_filter_simple_match(rules=rules, dimension=obj_dimension, context_value=context_value, strict_context_types=strict_context_types)
            rules = self._apply_dimension_filter_flags(rules=rules, dimension=obj_dimension)

        rules = rules.mutate(keep= ibis._.dropped.isnull())

        if keep_all:
            return rules
        else:
            return rules.filter(ibis._.keep)


