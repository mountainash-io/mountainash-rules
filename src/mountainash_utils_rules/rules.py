
from typing import List, Dict,  Set, Any, Optional
from upath import UPath
from dataclasses import dataclass

import polars as pl
import ibis
import ibis.expr.types as ir
from typing import Dict, Any


UNKNOWN = "<NA>"

@dataclass
class context:
    rule_name:  Optional[str]
    DIM_1:      Optional[str]
    DIM_2:      Optional[str]
    DIM_3:      Optional[str]

CONTEXT = context(rule_name="rule_1", DIM_1="A", DIM_2="1", DIM_3=UNKNOWN)


rules = pl.DataFrame({  "rule_name": ["rule_1", "rule_2", "rule_3"],
                        "DIM_1": ["A", "B", "C"],
                        "DIM_2": ["1", "2", "3"],
                        "DIM_3": ["X", UNKNOWN, UNKNOWN]
                    })

dimensions = ["DIM_1", "DIM_2", "DIM_3"]




def apply_context_rules_engine_ibis(CONTEXT: object, rules: ir.Table, dimensions: List[Any]) -> ir.Table:
    
    # Validate Dimensions
    valid_dimensions = all(dim in rules.columns for dim in dimensions)
    if not valid_dimensions:
        raise ValueError("Invalid dimensions specified.")
    
    # Validate Rules
    if rules.count().execute() == 0:
        raise ValueError("No rules specified.")
    
    # Initialization
    rules = rules.mutate(
        rule_softmatch_count=ibis.literal(0),
        context_softmatch_count=ibis.literal(0),
        hard_match_count=ibis.literal(0),
        dropped=ibis.NA,
        dropped_by=ibis.NA,
        filter_all_false=ibis.literal(False),
        filter_all_true=ibis.literal(True)
    )
    
    # Apply Rules
    for dimension in dimensions:
        if getattr(CONTEXT, dimension) is not None and dimension in rules.columns:

            context_value = getattr(CONTEXT, dimension)
            
            filter1 = (rules[dimension] == UNKNOWN).ifelse(ibis.literal(True), ibis.literal(False)).name('filter1')
            filter2 = ibis.literal(context_value == UNKNOWN).ifelse(ibis.literal(True), ibis.literal(False)).name('filter2')
            filter3 = (rules[dimension] == ibis.literal(context_value)).name('filter3')
            
            rules = rules.mutate(
                rule_softmatch_count=   rules['rule_softmatch_count']       + filter1.cast('int8'),
                context_softmatch_count=rules['context_softmatch_count']    + filter2.cast('int8'),
                hard_match_count=       rules['hard_match_count']           + filter3.cast('int8'),

                dropped_by=             ibis.ifelse( (rules['dropped'].isnull()) & ~(filter1 | filter2 | filter3), 
                                                        ibis.literal(dimension), 
                                                        rules['dropped_by']),
                dropped=                ibis.ifelse( (rules['dropped'].isnull()) & ~(filter1 | filter2 | filter3), 
                                                        ibis.literal(True), 
                                                        rules['dropped'])
            )
    
    rules = rules.mutate(keep=rules['dropped'].isnull())
    
    return rules

