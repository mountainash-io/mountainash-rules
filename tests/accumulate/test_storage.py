"""Immutable accumulator bundle transport and attachment behavior."""

from dataclasses import replace

import yaml
import pytest

import ibis
import mountainash as ma
from mountainash import DecimalDtype, MountainashDtype, materialization_scope
from mountainash.typespec import TypeSpec

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.engines.accumulate.build import build
from mountainash_rules.engines.accumulate.validate import validate
from tests.conftest import build_backend_df
from mountainash_rules.engines.accumulate import manifest


def _build(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15], "amount": [10, 5],
    }, "rules"))
    return build(rules, metadata)



def test_manifest_schema_preserves_canonical_physical_types():
    schema = (
        ("string", MountainashDtype.STRING),
        ("int64", MountainashDtype.I64),
        ("uint32", MountainashDtype.U32),
        ("float32", MountainashDtype.FP32),
        ("float64", MountainashDtype.FP64),
        ("boolean", MountainashDtype.BOOL),
        ("date", MountainashDtype.DATE),
        ("timestamp", MountainashDtype.TIMESTAMP),
        ("decimal", DecimalDtype(10, 2)),
    )
    descriptor = manifest._schema_descriptor(schema)
    spec = TypeSpec.from_frictionless(descriptor)
    fields = {field["name"]: field for field in descriptor["fields"]}

    assert manifest._schema_from_spec(spec) == schema
    assert manifest._matches_schema(spec, schema)

    assert {name: field["type"] for name, field in fields.items()} == {
        "string": "string",
        "int64": "integer",
        "uint32": "integer",
        "float32": "number",
        "float64": "number",
        "boolean": "boolean",
        "date": "date",
        "timestamp": "datetime",
        "decimal": "number",
    }
    assert fields["decimal"]["x-mountainash"]["dtype"] == {
        "kind": "decimal", "precision": 10, "scale": 2,
    }


def test_saved_version_reloads_stored_geometry_and_typed_outputs(backend_name, tmp_path):
    original = _build(backend_name)
    destination = tmp_path / "version"

    original.save(destination)
    restored = type(original).load(destination)

    assert restored.geometry_namespace == original.geometry_namespace
    assert restored.version_id == original.version_id
    assert restored.metadata.model_dump(mode="json") == original.metadata.model_dump(mode="json")
    assert restored.cells.schema == original.cells.schema
    assert sorted(row["v0001"] for row in restored.outputs["default"].to_dicts()) == [5, 10, 15]
    assert sorted(row["uuid"] for row in restored.sources.rules.select("uuid").to_dicts()) == ["A", "B"]


@pytest.mark.parametrize("geometry", ["ordered", "categorical", "mixed_alias"])
def test_transport_uses_physical_context_fields(backend_name, geometry, tmp_path):
    from tests.accumulate.test_resolve import payload_values

    dimensions = [Dimension(
        dimension_name="age_rule", context_field="age", data_type="int",
        **(
            {"match_strategy": "range", "range_min_field": "lo", "range_max_field": "hi"}
            if geometry == "ordered" else {"rule_field": "category"}
        ),
    )]
    if geometry == "mixed_alias":
        dimensions.append(Dimension(
            dimension_name="age_alias", context_field="age", data_type="int",
            match_strategy="range", range_min_field="lo", range_max_field="hi",
        ))
    metadata = DimensionsMetadata(
        uuid_field="uuid", dimensions=dimensions,
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "lo": [0], "hi": [10], "category": [2], "amount": [7],
    }))
    original = build(rules, metadata)
    destination = tmp_path / "physical-fields"
    original.save(destination)
    manifest_data = yaml.safe_load((destination / "manifest.yaml").read_text())
    restored = type(original).load(destination)
    attached = type(original).from_tables(original.tables, manifest=manifest_data)
    contexts = ma.relation(build_backend_df(backend_name, {"request_id": [1, 2], "age": [2, 20]}))
    for lattice in (restored, attached):
        assert payload_values(lattice.resolve(contexts), output="amount") == [
            {"request_id": 1, "value": 7}, {"request_id": 2, "value": None},
        ]



