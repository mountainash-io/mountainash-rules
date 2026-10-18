"""Typed immutable transport for accumulator versions."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, cast
from uuid import UUID, uuid4

import yaml
import mountainash.expressions as ma
from mountainash import MountainashDtype, materialization_scope
from mountainash.core.dtypes import PortableDtype, TypeTarget, registry
from mountainash.relations import Relation, relation
from mountainash.typespec import FieldSpec, TypeSpec, UniversalType
from mountainash.typespec.universal_types import from_canonical

from mountainash_rules.core.constants import MatchStrategy
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.engines.accumulate.lattice import Lattice, TableDescriptor
from mountainash_rules.engines.accumulate.sources import (
    CheckResult,
    DependencyReference,
    ReportBinding,
    SourceBundle,
    SourceReport,
)
from mountainash_rules.engines.accumulate.validate import Approval

_FORMAT_VERSION = 1
_IDENTITY_VERSION = 1


@dataclass(frozen=True, slots=True)
class UnavailableEvidenceReference:
    report_id: UUID
    reference_metadata: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.report_id, UUID):
            raise TypeError("report_id must be a UUID")
        if not isinstance(self.reference_metadata, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            for item in self.reference_metadata
        ):
            raise TypeError("reference_metadata must be an immutable tuple of string pairs")


def _schema_descriptor(schema: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    """Encode physical relation fields using Mountainash's Frictionless schema codec."""
    fields = []
    for name, dtype in schema:
        text = str(dtype).lower()
        if isinstance(dtype, PortableDtype):
            universal, format_hint = from_canonical(dtype)
            fields.append(FieldSpec(
                name=name, type=universal, format=format_hint or "default", dtype=dtype,
            ))
            continue
        canonical = dtype if isinstance(dtype, MountainashDtype) else None
        if canonical is None:
            try:
                canonical = MountainashDtype(text)
            except ValueError:
                pass
        format_hint = None
        if canonical is not None:
            universal, format_hint = from_canonical(canonical)
        elif "decimal" in text:
            universal = UniversalType.NUMBER
        elif any(token in text for token in ("int", "uint")) or text in {
            "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
        }:
            universal = UniversalType.INTEGER
        elif any(token in text for token in ("float", "double")):
            universal = UniversalType.NUMBER
        elif "bool" in text:
            universal = UniversalType.BOOLEAN
        elif "date" in text and "datetime" not in text:
            universal = UniversalType.DATE
        elif "datetime" in text or "timestamp" in text:
            universal = UniversalType.DATETIME
        elif "list" in text or "array" in text:
            universal = UniversalType.ARRAY
        elif "struct" in text:
            universal = UniversalType.OBJECT
        else:
            universal = UniversalType.STRING
        fields.append(FieldSpec(
            name=name, type=universal, format=format_hint or "default",
            backend_type=canonical.value if isinstance(dtype, MountainashDtype) else text,
        ))
    return TypeSpec(fields=fields).to_frictionless()


def _schema_dtype(field: FieldSpec) -> Any:
    if field.dtype is not None:
        return field.dtype
    if field.backend_type is not None:
        try:
            return MountainashDtype(field.backend_type)
        except ValueError:
            return field.backend_type
    return None


def _matches_schema(spec: TypeSpec, schema: tuple[tuple[str, Any], ...]) -> bool:
    if tuple(name for name, _ in schema) != tuple(field.name for field in spec.fields):
        return False
    for (_, actual), field in zip(schema, spec.fields):
        expected = _schema_dtype(field)
        if isinstance(expected, PortableDtype):
            if not isinstance(actual, PortableDtype):
                return False
            # Compare transport representations through the shared type owner:
            # an aware timestamp without a zone name is represented as UTC.
            # Precision, awareness and nested child types remain significant.
            if registry.to_native_schema(actual, TypeTarget.PYARROW) != registry.to_native_schema(expected, TypeTarget.PYARROW):
                return False
        elif expected != str(actual).lower():
            return False
    return True


def _schema_from_spec(spec: TypeSpec) -> tuple[tuple[str, Any], ...]:
    return tuple((field.name, _schema_dtype(field)) for field in spec.fields)


