"""Native snapshot decoder and serializer resource-admission regressions."""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mountainash_rules import ExactResourceError
from mountainash_rules.core.contracts import OperationBudget
from mountainash_rules.engines.accumulator import snapshot_io
from tests.accumulator.source_analysis_fixtures import limits


def _parquet_bytes(values: list[int]) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(
        pa.Table.from_arrays(
            [pa.array(values, type=pa.int64())],
            schema=pa.schema([pa.field("value", pa.int64(), nullable=False)]),
        ),
        sink,
        compression="zstd",
        use_dictionary=True,
        data_page_version="1.0",
    )
    return sink.getvalue().to_pybytes()


def _budget(**overrides: int) -> OperationBudget:
    return OperationBudget(limits(**overrides), "snapshot-resource-test")


def _page_uncompressed_offset(data: bytes) -> tuple[int, int]:
    """Find the fixed-width i32 PageHeader uncompressed-size varint."""
    parquet = pq.ParquetFile(pa.BufferReader(data))
    offset = parquet.metadata.row_group(0).column(0).data_page_offset
    assert data[offset : offset + 3] == b"\x15\x00\x15"
    start = offset + 3
    end = start
    while data[end] & 0x80:
        end += 1
    return start, end + 1


def test_rejects_signed_invalid_page_size_before_native_decode():
    """A compact-Thrift page header cannot make PyArrow decode an invalid size."""
    data = bytearray(_parquet_bytes(list(range(32))))
    start, end = _page_uncompressed_offset(data)
    data[start:end] = b"\xff" * (end - start - 1) + b"\x7f"

    with pytest.raises(ValueError, match="page.*size"):
        snapshot_io._read_parquet(
            bytes(data),
            columns=(("value", "int64", False),),
            row_count=32,
            budget=_budget(),
            name="lattice.parquet",
        )


def test_dictionary_decode_capacity_is_reserved_before_arrow_read():
    """Compressed dictionary pages consume admitted decode workspace for INT64."""
    data = _parquet_bytes(list(range(4_096)))

    with pytest.raises(ExactResourceError) as failure:
        snapshot_io._read_parquet(
            data,
            columns=(("value", "int64", False),),
            row_count=4_096,
            budget=_budget(max_live_bytes=500_000),
            name="lattice.parquet",
        )

    assert failure.value.phase == "snapshot.parquet_decode"
    assert failure.value.counter == "max_live_bytes"
    loaded = snapshot_io._read_parquet(
        data,
        columns=(("value", "int64", False),),
        row_count=4_096,
        budget=_budget(max_live_bytes=1_000_000),
        name="lattice.parquet",
    )
    assert loaded.to_dicts() == [{"value": value} for value in range(4_096)]


def test_wide_annotation_serialization_reserves_container_workspace_before_copy():
    """Wide immutable evidence cannot copy its container graph beyond the budget."""
    value = {"annotations": [{} for _ in range(128)]}

    with pytest.raises(ExactResourceError) as failure:
        snapshot_io._encoded_json(
            value,
            budget=_budget(max_live_bytes=50_000),
            phase="snapshot.predicates_encode",
        )

    assert failure.value.phase == "snapshot.predicates_encode"
    assert failure.value.counter == "max_live_bytes"


def test_wide_manifest_serialization_reserves_yaml_nodes_before_copy():
    """YAML node construction cannot escape the same wide-container admission."""
    value = {"annotations": [{} for _ in range(128)]}

    with pytest.raises(ExactResourceError) as failure:
        snapshot_io._encoded_yaml(
            value,
            budget=_budget(max_live_bytes=50_000),
        )

    assert failure.value.phase == "snapshot.manifest_encode"
    assert failure.value.counter == "max_live_bytes"


def test_deep_json_parser_frames_are_admitted_before_iterative_decode():
    """Nested JSON frames exhaust the snapshot budget before the decoder runs."""
    data = b'{"x":' + b"[" * 256 + b"0" + b"]" * 256 + b"}"

    with pytest.raises(ExactResourceError) as failure:
        snapshot_io._decode_container(
            data,
            budget=_budget(max_live_bytes=100_000),
            name="predicates.json",
        )

    assert failure.value.phase == "snapshot.json_decode"
    assert failure.value.counter == "max_live_bytes"
