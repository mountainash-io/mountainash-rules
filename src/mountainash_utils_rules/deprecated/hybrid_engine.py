"""
Hybrid Rules Engine - Seamless integration of numpy and ibis processing.

This module provides a drop-in replacement for the standard RulesEngine that automatically
selects between high-performance numpy vectorized processing and reliable ibis processing
based on configuration, data characteristics, and runtime conditions.

Key features:
- Automatic fallback from numpy to ibis on errors
- Configuration-driven optimization level selection  
- Seamless API compatibility with existing RulesEngine
- Performance monitoring and statistics collection
- Context value optimization for numpy operations
"""

import logging
import time
from typing import List, Optional, Dict, Any, Union
from enum import Enum
from dataclasses import dataclass

import ibis
from pydantic import BaseModel

from mountainash_dataframes import BaseDataFrame

from mountainash_utils_rules.constants import RuleTrinaryFlags
from mountainash_utils_rules.dimension import DimensionsMetadata, Dimension
from mountainash_utils_rules.engine import RulesEngine
from mountainash_utils_rules.numpy_processor import NumpyRuleProcessor
from mountainash_utils_rules.context import ContextHelper


logger = logging.getLogger(__name__)


class ProcessingMode(Enum):
    """Processing mode configuration for hybrid engine."""
    AUTO = "auto"              # Automatic selection based on data characteristics
    NUMPY_PREFERRED = "numpy"  # Prefer numpy with ibis fallback
    IBIS_ONLY = "ibis"        # Use only ibis processing
    NUMPY_ONLY = "numpy_only" # Use only numpy (no fallback)


@dataclass
class HybridEngineConfig:
    """Configuration for hybrid engine behavior."""
    
    # Processing mode selection
    processing_mode: ProcessingMode = ProcessingMode.AUTO
    
    # Performance thresholds for auto mode
    min_rules_for_numpy: int = 100        # Minimum rules to use numpy
    max_regex_ratio: float = 0.3          # Max regex dimension ratio for numpy
    
    # Fallback configuration
    enable_fallback: bool = True          # Enable automatic fallback
    max_fallback_attempts: int = 2        # Maximum fallback attempts
    
    # Performance monitoring
    enable_performance_logging: bool = False  # Log performance metrics
    performance_comparison: bool = False  # Compare numpy vs ibis performance


@dataclass
class ProcessingStats:
    """Statistics for processing performance tracking."""
    
    # Execution metrics
    numpy_attempts: int = 0
    numpy_successes: int = 0
    ibis_executions: int = 0
    
    # Performance metrics
    total_numpy_time: float = 0.0
    total_ibis_time: float = 0.0
    average_numpy_time: float = 0.0
    average_ibis_time: float = 0.0
    
    # Error tracking
    numpy_errors: int = 0
    fallback_triggers: int = 0