def _is_integer_dtype(dtype: Any) -> bool:
    if isinstance(dtype, MountainashDtype):
        return dtype in {
            MountainashDtype.I8, MountainashDtype.I16, MountainashDtype.I32,
            MountainashDtype.I64, MountainashDtype.U8, MountainashDtype.U16,
            MountainashDtype.U32, MountainashDtype.U64,
        }
    return str(dtype).lower() in {
        "int8", "int16", "int32", "int64",
        "uint8", "uint16", "uint32", "uint64",
        "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
    }


def _manifest(lattice: Lattice) -> dict[str, Any]:
    tables = []
    metadata = lattice.metadata
    scopes = metadata.outcome_scopes or {"default": metadata.aggregates}
    for descriptor in lattice.table_descriptors:
        if descriptor.role == "outputs":
            value_mapping = dict(lattice.output_columns[cast(str, descriptor.scope)])
        elif descriptor.role == "source_payloads":
            value_mapping = {
                aggregate.column_name: f"a{index:04d}"
                for index, aggregate in enumerate(scopes[cast(str, descriptor.scope)], 1)
            }
        else:
            value_mapping = None
        tables.append({
            "table_id": descriptor.table_id,
            "role": descriptor.role,
            "scope": descriptor.scope,
            "field": descriptor.field,
            "path": f"tables/{descriptor.table_id}.parquet",
            "keys": list(descriptor.key),
            "schema": _schema_descriptor(descriptor.schema),
            "dependency": {
                "version_id": str(lattice.version_id),
                "capture_id": str(descriptor.capture_id),
                "table_id": descriptor.table_id,
            },
            "capture_id": str(descriptor.capture_id),
            "output_columns": value_mapping,
        })
    return {
        "format_version": _FORMAT_VERSION,
        "identity_version": _IDENTITY_VERSION,
        "version_id": str(lattice.version_id),
        "parent_version_id": str(lattice.parent_version_id) if lattice.parent_version_id else None,
        "geometry_namespace": str(lattice.geometry_namespace),
        "published": lattice.published,
        "metadata": json.loads(lattice._metadata_json),
        "tables": tables,
        "observations": lattice.observations,
        "evidence": [],
    }


