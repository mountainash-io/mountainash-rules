"""
Numpy-based high-performance rule processor for vectorized rule evaluation.

This module implements vectorized rule evaluation using numpy arrays, leveraging the
mathematical elegance of the prime-based ternary flag system for maximum performance.

Key architectural features:
- Vectorized operations for all match strategies (exact, range, regex)
- Prime-based ternary logic (PRIME_TRUE=2, PRIME_FALSE=3, PRIME_UNKNOWN=5)
- Precompiled regex patterns with caching
- Memory-efficient array operations
- One-time rule data extraction to numpy arrays
"""

import re
import numpy as np
import polars as pl
from typing import Dict, List, Optional, Any, Union, Pattern, Tuple
from dataclasses import dataclass
from functools import lru_cache

from mountainash_dataframes import BaseDataFrame
from mountainash_utils_rules.constants import RuleTrinaryFlags, MatchStrategy
from mountainash_utils_rules.dimension import Dimension


@dataclass
class NumpyRuleData:
    """Container for numpy-converted rule data optimized for vectorized operations."""
    
    # Core rule information
    rule_names: np.ndarray          # String array of rule names
    rule_count: int                 # Total number of rules
    
    # Dimension data organized by strategy type for optimal vectorization
    exact_dimensions: Dict[str, np.ndarray]     # Exact match values
    range_dimensions: Dict[str, Tuple[np.ndarray, np.ndarray]]  # (min, max) arrays
    regex_dimensions: Dict[str, List[Pattern]]   # Precompiled regex patterns
    
    # Dimension metadata for validation
    dimension_types: Dict[str, type]            # Dimension data types
    dimension_strategies: Dict[str, MatchStrategy]  # Dimension match strategies


class NumpyMatchEngine:
    """High-performance vectorized matching engine using numpy operations."""
    
    def __init__(self):
        self._regex_cache: Dict[str, Pattern] = {}
    
    @lru_cache(maxsize=1000)
    def _compile_regex(self, pattern: str) -> Pattern:
        """Compile and cache regex patterns for optimal performance."""
        return re.compile(pattern)
    
    def exact_match_vectorized(self, 
                              context_value: Union[str, int, float], 
                              rule_values: np.ndarray) -> np.ndarray:
        """
        Vectorized exact matching using numpy comparison operations.
        
        Returns prime-based ternary flags:
        - PRIME_TRUE (2) for matches
        - PRIME_FALSE (3) for non-matches  
        - PRIME_UNKNOWN (5) for null/invalid values
        """
        # Handle null/invalid values using numpy-compatible operations
        if rule_values.dtype.kind in ['U', 'S', 'O']:  # String types
            null_mask = (rule_values == None) | (rule_values == '') | (rule_values == 'None')
        else:  # Numeric types
            try:
                numeric_values = rule_values.astype(float, errors='ignore')
                null_mask = np.isnan(numeric_values) | np.isinf(numeric_values)
            except (ValueError, TypeError):
                null_mask = rule_values == None
        
        # Vectorized comparison - handle type compatibility
        try:
            matches = np.equal(rule_values, context_value)
        except (ValueError, TypeError):
            # Type mismatch - no matches possible
            matches = np.zeros(len(rule_values), dtype=bool)
        
        # Apply prime-based ternary logic
        result = np.where(
            null_mask,
            RuleTrinaryFlags.PRIME_UNKNOWN,
            np.where(matches, RuleTrinaryFlags.PRIME_TRUE, RuleTrinaryFlags.PRIME_FALSE)
        )
        
        return result.astype(np.int32)
    
    def range_match_vectorized(self,
                              context_value: Union[int, float],
                              min_values: np.ndarray,
                              max_values: np.ndarray) -> np.ndarray:
        """
        Vectorized range matching using numpy comparison operations.
        
        Returns prime-based ternary flags for range inclusion.
        """
        # Handle null/invalid values using numpy operations
        try:
            min_float = min_values.astype(float)
            min_null_mask = np.isnan(min_float) | np.isinf(min_float)
        except (ValueError, TypeError):
            min_null_mask = (min_values == None) | (min_values == '')
        
        try:
            max_float = max_values.astype(float)
            max_null_mask = np.isnan(max_float) | np.isinf(max_float)
        except (ValueError, TypeError):
            max_null_mask = (max_values == None) | (max_values == '')
        
        # Check if context value is valid
        try:
            context_float = float(context_value)
            context_null = np.isnan(context_float) or np.isinf(context_float)
        except (ValueError, TypeError):
            context_null = True
        
        if context_null:
            return np.full(len(min_values), RuleTrinaryFlags.PRIME_UNKNOWN, dtype=np.int32)
        
        # Vectorized range comparison
        try:
            within_min = context_float >= min_float
            within_max = context_float <= max_float
            in_range = within_min & within_max
        except (ValueError, TypeError):
            # Comparison failed - mark as unknown
            in_range = np.zeros(len(min_values), dtype=bool)
            min_null_mask = np.ones(len(min_values), dtype=bool)
        
        # Apply prime-based ternary logic
        result = np.where(
            min_null_mask | max_null_mask,
            RuleTrinaryFlags.PRIME_UNKNOWN,
            np.where(in_range, RuleTrinaryFlags.PRIME_TRUE, RuleTrinaryFlags.PRIME_FALSE)
        )
        
        return result.astype(np.int32)
    
    def regex_match_vectorized(self,
                              context_value: str,
                              patterns: List[Pattern]) -> np.ndarray:
        """
        Vectorized regex matching using precompiled patterns.
        
        Returns prime-based ternary flags for pattern matches.
        """
        if not isinstance(context_value, str):
            return np.full(len(patterns), RuleTrinaryFlags.PRIME_UNKNOWN, dtype=np.int32)
        
        # Vectorized regex evaluation
        results = np.zeros(len(patterns), dtype=np.int32)
        
        for i, pattern in enumerate(patterns):
            if pattern is None:
                results[i] = RuleTrinaryFlags.PRIME_UNKNOWN
            else:
                try:
                    match_result = pattern.match(context_value) is not None
                    results[i] = RuleTrinaryFlags.PRIME_TRUE if match_result else RuleTrinaryFlags.PRIME_FALSE
                except Exception:
                    results[i] = RuleTrinaryFlags.PRIME_UNKNOWN
        
        return results


