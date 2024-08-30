

from typing import List, Any,Optional, Dict, Type

import ibis
import ibis.expr.types as ir
from ibis.common.deferred import Deferred
from ibis.common.exceptions import IbisTypeError


from mountainash_data import BaseDataFrame, DataFrameFactory
import re
from pydantic import BaseModel
from enum import Enum
# import operator 

from mountainash_utils_rules.constants import MatchStrategy, RuleConstants


class Dimension(BaseModel):

    dimension_name: str
    context_field: Optional[str] = None
    rule_field: Optional[str] = None

    match_strategy: MatchStrategy = MatchStrategy.EXACT
    data_type: Type = str  # Default to string, but can be int, float, date, bool etc.
    
    valid_values: List[Any] = []  # List of possible values for the dimension
    
    range_min_field: Optional[str] = None  # Minimum value for the dimension
    range_max_field: Optional[str] = None   # Maximum value for the dimension
    range_min_inclusive: bool = True  # Whether the minimum value is inclusive
    range_max_inclusive: bool = True  # Whether the maximum value is inclusive


    def get_dimension_attribute(self, 
                                attribute: str, 
                                default_value: Any) -> Any:
        """
        Get the field name for the context for a given dimension.
        """
        value = getattr(self, attribute, default_value)
        if value is not None:
            return value
                
        return default_value

    def get_dimension_context_fieldname(self) -> str:
        """
        Get the field name for the context for a given dimension.
        """
        return self.get_dimension_attribute(attribute="context_field", default_value=self.dimension_name)

    def get_dimension_rule_fieldname(self) -> str:
        """
        Get the field name for the rule_field for a given dimension.
        """
        if self.match_strategy == MatchStrategy.RANGE:
            return self.get_dimension_rule_range_min_field()
        else:
            return self.get_dimension_attribute( attribute="rule_field", default_value=self.dimension_name)

    def get_dimension_match_strategy(self) -> MatchStrategy:
        """
        Get the field name for the match_strategy for a given dimension.
        """
        return self.get_dimension_attribute(attribute="match_strategy", default_value=MatchStrategy.EXACT)

    def get_dimension_data_type(self) -> Type:
        """
        Get the field name for the data_type for a given dimension.
        """
        return self.get_dimension_attribute( attribute="data_type", default_value=str)

    def get_dimension_rule_range_min_field(self) -> str:
        """
        Get the field name for the range_min_field for a given dimension.
        """
        range_min_field = self.get_dimension_attribute(attribute="range_min_field", default_value=None)

        if range_min_field is None:
            return self.get_dimension_rule_fieldname()
        else:
            return range_min_field

    def get_dimension_rule_range_max_field(self) -> str:
        """
        Get the field name for the range_max_field for a given dimension.
        """
        range_max_field = self.get_dimension_attribute(attribute="range_max_field", default_value=None)

        if range_max_field is None:
            return self.get_dimension_rule_fieldname()
        else:
            return range_max_field

    def get_dimension_rule_range_min_inclusive(self) -> bool:
        """
        Get the field name for the range_min_inclusive for a given dimension.
        """
        return self.get_dimension_attribute( attribute="range_min_inclusive", default_value=True)


    def get_dimension_rule_range_max_inclusive(self) -> bool:
        """
        Get the field name for the range_max_inclusive for a given dimension.
        """
        return self.get_dimension_attribute(attribute="range_max_inclusive", default_value=True)





class DimensionsMetadata(BaseModel):
    dimensions: List[Dimension]



