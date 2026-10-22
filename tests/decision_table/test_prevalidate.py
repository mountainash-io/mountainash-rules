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


def test_prevalidate_aggregates_scope_and_contribution_breaches():
    declared = metadata().model_copy(
        update={
            "scope_column": "scope",
            "outputs": (
                Aggregate(
                    scope="fees",
                    output_name="fee",
                    contribution_column="fee_contribution",
                    data_type=DataType.INT,
                ),
            ),
        }
    )
    source = relation(
        {
            "rule_id": [None, "r2"],
            "scope": [None, "unknown"],
            "region": ["EU", "US"],
            "fee_contribution": [None, "wrong"],
        }
    )

    with pytest.raises(Exception) as caught:
        prevalidate(source, declared)

    assert {
        finding.kind for finding in caught.value.breaches
    } >= {"missing_uuid", "null_scope", "undeclared_scope"}


def test_prevalidate_rejects_null_list_elements_and_invalid_ternary_authoring():
    declared = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="active", data_type=DataType.BOOL, wildcard=False),),
        dimensions=(
            Dimension(
                dimension_name="active_set",
                context_field="active",
                rule_field="active_set",
                match_strategy=MatchStrategy.SET_MEMBERSHIP,
            ),
            Dimension(
                dimension_name="presence",
                context_field="active",
                rule_field="presence",
                match_strategy=MatchStrategy.PRESENCE,
            ),
            Dimension(
                dimension_name="outcome",
                context_field="active",
                rule_field="outcome",
                match_strategy=MatchStrategy.OUTCOME,
            ),
        ),
    )
    source = relation(
        {
            "rule_id": ["r1"],
            "active_set": [[True, None]],
            "presence": ["NULL"],
            "outcome": [2],
        }
    )

    with pytest.raises(Exception) as caught:
        prevalidate(source, declared)

    assert {
        finding.kind for finding in caught.value.breaches
    } >= {"null_list_element", "invalid_presence_value", "invalid_outcome_value"}


def test_prevalidate_canonicalizes_set_order_and_decimal_scale_for_signatures():
    from decimal import Decimal

    declared = DimensionsMetadata(
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
            Dimension(
                dimension_name="amounts",
                context_field="amount",
                rule_field="amounts",
                match_strategy=MatchStrategy.SET_MEMBERSHIP,
            ),
        ),
    )
    source = relation(
        {
            "rule_id": ["r1", "r2"],
            "amounts": [
                [Decimal("1.001"), Decimal("2.00"), Decimal("2.00")],
                [Decimal("2.0"), Decimal("1.00")],
            ],
        }
    )

    with pytest.raises(Exception) as caught:
        prevalidate(source, declared)

    duplicate = next(finding for finding in caught.value.breaches if finding.kind == "duplicate_rule")
    assert duplicate.rule_uuids == ("r1", "r2")


def test_prevalidate_treats_a_wildcard_inside_a_set_as_ignoring_other_payload():
    declared = DimensionsMetadata(
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
    )
    source = relation(
        {
            "rule_id": ["dirty", "clean"],
            "regions": [["*", "EU"], ["*"]],
        }
    )

    with pytest.raises(Exception) as caught:
        prevalidate(source, declared)

    duplicate = next(finding for finding in caught.value.breaches if finding.kind == "duplicate_rule")
    assert duplicate.rule_uuids == ("clean", "dirty")
