"""Updates compare authored structure and preserve immutable historical bases."""

import mountainash as ma
import polars as pl
import pytest

from mountainash_rules import DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.engines.accumulate.sources import capture_sources
from tests.conftest import build_backend_df


def metadata():
    return DimensionsMetadata(uuid_field="uuid", dimensions=[], aggregates=[Aggregate(column_name="amount", data_type="int")])


def rules(backend, amounts=(2, 3), conditions=("a", "b")):
    return ma.relation(build_backend_df(backend, {"uuid": ["A", "B"], "condition": list(conditions), "amount": list(amounts)}))


def totals(lattice):
    return lattice.outputs["default"].select("v0001").to_dicts()


def test_full_payload_update_reuses_geometry_without_changing_historical_base(backend_name):
    from mountainash_rules.engines.accumulate.updates import classify_update
    from mountainash_rules.engines.accumulate.build import build

    declaration = metadata()
    base = build(rules(backend_name), declaration)
    result = classify_update(base, capture_sources(rules(backend_name, (4, 3)), declaration), "full")
    assert result.change == "payload_changed"
    assert result.lattice.geometry_namespace == base.geometry_namespace
    assert totals(base) == [{"v0001": 5}]
    assert totals(result.lattice) == [{"v0001": 7}]
    assert result.changed_sources.to_dicts() == [{"partition": "default", "scope": "default", "source_uuid": "A"}]
    unchanged = classify_update(result.lattice, capture_sources(rules(backend_name, (4, 3)), declaration), "full")
    assert unchanged.change == "unchanged"
    assert unchanged.lattice is result.lattice
    assert unchanged.changed_sources.to_dicts() == []


def test_full_authored_structure_change_returns_candidate_without_new_lattice(backend_name):
    from mountainash_rules.engines.accumulate.updates import classify_update
    from mountainash_rules.engines.accumulate.build import build

    declaration = metadata()
    base = build(rules(backend_name), declaration)
    result = classify_update(base, capture_sources(rules(backend_name, conditions=("changed", "b")), declaration), "full")
    assert result.change == "structure_changed"
    assert result.lattice is None
    assert result.candidate_rules.filter(ma.col("uuid") == "A").select("condition").to_dicts() == [{"condition": "changed"}]
    assert totals(base) == [{"v0001": 5}]


def test_patch_omission_keeps_other_rows_and_null_is_supplied_not_omitted(backend_name):
    from mountainash_rules.engines.accumulate.updates import classify_update
    from mountainash_rules.engines.accumulate.build import build

    base = build(rules(backend_name), metadata())
    patch = ma.relation(build_backend_df(backend_name, {
        "partition": ["default"], "scope": ["default"], "source_uuid": ["A"], "amount": [7],
    }))
    updated = classify_update(base, patch, "patch")
    assert updated.change == "payload_changed"
    assert totals(updated.lattice) == [{"v0001": 10}]
    assert updated.candidate_rules.select("uuid", "amount").sort("uuid").to_dicts() == [
        {"uuid": "A", "amount": 7}, {"uuid": "B", "amount": 3},
    ]
    null_patch = ma.relation(build_backend_df(backend_name, pl.DataFrame({
        "partition": ["default"], "scope": ["default"], "source_uuid": ["A"], "amount": [None],
    }, schema_overrides={"amount": pl.Int64})))
    with pytest.raises(ValueError):
        classify_update(base, null_patch, "patch")
    assert totals(base) == [{"v0001": 5}]


@pytest.mark.parametrize("malformed", ["unknown", "duplicate", "structural"])
def test_malformed_patch_cannot_change_or_rebase_its_receiver(backend_name, malformed):
    from mountainash_rules.engines.accumulate.updates import classify_update
    from mountainash_rules.engines.accumulate.build import build

    base = build(rules(backend_name), metadata())
    identifiers = ["unknown"] if malformed == "unknown" else (["A", "A"] if malformed == "duplicate" else ["A"])
    values = {"partition": ["default"] * len(identifiers), "scope": ["default"] * len(identifiers), "source_uuid": identifiers}
    values["condition" if malformed == "structural" else "amount"] = (["changed"] if malformed == "structural" else [7] * len(identifiers))
    with pytest.raises(ValueError):
        classify_update(base, ma.relation(build_backend_df(backend_name, values)), "patch")
    assert totals(base) == [{"v0001": 5}]
