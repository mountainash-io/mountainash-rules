"""
Unified Engine Factory - Phase 4 Integration

Comprehensive factory system integrating all rules engine implementations:
- Phase 1: Original RulesEngine
- Phase 2: HybridRulesEngine (numpy/ibis processing) 
- Phase 3: VectorizedRulesEngine (pure polars performance)
- Phase 4: DataFrameVectorizedRulesEngine (framework-integrated performance)

Provides intelligent engine selection based on requirements, performance targets,
and framework integration needs.

Phase 4C: Integration & Validation - Factory Function Integration
"""

import logging
from typing import Dict, List, Optional, Any, Union, Literal
from enum import Enum
from dataclasses import dataclass

from mountainash_dataframes import BaseDataFrame
from mountainash_utils_rules.dimension import Dimension

logger = logging.getLogger(__name__)


class EngineType(Enum):
    """Available rules engine types with performance and feature characteristics."""
    
    # Phase 1: Original engine
    ORIGINAL = "original"
    
    # Phase 2: Hybrid numpy/ibis processing  
    HYBRID_PERFORMANCE = "hybrid_performance"
    HYBRID_RELIABILITY = "hybrid_reliability"
    HYBRID_DEVELOPMENT = "hybrid_development"
    
    # Phase 3: Pure vectorized polars processing
    VECTORIZED_ULTRA = "vectorized_ultra"
    VECTORIZED_MEMORY = "vectorized_memory"
    
    # Phase 4: Framework-integrated performance
    DATAFRAME_ULTRA = "dataframe_ultra"
    DATAFRAME_BALANCED = "dataframe_balanced" 
    DATAFRAME_FRAMEWORK = "dataframe_framework"
    DATAFRAME_DEVELOPMENT = "dataframe_development"


@dataclass
class EngineRequirements:
    """Requirements specification for engine selection."""
    
    # Performance requirements
    target_performance_multiplier: float = 10.0  # Target speedup over baseline
    performance_priority: Literal["maximum", "balanced", "framework"] = "balanced"
    memory_constraints: Optional[str] = None  # "low", "medium", "high"
    
    # Framework integration requirements  
    framework_integration: Literal["none", "minimal", "balanced", "full"] = "balanced"
    ecosystem_benefits: bool = True
    cross_backend_compatibility: bool = False
    
    # Feature requirements
    ternary_logic_required: bool = True
    advanced_optimization: bool = True
    monitoring_and_analytics: bool = True
    
    # Operational requirements
    development_mode: bool = False
    production_ready: bool = True
    benchmarking_enabled: bool = False
    
    # Data characteristics
    expected_rule_count: Optional[int] = None
    expected_dimension_count: Optional[int] = None
    complex_match_strategies: bool = True


@dataclass
class EngineCapabilities:
    """Capabilities and characteristics of each engine type."""
    
    engine_type: EngineType
    performance_multiplier: float  # Expected speedup
    memory_efficiency: str  # "low", "medium", "high"
    framework_integration: str  # "none", "minimal", "balanced", "full"
    
    # Feature support
    supports_ternary_logic: bool
    supports_vectorization: bool
    supports_parallel_processing: bool
    supports_advanced_optimization: bool
    
    # Operational characteristics
    production_ready: bool
    development_features: bool
    monitoring_capabilities: bool
    
    # Recommended use cases
    recommended_for: List[str]
    limitations: List[str]


