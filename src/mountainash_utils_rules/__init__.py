"""Mountain Ash Utils Rules — expression-based rule evaluation engine."""

from mountainash_utils_rules.__version__ import __version__
from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine
from mountainash_utils_rules.result import RuleResult

__all__ = (
    "__version__",
    "DimensionCompiler",
    "Dimension",
    "DimensionsMetadata",
    "ExpressionRulesEngine",
    "MatchStrategy",
    "RuleResult",
)
