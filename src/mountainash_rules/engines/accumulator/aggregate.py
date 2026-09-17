"""Aggregate declarations and exact native numeric folds."""

from __future__ import annotations

import datetime as dt
import math
import struct
import sys
import typing as t
from enum import StrEnum

from mountainash import ValueKind, value_kind
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from mountainash_rules.core.constants import DataType
from mountainash_rules.core.contracts import OperationBudget
from mountainash_rules.core.scalar import normalize_scalar


class AggregateOp(StrEnum):
    """Declared commutative reducer; exact-native folds use numeric-1."""

    SUM = "sum"
    MIN = "min"
    MAX = "max"
    PRODUCT = "product"


class Aggregate(BaseModel):
    """Output declaration; incomplete flat metadata is inspection-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    column_name: str
    operation: AggregateOp = AggregateOp.SUM
    output_name: str | None = None
    data_type: DataType | None = None
    numeric_semantics: t.Literal["numeric-1"] | None = None
    timezone: t.Literal["naive", "utc"] | None = None

    @field_validator("column_name", "output_name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is not None:
            if not value:
                raise ValueError("Aggregate names must be nonempty")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("Aggregate names must contain Unicode scalars") from exc
        return value

    @model_validator(mode="after")
    def _validate_declaration(self) -> "Aggregate":
        native = (self.output_name, self.data_type, self.numeric_semantics)
        if any(value is not None for value in native) and not all(value is not None for value in native):
            raise ValueError("Native aggregates require output_name, data_type and numeric_semantics together")
        if self.output_name is not None:
            parts = self.output_name.split(".")
            if len(parts) < 2 or not all(parts):
                raise ValueError("output_name requires nonempty dot-separated namespace and local name")
        if self.data_type is DataType.DATETIME:
            if self.timezone is None:
                raise ValueError("DATETIME aggregate requires explicit timezone")
        elif self.timezone is not None:
            raise ValueError("timezone is only valid for DATETIME aggregates")
        if self.data_type is not None and self.operation in (AggregateOp.SUM, AggregateOp.PRODUCT) and not self.data_type.is_numeric:
            raise ValueError("sum/product require an INT or FLOAT declaration")
        return self

    def native_record(self) -> dict[str, t.Any]:
        """Return complete semantic metadata, rejecting inspection-only inputs."""
        if self.output_name is None or self.data_type is None or self.numeric_semantics is None:
            raise ValueError(f"Incomplete exact aggregate declaration for {self.column_name!r}")
        result = self.model_dump(mode="json")
        if self.data_type is not DataType.DATETIME:
            result.pop("timezone")
        return result


def validate_aggregates(aggregates: t.Iterable[Aggregate]) -> tuple[Aggregate, ...]:
    """Freeze native declarations, checking output identity and shared sources."""
    declarations = tuple(aggregates)
    outputs: set[str] = set()
    sources: dict[str, tuple[DataType | None, str | None]] = {}
    for aggregate in declarations:
        aggregate.native_record()
        output = t.cast(str, aggregate.output_name)
        if output in outputs:
            raise ValueError(f"Duplicate aggregate output {output!r}")
        outputs.add(output)
        signature = aggregate.data_type, aggregate.timezone
        if aggregate.column_name in sources and sources[aggregate.column_name] != signature:
            raise ValueError(f"Incompatible declarations for source column {aggregate.column_name!r}")
        sources[aggregate.column_name] = signature
    return declarations


def lineage_relation(aggregates: t.Iterable[Aggregate], contributors: t.Any) -> t.Any:
    """Join declarations to validated unique source IDs and optional labels lazily.

    ``contributors`` is a Mountainash relation with source_id/source_label;
    selection of definite or candidate membership belongs to the artifact owner.
    No cell-by-output-by-source frame is retained at construction.
    """
    import mountainash.expressions as ma
    from mountainash.relations import relation

    declarations = validate_aggregates(aggregates)
    names = ("output_name", "column_name", "operation", "source_id", "source_label")
    if not declarations:
        return contributors.filter(ma.lit(False)).select(
            *[ma.col("source_id").cast(str).alias(name) for name in names]
        )
    outputs = relation({
        "output_name": [item.output_name for item in declarations],
        "column_name": [item.column_name for item in declarations],
        "operation": [item.operation.value for item in declarations],
    })
    return contributors.select("source_id", "source_label").cross_join(outputs).select(*names)


_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_REFERENCE_BYTES = struct.calcsize("P")
_LIST_BYTES = sys.getsizeof([])
_TWO_ITEM_TUPLE_BYTES = sys.getsizeof((0, 0))
_INTEGER_ZERO_BYTES = sys.getsizeof(0)
_FLOAT_BYTES = sys.getsizeof(0.0)
_BOOLEAN_BYTES = sys.getsizeof(False)
_DATE_BYTES = sys.getsizeof(dt.date.min)
_DATETIME_BYTES = sys.getsizeof(dt.datetime.min)
_STRING_BYTES = sys.getsizeof("")
_INTEGER_LIMB_BITS = sys.int_info.bits_per_digit
_INTEGER_LIMB_BYTES = sys.int_info.sizeof_digit


def _integer_bytes(bits: int) -> int:
    """Conservatively account for a Python arbitrary-precision integer."""
    limbs = max(1, (bits + _INTEGER_LIMB_BITS - 1) // _INTEGER_LIMB_BITS)
    return _INTEGER_ZERO_BYTES + limbs * _INTEGER_LIMB_BYTES


def _text_length(value: t.Any) -> int | None:
    """Read admitted text length without coercing another scalar domain."""
    if value_kind(value) is not ValueKind.TEXT:
        return None
    # value_kind admits only native text carriers; len() does not project one to str.
    return len(value)


def _input_bytes(data_type: DataType, text_length: int | None) -> int:
    """Return conservative decoded-input bytes for one scalar."""
    if data_type is DataType.STR:
        return 0 if text_length is None else 4 * text_length
    if data_type in (DataType.INT, DataType.FLOAT):
        return 8
    if data_type is DataType.BOOL:
        return 1
    if data_type is DataType.DATE:
        return 4
    return 10


def _scalar_bytes(data_type: DataType, text_length: int | None) -> int:
    """Return conservative live bytes for a normalized scalar plus its reference."""
    if data_type is DataType.STR:
        return _REFERENCE_BYTES + _STRING_BYTES + 4 * (text_length or 0)
    if data_type is DataType.INT:
        return _REFERENCE_BYTES + _integer_bytes(64)
    if data_type is DataType.FLOAT:
        return _REFERENCE_BYTES + _FLOAT_BYTES
    if data_type is DataType.BOOL:
        return _REFERENCE_BYTES + _BOOLEAN_BYTES
    if data_type is DataType.DATE:
        return _REFERENCE_BYTES + _DATE_BYTES
    return _REFERENCE_BYTES + _DATETIME_BYTES


def _retained_value_bytes(data_type: DataType, text_length: int | None) -> int:
    """Return the retained graph size for an extrema or numeric fold value."""
    if data_type is DataType.FLOAT:
        # The normalized float remains live while _float_parts creates this tuple.
        return (
            _scalar_bytes(data_type, text_length)
            + _TWO_ITEM_TUPLE_BYTES
            + _integer_bytes(53)
            + _integer_bytes(12)
        )
    return _scalar_bytes(data_type, text_length)


def _list_additional_bytes(length: int) -> int:
    """Bound list header and growth without a second reservation ledger."""
    if length == 0:
        return _LIST_BYTES + 4 * _REFERENCE_BYTES
    # Two slots per append dominates CPython's growth factor and leaves host slack.
    return 2 * _REFERENCE_BYTES


def _candidate_live_bytes(
    data_type: DataType,
    text_length: int | None,
    *,
    retained_length: int | None,
) -> int:
    value_bytes = _retained_value_bytes(data_type, text_length)
    if retained_length is None:
        return value_bytes
    return value_bytes + _list_additional_bytes(retained_length)


def _float_parts(value: float) -> tuple[int, int]:
    """Decode a normalized binary64 value as an exact dyadic."""
    bits = int.from_bytes(struct.pack(">d", value), "big")
    sign = -1 if bits >> 63 else 1
    exponent = (bits >> 52) & 0x7FF
    mantissa = bits & ((1 << 52) - 1)
    if exponent:
        mantissa |= 1 << 52
        exponent -= 1075
    else:
        exponent = -1074
    if mantissa:
        trailing = (mantissa & -mantissa).bit_length() - 1
        mantissa >>= trailing
        exponent += trailing
    return sign * mantissa, exponent


def _round_dyadic(mantissa: int, exponent: int) -> float:
    """Round an exact dyadic once to finite binary64, ties to even."""
    if not mantissa:
        return 0.0
    sign = -1 if mantissa < 0 else 1
    magnitude = abs(mantissa)
    top = magnitude.bit_length() - 1 + exponent
    if top > 1023:
        raise ValueError("Final float result is outside finite binary64")

    quantum = max(top - 52, -1074)
    shift = quantum - exponent
    if shift > magnitude.bit_length():
        rounded = 0
    elif shift > 0:
        rounded = magnitude >> shift
        remainder = magnitude - (rounded << shift)
        halfway = 1 << (shift - 1)
        if remainder > halfway or (remainder == halfway and rounded & 1):
            rounded += 1
    else:
        rounded = magnitude << -shift
    if not rounded:
        return 0.0

    try:
        result = math.ldexp(float(sign * rounded), quantum)
    except OverflowError as exc:
        raise ValueError("Final float result is outside finite binary64") from exc
    if not math.isfinite(result):
        raise ValueError("Final float result is outside finite binary64")
    return 0.0 if result == 0.0 else result


def _sum_bits(parts: t.Sequence[tuple[int, int]]) -> int:
    """Return the deterministic fixed-exponent accumulator capacity."""
    coefficient_bits = max(
        (abs(mantissa).bit_length() + exponent + 1074
         for mantissa, exponent in parts if mantissa),
        default=0,
    )
    return coefficient_bits + (len(parts) - 1).bit_length()


def _product_bits(parts: t.Sequence[tuple[int, int]]) -> int:
    """Return the deterministic exact product coefficient/exponent capacity."""
    return (
        sum(abs(mantissa).bit_length() for mantissa, _ in parts)
        + max(1, (1074 * len(parts)).bit_length())
    )


def _integer_sum_bits(values: t.Sequence[int]) -> int:
    return max((abs(value).bit_length() for value in values), default=0) + (len(values) - 1).bit_length()


def _integer_product_bits(values: t.Sequence[int]) -> int:
    return sum(abs(value).bit_length() for value in values)


def _workspace_live_bytes(bits: int) -> int:
    """Bound every simultaneous big-int slot during fold and final rounding."""
    # Accumulator, operand/result, abs copy, shifts, remainder, halfway and
    # rounded/sign-restored values can overlap. The extra bit covers carry.
    return 12 * _integer_bytes(bits + 1) + 8 * _REFERENCE_BYTES


def _reserve_workspace(
    budget: OperationBudget,
    *,
    bits: int,
    contributors: int,
) -> tuple[int, int]:
    """Reserve exact-state bits, arithmetic work, and all live big-int slots."""
    numeric_bits = 0
    live_bytes = 0
    try:
        budget.reserve("max_numeric_bits", bits, phase="exact_fold", units="bits")
        numeric_bits = bits
        budget.reserve(
            "max_work",
            bits * (contributors + 1),
            phase="exact_fold",
            units="integer-word operations",
        )
        requested_live_bytes = _workspace_live_bytes(bits)
        budget.reserve("max_live_bytes", requested_live_bytes, phase="exact_fold", units="bytes")
        live_bytes = requested_live_bytes
    except BaseException:
        if live_bytes:
            budget.release("max_live_bytes", live_bytes)
        if numeric_bits:
            budget.release("max_numeric_bits", numeric_bits)
        raise
    return numeric_bits, live_bytes


def _exact_integer_fold(
    values: t.Sequence[int],
    operation: AggregateOp,
    budget: OperationBudget,
) -> int:
    if operation is AggregateOp.SUM:
        bits = _integer_sum_bits(values)
    else:
        budget.reserve("max_work", len(values), phase="exact_fold", units="zero-product checks")
        if any(value == 0 for value in values):
            return 0
        bits = _integer_product_bits(values)

    numeric_bits, live_bytes = _reserve_workspace(budget, bits=bits, contributors=len(values))
    try:
        if operation is AggregateOp.SUM:
            result = 0
            for value in values:
                result += value
        else:
            result = 1
            for value in values:
                result *= value
        if not _INT64_MIN <= result <= _INT64_MAX:
            raise ValueError("Final integer result is outside signed int64")
        return result
    finally:
        budget.release("max_live_bytes", live_bytes)
        budget.release("max_numeric_bits", numeric_bits)


def _exact_float_fold(
    parts: t.Sequence[tuple[int, int]],
    operation: AggregateOp,
    budget: OperationBudget,
) -> float:
    if operation is AggregateOp.SUM:
        bits = _sum_bits(parts)
        exponent = -1074
    else:
        budget.reserve("max_work", len(parts), phase="exact_fold", units="zero-product checks")
        if any(mantissa == 0 for mantissa, _ in parts):
            return 0.0
        bits = _product_bits(parts)
        exponent = 0

    numeric_bits, live_bytes = _reserve_workspace(budget, bits=bits, contributors=len(parts))
    try:
        if operation is AggregateOp.SUM:
            mantissa = 0
            for value, value_exponent in parts:
                mantissa += value << (value_exponent + 1074)
        else:
            mantissa = 1
            for value, value_exponent in parts:
                mantissa *= value
                exponent += value_exponent
        return _round_dyadic(mantissa, exponent)
    finally:
        budget.release("max_live_bytes", live_bytes)
        budget.release("max_numeric_bits", numeric_bits)


def exact_fold(
    aggregate: Aggregate,
    values: t.Iterable[t.Any],
    *,
    budget: OperationBudget,
) -> t.Any:
    """Fold one nonempty contributor set under the complete numeric-1 declaration."""
    if not isinstance(aggregate, Aggregate):
        raise ValueError("exact_fold requires an Aggregate declaration")
    aggregate.native_record()
    if not isinstance(budget, OperationBudget):
        raise ValueError("exact_fold requires an OperationBudget")

    data_type = t.cast(DataType, aggregate.data_type)
    operation = aggregate.operation
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError("exact_fold values must be iterable") from exc

    retained: list[t.Any] | None = None
    retained_bytes = 0
    selected: t.Any = None
    selected_bytes = 0
    count = 0
    try:
        for value in iterator:
            text_length = _text_length(value) if data_type is DataType.STR else None
            budget.reserve(
                "max_input_bytes",
                _input_bytes(data_type, text_length),
                phase="exact_fold",
                units="decoded scalar bytes",
            )
            budget.reserve(
                "max_work",
                1 + (text_length or 0),
                phase="exact_fold",
                units="normalization steps",
            )
            retained_length = None if operation in (AggregateOp.MIN, AggregateOp.MAX) else (
                0 if retained is None else len(retained)
            )
            candidate_bytes = _candidate_live_bytes(
                data_type,
                text_length,
                retained_length=retained_length,
            )
            budget.reserve("max_live_bytes", candidate_bytes, phase="exact_fold", units="bytes")
            try:
                normalized = normalize_scalar(
                    value,
                    data_type,
                    timezone=aggregate.timezone,
                    context=False,
                    allow_reserved=True,
                )
            except BaseException:
                budget.release("max_live_bytes", candidate_bytes)
                raise
            count += 1
            if operation in (AggregateOp.MIN, AggregateOp.MAX):
                if selected_bytes:
                    if data_type is DataType.STR:
                        budget.reserve(
                            "max_work",
                            min(len(normalized), len(selected)) + 1,
                            phase="exact_fold",
                            units="Unicode scalar comparisons",
                        )
                    wins = (
                        operation is AggregateOp.MIN and normalized < selected
                        or operation is AggregateOp.MAX and normalized > selected
                    )
                else:
                    wins = True
                if wins:
                    if selected_bytes:
                        budget.release("max_live_bytes", selected_bytes)
                    selected = normalized
                    selected_bytes = candidate_bytes
                else:
                    budget.release("max_live_bytes", candidate_bytes)
                continue

            try:
                folded = _float_parts(normalized) if data_type is DataType.FLOAT else normalized
                if retained is None:
                    retained = []
                retained.append(folded)
            except BaseException:
                budget.release("max_live_bytes", candidate_bytes)
                raise
            retained_bytes += candidate_bytes

        if not count:
            raise ValueError("exact_fold requires at least one contribution")
        if operation in (AggregateOp.MIN, AggregateOp.MAX):
            return selected
        if operation not in (AggregateOp.SUM, AggregateOp.PRODUCT):
            raise ValueError(f"Unsupported aggregate operation {operation!r}")
        if data_type is DataType.INT:
            return _exact_integer_fold(t.cast(list[int], retained), operation, budget)
        if data_type is DataType.FLOAT:
            return _exact_float_fold(t.cast(list[tuple[int, int]], retained), operation, budget)
        raise ValueError("sum/product require an INT or FLOAT declaration")
    finally:
        if retained_bytes:
            budget.release("max_live_bytes", retained_bytes)
        if selected_bytes:
            budget.release("max_live_bytes", selected_bytes)