def _validate_manifest(manifest: Mapping[str, Any], *, check_paths: bool = True) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise TypeError("manifest must be a mapping")
    required = {"format_version", "identity_version", "version_id", "parent_version_id",
                "geometry_namespace", "published", "metadata", "tables", "observations", "evidence"}
    if set(manifest) != required:
        raise ValueError("manifest fields do not match format version 1")
    if manifest["format_version"] != _FORMAT_VERSION or manifest["identity_version"] != _IDENTITY_VERSION:
        raise ValueError("unsupported accumulator manifest version")
    if type(manifest["published"]) is not bool or not isinstance(manifest["observations"], list) or not isinstance(manifest["evidence"], list):
        raise ValueError("invalid manifest state fields")
    rows = manifest["tables"]
    if not isinstance(rows, list):
        raise ValueError("manifest tables must be an ordered list")
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("invalid table descriptor")
        expected_fields = {"table_id", "role", "scope", "field", "path", "keys", "schema", "dependency", "capture_id", "output_columns"}
        if set(row) != expected_fields:
            raise ValueError("table descriptor fields do not match format version 1")
        table_id = row.get("table_id")
        if not isinstance(table_id, str) or not re.fullmatch(r"t[0-9]{4}", table_id) or table_id in ids:
            raise ValueError("invalid or duplicate table ID")
        ids.add(table_id)
        if check_paths:
            path = row.get("path")
            pure_path = PurePosixPath(path) if isinstance(path, str) else None
            if pure_path is None or pure_path.as_posix() != path or pure_path.is_absolute() or ".." in pure_path.parts:
                raise ValueError("table path must be a generated relative path")
            if path != (Path("tables") / f"{table_id}.parquet").as_posix():
                raise ValueError("table path must be generated beneath tables/")
        if not isinstance(row.get("role"), str) or not row["role"]:
            raise ValueError("invalid table role")
        if row.get("scope") is not None and not isinstance(row["scope"], str):
            raise ValueError("invalid table scope")
        if row.get("field") is not None and not isinstance(row["field"], str):
            raise ValueError("invalid table field")
        if not isinstance(row.get("keys"), list) or not row["keys"] or not all(isinstance(k, str) for k in row["keys"]):
            raise ValueError("invalid table key declaration")
        if len(set(row["keys"])) != len(row["keys"]):
            raise ValueError("repeated table key column")
        dependency = row.get("dependency")
        if not isinstance(dependency, Mapping) or dependency.get("table_id") != table_id or dependency.get("version_id") != str(manifest["version_id"]) or dependency.get("capture_id") != row.get("capture_id"):
            raise ValueError("invalid table dependency reference")
        UUID(str(row["capture_id"]))
        spec = TypeSpec.from_frictionless(row["schema"])
        if row["role"] == "cells":
            identity_field = next(
                (field for field in spec.fields if field.name == "identity_version"),
                None,
            )
            if (
                identity_field is None
                or identity_field.type != UniversalType.INTEGER
            ):
                raise ValueError("cells schema must declare an integer identity_version")
        if any(key not in spec.field_names for key in row["keys"]):
            raise ValueError("table key is absent from declared schema")
        mapping = row.get("output_columns")
        if row["role"] in {"outputs", "source_payloads"}:
            if not isinstance(mapping, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
                raise ValueError("invalid aggregate value-column mapping")
            if any(value not in spec.field_names for value in mapping.values()) or len(set(mapping.values())) != len(mapping):
                raise ValueError("aggregate mapping does not match table schema")
        elif mapping is not None:
            raise ValueError("non-contribution table cannot declare aggregate mappings")
    UUID(str(manifest["version_id"]))
    UUID(str(manifest["geometry_namespace"]))
    if manifest["parent_version_id"] is not None:
        UUID(str(manifest["parent_version_id"]))
    for item in manifest["evidence"]:
        if not isinstance(item, Mapping):
            raise ValueError("invalid evidence reference")
        if item.get("kind") == "approval":
            if set(item) != {"kind", "report_id", "finding_ids"} or not isinstance(item["finding_ids"], list) or any(type(value) is not int for value in item["finding_ids"]):
                raise ValueError("invalid approval reference")
            UUID(str(item["report_id"]))
            continue
        if item.get("kind") == "unavailable_evidence":
            if set(item) != {"kind", "report_id", "reference"} or not isinstance(item["reference"], Mapping):
                raise ValueError("invalid unavailable evidence reference")
            reference = item["reference"]
            if set(reference) != {"kind", "path"} or reference["kind"] not in {"source_report", "artifact_report"}:
                raise ValueError("invalid unavailable evidence metadata")
            report_id = UUID(str(item["report_id"]))
            if reference["path"] != f"evidence/{report_id}/report.yaml":
                raise ValueError("invalid unavailable evidence path")
            continue
        if set(item) != {"kind", "report_id", "path"} or item["kind"] not in {"source_report", "artifact_report"}:
            raise ValueError("invalid report reference")
        report_id = UUID(str(item["report_id"]))
        if item["path"] != f"evidence/{report_id}/report.yaml":
            raise ValueError("invalid report reference path")
    return dict(manifest)


def _read_table(path: Path, descriptor: Mapping[str, Any]) -> Relation:
    import polars as pl  # allow: accumulator version Parquet serialization bridge

    if path.is_symlink():
        raise ValueError("bundle table paths must not be symlinks")
    frame = pl.read_parquet(path)
    expected = TypeSpec.from_frictionless(descriptor["schema"])
    if list(frame.columns) != [field.name for field in expected.fields]:
        raise ValueError(f"Parquet columns do not match manifest for {descriptor['table_id']}")
    actual = relation(frame)
    if not _matches_schema(expected, tuple(actual.collect_schema(allow_execution=False).items())):
        raise ValueError(f"Parquet schema does not match manifest for {descriptor['table_id']}")
    return actual


def _write_relation(rel: Relation, path: Path) -> None:
    import polars as pl  # allow: accumulator version Parquet serialization bridge

    frame = rel.to_polars()
    if not isinstance(frame, pl.DataFrame):
        raise TypeError("Parquet writer requires a Polars materialization")
    frame.write_parquet(path)


def _evidence_entry(report: Any, version_id: UUID, table_descriptors: Mapping[str, TableDescriptor]) -> dict[str, Any]:
    identifier = str(report.report_id)
    folder = f"evidence/{identifier}"
    tables: dict[str, dict[str, Any]] = {}
    for name, rel in (("findings", report.findings), ("subjects", report.subjects), *report.witnesses.items()):
        key = name if name in {"findings", "subjects"} else f"witness_{len(tables) - 1:04d}"
        path = f"{folder}/witnesses/{key}.parquet" if key.startswith("witness_") else f"{folder}/{key}.parquet"
        tables[key] = {"path": path, "schema": _schema_descriptor(tuple(rel.schema.items()))}
    dependencies = []
    for group, refs in report.binding.dependencies.items():
        for ref in refs:
            descriptor = table_descriptors.get(ref.table_id)
            local = (
                descriptor is not None
                and ref.version_id == version_id
                and descriptor.table_id == ref.table_id
                and descriptor.capture_id == ref.capture_id
                and descriptor.key == ref.keys
                and descriptor.schema == ref.schema
            )
            dependencies.append({
                "group": group, "version_id": str(ref.version_id), "capture_id": str(ref.capture_id),
                "table_ids": [ref.table_id] if local else [], "table_id": ref.table_id if local else None,
                "keys": list(ref.keys), "schema": _schema_descriptor(ref.schema),
            })
    return {
        "kind": "source_report" if isinstance(report, SourceReport) else "artifact_report",
        "report_id": identifier, "path": f"{folder}/report.yaml",
        "validator_version": report.binding.semantic_version,
        "metadata": json.loads(report.binding._metadata_json), "binding": dependencies,
        "checks": [{"name": c.name, "dependency_groups": list(c.dependency_groups),
                    "outcome": c.outcome, "finding_count": c.finding_count,
                    "emitted_count": c.emitted_count} for c in report.checks],
        "findings_table": "findings", "subjects_table": "subjects",
        "witness_tables": {
            name: key for name, key in zip(
                report.witnesses, (key for key in tables if key.startswith("witness_"))
            )
        },
        "table_schemas": tables,
    }


def _write_evidence(root: Path, lattice: Lattice, manifest: dict[str, Any]) -> None:
    table_descriptors = {descriptor.table_id: descriptor for descriptor in lattice.table_descriptors}
    entries: list[dict[str, Any]] = []
    for report in lattice.evidence:
        if isinstance(report, UnavailableEvidenceReference):
            entries.append({
                "kind": "unavailable_evidence", "report_id": str(report.report_id),
                "reference": dict(report.reference_metadata),
            })
            continue
        entry = _evidence_entry(report, lattice.version_id, table_descriptors)
        report_dir = root / f"evidence/{report.report_id}"
        report_dir.mkdir(parents=True)
        _write_relation(report.findings, report_dir / "findings.parquet")
        _write_relation(report.subjects, report_dir / "subjects.parquet")
        witness_dir = report_dir / "witnesses"
        if report.witnesses:
            witness_dir.mkdir()
        for name, key in entry["witness_tables"].items():
            _write_relation(report.witnesses[name], root / entry["table_schemas"][key]["path"])
        (report_dir / "report.yaml").write_text(yaml.safe_dump(entry, sort_keys=False), encoding="utf-8")
        entries.append({"kind": entry["kind"], "report_id": entry["report_id"], "path": entry["path"]})
    for approval in lattice.approvals:
        entries.append({"kind": "approval", "report_id": str(approval.report_id),
                        "finding_ids": list(approval.finding_ids)})
    manifest["evidence"] = entries


def _load_evidence(root: Path, manifest: Mapping[str, Any], tables: Mapping[str, Relation]) -> tuple[tuple[Any, ...], tuple[Approval, ...]]:
    from mountainash_rules.engines.accumulate.validate import ArtifactReport

    reports: list[Any] = []
    approvals: list[Approval] = []
    for entry in manifest["evidence"]:
        if not isinstance(entry, Mapping):
            raise ValueError("invalid evidence reference")
        if entry.get("kind") == "approval":
            findings = entry.get("finding_ids")
            if not isinstance(findings, list) or any(type(value) is not int for value in findings):
                raise ValueError("invalid approval reference")
            approvals.append(Approval(UUID(entry["report_id"]), tuple(findings)))
            continue
        if entry.get("kind") == "unavailable_evidence":
            reports.append(UnavailableEvidenceReference(
                UUID(entry["report_id"]), tuple(sorted(entry["reference"].items())),
            ))
            continue
        report_id = UUID(entry["report_id"])
        path_text = entry.get("path")
        expected_path = f"evidence/{report_id}/report.yaml"
        path = PurePosixPath(path_text) if isinstance(path_text, str) else None
        if path is None or path.as_posix() != path_text or path_text != expected_path:
            raise ValueError("unsafe or mismatched report path")
        report_path = root / Path(*path.parts)
        if report_path.is_symlink() or not report_path.resolve().is_relative_to(root.resolve()):
            raise ValueError("report path escapes the version directory")
        report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, Mapping) or report.get("report_id") != entry.get("report_id") or report.get("kind") != entry.get("kind"):
            raise ValueError("evidence reference does not match report metadata")
        report_tables = {}
        for name, info in report["table_schemas"].items():
            rel_path = PurePosixPath(info["path"])
            if rel_path.is_absolute() or ".." in rel_path.parts or rel_path.parts[0:2] != ("evidence", report["report_id"]):
                raise ValueError("unsafe evidence table path")
            table_path = root / Path(*rel_path.parts)
            if table_path.is_symlink() or not table_path.resolve().is_relative_to(root.resolve()):
                raise ValueError("evidence table escapes the version directory")
            report_tables[name] = _read_table(table_path, {"schema": info["schema"], "table_id": name})
        grouped: dict[str, list[DependencyReference]] = {}
        descriptor_by_id = {item["table_id"]: item for item in manifest["tables"]}
        for dep in report["binding"]:
            table_id = dep.get("table_id")
            relation_ = tables.get(table_id) if table_id is not None else None
            if table_id is not None:
                declared = descriptor_by_id.get(table_id)
                if (
                    declared is None
                    or dep["version_id"] != manifest["version_id"]
                    or dep["capture_id"] != declared["capture_id"]
                    or dep["keys"] != declared["keys"]
                    or dep["schema"] != declared["schema"]
                ):
                    raise ValueError("current evidence dependency reference does not match its table")
            if dep.get("table_ids") != ([table_id] if table_id is not None else []):
                raise ValueError("evidence table ID association is inconsistent")
            spec = TypeSpec.from_frictionless(dep["schema"])
            schema = _schema_from_spec(spec)
            grouped.setdefault(dep["group"], []).append(DependencyReference(
                relation_, tuple(dep["keys"]), schema, UUID(dep["version_id"]),
                UUID(dep["capture_id"]), table_id,
            ))
        binding = ReportBinding(
            report["validator_version"], DimensionsMetadata.model_validate(report["metadata"]).model_dump_json(),
            {name: tuple(refs) for name, refs in grouped.items()},
        )
        checks = tuple(CheckResult(
            item["name"], tuple(item["dependency_groups"]), item["outcome"],
            item["finding_count"], item["emitted_count"],
        ) for item in report["checks"])
        witnesses = {
            name: report_tables[key] for name, key in report["witness_tables"].items()
        }
        cls = SourceReport if report["kind"] == "source_report" else ArtifactReport
        reports.append(cls(
            UUID(report["report_id"]), binding, checks,
            report_tables[report["findings_table"]],
            report_tables[report["subjects_table"]],
            witnesses,
        ))
    return tuple(reports), tuple(approvals)


