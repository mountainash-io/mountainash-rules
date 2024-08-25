
from typing import List, Any,Type
from dataclasses import dataclass

import ibis
import ibis.expr.types as ir
from mountainash_data import BaseDataFrame, DataFrameFactory

# ibis.set_backend(backend="polars")
# from mountainash_data import BaseDataFrame

class RulesEngine:

    UNKNOWN = "<NA>"
    NOT_SET = "<NOT_SET>"

    # Flags for Prime Filtering
    PRIME_TRUE = 2
    PRIME_FALSE = 3
    PRIME_UNKNOWN = 5

    ALLOWED_CONTEXT_TYPES = (ir.IntegerScalar, ir.FloatingScalar, ir.BooleanScalar, ir.StringScalar)


    def __init__(self, rules: ir.Table):
        self.rules = rules


    @classmethod
    def _is_context_type_supported(cls, context_value: Any) -> bool:
        """
        Check if the column and literal value have compatible types.
        """
        try:
            if isinstance(ibis.literal(context_value), cls.ALLOWED_CONTEXT_TYPES):
                return True
            else:
                return False
        except Exception as e:
            return False


    @classmethod
    def apply_context_rules_engine(cls,
                                        CONTEXT: dataclass, 
                                        rules: BaseDataFrame|Any,  
                                        dimensions: List[Any],
                                        keep_all: bool=True,
                                        strict_context_types: bool = False
                                        ) -> BaseDataFrame:
                
        if not isinstance(rules, BaseDataFrame):
            rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules, ibis_backend_schema = "sqlite")

        if not isinstance(rules, BaseDataFrame):
            raise ValueError("Rules must be a BaseDataFrame")

        # Convert the rules to a backend that supports window functions        
        if rules.ibis_backend_schema in ["polars"]:
            rules = rules.convert_backend_schema("sqlite")


        # Validate Rules
        if rules.count() == int(0):
            raise ValueError("No rules specified.")

        
        # Initialization - add flags and counters to the rules
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

            context_type_supported = cls._is_context_type_supported(context_value)
            context_type = type(context_value)

            # Cast the rules column to the same type as the context value
            # rules, is_supported_type = cls._cast_column_to_context_type(rules, dimension, type(context_value))

            #Filter 3 is a direct comparison of the context value to the rule value
            if not context_type_supported:

                if strict_context_types is False:
                    rules = rules.mutate(filter3 = ibis.literal(cls.PRIME_UNKNOWN))
                else:
                    rules = rules.mutate(filter3 = ibis.literal(cls.PRIME_FALSE))

            else:

                if strict_context_types is False:
                    rules = rules.mutate(
                        filter3 = ibis.ifelse(condition= ibis._[dimension].cast(context_type) == ibis.literal(context_value), 
                                            true_expr=ibis.literal(cls.PRIME_TRUE), 
                                            false_expr=ibis.literal(cls.PRIME_FALSE) )
                    )
                else:
                    rules = rules.mutate(
                        filter3 = ibis.ifelse(condition= ibis._[dimension] == ibis.literal(context_value), 
                                            true_expr=ibis.literal(cls.PRIME_TRUE), 
                                            false_expr=ibis.literal(cls.PRIME_FALSE) )
                    )

            #Filter 1 is wild card for the rule
            rules = rules.mutate(

                filter1 = ibis.ifelse(condition=ibis._[dimension] == ibis.literal(cls.UNKNOWN), 
                                    true_expr=ibis.literal(cls.PRIME_TRUE), 
                                    false_expr=ibis.literal(cls.PRIME_UNKNOWN) ),
            )

            #Filter 2 is wild card for the context
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
                filter_product = ibis._.filter1 * ibis._.filter2 * ibis._.filter3

            ).mutate(

                #Flag across all 3 filters
                #How can we have any false when we have all softmatches...
                any_false = ibis._.filter_product % cls.PRIME_FALSE == ibis.literal(0),
                any_true =  ibis._.filter_product % cls.PRIME_TRUE  == ibis.literal(0),


                #Match Flags
                dimension_count=        ibis._.dimension_count            + ibis.literal(1).cast("int8"),
                rule_softmatch_count=   ibis._.rule_softmatch_count       + (ibis._.filter1 % cls.PRIME_TRUE == 0).cast("int8"),
                context_softmatch_count=ibis._.context_softmatch_count    + (ibis._.filter2 % cls.PRIME_TRUE == 0).cast("int8"),
                dual_softmatch_count=   ibis._.dual_softmatch_count       + (ibis._.filter1 % cls.PRIME_TRUE == 0).cast("int8") * (ibis._.filter2 % cls.PRIME_TRUE == 0).cast("int8"),
                match_softmatch_count=ibis._.match_softmatch_count    + (ibis._.filter3 % cls.PRIME_UNKNOWN == 0).cast("int8"),
                # dual_softmatch_count=   ibis._.dual_softmatch_count       + (ibis._.filter1 % cls.PRIME_TRUE == 0 and ibis._.filter2 % cls.PRIME_TRUE == 0).cast("int8"),
                any_softmatch_count=    ibis._.any_softmatch_count       + ((ibis._.filter1 % cls.PRIME_TRUE == 0).cast("int8") + (ibis._.filter2 % cls.PRIME_TRUE == 0).cast("int8") + (ibis._.filter3 % cls.PRIME_UNKNOWN == 0).cast("int8") > 0).cast("int8"),
                # any_softmatch_count=    ibis._.any_softmatch_count       + (ibis._.filter1 % cls.PRIME_TRUE == 0 or ibis._.filter2 % cls.PRIME_TRUE == 0).cast("int8"),
                hard_match_count=       ibis._.hard_match_count           + (ibis._.filter3 % cls.PRIME_TRUE == 0).cast("int8"),

            ).mutate(

                #Rolling Aggregates
                # all_false = ibis._.hard_match_count == 0,
                # all_true = ibis._.hard_match_count == 0,
                all_softmatch = ibis._.any_softmatch_count == ibis._.dimension_count,

            ).mutate(
                #Rule Row Drop Flags - This needs to NOT drop rows that have all softmatches
                dropped_by=             ibis.ifelse( condition=ibis._.dropped.isnull() & (ibis._.any_false | ~ibis._.any_true) & ~ibis._.all_softmatch, 
                                                    true_expr=ibis.literal(dimension), 
                                                    false_expr=ibis._.dropped_by),
                dropped=                ibis.ifelse( condition=ibis._.dropped.isnull() & (ibis._.any_false | ~ibis._.any_true) & ~ibis._.all_softmatch, 
                                                    true_expr=ibis.literal(True), 
                                                    false_expr=ibis._.dropped)
            )
                
        #End Loop


        rules = rules.mutate(keep= ibis._.dropped.isnull())

        if keep_all:
            return rules
        else:
            return rules.filter(ibis._.keep)




    # @staticmethod
    # def _simple_equality(column: ir.Column, context_value: Any) -> ir.BooleanColumn:
    #     """
    #     Performs a flexible equality check between a column and a value.
    #     Tries different type conversions to find a match.
    #     """
    #     # Try string comparison first

    #     match = ibis.literal(False)
    #     try:
    #         match = ibis.ifelse(condition= column == ibis.literal(context_value), 
    #                                                 true_expr=ibis.literal(cls.PRIME_TRUE), 
    #                                                 false_expr=ibis.literal(cls.PRIME_FALSE) )

    #         return match

    #     except Exception:           
    #         raise ValueError(f"Malformed Rule: Could not compare column {column} to context value {context_value}.")




    @classmethod
    def _simple_equality(cls, column: ir.Column, context_value: Any) -> ir.BooleanColumn:
        """
        Performs a flexible equality check between a column and a value.
        Handles int, float, bool, and string types.
        """
        # Ensure context_value is an ibis literal
        literal_value = ibis.literal(context_value)

        # Check if the types are compatible
        if not cls._are_types_compatible(column, literal_value):
            return ibis.literal(cls.PRIME_FALSE)
            # raise ValueError(f"Incompatible types: Column type {column.type()} and value type {type(context_value)}")

        # Perform the comparison
        try:
            return ( ibis.case()
                .when(column == literal_value, ibis.literal(SimpleEqualityChecker.PRIME_TRUE))
                .else_(ibis.literal(SimpleEqualityChecker.PRIME_FALSE))
                .end()
            )
        except Exception as e:
            raise ValueError(f"Malformed Rule: Could not compare column {column} to context value {context_value}. Error: {str(e)}")

    @classmethod
    def _are_types_compatible(cls, column: ir.Column, literal_value: Any) -> bool:
        """
        Check if the column and literal value have compatible types.
        """
        allowed_types = (ir.IntegerColumn, ir.FloatingColumn, ir.BooleanColumn, ir.StringColumn)

        if isinstance(column, allowed_types) and isinstance(literal_value, allowed_types):
            return True
        else:
            return False




    @staticmethod
    def _flexible_equality(column: ir.Column, value: Any) -> ir.BooleanColumn:
        """
        Performs a flexible equality check between a column and a value.
        Tries different type conversions to find a match.
        """
        # Try string comparison first

        string_match = ibis.literal(False)
        numeric_match = ibis.literal(False)
        try:
            string_match = column.cast('string') == ibis.literal(str(value))
        except ValueError:
            string_match = ibis.literal(False)
        except TypeError:
            string_match = ibis.literal(False)
        finally:
            string_match = string_match.ifelse(ibis.literal(True), ibis.literal(False))


        # If the value is numeric, try numeric comparison
        try:
            numeric_match = column.cast('double') == ibis.literal(float(value))
        except ValueError:
            numeric_match = ibis.literal(False)
        except TypeError:
            numeric_match = ibis.literal(False)
        finally:
            numeric_match = numeric_match.ifelse(ibis.literal(RulesEngine.PRIME_TRUE), ibis.literal(RulesEngine.PRIME_FALSE))

        return (string_match | numeric_match).ifelse(ibis.literal(RulesEngine.PRIME_TRUE), ibis.literal(RulesEngine.PRIME_FALSE))


            # numeric_match = ibis.try_(column.cast('double') == ibis.literal(float(value)))
            # return (string_match | numeric_match).ifelse(ibis.literal(RulesEngine.PRIME_TRUE), ibis.literal(RulesEngine.PRIME_FALSE))
        
        # For non-numeric types, just use string comparison
        # return string_match.ifelse(ibis.literal(RulesEngine.PRIME_TRUE), ibis.literal(RulesEngine.PRIME_FALSE))




    @staticmethod
    def _cast_column_to_context_type(rules: BaseDataFrame, column: str, target_type: Type) -> tuple[BaseDataFrame, bool]:
        is_supported_type = True
        if target_type == str:
            return rules.mutate(**{column: ibis._[column].cast('string')}), is_supported_type
        elif target_type == int:
            return rules.mutate(**{column: ibis._[column].cast('int64')}), is_supported_type
        elif target_type == float:
            return rules.mutate(**{column: ibis._[column].cast('float64')}), is_supported_type
        elif target_type == bool:
            return rules.mutate(**{column: ibis._[column].cast('boolean')}), is_supported_type
        else:
            # For unsupported types, we'll leave it as is and set is_supported_type to False
            return rules, False