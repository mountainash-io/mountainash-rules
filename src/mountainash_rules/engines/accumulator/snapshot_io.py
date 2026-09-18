"""Bounded, descriptor-safe transport for native accumulator snapshots.

This module authenticates the fixed native wire before the lattice layer performs
semantic reconstruction.  It deliberately does not interpret predicates,
evidence, or compiled state.
"""

from __future__ import annotations

import ctypes
from decimal import Decimal
import errno
import hashlib
import math
import os
import pathlib
import re
import stat
import sys
import tempfile
import typing as t

import pyarrow as pa  # allow: snapshot parquet metadata/schema codec seam
import pyarrow.parquet as pq  # allow: snapshot parquet metadata/schema codec seam
import yaml
from yaml import tokens as yaml_tokens

from mountainash.relations import Relation, relation

from mountainash_rules.core.codec import (
    _bounded_json_size,
    _json_parse_live_bytes,
    _materialize_json,
    _transport_bytes,
    decode_json,
)
from mountainash_rules.core.contracts import (
    ExactResourceError,
    OperationBudget,
    Reservation,
)
from mountainash_rules.engines.accumulator.tables import native_column_bytes

_PARQUET_FILES = (
    "lattice.parquet",
    "sources.parquet",
    "contributors.parquet",
    "scopes.parquet",
    "scope_keys.parquet",
    "source_maps.parquet",
    "vectors.parquet",
    "words.parquet",
)
_JSON_FILES = ("predicates.json", "validation.json")
_DATA_FILES = frozenset((*_PARQUET_FILES, *_JSON_FILES))
_MANIFEST = "manifest.yaml"
_MAX_I64 = (1 << 63) - 1
_FILE_ENTRY_KEYS = frozenset(
    {"path", "format", "schema_version", "sha256", "columns", "row_count"}
)
_COLUMN_KEYS = frozenset({"name", "storage_type", "nullable"})
_STORAGE_TYPES = frozenset(
    {
        "utf8",
        "bool",
        "int64",
        "float64",
        "date32",
        "datetime_us_naive",
        "datetime_us_utc",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "artifact_kind",
        "native_schema_version",
        "semantic_versions",
        "ruleset_id",
        "source_bundle_id",
        "compilation_domain_id",
        "partition",
        "artifact_id",
        "dimensions",
        "aggregates",
        "output_schema",
        "source_schema",
        "context_contracts",
        "files",
        "binding_ids",
        "compiled_layout",
    }
)
_LEGACY_FIELDS = frozenset({"dimensions", "aggregates", "partition_key"})

_JSON_FLOAT = re.compile(
    r"^(?:-?(?:0|[1-9][0-9]*)\.[0-9]+(?:[eE][+-]?[0-9]+)?|-?(?:0|[1-9][0-9]*)[eE][+-]?[0-9]+)$"
)
_JSON_INT = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[Tt ][0-9]|$)")
_YAML_NONFINITE = frozenset({".nan", ".inf", "+.inf", "-.inf"})


class _StrictYamlLoader(yaml.SafeLoader):
    """YAML restricted to the JSON data model, with Decimal JSON numbers."""


# SafeLoader's resolver map is mutable at class level.  A fresh map prevents
# removal of timestamp resolution from changing unrelated loaders.
_StrictResolvers: dict[t.Any, list[tuple[str, re.Pattern[str]]]] = {}
_StrictYamlLoader.yaml_implicit_resolvers = _StrictResolvers
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:null", re.compile(r"null$"), ["n"]
)
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"(?:true|false)$"), ["t", "f"]
)
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int", _JSON_INT, list("-0123456789")
)
_StrictYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float", _JSON_FLOAT, list("-0123456789")
)


def _yaml_decimal(loader: _StrictYamlLoader, node: yaml.ScalarNode) -> Decimal:
    value = Decimal(loader.construct_scalar(node))
    if not value.is_finite():
        raise ValueError("YAML number must be finite")
    return value


def _yaml_mapping(
    loader: _StrictYamlLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, t.Any]:
    result: dict[str, t.Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("YAML object keys must be strings")
        if key == "<<":
            raise ValueError("YAML merge keys are forbidden")
        if key in result:
            raise ValueError(f"Duplicate YAML object key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictYamlLoader.add_constructor("tag:yaml.org,2002:float", _yaml_decimal)
_StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping
)


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: t.Any) -> bool:
        return True


def _yaml_decimal_representer(
    dumper: _NoAliasDumper, value: Decimal
) -> yaml.ScalarNode:
    if not value.is_finite():
        raise ValueError("YAML number must be finite")
    return dumper.represent_scalar("tag:yaml.org,2002:float", str(value).lower())


_NoAliasDumper.add_representer(Decimal, _yaml_decimal_representer)


def _checked_add(*values: int) -> int:
    total = 0
    for value in values:
        if type(value) is not int or value < 0 or value > _MAX_I64 - total:
            raise ValueError("Snapshot size arithmetic overflow")
        total += value
    return total