def _validate_cells_identity_version(cells: Relation) -> None:
    if (
        "identity_version" not in cells.columns
        or not _is_integer_dtype(cells.schema["identity_version"])
    ):
        raise ValueError("cells must contain an integer identity_version column")
    invalid = cells.filter(
        ma.col("identity_version").is_null()
        | ma.col("identity_version").ne(ma.lit(1, dtype=MountainashDtype.I64))
    ).limit(1)
    if invalid.count_rows():
        raise ValueError("cells contain an unsupported identity_version")


def _make_lattice(manifest: Mapping[str, Any], relations: Mapping[str, Relation]) -> Lattice:
    descriptors: list[TableDescriptor] = []
    tables: dict[str, Relation] = {}
    output_columns: dict[str, dict[str, str]] = {}
    for row in manifest["tables"]:
        table_id = row["table_id"]
        if table_id not in relations:
            raise ValueError(f"missing attached relation {table_id}")
        rel = relations[table_id]
        schema = tuple(rel.collect_schema(allow_execution=False).items())
        spec = TypeSpec.from_frictionless(row["schema"])
        declared_names = tuple(field.name for field in spec.fields)
        if tuple(rel.columns) != declared_names:
            raise ValueError(f"attached relation columns do not match {table_id}")
        keys = tuple(row["keys"])
        if len(set(keys)) != len(keys) or any(key not in rel.columns for key in keys):
            raise ValueError(f"invalid key declaration for {table_id}")
        if not _matches_schema(spec, schema):
            raise ValueError(f"attached relation schema does not match {table_id}")
        if row["role"] == "cells":
            _validate_cells_identity_version(rel)
        descriptors.append(TableDescriptor(
            table_id=table_id, role=row["role"], schema=schema, key=keys,
            scope=row.get("scope"), field=row.get("field"), capture_id=UUID(row["capture_id"]),
        ))
        tables[table_id] = rel
        mapping = row.get("output_columns")
        if row["role"] == "outputs" and isinstance(mapping, Mapping):
            output_columns[row["scope"]] = dict(mapping)
    metadata = DimensionsMetadata.model_validate(manifest["metadata"])
    descriptor_by_role = {}
    for descriptor in descriptors:
        semantic_key = (descriptor.role, descriptor.scope, descriptor.field)
        if semantic_key in descriptor_by_role:
            raise ValueError(f"duplicate table descriptor for {semantic_key}")
        descriptor_by_role[semantic_key] = descriptor
    scopes = metadata.outcome_scopes or {"default": metadata.aggregates}
    expected: dict[tuple[str, str | None, str | None], tuple[str, ...]] = {
        ("cells", None, None): ("partition", "scope", "cell_id"),
        ("cell_boxes", None, None): ("partition", "scope", "cell_id", "box_id"),
        ("contributors", None, None): ("partition", "scope", "contributor_set_id", "source_uuid"),
        ("source_rules", None, None): tuple(
            name for name in (metadata.partition_field, metadata.outcome_scope_field, metadata.uuid_field)
            if name is not None
        ),
    }
    for scope_name in scopes:
        expected[("outputs", scope_name, None)] = ("partition", "scope", "contributor_set_id")
        expected[("source_payloads", scope_name, None)] = ("partition", "scope", "source_uuid")
    for dimension in metadata.dimensions:
        if dimension.match_strategy is not MatchStrategy.CONTEXT_REGEX:
            expected[("source_state", None, dimension.dimension_name)] = ("partition", "scope", "source_uuid")
        if dimension.match_strategy in {
            MatchStrategy.RANGE, MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
        }:
            expected[("cell_ordered_bounds", None, dimension.resolved_context_field)] = (
                "partition", "scope", "cell_id", "box_id",
            )
        elif dimension.match_strategy in {
            MatchStrategy.EXACT, MatchStrategy.EXACT_KEY, MatchStrategy.NOT_EQUAL,
            MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
        }:
            expected[("cell_category_sets", None, dimension.resolved_context_field)] = (
                "partition", "scope", "cell_id", "box_id",
            )
            expected[("cell_category_values", None, dimension.resolved_context_field)] = (
                "partition", "scope", "cell_id", "box_id", "value",
            )
    if set(descriptor_by_role) != set(expected):
        raise ValueError("manifest table roles do not match declared accumulator semantics")
    for semantic_key, key in expected.items():
        if descriptor_by_role[semantic_key].key != key:
            raise ValueError(f"manifest table key does not match role {semantic_key}")
    states = {d.field: tables[d.table_id] for d in descriptors if d.role == "source_state" and d.field is not None}
    payloads = {d.scope: tables[d.table_id] for d in descriptors if d.role == "source_payloads" and d.scope is not None}
    source_descriptor = descriptor_by_role[("source_rules", None, None)]
    rules = tables[source_descriptor.table_id]
    if metadata.uuid_field is None or metadata.uuid_field not in rules.columns:
        raise ValueError("source rules lack their declared UUID field")
    partition = ma.col(metadata.partition_field) if metadata.partition_field else ma.lit("default")
    scope = ma.col(metadata.outcome_scope_field) if metadata.outcome_scope_field else ma.lit("default")
    identities = rules.select(partition.alias("partition"), scope.alias("scope"),
                              ma.col(metadata.uuid_field).alias("source_uuid"))
    sources = SourceBundle(rules, states, payloads, identities, metadata.model_dump_json())
    evidence: tuple[Any, ...] = ()
    approvals: tuple[Approval, ...] = ()
    return Lattice(
        tables=tables, table_descriptors=tuple(descriptors), sources=sources,
        geometry_namespace=UUID(manifest["geometry_namespace"]),
        _metadata_json=metadata.model_dump_json(), output_columns=output_columns,
        version_id=UUID(manifest["version_id"]),
        parent_version_id=UUID(manifest["parent_version_id"]) if manifest["parent_version_id"] else None,
        published=manifest["published"], _observations_json=json.dumps(manifest["observations"]),
        evidence=evidence, approvals=approvals,
    )


