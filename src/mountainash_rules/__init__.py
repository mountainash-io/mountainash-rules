"""Mountain Ash rules public API."""

from mountainash_rules.__version__ import __version__
from mountainash_rules.context_validation import check_context_schema, validate_contexts
from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.batch_result import BatchRuleResult
from mountainash_rules.core.compiler import DimensionCompiler
from mountainash_rules.core.constants import (
    UNKNOWN,
    UNKNOWN_DATE,
    UNKNOWN_DATETIME,
    UNKNOWN_NUMERIC,
    BooleanCoercion,
    DataType,
    HitPolicy,
    MatchStrategy,
    unknown_sentinel_for,
)
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.core.hit_policy import HitPolicyViolationError, SelectionInfo
from mountainash_rules.core.result import ExplainResult, RuleResult
from mountainash_rules.decision_table.apply import MatchOperation, MatchResult
from mountainash_rules.decision_table.build import build_decision_table
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.decision_table.persistence import VerificationReport, verify_saved_table
from mountainash_rules.decision_table.prevalidate import (
    DataContractError,
    Finding,
    ValidatedRules,
    prevalidate,
)
from mountainash_rules.decision_table.table import DecisionTable
from mountainash_rules.engines.filter.engine import ExpressionRulesEngine

__all__ = (
    "UNKNOWN",
    "UNKNOWN_DATE",
    "UNKNOWN_DATETIME",
    "UNKNOWN_NUMERIC",
    "Aggregate",
    "AggregateOp",
    "BatchRuleResult",
    "BooleanCoercion",
    "ContextField",
    "DataContractError",
    "DataType",
    "DecisionTable",
    "DecisionTableError",
    "Dimension",
    "DimensionCompiler",
    "DimensionsMetadata",
    "ExplainResult",
    "ExpressionRulesEngine",
    "Finding",
    "HitPolicy",
    "HitPolicyViolationError",
    "MatchOperation",
    "MatchResult",
    "MatchStrategy",
    "RuleResult",
    "SelectionInfo",
    "ValidatedRules",
    "VerificationReport",
    "__version__",
    "build_decision_table",
    "check_context_schema",
    "prevalidate",
    "unknown_sentinel_for",
    "validate_contexts",
    "verify_saved_table",
)