def _checked_product(left: int, right: int) -> int:
    if (
        type(left) is not int
        or type(right) is not int
        or left < 0
        or right < 0
        or (left and right > _MAX_I64 // left)
    ):
        raise ValueError("Snapshot size arithmetic overflow")
    return left * right


def _reserve(
    budget: OperationBudget, counter: str, amount: int, phase: str, units: str
) -> Reservation:
    return budget.reserve(counter, amount, phase=phase, units=units)


_PARQUET_FOOTER_TRAILER_BYTES = 8
_PARQUET_METADATA_SLOT_BYTES = 128
_PARQUET_METADATA_FIXED_BYTES = 65536
_PARQUET_ENCODER_FIXED_BYTES = 65536
_PARQUET_DATA_PAGE_BYTES = 65536
_PARQUET_WRITE_BATCH_ROWS = 1024
_PARQUET_ROW_GROUP_ROWS = 65536


def _parquet_footer_length(data: bytes, *, name: str) -> int:
    if len(data) < _PARQUET_FOOTER_TRAILER_BYTES or data[-4:] != b"PAR1":
        raise ValueError(f"Malformed Parquet snapshot file {name}")
    length = int.from_bytes(data[-8:-4], "little")
    if length > len(data) - _PARQUET_FOOTER_TRAILER_BYTES:
        raise ValueError(f"Malformed Parquet snapshot file {name}")
    return length


def _parquet_metadata_capacity(footer_length: int) -> int:
    return _checked_add(
        _PARQUET_METADATA_FIXED_BYTES,
        _checked_product(max(1, footer_length), _PARQUET_METADATA_SLOT_BYTES),
    )


def _parquet_physical_width(field: t.Any) -> int | None:
    return {
        "BOOLEAN": 1,
        "INT32": 4,
        "INT64": 8,
        "INT96": 12,
        "FLOAT": 4,
        "DOUBLE": 8,
    }.get(field.physical_type)


_PARQUET_I32_MAX = (1 << 31) - 1
_COMPACT_STOP = 0
_COMPACT_BOOLEAN_TRUE = 1
_COMPACT_BOOLEAN_FALSE = 2
_COMPACT_I32 = 5
_COMPACT_I64 = 6
_COMPACT_BINARY = 8
_COMPACT_STRUCT = 12
_PAGE_DATA = 0
_PAGE_INDEX = 1
_PAGE_DICTIONARY = 2
_PAGE_DATA_V2 = 3


class _PagePlan(t.NamedTuple):
    decoded_bytes: int
    dictionary_bytes: int
    page_count: int


def _compact_u32(data: bytes, offset: int, end: int) -> tuple[int, int]:
    """Read one bounded compact-Thrift varint without materialising a tree."""
    value = 0
    for shift in range(0, 35, 7):
        if offset >= end:
            raise ValueError("Parquet page header is truncated")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if value > 0xFFFFFFFF:
                raise ValueError("Parquet page header integer overflows")
            return value, offset
    raise ValueError("Parquet page header integer overflows")


def _compact_u64(data: bytes, offset: int, end: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= end:
            raise ValueError("Parquet page header is truncated")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if value > (1 << 64) - 1:
                raise ValueError("Parquet page header integer overflows")
            return value, offset
    raise ValueError("Parquet page header integer overflows")


def _compact_i32(data: bytes, offset: int, end: int) -> tuple[int, int]:
    value, offset = _compact_u32(data, offset, end)
    value = (value >> 1) ^ -(value & 1)
    if not -_PARQUET_I32_MAX - 1 <= value <= _PARQUET_I32_MAX:
        raise ValueError("Parquet page header integer overflows")
    return value, offset


def _compact_field(
    data: bytes, offset: int, end: int, previous: int
) -> tuple[int, int, int]:
    if offset >= end:
        raise ValueError("Parquet page header is truncated")
    header = data[offset]
    offset += 1
    field_type = header & 0x0F
    delta = header >> 4
    if field_type == _COMPACT_STOP:
        return 0, field_type, offset
    if delta:
        field_id = previous + delta
    else:
        field_id, offset = _compact_i32(data, offset, end)
    if not 0 < field_id <= 32767:
        raise ValueError("Parquet page header field ID is invalid")
    return field_id, field_type, offset


def _compact_skip(
    data: bytes, offset: int, end: int, field_type: int, depth: int = 0
) -> int:
    """Skip a known optional compact-Thrift statistics member without allocation."""
    if depth > 8:
        raise ValueError("Parquet page header nesting is excessive")
    if field_type in {_COMPACT_BOOLEAN_TRUE, _COMPACT_BOOLEAN_FALSE}:
        return offset
    if field_type == _COMPACT_I32:
        _value, offset = _compact_i32(data, offset, end)
        return offset
    if field_type == _COMPACT_I64:
        _value, offset = _compact_u64(data, offset, end)
        return offset
    if field_type == _COMPACT_BINARY:
        size, offset = _compact_u32(data, offset, end)
        offset = _checked_add(offset, size)
        if offset > end:
            raise ValueError("Parquet page header binary exceeds its page")
        return offset
    if field_type == _COMPACT_STRUCT:
        previous = 0
        seen = set()
        while True:
            field_id, nested_type, offset = _compact_field(data, offset, end, previous)
            if nested_type == _COMPACT_STOP:
                return offset
            if field_id in seen:
                raise ValueError("Parquet page header contains duplicate fields")
            seen.add(field_id)
            previous = field_id
            offset = _compact_skip(data, offset, end, nested_type, depth + 1)
    raise ValueError("Parquet page header field type is unsupported")


def _compact_struct(
    data: bytes,
    offset: int,
    end: int,
    *,
    fields: frozenset[int],
    boolean_fields: frozenset[int] = frozenset(),
    skipped_structures: frozenset[int] = frozenset(),
) -> tuple[dict[int, int | bool], int]:
    """Read only the scalar fields native page admission needs."""
    values: dict[int, int | bool] = {}
    previous = 0
    while True:
        field_id, field_type, offset = _compact_field(data, offset, end, previous)
        if field_type == _COMPACT_STOP:
            return values, offset
        previous = field_id
        if field_id not in fields or field_id in values:
            raise ValueError("Parquet page header fields are unsupported")
        if field_id in boolean_fields:
            if field_type == _COMPACT_BOOLEAN_TRUE:
                values[field_id] = True
            elif field_type == _COMPACT_BOOLEAN_FALSE:
                values[field_id] = False
            else:
                raise ValueError("Parquet page header Boolean is malformed")
        elif field_id in skipped_structures:
            if field_type != _COMPACT_STRUCT:
                raise ValueError("Parquet page header statistics are malformed")
            values[field_id] = False
            offset = _compact_skip(data, offset, end, field_type)
        elif field_type == _COMPACT_I32:
            values[field_id], offset = _compact_i32(data, offset, end)
        else:
            raise ValueError("Parquet page header field type is unsupported")


def _page_header(data: bytes, offset: int, end: int) -> tuple[int, int, int, int, int]:
    """Return page type, sizes, value count, dictionary count, and header end."""
    values: dict[int, int | bool | dict[int, int | bool]] = {}
    previous = 0
    while True:
        field_id, field_type, offset = _compact_field(data, offset, end, previous)
        if field_type == _COMPACT_STOP:
            break
        previous = field_id
        if field_id in values:
            raise ValueError("Parquet page header contains duplicate fields")
        if field_id in {1, 2, 3, 4}:
            if field_type != _COMPACT_I32:
                raise ValueError("Parquet page header scalar is malformed")
            values[field_id], offset = _compact_i32(data, offset, end)
        elif field_id == 5:
            if field_type != _COMPACT_STRUCT:
                raise ValueError("Parquet data page header is malformed")
            values[field_id], offset = _compact_struct(
                data,
                offset,
                end,
                fields=frozenset({1, 2, 3, 4, 5}),
                skipped_structures=frozenset({5}),
            )
        elif field_id == 6:
            if field_type != _COMPACT_STRUCT:
                raise ValueError("Parquet index page header is malformed")
            values[field_id], offset = _compact_struct(
                data, offset, end, fields=frozenset()
            )
        elif field_id == 7:
            if field_type != _COMPACT_STRUCT:
                raise ValueError("Parquet dictionary page header is malformed")
            values[field_id], offset = _compact_struct(
                data,
                offset,
                end,
                fields=frozenset({1, 2, 3}),
                boolean_fields=frozenset({3}),
            )
        elif field_id == 8:
            if field_type != _COMPACT_STRUCT:
                raise ValueError("Parquet data page v2 header is malformed")
            values[field_id], offset = _compact_struct(
                data,
                offset,
                end,
                fields=frozenset({1, 2, 3, 4, 5, 6, 7, 8}),
                boolean_fields=frozenset({7}),
                skipped_structures=frozenset({8}),
            )
        else:
            raise ValueError("Parquet page header fields are unsupported")
    if not {1, 2, 3}.issubset(values):
        raise ValueError("Parquet page header lacks required fields")
    page_type = values[1]
    uncompressed = values[2]
    compressed = values[3]
    if not all(type(item) is int for item in (page_type, uncompressed, compressed)):
        raise ValueError("Parquet page header scalar is malformed")
    if page_type not in {_PAGE_DATA, _PAGE_INDEX, _PAGE_DICTIONARY, _PAGE_DATA_V2}:
        raise ValueError("Parquet page type is unsupported")
    if uncompressed < 0 or compressed < 0:
        raise ValueError("Parquet page size is negative")
    value_count = 0
    dictionary_count = 0
    if page_type == _PAGE_DATA:
        header = values.get(5)
        if not isinstance(header, dict) or type(header.get(1)) is not int:
            raise ValueError("Parquet data page lacks a value count")
        value_count = header[1]
    elif page_type == _PAGE_DATA_V2:
        header = values.get(8)
        if not isinstance(header, dict) or any(
            type(header.get(key)) is not int for key in (1, 2, 3, 5, 6)
        ):
            raise ValueError("Parquet data page v2 lacks required counts")
        value_count = header[1]
        null_count = header[2]
        row_count = header[3]
        definition_bytes = header[5]
        repetition_bytes = header[6]
        if (
            min(value_count, null_count, row_count, definition_bytes, repetition_bytes)
            < 0
        ):
            raise ValueError("Parquet data page v2 count is negative")
        if null_count > value_count or row_count != value_count:
            raise ValueError("Parquet data page v2 cardinality is invalid")
        if _checked_add(definition_bytes, repetition_bytes) > compressed:
            raise ValueError("Parquet data page v2 level sizes exceed payload")
    elif page_type == _PAGE_DICTIONARY:
        header = values.get(7)
        if not isinstance(header, dict) or type(header.get(1)) is not int:
            raise ValueError("Parquet dictionary page lacks a value count")
        dictionary_count = header[1]
    if value_count < 0 or dictionary_count < 0:
        raise ValueError("Parquet page cardinality is negative")
    return page_type, uncompressed, compressed, value_count or dictionary_count, offset


def _parquet_page_plan(parquet: pq.ParquetFile, data: bytes) -> _PagePlan:
    """Validate every referenced page range before native decompression begins."""
    footer_start = (
        len(data)
        - _PARQUET_FOOTER_TRAILER_BYTES
        - _parquet_footer_length(data, name="Parquet")
    )
    metadata = parquet.metadata
    decoded = dictionary = pages = 0
    columns = len(parquet.schema_arrow)
    for group_index in range(metadata.num_row_groups):
        group = metadata.row_group(group_index)
        group_rows = group.num_rows
        if (
            type(group_rows) is not int
            or group_rows < 0
            or group.num_columns != columns
        ):
            raise ValueError("Parquet row group metadata is invalid")
        for column_index in range(columns):
            chunk = group.column(column_index)
            compressed_size = chunk.total_compressed_size
            declared = chunk.total_uncompressed_size
            data_offset = chunk.data_page_offset
            raw_dictionary_offset = chunk.dictionary_page_offset
            dictionary_offset = (
                0 if raw_dictionary_offset is None else raw_dictionary_offset
            )
            if (
                type(compressed_size) is not int
                or type(declared) is not int
                or type(data_offset) is not int
                or type(dictionary_offset) is not int
                or compressed_size < 0
                or declared < 0
                or dictionary_offset < 0
            ):
                raise ValueError("Parquet column chunk offsets or sizes are invalid")
            if group_rows == 0 and compressed_size == declared == 0:
                continue
            if data_offset < 4:
                raise ValueError("Parquet column chunk offsets or sizes are invalid")
            start = dictionary_offset or data_offset
            if dictionary_offset and dictionary_offset > data_offset:
                raise ValueError("Parquet dictionary page offset is invalid")
            end = _checked_add(start, compressed_size)
            if start >= footer_start or end > footer_start:
                raise ValueError("Parquet column chunk range is invalid")
            offset = start
            chunk_decoded = chunk_dictionary = chunk_values = 0
            seen_dictionary = False
            physical_width = _parquet_physical_width(
                parquet.schema.column(column_index)
            )
            while offset < end:
                page_type, uncompressed, compressed, count, header_end = _page_header(
                    data, offset, end
                )
                payload_end = _checked_add(header_end, compressed)
                if payload_end > end:
                    raise ValueError("Parquet page payload exceeds its column chunk")
                if page_type == _PAGE_DICTIONARY:
                    if seen_dictionary:
                        raise ValueError(
                            "Parquet column chunk has multiple dictionary pages"
                        )
                    seen_dictionary = True
                    minimum = physical_width if physical_width is not None else 4
                    if count > uncompressed // minimum:
                        raise ValueError(
                            "Parquet dictionary cardinality exceeds its payload"
                        )
                    chunk_dictionary = _checked_add(chunk_dictionary, uncompressed)
                elif page_type in {_PAGE_DATA, _PAGE_DATA_V2}:
                    chunk_values = _checked_add(chunk_values, count)
                chunk_decoded = _checked_add(chunk_decoded, uncompressed)
                pages = _checked_add(pages, 1)
                offset = payload_end
            if offset != end or chunk_values != group_rows or chunk_decoded > declared:
                raise ValueError("Parquet page totals do not match the column chunk")
            decoded = _checked_add(decoded, chunk_decoded)
            dictionary = _checked_add(dictionary, chunk_dictionary)
    return _PagePlan(decoded, dictionary, pages)


def _parquet_native_capacity(
    parquet: pq.ParquetFile, *, data: bytes, plan: _PagePlan | None = None
) -> int:
    """Bound decoded Arrow buffers, codec workspaces, and dictionary expansion."""
    metadata = parquet.metadata
    rows = metadata.num_rows
    columns = len(parquet.schema_arrow)
    plan = plan or _parquet_page_plan(parquet, data)
    total = _checked_add(
        _PARQUET_ENCODER_FIXED_BYTES,
        _checked_product(4096, max(1, columns)),
        plan.decoded_bytes,
        plan.dictionary_bytes,
    )
    for group_index in range(metadata.num_row_groups):
        group = metadata.row_group(group_index)
        group_rows = group.num_rows
        if type(group_rows) is not int or group_rows < 0:
            raise ValueError("Parquet row group has an invalid row count")
        if group.num_columns != columns:
            raise ValueError("Parquet row group column count changed")
        for column_index in range(columns):
            width = _parquet_physical_width(parquet.schema.column(column_index))
            if width is None:
                # Dictionary and delta-prefix encodings can repeat decoded
                # chunk bytes in every output row.
                values = _checked_product(group_rows, max(1, plan.decoded_bytes))
                slots = _checked_product(group_rows, 9)
            else:
                values = _checked_product(group_rows, width)
                slots = group_rows
            total = _checked_add(total, values, slots, 4096)
    if type(rows) is not int or rows < 0:
        raise ValueError("Parquet metadata has an invalid row count")
    return total


class _BoundedParquetSink:
    """A non-seekable descriptor sink that cannot outgrow its reservation."""

    def __init__(
        self, raw: t.BinaryIO, *, capacity: int, budget: OperationBudget
    ) -> None:
        self._raw = raw
        self._capacity = capacity
        self._budget = budget
        self._written = 0
        self.closed = False

    def write(self, data: t.Any) -> int:
        size = len(data)
        requested = _checked_add(self._written, size)
        if requested > self._capacity:
            raise ExactResourceError(
                operation=self._budget.operation,
                phase="snapshot.parquet_write",
                counter="max_output_bytes",
                limit=self._capacity,
                observed=self._written,
                requested=requested,
                units="reserved parquet output bytes",
            )
        written = self._raw.write(data)
        if written is None:
            written = size
        if written != size:
            raise OSError("Short snapshot write")
        self._written = requested
        return written

    def tell(self) -> int:
        return self._written

    def flush(self) -> None:
        self._raw.flush()

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        if not self.closed:
            self.flush()
            self.closed = True


def _open_directory(path: str | pathlib.Path) -> int:
    """Open every component without following a link, returning an owned fd."""
    candidate = pathlib.PurePath(os.fspath(path))
    if not candidate.parts:
        raise ValueError("Snapshot path is empty")
    if candidate.is_absolute():
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        parts = candidate.parts[1:]
    else:
        fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        parts = candidate.parts
    try:
        for part in parts:
            if part in {"", ".", ".."}:
                raise ValueError("Snapshot paths must not contain dot components")
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=fd,
                )
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError("Snapshot directory path is unsafe") from exc
                raise
            os.close(fd)
            fd = next_fd
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError("Snapshot root is not a directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _split_destination(path: str | pathlib.Path) -> tuple[int, str]:
    candidate = pathlib.PurePath(os.fspath(path))
    name = candidate.name
    if name in {"", ".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Snapshot destination must name one directory")
    return _open_directory(candidate.parent), name


def _read_child(
    dir_fd: int, name: str, *, budget: OperationBudget, phase: str
) -> bytes:
    if name not in _DATA_FILES | {_MANIFEST}:
        raise ValueError("Snapshot attempted an unrecognised child path")
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=dir_fd,
        )
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("Snapshot child must not be a symbolic link") from exc
        raise
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"Snapshot file {name} is not regular")
        size = metadata.st_size
        if type(size) is not int or size < 0:
            raise ValueError(f"Snapshot file {name} has invalid size")
        _reserve(budget, "max_input_bytes", size, phase, "snapshot file bytes")
        hold = _reserve(
            budget,
            "max_live_bytes",
            _checked_add(size, size),
            phase,
            "snapshot read and immutable byte buffers",
        )
        try:
            result = bytearray(size)
            offset = 0
            while offset < size:
                chunk = os.read(fd, min(1 << 20, size - offset))
                if not chunk:
                    raise ValueError(f"Snapshot file {name} changed while being read")
                result[offset : offset + len(chunk)] = chunk
                offset += len(chunk)
            if os.read(fd, 1):
                raise ValueError(f"Snapshot file {name} changed while being read")
            return bytes(result)
        finally:
            budget.release(hold.counter, hold.amount)
    finally:
        os.close(fd)