def save(lattice: Lattice, directory: os.PathLike[str] | str) -> None:
    destination = Path(directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (stage / "tables").mkdir()
        manifest = _manifest(lattice)
        for descriptor in lattice.table_descriptors:
            _write_relation(lattice.tables[descriptor.table_id], stage / "tables" / f"{descriptor.table_id}.parquet")
        _write_evidence(stage, lattice, manifest)
        (stage / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(stage, destination)
    except BaseException:
        try:
            shutil.rmtree(stage)
        except BaseException:
            pass
        raise


def load(directory: os.PathLike[str] | str) -> Lattice:
    root = Path(directory)
    manifest = _validate_manifest(yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8")))
    base = root.resolve()
    relations = {}
    for row in manifest["tables"]:
        path = root / row["path"]
        if not path.resolve().is_relative_to(base):
            raise ValueError("table path escapes the version directory")
        relations[row["table_id"]] = _read_table(path, row)
    lattice = _make_lattice({**manifest, "evidence": []}, relations)
    reports, approvals = _load_evidence(root, manifest, relations)
    return replace(lattice, evidence=reports, approvals=approvals)


def from_tables(
    relations: Mapping[str, Relation], *, manifest: Mapping[str, Any],
    evidence: tuple[Any, ...] = (),
) -> Lattice:
    validated = _validate_manifest(manifest, check_paths=False)
    expected_ids = {row["table_id"] for row in validated["tables"]}
    if set(relations) != expected_ids:
        raise ValueError("attached relation IDs must exactly match manifest table IDs")
    ordered = [relations[row["table_id"]] for row in validated["tables"]]
    if not all(isinstance(rel, Relation) for rel in ordered):
        raise TypeError("attached tables must be Mountainash Relation instances")
    with materialization_scope() as owner:
        captured = owner.capture(*ordered)
        owned = tuple(owner.detach(rel) for rel in captured)
    attached = dict(zip((row["table_id"] for row in validated["tables"]), owned))
    lattice = _make_lattice({**validated, "evidence": []}, attached)
    supplied_evidence = tuple(evidence)
    supplied_ids = {item.report_id for item in supplied_evidence}
    unavailable = tuple(
        UnavailableEvidenceReference(
            UUID(entry["report_id"]),
            tuple(sorted(entry["reference"].items()))
            if entry.get("kind") == "unavailable_evidence"
            else tuple(sorted((key, value) for key, value in entry.items() if key != "report_id")),
        )
        for entry in validated["evidence"]
        if entry.get("kind") in {"source_report", "artifact_report", "unavailable_evidence"}
        and UUID(entry["report_id"]) not in supplied_ids
    )
    retained_evidence = supplied_evidence + unavailable
    descriptors = tuple(replace(descriptor, capture_id=uuid4()) for descriptor in lattice.table_descriptors)
    approvals = tuple(
        Approval(UUID(entry["report_id"]), tuple(entry["finding_ids"]))
        for entry in validated["evidence"] if entry.get("kind") == "approval"
    )
    return replace(lattice, table_descriptors=descriptors, evidence=retained_evidence, approvals=approvals)
