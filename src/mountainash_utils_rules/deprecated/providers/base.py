"""
Abstract base class for rule evaluation providers.

This module defines the interface that all rule evaluation providers must implement
to support different backend evaluation strategies in the VectorizedRulesEngine.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from mountainash_dataframes import BaseDataFrame
from mountainash_utils_rules.dimension import Dimension


class RuleEvaluationProvider(ABC):
    """
    Abstract base class for rule evaluation backends.
    
    This interface defines the contract that all providers must implement
    to support rule evaluation with prime-based ternary logic (2, 3, 5).
    
    The provider pattern enables:
    - Backend flexibility (Polars, Ibis+DuckDB, Ibis+SQLite, etc.)
    - Consistent ternary logic across all backends
    - Clean separation of evaluation logic from engine logic
    - Easy extension for custom backends
    """
    
    @abstractmethod
    def get_filter_visitor(self):
        """
        Get the appropriate filter visitor for this provider.
        
        Returns:
            RuleTrinaryFilterVisitor configured for this provider's backend
        """
        pass
    
    @abstractmethod
    def materialize_rules(self, rules: BaseDataFrame) -> Any:
        """
        Convert BaseDataFrame to backend-specific format.
        
        This method handles the conversion from the generic BaseDataFrame
        to the specific data structure required by the backend (e.g., 
        pl.DataFrame for Polars, ibis.Table for Ibis).
        
        Args:
            rules: BaseDataFrame containing rules to evaluate
            
        Returns:
            Backend-specific data structure (e.g., pl.DataFrame, ibis.Table)
            
        Raises:
            ValueError: If conversion fails
        """
        pass
    
    @abstractmethod
    def execute_evaluation(self, 
                          rules_data: Any,
                          context_values: Dict[str, Any],
                          dimensions: List[Dimension]) -> Any:
        """
        Execute rule evaluation with the backend.
        
        This method performs the actual rule evaluation using the backend's
        capabilities, applying prime-based ternary logic to determine matches.
        
        Ternary Logic:
        - PRIME_TRUE (2): Condition matches
        - PRIME_FALSE (3): Condition doesn't match
        - PRIME_UNKNOWN (5): Condition unknown/unset
        
        Args:
            rules_data: Backend-specific data structure from materialize_rules
            context_values: Dictionary of dimension names to context values
            dimensions: List of Dimension objects defining match strategies
            
        Returns:
            Backend-specific result with all columns plus 'keep' flag
        """
        pass
    
    @abstractmethod
    def to_base_dataframe(self, result: Any) -> BaseDataFrame:
        """
        Convert result back to BaseDataFrame.
        
        This method handles the conversion from the backend-specific result
        back to a BaseDataFrame for compatibility with the rest of the system.
        
        Args:
            result: Backend-specific result from execute_evaluation
            
        Returns:
            BaseDataFrame compatible with mountainash-dataframes
        """
        pass
    
    @property
    @abstractmethod
    def backend_name(self) -> str:
        """
        Name of the backend for logging and monitoring.
        
        Returns:
            String identifier for this backend (e.g., "polars", "ibis_duckdb")
        """
        pass
    
    @property
    @abstractmethod
    def supports_lazy_evaluation(self) -> bool:
        """
        Whether this provider supports lazy evaluation.
        
        Lazy evaluation can significantly improve performance by optimizing
        the query plan before execution.
        
        Returns:
            True if the backend supports lazy evaluation, False otherwise
        """
        pass
    
    @property
    def supports_parallel_processing(self) -> bool:
        """
        Whether this provider supports parallel processing.
        
        Default implementation returns False. Override in providers that
        support parallel execution.
        
        Returns:
            True if the backend supports parallel processing, False otherwise
        """
        return False
    
    @property
    def supports_expression_caching(self) -> bool:
        """
        Whether this provider benefits from expression caching.
        
        Default implementation returns True. Override if caching doesn't
        provide benefits for the specific backend.
        
        Returns:
            True if expression caching is beneficial, False otherwise
        """
        return True
    
    def clear_caches(self) -> None:
        """
        Clear any internal caches maintained by the provider.
        
        Default implementation does nothing. Override in providers that
        maintain internal caches.
        """
        pass
    
    def get_performance_hints(self) -> Dict[str, Any]:
        """
        Get performance hints specific to this provider.
        
        Returns a dictionary of performance-related hints that can be used
        to optimize engine configuration for this specific backend.
        
        Returns:
            Dictionary of performance hints
        """
        return {
            'supports_lazy': self.supports_lazy_evaluation,
            'supports_parallel': self.supports_parallel_processing,
            'benefits_from_caching': self.supports_expression_caching,
            'backend': self.backend_name
        }