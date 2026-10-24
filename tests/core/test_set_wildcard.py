"""Reusable normalized set payloads retain authored values and explicit state."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from mountainash import MountainashDtype
from mountainash.core.dtypes import DecimalDtype, dtype_family
from mountainash.relations import relation

from mountainash_rules import ContextField
from mountainash_rules.core.compiler import normalize_rule_relation, rule_binding_names
from mountainash_rules.core.constants import DataType
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from tests.conftest import build_backend_df


def set_dimension(field: ContextField) -> Dimension:
    metadata = DimensionsMetadata(
        fields=(field,),
        dimensions=(Dimension(dimension_name="value", context_field="value", match_strategy="set_membership"),),
    )
    return metadata.dimensions[0]


def normalize(backend_name, dim, authored):
    rules = relation(build_backend_df(backend_name, {"rule": list(range(len(authored))), "value": authored}))
    return normalize_rule_relation(rules, [dim]).sort("rule")


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
    field = ContextField(name="value", data_type=DataType(data_type), wildcard=None, wildcard_declared=True)
    dim = set_dimension(field)
    value_name, state_name = rule_binding_names(dim)
    rows = normalize(backend_name, dim, authored).to_dicts()
    assert [row["value"] for row in rows] == authored
    assert [row[value_name] for row in rows] == [sorted(values), [], None]
    assert [row[state_name] for row in rows] == [False, False, True]


def _declared(data_type, **extra):
    return ContextField(name="value", data_type=data_type, wildcard=None, wildcard_declared=True, **extra)


# data type -> (field, one scalar authored value, expected empty-set element)
NON_LIST_SET_CASES = {
    "str": (_declared(DataType.STR), "UK", MountainashDtype.STRING),
    "int": (_declared(DataType.INT), 7, MountainashDtype.I64),
    "float": (_declared(DataType.FLOAT), 2.5, MountainashDtype.FP64),
    "bool": (_declared(DataType.BOOL), True, MountainashDtype.BOOL),
    "date": (_declared(DataType.DATE), date(2020, 1, 1), MountainashDtype.DATE),
    "datetime": (
        _declared(DataType.DATETIME, datetime_unit="us", timezone="naive"),
        datetime(2020, 1, 1, 12),
        MountainashDtype.TIMESTAMP,
    ),
    "decimal": (
        _declared(DataType.DECIMAL, precision=5, scale=2, rounding="half_to_even"),
        Decimal("1.50"),
        DecimalDtype(5, 2),
    ),
}


@pytest.mark.parametrize("case", NON_LIST_SET_CASES)
def test_a_non_list_set_cell_normalizes_to_an_empty_set_of_the_declared_element_type(backend_name, case):
    field, scalar, expected = NON_LIST_SET_CASES[case]
    dim = set_dimension(field)
    value_name, _ = rule_binding_names(dim)
    normalized = normalize(backend_name, dim, [scalar, scalar])
    rows = normalized.to_dicts()
    element = normalized.collect_schema()[value_name].element
    assert [row["value"] for row in rows] == [scalar, scalar]
    assert [row[value_name] for row in rows] == [[], []]
    # DATETIME preserves the existing element family; this does not assert a unit or timezone.
    assert (element if isinstance(expected, DecimalDtype) else dtype_family(element)) == expected


def test_an_integer_set_under_a_float_dimension_is_normalized_to_floats(backend_name):
    dim = set_dimension(_declared(DataType.FLOAT))
    value_name, _ = rule_binding_names(dim)
    normalized = normalize(backend_name, dim, [[2, 1, 1], [], None])
    rows = normalized.to_dicts()
    assert [row["value"] for row in rows] == [[2, 1, 1], [], None]
    payload = [row[value_name] for row in rows]
    assert payload == [[1.0, 2.0], [], None]
    assert all(type(member) is float for member in payload[0])
    assert normalized.collect_schema()[value_name].element is MountainashDtype.FP64