class HybridRulesEngine:
    """
    High-performance hybrid rules engine combining numpy vectorization with ibis reliability.
    
    This engine provides a drop-in replacement for the standard RulesEngine with automatic
    optimization selection based on data characteristics and runtime conditions.
    
    Architecture:
    - Primary: NumpyRuleProcessor for high-performance vectorized evaluation
    - Fallback: Standard RulesEngine for reliability and compatibility
    - Smart selection: Automatic mode switching based on data characteristics
    """
    
    def __init__(self,
                 rules: BaseDataFrame,
                 dimension_metadata: Optional[DimensionsMetadata] = None,
                 config: Optional[HybridEngineConfig] = None):
        """
        Initialize hybrid rules engine with automatic optimization selection.
        
        Args:
            rules: BaseDataFrame containing rule definitions
            dimension_metadata: Optional dimension metadata for validation
            config: Hybrid engine configuration options
        """
        self.config = config or HybridEngineConfig()
        self.stats = ProcessingStats()
        
        # Initialize base ibis engine (always available as fallback)
        self.ibis_engine = RulesEngine(rules=rules, dimension_metadata=dimension_metadata)
        
        # Initialize numpy processor (if conditions are met)
        self.numpy_processor: Optional[NumpyRuleProcessor] = None
        self._initialize_numpy_processor(rules, dimension_metadata)
        
        # Determine optimal processing mode
        self.active_processing_mode = self._determine_processing_mode(rules, dimension_metadata)
        
        if self.config.enable_performance_logging:
            logger.info(f"HybridRulesEngine initialized with mode: {self.active_processing_mode}")
    
    def _initialize_numpy_processor(self, 
                                   rules: BaseDataFrame, 
                                   dimension_metadata: Optional[DimensionsMetadata]):
        """Initialize numpy processor if conditions are suitable."""
        try:
            if dimension_metadata and dimension_metadata.dimensions:
                self.numpy_processor = NumpyRuleProcessor(rules, dimension_metadata.dimensions)
                logger.debug("NumpyRuleProcessor initialized successfully")
            else:
                logger.warning("Cannot initialize NumpyRuleProcessor: missing dimension metadata")
        except Exception as e:
            logger.warning(f"Failed to initialize NumpyRuleProcessor: {e}")
            self.numpy_processor = None
    
    def _determine_processing_mode(self, 
                                  rules: BaseDataFrame,
                                  dimension_metadata: Optional[DimensionsMetadata]) -> ProcessingMode:
        """
        Determine optimal processing mode based on data characteristics.
        
        Auto mode selection criteria:
        - Rule count: Numpy beneficial for 100+ rules
        - Regex ratio: Ibis preferred when >30% regex dimensions
        - Data complexity: Numpy optimal for exact/range matching
        """
        if self.config.processing_mode != ProcessingMode.AUTO:
            return self.config.processing_mode
        
        # Force ibis if numpy processor unavailable
        if self.numpy_processor is None:
            return ProcessingMode.IBIS_ONLY
        
        try:
            # Analyze data characteristics
            rule_count = self.numpy_processor.rule_data.rule_count
            
            if dimension_metadata and dimension_metadata.dimensions:
                total_dimensions = len(dimension_metadata.dimensions)
                regex_dimensions = sum(1 for d in dimension_metadata.dimensions 
                                     if d.match_strategy.name == 'REGEX')
                regex_ratio = regex_dimensions / total_dimensions if total_dimensions > 0 else 0
            else:
                regex_ratio = 0
            
            # Apply selection criteria
            if rule_count < self.config.min_rules_for_numpy:
                logger.debug(f"Using ibis: rule count {rule_count} < {self.config.min_rules_for_numpy}")
                return ProcessingMode.IBIS_ONLY
            
            if regex_ratio > self.config.max_regex_ratio:
                logger.debug(f"Using ibis: regex ratio {regex_ratio:.2f} > {self.config.max_regex_ratio}")
                return ProcessingMode.IBIS_ONLY
            
            logger.debug(f"Using numpy: rule count={rule_count}, regex ratio={regex_ratio:.2f}")
            return ProcessingMode.NUMPY_PREFERRED
            
        except Exception as e:
            logger.warning(f"Error determining processing mode, defaulting to ibis: {e}")
            return ProcessingMode.IBIS_ONLY
    
    def apply_context_rules_engine(self, 
                                  context: BaseModel,
                                  active_dimensions: List[str]) -> BaseDataFrame:
        """
        Apply rules engine to context with automatic optimization selection.
        
        This method provides the same interface as the standard RulesEngine while
        automatically selecting the optimal processing approach.
        
        Args:
            context: Context model containing dimension values
            active_dimensions: List of dimension names to evaluate
            
        Returns:
            BaseDataFrame with rule evaluation results and keep flags
        """
        start_time = time.time()
        
        try:
            if self.active_processing_mode in [ProcessingMode.NUMPY_PREFERRED, ProcessingMode.NUMPY_ONLY]:
                result = self._apply_numpy_processing(context, active_dimensions)
                
                # Record successful numpy execution
                execution_time = time.time() - start_time
                self.stats.numpy_attempts += 1
                self.stats.numpy_successes += 1
                self.stats.total_numpy_time += execution_time
                self.stats.average_numpy_time = self.stats.total_numpy_time / self.stats.numpy_successes
                
                if self.config.enable_performance_logging:
                    logger.info(f"Numpy processing completed in {execution_time:.3f}s")
                
                return result
                
        except Exception as e:
            self.stats.numpy_errors += 1
            logger.warning(f"Numpy processing failed: {e}")
            
            # Handle fallback logic
            if (self.config.enable_fallback and 
                self.active_processing_mode != ProcessingMode.NUMPY_ONLY and
                self.stats.fallback_triggers < self.config.max_fallback_attempts):
                
                self.stats.fallback_triggers += 1
                logger.info("Falling back to ibis processing")
                
                # Reset timer for ibis execution
                start_time = time.time()
            else:
                # No fallback available or max attempts reached
                raise
        
        # Execute using ibis engine (either by design or fallback)
        result = self.ibis_engine.apply_context_rules_engine(context, active_dimensions)
        
        # Record ibis execution stats
        execution_time = time.time() - start_time
        self.stats.ibis_executions += 1
        self.stats.total_ibis_time += execution_time
        if self.stats.ibis_executions > 0:
            self.stats.average_ibis_time = self.stats.total_ibis_time / self.stats.ibis_executions
        
        if self.config.enable_performance_logging:
            logger.info(f"Ibis processing completed in {execution_time:.3f}s")
        
        return result
    
    def _apply_numpy_processing(self, 
                               context: BaseModel, 
                               active_dimensions: List[str]) -> BaseDataFrame:
        """
        Apply numpy-based vectorized processing with optimized context extraction.
        
        This method leverages the numpy processor for high-performance evaluation
        and converts results back to the expected BaseDataFrame format.
        """
        if self.numpy_processor is None:
            raise ValueError("Numpy processor not available")
        
        # Extract context values using optimized batch processing
        dimensions = [d for d in self.ibis_engine.metadata_manager.raw_dimension_metadata.dimensions
                     if d.dimension_name in active_dimensions]
        context_values = ContextHelper.get_all_context_values(context=context, dimensions=dimensions)
        
        # Perform vectorized evaluation
        flags = self.numpy_processor.evaluate_context_vectorized(context_values, active_dimensions)
        
        # Convert numpy results back to ibis-compatible format
        return self._convert_numpy_results_to_dataframe(flags)
    
    def _convert_numpy_results_to_dataframe(self, flags: 'np.ndarray') -> BaseDataFrame:
        """
        Convert numpy evaluation results back to BaseDataFrame with proper keep flags.
        
        This method bridges the numpy processor output with the expected ibis
        BaseDataFrame format, ensuring seamless API compatibility.
        """
        import numpy as np
        
        # Get base rules dataframe structure
        base_rules = self.ibis_engine.rule_manager.rules
        
        # Create keep column based on prime flags
        keep_flags = flags == RuleTrinaryFlags.PRIME_TRUE
        
        # Add evaluation results to rules dataframe
        result_df = base_rules.mutate(
            keep=ibis.array([bool(flag) for flag in keep_flags])
        )
        
        return result_df
    
    def get_processing_stats(self) -> ProcessingStats:
        """Get comprehensive processing statistics."""
        return self.stats
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary with key metrics."""
        stats = self.stats
        
        # Calculate performance ratios
        total_executions = stats.numpy_successes + stats.ibis_executions
        numpy_success_rate = (stats.numpy_successes / stats.numpy_attempts 
                             if stats.numpy_attempts > 0 else 0)
        
        performance_improvement = 0.0
        if stats.average_ibis_time > 0 and stats.average_numpy_time > 0:
            performance_improvement = (
                (stats.average_ibis_time - stats.average_numpy_time) / stats.average_ibis_time
            ) * 100
        
        return {
            'processing_mode': self.active_processing_mode.value,
            'total_executions': total_executions,
            'numpy_executions': stats.numpy_successes,
            'ibis_executions': stats.ibis_executions,
            'numpy_success_rate': f"{numpy_success_rate:.1%}",
            'fallback_rate': f"{stats.fallback_triggers / total_executions:.1%}" if total_executions > 0 else "0%",
            'average_numpy_time_ms': f"{stats.average_numpy_time * 1000:.2f}",
            'average_ibis_time_ms': f"{stats.average_ibis_time * 1000:.2f}",
            'performance_improvement': f"{performance_improvement:.1f}%",
            'numpy_processor_available': self.numpy_processor is not None
        }
    
    def reset_stats(self):
        """Reset all processing statistics."""
        self.stats = ProcessingStats()
    
    def update_config(self, new_config: HybridEngineConfig):
        """Update configuration and re-evaluate processing mode."""
        self.config = new_config
        
        # Re-determine processing mode with new config
        rules = self.ibis_engine.rule_manager.rules
        dimension_metadata = self.ibis_engine.metadata_manager.raw_dimension_metadata
        self.active_processing_mode = self._determine_processing_mode(rules, dimension_metadata)
        
        if self.config.enable_performance_logging:
            logger.info(f"Configuration updated, new processing mode: {self.active_processing_mode}")
    
    # Delegate other methods to ibis engine for full compatibility
    def initialize_rule_flags(self, rules: BaseDataFrame) -> BaseDataFrame:
        """Delegate to ibis engine for rule flag initialization."""
        return self.ibis_engine.initialize_rule_flags(rules)
    
    def apply_dimension_filter_flags(self, 
                                    rules: BaseDataFrame, 
                                    dimension: Dimension, 
                                    context_value: Union[str, int, float]) -> BaseDataFrame:
        """Delegate to ibis engine for dimension filtering."""
        return self.ibis_engine.apply_dimension_filter_flags(rules, dimension, context_value)


# Convenience functions for common configuration patterns

def create_performance_optimized_engine(rules: BaseDataFrame, 
                                       dimension_metadata: Optional[DimensionsMetadata] = None) -> HybridRulesEngine:
    """Create a hybrid engine optimized for maximum performance."""
    config = HybridEngineConfig(
        processing_mode=ProcessingMode.NUMPY_PREFERRED,
        min_rules_for_numpy=50,  # Lower threshold for numpy usage
        max_regex_ratio=0.5,     # Higher regex tolerance
        enable_performance_logging=True
    )
    return HybridRulesEngine(rules, dimension_metadata, config)


def create_reliability_focused_engine(rules: BaseDataFrame,
                                     dimension_metadata: Optional[DimensionsMetadata] = None) -> HybridRulesEngine:
    """Create a hybrid engine prioritizing reliability with conservative fallback."""
    config = HybridEngineConfig(
        processing_mode=ProcessingMode.AUTO,
        min_rules_for_numpy=500,  # Higher threshold for numpy usage
        max_regex_ratio=0.1,      # Conservative regex handling
        enable_fallback=True,
        max_fallback_attempts=3
    )
    return HybridRulesEngine(rules, dimension_metadata, config)


def create_development_engine(rules: BaseDataFrame,
                            dimension_metadata: Optional[DimensionsMetadata] = None) -> HybridRulesEngine:
    """Create a hybrid engine with comprehensive logging for development."""
    config = HybridEngineConfig(
        processing_mode=ProcessingMode.AUTO,
        enable_performance_logging=True,
        performance_comparison=True,
        enable_fallback=True
    )
    return HybridRulesEngine(rules, dimension_metadata, config)