# Engine capability matrix
ENGINE_CAPABILITIES = {
    EngineType.ORIGINAL: EngineCapabilities(
        engine_type=EngineType.ORIGINAL,
        performance_multiplier=1.0,
        memory_efficiency="medium",
        framework_integration="none",
        supports_ternary_logic=True,
        supports_vectorization=False,
        supports_parallel_processing=False,
        supports_advanced_optimization=False,
        production_ready=True,
        development_features=False,
        monitoring_capabilities=True,
        recommended_for=["Legacy compatibility", "Simple rule sets", "Basic requirements"],
        limitations=["Lower performance", "No vectorization", "Limited optimization"]
    ),
    
    EngineType.HYBRID_PERFORMANCE: EngineCapabilities(
        engine_type=EngineType.HYBRID_PERFORMANCE,
        performance_multiplier=8.2,  # 75.2% improvement from Phase 2
        memory_efficiency="high",
        framework_integration="minimal",
        supports_ternary_logic=True,
        supports_vectorization=True,
        supports_parallel_processing=True,
        supports_advanced_optimization=True,
        production_ready=True,
        development_features=False,
        monitoring_capabilities=True,
        recommended_for=["High performance", "Memory constraints", "Numpy compatibility"],
        limitations=["Complex setup", "Limited framework benefits"]
    ),
    
    EngineType.VECTORIZED_ULTRA: EngineCapabilities(
        engine_type=EngineType.VECTORIZED_ULTRA,
        performance_multiplier=16.40,  # 93.9% improvement from Phase 3
        memory_efficiency="high",
        framework_integration="minimal",
        supports_ternary_logic=True,
        supports_vectorization=True,
        supports_parallel_processing=True,
        supports_advanced_optimization=True,
        production_ready=True,
        development_features=False,
        monitoring_capabilities=True,
        recommended_for=["Maximum performance", "Large rule sets", "High throughput"],
        limitations=["Minimal framework integration", "Polars dependency"]
    ),
    
    EngineType.DATAFRAME_ULTRA: EngineCapabilities(
        engine_type=EngineType.DATAFRAME_ULTRA,
        performance_multiplier=14.76,  # 90% retention of Phase 3 performance
        memory_efficiency="high",
        framework_integration="minimal",
        supports_ternary_logic=True,
        supports_vectorization=True,
        supports_parallel_processing=True,
        supports_advanced_optimization=True,
        production_ready=True,
        development_features=False,
        monitoring_capabilities=True,
        recommended_for=["Ultra performance with framework benefits", "Production systems"],
        limitations=["Minimal framework utilization"]
    ),
    
    EngineType.DATAFRAME_BALANCED: EngineCapabilities(
        engine_type=EngineType.DATAFRAME_BALANCED,
        performance_multiplier=13.12,  # ~80% retention with framework benefits
        memory_efficiency="high", 
        framework_integration="balanced",
        supports_ternary_logic=True,
        supports_vectorization=True,
        supports_parallel_processing=True,
        supports_advanced_optimization=True,
        production_ready=True,
        development_features=False,
        monitoring_capabilities=True,
        recommended_for=["Balanced performance and framework benefits", "Most use cases"],
        limitations=["Moderate performance trade-off"]
    ),
    
    EngineType.DATAFRAME_FRAMEWORK: EngineCapabilities(
        engine_type=EngineType.DATAFRAME_FRAMEWORK,
        performance_multiplier=11.48,  # ~70% retention with full framework benefits
        memory_efficiency="medium",
        framework_integration="full",
        supports_ternary_logic=True,
        supports_vectorization=True,
        supports_parallel_processing=True,
        supports_advanced_optimization=True,
        production_ready=True,
        development_features=True,
        monitoring_capabilities=True,
        recommended_for=["Maximum framework integration", "Ecosystem benefits", "Cross-backend"],
        limitations=["Performance trade-off for framework benefits"]
    ),
    
    EngineType.DATAFRAME_DEVELOPMENT: EngineCapabilities(
        engine_type=EngineType.DATAFRAME_DEVELOPMENT,
        performance_multiplier=12.00,  # Variable based on development settings
        memory_efficiency="medium",
        framework_integration="balanced",
        supports_ternary_logic=True,
        supports_vectorization=True,
        supports_parallel_processing=True,
        supports_advanced_optimization=True,
        production_ready=False,
        development_features=True,
        monitoring_capabilities=True,
        recommended_for=["Development", "Testing", "Performance analysis"],
        limitations=["Not optimized for production", "Additional overhead"]
    )
}


