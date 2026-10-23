"""Save and load a DecisionTable through ``mountainash-files`` and ``mountainash-transport``.

A saved table is a directory of eight fixed Parquet files and one ``manifest.json``.  Every data
file is written independently (replacing any earlier file of the same name) and the manifest is
written last: it records the table's ``build_id`` and each data file's name, byte size and SHA-256
over the exact bytes written.  A table that is only partly written therefore has no manifest, or
one from an earlier save.

Load reads ``build_id`` from the manifest and the eight files by name, without checking them.
:func:`verify_saved_table` is the separate, optional integrity check.

Arrow tables pass between Mountainash and Files as values; this module imports neither Polars,
Ibis nor PyArrow.
"""

from __future__ import annotations

import hashlib
import json
import typing as t
from dataclasses import dataclass

from mountainash.relations import Relation, from_arrow
from mountainash_files import FileSinkSpec, FileSourceSpec, parse, serialise_bytes, write
from mountainash_transport import (
    PathNotFoundError,
    StorageFacade,
    detect_provider_from_path,
    resolve_storage,
    storage,
)

from mountainash_rules.core.dimension import DimensionsMetadata

if t.TYPE_CHECKING:
    from mountainash_rules.decision_table.table import DecisionTable

MANIFEST_NAME = "manifest.json"
CONSTRUCTION_FILES: t.Final = ("entries", "entry_sources", "pattern_conditions")
METADATA_FILES: t.Final = ("ruleset", "fields", "dimensions", "outputs", "truthy")
DATA_FILES: t.Final = (*CONSTRUCTION_FILES, *METADATA_FILES)
_CHUNK = 1 << 20


def file_name(table_name: str) -> str:
    """The fixed file name of a data table."""
    return f"{table_name}.parquet"


def _join(path: str, name: str) -> str:
    return f"{path.rstrip('/')}/{name}"


@dataclass(frozen=True)
class VerificationReport:
    """What :func:`verify_saved_table` found, by data-file name.

    ``mismatched`` files exist but differ from the manifest in size or SHA-256; ``missing`` files
    are listed in the manifest but absent from storage.
    """

    build_id: str
    mismatched: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatched and not self.missing


def _open_storage(path: str, storage_profile: t.Any) -> StorageFacade:
    """The Transport facade for ``path``; a profile name resolves as Files resolves it."""
    auth = None
    if isinstance(storage_profile, str):
        storage_profile, auth = resolve_storage(storage_profile)
    return storage(detect_provider_from_path(path), storage_profile, auth_profile=auth)


def _sink(path: str, storage_profile: t.Any) -> FileSinkSpec:
    return FileSinkSpec(
        path=path, format="parquet", storage_profile=storage_profile,
        write_disposition="replace", create_path=True,
    )


def _arrow_tables(table: DecisionTable) -> dict[str, t.Any]:
    metadata = table.source_metadata.to_tables()
    relations: dict[str, Relation] = {
        "entries": table.entries,
        "entry_sources": table.entry_sources,
        "pattern_conditions": table.pattern_conditions,
        **metadata,
    }
    return {name: relations[name].to_pyarrow() for name in DATA_FILES}


def save_table(table: DecisionTable, path: str, *, storage_profile: t.Any = None) -> None:
    """Write the eight data files, then the manifest, replacing what ``path`` held."""
    arrow = _arrow_tables(table)
    manifest_files = []
    for name in DATA_FILES:
        data = serialise_bytes(arrow[name], "parquet")
        write(_sink(_join(path, file_name(name)), storage_profile), data)
        manifest_files.append(
            {"name": file_name(name), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    manifest = json.dumps({"build_id": table.build_id, "files": manifest_files}, indent=2, sort_keys=True)
    write(_sink(_join(path, MANIFEST_NAME), storage_profile), manifest.encode("utf-8"))


def _read_manifest(path: str, storage_profile: t.Any) -> dict[str, t.Any]:
    with _open_storage(path, storage_profile) as files:
        return json.loads(files.read(_join(path, MANIFEST_NAME)).decode("utf-8"))


def _parse_table(path: str, name: str, storage_profile: t.Any) -> t.Any:
    (result,) = parse(
        FileSourceSpec(path=_join(path, file_name(name)), format="parquet", storage_profile=storage_profile),
        output="arrow",
    )
    return result.data


def load_table(path: str, *, connection: t.Any = None, storage_profile: t.Any = None) -> DecisionTable:
    """Read a saved table; the three construction relations go onto ``connection``, if given."""
    from mountainash_rules.decision_table.table import DecisionTable

    build_id = _read_manifest(path, storage_profile)["build_id"]
    arrow = {name: _parse_table(path, name, storage_profile) for name in DATA_FILES}
    source_metadata = DimensionsMetadata.from_tables(
        {name: from_arrow(arrow[name]) for name in METADATA_FILES}
    )
    entries, entry_sources, pattern_conditions = (
        from_arrow(arrow[name], connection=connection) for name in CONSTRUCTION_FILES
    )
    return DecisionTable(entries, source_metadata, entry_sources, pattern_conditions, build_id)


def _differs(files: StorageFacade, path: str, listed: dict[str, t.Any]) -> bool:
    """Whether the stored bytes differ from the manifest entry (size first, then SHA-256)."""
    target = _join(path, listed["name"])
    size = files.get_size(target)
    if size is not None and size != listed["size"]:
        return True
    digest = hashlib.sha256()
    total = 0
    with files.read_stream(target) as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
            total += len(chunk)
    return total != listed["size"] or digest.hexdigest() != listed["sha256"]


def verify_saved_table(path: str, *, storage_profile: t.Any = None) -> VerificationReport:
    """Check each manifest-listed data file's byte size and SHA-256; load no relations.

    Reports every mismatched or missing file.  A missing or unreadable manifest, and storage
    errors other than a file being absent, propagate unchanged.
    """
    manifest = _read_manifest(path, storage_profile)
    mismatched: list[str] = []
    missing: list[str] = []
    with _open_storage(path, storage_profile) as files:
        for listed in manifest["files"]:
            try:
                if _differs(files, path, listed):
                    mismatched.append(listed["name"])
            except PathNotFoundError:
                missing.append(listed["name"])
    return VerificationReport(manifest["build_id"], tuple(mismatched), tuple(missing))
