"""Construction's named-value classification and application's context scoring share one operation.

For every named value of a field and every built entry on that value, the entry matches a
context carrying the value exactly when each pattern column it requires is classified 1 for the
value by ``classify_named_values``.
"""

from __future__ import annotations

import pytest

from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.build import build_decision_table
from mountainash_rules.decision_table.stages import classify_named_values

from tests.decision_table.apply_support import context_model, fetch
from tests.decision_table.construction_support import run_stages, validated_rules

M = MatchStrategy
Code = context_model(code=str)
VALUES = ["EU", "US", "EUROPE"]


@pytest.mark.parametrize(
    ("strategy", "patterns"),
    [
        (M.REGEX, ["^E", "S$"]),
        (M.PREFIX, ["E", "U"]),
        (M.SUFFIX, ["U", "S"]),
        (M.CONTAINS, ["EU", "S"]),
    ],
    ids=["regex", "prefix", "suffix", "contains"],
)
def test_built_entries_match_a_named_value_exactly_when_construction_classifies_it_into_them(
    backend_name, strategy, patterns
):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="code", data_type=DataType.STR, wildcard="*"),),
        dimensions=(
            Dimension(dimension_name="rx", context_field="code", match_strategy=strategy),
            Dimension(dimension_name="code", context_field="code", match_strategy=M.EXACT, rule_field="code_value"),
        ),
    )
    data = {
        "rule_id": [f"n{i}" for i in range(len(VALUES))] + [f"p{i}" for i in range(len(patterns))],
        "code_value": [*VALUES, *["*"] * len(patterns)],
        "rx": [*["*"] * len(VALUES), *patterns],
    }
    validated = validated_rules(backend_name, data, metadata)
    boundaries, _, _ = run_stages(validated)
    classified = {
        (row["value"], row["column"]): row["outcome"]
        for row in classify_named_values(
            boundaries.named_values["code"], boundaries.pattern_conditions, "code"
        ).to_dicts()
    }
    table = build_decision_table(validated)
    pattern_columns = [row["column"] for row in table.pattern_conditions.to_dicts()]
    entries = table.entries.to_dicts()

    for value in VALUES:
        expected = {
            entry["entry_id"]
            for entry in entries
            if entry["code"] == value
            and all(entry[column] == 0 or classified[(value, column)] == 1 for column in pattern_columns)
        }
        matched = {row["entry_id"] for row in fetch(table, [Code(request_id=1, code=value)]) if row["code"] == value}
        assert expected, value
        assert matched == expected, value
