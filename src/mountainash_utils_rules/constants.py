from enum import Enum

class RuleType(Enum):
    EXACT = "EXACT"
    RANGE = "RANGE"
    REGEX = "REGEX"
    # WILDCARD = "WILDCARD"
    # FUZZY = "FUZZY"


class RuleConstants(Enum):
    
    UNKNOWN = "<NA>"
    NOT_SET = "<NOT_SET>"

    # Flags for Prime Filtering
    PRIME_TRUE = 2
    PRIME_FALSE = 3
    PRIME_UNKNOWN = 5

    ALLOWED_CONTEXT_TYPES = (str, int, float, bool, type(None))