def _yaml_token_check(text: str, *, budget: OperationBudget) -> None:
    try:
        for token in yaml.scan(text, Loader=_StrictYamlLoader):
            _reserve(budget, "max_work", 1, "snapshot.yaml_scan", "YAML token")
            if isinstance(
                token,
                (yaml_tokens.AliasToken, yaml_tokens.AnchorToken, yaml_tokens.TagToken),
            ):
                raise ValueError("YAML aliases, anchors, and tags are forbidden")
            if (
                not isinstance(token, yaml_tokens.ScalarToken)
                or token.style is not None
            ):
                continue
            if token.value.lower() in _YAML_NONFINITE:
                raise ValueError("YAML number must be finite")
            if _TIMESTAMP.fullmatch(token.value):
                raise ValueError("YAML timestamps must be quoted scalar-codec strings")
    except yaml.YAMLError as exc:
        raise ValueError("Malformed YAML manifest") from exc


_JSON_CONTAINER_BYTES = max(
    256, sys.getsizeof({}), sys.getsizeof([]), sys.getsizeof({"key": None})
)
_JSON_CONTAINER_ENTRY_BYTES = 128
_JSON_PARSER_FRAME_BYTES = max(
    1024,
    sys.getsizeof({"kind": "object", "value": {}, "key": None, "state": "key_or_end"}),
)
_JSON_SERIALIZER_FRAME_BYTES = 512
_YAML_NODE_BYTES = max(
    512,
    sys.getsizeof(yaml.MappingNode("tag:yaml.org,2002:map", [])),
    sys.getsizeof(yaml.SequenceNode("tag:yaml.org,2002:seq", [])),
    sys.getsizeof(yaml.ScalarNode("tag:yaml.org,2002:str", "")),
)


