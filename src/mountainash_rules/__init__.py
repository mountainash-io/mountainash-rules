"""Mountain Ash Utils Rules — expression-based rule evaluation engine."""

from mountainash_rules.__version__ import __version__
from mountainash_rules.accumulator_engine import AccumulatorEngine
from mountainash_rules.accumulator_result import AccumulatorResult
from mountainash_rules.aggregate import Aggregate
from mountainash_rules.compiler import DimensionCompiler
from mountainash_rules.constants import DimensionRole, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine
from mountainash_rules.lattice import Lattice
from mountainash_rules.result import RuleResult

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
