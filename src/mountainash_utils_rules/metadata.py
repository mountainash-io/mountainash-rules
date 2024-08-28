

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


class DimensionMetadata(BaseModel):

    dimension_name: str
    context_field: Optional[str] = None
    rule_field: Optional[str] = None

    rule_type: RuleType = RuleType.EXACT
    data_type: str = "string"  # Default to string, but can be int, float, date, bool etc.
    
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
        if self.rule_type == RuleType.RANGE:
            return self.get_dimension_rule_range_min_field()
        else:
            return self.get_dimension_attribute( attribute="rule_field", default_value=self.dimension_name)

    def get_dimension_rule_type(self) -> RuleType:
        """
        Get the field name for the rule_type for a given dimension.
        """
        return self.get_dimension_attribute(attribute="rule_type", default_value=RuleType.EXACT)

    def get_dimension_data_type(self) -> str:
        """
        Get the field name for the data_type for a given dimension.
        """
        return self.get_dimension_attribute( attribute="data_type", default_value="string")

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





class RuleMetadata(BaseModel):
    dimensions: List[DimensionMetadata]



# Metadata Manager
class MetadataManager:

    def __init__(self, rule_metadata: RuleMetadata):

        self.raw_rule_metadata: RuleMetadata = rule_metadata
        self.lookup_rule_metadata: Dict[str, DimensionMetadata] = self._init_rule_metadata(rule_metadata=rule_metadata)



    def _init_rule_metadata(self, rule_metadata: Optional[RuleMetadata] = None) -> Dict[str, DimensionMetadata]:
        """
        Validate the dimensions in the rule metadata.
        """

        if rule_metadata is None:
            raise ValueError("No rule metadata provided")
        else:

            self._validate_unique_dimension_names(rule_metadata=rule_metadata)

            #validate the rule metadata
            for dimension in rule_metadata.dimensions:

                if dimension.rule_type == RuleType.RANGE:
                    if dimension.range_min_field is None or dimension.range_max_field is None:
                        raise ValueError(f"Dimension {dimension.dimension_name} is of type RANGE but no min/max fields are specified.")
                    
                elif dimension.rule_type in {RuleType.REGEX, RuleType.EXACT }:
                    continue

                else:
                    raise ValueError(f"Dimension {dimension.dimension_name} has an invalid rule type: {dimension.rule_type}")


            #If we get this far, set up the dimensions lookup!
            return {dimension.dimension_name: dimension for dimension in rule_metadata.dimensions}


    def _validate_unique_dimension_names(self, rule_metadata: RuleMetadata) -> None:
            #validate names are unique:
            dimension_names = [dimension.dimension_name for dimension in rule_metadata.dimensions]

            if len(dimension_names) != len(set(dimension_names)):
                raise ValueError("Dimension names must be unique.")



    def get_dimension(self, dimension_name: str) -> DimensionMetadata:

        if self.lookup_rule_metadata is not None and dimension_name in self.lookup_rule_metadata:
            return self.lookup_rule_metadata[dimension_name]
        else:
            raise ValueError("Rule metadata not initialized")


    def get_dimensions_list(self, dimension_names: List[str]) -> List[DimensionMetadata]:

        return [self.get_dimension(dimension_name=dimension_name) for dimension_name in dimension_names]


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
                                                if getattr(context, fieldname, RuleConstants.NOT_SET) is not RuleConstants.NOT_SET}
        

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
