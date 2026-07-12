"""Constants for the expression-based rules engine."""

import datetime
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


class HitPolicy(StrEnum):
    """Selection semantics applied over surviving rules."""

    COLLECT = "collect"        # all survivors, specificity order (default)
    UNIQUE = "unique"          # assert <= 1 survivor
    FIRST = "first"            # single survivor, rule order wins
    PRIORITY = "priority"      # single survivor, priority_field wins
    ANY = "any"                # survivors must agree on outputs; return one
    RULE_ORDER = "rule_order"  # all survivors, rule order


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


class DataType(StrEnum):
    """Serialisable dimension data type."""

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATE = "date"
    DATETIME = "datetime"

    @property
    def is_numeric(self) -> bool:
        return self in (DataType.INT, DataType.FLOAT)

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
    DataType.BOOL: bool,
    DataType.DATE: datetime.date,
    DataType.DATETIME: datetime.datetime,
}
PYTHON_TO_DATATYPE: dict[type, DataType] = {
    v: k for k, v in _DATATYPE_TO_PYTHON.items()
}

# Temporal don't-care sentinels: the proleptic floor, where no business
# data lives.
UNKNOWN_DATE = datetime.date(1, 1, 1)
NOT_SET_DATE = datetime.date(1, 1, 2)
UNKNOWN_DATETIME = datetime.datetime(1, 1, 1)
NOT_SET_DATETIME = datetime.datetime(1, 1, 2)
TEMPORAL_DATE_SENTINELS = {UNKNOWN_DATE, NOT_SET_DATE}
TEMPORAL_DATETIME_SENTINELS = {UNKNOWN_DATETIME, NOT_SET_DATETIME}


def sentinels_for(data_type: DataType) -> set:
    """The unknown-sentinel set the expression layer treats as UNKNOWN (0)."""
    if data_type.is_numeric:
        return NUMERIC_SENTINELS
    if data_type is DataType.DATE:
        return TEMPORAL_DATE_SENTINELS
    if data_type is DataType.DATETIME:
        return TEMPORAL_DATETIME_SENTINELS
    return STRING_SENTINELS


def unknown_sentinel_for(data_type: DataType):
    """The rule-side don't-care value for a data type."""
    if data_type.is_numeric:
        return UNKNOWN_NUMERIC
    if data_type is DataType.DATE:
        return UNKNOWN_DATE
    if data_type is DataType.DATETIME:
        return UNKNOWN_DATETIME
    return UNKNOWN


def not_set_sentinel_for(data_type: DataType):
    """The context-side missing-value sentinel for a data type."""
    if data_type.is_numeric:
        return NOT_SET_NUMERIC
    if data_type is DataType.DATE:
        return NOT_SET_DATE
    if data_type is DataType.DATETIME:
        return NOT_SET_DATETIME
    return NOT_SET
