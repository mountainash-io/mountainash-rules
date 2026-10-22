from __future__ import annotations

import mountainash as ma
import mountainash.expressions as ma_expr
import pytest

from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.dimension import DimensionsMetadata
from mountainash_rules.decision_table import DecisionTableError
from mountainash_rules.decision_table.folds import fold_contributions
from mountainash_rules.decision_table.helpers import check_sources, select_scopes


def scoped_metadata():
    aggregate = Aggregate(column_name="fee", output_name="fee", data_type="int")
    return DimensionsMetadata(
        dimensions=[],
        uuid_field="rule_id",
        outcome_scope_field="scope",
        outcome_scopes={"rates": [aggregate], "fees": [aggregate]},
    )


def test_select_scopes_omitted_returns_all_declared_scopes(backend_name):
    assert select_scopes(scoped_metadata(), None) == ("rates", "fees")


def test_select_scopes_returns_selected_existing_scopes(backend_name):
    assert select_scopes(scoped_metadata(), ["fees"]) == ("fees",)


def test_select_scopes_empty_returns_empty_tuple(backend_name):
    assert select_scopes(scoped_metadata(), []) == ()


def test_select_scopes_preserves_requested_order(backend_name):
    assert select_scopes(scoped_metadata(), ["fees", "rates"]) == ("fees", "rates")


def test_select_scopes_rejects_unknown_scope(backend_name):
    with pytest.raises(DecisionTableError):
        select_scopes(scoped_metadata(), ["unknown"])


def test_ma_column_expression_is_usable(backend_name):
    assert ma_expr.col("amount") is not None


def test_check_sources_rejects_missing_source_identity(backend_name):
    rules = ma.relation({"amount": [1]})
    with pytest.raises(DecisionTableError):
        check_sources(rules, scoped_metadata())


@pytest.mark.parametrize(
    "operation, expected", [(AggregateOp.SUM, 7), (AggregateOp.MIN, 2), (AggregateOp.MAX, 5)]
)
def test_fold_contributions_once_per_rule_and_cell(backend_name, operation, expected):
    contributions = ma.relation({"cell": ["c1", "c1", "c2"], "a0001": [2, 5, 11]})
    aggregate = Aggregate(
        column_name="a0001", output_name="fee", data_type="int", operation=operation
    )
    result = fold_contributions(contributions, [aggregate], ["cell"])
    assert result.filter(ma_expr.col("cell") == "c1").item("fee") == expected


def test_fold_cells_are_independent_and_outputs_use_output_name(backend_name):
    contributions = ma.relation({"cell": ["c1", "c1", "c2"], "a0001": [2, 5, 11]})
    aggregate = Aggregate(
        column_name="a0001", output_name="fee", data_type="int", operation=AggregateOp.SUM
    )
    result = fold_contributions(contributions, [aggregate], ["cell"])
    assert result.filter(ma_expr.col("cell") == "c1").item("fee") == 7
    assert result.filter(ma_expr.col("cell") == "c2").item("fee") == 11
    assert "fee" in result.columns
