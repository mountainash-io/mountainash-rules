"""Reusable normalized set payloads retain authored values and explicit state."""

from datetime import date

import pytest
from mountainash.relations import relation

from mountainash_rules import ContextField
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.core.constants import DataType
from mountainash_rules.core.compiler import normalize_rule_relation, rule_binding_names
from tests.conftest import build_backend_df


@pytest.mark.parametrize(
    "data_type,values",
    [
        ("str", ["UK", "NZ"]),
        ("float", [2.5, 1.5]),
        ("date", [date(2020, 1, 2), date(2020, 1, 1)]),
    ],
)
def test_normalized_set_payload_is_canonical_without_rewriting_authored_values(
    backend_name, data_type, values
):
    authored = [values + values[:1], [], None]
    rules = relation(
        build_backend_df(backend_name, {"rule": [0, 1, 2], "value": authored})
    )
    metadata = DimensionsMetadata(
        fields=(ContextField(name="value", data_type=DataType(data_type), wildcard=None, wildcard_declared=True),),
        dimensions=(Dimension(dimension_name="value", context_field="value", match_strategy="set_membership"),),
    )
    dim = metadata.dimensions[0]
    value_name, state_name = rule_binding_names(dim)
    rows = normalize_rule_relation(rules, [dim]).sort("rule").to_dicts()
    assert [row["value"] for row in rows] == authored
    assert [row[value_name] for row in rows] == [sorted(values), [], None]
    assert [row[state_name] for row in rows] == [False, False, True]
