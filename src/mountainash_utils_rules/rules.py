
from typing import List, Any,Type
from dataclasses import dataclass

import ibis
import ibis.expr.types as ir
from mountainash_data import BaseDataFrame, IbisDataFrame, DataFrameFactory

# ibis.set_backend(backend="polars")
# from mountainash_data import BaseDataFrame

class RulesEngine:

    UNKNOWN = "<NA>"
    NOT_SET = object()

    # Flags for Prime Filtering
    PRIME_TRUE = 2
    PRIME_FALSE = 3
    PRIME_UNKNOWN = 5


    def __init__(self, rules: ir.Table):
        self.rules = rules

    @classmethod
    def apply_context_rules_engine(cls,
                                        CONTEXT: Type[dataclass], 
                                        rules: BaseDataFrame|Any,  
                                        dimensions: List[Any],
                                        keep_all: bool=True
                                        ) -> BaseDataFrame:
                
        if not isinstance(rules, BaseDataFrame):
            rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules, ibis_backend_schema = "sqlite")

        if not isinstance(rules, BaseDataFrame):
            raise ValueError("Rules must be a BaseDataFrame")

        # Convert the rules to a backend that supports window functions        
        if rules.ibis_backend_schema in ("polars", "pandas"):
            rules = rules.convert_backend_schema("sqlite")


        # Validate Rules
        if rules.count() == int(0):
            raise ValueError("No rules specified.")

        
        # Initialization - add flags and counters to the rules
        rules = rules.mutate(
            rule_softmatch_count=       ibis.literal(0),
            context_softmatch_count=    ibis.literal(0),
            dual_softmatch_count =      ibis.literal(0),
            hard_match_count=           ibis.literal(0),
            dropped=                    ibis.NA,
            dropped_by=                 ibis.NA,
            filter_all_false=           ibis.literal(False),
            filter_all_true=            ibis.literal(True)
        )
        
        #identify dimensions in Context
        context_dimensions = [dim for dim in dimensions if getattr(CONTEXT, dim, cls.NOT_SET) is not cls.NOT_SET]
        rule_dimensions = [dim for dim in dimensions if dim in rules.get_column_names()]

        #find the common elements in the context and the rules
        active_dimensions = list(set(context_dimensions).union(set(rule_dimensions)))

        #find the dimensions that are not in all sources:
        missing_dimensions = set(dimensions) - set(active_dimensions)

        if missing_dimensions:
            print(f"Dimensons missing in rules or context: {missing_dimensions}")
    
        if active_dimensions == []:
            raise ValueError("No active dimensions found in rules or context")


        #What to do with dimensions that are in the context but not in the rules, and vice-versa?
        #Are missing dimensions a problem? Maybe create a warning, but run it anyway.

        # print(f"Active dimensions: {active_dimensions}")

        # Apply Rules
        for dimension in active_dimensions:
            # print(f"Evaluating dimension: {dimension}")

            context_value = getattr(CONTEXT, dimension)

            #Apply the filters to the rules
            rules = rules.mutate(

                filter1 = ibis.ifelse(condition=ibis._[dimension] == ibis.literal(cls.UNKNOWN), 
                                    true_expr=ibis.literal(cls.PRIME_TRUE), 
                                    false_expr=ibis.literal(cls.PRIME_UNKNOWN) ),
                filter3 = ibis.ifelse(condition=ibis._[dimension] == ibis.literal(context_value), 
                                    true_expr=ibis.literal(cls.PRIME_TRUE), 
                                    false_expr=ibis.literal(cls.PRIME_FALSE) )

            )
            #Rule 2 needs to be controlled outside the ibis expression        
            if context_value == cls.UNKNOWN:
                rules = rules.mutate(
                    filter2 = ibis.literal(cls.PRIME_TRUE)
                )
            else:
                rules = rules.mutate(
                    filter2 = ibis.literal(cls.PRIME_UNKNOWN)
                )
            
            
            rules = rules.mutate(
                # Product of prime filters
                filter_product = ibis._.filter1 * ibis._.filter2 * ibis._.filter3,

            ).mutate(

                #Flag across all 3 filters
                any_false = ibis._.filter_product % cls.PRIME_FALSE == ibis.literal(0),
                any_true =  ibis._.filter_product % cls.PRIME_TRUE  == ibis.literal(0),

                #Match Flags
                rule_softmatch_count=   ibis._.rule_softmatch_count       + (ibis._.filter1 % cls.PRIME_TRUE == 0).cast("int8"),
                context_softmatch_count=ibis._.context_softmatch_count    + (ibis._.filter2 % cls.PRIME_TRUE == 0).cast("int8"),
                dual_softmatch_count=   ibis._.dual_softmatch_count       + (ibis._.filter1 % cls.PRIME_TRUE == 0).cast("int8") * (ibis._.filter2 % cls.PRIME_TRUE == 0).cast("int8"),
                hard_match_count=       ibis._.hard_match_count           + (ibis._.filter3 % cls.PRIME_TRUE == 0).cast("int8"),

            ).mutate(

                #Rule Row Drop Flags
                dropped_by=             ibis.ifelse( condition=ibis._.dropped.isnull() & ~ibis._.any_true, 
                                                    true_expr=ibis.literal(dimension), 
                                                    false_expr=ibis._.dropped_by),
                dropped=                ibis.ifelse( condition=ibis._.dropped.isnull() & ~ibis._.any_true, 
                                                    true_expr=ibis.literal(True), 
                                                    false_expr=ibis._.dropped)
            )


        rules = rules.mutate(keep= ibis._.dropped.isnull())

        if keep_all:
            return rules
        else:
            return rules.filter(ibis._.keep)



