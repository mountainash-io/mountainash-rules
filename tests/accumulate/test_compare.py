"""Explicitly scoped fingerprint comparisons over accumulator tables."""

import mountainash as ma
from dataclasses import replace

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.engines.accumulate.build import build
from mountainash_rules.engines.accumulate.compare import TableSelection, compare
from tests.conftest import build_backend_df


def _build(backend_name, amount):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "minimum": [0], "maximum": [10], "amount": [amount],
    }, "rules"))
    return build(rules, metadata)


def _build_rows(backend_name, uuids, amounts):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": uuids, "minimum": [0 + 20 * i for i in range(len(uuids))],
        "maximum": [10 + 20 * i for i in range(len(uuids))], "amount": amounts,
    }, "rules"))
    return build(rules, metadata)


def test_empty_scope_does_not_scan_and_selected_source_values_report_drift(backend_name):
    baseline = _build(backend_name, 10)
    current = _build(backend_name, 11)

    assert compare(baseline, current, scope=()) == ()
    source_table = next(d for d in baseline.table_descriptors if d.role == "source_rules")
    result, = compare(baseline, current, scope=(
        TableSelection(source_table.table_id, columns=("amount",)),
    ))

    assert result.status == "drift_detected"
    assert result.baseline is not None and result.current is not None
    assert result.baseline["row_count"].to_list() == [1, 1]
    assert result.current["row_count"].to_list() == [1, 1]


def test_scoped_columns_and_predicates_only_observe_selected_rows(backend_name):
    baseline = _build_rows(backend_name, ["A", "B"], [10, 20])
    current = _build_rows(backend_name, ["A", "B"], [10, 99])
    descriptor = next(d for d in baseline.table_descriptors if d.role == "source_rules")

    unchanged_slice, = compare(baseline, current, scope=(
        TableSelection(descriptor.table_id, columns=("amount",), where=ma.col("uuid").eq("A")),
    ))
    changed_column, = compare(baseline, current, scope=(
        TableSelection(descriptor.table_id, columns=("amount",)),
    ))
    untouched_column, = compare(baseline, current, scope=(
        TableSelection(descriptor.table_id, columns=("minimum",)),
    ))

    assert unchanged_slice.status == "no_drift_detected"
    assert unchanged_slice.baseline["row_count"].to_list() == [1, 1]
    assert changed_column.status == "drift_detected"
    assert untouched_column.status == "no_drift_detected"


def test_filter_entry_exit_and_unknown_selection_are_not_hidden(backend_name):
    baseline = _build_rows(backend_name, ["A"], [10])
    current = _build_rows(backend_name, ["A", "B"], [10, 20])
    descriptor = next(d for d in baseline.table_descriptors if d.role == "source_rules")

    entered_slice, = compare(baseline, current, scope=(
        TableSelection(descriptor.table_id, columns=("amount",), where=ma.col("uuid").eq("B")),
    ))

    assert entered_slice.status == "drift_detected"
    assert entered_slice.baseline["row_count"].to_list() == [0, 0]
    assert entered_slice.current["row_count"].to_list() == [1, 1]
    try:
        compare(baseline, current, scope=(
            TableSelection("missing-table", columns=("amount",)),
        ))
    except KeyError as error:
        assert error.args == ("missing-table",)
    else:
        raise AssertionError("unknown baseline table selection must fail")


def test_all_scope_reports_current_only_descriptors(backend_name):
    baseline = _build(backend_name, 10)
    current = _build(backend_name, 10)
    removed = next(d for d in baseline.table_descriptors if d.role == "source_state")
    baseline = replace(
        baseline,
        table_descriptors=tuple(d for d in baseline.table_descriptors if d != removed),
    )

    results = compare(baseline, current, scope="all")

    current_only = next(item for item in results if item.table_id == removed.table_id)
    assert current_only.status == "not_comparable"
    assert current_only.reason == "no matching baseline descriptor"
