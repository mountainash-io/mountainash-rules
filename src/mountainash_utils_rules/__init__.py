"""Mountain Ash Utils Rules — expression-based rule evaluation engine."""

from mountainash_utils_rules.__version__ import __version__
from mountainash_utils_rules.accumulator_engine import AccumulatorEngine
from mountainash_utils_rules.accumulator_result import AccumulatorResult
from mountainash_utils_rules.aggregate import Aggregate
from mountainash_utils_rules.compiler import DimensionCompiler
from mountainash_utils_rules.constants import DimensionRole, MatchStrategy
from mountainash_utils_rules.dimension import Dimension, DimensionsMetadata
from mountainash_utils_rules.engine import ExpressionRulesEngine
from mountainash_utils_rules.lattice import Lattice
from mountainash_utils_rules.result import RuleResult

__all__ = (
    "__version__",
    "AccumulatorEngine",
    "AccumulatorResult",
    "Aggregate",
    "DimensionCompiler",
    "Dimension",
    "DimensionRole",
    "DimensionsMetadata",
    "ExpressionRulesEngine",
    "Lattice",
    "MatchStrategy",
    "RuleResult",
)
