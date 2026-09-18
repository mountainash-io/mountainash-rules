"""Tests for the Aggregate model and AggregateOp enum."""

import pytest
from pydantic import ValidationError

from mountainash_rules import Aggregate


class TestAggregateOp:
    def test_unknown_operation_rejected_at_construction(self):
        with pytest.raises(ValidationError):
            Aggregate(column_name="margin", operation="median")


def test_exact_declaration_preserves_distinct_output_identity():
    declaration = Aggregate(
        column_name="amount",
        output_name="charge.sum",
        data_type="float",
        numeric_semantics="numeric-1",
    )
    assert declaration.native_record() == {
        "column_name": "amount",
        "output_name": "charge.sum",
        "operation": "sum",
        "data_type": "float",
        "numeric_semantics": "numeric-1",
    }


def test_flat_declaration_cannot_acquire_native_semantics():
    declaration = Aggregate(column_name="amount")
    with pytest.raises(ValueError):
        declaration.native_record()


@pytest.mark.parametrize("output_name", ["amount", ".sum", "amount.", "a..sum"])
def test_output_identity_requires_nonempty_namespace_segments(output_name):
    with pytest.raises(ValueError):
        Aggregate(
            column_name="amount",
            output_name=output_name,
            data_type="int",
            numeric_semantics="numeric-1",
        )


def test_exact_aggregate_type_and_timezone_are_not_inferred():
    with pytest.raises(ValueError):
        Aggregate(
            column_name="when",
            operation="min",
            output_name="time.min",
            data_type="datetime",
            numeric_semantics="numeric-1",
        )
    with pytest.raises(ValueError):
        Aggregate(
            column_name="flag",
            data_type="bool",
            output_name="flag.sum",
            numeric_semantics="numeric-1",
        )
    with pytest.raises(ValueError):
        Aggregate(column_name="amount", nullable=True)


def test_native_declarations_allow_reused_inputs_not_duplicate_outputs():
    from mountainash_rules.engines.accumulator.aggregate import validate_aggregates

    declarations = [
        Aggregate(
            column_name="amount",
            operation=op,
            output_name=f"charge.{op}",
            data_type="int",
            numeric_semantics="numeric-1",
        )
        for op in ("sum", "max")
    ]
    assert tuple(validate_aggregates(declarations)) == tuple(declarations)
    with pytest.raises(ValueError):
        validate_aggregates([declarations[0], declarations[0]])
    with pytest.raises(ValueError):
        validate_aggregates(
            [
                declarations[0],
                Aggregate(
                    column_name="amount",
                    operation="min",
                    output_name="charge.min",
                    data_type="float",
                    numeric_semantics="numeric-1",
                ),
            ]
        )


def test_lineage_retains_each_output_source_pair_and_missing_labels():
    import polars as pl
    from mountainash.relations import relation
    from mountainash_rules.engines.accumulator.aggregate import lineage_relation

    sources = relation(
        pl.DataFrame({"source_id": ["a", "b"], "source_label": ["base", None]})
    )
    declarations = [
        Aggregate(
            column_name="amount",
            operation=op,
            output_name=f"charge.{op}",
            data_type="int",
            numeric_semantics="numeric-1",
        )
        for op in ("sum", "max")
    ]
    rows = (
        lineage_relation(declarations, sources)
        .to_polars()
        .sort("output_name", "source_id")
    )
    assert rows.rows() == [
        ("charge.max", "amount", "max", "a", "base"),
        ("charge.max", "amount", "max", "b", None),
        ("charge.sum", "amount", "sum", "a", "base"),
        ("charge.sum", "amount", "sum", "b", None),
    ]
    empty = lineage_relation([], sources).to_polars()
    assert empty.rows() == []
    assert empty.schema == rows.schema


def test_partial_native_declaration_cannot_enter_legacy_build_path():
    for fields in (
        {"output_name": "charge.sum"},
        {"data_type": "int"},
        {"numeric_semantics": "numeric-1"},
    ):
        with pytest.raises(ValueError):
            Aggregate(column_name="amount", **fields)
