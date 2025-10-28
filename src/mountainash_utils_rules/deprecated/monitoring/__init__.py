"""
Monitoring infrastructure for the Enhanced VectorizedRulesEngine.

This module provides performance monitoring and memory management
capabilities with minimal overhead when disabled.
"""

from .performance import PerformanceMonitor
from .memory import MemoryManager

__all__ = [
    'PerformanceMonitor',
    'MemoryManager',
]