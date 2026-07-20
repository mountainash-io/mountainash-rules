from enum import StrEnum

from pydantic import BaseModel


class AggregateOp(StrEnum):
    """Commutative fold applied when accumulating a numeric across a combination.

    All four operations are commutative and associative, so the accumulated
    value is independent of rule order within a combination.

    ``sum``/``product`` require a numeric column; ``min``/``max`` require an
    orderable column (numeric or temporal). No dtype validation is performed —
    the backend raises a clear error on an incompatible column.

    Known limitation: aggregate-value overflow is backend-defined and
    unguarded (only the combination identity ``__prime_product`` is int64
    guarded). ``product`` reaches that ceiling faster than ``sum``.
    """

    SUM = "sum"
    MIN = "min"
    MAX = "max"
    PRODUCT = "product"


class Aggregate(BaseModel):
    column_name: str
    operation: AggregateOp = AggregateOp.SUM