def _decode_capacities(data_size: int, *, yaml_nodes: bool) -> tuple[int, int]:
    """Bound retained decoded values and all text/parser frame allocations."""
    values = _checked_add(data_size, 1)
    retained = _checked_add(
        _json_parse_live_bytes(data_size),
        _checked_product(values, _JSON_CONTAINER_BYTES),
        _checked_product(values, _JSON_CONTAINER_ENTRY_BYTES),
    )
    temporary = _checked_add(
        _checked_product(data_size, 4),
        _checked_product(values, _JSON_PARSER_FRAME_BYTES),
        _checked_product(values, _YAML_NODE_BYTES) if yaml_nodes else 0,
    )
    return retained, temporary


def _serialization_shape(
    value: t.Any, *, wire_size: int, budget: OperationBudget, phase: str
) -> tuple[int, int, int]:
    """Measure nodes/edges/depth before materialising mutable serializer input."""
    frame_hold = _reserve(
        budget,
        "max_live_bytes",
        _checked_product(_checked_add(wire_size, 1), _JSON_SERIALIZER_FRAME_BYTES),
        phase,
        "serialization preflight frames and cycle guard",
    )
    nodes = edges = depth = 0
    active: set[int] = set()
    stack: list[tuple[t.Any, int, bool]] = [(value, 1, False)]
    try:
        while stack:
            item, level, close = stack.pop()
            if close:
                active.remove(id(item))
                continue
            nodes = _checked_add(nodes, 1)
            depth = max(depth, level)
            if not isinstance(item, t.Mapping | list | tuple):
                continue
            identity = id(item)
            if identity in active:
                raise ValueError("cyclic JSON payload")
            active.add(identity)
            stack.append((item, level, True))
            children = item.values() if isinstance(item, t.Mapping) else item
            for child in children:
                edges = _checked_add(edges, 1)
                stack.append((child, _checked_add(level, 1), False))
            _reserve(budget, "max_work", 1, phase, "serialization container preflight")
        return nodes, edges, depth
    finally:
        budget.release(frame_hold.counter, frame_hold.amount)


