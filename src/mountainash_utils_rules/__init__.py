from .__version__ import __version__

# from .rules import RulesEngine, DimensionsMetadata, MatchStrategy, Dimension

from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags
from mountainash_utils_rules.context import ContextHelper
from mountainash_utils_rules.rule_strategies import ExactMatchStrategy, RangeMatchStrategy, RegexMatchStrategy, MatchStrategyFactory, BaseMatchStrategy
from mountainash_utils_rules.dimension import DimensionsMetadata, MetadataManager, Dimension
from mountainash_utils_rules.observer import ObservabilityManager
from mountainash_utils_rules.rule_manager import RuleManager
from mountainash_utils_rules.engine import RulesEngine
# from mountainash_utils_rules.hybrid_engine import (
#     HybridRulesEngine,
#     HybridEngineConfig,
#     ProcessingMode,
#     create_performance_optimized_engine,
#     create_reliability_focused_engine,
#     create_development_engine
# )
# from mountainash_utils_rules.numpy_processor import NumpyRuleProcessor
from mountainash_utils_rules.vectorized_engine import (
    VectorizedRulesEngine,
    VectorizedEngineConfig,
    TernaryRuleProcessor,  # New enhanced processor with ternary logic
    # create_ultra_performance_engine,
    # create_memory_optimized_engine
)

# Enhanced VectorizedRulesEngine with provider pattern
# from mountainash_utils_rules.enhanced_vectorized_engine import (
#     EnhancedVectorizedRulesEngine,
#     create_polars_engine,
#     create_production_engine,
#     create_high_performance_engine
# )
# from mountainash_utils_rules.vectorized_config import VectorizedEngineConfig as EnhancedVectorizedEngineConfig
# from mountainash_utils_rules.providers import (
#     RuleEvaluationProvider,
#     PolarsProvider,
#     ProviderFactory
# )
# from mountainash_utils_rules.monitoring import (
#     PerformanceMonitor,
#     MemoryManager
# )

# Phase 4: DataFrameVectorizedRulesEngine - Now uses mountainash-dataframes ternary system
# Old dataframe_ternary_filters module replaced by mountainash-dataframes.utils.expressions.ternary
# Use mountainash-dataframes ternary expressions instead:
#   - TernaryColumnExpression, TernaryLogicalExpression
#   - PolarsTernaryExpressionVisitor
#   - TernaryExpressionBuilder
# Deprecated modules - moved to deprecated folder
# If you need these, import them directly from mountainash_utils_rules.deprecated


__all__ = (
    "__version__",

    "MatchStrategy",
    "RuleConstants",
    "RuleTrinaryFlags",

    "ContextHelper",

    "BaseMatchStrategy",
    "ExactMatchStrategy",
    "RangeMatchStrategy",
    "RegexMatchStrategy",
    "MatchStrategyFactory",

    "DimensionsMetadata",
    "MetadataManager",
    "Dimension",

    "ObservabilityManager",

    "RuleManager",
    "RulesEngine",

    # Phase 2: Hybrid numpy/ibis processing
    # "HybridRulesEngine",
    # "HybridEngineConfig",
    # "ProcessingMode",
    # "create_performance_optimized_engine",
    # "create_reliability_focused_engine",
    # "create_development_engine",
    # "NumpyRuleProcessor",

    # Phase 3: Pure vectorized polars processing
    "VectorizedRulesEngine",
    "VectorizedEngineConfig",
    "TernaryRuleProcessor",  # Enhanced with ternary logic
    # "create_ultra_performance_engine",
    # "create_memory_optimized_engine",

    # Phase 4: DataFrameVectorizedRulesEngine - Framework-integrated performance
    # "DataFrameVectorizedRulesEngine",
    # "DataFrameEngineConfig",
    # "create_dataframe_ultra_performance_engine",
    # "create_dataframe_framework_integrated_engine",
    # "create_dataframe_balanced_engine",
    # "create_dataframe_development_engine",

    # # DataFrameRuleProcessor components
    # "DataFrameRuleProcessor",
    # "DataFrameProcessorConfig",
    # "create_dataframe_rule_processor",
    # "create_high_performance_processor_config",
    # "create_memory_optimized_processor_config",

    # # HybridExpressionBuilder components
    # "HybridExpressionBuilder",
    # "HybridBuilderConfig",
    # "create_hybrid_expression_builder",
    # "create_performance_optimized_builder_config",
    # "create_framework_integrated_config",
    # "create_balanced_config",

    # # Ternary logic extensions - now provided by mountainash-dataframes
    # # Use: from mountainash_dataframes.utils.expressions.ternary import ...

    # # Performance benchmarking
    # "DataFrameBenchmarkRunner",
    # "BenchmarkConfig",
    # "run_quick_performance_validation",
    # "create_benchmark_report",

    # # Unified Engine Factory - Complete integration
    # "UnifiedEngineFactory",
    # "EngineType",
    # "EngineRequirements",
    # "EngineCapabilities",
    # "get_engine_factory",
    # "create_optimal_rules_engine",
    # "create_recommended_rules_engine",
    # "get_engine_recommendations",
    # "migrate_from_engine"
)
