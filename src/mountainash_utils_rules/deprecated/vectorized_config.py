"""
Configuration system for the Enhanced VectorizedRulesEngine.

This module provides a comprehensive configuration dataclass that controls
all aspects of the enhanced engine's behavior, from provider selection to
performance optimization settings.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class VectorizedEngineConfig:
    """
    Enhanced configuration for the vectorized rules engine.
    
    This configuration class provides fine-grained control over all aspects
    of the engine's behavior while maintaining sensible defaults for common
    use cases.
    
    Configuration Categories:
    1. Provider Settings - Backend selection and configuration
    2. Performance Optimization - Query optimization and parallelization
    3. Memory Management - Cache and memory cleanup settings
    4. Monitoring - Performance tracking and metrics
    5. Compatibility - API compatibility options
    
    Examples:
        >>> # Default configuration (high performance Polars)
        >>> config = VectorizedEngineConfig()
        
        >>> # Production configuration with monitoring
        >>> config = VectorizedEngineConfig(
        ...     provider="polars",
        ...     enable_monitoring=True,
        ...     enable_cleanup=True,
        ...     cleanup_interval=5000
        ... )
        
        >>> # Cross-backend configuration (future)
        >>> config = VectorizedEngineConfig(
        ...     provider="ibis_duckdb",
        ...     enable_memory_pooling=False
        ... )
    """
    
    # ========================================================================
    # Provider Settings
    # ========================================================================
    
    provider: str = "polars"
    """Backend provider to use. Options: 'polars', 'ibis_polars', 'ibis_duckdb', 'ibis_sqlite'"""
    
    provider_config: Dict[str, Any] = field(default_factory=dict)
    """Additional configuration passed to the provider constructor"""
    
    # ========================================================================
    # Performance Optimization (from current VectorizedRulesEngine)
    # ========================================================================
    
    enable_query_optimization: bool = True
    """Enable query plan optimization for better performance"""
    
    enable_parallel_processing: bool = True
    """Enable parallel processing for independent dimensions"""
    
    max_worker_threads: int = 4
    """Maximum number of worker threads for parallel processing"""
    
    enable_selectivity_analysis: bool = True
    """Enable rule selectivity analysis for optimization"""
    
    enable_early_termination: bool = True
    """Enable early termination when selectivity indicates low match probability"""
    
    selectivity_sample_size: int = 100
    """Sample size for selectivity analysis"""
    
    parallel_dimension_threshold: int = 3
    """Minimum number of dimensions required to enable parallel processing"""
    
    enable_simd_optimization: bool = True
    """Enable SIMD optimization where supported"""
    
    # ========================================================================
    # Memory Management
    # ========================================================================
    
    enable_memory_pooling: bool = True
    """Enable memory pooling for better memory utilization"""
    
    chunk_size_mb: int = 100
    """Chunk size in MB for processing large datasets"""
    
    cleanup_interval: int = 10000
    """Number of evaluations between automatic cache cleanup"""
    
    enable_cleanup: bool = True
    """Enable automatic memory cleanup for long-running processes"""
    
    max_memory_mb: Optional[int] = None
    """Maximum memory usage in MB (None for unlimited)"""
    
    # ========================================================================
    # Expression Caching
    # ========================================================================
    
    cache_expressions: bool = True
    """Enable caching of compiled expressions"""
    
    max_cache_size: int = 1000
    """Maximum number of cached expressions"""
    
    max_cached_patterns: int = 1000
    """Maximum number of cached regex patterns"""
    
    cache_ttl_seconds: Optional[int] = None
    """Time-to-live for cached items in seconds (None for no expiry)"""
    
    # ========================================================================
    # Monitoring and Metrics
    # ========================================================================
    
    enable_monitoring: bool = False
    """Enable performance monitoring (adds minimal overhead)"""
    
    detailed_timing: bool = False
    """Enable detailed timing breakdown for each phase"""
    
    metrics_window_size: int = 100
    """Size of sliding window for recent performance metrics"""
    
    log_performance: bool = False
    """Log performance metrics to logger"""
    
    # ========================================================================
    # Compatibility Options
    # ========================================================================
    
    strict_compatibility: bool = False
    """Enforce strict API compatibility with original RulesEngine"""
    
    maintain_column_order: bool = True
    """Maintain original column order in results"""
    
    include_intermediate_columns: bool = False
    """Include intermediate evaluation columns in results"""
    
    # ========================================================================
    # Advanced Options
    # ========================================================================
    
    enable_expression_caching: bool = True
    """Enable caching at the expression builder level"""
    
    enable_result_validation: bool = False
    """Enable validation of results (useful for debugging)"""
    
    fallback_on_error: bool = False
    """Fall back to a simpler evaluation strategy on error"""
    
    profile_execution: bool = False
    """Enable execution profiling for performance analysis"""
    
    # ========================================================================
    # Factory Methods for Common Configurations
    # ========================================================================
    
    @classmethod
    def high_performance(cls) -> 'VectorizedEngineConfig':
        """
        Create a configuration optimized for maximum performance.
        
        Returns:
            Configuration with all performance optimizations enabled
        """
        return cls(
            provider="polars",
            enable_query_optimization=True,
            enable_parallel_processing=True,
            max_worker_threads=8,
            enable_selectivity_analysis=True,
            enable_early_termination=True,
            enable_simd_optimization=True,
            cache_expressions=True,
            max_cache_size=2000,
            enable_monitoring=False,  # Disable for max performance
            enable_cleanup=False      # Disable for max performance
        )
    
    @classmethod
    def production(cls) -> 'VectorizedEngineConfig':
        """
        Create a configuration suitable for production use.
        
        Balances performance with monitoring and stability.
        
        Returns:
            Configuration with production-ready settings
        """
        return cls(
            provider="polars",
            enable_query_optimization=True,
            enable_parallel_processing=True,
            max_worker_threads=4,
            enable_monitoring=True,
            enable_cleanup=True,
            cleanup_interval=5000,
            cache_expressions=True,
            log_performance=True,
            fallback_on_error=True
        )
    
    @classmethod
    def memory_constrained(cls) -> 'VectorizedEngineConfig':
        """
        Create a configuration for memory-constrained environments.
        
        Returns:
            Configuration optimized for low memory usage
        """
        return cls(
            provider="polars",
            enable_memory_pooling=False,
            chunk_size_mb=50,
            enable_cleanup=True,
            cleanup_interval=1000,
            max_cache_size=500,
            max_cached_patterns=500,
            cache_ttl_seconds=300,  # 5 minute TTL
            max_memory_mb=512
        )
    
    @classmethod
    def debugging(cls) -> 'VectorizedEngineConfig':
        """
        Create a configuration for debugging and development.
        
        Returns:
            Configuration with extensive logging and validation
        """
        return cls(
            provider="polars",
            enable_monitoring=True,
            detailed_timing=True,
            log_performance=True,
            enable_result_validation=True,
            include_intermediate_columns=True,
            profile_execution=True,
            fallback_on_error=False  # Don't hide errors
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert configuration to dictionary.
        
        Returns:
            Dictionary representation of configuration
        """
        return {
            # Provider
            'provider': self.provider,
            'provider_config': self.provider_config,
            
            # Performance
            'enable_query_optimization': self.enable_query_optimization,
            'enable_parallel_processing': self.enable_parallel_processing,
            'max_worker_threads': self.max_worker_threads,
            'enable_selectivity_analysis': self.enable_selectivity_analysis,
            'enable_early_termination': self.enable_early_termination,
            'selectivity_sample_size': self.selectivity_sample_size,
            'parallel_dimension_threshold': self.parallel_dimension_threshold,
            'enable_simd_optimization': self.enable_simd_optimization,
            
            # Memory
            'enable_memory_pooling': self.enable_memory_pooling,
            'chunk_size_mb': self.chunk_size_mb,
            'cleanup_interval': self.cleanup_interval,
            'enable_cleanup': self.enable_cleanup,
            'max_memory_mb': self.max_memory_mb,
            
            # Caching
            'cache_expressions': self.cache_expressions,
            'max_cache_size': self.max_cache_size,
            'max_cached_patterns': self.max_cached_patterns,
            'cache_ttl_seconds': self.cache_ttl_seconds,
            
            # Monitoring
            'enable_monitoring': self.enable_monitoring,
            'detailed_timing': self.detailed_timing,
            'metrics_window_size': self.metrics_window_size,
            'log_performance': self.log_performance,
            
            # Compatibility
            'strict_compatibility': self.strict_compatibility,
            'maintain_column_order': self.maintain_column_order,
            'include_intermediate_columns': self.include_intermediate_columns,
            
            # Advanced
            'enable_expression_caching': self.enable_expression_caching,
            'enable_result_validation': self.enable_result_validation,
            'fallback_on_error': self.fallback_on_error,
            'profile_execution': self.profile_execution
        }
    
    def validate(self) -> None:
        """
        Validate configuration settings.
        
        Raises:
            ValueError: If configuration is invalid
        """
        if self.max_worker_threads < 1:
            raise ValueError("max_worker_threads must be at least 1")
        
        if self.chunk_size_mb < 1:
            raise ValueError("chunk_size_mb must be at least 1")
        
        if self.cleanup_interval < 1:
            raise ValueError("cleanup_interval must be at least 1")
        
        if self.max_cache_size < 0:
            raise ValueError("max_cache_size cannot be negative")
        
        if self.max_memory_mb is not None and self.max_memory_mb < 1:
            raise ValueError("max_memory_mb must be at least 1 if specified")
        
        if self.cache_ttl_seconds is not None and self.cache_ttl_seconds < 1:
            raise ValueError("cache_ttl_seconds must be at least 1 if specified")