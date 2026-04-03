"""Constants for the expression-based rules engine."""

from enum import Enum, auto


class MatchStrategy(Enum):
    """How a dimension matches context values against rule values."""

    EXACT = auto()
    RANGE = auto()
    REGEX = auto()


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
