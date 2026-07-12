"""Constants for the expression-based rules engine."""

from enum import StrEnum


class MatchStrategy(StrEnum):
    """How a dimension matches context values against rule values."""

    EXACT = "exact"
    NOT_EQUAL = "not_equal"
    RANGE = "range"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    REGEX = "regex"
    CONTEXT_REGEX = "context_regex"
    SET_MEMBERSHIP = "set_membership"
    SET_EXCLUSION = "set_exclusion"


class DimensionRole(StrEnum):
    """Whether a dimension partitions the lattice or participates in coalesce."""

    CONSTRAINT = "constraint"
    CONTEXT_KEY = "context_key"


# Sentinel values for unknown/unset rule and context fields.
# These are passed to ma.t_col(unknown={...}) so the expression library
# treats them as UNKNOWN (0) in ternary logic automatically.
UNKNOWN = "<NA>"
NOT_SET = "<NOT_SET>"
UNKNOWN_NUMERIC = -999999999
NOT_SET_NUMERIC = -999999998

# All string sentinels and all numeric sentinels, for convenience.
STRING_SENTINELS = {UNKNOWN, NOT_SET}
NUMERIC_SENTINELS = {UNKNOWN_NUMERIC, NOT_SET_NUMERIC}

# Prefix for context literal columns added to the rules DataFrame during evaluation.
CTX_PREFIX = "__ctx_"
