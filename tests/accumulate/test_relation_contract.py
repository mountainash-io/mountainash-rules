"""Installed dependency contracts needed by native accumulator operations."""

from contextlib import nullcontext

import duckdb
import ibis
import mountainash as ma
import polars as pl
import pytest

from mountainash.core.types import BackendCapabilityError
from mountainash.relations.core.errors import MaterializationScopeClosedError
from tests.conftest import build_backend_df


def test_join_keys_and_absence_detection(backend_name, records):
    # Separate factories deliberately create independent Ibis connections.
    left = ma.relation(build_backend_df(backend_name, {"id": [1, 2]}, "left_keys"))
    right = ma.relation(
        build_backend_df(backend_name, {"id": [1], "value": [7]}, "right_keys")
    )
    merged = left.join(right, on="id", how="left")
    assert merged.schema == {**left.schema, "value": right.schema["value"]}
    assert records(merged.sort("id")) == [
        {"id": 1, "value": 7},
        {"id": 2, "value": None},
    ]
    renamed = right.select(ma.col("id").alias("right_id"), "value")
    retained = left.join(renamed, left_on="id", right_on="right_id", how="left")
    assert retained.schema == {**left.schema, **renamed.schema}
    assert records(retained.sort("id")) == [
        {"id": 1, "right_id": 1, "value": 7},
        {"id": 2, "right_id": None, "value": None},
    ]
    coalesced = left.join(
        renamed, left_on="id", right_on="right_id", how="left", coalesce=True
    )
    assert records(coalesced.sort("id")) == [
        {"id": 1, "value": 7},
        {"id": 2, "value": None},
    ]
    inner = left.join(
        renamed, left_on="id", right_on="right_id", how="inner", coalesce=True
    )
    assert inner.schema == coalesced.schema == merged.schema
    assert records(inner) == [{"id": 1, "value": 7}]


def test_self_semi_and_anti_preserve_row_membership(backend_name, records):
    source = ma.relation(
        build_backend_df(backend_name, {"id": [1, 2, 2], "value": [9, 8, 8]})
    )
    assert records(source.join(source, on="id", how="semi").sort("id")) == [
        {"id": 1, "value": 9},
        {"id": 2, "value": 8},
        {"id": 2, "value": 8},
    ]
    assert records(source.join(source, on="id", how="anti")) == []
    selected = source.filter(ma.col("id") == 2)
    assert records(source.join(selected, on="id", how="anti")) == [
        {"id": 1, "value": 9}
    ]


def test_grouped_folds_concat_distinct_and_order(backend_name, records):
    source = ma.relation(
        build_backend_df(backend_name, {"key": [2, 1, 2], "amount": [3, 7, 5]})
    )
    folded = source.group_by("key").agg(ma.col("amount").sum().alias("total"))
    result = (
        ma.concat([folded, folded]).unique().sort("key").with_row_index(name="position")
    )
    assert records(result.select("position", "key", "total")) == [
        {"position": 0, "key": 1, "total": 7},
        {"position": 1, "key": 2, "total": 8},
    ]


@pytest.mark.parametrize("fail", [False, True])
def test_scope_releases_tables_and_expires_relations(fail, records):
    connection = ibis.duckdb.connect()
    try:
        source = ma.relation(connection.create_table("source", {"id": [1]}))
        before = set(connection.list_tables())
        with pytest.raises(RuntimeError) if fail else nullcontext():
            with ma.materialization_scope() as scope:
                owned = scope.snapshot(source)
                assert records(owned) == [{"id": 1}]
                if fail:
                    raise RuntimeError("consumer failed")
        assert set(connection.list_tables()) == before
        with pytest.raises(MaterializationScopeClosedError):
            records(owned)
    finally:
        connection.disconnect()


def test_four_table_capture_survives_source_deletion(backend_name, records):
    data = [
        {"source_id": [1], "amount": [7]},
        {"cell_id": [2], "lower": [0], "upper": [10]},
        {"cell_id": [2], "source_id": [1]},
        {"cell_id": [2], "total": [7]},
    ]
    connection = ibis.duckdb.connect() if backend_name == "ibis-duckdb" else None
    try:
        native = [
            connection.create_table(f"input_{i}", values)
            if connection is not None
            else build_backend_df(backend_name, values)
            for i, values in enumerate(data)
        ]
        with ma.materialization_scope() as scope:
            captured = scope.capture(*(ma.relation(frame) for frame in native))
            kept = [scope.detach(table) for table in captured]
        if connection is not None:
            for i in range(4):
                connection.drop_table(f"input_{i}")
        else:
            native.clear()
        assert [records(table) for table in kept] == [
            [{"source_id": 1, "amount": 7}],
            [{"cell_id": 2, "lower": 0, "upper": 10}],
            [{"cell_id": 2, "source_id": 1}],
            [{"cell_id": 2, "total": 7}],
        ]
    finally:
        if connection is not None:
            connection.disconnect()


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("completion, expected", [("COMMIT", 2), ("ROLLBACK", 1)])
def test_snapshot_refusal_preserves_caller_transaction(
    records, batch, completion, expected
):
    connection = ibis.duckdb.connect()
    try:
        source = connection.create_table("source", pl.DataFrame({"id": [1]}))
        connection.raw_sql("BEGIN")
        connection.raw_sql("UPDATE source SET id = 2")

        before = set(connection.list_tables())
        with ma.materialization_scope() as scope:
            with pytest.raises(BackendCapabilityError):
                if batch:
                    scope.capture(ma.relation(source), ma.relation(source))
                else:
                    ma.relation(source).snapshot()
        assert set(connection.list_tables()) == before
        assert records(ma.relation(source)) == [{"id": 2}]
        connection.raw_sql(completion)
        assert records(ma.relation(source)) == [{"id": expected}]
    finally:
        connection.disconnect()


def test_failed_capture_preserves_prior_owned_state(records):
    connection = ibis.duckdb.connect()
    try:
        source = ma.relation(connection.create_table("source", {"id": [1]}))
        invalid = ma.relation(
            connection.create_table("invalid", {"text": ["not-an-integer"]})
        ).select(ma.col("text").cast("int64"))
        with ma.materialization_scope() as scope:
            prior = scope.snapshot(source)
            before = set(connection.list_tables())
            with pytest.raises(duckdb.ConversionException):
                scope.capture(source, invalid)
            assert set(connection.list_tables()) == before
            assert records(prior) == [{"id": 1}]
    finally:
        connection.disconnect()
