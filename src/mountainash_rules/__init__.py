"""Mountain Ash rules engine public API."""

from mountainash_rules.__version__ import __version__
from mountainash_rules.core.aggregate import Aggregate, AggregateOp
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
    BooleanCoercion,
    DataType,
    HitPolicy,
    MatchStrategy,
    not_set_sentinel_for,
    sentinels_for,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.core.hit_policy import HitPolicyViolationError, SelectionInfo
from mountainash_rules.core.result import ExplainResult, RuleResult
from mountainash_rules.decision_table import DecisionTableError
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

__all__ = (
    "Aggregate",
    "ContextField",
    "AggregateOp",
    "BatchRuleResult",
    "BooleanCoercion",
    "DataType",
    "DecisionTableError",
    "Dimension",
    "DimensionCompiler",
    "DimensionsMetadata",
    "ExplainResult",
    "ExpressionRulesEngine",
    "HitPolicy",
    "HitPolicyViolationError",
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
    "__version__",
    "not_set_sentinel_for",
    "sentinels_for",
    "unknown_sentinel_for",
)
