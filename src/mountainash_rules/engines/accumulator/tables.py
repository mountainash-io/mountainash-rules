"""Bounded host/relational boundary for exact runtime state."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import mountainash.expressions as ma
from mountainash.relations import relation

from mountainash_rules.core.contracts import ExactCapabilityError, OperationBudget

# Storage names are shared by the native schema, not inferred from values.
_TYPES = {
    "utf8": str,
    "bool": bool,
    "int64": int,
    "float64": float,
    "date32": dt.date,
    "datetime_us_naive": dt.datetime,
    "datetime_us_utc": dt.datetime,
}
_SEEDS = {
    "utf8": "",
    "bool": False,
    "int64": 0,
    "float64": 0.0,
    "date32": dt.date(2000, 1, 1),
    "datetime_us_naive": dt.datetime(2000, 1, 1),
    "datetime_us_utc": dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc),
}


def storage_type(data_type, timezone=None):
    if str(data_type) == "datetime":
        return "datetime_us_utc" if timezone == "utc" else "datetime_us_naive"
    return {
        "str": "utf8",
        "bool": "bool",
        "int": "int64",
        "float": "float64",
        "date": "date32",
    }[str(data_type)]


_FIXED_WIDTHS = {
    "bool": 1,
    "int8": 1,
    "uint8": 1,
    "int16": 2,
    "uint16": 2,
    "int32": 4,
    "uint32": 4,
    "int64": 8,
    "uint64": 8,
    "float32": 4,
    "float64": 8,
    "date32": 4,
    "datetime_us_naive": 8,
    "datetime_us_utc": 8,
}


def native_storage_type(value) -> str:
    """Normalize a backend schema value to a conservative physical type."""
    text = str(getattr(value, "value", value)).lower()
    if text in {"i8", "i16", "i32", "i64"}:
        return "int" + text[1:]
    if text in {"u8", "u16", "u32", "u64"}:
        return "uint" + text[1:]
    if text in {"fp32", "fp64"}:
        return "float" + text[2:]
    for kind in _FIXED_WIDTHS:
        if kind in text:
            return kind
    if "datetime" in text or "timestamp" in text:
        return "datetime_us_naive"
    if "date" in text:
        return "date32"
    if "bool" in text:
        return "bool"
    if "utf8" in text or "string" in text or text == "str":
        return "utf8"
    return "opaque"


def _native_scalar_bytes(value, kind: str) -> int:
    if value is None:
        return 0
    if kind == "utf8":
        return 8 + 4 * len(value)
    return _FIXED_WIDTHS.get(kind, 64)


def relation_native_bytes(rows, columns) -> int:
    """Conservatively size native arrays for already-admitted typed rows."""
    size = 512 + 64 * len(columns)
    for row in rows:
        for name, kind, nullable in columns:
            size += _native_scalar_bytes(row[name], kind)
            if nullable:
                size += 1
    return size


def _host_value_bytes(value) -> int:
    if isinstance(value, str):
        return 128 + 4 * len(value)
    if isinstance(value, Mapping):
        return 256 + sum(
            _host_value_bytes(key) + _host_value_bytes(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return 128 + sum(_host_value_bytes(item) for item in value)
    return 64


def relation_host_bytes(rows, columns) -> int:
    """Conservatively size retained host rows without inferring backend layout."""
    return 512 + sum(
        256 + sum(_host_value_bytes(row[name]) for name, _kind, _nullable in columns)
        for row in rows
    )


def relation_buffer_bytes(rows, columns) -> int:
    """Shared host-plus-native accounting for a typed materialized relation."""
    return relation_host_bytes(rows, columns) + relation_native_bytes(rows, columns)


def native_column_bytes(
    rel, column, *, budget: OperationBudget, phase: str
) -> tuple[int, str]:
    """Preflight one native column before transferring it to a host-backed result."""
    if column not in rel.columns:
        raise ValueError(f"Column {column!r} is absent")
    kind = native_storage_type(rel.schema[column])
    count = rel.count_rows()
    budget.reserve("max_work", count, phase=phase, units="native column sizing rows")
    if kind == "utf8":
        workspace = max(1, count) * 128
        budget.reserve(
            "max_live_bytes",
            workspace,
            phase=phase,
            units="native string sizing buffer",
        )
        try:
            chars = (
                rel.group_by()
                .agg(ma.col(column).text_value().str.len_chars().sum().alias(column))
                .to_dicts()
            )
        finally:
            budget.release("max_live_bytes", workspace)
        string_bytes = 4 * ((chars[0][column] if chars else 0) or 0)
        return 512 + 64 + 8 * (count + 1) + string_bytes, kind
    width = _FIXED_WIDTHS.get(kind, 64)
    return 512 + 64 + count * width, kind


def make_relation(rows, columns, *, budget: OperationBudget):
    """Materialize narrow typed state; a filtered seed declares zero-row schemas."""
    size = relation_buffer_bytes(rows, columns)
    budget.reserve(
        "max_live_bytes",
        size * 3,
        phase="tables.materialize",
        units="host, ingress and materialized table bytes",
    )
    budget.reserve(
        "max_output_bytes",
        size,
        phase="tables.materialize",
        units="returned relation bytes",
    )
    budget.reserve(
        "max_work",
        max(1, len(rows)) * len(columns),
        phase="tables.materialize",
        units="typed scalar slots",
    )
    seed = not rows or any(
        kind == "datetime_us_utc" and all(row[name] is None for row in rows)
        for name, kind, _ in columns
    )
    values = {
        name: ([_SEEDS[kind]] if seed else []) + [row[name] for row in rows]
        for name, kind, _nullable in columns
    }
    result = relation(values).select(
        *[
            (
                ma.col(name)
                if kind == "datetime_us_utc"
                else ma.col(name).cast(_TYPES[kind])
            ).alias(name)
            for name, kind, _ in columns
        ]
    )
    if seed:
        result = result.slice(1)
    return relation(result.collect())


def source_rows(data, *, budget: OperationBudget):
    """Reserve original-scalar extraction before crossing the backend boundary."""
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        # prepare_sources owns each record's detailed scalar and retained-state charge.
        return data
    if isinstance(data, Mapping):
        names = tuple(data)
        if any(not isinstance(data[name], Sequence) for name in names):
            raise ValueError("column-oriented sources require column sequences")
        lengths = {len(data[name]) for name in names}
        if len(lengths) > 1:
            raise ValueError("source columns have unequal lengths")
        count = next(iter(lengths), 0)
        budget.reserve(
            "max_live_bytes",
            count * (256 + 128 * len(names)),
            phase="source.transfer",
            units="row mapping bytes",
        )
        return ({name: data[name][i] for name in names} for i in range(count))
    from mountainash.relations import Relation

    rel = data if isinstance(data, Relation) else relation(data)
    names = rel.columns
    budget.reserve(
        "max_work", len(names), phase="source.transfer", units="transfer schema fields"
    )
    # The size query transfers only counters; concrete rows are never cast.
    # UTF-8 scalar text bounds variable data; container overhead is charged separately.
    try:
        count = rel.count_rows()
        budget.reserve(
            "max_work",
            count * max(1, len(names)),
            phase="source.transfer",
            units="size preflight scalar slots",
        )
        schema = rel.schema
        scalar_names = [
            name
            for name in names
            if getattr(schema[name], "value", schema[name]) != "list"
        ]
        workspace = max(1, count) * 128
        budget.reserve(
            "max_live_bytes",
            workspace,
            phase="source.size",
            units="scalar size query buffers",
        )
        try:
            lengths = (
                rel.group_by()
                .agg(
                    *[
                        ma.col(name).text_value().str.len_chars().sum().alias(name)
                        for name in scalar_names
                    ]
                )
                .to_dicts()
                if scalar_names
                else []
            )
            chars = sum(value or 0 for value in lengths[0].values()) if lengths else 0
        finally:
            budget.release("max_live_bytes", workspace)
        for name in names:
            if name in scalar_names:
                continue
            entries = (
                rel.group_by()
                .agg(ma.col(name).list.len().sum().alias("n"))
                .to_dicts()[0]["n"]
                or 0
            )
            workspace = 256 * max(1, entries)
            budget.reserve(
                "max_work", entries, phase="source.size", units="nested scalar sizing"
            )
            budget.reserve(
                "max_live_bytes",
                workspace,
                phase="source.size",
                units="exploded scalar size buffers",
            )
            try:
                chars += entries * 16
                chars += (
                    rel.select(name)
                    .explode(name)
                    .group_by()
                    .agg(ma.col(name).text_value().str.len_chars().sum().alias("n"))
                    .to_dicts()[0]["n"]
                    or 0
                )
            finally:
                budget.release("max_live_bytes", workspace)
    except Exception as exc:
        from mountainash_rules.core.contracts import ExactResourceError

        if isinstance(exc, ExactResourceError):
            raise
        raise ExactCapabilityError(
            operation=budget.operation,
            backend=type(data).__module__,
            feature="bounded original-scalar host transfer",
            scalar_type=None,
            predicate_op=None,
        ) from exc
    size = 512 + count * (256 + 256 * len(names)) + 16 * chars
    budget.reserve(
        "max_input_bytes",
        size,
        phase="source.transfer",
        units="maximum original-scalar transfer bytes",
    )
    budget.reserve(
        "max_live_bytes",
        size * 3,
        phase="source.transfer",
        units="overlapping backend and host transfer buffers",
    )
    return rel.to_dicts()
