"""
Factory for creating rule evaluation providers.

This module implements the factory pattern for provider instantiation,
allowing easy creation and registration of different backend providers.
"""

import logging
from typing import Dict, Callable, List, Any, Optional
from .base import RuleEvaluationProvider
from .polars_provider import PolarsProvider


logger = logging.getLogger(__name__)


class ProviderFactory:
    """
    Factory for creating rule evaluation providers.
    
    This factory maintains a registry of available providers and provides
    methods for creating instances and registering custom providers.
    
    Built-in providers:
    - 'polars': High-performance Polars provider
    - 'ibis_polars': Ibis with Polars backend (future)
    - 'ibis_duckdb': Ibis with DuckDB backend (future)
    - 'ibis_sqlite': Ibis with SQLite backend (future)
    """
    
    # Static registry of provider factories
    _providers: Dict[str, Callable[..., RuleEvaluationProvider]] = {}
    
    @classmethod
    def _initialize_providers(cls) -> None:
        """Initialize the default provider registry."""
        if not cls._providers:
            # Register built-in providers
            cls._providers['polars'] = lambda **kwargs: PolarsProvider(**kwargs)
            
            # Placeholder for future Ibis providers
            # These will be implemented in Phase 3
            def _ibis_not_implemented(**kwargs):
                raise NotImplementedError(
                    "Ibis providers are not yet implemented. "
                    "Please use 'polars' provider for now."
                )
            
            cls._providers['ibis_polars'] = _ibis_not_implemented
            cls._providers['ibis_duckdb'] = _ibis_not_implemented
            cls._providers['ibis_sqlite'] = _ibis_not_implemented
            
            logger.debug(f"Initialized provider registry with {len(cls._providers)} providers")
    
    @classmethod
    def create_provider(cls, 
                       provider_type: str, 
                       **kwargs) -> RuleEvaluationProvider:
        """
        Create a provider instance.
        
        Args:
            provider_type: Type of provider to create (e.g., 'polars', 'ibis_duckdb')
            **kwargs: Additional arguments passed to the provider constructor
            
        Returns:
            Configured provider instance
            
        Raises:
            ValueError: If provider_type is not registered
            
        Examples:
            >>> # Create a Polars provider with caching
            >>> provider = ProviderFactory.create_provider('polars', enable_caching=True)
            
            >>> # Create a provider with custom configuration
            >>> provider = ProviderFactory.create_provider(
            ...     'polars',
            ...     enable_caching=False,
            ...     enable_optimization=True
            ... )
        """
        cls._initialize_providers()
        
        if provider_type not in cls._providers:
            available = ', '.join(cls.available_providers())
            raise ValueError(
                f"Unknown provider type: '{provider_type}'. "
                f"Available providers: {available}"
            )
        
        logger.info(f"Creating provider: {provider_type} with kwargs: {kwargs}")
        
        try:
            provider_factory = cls._providers[provider_type]
            provider = provider_factory(**kwargs)
            
            logger.info(f"Successfully created {provider_type} provider")
            return provider
            
        except Exception as e:
            logger.error(f"Failed to create provider {provider_type}: {e}")
            raise
    
    @classmethod
    def register_provider(cls, 
                         name: str, 
                         provider_factory: Callable[..., RuleEvaluationProvider],
                         replace: bool = False) -> None:
        """
        Register a custom provider.
        
        This method allows registration of custom providers for specialized
        use cases or experimental backends.
        
        Args:
            name: Name for the provider
            provider_factory: Factory function that creates provider instances
            replace: Whether to replace an existing provider with the same name
            
        Raises:
            ValueError: If name already exists and replace is False
            
        Examples:
            >>> # Register a custom provider
            >>> class CustomProvider(RuleEvaluationProvider):
            ...     # Implementation...
            ...     pass
            >>> 
            >>> ProviderFactory.register_provider(
            ...     'custom',
            ...     lambda **kwargs: CustomProvider(**kwargs)
            ... )
        """
        cls._initialize_providers()
        
        if name in cls._providers and not replace:
            raise ValueError(
                f"Provider '{name}' already registered. "
                f"Use replace=True to override."
            )
        
        cls._providers[name] = provider_factory
        logger.info(f"Registered provider: {name} (replace={replace})")
    
    @classmethod
    def unregister_provider(cls, name: str) -> None:
        """
        Unregister a provider.
        
        Args:
            name: Name of the provider to unregister
            
        Raises:
            KeyError: If provider doesn't exist
        """
        cls._initialize_providers()
        
        if name not in cls._providers:
            raise KeyError(f"Provider '{name}' not found in registry")
        
        del cls._providers[name]
        logger.info(f"Unregistered provider: {name}")
    
    @classmethod
    def available_providers(cls) -> List[str]:
        """
        Get list of available provider names.
        
        Returns:
            List of registered provider names
            
        Examples:
            >>> providers = ProviderFactory.available_providers()
            >>> print(providers)
            ['polars', 'ibis_polars', 'ibis_duckdb', 'ibis_sqlite']
        """
        cls._initialize_providers()
        return list(cls._providers.keys())
    
    @classmethod
    def get_provider_info(cls, provider_type: str) -> Dict[str, Any]:
        """
        Get information about a provider.
        
        Args:
            provider_type: Name of the provider
            
        Returns:
            Dictionary with provider information
            
        Raises:
            ValueError: If provider_type is not registered
            
        Examples:
            >>> info = ProviderFactory.get_provider_info('polars')
            >>> print(info)
            {
                'name': 'polars',
                'backend_name': 'polars',
                'supports_lazy_evaluation': True,
                'supports_parallel_processing': True,
                'type': 'PolarsProvider'
            }
        """
        cls._initialize_providers()
        
        if provider_type not in cls._providers:
            raise ValueError(f"Unknown provider: {provider_type}")
        
        try:
            # Create a temporary instance to get info
            provider = cls.create_provider(provider_type)
            
            info = {
                'name': provider_type,
                'backend_name': provider.backend_name,
                'supports_lazy_evaluation': provider.supports_lazy_evaluation,
                'supports_parallel_processing': provider.supports_parallel_processing,
                'supports_expression_caching': provider.supports_expression_caching,
                'type': type(provider).__name__,
                'performance_hints': provider.get_performance_hints()
            }
            
            return info
            
        except NotImplementedError:
            # Handle not-yet-implemented providers
            return {
                'name': provider_type,
                'status': 'not_implemented',
                'message': f"Provider '{provider_type}' is planned but not yet implemented"
            }
    
    @classmethod
    def reset_registry(cls) -> None:
        """
        Reset the provider registry to empty state.
        
        This is mainly useful for testing purposes.
        """
        cls._providers.clear()
        logger.debug("Provider registry reset")