class NumpyRuleProcessor:
    """
    High-performance numpy-based rule processor leveraging vectorized operations
    and the mathematical elegance of prime-based ternary logic.
    
    This processor provides significant performance improvements over ibis-based
    evaluation through:
    - One-time rule data extraction to numpy arrays
    - Vectorized boolean operations for all match strategies  
    - Precompiled regex patterns for maximum efficiency
    - Prime arithmetic for efficient ternary state management
    """
    
    def __init__(self, rules: BaseDataFrame, dimensions: List[Dimension]):
        """
        Initialize the numpy processor with rule data and dimension metadata.
        
        Args:
            rules: BaseDataFrame containing rule definitions
            dimensions: List of dimension metadata for validation and processing
        """
        self.match_engine = NumpyMatchEngine()
        self.rule_data = self._extract_rule_data(rules, dimensions)
    
    def _extract_rule_data(self, rules: BaseDataFrame, dimensions: List[Dimension]) -> NumpyRuleData:
        """
        Extract rule data into optimized numpy arrays for vectorized processing.
        
        This method performs one-time conversion of ibis/polars data to numpy
        arrays organized by match strategy for optimal vectorization performance.
        """
        # Convert to pandas for numpy extraction (with improved compatibility)
        try:
            if hasattr(rules, 'to_pandas'):
                rules_df = rules.to_pandas()
            elif hasattr(rules, 'ibis_table') and hasattr(rules.ibis_table, 'to_pandas'):
                rules_df = rules.ibis_table.to_pandas()
            elif hasattr(rules, 'to_polars') and hasattr(rules.to_polars(), 'to_pandas'):
                rules_df = rules.to_polars().to_pandas()
            else:
                raise ValueError("Unable to convert rules to pandas DataFrame")
        except Exception as e:
            raise ValueError(f"Failed to extract rule data for numpy processing: {e}")
        
        # Extract rule names
        rule_names = rules_df.get('rule_name', rules_df.index).values
        rule_count = len(rule_names)
        
        # Organize data by match strategy for vectorization
        exact_dimensions = {}
        range_dimensions = {}
        regex_dimensions = {}
        dimension_types = {}
        dimension_strategies = {}
        
        for dimension in dimensions:
            dim_name = dimension.dimension_name
            dimension_types[dim_name] = dimension.data_type
            dimension_strategies[dim_name] = dimension.match_strategy
            
            if dimension.match_strategy == MatchStrategy.EXACT:
                # Extract exact match values
                if dim_name in rules_df.columns:
                    exact_dimensions[dim_name] = rules_df[dim_name].values
                else:
                    exact_dimensions[dim_name] = np.full(rule_count, None)
            
            elif dimension.match_strategy == MatchStrategy.RANGE:
                # Extract range values (min, max)
                min_field = dimension.range_min_field or f"{dim_name}_MIN"
                max_field = dimension.range_max_field or f"{dim_name}_MAX"
                
                min_values = rules_df.get(min_field, np.full(rule_count, None)).values
                max_values = rules_df.get(max_field, np.full(rule_count, None)).values
                range_dimensions[dim_name] = (min_values, max_values)
            
            elif dimension.match_strategy == MatchStrategy.REGEX:
                # Precompile regex patterns
                if dim_name in rules_df.columns:
                    patterns = []
                    for pattern_str in rules_df[dim_name].values:
                        if pattern_str is None or pattern_str == '' or str(pattern_str).lower() == 'none':
                            patterns.append(None)
                        else:
                            try:
                                patterns.append(self.match_engine._compile_regex(str(pattern_str)))
                            except re.error:
                                patterns.append(None)
                    regex_dimensions[dim_name] = patterns
                else:
                    regex_dimensions[dim_name] = [None] * rule_count
        
        return NumpyRuleData(
            rule_names=rule_names,
            rule_count=rule_count,
            exact_dimensions=exact_dimensions,
            range_dimensions=range_dimensions,
            regex_dimensions=regex_dimensions,
            dimension_types=dimension_types,
            dimension_strategies=dimension_strategies
        )
    
    def evaluate_context_vectorized(self, 
                                   context_values: Dict[str, Any], 
                                   active_dimensions: List[str]) -> np.ndarray:
        """
        Perform vectorized rule evaluation for the given context and dimensions.
        
        This method leverages the mathematical elegance of prime-based ternary logic
        to efficiently compute rule matches across all dimensions simultaneously.
        
        Args:
            context_values: Dictionary of context values for each dimension
            active_dimensions: List of dimension names to evaluate
            
        Returns:
            numpy array of prime-based flags indicating rule matches:
            - PRIME_TRUE (2): Rule matches
            - PRIME_FALSE (3): Rule doesn't match  
            - PRIME_UNKNOWN (5): Unable to determine match
        """
        # Initialize result array with PRIME_TRUE (all rules start as potential matches)
        result_flags = np.full(self.rule_data.rule_count, RuleTrinaryFlags.PRIME_TRUE, dtype=np.int32)
        
        # Evaluate each active dimension
        for dim_name in active_dimensions:
            if dim_name not in context_values:
                # Missing context value - mark all as PRIME_UNKNOWN
                result_flags = np.where(
                    result_flags == RuleTrinaryFlags.PRIME_TRUE,
                    RuleTrinaryFlags.PRIME_UNKNOWN,
                    result_flags
                )
                continue
            
            context_value = context_values[dim_name]
            strategy = self.rule_data.dimension_strategies.get(dim_name)
            
            # Evaluate based on match strategy
            if strategy == MatchStrategy.EXACT:
                dimension_flags = self.match_engine.exact_match_vectorized(
                    context_value, 
                    self.rule_data.exact_dimensions[dim_name]
                )
            elif strategy == MatchStrategy.RANGE:
                min_vals, max_vals = self.rule_data.range_dimensions[dim_name]
                dimension_flags = self.match_engine.range_match_vectorized(
                    context_value, min_vals, max_vals
                )
            elif strategy == MatchStrategy.REGEX:
                dimension_flags = self.match_engine.regex_match_vectorized(
                    context_value, 
                    self.rule_data.regex_dimensions[dim_name]
                )
            else:
                # Unknown strategy - mark as PRIME_UNKNOWN
                dimension_flags = np.full(self.rule_data.rule_count, RuleTrinaryFlags.PRIME_UNKNOWN, dtype=np.int32)
            
            # Apply prime-based logic for combining dimension results
            # Rules must match ALL dimensions to be considered a match
            result_flags = self._combine_dimension_flags(result_flags, dimension_flags)
        
        return result_flags
    
    def _combine_dimension_flags(self, 
                                current_flags: np.ndarray, 
                                dimension_flags: np.ndarray) -> np.ndarray:
        """
        Combine dimension evaluation results using prime-based ternary logic.
        
        The mathematical properties of prime numbers provide elegant logic:
        - PRIME_TRUE (2) AND PRIME_TRUE (2) = PRIME_TRUE (2)
        - PRIME_TRUE (2) AND PRIME_FALSE (3) = PRIME_FALSE (3)  
        - Any combination with PRIME_UNKNOWN (5) = PRIME_UNKNOWN (5)
        
        This leverages numpy's vectorized operations for maximum performance.
        """
        # Handle UNKNOWN propagation (highest priority)
        unknown_mask = (current_flags == RuleTrinaryFlags.PRIME_UNKNOWN) | (dimension_flags == RuleTrinaryFlags.PRIME_UNKNOWN)
        
        # Handle FALSE propagation (any FALSE makes the overall result FALSE)
        false_mask = (current_flags == RuleTrinaryFlags.PRIME_FALSE) | (dimension_flags == RuleTrinaryFlags.PRIME_FALSE)
        
        # Combine using vectorized operations
        result = np.where(
            unknown_mask,
            RuleTrinaryFlags.PRIME_UNKNOWN,
            np.where(
                false_mask,
                RuleTrinaryFlags.PRIME_FALSE,
                RuleTrinaryFlags.PRIME_TRUE
            )
        )
        
        return result.astype(np.int32)
    
    def get_matching_rules(self, context_values: Dict[str, Any], active_dimensions: List[str]) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get matching rule names and their evaluation flags.
        
        Returns:
            Tuple of (rule_names, flags) for matching rules
        """
        flags = self.evaluate_context_vectorized(context_values, active_dimensions)
        matching_mask = flags == RuleTrinaryFlags.PRIME_TRUE
        
        return self.rule_data.rule_names[matching_mask], flags[matching_mask]
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance-related statistics about the processor."""
        return {
            'rule_count': self.rule_data.rule_count,
            'exact_dimensions': len(self.rule_data.exact_dimensions),
            'range_dimensions': len(self.rule_data.range_dimensions), 
            'regex_dimensions': len(self.rule_data.regex_dimensions),
            'total_regex_patterns': sum(len([p for p in patterns if p is not None]) 
                                      for patterns in self.rule_data.regex_dimensions.values()),
            'memory_usage_mb': self._estimate_memory_usage()
        }
    
    def _estimate_memory_usage(self) -> float:
        """Estimate memory usage of numpy arrays in MB."""
        total_bytes = 0
        
        # Rule names
        total_bytes += self.rule_data.rule_names.nbytes
        
        # Exact dimensions
        for arr in self.rule_data.exact_dimensions.values():
            total_bytes += arr.nbytes
        
        # Range dimensions  
        for min_arr, max_arr in self.rule_data.range_dimensions.values():
            total_bytes += min_arr.nbytes + max_arr.nbytes
        
        # Regex patterns (estimated)
        pattern_count = sum(len(patterns) for patterns in self.rule_data.regex_dimensions.values())
        total_bytes += pattern_count * 100  # Rough estimate per pattern
        
        return total_bytes / (1024 * 1024)  # Convert to MB


# Import pandas for compatibility
try:
    import pandas as pd
except ImportError:
    # Create minimal pandas compatibility for numpy operations
    class _PandasCompat:
        @staticmethod
        def isna(value):
            return value is None or (hasattr(value, '__len__') and len(str(value).strip()) == 0)
    
    pd = _PandasCompat()