def _serialization_capacity(
    *, wire_size: int, nodes: int, edges: int, depth: int, yaml_output: bool
) -> tuple[int, int]:
    """Reserve copied containers, canonical fragments/text, and output together."""
    if yaml_output:
        output = _checked_add(
            _checked_product(wire_size, 8),
            _checked_product(_checked_product(nodes, depth), 2),
            4096,
        )
        nodes_workspace = _checked_product(nodes, _YAML_NODE_BYTES)
    else:
        output = wire_size
        nodes_workspace = 0
    workspace = _checked_add(
        _checked_product(nodes, _JSON_CONTAINER_BYTES),
        _checked_product(edges, _JSON_CONTAINER_ENTRY_BYTES),
        _checked_product(nodes, _JSON_SERIALIZER_FRAME_BYTES),
        _checked_product(_checked_add(wire_size, nodes), 32),
        _checked_product(wire_size, 4),
        nodes_workspace,
        output,
    )
    return workspace, output


class _EncodedBytes:
    """An output buffer with its live reservation retained through descriptor write."""

    def __init__(self, data: bytes, hold: Reservation, budget: OperationBudget) -> None:
        self.data = data
        self._hold = hold
        self._budget = budget

    def release(self) -> None:
        self.data = b""
        self._budget.release(self._hold.counter, self._hold.amount)


def _decode_yaml(data: bytes, *, budget: OperationBudget) -> dict[str, t.Any]:
    retained_capacity, temporary_capacity = _decode_capacities(
        len(data), yaml_nodes=True
    )
    retained = _reserve(
        budget,
        "max_live_bytes",
        retained_capacity,
        "snapshot.yaml_decode",
        "retained YAML manifest object and container capacity",
    )
    temporary = _reserve(
        budget,
        "max_live_bytes",
        temporary_capacity,
        "snapshot.yaml_decode",
        "YAML text, scanner, composer, and parser frame workspace",
    )
    completed = False
    try:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Malformed YAML manifest") from exc
        _yaml_token_check(text, budget=budget)
        _reserve(
            budget,
            "max_work",
            len(data),
            "snapshot.yaml_decode",
            "bounded YAML parse bytes",
        )
        try:
            value = yaml.load(text, Loader=_StrictYamlLoader)
        except yaml.YAMLError as exc:
            raise ValueError("Malformed YAML manifest") from exc
        if not isinstance(value, dict):
            raise ValueError("Snapshot manifest must be an object")
        _bounded_json_size(value, budget, phase="snapshot.yaml_decode")
        completed = True
        return value
    finally:
        budget.release(temporary.counter, temporary.amount)
        if not completed:
            budget.release(retained.counter, retained.amount)


def _decode_container(
    data: bytes, *, budget: OperationBudget, name: str
) -> dict[str, t.Any]:
    retained_capacity, temporary_capacity = _decode_capacities(
        len(data), yaml_nodes=False
    )
    retained = _reserve(
        budget,
        "max_live_bytes",
        retained_capacity,
        "snapshot.json_decode",
        "retained JSON container object and slot capacity",
    )
    temporary = _reserve(
        budget,
        "max_live_bytes",
        temporary_capacity,
        "snapshot.json_decode",
        "JSON text, decoder frame, and parser workspace",
    )
    completed = False
    try:
        _reserve(
            budget,
            "max_work",
            len(data),
            "snapshot.json_decode",
            "bounded JSON parse bytes",
        )
        value = decode_json(data)
        if not isinstance(value, dict):
            raise ValueError(f"Snapshot {name} must be a JSON object")
        _bounded_json_size(value, budget, phase="snapshot.json_decode")
        completed = True
        return value
    finally:
        budget.release(temporary.counter, temporary.amount)
        if not completed:
            budget.release(retained.counter, retained.amount)


def _column(value: t.Any) -> tuple[str, str, bool]:
    if not isinstance(value, dict) or set(value) != _COLUMN_KEYS:
        raise ValueError("Invalid snapshot Column record")
    name, storage, nullable = value["name"], value["storage_type"], value["nullable"]
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(storage, str)
        or storage not in _STORAGE_TYPES
        or type(nullable) is not bool
    ):
        raise ValueError("Invalid snapshot Column values")
    return name, storage, nullable


