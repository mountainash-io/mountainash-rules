from decimal import Decimal

import ibis
import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.prevalidate import DataContractError, prevalidate


def _relation(backend_name, data, table_name):
    frame = (
        pl.DataFrame(data)
        if backend_name == "polars"
        else ibis.duckdb.connect().create_table(table_name, data, overwrite=True)
    )
    return relation(frame)


def _breaches(backend_name, data, metadata, table_name):
    with pytest.raises(DataContractError) as caught:
        prevalidate(_relation(backend_name, data, table_name), metadata)
    return caught.value.breaches


def _findings(backend_name, data, metadata, table_name):
    return {
        finding.kind: finding
        for finding in _breaches(backend_name, data, metadata, table_name)
    }


def _region_metadata(*, outputs=(), scope_column=None):
    return DimensionsMetadata(
        uuid_column="rule_id",
        scope_column=scope_column,
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(
            Dimension(
                dimension_name="region",
                context_field="region",
                rule_field="region",
            ),
        ),
        outputs=outputs,
    )


def _result_output(scope=None, column="result"):
    return Aggregate(
        scope=scope,
        output_name="result",
        contribution_column=column,
        data_type=DataType.INT,
    )


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_normalized_signatures_report_duplicate_and_conflict_groups(backend_name):
    metadata = _region_metadata(outputs=(_result_output(),))

    findings = _findings(
        backend_name,
        {
            "rule_id": ["duplicate-a", "duplicate-b", "conflict-a", "conflict-b"],
            "region": ["EU", "EU", "US", "US"],
            "result": [1, 1, 2, 3],
        },
        metadata,
        "signature_duplicate_conflict",
    )

    assert findings["duplicate_rule"].rule_uuids == ("duplicate-a", "duplicate-b")
    assert findings["conflicting_rule"].rule_uuids == ("conflict-a", "conflict-b")


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_signature_groups_include_each_uuid_once(backend_name):
    metadata = _region_metadata(outputs=(_result_output(),))

    findings = _findings(
        backend_name,
        {
            "rule_id": ["repeated-a", "repeated-b", "repeated-c", "new-output"],
            "region": ["EU", "EU", "EU", "EU"],
            "result": [1, 1, 1, 2],
        },
        metadata,
        "signature_every_uuid",
    )

    assert findings["duplicate_rule"].rule_uuids == (
        "repeated-a",
        "repeated-b",
        "repeated-c",
    )
    assert findings["conflicting_rule"].rule_uuids == (
        "new-output",
        "repeated-a",
        "repeated-b",
        "repeated-c",
    )


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_signature_scope_isolation_and_multi_output_conflict(backend_name):
    scoped = _region_metadata(
        scope_column="scope",
        outputs=(_result_output("one", "one_result"), _result_output("two", "two_result")),
    )

    assert prevalidate(
        _relation(
            backend_name,
            {
                "rule_id": ["one", "two"],
                "scope": ["one", "two"],
                "region": ["EU", "EU"],
                "one_result": [1, 99],
                "two_result": [99, 1],
            },
            "signature_scope_isolation",
        ),
        scoped,
    ).warnings == ()

    metadata = _region_metadata(
        outputs=(
            _result_output(column="first"),
            Aggregate(output_name="second", contribution_column="second", data_type=DataType.INT),
        )
    )
    findings = _findings(
        backend_name,
        {
            "rule_id": ["left", "right"],
            "region": ["EU", "EU"],
            "first": [1, 2],
            "second": [3, 4],
        },
        metadata,
        "signature_multi_output_conflict",
    )

    assert set(findings) == {"conflicting_rule"}
    assert findings["conflicting_rule"].rule_uuids == ("left", "right")


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_signature_uses_only_declared_dimensions_and_outputs(backend_name):
    metadata = _region_metadata(outputs=(_result_output(),))
    findings = _findings(
        backend_name,
        {
            "rule_id": ["duplicate-a", "duplicate-b", "dimension-difference", "output-difference"],
            "region": ["EU", "EU", "US", "EU"],
            "result": [1, 1, 1, 2],
            "notes": ["first", "second", "third", "fourth"],
        },
        metadata,
        "signature_declared_only",
    )

    assert set(findings) == {"duplicate_rule", "conflicting_rule"}
    assert findings["duplicate_rule"].rule_uuids == ("duplicate-a", "duplicate-b")
    assert findings["conflicting_rule"].rule_uuids == (
        "duplicate-a",
        "duplicate-b",
        "output-difference",
    )


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_signature_canonicalizes_set_order_repetition_and_wildcard_payload(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(
            Dimension(
                dimension_name="regions",
                context_field="region",
                rule_field="regions",
                match_strategy=MatchStrategy.SET_MEMBERSHIP,
            ),
        ),
        outputs=(_result_output(),),
    )

    findings = _breaches(
        backend_name,
        {
            "rule_id": ["set-a", "set-b", "wildcard-dirty", "wildcard-clean", "conflict"],
            "regions": [["EU", "US", "EU"], ["US", "EU"], ["*", "EU"], ["*"], ["EU", "US"]],
            "result": [1, 1, 2, 2, 3],
        },
        metadata,
        "signature_canonical_set",
    )

    assert {
        finding.rule_uuids
        for finding in findings
        if finding.kind == "duplicate_rule"
    } == {
        ("set-a", "set-b"),
        ("wildcard-clean", "wildcard-dirty"),
    }
    assert {
        finding.rule_uuids
        for finding in findings
        if finding.kind == "conflicting_rule"
    } == {("conflict", "set-a", "set-b")}


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_signature_uses_declared_decimal_scale(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(
            ContextField(
                name="amount",
                data_type=DataType.DECIMAL,
                precision=8,
                scale=2,
                rounding="half_to_even",
                wildcard=Decimal("-99.99"),
            ),
        ),
        dimensions=(
            Dimension(dimension_name="amount", context_field="amount", rule_field="amount"),
        ),
        outputs=(_result_output(),),
    )

    findings = _findings(
        backend_name,
        {
            "rule_id": ["same-scale-a", "same-scale-b", "different-scale"],
            "amount": [Decimal("1.001"), Decimal("1.00"), Decimal("1.01")],
            "result": [1, 1, 1],
        },
        metadata,
        "signature_decimal_scale",
    )

    assert set(findings) == {"duplicate_rule"}
    assert findings["duplicate_rule"].rule_uuids == ("same-scale-a", "same-scale-b")


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_mixed_normalized_signature_fixture_has_expected_uuid_groups(backend_name):
    metadata = _region_metadata(outputs=(_result_output(),))
    findings = _findings(
        backend_name,
        {
            "rule_id": ["duplicate-a", "duplicate-b", "conflict-a", "conflict-b", "distinct"],
            "region": ["EU", "EU", "US", "US", "APAC"],
            "result": [1, 1, 4, 5, 6],
            "notes": ["one", "two", "three", "four", "five"],
        },
        metadata,
        "signature_mixed",
    )

    assert {
        kind: finding.rule_uuids for kind, finding in findings.items()
    } == {
        "duplicate_rule": ("duplicate-a", "duplicate-b"),
        "conflicting_rule": ("conflict-a", "conflict-b"),
    }


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_presence_column_on_int_field_signs_as_text(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="n", data_type=DataType.INT, wildcard=-1),),
        dimensions=(
            Dimension(
                dimension_name="presence",
                context_field="n",
                rule_field="presence",
                match_strategy=MatchStrategy.PRESENCE,
            ),
        ),
        outputs=(_result_output(),),
    )
    data = {
        "rule_id": ["dup-1", "dup-2", "conflict", "other"],
        "presence": ["IS_NULL", "IS_NULL", "IS_NULL", "IS_NOT_NULL"],
        "result": [1, 1, 2, 1],
    }
    findings = _findings(backend_name, data, metadata, "presence_int")
    assert findings["duplicate_rule"].rule_uuids == ("dup-1", "dup-2")
    assert findings["conflicting_rule"].rule_uuids == ("conflict", "dup-1", "dup-2")

    clean = {"rule_id": ["a", "b"], "presence": ["IS_NULL", "IS_NOT_NULL"], "result": [1, 1]}
    assert prevalidate(_relation(backend_name, clean, "presence_clean"), metadata).warnings == ()
