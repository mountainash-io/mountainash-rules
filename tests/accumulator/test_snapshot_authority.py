"""Native snapshot authority and bounded legacy inspection regressions."""

from __future__ import annotations

import copy
import json

import polars as pl
import pytest

import mountainash_rules as rules
from mountainash_rules import Dimension, DimensionsMetadata, Lattice
from tests.accumulator.source_analysis_fixtures import limits
from tests.accumulator.test_lattice import (
    _manifest,
    _refresh_file_digest,
    _source_gated_lattice,
)


def _write_validation(path, validation):
    (path / "validation.json").write_text(
        json.dumps(validation, separators=(",", ":"), ensure_ascii=False, default=dict),
        encoding="utf-8",
    )
    manifest = _manifest(path)
    manifest["binding_ids"] = [binding["id"] for binding in validation["bindings"]]
    _refresh_file_digest(path, manifest, "validation.json")


def _report(payload, limits):
    envelope = rules.make_exact_envelope("report", payload, limits=limits)
    return json.loads(
        json.dumps({"id": envelope["id"], **envelope["payload"]}, default=dict)
    )


def _unbound_validation(path, limits):
    validation = json.loads((path / "validation.json").read_text(encoding="utf-8"))
    validation["bindings"] = []
    compiled = next(
        report for report in validation["reports"] if report["stage"] == "compiled"
    )
    return validation, compiled


def _replace_compiled(validation, original, replacement):
    validation["reports"] = sorted(
        [
            replacement if report["id"] == original["id"] else report
            for report in validation["reports"]
        ],
        key=lambda report: report["id"],
    )


def _unsupported_version(validation, compiled, limits):
    payload = copy.deepcopy(compiled)
    payload.pop("id")
    payload["validator"]["semantic_version"] = "compiled-analysis-unknown"
    _replace_compiled(validation, compiled, _report(payload, limits))


def _extra_catalogue_entry(validation, compiled, limits):
    payload = copy.deepcopy(compiled)
    payload.pop("id")
    payload["checks"].append(
        {
            "check_id": "unexpected_compiled_check",
            "scope": copy.deepcopy(payload["scope"]),
            "status": "passed",
            "complete": True,
            "finding_ids": [],
        }
    )
    payload["checks"].sort(key=lambda check: check["check_id"])
    _replace_compiled(validation, compiled, _report(payload, limits))


def _incomplete_check(validation, compiled, limits):
    payload = copy.deepcopy(compiled)
    payload.pop("id")
    payload["checks"][0].update(
        {"status": "resource_exhausted", "complete": False, "finding_ids": []}
    )
    _replace_compiled(validation, compiled, _report(payload, limits))


def _dirty_compiled_report(validation, compiled, limits):
    payload = copy.deepcopy(compiled)
    payload.pop("id")
    finding_payload = {
        "schema_version": 1,
        "analysis_input_id": payload["analysis_input_id"],
        "stage": "compiled",
        "check_id": "cell_nonempty",
        "code": "invalid_source",
        "severity": "error",
        "scope": copy.deepcopy(payload["scope"]),
        "source_ids": [],
        "cell_ids": [],
        "region_predicate_id": None,
        "witnesses": [],
        "witnesses_complete": True,
    }
    envelope = rules.make_exact_envelope("finding", finding_payload, limits=limits)
    finding = {"id": envelope["id"], **envelope["payload"]}
    validation["findings"] = sorted(
        [*validation["findings"], finding], key=lambda item: item["id"]
    )
    check = next(
        check for check in payload["checks"] if check["check_id"] == "cell_nonempty"
    )
    check.update(
        {"status": "findings", "complete": True, "finding_ids": [finding["id"]]}
    )
    payload["finding_ids"] = [finding["id"]]
    _replace_compiled(validation, compiled, _report(payload, limits))