def _file_entries(
    manifest: dict[str, t.Any],
) -> dict[str, tuple[tuple[tuple[str, str, bool], ...], int | None, str]]:
    if set(manifest) - {"annotations"} != _MANIFEST_FIELDS:
        raise ValueError("Native manifest fields are not exact")
    entries = manifest["files"]
    if not isinstance(entries, list) or len(entries) != len(_DATA_FILES):
        raise ValueError("Native manifest must contain exactly ten file entries")
    result: dict[str, tuple[tuple[tuple[str, str, bool], ...], int | None, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _FILE_ENTRY_KEYS:
            raise ValueError("Invalid snapshot FileEntry")
        path, fmt, version, digest, raw_columns, rows = (
            entry[key]
            for key in (
                "path",
                "format",
                "schema_version",
                "sha256",
                "columns",
                "row_count",
            )
        )
        if not isinstance(path, str) or path not in _DATA_FILES or path in result:
            raise ValueError("Duplicate or invalid snapshot FileEntry path")
        if (
            type(version) is not int
            or version != 1
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("Invalid snapshot FileEntry version or checksum")
        if path in _PARQUET_FILES:
            if (
                fmt != "parquet"
                or type(rows) is not int
                or rows < 0
                or not isinstance(raw_columns, list)
                or not raw_columns
            ):
                raise ValueError("Invalid Parquet FileEntry")
            columns = tuple(_column(item) for item in raw_columns)
            if len({item[0] for item in columns}) != len(columns):
                raise ValueError("Duplicate snapshot parquet column")
        else:
            if fmt != "json" or raw_columns is not None or rows is not None:
                raise ValueError("Invalid JSON FileEntry")
            columns = ()
        result[path] = (columns, rows, digest)
    if set(result) != _DATA_FILES:
        raise ValueError("Native manifest has missing snapshot FileEntry paths")
    return result


def _arrow_type(storage: str) -> pa.DataType:
    return {
        "utf8": pa.string(),
        "bool": pa.bool_(),
        "int64": pa.int64(),
        "float64": pa.float64(),
        "date32": pa.date32(),
        "datetime_us_naive": pa.timestamp("us"),
        "datetime_us_utc": pa.timestamp("us", tz="UTC"),
    }[storage]


def _expected_arrow(columns: tuple[tuple[str, str, bool], ...]) -> pa.Schema:
    return pa.schema(
        [
            pa.field(name, _arrow_type(storage), nullable=nullable)
            for name, storage, nullable in columns
        ]
    )


def _physical_schema(
    parquet: pq.ParquetFile, columns: tuple[tuple[str, str, bool], ...]
) -> None:
    schema = parquet.schema
    if len(schema) != len(columns):
        raise ValueError("Parquet physical column count does not match manifest")
    expected = _expected_arrow(columns)
    if parquet.schema_arrow != expected:
        raise ValueError("Parquet Arrow schema/nullability does not match manifest")
    for index, (_name, storage, _nullable) in enumerate(columns):
        field = schema.column(index)
        physical = field.physical_type
        logical = str(field.logical_type).lower()
        required = {
            "utf8": "BYTE_ARRAY",
            "bool": "BOOLEAN",
            "int64": "INT64",
            "float64": "DOUBLE",
            "date32": "INT32",
            "datetime_us_naive": "INT64",
            "datetime_us_utc": "INT64",
        }[storage]
        if physical != required:
            raise ValueError("Parquet physical storage width does not match manifest")
        if storage == "utf8" and "string" not in logical:
            raise ValueError("Parquet UTF8 annotation is required")
        if storage == "date32" and "date" not in logical:
            raise ValueError("Parquet DATE annotation is required")
        if storage.startswith("datetime_us"):
            if "timestamp" not in logical or "microseconds" not in logical:
                raise ValueError("Parquet microsecond timestamp annotation is required")
            adjusted = "isadjustedtoutc=true" in logical.replace(" ", "")
            if adjusted != (storage == "datetime_us_utc"):
                raise ValueError(
                    "Parquet timestamp UTC annotation does not match manifest"
                )


def _read_parquet(
    data: bytes,
    *,
    columns: tuple[tuple[str, str, bool], ...] | None,
    row_count: int | None,
    budget: OperationBudget,
    name: str,
) -> Relation:
    footer_length = _parquet_footer_length(data, name=name)
    metadata_capacity = _reserve(
        budget,
        "max_live_bytes",
        _parquet_metadata_capacity(footer_length),
        "snapshot.parquet_footer",
        "Thrift footer metadata capacity",
    )
    retained: Reservation | None = None
    temporary: Reservation | None = None
    completed = False
    try:
        source = pa.BufferReader(data)
        parquet = pq.ParquetFile(
            source,
            thrift_string_size_limit=max(1, footer_length),
            thrift_container_size_limit=max(1, footer_length),
        )
        metadata = parquet.metadata
        _reserve(
            budget,
            "max_work",
            _checked_add(
                metadata.num_row_groups,
                len(parquet.schema_arrow),
                _checked_product(metadata.num_rows, max(1, len(parquet.schema_arrow))),
            ),
            "snapshot.parquet_footer",
            "Parquet metadata and decoded scalar slots",
        )
        if columns is not None:
            _physical_schema(parquet, columns)
            if metadata.num_rows != row_count:
                raise ValueError("Parquet row count does not match manifest")
        _reserve(
            budget,
            "max_work",
            _checked_product(
                len(data),
                _checked_product(
                    max(1, metadata.num_row_groups), max(1, metadata.num_columns)
                ),
            ),
            "snapshot.parquet_preflight",
            "conservative compact-Thrift byte walk across all column chunks",
        )
        plan = _parquet_page_plan(parquet, data)
        _reserve(
            budget,
            "max_work",
            plan.page_count,
            "snapshot.parquet_preflight",
            "referenced compact-Thrift page headers",
        )
        _reserve(
            budget,
            "max_input_bytes",
            plan.decoded_bytes,
            "snapshot.parquet_decode",
            "Parquet decompressed page bytes",
        )
        native = _parquet_native_capacity(parquet, data=data, plan=plan)
        retained = _reserve(
            budget,
            "max_live_bytes",
            native,
            "snapshot.parquet_decode",
            "retained native relation capacity",
        )
        temporary = _reserve(
            budget,
            "max_live_bytes",
            _checked_add(len(data), native, native),
            "snapshot.parquet_decode",
            "input, Arrow decode, and Polars conversion buffers",
        )
        table = parquet.read()
        if table.num_rows != metadata.num_rows:
            raise ValueError("Parquet decode row count changed")
        if columns is not None and table.schema != _expected_arrow(columns):
            raise ValueError("Decoded Parquet schema does not match manifest")
        for field in table.schema:
            if pa.types.is_floating(field.type):
                for chunk in table[field.name].chunks:
                    for scalar in chunk:
                        value = scalar.as_py()
                        if value is not None and not math.isfinite(value):
                            raise ValueError("Parquet values contain nonfinite floats")
        import polars as pl  # allow: snapshot parquet relation bridge pending backend-agnostic file IO

        result = relation(pl.from_arrow(table))
        completed = True
        return result
    except (ExactResourceError, ValueError):
        raise
    except Exception as exc:
        raise ValueError(f"Malformed Parquet snapshot file {name}") from exc
    finally:
        if temporary is not None:
            budget.release(temporary.counter, temporary.amount)
        budget.release(metadata_capacity.counter, metadata_capacity.amount)
        if retained is not None and not completed:
            budget.release(retained.counter, retained.amount)


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("Short snapshot write")
        offset += written


def _write_bytes(dir_fd: int, name: str, data: bytes) -> None:
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=dir_fd,
    )
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_parquet(
    dir_fd: int,
    name: str,
    rel: Relation,
    columns: tuple[tuple[str, str, bool], ...],
    budget: OperationBudget,
) -> None:
    if not isinstance(rel, Relation):
        raise TypeError("Snapshot relations must be Mountainash Relation instances")
    if tuple(rel.columns) != tuple(item[0] for item in columns):
        raise ValueError(
            f"Relation columns for {name} do not match the declared native order"
        )
    rows = rel.count_rows()
    if type(rows) is not int or rows < 0:
        raise ValueError("Snapshot relation has an invalid row count")
    estimate = 4096
    for column, _storage, _nullable in columns:
        size, _ = native_column_bytes(
            rel, column, budget=budget, phase="snapshot.parquet_preflight"
        )
        estimate = _checked_add(estimate, size)
    row_groups = (
        _checked_add(rows, _PARQUET_ROW_GROUP_ROWS - 1) // _PARQUET_ROW_GROUP_ROWS
    )
    encoder_capacity = _checked_add(
        _PARQUET_ENCODER_FIXED_BYTES,
        _checked_product(_PARQUET_DATA_PAGE_BYTES, max(1, len(columns))),
        _checked_product(
            _PARQUET_WRITE_BATCH_ROWS, _checked_product(max(1, len(columns)), 16)
        ),
        _checked_product(row_groups, 4096),
    )
    output_capacity = _checked_add(estimate, estimate, encoder_capacity)
    output = _reserve(
        budget,
        "max_output_bytes",
        output_capacity,
        "snapshot.parquet_write",
        "reserved parquet output bytes",
    )
    hold = _reserve(
        budget,
        "max_live_bytes",
        _checked_add(estimate, estimate, estimate, encoder_capacity),
        "snapshot.parquet_write",
        "relation egress, Arrow cast, page, metadata, and parquet writer buffers",
    )
    fd = -1
    raw: t.BinaryIO | None = None
    sink: pa.PythonFile | None = None
    try:
        import polars as pl  # allow: snapshot parquet relation bridge pending backend-agnostic file IO

        frame = rel.to_polars()
        if not isinstance(frame, pl.DataFrame):
            raise ValueError("Snapshot relation cannot provide a Polars frame")
        source = frame.to_arrow()
        expected = _expected_arrow(columns)
        arrays = []
        for field in expected:
            array = source[field.name]
            if array.type == pa.large_string() and field.type == pa.string():
                try:
                    array = array.cast(pa.string(), safe=True)
                except pa.ArrowException as exc:
                    raise ValueError(
                        f"Relation storage type for {field.name} cannot be encoded as native UTF8"
                    ) from exc
            if array.type != field.type:
                raise ValueError(
                    f"Relation storage type for {field.name} does not match native schema"
                )
            if not field.nullable and array.null_count:
                raise ValueError(f"Required native field {field.name} contains null")
            if pa.types.is_floating(field.type):
                for chunk in array.chunks:
                    for scalar in chunk:
                        value = scalar.as_py()
                        if value is not None and not math.isfinite(value):
                            raise ValueError(
                                f"Native float field {field.name} contains a nonfinite value"
                            )
            arrays.append(array)
        table = pa.Table.from_arrays(arrays, schema=expected)
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=dir_fd,
        )
        raw = os.fdopen(fd, "wb", closefd=False)
        bounded = _BoundedParquetSink(raw, capacity=output.amount, budget=budget)
        sink = pa.PythonFile(bounded, mode="w")
        try:
            pq.write_table(
                table,
                sink,
                compression="NONE",
                use_dictionary=False,
                write_statistics=False,
                data_page_size=_PARQUET_DATA_PAGE_BYTES,
                write_batch_size=_PARQUET_WRITE_BATCH_ROWS,
                row_group_size=_PARQUET_ROW_GROUP_ROWS,
            )
        finally:
            try:
                sink.close()
            finally:
                sink = None
                raw.close()
                raw = None
        os.fsync(fd)
        if os.fstat(fd).st_size != bounded.tell():
            raise OSError("Snapshot parquet writer reported an invalid output size")
    finally:
        try:
            if sink is not None:
                sink.close()
        finally:
            try:
                if raw is not None:
                    raw.close()
            finally:
                if fd >= 0:
                    os.close(fd)
                budget.release(hold.counter, hold.amount)


def _file_digest(dir_fd: int, name: str, *, budget: OperationBudget) -> tuple[str, int]:
    data = _read_child(dir_fd, name, budget=budget, phase="snapshot.file_digest")
    return hashlib.sha256(data).hexdigest(), len(data)


def _file_entry(
    name: str,
    *,
    columns: tuple[tuple[str, str, bool], ...] | None,
    row_count: int | None,
    dir_fd: int,
    budget: OperationBudget,
) -> dict[str, t.Any]:
    digest, _ = _file_digest(dir_fd, name, budget=budget)
    return {
        "path": name,
        "format": "parquet" if name in _PARQUET_FILES else "json",
        "schema_version": 1,
        "sha256": digest,
        "columns": [
            {"name": column, "storage_type": storage, "nullable": nullable}
            for column, storage, nullable in columns
        ]
        if columns is not None
        else None,
        "row_count": row_count,
    }


def _encoded_json(
    value: dict[str, t.Any], *, budget: OperationBudget, phase: str
) -> _EncodedBytes:
    wire_size = _bounded_json_size(
        value, budget, phase=phase, counter="max_output_bytes"
    )
    nodes, edges, depth = _serialization_shape(
        value, wire_size=wire_size, budget=budget, phase=phase
    )
    workspace, output = _serialization_capacity(
        wire_size=wire_size, nodes=nodes, edges=edges, depth=depth, yaml_output=False
    )
    budget.reserve(
        "max_output_bytes", output, phase=phase, units="reserved canonical JSON bytes"
    )
    hold = _reserve(
        budget,
        "max_live_bytes",
        workspace,
        phase,
        "JSON copy, canonical parts, and output buffer",
    )
    try:
        materialized = _materialize_json(value)
        data = _transport_bytes(materialized)
        if len(data) > output:
            raise ValueError("JSON serialization exceeded its admitted capacity")
        return _EncodedBytes(data, hold, budget)
    except BaseException:
        budget.release(hold.counter, hold.amount)
        raise


def _encoded_yaml(value: dict[str, t.Any], *, budget: OperationBudget) -> _EncodedBytes:
    phase = "snapshot.manifest_encode"
    wire_size = _bounded_json_size(
        value, budget, phase=phase, counter="max_output_bytes"
    )
    nodes, edges, depth = _serialization_shape(
        value, wire_size=wire_size, budget=budget, phase=phase
    )
    workspace, output = _serialization_capacity(
        wire_size=wire_size, nodes=nodes, edges=edges, depth=depth, yaml_output=True
    )
    budget.reserve(
        "max_output_bytes", output, phase=phase, units="reserved YAML output bytes"
    )
    hold = _reserve(
        budget,
        "max_live_bytes",
        workspace,
        phase,
        "YAML copy, nodes, text, and output buffer",
    )
    try:
        materialized = _materialize_json(value)
        # Canonical JSON transport preflights Unicode, finite numbers, and cycles.
        transport = _transport_bytes(materialized)
        del transport
        text = yaml.dump(
            materialized,
            Dumper=_NoAliasDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
        data = text.encode("utf-8")
        if len(data) > output:
            raise ValueError("YAML serialization exceeded its admitted capacity")
        return _EncodedBytes(data, hold, budget)
    except BaseException:
        budget.release(hold.counter, hold.amount)
        raise


def _remove_owned_staging(parent_fd: int, name: str) -> None:
    """Remove only the fixed children of a private, descriptor-opened staging dir."""
    try:
        staging_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return
    try:
        for child in (*_DATA_FILES, _MANIFEST):
            try:
                os.unlink(child, dir_fd=staging_fd)
            except FileNotFoundError:
                continue
    finally:
        os.close(staging_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _publish_noreplace(parent_fd: int, staging: str, target: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(
            errno.ENOSYS, "atomic no-replace directory publication is unavailable"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(parent_fd, staging.encode(), parent_fd, target.encode(), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _legacy_manifest(manifest: dict[str, t.Any]) -> bool:
    return "native_schema_version" not in manifest


def read_snapshot(
    dir_path: str | pathlib.Path, *, budget: OperationBudget
) -> tuple[dict[str, t.Any], dict[str, Relation], dict[str, dict[str, t.Any]]]:
    """Authenticate fixed snapshot bytes and return undecoded semantic containers."""
    if not isinstance(budget, OperationBudget):
        raise TypeError("budget must be OperationBudget")
    root_fd = _open_directory(dir_path)
    try:
        try:
            raw_manifest = _read_child(
                root_fd, _MANIFEST, budget=budget, phase="snapshot.manifest_read"
            )
        except FileNotFoundError:
            raise FileNotFoundError(f"No manifest.yaml in {dir_path}") from None
        manifest_input = _reserve(
            budget,
            "max_live_bytes",
            len(raw_manifest),
            "snapshot.manifest_read",
            "retained manifest input bytes",
        )
        try:
            manifest = _decode_yaml(raw_manifest, budget=budget)
        finally:
            del raw_manifest
            budget.release(manifest_input.counter, manifest_input.amount)
        if _legacy_manifest(manifest):
            data = _read_child(
                root_fd,
                "lattice.parquet",
                budget=budget,
                phase="snapshot.legacy_lattice",
            )
            data_hold = _reserve(
                budget,
                "max_live_bytes",
                len(data),
                "snapshot.legacy_lattice",
                "retained parquet input bytes",
            )
            try:
                lattice = _read_parquet(
                    data,
                    columns=None,
                    row_count=None,
                    budget=budget,
                    name="lattice.parquet",
                )
            finally:
                del data
                budget.release(data_hold.counter, data_hold.amount)
            return manifest, {"lattice.parquet": lattice}, {}
        entries = _file_entries(manifest)
        relations: dict[str, Relation] = {}
        containers: dict[str, dict[str, t.Any]] = {}
        for name in sorted(_DATA_FILES):
            data = _read_child(root_fd, name, budget=budget, phase="snapshot.data_read")
            data_hold = _reserve(
                budget,
                "max_live_bytes",
                len(data),
                "snapshot.data_read",
                "retained snapshot input bytes",
            )
            try:
                columns, rows, digest = entries[name]
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"Snapshot checksum mismatch for {name}")
                if name in _PARQUET_FILES:
                    relations[name] = _read_parquet(
                        data, columns=columns, row_count=rows, budget=budget, name=name
                    )
                else:
                    containers[name.removesuffix(".json")] = _decode_container(
                        data, budget=budget, name=name
                    )
            finally:
                del data
                budget.release(data_hold.counter, data_hold.amount)
        return manifest, relations, containers
    finally:
        os.close(root_fd)


def write_snapshot(
    dir_path: str | pathlib.Path,
    *,
    manifest: dict[str, t.Any],
    relations: t.Mapping[str, Relation],
    columns: t.Mapping[str, tuple[tuple[str, str, bool], ...]],
    containers: t.Mapping[str, dict[str, t.Any]],
    budget: OperationBudget,
) -> pathlib.Path:
    """Write a complete native or bounded legacy snapshot through owned staging."""
    if not isinstance(budget, OperationBudget):
        raise TypeError("budget must be OperationBudget")
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a dict")
    parent_fd, target = _split_destination(dir_path)
    staging = ""
    try:
        try:
            os.stat(target, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Snapshot destination already exists")
        staging_path = tempfile.mkdtemp(
            prefix=f".{target}.staging-", dir=f"/proc/self/fd/{parent_fd}"
        )
        staging = pathlib.PurePath(staging_path).name
        staging_fd = os.open(
            staging,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            if _legacy_manifest(manifest):
                if (
                    set(manifest) - {"annotations"} != _LEGACY_FIELDS
                    or set(relations) != {"lattice.parquet"}
                    or set(columns) != {"lattice.parquet"}
                    or containers
                ):
                    raise ValueError(
                        "Legacy write requires only manifest, lattice relation, and lattice columns"
                    )
                legacy_columns = tuple(columns["lattice.parquet"])
                _write_parquet(
                    staging_fd,
                    "lattice.parquet",
                    relations["lattice.parquet"],
                    legacy_columns,
                    budget,
                )
                encoded_manifest = _encoded_yaml(manifest, budget=budget)
                try:
                    _write_bytes(staging_fd, _MANIFEST, encoded_manifest.data)
                finally:
                    encoded_manifest.release()
            else:
                if "files" in manifest or set(manifest) - {"annotations"} != (
                    _MANIFEST_FIELDS - {"files"}
                ):
                    raise ValueError(
                        "Native writer requires complete manifest fields except files"
                    )
                if (
                    set(relations) != set(_PARQUET_FILES)
                    or set(columns) != set(_PARQUET_FILES)
                    or set(containers) != {"predicates", "validation"}
                ):
                    raise ValueError(
                        "Native writer requires the fixed eight relations and two containers"
                    )
                normalized_columns: dict[str, tuple[tuple[str, str, bool], ...]] = {}
                row_counts: dict[str, int] = {}
                for name in _PARQUET_FILES:
                    schema = tuple(columns[name])
                    if not schema or any(
                        not isinstance(item, tuple) or len(item) != 3 for item in schema
                    ):
                        raise ValueError(
                            "Native parquet columns must be nonempty triples"
                        )
                    normalized = tuple(
                        _column(
                            {
                                "name": item[0],
                                "storage_type": item[1],
                                "nullable": item[2],
                            }
                        )
                        for item in schema
                    )
                    normalized_columns[name] = normalized
                    row_counts[name] = relations[name].count_rows()
                    _write_parquet(
                        staging_fd, name, relations[name], normalized, budget
                    )
                for key, name in (
                    ("predicates", "predicates.json"),
                    ("validation", "validation.json"),
                ):
                    if not isinstance(containers[key], t.Mapping):
                        raise TypeError(f"{key} container must be a mapping")
                    encoded = _encoded_json(
                        containers[key], budget=budget, phase=f"snapshot.{key}_encode"
                    )
                    try:
                        _write_bytes(staging_fd, name, encoded.data)
                    finally:
                        encoded.release()
                completed = dict(manifest)
                completed["files"] = [
                    _file_entry(
                        name,
                        columns=normalized_columns[name],
                        row_count=row_counts[name],
                        dir_fd=staging_fd,
                        budget=budget,
                    )
                    if name in _PARQUET_FILES
                    else _file_entry(
                        name,
                        columns=None,
                        row_count=None,
                        dir_fd=staging_fd,
                        budget=budget,
                    )
                    for name in sorted(_DATA_FILES)
                ]
                _file_entries(completed)
                encoded_manifest = _encoded_yaml(completed, budget=budget)
                try:
                    _write_bytes(staging_fd, _MANIFEST, encoded_manifest.data)
                finally:
                    encoded_manifest.release()
            os.fsync(staging_fd)
        finally:
            os.close(staging_fd)
        _publish_noreplace(parent_fd, staging, target)
        staging = ""
        os.fsync(parent_fd)
        return pathlib.Path(dir_path)
    finally:
        if staging:
            _remove_owned_staging(parent_fd, staging)
        os.close(parent_fd)
