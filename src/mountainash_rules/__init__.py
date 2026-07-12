"""Mountain Ash Utils Rules — expression-based rule evaluation engine."""

from mountainash_rules.__version__ import __version__
from mountainash_rules.accumulator_engine import AccumulatorEngine
from mountainash_rules.accumulator_result import AccumulatorResult
from mountainash_rules.aggregate import Aggregate
from mountainash_rules.compiler import DimensionCompiler
from mountainash_rules.constants import DataType, DimensionRole, HitPolicy, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.batch_result import BatchRuleResult
from mountainash_rules.engine import ExpressionRulesEngine
from mountainash_rules.hit_policy import HitPolicyViolationError, SelectionInfo
from mountainash_rules.lattice import Lattice, LatticeIndex
from mountainash_rules.result import RuleResult

__all__ = (
    "__version__",
    "AccumulatorEngine",
    "AccumulatorResult",
    "Aggregate",
    "BatchRuleResult",
    "DataType",
    "DimensionCompiler",
    "Dimension",
    "DimensionRole",
    "DimensionsMetadata",
    "ExpressionRulesEngine",
    "HitPolicy",
    "HitPolicyViolationError",
    "Lattice",
    "LatticeIndex",
    "MatchStrategy",
    "RuleResult",
    "SelectionInfo",
)
