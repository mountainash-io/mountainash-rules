"""Observe driver boundaries: construction is metadata-only, fetched views local."""

import re
import duckdb

import mountainash as ma
import pytest

from tests.accumulate.test_retrieval import range_lattice


class DuckDBTrace:
    """Wrap execution and returned cursors, counting calls rather than lookup."""

    def __init__(self, connection):
        self.connection = connection
        self.events = []

    def __getattr__(self, name):
        native = getattr(self.connection, name)
        if name in ("register", "append", "executemany"):
            def transfer(*args, **kwargs):
                self.events.append(("transfer", name))
                return native(*args, **kwargs)
            return transfer
        if name in ("execute", "sql", "query"):
            def statement(*args, **kwargs):
                sql = str(args[0] if args else kwargs.get("query", ""))
                kind = "metadata" if re.search(
                    r"^\s*(DESCRIBE|SHOW|PRAGMA)\b|information_schema\.|pg_catalog\.|"
                    r"^\s*SELECT\s+(current_database\(\)|current_schema\(\)|version\(\))",
                    sql, re.IGNORECASE,
                ) else "data"
                result = native(*args, **kwargs)
                # DuckDB's relational API binds SELECT without reading rows;
                # cursor fetch/execute is the execution boundary, not sql().
                if name != "execute" and isinstance(result, duckdb.DuckDBPyRelation):
                    kind = "binding" if kind == "data" else kind
                self.events.append((kind, sql))
                return _CursorTrace(self, result, kind, sql)
            return statement
        return native


class _CursorTrace:
    def __init__(self, trace, cursor, kind, sql):
        self.trace, self.cursor, self.kind, self.sql = trace, cursor, kind, sql

    def __getattr__(self, name):
        native = getattr(self.cursor, name)
        if callable(native) and name in ("execute", "create", "insert_into", "write_csv", "write_parquet"):
            def execute(*args, **kwargs):
                self.trace.events.append(("data", self.sql))
                self.kind = "data"
                result = native(*args, **kwargs)
                return self if result is self.cursor else result
            return execute
        if callable(native) and name.startswith(("fetch", "arrow", "to_arrow", "df", "pl", "record_batch")):
            def fetch(*args, **kwargs):
                if self.kind == "binding":
                    self.trace.events.append(("data", self.sql))
                    self.kind = "data"
                self.trace.events.append(("fetch:" + self.kind, name))
                return native(*args, **kwargs)
            return fetch
        return native




def test_duckdb_stored_resolve_is_deferred_and_combines_matches_in_one_fetch():
    from tests.accumulate.test_resolve import controlled_metadata
    from tests.conftest import build_backend_df
    from mountainash_rules.engines.accumulate.build import build

    lattice = build(ma.relation(build_backend_df("ibis-duckdb", {
        "uuid": ["E", "U"], "region": ["EU", "US"], "amount": [10, 20],
    }, "resolve_execution_rules")), controlled_metadata())
    backend = lattice.cells.compile()._find_backend(use_default=False)
    trace = DuckDBTrace(backend.con)
    backend.con = trace
    try:
        operation = lattice.resolve([
            {"request_id": 31, "context": {"region": "EU"},
             "contract_id": "P", "profile_id": "decision"},
        ], include_contributors=True)
        assert all(kind in ("binding", "metadata", "fetch:metadata") for kind, _ in trace.events), trace.events
        trace.events.clear()
        result = operation.fetch(include_matches=True)
        data = [sql for kind, sql in trace.events if kind == "data"]
        assert len(data) == 1, trace.events
        assert sum(kind == "fetch:data" for kind, _ in trace.events) == 1, trace.events
        assert result.matches is not None
        assert result.status.select("request_id", "status").to_dicts() == [
            {"request_id": 31, "status": "resolved"},
        ]
        assert result.payload("default").to_dicts() == [{"request_id": 31, "v0001": 10}]
        assert result.matches.scope("default").select("request_id", "v0001").to_dicts() == [
            {"request_id": 31, "v0001": 10},
        ]
    finally:
        backend.con = trace.connection
