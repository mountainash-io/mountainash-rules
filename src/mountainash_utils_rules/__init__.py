from .__version__ import __version__

# from .rules import RulesEngine, DimensionsMetadata, MatchStrategy, Dimension

from mountainash_utils_rules.constants import MatchStrategy, RuleConstants, RuleTrinaryFlags
from mountainash_utils_rules.context import ContextHelper
from mountainash_utils_rules.rule_strategies import ExactMatchStrategy, RangeMatchStrategy, RegexMatchStrategy, MatchStrategyFactory, BaseMatchStrategy
from mountainash_utils_rules.dimension import DimensionsMetadata, MetadataManager, Dimension
from mountainash_utils_rules.observer import ObservabilityManager
from mountainash_utils_rules.rule_manager import RuleManager
from mountainash_utils_rules.engine import RulesEngine
from mountainash_utils_rules.hybrid_engine import (
    HybridRulesEngine, 
    HybridEngineConfig, 
    ProcessingMode,
    create_performance_optimized_engine,
    create_reliability_focused_engine,
    create_development_engine
)
from mountainash_utils_rules.numpy_processor import NumpyRuleProcessor
from mountainash_utils_rules.vectorized_engine import (
    VectorizedRulesEngine,
    VectorizedEngineConfig,
    PolarsRuleProcessor,
    create_ultra_performance_engine,
    create_memory_optimized_engine
)


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
    "HybridRulesEngine",
    "HybridEngineConfig", 
    "ProcessingMode",
    "create_performance_optimized_engine",
    "create_reliability_focused_engine",
    "create_development_engine",
    "NumpyRuleProcessor",
    
    # Phase 3: Pure vectorized polars processing
    "VectorizedRulesEngine",
    "VectorizedEngineConfig",
    "PolarsRuleProcessor", 
    "create_ultra_performance_engine",
    "create_memory_optimized_engine"
)