class UnifiedEngineFactory:
    """
    Unified factory for creating optimal rules engines based on requirements.
    
    Intelligently selects the best engine type based on performance targets,
    framework integration needs, and operational requirements.
    
    Key Features:
    - Intelligent engine selection based on requirements
    - Performance target matching
    - Framework integration optimization
    - Comprehensive capability analysis
    - Migration path recommendations
    
    Examples:
        >>> factory = UnifiedEngineFactory()
        >>> 
        >>> # High-performance production engine
        >>> requirements = EngineRequirements(
        ...     target_performance_multiplier=15.0,
        ...     performance_priority="maximum"
        ... )
        >>> engine = factory.create_optimal_engine(rules, dimensions, requirements)
        
        >>> # Balanced production engine  
        >>> engine = factory.create_recommended_engine(rules, dimensions)
        
        >>> # Framework-integrated engine
        >>> engine = factory.create_framework_integrated_engine(rules, dimensions)
    """
    
    def __init__(self, enable_performance_analysis: bool = True):
        self.enable_performance_analysis = enable_performance_analysis
        self.engine_selection_history: List[Dict[str, Any]] = []
        
        logger.info("UnifiedEngineFactory initialized with comprehensive engine support")
    
    def create_optimal_engine(self,
                             rules: BaseDataFrame,
                             dimensions: List[Dimension],
                             requirements: EngineRequirements) -> Any:
        """
        Create the optimal engine based on specific requirements.
        
        Analyzes requirements and selects the best engine type, then creates
        and configures it for optimal performance.
        
        Args:
            rules: BaseDataFrame containing rules to evaluate
            dimensions: List of dimension metadata
            requirements: Detailed requirements specification
            
        Returns:
            Optimally configured rules engine instance
            
        Example:
            >>> requirements = EngineRequirements(
            ...     target_performance_multiplier=15.0,
            ...     framework_integration="balanced",
            ...     production_ready=True
            ... )
            >>> engine = factory.create_optimal_engine(rules, dimensions, requirements)
        """
        # Analyze requirements and select optimal engine type
        optimal_type = self._select_optimal_engine_type(requirements)
        
        # Create engine with optimal configuration
        engine = self._create_engine_by_type(optimal_type, rules, dimensions, requirements)
        
        # Record selection for analysis
        self._record_engine_selection(optimal_type, requirements, rules, dimensions)
        
        logger.info(f"Created optimal engine: {optimal_type.value} for requirements")
        
        return engine
    
    def create_recommended_engine(self,
                                 rules: BaseDataFrame, 
                                 dimensions: List[Dimension],
                                 use_case: str = "production") -> Any:
        """
        Create recommended engine for common use cases.
        
        Provides opinionated defaults for common scenarios without requiring
        detailed requirements specification.
        
        Args:
            rules: BaseDataFrame containing rules to evaluate
            dimensions: List of dimension metadata
            use_case: Use case scenario ("production", "development", "high_performance", "framework")
            
        Returns:
            Recommended rules engine instance
            
        Example:
            >>> # Production-ready balanced engine
            >>> engine = factory.create_recommended_engine(rules, dimensions, "production")
        """
        use_case_requirements = {
            "production": EngineRequirements(
                target_performance_multiplier=12.0,
                performance_priority="balanced",
                framework_integration="balanced",
                production_ready=True,
                monitoring_and_analytics=True
            ),
            "high_performance": EngineRequirements(
                target_performance_multiplier=16.0,
                performance_priority="maximum",
                framework_integration="minimal",
                advanced_optimization=True
            ),
            "framework": EngineRequirements(
                performance_priority="framework",
                framework_integration="full",
                ecosystem_benefits=True,
                cross_backend_compatibility=True
            ),
            "development": EngineRequirements(
                performance_priority="balanced",
                framework_integration="balanced",
                development_mode=True,
                benchmarking_enabled=True,
                monitoring_and_analytics=True
            )
        }
        
        requirements = use_case_requirements.get(use_case, use_case_requirements["production"])
        
        return self.create_optimal_engine(rules, dimensions, requirements)
    
    def create_migration_engine(self,
                               rules: BaseDataFrame,
                               dimensions: List[Dimension],
                               current_engine_type: str,
                               target_performance_improvement: float = 2.0) -> Any:
        """
        Create engine optimized for migration from existing implementation.
        
        Provides smooth migration path with performance improvements while
        maintaining compatibility and reducing migration risk.
        
        Args:
            rules: BaseDataFrame containing rules to evaluate
            dimensions: List of dimension metadata  
            current_engine_type: Current engine type ("original", "hybrid", "vectorized")
            target_performance_improvement: Target performance multiplier improvement
            
        Returns:
            Migration-optimized rules engine instance
        """
        migration_paths = {
            "original": EngineType.DATAFRAME_BALANCED,  # Significant improvement with safety
            "hybrid": EngineType.DATAFRAME_ULTRA,       # Performance boost with framework
            "vectorized": EngineType.DATAFRAME_BALANCED, # Add framework benefits
        }
        
        recommended_type = migration_paths.get(current_engine_type, EngineType.DATAFRAME_BALANCED)
        
        requirements = EngineRequirements(
            target_performance_multiplier=target_performance_improvement,
            performance_priority="balanced",
            framework_integration="balanced",
            production_ready=True
        )
        
        engine = self._create_engine_by_type(recommended_type, rules, dimensions, requirements)
        
        logger.info(f"Created migration engine: {current_engine_type} -> {recommended_type.value}")
        
        return engine
    
    def _select_optimal_engine_type(self, requirements: EngineRequirements) -> EngineType:
        """Select optimal engine type based on requirements analysis."""
        
        # Score each engine type against requirements
        engine_scores = {}
        
        for engine_type, capabilities in ENGINE_CAPABILITIES.items():
            score = self._calculate_engine_score(capabilities, requirements)
            engine_scores[engine_type] = score
        
        # Select highest scoring engine
        optimal_type = max(engine_scores, key=engine_scores.get)
        
        if self.enable_performance_analysis:
            logger.debug(f"Engine selection scores: {[(t.value, s) for t, s in engine_scores.items()]}")
        
        return optimal_type
    
    def _calculate_engine_score(self, 
                               capabilities: EngineCapabilities,
                               requirements: EngineRequirements) -> float:
        """Calculate compatibility score between engine capabilities and requirements."""
        score = 0.0
        
        # Performance scoring
        performance_diff = abs(capabilities.performance_multiplier - requirements.target_performance_multiplier)
        if performance_diff == 0:
            score += 100
        else:
            score += max(0, 100 - (performance_diff * 5))  # Penalty for performance mismatch
        
        # Framework integration scoring
        framework_weights = {"none": 0, "minimal": 1, "balanced": 2, "full": 3}
        req_framework_weight = framework_weights.get(requirements.framework_integration, 1)
        cap_framework_weight = framework_weights.get(capabilities.framework_integration, 1)
        
        framework_diff = abs(req_framework_weight - cap_framework_weight)
        score += max(0, 50 - (framework_diff * 15))
        
        # Feature requirements scoring
        if requirements.ternary_logic_required and capabilities.supports_ternary_logic:
            score += 25
        if requirements.advanced_optimization and capabilities.supports_advanced_optimization:
            score += 25
        if requirements.monitoring_and_analytics and capabilities.monitoring_capabilities:
            score += 20
        
        # Production readiness scoring
        if requirements.production_ready and capabilities.production_ready:
            score += 30
        elif requirements.development_mode and capabilities.development_features:
            score += 30
        
        # Memory efficiency scoring
        memory_scores = {"low": 10, "medium": 20, "high": 30}
        if requirements.memory_constraints:
            required_memory = memory_scores.get(requirements.memory_constraints, 20)
            actual_memory = memory_scores.get(capabilities.memory_efficiency, 20)
            if actual_memory >= required_memory:
                score += 15
        else:
            score += memory_scores.get(capabilities.memory_efficiency, 20)
        
        return score
    
    def _create_engine_by_type(self,
                              engine_type: EngineType,
                              rules: BaseDataFrame,
                              dimensions: List[Dimension],
                              requirements: EngineRequirements) -> Any:
        """Create engine instance of specified type with optimal configuration."""
        
        try:
            if engine_type == EngineType.ORIGINAL:
                from mountainash_utils_rules import RulesEngine
                return RulesEngine(rules, dimensions)
            
            elif engine_type == EngineType.HYBRID_PERFORMANCE:
                from mountainash_utils_rules import create_performance_optimized_engine
                return create_performance_optimized_engine(rules, dimensions)
            
            elif engine_type == EngineType.HYBRID_RELIABILITY:
                from mountainash_utils_rules import create_reliability_focused_engine
                return create_reliability_focused_engine(rules, dimensions)
            
            elif engine_type == EngineType.VECTORIZED_ULTRA:
                from mountainash_utils_rules import create_ultra_performance_engine
                return create_ultra_performance_engine(rules, dimensions)
            
            elif engine_type == EngineType.VECTORIZED_MEMORY:
                from mountainash_utils_rules import create_memory_optimized_engine
                return create_memory_optimized_engine(rules, dimensions)
            
            elif engine_type == EngineType.DATAFRAME_ULTRA:
                from mountainash_utils_rules import create_dataframe_ultra_performance_engine
                return create_dataframe_ultra_performance_engine(rules, dimensions)
            
            elif engine_type == EngineType.DATAFRAME_BALANCED:
                from mountainash_utils_rules import create_dataframe_balanced_engine
                return create_dataframe_balanced_engine(rules, dimensions)
            
            elif engine_type == EngineType.DATAFRAME_FRAMEWORK:
                from mountainash_utils_rules import create_dataframe_framework_integrated_engine
                return create_dataframe_framework_integrated_engine(rules, dimensions)
            
            elif engine_type == EngineType.DATAFRAME_DEVELOPMENT:
                from mountainash_utils_rules import create_dataframe_development_engine
                return create_dataframe_development_engine(rules, dimensions)
            
            else:
                raise ValueError(f"Unsupported engine type: {engine_type}")
                
        except ImportError as e:
            logger.error(f"Failed to import engine type {engine_type}: {e}")
            # Fallback to available engine
            return self._create_fallback_engine(rules, dimensions)
        
    def _create_fallback_engine(self, rules: BaseDataFrame, dimensions: List[Dimension]) -> Any:
        """Create fallback engine when preferred type is unavailable."""
        try:
            # Try DataFrameBalanced as primary fallback
            from mountainash_utils_rules import create_dataframe_balanced_engine
            logger.warning("Using DataFrameBalanced engine as fallback")
            return create_dataframe_balanced_engine(rules, dimensions)
        except ImportError:
            try:
                # Try VectorizedUltra as secondary fallback
                from mountainash_utils_rules import create_ultra_performance_engine
                logger.warning("Using VectorizedUltra engine as fallback")
                return create_ultra_performance_engine(rules, dimensions)
            except ImportError:
                # Final fallback to original engine
                from mountainash_utils_rules import RulesEngine
                logger.warning("Using Original RulesEngine as final fallback")
                return RulesEngine(rules, dimensions)
    
    def _record_engine_selection(self,
                                engine_type: EngineType,
                                requirements: EngineRequirements,
                                rules: BaseDataFrame,
                                dimensions: List[Dimension]) -> None:
        """Record engine selection for analysis and optimization."""
        selection_record = {
            "timestamp": time.time(),
            "engine_type": engine_type.value,
            "requirements": {
                "target_performance": requirements.target_performance_multiplier,
                "performance_priority": requirements.performance_priority,
                "framework_integration": requirements.framework_integration,
                "production_ready": requirements.production_ready
            },
            "data_characteristics": {
                "rule_count": rules.count(),
                "dimension_count": len(dimensions),
                "match_strategies": [d.match_strategy.name for d in dimensions]
            }
        }
        
        self.engine_selection_history.append(selection_record)
    
    def get_engine_recommendation_analysis(self,
                                          rules: BaseDataFrame,
                                          dimensions: List[Dimension]) -> Dict[str, Any]:
        """
        Get comprehensive analysis of engine recommendations for given data.
        
        Returns detailed comparison of all available engines with recommendations.
        """
        analysis = {
            "data_characteristics": {
                "rule_count": rules.count(),
                "dimension_count": len(dimensions),
                "match_strategies": [d.match_strategy.name for d in dimensions],
                "complexity_score": self._calculate_data_complexity(rules, dimensions)
            },
            "engine_recommendations": {},
            "use_case_recommendations": {}
        }
        
        # Analyze each engine type
        for engine_type, capabilities in ENGINE_CAPABILITIES.items():
            suitability_score = self._calculate_suitability_score(capabilities, rules, dimensions)
            
            analysis["engine_recommendations"][engine_type.value] = {
                "suitability_score": suitability_score,
                "performance_multiplier": capabilities.performance_multiplier,
                "framework_integration": capabilities.framework_integration,
                "recommended_for": capabilities.recommended_for,
                "limitations": capabilities.limitations,
                "production_ready": capabilities.production_ready
            }
        
        # Use case specific recommendations
        use_cases = ["production", "high_performance", "framework", "development"]
        for use_case in use_cases:
            best_engine = self._get_best_engine_for_use_case(use_case, rules, dimensions)
            analysis["use_case_recommendations"][use_case] = {
                "recommended_engine": best_engine.value,
                "expected_performance": ENGINE_CAPABILITIES[best_engine].performance_multiplier,
                "rationale": self._get_use_case_rationale(use_case, best_engine)
            }
        
        return analysis
    
    def _calculate_data_complexity(self, rules: BaseDataFrame, dimensions: List[Dimension]) -> float:
        """Calculate complexity score for the given data characteristics."""
        complexity = 0.0
        
        # Rule count complexity
        rule_count = rules.count()
        if rule_count > 100000:
            complexity += 3.0
        elif rule_count > 10000:
            complexity += 2.0
        elif rule_count > 1000:
            complexity += 1.0
        
        # Dimension complexity
        dimension_count = len(dimensions)
        complexity += dimension_count * 0.5
        
        # Match strategy complexity
        strategy_weights = {
            MatchStrategy.EXACT: 1.0,
            MatchStrategy.RANGE: 1.5,
            MatchStrategy.REGEX: 2.0
        }
        
        for dimension in dimensions:
            complexity += strategy_weights.get(dimension.match_strategy, 1.0)
        
        return min(complexity, 10.0)  # Cap at 10.0
    
    def _calculate_suitability_score(self, 
                                   capabilities: EngineCapabilities,
                                   rules: BaseDataFrame,
                                   dimensions: List[Dimension]) -> float:
        """Calculate how suitable an engine is for the given data characteristics."""
        score = 50.0  # Base score
        
        rule_count = rules.count()
        dimension_count = len(dimensions)
        
        # Performance scaling suitability
        if rule_count > 10000 and capabilities.supports_vectorization:
            score += 20
        if rule_count > 50000 and capabilities.performance_multiplier > 10:
            score += 20
        
        # Dimension complexity suitability
        if dimension_count > 5 and capabilities.supports_parallel_processing:
            score += 15
        
        # Match strategy suitability
        has_regex = any(d.match_strategy == MatchStrategy.REGEX for d in dimensions)
        if has_regex and capabilities.supports_advanced_optimization:
            score += 10
        
        # Production readiness
        if capabilities.production_ready:
            score += 15
        
        return min(score, 100.0)
    
    def _get_best_engine_for_use_case(self,
                                     use_case: str,
                                     rules: BaseDataFrame,
                                     dimensions: List[Dimension]) -> EngineType:
        """Get the best engine for a specific use case."""
        use_case_priorities = {
            "production": [EngineType.DATAFRAME_BALANCED, EngineType.DATAFRAME_ULTRA],
            "high_performance": [EngineType.DATAFRAME_ULTRA, EngineType.VECTORIZED_ULTRA],
            "framework": [EngineType.DATAFRAME_FRAMEWORK, EngineType.DATAFRAME_BALANCED],
            "development": [EngineType.DATAFRAME_DEVELOPMENT, EngineType.DATAFRAME_BALANCED]
        }
        
        priorities = use_case_priorities.get(use_case, [EngineType.DATAFRAME_BALANCED])
        
        # Return first available engine from priority list
        for engine_type in priorities:
            if engine_type in ENGINE_CAPABILITIES:
                return engine_type
        
        return EngineType.DATAFRAME_BALANCED  # Final fallback
    
    def _get_use_case_rationale(self, use_case: str, engine_type: EngineType) -> str:
        """Get rationale for use case recommendation."""
        rationales = {
            ("production", EngineType.DATAFRAME_BALANCED): "Optimal balance of performance, framework benefits, and reliability",
            ("production", EngineType.DATAFRAME_ULTRA): "Maximum performance with framework integration for production systems", 
            ("high_performance", EngineType.DATAFRAME_ULTRA): "Revolutionary performance with framework benefits",
            ("high_performance", EngineType.VECTORIZED_ULTRA): "Ultimate performance optimization for high-throughput scenarios",
            ("framework", EngineType.DATAFRAME_FRAMEWORK): "Maximum framework integration and ecosystem benefits",
            ("development", EngineType.DATAFRAME_DEVELOPMENT): "Comprehensive development features and performance analysis"
        }
        
        return rationales.get((use_case, engine_type), "Recommended based on capability analysis")