def test_duckdb_retrieval_preserves_stored_placement_and_local_result_lifetime():
    lattice = range_lattice("ibis-duckdb")
    backend = lattice.cells.compile()._find_backend(use_default=False)
    trace = DuckDBTrace(backend.con)
    backend.con = trace
    try:
        operation = lattice.query([
            {"request_id": 1, "context": {"x": 7}},
            {"request_id": 2, "context": {"x": 16}},
        ], include_cell_key=True, include_contributors=True)
        assert all(kind in ("binding", "metadata", "fetch:metadata") for kind, _ in trace.events), trace.events
        trace.events.clear()
        result = operation.fetch()
        data = [sql for kind, sql in trace.events if kind == "data"]
        assert len(data) == 1, trace.events
        assert sum(kind == "fetch:data" for kind, _ in trace.events) == 1, trace.events
        assert re.match(r"\s*(SELECT|WITH)\b", data[0], re.IGNORECASE), data
        # Stored table references must remain in the terminal SQL, not a plan
        # containing only uploaded local matches.
        table_name = lattice.cells.compile().op().name
        assert table_name in data[0]
        del operation
        trace.events.clear()
        value = result.payload_columns["default"]["amount"]
        assert result.scope("default").select("request_id", value).sort("request_id").to_dicts() == [
            {"request_id": 1, value: 7},
        ]
        assert result.contributors.select("source_uuid").sort("source_uuid").to_dicts() == [
            {"source_uuid": "A"}, {"source_uuid": "B"},
        ]
        assert result.cell_identity_ordered_bounds["x"].select("lower_value", "upper_value").to_dicts() == [
            {"lower_value": 5, "upper_value": 10},
        ]
        assert trace.events == []
    finally:
        backend.con = trace.connection


@pytest.mark.parametrize("placement", ["local", "same_connection", "other_connection"])
def test_native_request_placement_defers_transfer_and_keeps_lattice_remote(placement):
    import ibis

    lattice = range_lattice("ibis-duckdb")
    stored_backend = lattice.cells.compile()._find_backend(use_default=False)
    request_backend = stored_backend if placement == "same_connection" else ibis.duckdb.connect()
    raw = ma.relation({"request_id": [9, 3], "x": [7, 16]})
    contexts = raw if placement == "local" else ma.relation(
        request_backend.create_table("native_retrieval_requests", raw.to_polars(), temp=True)
    )
    stored_trace = DuckDBTrace(stored_backend.con)
    request_trace = DuckDBTrace(request_backend.con) if request_backend is not stored_backend else stored_trace
    stored_backend.con = stored_trace
    request_backend.con = request_trace
    try:
        operation = lattice.query(contexts)
        for trace in (stored_trace, request_trace):
            assert all(kind in ("binding", "metadata", "fetch:metadata") for kind, _ in trace.events), trace.events
            trace.events.clear()
        result = operation.fetch()
        assert sum(kind == "fetch:data" for kind, _ in stored_trace.events) == 1, stored_trace.events
        stored_sql = [sql for kind, sql in stored_trace.events if kind == "data"]
        assert len(stored_sql) == 1, stored_trace.events
        assert lattice.cells.compile().op().name in stored_sql[0]
        if placement == "same_connection":
            assert not any(kind == "transfer" for kind, _ in stored_trace.events)
        assert result.scope("default").select("request_id", "v0001").to_dicts() == [
            {"request_id": 9, "v0001": 7},
        ]
    finally:
        stored_backend.con = stored_trace.connection
        request_backend.con = request_trace.connection
        if request_backend is not stored_backend:
            request_backend.disconnect()


def test_native_mask_channel_is_not_exported_for_local_control_composition():
    from mountainash_rules.engines.accumulate.build import build
    from tests.accumulate.test_resolve import controlled_metadata
    from tests.conftest import build_backend_df

    lattice = build(ma.relation(build_backend_df("ibis-duckdb", {
        "uuid": ["E", "U", "A"], "region": ["EU", "US", "APAC"], "amount": [1, 2, 3],
    }, "mask_placement_rules")), controlled_metadata())
    backend = lattice.cells.compile()._find_backend(use_default=False)
    masks = ma.relation(backend.create_table("query_masks", {
        "request_id": [1], "dimension_name": ["region"],
    }, temp=True))
    controls = ma.relation({
        "request_id": [1], "contract_id": ["P"], "profile_id": ["decision"],
    })
    trace = DuckDBTrace(backend.con)
    backend.con = trace
    try:
        operation = lattice.query([{"request_id": 1, "context": {"region": "EU"}}],
                                  request_control=controls, masks=masks)
        assert all(kind in ("binding", "metadata", "fetch:metadata") for kind, _ in trace.events), trace.events
        trace.events.clear()
        result = operation.fetch()
        assert sum(kind == "data" for kind, _ in trace.events) == 1, trace.events
        assert sum(kind == "fetch:data" for kind, _ in trace.events) == 1, trace.events
        del operation, controls, masks
        backend.drop_table("query_masks", force=True)
        trace.events.clear()
        assert result.scope("default").select("request_id", "v0001").sort("v0001").to_dicts() == [
            {"request_id": 1, "v0001": 1}, {"request_id": 1, "v0001": 2},
        ]
        assert trace.events == []
    finally:
        backend.con = trace.connection