# Metadata Manager
class MetadataManager:

    def __init__(self, 
                 rules: BaseDataFrame,
                 dimension_metadata: Optional[DimensionsMetadata] = None):

        self.raw_dimension_metadata: Optional[DimensionsMetadata]  = dimension_metadata

        self.lookup_dimension_metadata: Optional[Dict[str, Dimension]] = self._init_dimension_metadata(rules=rules,
                                                                                                       dimension_metadata=dimension_metadata)


    def _init_dimension_metadata(self, 
                                 rules: BaseDataFrame,
                                 dimension_metadata: Optional[DimensionsMetadata] = None) -> Optional[Dict[str, Dimension]]:
        """
        Validate the dimensions in the rule metadata.
        """

        if dimension_metadata is None:
            return None
        else:

            self._validate_unique_dimension_names(dimension_metadata=dimension_metadata)

            # Loop through 

            #validate the rule metadata
            for dimension in dimension_metadata.dimensions:

                #Validate Rule type has required fields in rules
                if dimension.match_strategy == MatchStrategy.RANGE:
                    self._validate_range_strategy_dimension( dimension=dimension)

                elif dimension.match_strategy ==  MatchStrategy.REGEX:
                    self._validate_regex_strategy_dimension( dimension=dimension)

                elif dimension.match_strategy ==  MatchStrategy.EXACT:
                    continue

                else:
                    raise ValueError(f"Dimension {dimension.dimension_name} has an invalid rule type: {dimension.match_strategy}")

            #If we get this far, set up the dimensions lookup!
            return {dimension.dimension_name: dimension for dimension in dimension_metadata.dimensions}


    ### Validators

    def _validate_range_strategy_dimension(self, dimension: Dimension) -> None:
        """
        Validate the range strategy dimension.
        """
        if dimension.match_strategy == MatchStrategy.RANGE:

            if dimension.data_type not in [int, float]:
                raise ValueError(f"Dimension {dimension.dimension_name} is of type RANGE but the data type is not int or float.")

            if dimension.range_min_field is None or dimension.range_max_field is None:
                raise ValueError(f"Dimension {dimension.dimension_name} is of type RANGE but no min/max fields are specified.")


    def _validate_regex_strategy_dimension(self, dimension: Dimension) -> None:
        """
        Validate the range strategy dimension.
        """
        if dimension.match_strategy ==  MatchStrategy.REGEX:

            if dimension.data_type is not str:
                raise ValueError(f"Dimension {dimension.dimension_name} is of type REGEX but the data type is not a string")



    def _validate_unique_dimension_names(self, 
                                         dimension_metadata: DimensionsMetadata) -> None:
            
            #validate names are unique:
            dimension_names = [dimension.dimension_name for dimension in dimension_metadata.dimensions]

            if len(dimension_names) != len(set(dimension_names)):
                raise ValueError("Dimension names must be unique.")


    ### Getters
    def get_dimension(self, 
                      dimension_name: str) -> Dimension:

        if self.lookup_dimension_metadata is not None and dimension_name in self.lookup_dimension_metadata:
            return self.lookup_dimension_metadata[dimension_name]
        else:
            return Dimension(dimension_name=dimension_name)


    def get_dimensions_list(self, 
                            dimension_names: List[str]) -> List[Dimension]:

        if self.lookup_dimension_metadata is not None:

            return [self.get_dimension(dimension_name=dimension_name) for dimension_name in dimension_names]
        else:
            return [Dimension(dimension_name=dimension_name) for dimension_name in dimension_names]
        


    def get_active_dimension_names(self, 
                                   context: BaseModel, 
                                   rules:   BaseDataFrame,
                                   dimension_names: List[str]
                                   ) -> List[str]:

        if dimension_names == []:
            raise ValueError("No dimension names specified") 
        

        #The fields the rule metadata asks for:
        expected_rule_fields:    Dict[str,str] = {dimension_name: self.get_dimension(dimension_name=dimension_name).get_dimension_rule_fieldname() for dimension_name in dimension_names}
        expected_context_fields: Dict[str,str] = {dimension_name: self.get_dimension(dimension_name=dimension_name).get_dimension_context_fieldname() for dimension_name in dimension_names}

        #The fields that actually exist
        actual_rule_fields:    Dict[str,str] = {dimension_name: fieldname
                                                for dimension_name, fieldname in expected_rule_fields.items() 
                                                if fieldname in rules.get_column_names()}

        actual_context_fields: Dict[str,str] = {dimension_name: fieldname
                                                for dimension_name, fieldname in expected_context_fields.items() 
                                                if getattr(context, fieldname, RuleConstants.NOT_SET) not in {RuleConstants.NOT_SET, None} }

        print(f"context: {context}")
        print(f"rules: {rules.get_column_names()}")

        print(f"actual_rule_fields: {actual_rule_fields}")
        print(f"actual_context_fields: {actual_context_fields}")

        print(f"expected_rule_fields: {expected_rule_fields}")
        print(f"expected_context_fields: {expected_context_fields}")


        #find the dimensions that have their fields active in the rules and the context
        active_context_dimensions =    [dimension_name for dimension_name in dimension_names if dimension_name in actual_context_fields.keys()]
        active_rule_dimensions =       [dimension_name for dimension_name in dimension_names if dimension_name in actual_rule_fields.keys()]

        print(f"active_context_dimensions: {active_context_dimensions}")
        print(f"active_rule_dimensions: {active_rule_dimensions}")

        #find the common elements in the context and the rules
        active_dimensions = list(set(active_context_dimensions).intersection(set(active_rule_dimensions)))

        print(f"active_dimensions: {active_dimensions}")

        #find the dimensions that are not in all sources:
        missing_dimensions = set(dimension_names) - set(active_dimensions)

        if missing_dimensions:
            print(f"Warning: Dimensons requested in rules_meatadata, but are missing in rules or context: {missing_dimensions}")
    
        if active_dimensions == []:
            raise ValueError("No active dimensions found in rules or context")        

        return active_dimensions