# Global factory instance
_global_factory = None


def get_engine_factory() -> UnifiedEngineFactory:
    """Get global engine factory instance."""
    global _global_factory
    if _global_factory is None:
        _global_factory = UnifiedEngineFactory()
    return _global_factory


# Convenience functions
def create_optimal_rules_engine(rules: BaseDataFrame,
                               dimensions: List[Dimension], 
                               requirements: EngineRequirements) -> Any:
    """Create optimal rules engine based on requirements."""
    return get_engine_factory().create_optimal_engine(rules, dimensions, requirements)


def create_recommended_rules_engine(rules: BaseDataFrame,
                                   dimensions: List[Dimension],
                                   use_case: str = "production") -> Any:
    """Create recommended rules engine for common use case."""
    return get_engine_factory().create_recommended_engine(rules, dimensions, use_case)


def get_engine_recommendations(rules: BaseDataFrame,
                              dimensions: List[Dimension]) -> Dict[str, Any]:
    """Get comprehensive engine recommendations for given data."""
    return get_engine_factory().get_engine_recommendation_analysis(rules, dimensions)


def migrate_from_engine(rules: BaseDataFrame,
                       dimensions: List[Dimension],
                       current_engine_type: str,
                       target_improvement: float = 2.0) -> Any:
    """Create migration-optimized engine from existing implementation."""
    return get_engine_factory().create_migration_engine(
        rules, dimensions, current_engine_type, target_improvement
    )


# Import time fix for record function
import time