"""
Performance monitoring for the Enhanced VectorizedRulesEngine.

This module provides lightweight performance monitoring with minimal
overhead when disabled.
"""

import time
import logging
from contextlib import contextmanager
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import statistics


logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """
    Container for performance metrics.
    
    Tracks various performance indicators with minimal overhead.
    """
    
    # Basic counters
    total_evaluations: int = 0
    successful_evaluations: int = 0
    failed_evaluations: int = 0
    
    # Timing metrics
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0
    
    # Provider usage
    provider_usage: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    
    # Recent performance (sliding window)
    recent_times: deque = field(default_factory=lambda: deque(maxlen=100))
    
    # Detailed timing breakdown (if enabled)
    phase_timings: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    
    def get_average_time(self) -> float:
        """Calculate average evaluation time."""
        if self.total_evaluations == 0:
            return 0.0
        return self.total_time / self.total_evaluations
    
    def get_recent_average(self) -> float:
        """Calculate average of recent evaluations."""
        if not self.recent_times:
            return 0.0
        return statistics.mean(self.recent_times)
    
    def get_recent_p95(self) -> float:
        """Calculate 95th percentile of recent evaluations."""
        if not self.recent_times:
            return 0.0
        if len(self.recent_times) < 2:
            return self.recent_times[0] if self.recent_times else 0.0
        return statistics.quantiles(self.recent_times, n=20)[18]  # 95th percentile
    
    def get_success_rate(self) -> float:
        """Calculate success rate."""
        total = self.successful_evaluations + self.failed_evaluations
        if total == 0:
            return 1.0
        return self.successful_evaluations / total
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            'total_evaluations': self.total_evaluations,
            'successful_evaluations': self.successful_evaluations,
            'failed_evaluations': self.failed_evaluations,
            'success_rate': self.get_success_rate(),
            'total_time': self.total_time,
            'average_time': self.get_average_time(),
            'min_time': self.min_time if self.min_time != float('inf') else 0.0,
            'max_time': self.max_time,
            'recent_average': self.get_recent_average(),
            'recent_p95': self.get_recent_p95(),
            'provider_usage': dict(self.provider_usage),
            'recent_sample_size': len(self.recent_times)
        }


class PerformanceMonitor:
    """
    Lightweight performance monitoring with minimal overhead.
    
    This monitor tracks performance metrics with zero overhead when disabled.
    When enabled, it provides detailed timing and success rate tracking.
    
    Features:
    - Zero overhead when disabled
    - Minimal overhead when enabled
    - Sliding window for recent performance
    - Provider-specific tracking
    - Optional detailed phase timing
    """
    
    def __init__(self, 
                 enabled: bool = True,
                 detailed_timing: bool = False,
                 window_size: int = 100,
                 log_performance: bool = False):
        """
        Initialize the performance monitor.
        
        Args:
            enabled: Whether monitoring is enabled
            detailed_timing: Whether to track detailed phase timings
            window_size: Size of sliding window for recent metrics
            log_performance: Whether to log performance metrics
        """
        self.enabled = enabled
        self.detailed_timing = detailed_timing
        self.log_performance = log_performance
        
        if enabled:
            self.metrics = PerformanceMetrics()
            self.metrics.recent_times = deque(maxlen=window_size)
            self._current_evaluation_start: Optional[float] = None
            self._current_provider: Optional[str] = None
        else:
            self.metrics = None
    
    @contextmanager
    def time_evaluation(self, provider: str):
        """
        Context manager for timing evaluations.
        
        Zero overhead when monitoring is disabled.
        
        Args:
            provider: Name of the provider being used
            
        Examples:
            >>> monitor = PerformanceMonitor(enabled=True)
            >>> with monitor.time_evaluation('polars'):
            ...     # Perform evaluation
            ...     pass
        """
        if not self.enabled:
            yield
            return
        
        start = time.perf_counter()
        self._current_evaluation_start = start
        self._current_provider = provider
        success = True
        
        try:
            yield
        except Exception as e:
            success = False
            self.metrics.failed_evaluations += 1
            if self.log_performance:
                logger.warning(f"Evaluation failed for provider {provider}: {e}")
            raise
        finally:
            elapsed = time.perf_counter() - start
            self._record_evaluation(provider, elapsed, success)
    
    @contextmanager
    def time_phase(self, phase_name: str):
        """
        Context manager for timing specific phases.
        
        Only active when detailed timing is enabled.
        
        Args:
            phase_name: Name of the phase being timed
        """
        if not self.enabled or not self.detailed_timing:
            yield
            return
        
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.metrics.phase_timings[phase_name].append(elapsed)
    
    def _record_evaluation(self, provider: str, elapsed: float, success: bool):
        """Record evaluation metrics."""
        if not self.enabled:
            return
        
        # Update counters
        self.metrics.total_evaluations += 1
        if success:
            self.metrics.successful_evaluations += 1
        
        # Update timing
        self.metrics.total_time += elapsed
        self.metrics.min_time = min(self.metrics.min_time, elapsed)
        self.metrics.max_time = max(self.metrics.max_time, elapsed)
        self.metrics.recent_times.append(elapsed)
        
        # Update provider usage
        self.metrics.provider_usage[provider] += 1
        
        # Log if enabled
        if self.log_performance:
            logger.info(
                f"Evaluation completed: provider={provider}, "
                f"time={elapsed*1000:.2f}ms, success={success}, "
                f"total={self.metrics.total_evaluations}"
            )
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get current performance metrics.
        
        Returns:
            Dictionary of performance metrics, or empty dict if disabled
        """
        if not self.enabled:
            return {'monitoring_enabled': False}
        
        metrics = self.metrics.to_dict()
        metrics['monitoring_enabled'] = True
        metrics['detailed_timing_enabled'] = self.detailed_timing
        
        # Add phase timings if available
        if self.detailed_timing and self.metrics.phase_timings:
            phase_stats = {}
            for phase, timings in self.metrics.phase_timings.items():
                if timings:
                    phase_stats[phase] = {
                        'count': len(timings),
                        'total': sum(timings),
                        'average': statistics.mean(timings),
                        'min': min(timings),
                        'max': max(timings)
                    }
            metrics['phase_statistics'] = phase_stats
        
        return metrics
    
    def reset_metrics(self):
        """Reset all metrics to initial state."""
        if not self.enabled:
            return
        
        window_size = self.metrics.recent_times.maxlen
        self.metrics = PerformanceMetrics()
        self.metrics.recent_times = deque(maxlen=window_size)
        
        logger.info("Performance metrics reset")
    
    def log_summary(self):
        """Log a summary of current performance metrics."""
        if not self.enabled:
            return
        
        metrics = self.get_metrics()
        
        logger.info(
            f"Performance Summary: "
            f"Total={metrics['total_evaluations']}, "
            f"Success Rate={metrics['success_rate']:.2%}, "
            f"Avg Time={metrics['average_time']*1000:.2f}ms, "
            f"Recent Avg={metrics['recent_average']*1000:.2f}ms, "
            f"P95={metrics['recent_p95']*1000:.2f}ms"
        )
        
        if metrics.get('provider_usage'):
            logger.info(f"Provider Usage: {metrics['provider_usage']}")
        
        if metrics.get('phase_statistics'):
            for phase, stats in metrics['phase_statistics'].items():
                logger.info(
                    f"Phase '{phase}': "
                    f"Count={stats['count']}, "
                    f"Avg={stats['average']*1000:.2f}ms"
                )