def test_manifest_requires_integer_cell_identity_version(backend_name, tmp_path):
    original = _build(backend_name)
    destination = tmp_path / "without-cell-identity"
    original.save(destination)
    manifest_path = destination / "manifest.yaml"
    stored_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cells = next(item for item in stored_manifest["tables"] if item["role"] == "cells")
    fields = cells["schema"]["fields"]
    cells["schema"]["fields"] = [
        field for field in fields if field["name"] != "identity_version"
    ]
    manifest_path.write_text(yaml.safe_dump(stored_manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="cells schema must declare an integer identity_version"):
        type(original).load(destination)


def test_from_tables_rejects_unsupported_cell_identity_version(backend_name, tmp_path):
    original = _build(backend_name)
    destination = tmp_path / "cell-identity"
    original.save(destination)
    stored_manifest = yaml.safe_load(
        (destination / "manifest.yaml").read_text(encoding="utf-8")
    )
    cells_descriptor = next(item for item in original.table_descriptors if item.role == "cells")
    cells = original.tables[cells_descriptor.table_id]
    changed_cells = cells.with_columns(
        ma.lit(2).cast(cells.schema["identity_version"]).alias("identity_version")
    )
    tables = dict(original.tables)
    tables[cells_descriptor.table_id] = changed_cells

    with pytest.raises(ValueError, match="unsupported identity_version"):
        type(original).from_tables(tables, manifest=stored_manifest)

def test_from_tables_captures_attached_relations_under_new_capture_ids(backend_name, tmp_path):
    original = _build(backend_name)
    destination = tmp_path / "version"
    original.save(destination)
    manifest = yaml.safe_load((destination / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["tables"][0]["path"] = "../ignored.parquet"

    attached = type(original).from_tables(original.tables, manifest=manifest)

    assert attached.version_id == original.version_id
    assert attached.geometry_namespace == original.geometry_namespace
    assert {d.table_id: d.capture_id for d in attached.table_descriptors} != {
        d.table_id: d.capture_id for d in original.table_descriptors
    }
    assert sorted(row["v0001"] for row in attached.outputs["default"].to_dicts()) == [5, 10, 15]


def test_from_tables_preserves_report_findings_or_marks_missing_reports_unavailable(backend_name, tmp_path):
    original = _build(backend_name)
    output_descriptor = next(item for item in original.table_descriptors if item.role == "outputs")
    output_dtype = original.outputs["default"].schema["v0001"]
    changed_output = original.outputs["default"].with_columns(ma.lit(999).cast(output_dtype).alias("v0001"))
    tables = dict(original.tables)
    tables[output_descriptor.table_id] = changed_output
    corrupted = replace(original, tables=tables)
    report = validate(corrupted, original.sources.rules)
    assert report.findings.count_rows() > 0

    destination = tmp_path / "with-report"
    replace(corrupted, evidence=(report,)).save(destination)
    manifest = yaml.safe_load((destination / "manifest.yaml").read_text(encoding="utf-8"))

    without_report = type(original).from_tables(corrupted.tables, manifest=manifest)
    assert without_report.outputs["default"].count_rows() == corrupted.outputs["default"].count_rows()
    unavailable = without_report.evidence[0]
    assert unavailable.report_id == report.report_id
    assert dict(unavailable.reference_metadata) == {
        "kind": "artifact_report", "path": f"evidence/{report.report_id}/report.yaml",
    }

    reloaded_path = tmp_path / "unavailable"
    without_report.save(reloaded_path)
    reloaded = type(original).load(reloaded_path)
    assert reloaded.evidence[0].report_id == report.report_id
    assert reloaded.evidence[0].reference_metadata == unavailable.reference_metadata

    with_report = type(original).from_tables(
        corrupted.tables, manifest=manifest, evidence=(report,),
    )
    assert with_report.evidence[0] is report
    current_capture_ids = {item.capture_id for item in with_report.table_descriptors}
    assert with_report.evidence[0].findings.count_rows() > 0
    historical_refs = tuple(
        reference
        for references in report.binding.dependencies.values()
        for reference in references
    )
    assert historical_refs
    assert not {reference.capture_id for reference in historical_refs} & current_capture_ids


def test_evidence_binding_mismatch_restores_dependency_as_historical(backend_name, tmp_path):
    original = _build(backend_name)
    report = validate(original, original.sources.rules)
    selected_group = next(
        group for group, references in report.binding.dependencies.items()
        if any(reference.table_id is not None for reference in references)
    )
    selected_reference = next(
        reference for reference in report.binding.dependencies[selected_group]
        if reference.table_id is not None
    )
    dependencies = dict(report.binding.dependencies)
    dependencies[selected_group] = tuple(
        replace(reference, keys=("mismatched-key",))
        if reference is selected_reference else reference
        for reference in dependencies[selected_group]
    )
    stale_report = replace(
        report, binding=replace(report.binding, dependencies=dependencies),
    )
    destination = tmp_path / "mismatched-binding"
    replace(original, evidence=(stale_report,)).save(destination)

    restored = type(original).load(destination)
    references = tuple(
        reference
        for group in restored.evidence[0].binding.dependencies.values()
        for reference in group
    )
    historical = next(reference for reference in references if reference.keys == ("mismatched-key",))
    assert historical.relation is None
    assert historical.table_id is None


def test_failed_bundle_write_removes_staging_directory_and_keeps_target_absent(
    backend_name, tmp_path, monkeypatch,
):
    original = _build(backend_name)
    destination = tmp_path / "failed"

    def fail_write(*args):
        raise OSError("injected serialization failure")

    monkeypatch.setattr(manifest, "_write_relation", fail_write)
    with pytest.raises(OSError, match="injected serialization failure"):
        original.save(destination)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_existing_bundle_is_not_overwritten_after_failed_publish(backend_name, tmp_path):
    original = _build(backend_name)
    destination = tmp_path / "version"
    original.save(destination)
    first = type(original).load(destination)

    with pytest.raises(FileExistsError):
        original.save(destination)

    retained = type(original).load(destination)
    assert retained.version_id == first.version_id
    assert sorted(row["v0001"] for row in retained.outputs["default"].to_dicts()) == [5, 10, 15]


def test_database_attachment_retains_a_snapshot_after_external_table_edit(tmp_path):
    original = _build("polars")
    destination = tmp_path / "database-source"
    original.save(destination)
    manifest_data = yaml.safe_load((destination / "manifest.yaml").read_text(encoding="utf-8"))
    connection = ibis.duckdb.connect()
    try:
        relations = {
            descriptor.table_id: ma.relation(connection.create_table(
                descriptor.table_id, original.tables[descriptor.table_id].collect(),
            ))
            for descriptor in original.table_descriptors
        }
        attached = type(original).from_tables(relations, manifest=manifest_data)
        output_descriptor = next(item for item in attached.table_descriptors if item.role == "outputs")
        connection.raw_sql(
            f'UPDATE "{output_descriptor.table_id}" SET "v0001" = 999',
        )

        assert sorted(row["v0001"] for row in attached.outputs["default"].to_dicts()) == [5, 10, 15]
        assert attached.cells.count_rows() == original.cells.count_rows()
        assert attached.cell_boxes.count_rows() == original.cell_boxes.count_rows()
        assert sorted(
            row["source_uuid"] for row in attached.contributors.select("source_uuid").to_dicts()
        ) == sorted(row["source_uuid"] for row in original.contributors.select("source_uuid").to_dicts())
        assert sorted(
            row["uuid"] for row in attached.sources.rules.select("uuid").to_dicts()
        ) == sorted(row["uuid"] for row in original.sources.rules.select("uuid").to_dicts())
    finally:
        connection.disconnect()