@pytest.mark.parametrize(
    "mutate",
    [
        _unsupported_version,
        _extra_catalogue_entry,
        _incomplete_check,
        _dirty_compiled_report,
    ],
    ids=["version", "catalogue", "incomplete", "dirty"],
)
def test_load_rejects_rehashed_unbound_compiled_authority(tmp_path, mutate):
    """A structurally coherent snapshot cannot self-authorize a bad report."""
    built = _source_gated_lattice()
    path = built["lattice"].save(tmp_path / "snapshot", limits=built["limits"])
    validation, compiled = _unbound_validation(path, built["limits"])
    mutate(validation, compiled, built["limits"])
    _write_validation(path, validation)

    with pytest.raises(ValueError):
        Lattice.load(path, limits=built["limits"])


def test_validation_checksum_precedes_authority_replay(tmp_path):
    """Unauthenticated evidence bytes never reach semantic report admission."""
    built = _source_gated_lattice()
    path = built["lattice"].save(tmp_path / "snapshot", limits=built["limits"])
    with (path / "validation.json").open("ab") as stream:
        stream.write(b" ")

    with pytest.raises(ValueError, match="checksum mismatch"):
        Lattice.load(path, limits=built["limits"])


def test_flat_snapshot_writes_and_restores_its_actual_bounded_schema(tmp_path):
    """Legacy inspection snapshots supply their real physical columns to transport."""
    flat = Lattice(
        pl.DataFrame({"rule_name": ["legacy"], "rank": [1]}),
        DimensionsMetadata(dimensions=[Dimension(dimension_name="rule_name")]),
        [],
        None,
    )
    path = flat.save(tmp_path / "flat", limits=limits())

    assert Lattice.load(path, limits=limits()).combinations.to_dicts() == [
        {"rule_name": "legacy", "rank": 1}
    ]


def test_unbound_context_regex_snapshot_retains_its_guarded_domain(tmp_path):
    """Actual source-gated guard evidence remains loadable without a binding."""
    from tests.accumulator.source_analysis_fixtures import (
        gate,
        row,
        string_case,
    )

    kwargs, _ = string_case("context_regex")
    rows = [row(1, amount=10)]
    source_bundle = rules.analyze_sources(rows, **kwargs)
    validation = gate(rows, kwargs, source_bundle)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    lattice = engine.build(rows, validation=validation)
    path = lattice.save(tmp_path / "guarded", limits=kwargs["limits"])

    assert (
        Lattice.load(path, limits=kwargs["limits"]).artifact_id == lattice.artifact_id
    )


def test_boolean_context_key_snapshot_preserves_bound_routing(tmp_path):
    """The Boolean key codec retains a value route through native restore."""
    from tests.accumulator.test_lattice import TestBooleanAndR8Routing

    engine, lattices = TestBooleanAndR8Routing()._artifacts()
    lattice = next(
        item
        for item in lattices
        if item.partition_identity["key_values"][0]["match"]["kind"] == "value"
        and item.partition_key == {"flag": True}
    )
    path = lattice.save(tmp_path / "boolean", limits=engine._limits)

    assert Lattice.load(path, limits=engine._limits).partition_key == {"flag": True}


def test_key_origin_must_match_native_boolean_routing_value(tmp_path):
    """The native key columns cannot redirect a retained Boolean source origin."""
    from tests.accumulator.test_lattice import TestBooleanAndR8Routing

    engine, lattices = TestBooleanAndR8Routing()._artifacts()
    lattice = next(item for item in lattices if item.partition_key == {"flag": True})
    path = lattice.save(tmp_path / "boolean", limits=engine._limits)
    manifest = _manifest(path)
    value_column = manifest["source_schema"]["routing"][0]["value_column"]
    import pyarrow as pa
    import pyarrow.parquet as pq

    sources = pq.read_table(path / "sources.parquet")
    position = sources.schema.get_field_index(value_column)
    field = sources.schema.field(position)
    changed = sources.set_column(
        position, field, pa.array([False] * sources.num_rows, type=field.type)
    )
    pq.write_table(changed, path / "sources.parquet")
    _refresh_file_digest(path, manifest, "sources.parquet")

    with pytest.raises(ValueError, match="context-key origin"):
        Lattice.load(path, limits=engine._limits)
