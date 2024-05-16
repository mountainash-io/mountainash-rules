
from typing import List, Any,Type
from dataclasses import dataclass

import ibis
import ibis.expr.types as ir

ibis.set_backend(backend="polars")
# from mountainash_data import BaseDataFrame

UNKNOWN = "<NA>"
NOT_SET = object()

# Flags for Prime Filtering
PRIME_TRUE = 2
PRIME_FALSE = 3
PRIME_UNKNOWN = 5

#TODO: Need to be more forgiving with over/underlap of dimensions on the rules vs context


def apply_context_rules_engine_ibis(CONTEXT: Type[dataclass], 
                                    rules: ir.Table,  
                                    dimensions: List[Any],
                                    keep_all: bool=True) -> ir.Table:
            
    # Validate Rules
    if rules.count().execute() == 0:
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
    context_dimensions = [dim for dim in dimensions if getattr(CONTEXT, dim, NOT_SET) is not NOT_SET]
    rule_dimensions = [dim for dim in dimensions if dim in rules.columns]

    #find the common elements in the context and the rules
    active_dimensions = list(set(context_dimensions).union(set(rule_dimensions)))

    #find the dimensions that are not in all sources:
    missing_dimensions = set(dimensions) - set(active_dimensions)

    if missing_dimensions:
        print(f"Dimensons missing in rules or context: {missing_dimensions}")
 

    #What to do with dimensions that are in the context but not in the rules, and vice-versa?
    #Are missing dimensions a problem? Maybe create a warning, but run it anyway.

    print(f"Active dimensions: {active_dimensions}")

    # Apply Rules
    for dimension in active_dimensions:
        print(f"Evaluating dimension: {dimension}")
        # if getattr(CONTEXT, dimension) is not None:

        context_value = getattr(CONTEXT, dimension)
        print(f"context_value: {context_value}")
        
        #Build filters for:
        # 1. Rule dimension is UNKNOWN - soft match
        # 2. Context dimension is UNKNOWN - soft match
        # 3. Rule dimension matches context dimension
        filter1 = ibis.ifelse(condition=rules[dimension] == ibis.literal(UNKNOWN), 
                              true_expr=ibis.literal(PRIME_TRUE), 
                              false_expr=ibis.literal(PRIME_UNKNOWN) )
        filter2 = ibis.ifelse(condition=ibis.literal(context_value == UNKNOWN), 
                              true_expr=ibis.literal(PRIME_TRUE), 
                              false_expr=ibis.literal(PRIME_UNKNOWN) )
        filter3 = ibis.ifelse(condition=rules[dimension] == ibis.literal(context_value), 
                              true_expr=ibis.literal(PRIME_TRUE), 
                              false_expr=ibis.literal(PRIME_FALSE) )
        
        #Apply the filters to the rules
        rules = rules.mutate(

            filter1 =      filter1,
            filter2 =      filter2,
            filter3 =      filter3,

            # Product of prime filters
            filter_product =    (filter1 * filter2 * filter3),

        ).mutate(

            #Flag across all 3 filters
            any_false = ibis._.filter_product % PRIME_FALSE == 0,
            any_true = ibis._.filter_product % PRIME_TRUE == 0,

            #Match Flags
            rule_softmatch_count=   ibis._.rule_softmatch_count       + (ibis._.filter1 % PRIME_TRUE == 0).cast("int8"),
            context_softmatch_count=ibis._.context_softmatch_count    + (ibis._.filter2 % PRIME_TRUE == 0).cast("int8"),
            dual_softmatch_count=   ibis._.dual_softmatch_count       + (ibis._.filter1 % PRIME_TRUE == 0).cast("int8") * (ibis._.filter2 % PRIME_TRUE == 0).cast("int8"),
            hard_match_count=       ibis._.hard_match_count           + (ibis._.filter3 % PRIME_TRUE == 0).cast("int8"),

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


