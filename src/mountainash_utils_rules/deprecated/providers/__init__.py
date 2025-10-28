"""
Provider infrastructure for the Enhanced VectorizedRulesEngine.

This module provides the provider pattern implementation for supporting
multiple backend evaluation strategies while maintaining consistent
prime-based ternary logic.
"""

from .base import RuleEvaluationProvider
from .polars_provider import PolarsProvider
from .factory import ProviderFactory

__all__ = [
    'RuleEvaluationProvider',
    'PolarsProvider',
    'ProviderFactory',
]