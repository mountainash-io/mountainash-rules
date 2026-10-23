"""Constants for the expression-based rules engine."""

import datetime
from decimal import Decimal
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
        return self in (DataType.INT, DataType.FLOAT, DataType.DECIMAL)

    @property
    def is_temporal(self) -> bool:
        return self in (DataType.DATE, DataType.DATETIME)

    @property
    def python_type(self) -> type:
        return _DATATYPE_TO_PYTHON[self]


_DATATYPE_TO_PYTHON: dict[DataType, type] = {
    DataType.STR: str,
    DataType.INT: int,
    DataType.FLOAT: float,
    DataType.DECIMAL: Decimal,
    DataType.BOOL: bool,
    DataType.DATE: datetime.date,
    DataType.DATETIME: datetime.datetime,
}
PYTHON_TO_DATATYPE: dict[type, DataType] = {
    v: k for k, v in _DATATYPE_TO_PYTHON.items()
}

# Conventional temporal markers: the proleptic floor, where no business data lives.
UNKNOWN_DATE = datetime.date(1, 1, 1)
UNKNOWN_DATETIME = datetime.datetime(1, 1, 1)


def unknown_sentinel_for(data_type: DataType):
    """The conventional rule-side don't-care marker for a data type."""
    if data_type.is_numeric:
        return UNKNOWN_NUMERIC
    if data_type is DataType.DATE:
        return UNKNOWN_DATE
    if data_type is DataType.DATETIME:
        return UNKNOWN_DATETIME
    return UNKNOWN
