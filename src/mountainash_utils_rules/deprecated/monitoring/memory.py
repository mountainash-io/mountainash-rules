"""
Memory management for long-running processes.

This module provides memory management capabilities to prevent memory leaks
and optimize memory usage in long-running rule evaluation processes.
"""

import gc
import weakref
import logging
from typing import Set, Any, Optional, Dict
from dataclasses import dataclass
import psutil
import os


logger = logging.getLogger(__name__)


@dataclass
class MemoryStats:
    """Container for memory statistics."""
    
    process_memory_mb: float
    available_memory_mb: float
    memory_percent: float
    gc_collections: Dict[int, int]
    cached_objects_count: int
    evaluation_count: int
    cleanups_performed: int


class MemoryManager:
    """
    Memory management for long-running processes.
    
    This manager provides automatic memory cleanup and monitoring to prevent
    memory leaks in long-running rule evaluation processes.
    
    Features:
    - Periodic cache cleanup
    - Garbage collection management
    - Memory usage monitoring
    - Weak reference tracking for cached objects
    """
    
    def __init__(self, 
                 cleanup_interval: int = 10000,
                 enable_gc: bool = True,
                 max_memory_mb: Optional[int] = None,
                 aggressive_cleanup: bool = False):
        """
        Initialize the memory manager.
        
        Args:
            cleanup_interval: Number of evaluations between cleanups
            enable_gc: Whether to trigger garbage collection
            max_memory_mb: Maximum memory usage in MB (triggers cleanup if exceeded)
            aggressive_cleanup: Whether to use aggressive cleanup strategies
        """
        self.cleanup_interval = cleanup_interval
        self.enable_gc = enable_gc
        self.max_memory_mb = max_memory_mb
        self.aggressive_cleanup = aggressive_cleanup
        
        # Tracking
        self.evaluation_count = 0
        self.cleanups_performed = 0
        self._cached_objects: Set[weakref.ref] = weakref.WeakSet()
        self._cleanup_callbacks = []
        
        # Process handle for memory monitoring
        try:
            self._process = psutil.Process(os.getpid())
        except Exception as e:
            logger.warning(f"Failed to initialize process monitoring: {e}")
            self._process = None
        
        logger.info(
            f"MemoryManager initialized: cleanup_interval={cleanup_interval}, "
            f"max_memory_mb={max_memory_mb}, aggressive={aggressive_cleanup}"
        )
    
    def register_cache(self, cache_object: Any) -> None:
        """
        Register an object with a cache for cleanup tracking.
        
        The object should have a 'clear_cache' or 'clear' method.
        
        Args:
            cache_object: Object with cache to track
        """
        if hasattr(cache_object, 'clear_cache') or hasattr(cache_object, 'clear'):
            self._cached_objects.add(cache_object)
            logger.debug(f"Registered cache object: {type(cache_object).__name__}")
    
    def register_cleanup_callback(self, callback) -> None:
        """
        Register a callback to be called during cleanup.
        
        Args:
            callback: Callable to invoke during cleanup
        """
        self._cleanup_callbacks.append(callback)
        logger.debug(f"Registered cleanup callback: {callback.__name__}")
    
    def check_and_cleanup(self) -> bool:
        """
        Check if cleanup is needed and perform it.
        
        Returns:
            True if cleanup was performed, False otherwise
        """
        self.evaluation_count += 1
        
        # Check if cleanup is needed
        needs_cleanup = False
        
        # Periodic cleanup
        if self.evaluation_count % self.cleanup_interval == 0:
            needs_cleanup = True
            logger.debug(f"Periodic cleanup triggered at evaluation {self.evaluation_count}")
        
        # Memory threshold cleanup
        if self.max_memory_mb and self._check_memory_threshold():
            needs_cleanup = True
            logger.warning(f"Memory threshold cleanup triggered")
        
        if needs_cleanup:
            self.perform_cleanup()
            return True
        
        return False
    
    def perform_cleanup(self) -> None:
        """
        Perform memory cleanup.
        
        This includes:
        - Clearing registered caches
        - Running cleanup callbacks
        - Triggering garbage collection
        """
        logger.info(f"Performing memory cleanup (evaluation {self.evaluation_count})")
        
        # Clear registered caches
        cleared_count = 0
        for obj_ref in list(self._cached_objects):
            try:
                obj = obj_ref() if isinstance(obj_ref, weakref.ref) else obj_ref
                if obj is not None:
                    if hasattr(obj, 'clear_cache'):
                        obj.clear_cache()
                        cleared_count += 1
                    elif hasattr(obj, 'clear'):
                        obj.clear()
                        cleared_count += 1
            except Exception as e:
                logger.warning(f"Failed to clear cache: {e}")
        
        logger.debug(f"Cleared {cleared_count} caches")
        
        # Run cleanup callbacks
        for callback in self._cleanup_callbacks:
            try:
                callback()
            except Exception as e:
                logger.warning(f"Cleanup callback failed: {e}")
        
        # Garbage collection
        if self.enable_gc:
            if self.aggressive_cleanup:
                # Aggressive: collect all generations
                gc.collect(2)
            else:
                # Normal: collect youngest generation
                gc.collect(0)
            
            logger.debug(f"Garbage collection completed")
        
        self.cleanups_performed += 1
        
        # Log memory stats after cleanup
        if logger.isEnabledFor(logging.DEBUG):
            stats = self.get_memory_stats()
            logger.debug(
                f"Memory after cleanup: {stats.process_memory_mb:.1f}MB "
                f"({stats.memory_percent:.1f}% of system)"
            )
    
    def _check_memory_threshold(self) -> bool:
        """Check if memory usage exceeds threshold."""
        if not self._process or not self.max_memory_mb:
            return False
        
        try:
            memory_info = self._process.memory_info()
            memory_mb = memory_info.rss / (1024 * 1024)
            
            if memory_mb > self.max_memory_mb:
                logger.warning(
                    f"Memory usage ({memory_mb:.1f}MB) exceeds "
                    f"threshold ({self.max_memory_mb}MB)"
                )
                return True
                
        except Exception as e:
            logger.warning(f"Failed to check memory usage: {e}")
        
        return False
    
    def get_memory_stats(self) -> MemoryStats:
        """
        Get current memory statistics.
        
        Returns:
            MemoryStats object with current memory information
        """
        # Process memory
        process_memory_mb = 0.0
        memory_percent = 0.0
        
        if self._process:
            try:
                memory_info = self._process.memory_info()
                process_memory_mb = memory_info.rss / (1024 * 1024)
                memory_percent = self._process.memory_percent()
            except Exception as e:
                logger.warning(f"Failed to get process memory: {e}")
        
        # System memory
        available_memory_mb = 0.0
        try:
            virtual_memory = psutil.virtual_memory()
            available_memory_mb = virtual_memory.available / (1024 * 1024)
        except Exception as e:
            logger.warning(f"Failed to get system memory: {e}")
        
        # GC stats
        gc_collections = {}
        for i in range(gc.get_count().__len__()):
            gc_collections[i] = gc.get_count()[i]
        
        return MemoryStats(
            process_memory_mb=process_memory_mb,
            available_memory_mb=available_memory_mb,
            memory_percent=memory_percent,
            gc_collections=gc_collections,
            cached_objects_count=len(self._cached_objects),
            evaluation_count=self.evaluation_count,
            cleanups_performed=self.cleanups_performed
        )
    
    def force_cleanup(self) -> None:
        """Force an immediate cleanup regardless of interval."""
        logger.info("Forcing immediate memory cleanup")
        self.perform_cleanup()
    
    def reset(self) -> None:
        """Reset the memory manager state."""
        self.evaluation_count = 0
        self.cleanups_performed = 0
        self._cached_objects.clear()
        self._cleanup_callbacks.clear()
        logger.info("Memory manager reset")