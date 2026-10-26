"""Constants for the expression-based rules engine."""

import datetime
from enum import IntFlag, StrEnum


class BooleanCoercion(IntFlag):
    """Additional original input domains admitted by Boolean dimensions."""

    NONE = 0
    BINARY_NUMBERS = 1
    BOOLEAN_TEXT = 2
    NUMERIC_TRUTHINESS = 4


class MatchStrategy(StrEnum):
    """How a dimension matches context values against rule values."""

    EXACT = "exact"
    NOT_EQUAL = "not_equal"
    RANGE = "range"
    MIN = "min"
    MAX = "max"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    REGEX = "regex"
    CONTEXT_REGEX = "context_regex"
    SET_MEMBERSHIP = "set_membership"
    SET_EXCLUSION = "set_exclusion"
    TRUTHY = "truthy"
    OUTCOME = "outcome"
    PRESENCE = "presence"

class HitPolicy(StrEnum):
    """Selection semantics applied over surviving rules."""

    COLLECT = "collect"  # all survivors, specificity order (default)
    UNIQUE = "unique"  # assert <= 1 survivor
    FIRST = "first"  # single survivor, rule order wins
    PRIORITY = "priority"  # single survivor, priority_field wins
    ANY = "any"  # survivors must agree on outputs; return one
    RULE_ORDER = "rule_order"  # all survivors, rule order


# Conventional rule-side don't-care markers.  The engine gives them no meaning of their own:
# a marker is a wildcard only where a ContextField declares it as its ``wildcard``.
UNKNOWN = "<NA>"
UNKNOWN_NUMERIC = -999999999

# Prefix for context literal columns added to the rules DataFrame during evaluation.
CTX_PREFIX = "__ctx_"


class DataType(StrEnum):
    """Serialisable dimension data type."""

    STR = "str"
    INT = "int"
    FLOAT = "float"
    DECIMAL = "decimal"
    BOOL = "bool"
    DATE = "date"
    DATETIME = "datetime"

    @property
    def is_numeric(self) -> bool:
        from mountainash_rules.core.type_families import type_family
        return type_family(self).is_numeric

    @property
    def is_temporal(self) -> bool:
        from mountainash_rules.core.type_families import type_family
        return type_family(self).is_temporal

    @property
    def python_type(self) -> type:
        from mountainash_rules.core.type_families import type_family
        return type_family(self).python_type


# Conventional temporal markers: the proleptic floor, where no business data lives.
UNKNOWN_DATE = datetime.date(1, 1, 1)
UNKNOWN_DATETIME = datetime.datetime(1, 1, 1)


def unknown_sentinel_for(data_type: DataType):
    """The conventional rule-side don't-care marker for a data type."""
    from mountainash_rules.core.type_families import type_family
    return type_family(data_type).conventional_marker
