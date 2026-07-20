"""Mountain Ash Utils Rules — expression-based rule evaluation engine."""

from mountainash_rules.__version__ import __version__
from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.result import AccumulatorResult
from mountainash_rules.engines.accumulator.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.compiler import DimensionCompiler
from mountainash_rules.core.constants import (
    NOT_SET,
    NOT_SET_DATE,
    NOT_SET_DATETIME,
    NOT_SET_NUMERIC,
    UNKNOWN,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    UNKNOWN_NUMERIC,
    DataType,
    DimensionRole,
    HitPolicy,
    MatchStrategy,
    not_set_sentinel_for,
    sentinels_for,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.batch_result import BatchRuleResult
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine
from mountainash_rules.core.hit_policy import HitPolicyViolationError, SelectionInfo
from mountainash_rules.engines.accumulator.lattice import (
    AmbiguousPartitionError,
    Lattice,
    LatticeIndex,
)
from mountainash_rules.core.result import ExplainResult, RuleResult

__all__ = (
    "__version__",
    "AccumulatorEngine",
    "AccumulatorResult",
    "Aggregate",
    "AggregateOp",
    "AmbiguousPartitionError",
    "BatchRuleResult",
    "DataType",
    "DimensionCompiler",
    "Dimension",
    "DimensionRole",
    "DimensionsMetadata",
    "ExplainResult",
    "ExpressionRulesEngine",
    "HitPolicy",
    "HitPolicyViolationError",
    "Lattice",
    "LatticeIndex",
    "MatchStrategy",
    "NOT_SET",
    "NOT_SET_DATE",
    "NOT_SET_DATETIME",
    "NOT_SET_NUMERIC",
    "RuleResult",
    "SelectionInfo",
    "UNKNOWN",
    "UNKNOWN_DATE",
    "UNKNOWN_DATETIME",
    "UNKNOWN_NUMERIC",
    "not_set_sentinel_for",
    "sentinels_for",
    "unknown_sentinel_for",
)
