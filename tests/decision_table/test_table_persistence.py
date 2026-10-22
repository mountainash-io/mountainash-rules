"""Plan 2 Task 6: save/load through mountainash-files and transport, the manifest and verification.

Spec: ``2026-10-21-decision-table-persistence-design.md``.  The remote tests (Test 9) run only when a
writable scratch location is configured: set ``MOUNTAINASH_RULES_SCRATCH_PROFILE`` to a storage
profile name (resolved through ``MOUNTAINASH_PROFILES_CONFIG`` as usual) and
``MOUNTAINASH_RULES_SCRATCH_PATH`` to a writable directory on that storage, e.g. ``s3://bucket/tmp``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import typing as t
import uuid
from decimal import Decimal

import ibis
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mountainash_transport import resolve_storage, storage

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table import persistence
from mountainash_rules.decision_table.build import build_decision_table
from mountainash_rules.decision_table.persistence import (
    DATA_FILES,
    MANIFEST_NAME,
    file_name,
    verify_saved_table,
)
from mountainash_rules.decision_table.table import DecisionTable

from tests.decision_table.apply_support import context_model, fetch
from tests.decision_table.construction_support import validated_rules
from tests.decision_table.oracles.finite_completion import FiniteCompletionOracle, contexts_over
from tests.decision_table.test_build_equivalence import built
from tests.decision_table.test_ordered_and_categorical_fields import fees_rates_metadata, fees_rates_rules

M = MatchStrategy
PROFILES_ENV = "MOUNTAINASH_PROFILES_CONFIG"
SCRATCH_PROFILE_ENV = "MOUNTAINASH_RULES_SCRATCH_PROFILE"
SCRATCH_PATH_ENV = "MOUNTAINASH_RULES_SCRATCH_PATH"
CONSTRUCTION = ("entries", "entry_sources", "pattern_conditions")
METADATA = ("ruleset", "fields", "dimensions", "outputs", "truthy")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def connection_for(backend_name: str):
    return None if backend_name == "polars" else ibis.duckdb.connect()


def dimension(name: str, strategy: MatchStrategy, field: str, **kwargs) -> Dimension:
    return Dimension(dimension_name=name, context_field=field, match_strategy=strategy, **kwargs)


def every_kind(backend_name: str) -> DecisionTable:
    """A table with every column kind the entries can hold, and truthy values in its metadata."""
    fields = (
        ContextField(name="region", data_type=DataType.STR, wildcard="*", truthy={"yes": 1, "no": -1}),
        ContextField(name="tags", data_type=DataType.STR, wildcard=None, wildcard_declared=True),
        ContextField(name="amount", data_type=DataType.DECIMAL, precision=16, scale=2,
                     rounding="half_to_even", wildcard=None, wildcard_declared=True),
        ContextField(name="day", data_type=DataType.DATE, wildcard=None, wildcard_declared=True),
        ContextField(name="seen", data_type=DataType.DATETIME, datetime_unit="ms", timezone="utc",
                     wildcard=None, wildcard_declared=True),
        ContextField(name="at", data_type=DataType.DATETIME, datetime_unit="us", timezone="naive",
                     wildcard=None, wildcard_declared=True),
    )
    dimensions = (
        dimension("region_in", M.SET_MEMBERSHIP, "region"),
        dimension("region_out", M.SET_EXCLUSION, "region"),
        dimension("tags", M.SET_EXCLUSION, "tags"),
        dimension("amount", M.RANGE, "amount", range_min_field="amount_lo", range_max_field="amount_hi"),
        dimension("day", M.MIN, "day"),
        dimension("seen", M.MIN, "seen"),
        dimension("at", M.MAX, "at"),
        dimension("pre", M.PREFIX, "region"),
        dimension("agree", M.TRUTHY, "region"),
    )
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=fields,
        dimensions=dimensions,
        outputs=(
            Aggregate(scope="s", output_name="fee", contribution_column="fee", data_type=DataType.DECIMAL,
                      precision=16, scale=2, rounding="half_to_even"),
            Aggregate(scope="s", output_name="n", contribution_column="n", data_type=DataType.INT),
        ),
    )
    data = {
        "rule_id": ["a", "b", "c"],
        "scope": ["s", "s", "s"],
        "region_in": [["EU", "US"], ["*"], ["*"]],
        "region_out": [["*"], ["US"], ["*"]],
        "tags": pl.Series([[], None, ["x", "y"]], dtype=pl.List(pl.String)),
        "amount_lo": pl.Series([Decimal("1.00"), None, None], dtype=pl.Decimal(16, 2)),
        "amount_hi": pl.Series([Decimal("9.99"), None, None], dtype=pl.Decimal(16, 2)),
        "day": pl.Series([dt.date(2024, 1, 2), None, None], dtype=pl.Date),
        "seen": pl.Series(
            [dt.datetime(2024, 5, 6, 7, 8, 9, 123000, tzinfo=dt.timezone.utc), None, None],
            dtype=pl.Datetime("ms", "UTC"),
        ),
        "at": pl.Series([dt.datetime(2024, 1, 2, 3, 4, 5, 123456), None, None], dtype=pl.Datetime("us")),
        "pre": ["EU", "*", "*"],
        "agree": ["yes", "*", "*"],
        "fee": pl.Series([Decimal("1.50"), Decimal("2.25"), Decimal("0.10")], dtype=pl.Decimal(16, 2)),
        "n": [1, 2, 3],
    }
    return build_decision_table(validated_rules(backend_name, data, metadata))


def empty_table(backend_name: str) -> DecisionTable:
    """The fees/rates rules with no rules: a table with no entries and its typed schema."""
    data = fees_rates_rules()
    schema = pl.DataFrame(data).schema
    empty = {name: pl.Series(name, [], dtype=dtype) for name, dtype in schema.items()}
    return build_decision_table(validated_rules(backend_name, pl.DataFrame(empty), fees_rates_metadata()))


def arrow(table: DecisionTable, name: str) -> pa.Table:
    relations = {
        "entries": table.entries,
        "entry_sources": table.entry_sources,
        "pattern_conditions": table.pattern_conditions,
        **table.source_metadata.to_tables(),
    }
    return relations[name].to_pyarrow()


def logical(data_type: pa.DataType) -> str:
    """``large_string``/``large_list`` are the same logical type as ``string``/``list``."""
    return str(data_type).replace("large_", "")


def instant(value: t.Any) -> t.Any:
    """Datetimes compare by instant (naive values as UTC); containers by element."""
    if isinstance(value, dt.datetime):
        return (value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)).timestamp()
    if isinstance(value, list):
        return [instant(item) for item in value]
    return value


def comparable(table: pa.Table) -> tuple[dict[str, str], list[dict]]:
    return (
        {field.name: logical(field.type) for field in table.schema},
        [{key: instant(value) for key, value in row.items()} for row in table.to_pylist()],
    )


def assert_same_table(saved: DecisionTable, loaded: DecisionTable) -> None:
    for name in (*CONSTRUCTION, *METADATA):
        before, after = arrow(saved, name), arrow(loaded, name)
        assert comparable(before)[0] == comparable(after)[0], f"{name}: logical types"
        assert sorted(map(repr, comparable(before)[1])) == sorted(map(repr, comparable(after)[1])), name
    assert loaded.build_id == saved.build_id
    assert loaded.source_metadata.model_dump() == saved.source_metadata.model_dump()
    assert loaded.entry_metadata.model_dump() == saved.entry_metadata.model_dump()


def manifest_of(path: str) -> dict:
    with open(os.path.join(path, MANIFEST_NAME), encoding="utf-8") as handle:
        return json.load(handle)


def write_profiles(tmp_path, monkeypatch, name: str = "scratch") -> str:
    """A configuration naming one local profile; returns the profile name."""
    config = tmp_path / "profiles.yaml"
    config.write_text(
        f"storage_profiles:\n  {name}:\n    provider: local\n    parameters:\n      ROOT_PATH: null\n"
    )
    monkeypatch.setenv(PROFILES_ENV, str(config))
    return name


@pytest.fixture
def target(tmp_path):
    return str(tmp_path / "saved")


# ---------------------------------------------------------------------------
# 1. Round trip of every column kind and all five metadata tables
# ---------------------------------------------------------------------------


def test_every_column_kind_and_metadata_table_round_trips(backend_name, target):
    table = every_kind(backend_name)
    schema = {field.name: logical(field.type) for field in arrow(table, "entries").schema}
    assert {
        "decimal128(16, 2)", "date32[day]", "timestamp[us, tz=UTC]", "timestamp[us]", "string", "int8",
        "list<item: string>", "int64",
    } <= set(schema.values())
    tags = [row["tags"] for row in arrow(table, "entries").to_pylist()]
    assert [] in tags and None in tags and any(tags)  # empty list, declared null wildcard, a real list
    assert any(row["region_out"] for row in arrow(table, "entries").to_pylist())
    assert arrow(table, "truthy").num_rows == 2

    table.save(target)
    loaded = DecisionTable.load(target, connection=connection_for(backend_name))

    assert_same_table(table, loaded)
    assert isinstance(loaded, DecisionTable)


def test_a_table_loaded_from_polars_or_ibis_equals_one_built_on_either(backend_name, target):
    table = every_kind(backend_name)
    table.save(target)
    other = "ibis-duckdb" if backend_name == "polars" else "polars"

    loaded = DecisionTable.load(target, connection=connection_for(other))

    assert_same_table(table, loaded)


# ---------------------------------------------------------------------------
# 2. Where the loaded relations live
# ---------------------------------------------------------------------------


def test_ibis_load_compiles_each_construction_relation_to_the_supplied_connection(target):
    table = every_kind("ibis-duckdb")
    table.save(target)
    connection = ibis.duckdb.connect()

    loaded = DecisionTable.load(target, connection=connection)

    for relation in (loaded.entries, loaded.entry_sources, loaded.pattern_conditions):
        assert relation.compile().get_backend() is connection


def test_load_without_a_connection_is_in_process_polars(target):
    table = every_kind("polars")
    table.save(target)

    loaded = DecisionTable.load(target)

    for relation in (loaded.entries, loaded.entry_sources, loaded.pattern_conditions):
        assert isinstance(relation.compile(), (pl.DataFrame, pl.LazyFrame))


# ---------------------------------------------------------------------------
# 3. save -> load -> match equals match on the built table
# ---------------------------------------------------------------------------

PARITY = ("categorical", "categorical_negation", "ordered", "patterns")


def match_rows(table: DecisionTable, grid: dict) -> list[tuple]:
    kinds = {
        name: type(next(value for value in values if value is not None)) for name, values in grid.items()
    }
    model = context_model(**kinds)
    contexts = [model(request_id=index, **context) for index, context in enumerate(contexts_over(grid))]
    rows = fetch(table, contexts)
    return sorted(
        (tuple(sorted((key, repr(value)) for key, value in row.items())) for row in rows)
    )


@pytest.mark.parametrize("name", PARITY)
def test_matching_a_loaded_table_equals_matching_the_built_table(backend_name, target, name):
    validated, table, grid, exact_grid = built(backend_name, name)
    table.save(target)

    loaded = DecisionTable.load(target, connection=connection_for(backend_name))

    assert match_rows(loaded, grid) == match_rows(table, grid)
    assert FiniteCompletionOracle(validated, grid, exact_grid).check_equivalence(loaded) > 0


# ---------------------------------------------------------------------------
# 4. A zero-entry table
# ---------------------------------------------------------------------------


def test_a_zero_entry_table_round_trips_with_its_typed_schema(backend_name, target):
    table = empty_table(backend_name)
    assert arrow(table, "entries").num_rows == 0

    table.save(target)
    loaded = DecisionTable.load(target, connection=connection_for(backend_name))

    assert arrow(loaded, "entries").num_rows == 0
    assert comparable(arrow(loaded, "entries"))[0] == comparable(arrow(table, "entries"))[0]
    assert_same_table(table, loaded)
    listed = {entry["name"]: entry for entry in manifest_of(target)["files"]}
    for name in ("entries", "entry_sources", "pattern_conditions"):
        assert pq.read_table(os.path.join(target, file_name(name))).num_rows == 0
        assert listed[file_name(name)]["size"] > 0
    assert verify_saved_table(target).ok


# ---------------------------------------------------------------------------
# 5. Replace
# ---------------------------------------------------------------------------


def test_saving_again_leaves_nothing_from_the_earlier_save(backend_name, target):
    first = every_kind(backend_name)
    first.save(target)
    assert arrow(DecisionTable.load(target), "entries").num_rows > 0

    second = empty_table(backend_name)
    second.save(target)
    loaded = DecisionTable.load(target, connection=connection_for(backend_name))

    for name in CONSTRUCTION:
        assert arrow(loaded, name).num_rows == 0, name
    assert comparable(arrow(loaded, "entries"))[0] == comparable(arrow(second, "entries"))[0]
    assert loaded.build_id == second.build_id != first.build_id
    assert loaded.source_metadata.model_dump() == second.source_metadata.model_dump()
    assert verify_saved_table(target).ok


# ---------------------------------------------------------------------------
# 6. The manifest
# ---------------------------------------------------------------------------


def test_manifest_records_build_id_and_every_file_name_size_and_hash(backend_name, target):
    table = every_kind(backend_name)

    table.save(target)

    manifest = manifest_of(target)
    assert manifest["build_id"] == table.build_id
    assert [entry["name"] for entry in manifest["files"]] == [file_name(name) for name in DATA_FILES]
    assert len(manifest["files"]) == 8
    for entry in manifest["files"]:
        with open(os.path.join(target, entry["name"]), "rb") as handle:
            data = handle.read()
        assert entry["size"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert "build_id" not in arrow(table, "entries").column_names
    assert sorted(os.listdir(target)) == sorted([MANIFEST_NAME, *(file_name(name) for name in DATA_FILES)])


def test_the_manifest_is_written_last_and_every_file_is_written_independently(backend_name, target, monkeypatch):
    table = every_kind(backend_name)
    written: list[tuple[str, str]] = []
    real_write = persistence.write

    def recording_write(sink, data, **kwargs):
        written.append((os.path.basename(sink.path), sink.write_disposition))
        return real_write(sink, data, **kwargs)

    monkeypatch.setattr(persistence, "write", recording_write)

    table.save(target)

    assert [name for name, _ in written] == [*(file_name(name) for name in DATA_FILES), MANIFEST_NAME]
    assert {disposition for _, disposition in written} == {"replace"}


def test_a_failed_save_never_leaves_a_manifest_describing_missing_files(backend_name, target, monkeypatch):
    table = every_kind(backend_name)
    real_write = persistence.write

    def failing_write(sink, data, **kwargs):
        if os.path.basename(sink.path) == file_name("outputs"):
            raise OSError("disk full")
        return real_write(sink, data, **kwargs)

    monkeypatch.setattr(persistence, "write", failing_write)

    with pytest.raises(OSError, match="disk full"):
        table.save(target)

    assert not os.path.exists(os.path.join(target, MANIFEST_NAME))


# ---------------------------------------------------------------------------
# 7. Verification
# ---------------------------------------------------------------------------


def test_verification_passes_an_untouched_save(backend_name, target):
    table = every_kind(backend_name)
    table.save(target)

    report = verify_saved_table(target)

    assert report.ok and report.mismatched == () and report.missing == ()
    assert report.build_id == table.build_id


def test_tampering_with_one_file_is_reported_and_load_does_not_check(backend_name, target, monkeypatch):
    table = every_kind(backend_name)
    table.save(target)
    victim = os.path.join(target, file_name("entries"))
    original = pq.read_table(victim)
    pq.write_table(original.slice(0, original.num_rows - 1), victim)  # a valid file, with a row gone

    report = verify_saved_table(target)

    assert report.mismatched == (file_name("entries"),)
    assert report.missing == () and not report.ok

    def forbidden(*_args, **_kwargs):
        raise AssertionError("load must not hash")

    monkeypatch.setattr(persistence.hashlib, "sha256", forbidden)
    loaded = DecisionTable.load(target, connection=connection_for(backend_name))
    assert arrow(loaded, "entries").num_rows == original.num_rows - 1


def test_a_same_size_change_is_found_by_the_hash(backend_name, target):
    table = every_kind(backend_name)
    table.save(target)
    victim = os.path.join(target, file_name("fields"))
    with open(victim, "rb") as handle:
        data = bytearray(handle.read())
    data[len(data) // 2] ^= 0xFF
    with open(victim, "wb") as handle:
        handle.write(data)

    assert verify_saved_table(target).mismatched == (file_name("fields"),)


def test_a_missing_file_is_reported(backend_name, target):
    table = every_kind(backend_name)
    table.save(target)
    os.remove(os.path.join(target, file_name("truthy")))
    pq.write_table(pq.read_table(os.path.join(target, file_name("outputs"))).slice(0, 0),
                   os.path.join(target, file_name("outputs")))

    report = verify_saved_table(target)

    assert report.missing == (file_name("truthy"),)
    assert report.mismatched == (file_name("outputs"),)


def test_verification_reads_bytes_through_the_transport_facade_and_loads_no_relations(
    backend_name, target, monkeypatch
):
    table = every_kind(backend_name)
    table.save(target)
    facades = []
    real_storage = persistence.storage

    def recording_storage(*args, **kwargs):
        facades.append(real_storage(*args, **kwargs))
        return facades[-1]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("verification must not parse or build relations")

    monkeypatch.setattr(persistence, "storage", recording_storage)
    monkeypatch.setattr(persistence, "parse", forbidden)
    monkeypatch.setattr(persistence, "from_arrow", forbidden)

    assert verify_saved_table(target).ok
    assert facades


def test_a_missing_manifest_propagates_the_storage_error(target):
    from mountainash_transport import PathNotFoundError

    os.makedirs(target)

    with pytest.raises(PathNotFoundError):
        verify_saved_table(target)
    with pytest.raises(PathNotFoundError):
        DecisionTable.load(target)


# ---------------------------------------------------------------------------
# 8. Storage forms: direct local path, profile by name, profile object
# ---------------------------------------------------------------------------


def test_direct_local_path(backend_name, target):
    table = every_kind(backend_name)

    table.save(target)

    assert_same_table(table, DecisionTable.load(target, connection=connection_for(backend_name)))


def test_storage_profile_by_name(backend_name, target, tmp_path, monkeypatch):
    name = write_profiles(tmp_path, monkeypatch)
    table = every_kind(backend_name)

    table.save(target, storage_profile=name)
    loaded = DecisionTable.load(target, connection=connection_for(backend_name), storage_profile=name)

    assert_same_table(table, loaded)
    assert verify_saved_table(target, storage_profile=name).ok


def test_storage_profile_object(backend_name, target, tmp_path, monkeypatch):
    name = write_profiles(tmp_path, monkeypatch)
    profile, _ = resolve_storage(name)
    table = every_kind(backend_name)

    table.save(target, storage_profile=profile)
    loaded = DecisionTable.load(target, connection=connection_for(backend_name), storage_profile=profile)

    assert_same_table(table, loaded)
    assert verify_saved_table(target, storage_profile=profile).ok
    assert sorted(os.listdir(target)) == sorted([MANIFEST_NAME, *(file_name(n) for n in DATA_FILES)])


# ---------------------------------------------------------------------------
# 9. Remote storage: one configured writable scratch profile, by name and by object
# ---------------------------------------------------------------------------

REMOTE = pytest.mark.skipif(
    not (os.environ.get(SCRATCH_PROFILE_ENV) and os.environ.get(SCRATCH_PATH_ENV)),
    reason=f"set {SCRATCH_PROFILE_ENV} (profile name) and {SCRATCH_PATH_ENV} (writable directory) to run",
)


def scratch_storage(path: str):
    """A transport facade on the scratch location, for cleanup and tampering."""
    profile, auth = resolve_storage(os.environ[SCRATCH_PROFILE_ENV])
    return storage(persistence.detect_provider_from_path(path), profile, auth_profile=auth)


@pytest.fixture
def remote_target():
    base = os.environ[SCRATCH_PATH_ENV].rstrip("/")
    path = f"{base}/decision-table-{uuid.uuid4().hex}"
    yield path
    with scratch_storage(path) as files:
        for name in (MANIFEST_NAME, *(file_name(n) for n in DATA_FILES)):
            try:
                files.delete(f"{path}/{name}")
            except Exception:  # noqa: BLE001 - best-effort scratch cleanup
                pass


def remote_profiles():
    name = os.environ[SCRATCH_PROFILE_ENV]
    return {"name": name, "object": resolve_storage(name)[0]}


@pytest.mark.integration
@REMOTE
@pytest.mark.parametrize("form", ["name", "object"])
def test_remote_save_load_verify_and_replace(backend_name, remote_target, form):
    profile = remote_profiles()[form]
    table = every_kind(backend_name)

    table.save(remote_target, storage_profile=profile)
    loaded = DecisionTable.load(remote_target, connection=connection_for(backend_name), storage_profile=profile)
    assert_same_table(table, loaded)
    assert verify_saved_table(remote_target, storage_profile=profile).ok

    second = empty_table(backend_name)
    second.save(remote_target, storage_profile=profile)
    replaced = DecisionTable.load(remote_target, connection=connection_for(backend_name), storage_profile=profile)
    for name in CONSTRUCTION:
        assert arrow(replaced, name).num_rows == 0, name
    assert replaced.build_id == second.build_id
    assert verify_saved_table(remote_target, storage_profile=profile).ok


@pytest.mark.integration
@REMOTE
def test_remote_match_parity_and_tamper_detection(backend_name, remote_target):
    profile = os.environ[SCRATCH_PROFILE_ENV]
    _, table, grid, _ = built(backend_name, "categorical_negation")
    table.save(remote_target, storage_profile=profile)

    loaded = DecisionTable.load(remote_target, connection=connection_for(backend_name), storage_profile=profile)

    assert match_rows(loaded, grid) == match_rows(table, grid)
    with scratch_storage(remote_target) as files:
        files.write(f"{remote_target}/{file_name('entries')}", b"tampered")
    report = verify_saved_table(remote_target, storage_profile=profile)
    assert report.mismatched == (file_name("entries"),)

