"""Mountain Ash Utils Rules — expression-based rule evaluation engine."""

from mountainash_rules.__version__ import __version__
from mountainash_rules._native import (
    LanguageResourceError,
    LanguageSyntaxError,
    LanguageWireError,
)
from mountainash_rules.core.batch_result import BatchRuleResult
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
from mountainash_rules.core.hit_policy import HitPolicyViolationError, SelectionInfo
from mountainash_rules.core.language import LanguageLimits, RegexOptions, StringLanguage
from mountainash_rules.core.result import ExplainResult, RuleResult
from mountainash_rules.engines.accumulator.aggregate import Aggregate, AggregateOp
from mountainash_rules.engines.accumulator.engine import AccumulatorEngine
from mountainash_rules.engines.accumulator.lattice import (
    AmbiguousPartitionError,
    Lattice,
    LatticeIndex,
)
from mountainash_rules.engines.accumulator.result import AccumulatorResult
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

__all__ = (
    "NOT_SET",
    "NOT_SET_DATE",
    "NOT_SET_DATETIME",
    "NOT_SET_NUMERIC",
    "UNKNOWN",
    "UNKNOWN_DATE",
    "UNKNOWN_DATETIME",
    "UNKNOWN_NUMERIC",
    "AccumulatorEngine",
    "AccumulatorResult",
    "Aggregate",
    "AggregateOp",
    "AmbiguousPartitionError",
    "BatchRuleResult",
    "DataType",
    "Dimension",
    "DimensionCompiler",
    "DimensionRole",
    "DimensionsMetadata",
    "ExplainResult",
    "ExpressionRulesEngine",
    "HitPolicy",
    "HitPolicyViolationError",
    "LanguageLimits",
    "LanguageResourceError",
    "LanguageSyntaxError",
    "LanguageWireError",
    "Lattice",
    "LatticeIndex",
    "MatchStrategy",
    "RegexOptions",
    "RuleResult",
    "SelectionInfo",
    "StringLanguage",
    "__version__",
    "not_set_sentinel_for",
    "sentinels_for",
    "unknown_sentinel_for",
)
