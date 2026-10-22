from dataclasses import FrozenInstanceError

import ibis
import polars as pl
import pytest
from mountainash.relations import relation

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.prevalidate import ValidatedRules, prevalidate


def metadata():
    return DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(
            Dimension(
                dimension_name="region",
                context_field="region",
                rule_field="region",
                match_strategy=MatchStrategy.EXACT,
            ),
        ),
    )


def test_prevalidate_captures_rules_and_owns_frozen_metadata():
    source = relation({"rule_id": ["r1"], "region": ["EU"], "notes": ["kept"]})
    original = metadata()

    validated = prevalidate(source, original)

    assert isinstance(validated, ValidatedRules)
    assert validated.rules.to_dicts() == source.to_dicts()
    assert validated.source_metadata == original
    assert validated.source_metadata is not original
    with pytest.raises(FrozenInstanceError):
        validated.rules = source


def test_prevalidate_aggregates_identity_value_and_duplicate_breaches():
    source = relation(
        {
            "rule_id": ["same", "same"],
            "region": [None, None],
        }
    )

    with pytest.raises(Exception) as caught:
        prevalidate(source, metadata())

    findings = {finding.kind for finding in caught.value.breaches}
    assert {"duplicate_uuid", "null_rule_cell", "duplicate_rule"} <= findings

def test_prevalidate_reports_duplicate_and_conflicting_normalized_rules():
    source = relation(
        {
            "rule_id": ["duplicate-1", "duplicate-2", "conflict"],
            "region": ["EU", "EU", "EU"],
            "contribution": [1, 1, 2],
        }
    )
    declared = metadata().model_copy(
        update={
            "outputs": (
                Aggregate(
                    output_name="result",
                    contribution_column="contribution",
                    data_type=DataType.INT,
                ),
            ),
        }
    )

    with pytest.raises(Exception) as caught:
        prevalidate(source, declared)

    findings = {finding.kind: finding for finding in caught.value.breaches}
    assert findings["duplicate_rule"].rule_uuids == ("duplicate-1", "duplicate-2")
    assert findings["conflicting_rule"].rule_uuids == ("conflict", "duplicate-1", "duplicate-2")


def test_presence_with_concrete_value_is_a_nonblocking_unsatisfiable_warning():
    declared = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(
            Dimension(dimension_name="region", context_field="region", rule_field="region"),
            Dimension(
                dimension_name="region_presence",
                context_field="region",
                rule_field="region_presence",
                match_strategy=MatchStrategy.PRESENCE,
            ),
        ),
    )

    validated = prevalidate(
        relation({"rule_id": ["r1"], "region": ["EU"], "region_presence": ["IS_NULL"]}),
        declared,
    )

    assert [(finding.kind, finding.rule_uuids) for finding in validated.warnings] == [
        ("unsatisfiable_rule", ("r1",))
    ]


@pytest.mark.parametrize("backend_name", ["polars", "ibis-duckdb"])
def test_prevalidate_preserves_undeclared_columns_on_supported_backends(backend_name):
    data = {"rule_id": ["r1"], "region": ["EU"], "authored_note": ["retained"]}
    source = relation(
        pl.DataFrame(data)
        if backend_name == "polars"
        else ibis.duckdb.connect().create_table("authored_rules", data, overwrite=True)
    )

    validated = prevalidate(source, metadata())

    assert validated.rules.to_dicts() == source.to_dicts()
