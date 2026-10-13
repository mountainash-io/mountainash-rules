"""Declared-domain predicates retain joint meaning on the selected backend."""

import datetime as dt
from decimal import Decimal

import mountainash as ma
import pytest
from mountainash import DecimalDtype, MountainashDtype

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.codec import canonical_bytes, content_id
from mountainash_rules.core.contracts import DomainDefinition, DomainField
from mountainash_rules.core.scalar import encode_scalar
from tests.conftest import build_backend_df


def node(graph, op, **members):
    payload = {"schema_version": 1, "node": {"op": op, **members}}
    identifier = content_id("predicate", payload)
    graph[identifier] = payload
    return identifier


def scalar(value, dtype="int"):
    return encode_scalar(value, dtype, allow_reserved=True)


def interval_node(graph, field, lower, upper, *, closed=(True, False), dtype="int"):
    return node(
        graph,
        "interval",
        field=field,
        lower=None if lower is None else scalar(lower, dtype),
        upper=None if upper is None else scalar(upper, dtype),
        lower_closed=lower is not None and closed[0],
        upper_closed=upper is not None and closed[1],
    )


def domain(root, *fields):
    return DomainDefinition(
        schema_version=1,
        domain_id="D",
        predicate_id=root,
        fields=tuple(sorted(fields, key=lambda field: field.name)),
    )


def enum_node(graph, field, values):
    encoded = [scalar(value, "str") for value in values]
    encoded.sort(key=lambda value: canonical_bytes({"value": value}))
    if not encoded:
        return node(graph, "false")
    if len(encoded) == 1:
        return node(graph, "eq", field=field, value=encoded[0])
    return node(graph, "in", field=field, values=encoded)


def truth(relation, expression):
    return relation.select("id", expression.alias("allowed")).sort("id")


def native_schema(relation):
    """Assert terminal result types, not optional AST schema inference."""
    return ma.relation(relation.to_polars()).schema


@pytest.mark.parametrize("root_kind", ["absent", "true", "false"])
def test_no_domain_and_constants_preserve_input_identity_and_typed_empty(
    backend_name,
    records,
    root_kind,
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    relation = ma.relation(build_backend_df(backend_name, {"id": [8, 2], "x": [5, 10]}))
    graph = {}
    declared = None if root_kind == "absent" else domain(node(graph, root_kind))
    lowered = lower_domain(declared, graph)
    result = truth(relation, lowered.predicate)
    assert records(result) == [
        {"id": 2, "allowed": root_kind != "false"},
        {"id": 8, "allowed": root_kind != "false"},
    ]
    empty = truth(relation.filter(ma.lit(False)), lowered.predicate)
    assert records(empty) == []
    actual_schema = native_schema(result)
    assert native_schema(empty) == actual_schema
    assert actual_schema["allowed"] == MountainashDtype.BOOL



def test_feasible_assignments_adds_boundaries_without_restricting_input_and_uses_selected_domains(
    backend_name, records,
):
    from mountainash_rules.engines.accumulate.domain import feasible_assignments

    graph = {}
    global_domain = domain(
        node(graph, "true"), DomainField(name="x", data_type="int"),
    )
    selected_domain = DomainDefinition(
        schema_version=1,
        domain_id="Q",
        predicate_id=node(graph, "false"),
        fields=(DomainField(name="x", data_type="int"),),
    )
    metadata = DimensionsMetadata(
        dimensions=[],
        domain=global_domain,
        context_domains=[selected_domain],
        domain_nodes=graph,
    )
    pairs = ma.relation(build_backend_df(
        backend_name, {"pair_id": [1, 2], "marker": ["left", "right"]},
    ))
    boundaries = ma.relation(build_backend_df(
        backend_name, {"pair_id": [1, 1, 2], "value": [3, 5, 7]},
    ))

    assignments, value_columns, residual_columns = feasible_assignments(
        pairs, metadata, {}, {}, pair_key="pair_id", boundaries={"x": boundaries},
    )
    value_name, residual_name = value_columns["x"], residual_columns["x"]
    materialized = records(assignments.select(
        "pair_id", "marker",
        ma.col(value_name).alias("x"),
        ma.col(residual_name).alias("residual"),
    ))
    concrete = {
        (row["pair_id"], row["x"])
        for row in materialized if row["residual"] == -1
    }
    assert {(1, 0), (1, 3), (1, 5), (2, 0), (2, 7)} <= concrete
    assert {(row["pair_id"], row["marker"]) for row in materialized} == {
        (1, "left"), (2, "right"),
    }

    selected, _, _ = feasible_assignments(
        pairs,
        metadata,
        {},
        {},
        pair_key="pair_id",
        boundaries={"x": boundaries},
        domains=(global_domain, selected_domain),
    )
    assert records(selected) == []

def test_interval_preserves_exclusive_boundary_and_old_reserved_integers(
    backend_name, records
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    root = interval_node(graph, "x", 0, 10)
    lowered = lower_domain(domain(root, DomainField(name="x", data_type="int")), graph)
    relation = ma.relation(
        build_backend_df(backend_name, {"id": [3, 1, 2], "x": [5, 10, -1]})
    )
    assert records(truth(relation, lowered.predicate)) == [
        {"id": 1, "allowed": False},
        {"id": 2, "allowed": False},
        {"id": 3, "allowed": True},
    ]
    assert records(relation.filter(lowered.predicate).select("id")) == [{"id": 3}]
    root = interval_node(graph, "x", -999999999, -999999998, closed=(True, True))
    lowered = lower_domain(domain(root, DomainField(name="x", data_type="int")), graph)
    relation = ma.relation(
        build_backend_df(
            backend_name,
            {"id": [1, 2, 3], "x": [-999999999, -999999998, -999999997]},
        )
    )
    assert records(truth(relation, lowered.predicate)) == [
        {"id": 1, "allowed": True},
        {"id": 2, "allowed": True},
        {"id": 3, "allowed": False},
    ]


@pytest.mark.parametrize(
    "shape, expected",
    [
        ("and", [False, True, False, False]),
        ("contradiction", [False, False, False, False]),
        ("or", [True, True, False, True]),
        ("not", [False, False, True, True]),
    ],
)
def test_boolean_shape_is_not_replaced_by_axis_hulls(
    backend_name, records, shape, expected
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    left = interval_node(graph, "x", 0, 4)
    right = interval_node(graph, "x", 2 if shape == "and" else 8, 10)
    root = (
        node(graph, "not", arg=left)
        if shape == "not"
        else node(
            graph,
            "and" if shape == "contradiction" else shape,
            args=sorted([left, right]),
        )
    )
    lowered = lower_domain(domain(root, DomainField(name="x", data_type="int")), graph)
    relation = ma.relation(
        build_backend_df(backend_name, {"id": [1, 2, 3, 4], "x": [0, 2, 6, 9]})
    )
    assert records(truth(relation, lowered.predicate)) == [
        {"id": identifier, "allowed": allowed}
        for identifier, allowed in enumerate(expected, 1)
    ]
    if shape in {"and", "contradiction"}:
        assert records(truth(relation, lowered.projections["x"].predicate)) == [
            {"id": identifier, "allowed": allowed}
            for identifier, allowed in enumerate(expected, 1)
        ]
    else:
        assert "x" not in lowered.projections


@pytest.mark.parametrize(
    "operation, expected",
    [("and", [False, True, False, False]), ("or", [True, True, True, False])],
)
def test_enumeration_projection_intersects_or_unions_without_losing_literals(
    backend_name,
    records,
    operation,
    expected,
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    left = enum_node(graph, "region", ["EU", "US"])
    right = enum_node(graph, "region", ["US", "APAC"])
    root = node(graph, operation, args=sorted([left, right]))
    lowered = lower_domain(
        domain(root, DomainField(name="region", data_type="str")), graph
    )
    relation = ma.relation(
        build_backend_df(
            backend_name,
            {"id": [1, 2, 3, 4], "region": ["EU", "US", "APAC", "NONE"]},
        )
    )
    wanted = [
        {"id": identifier, "allowed": allowed}
        for identifier, allowed in enumerate(expected, 1)
    ]
    assert records(truth(relation, lowered.predicate)) == wanted
    assert records(truth(relation, lowered.projections["region"].predicate)) == wanted
    assert lowered.projections["region"].values is not None


@pytest.mark.parametrize(
    "relation_name, expected",
    [
        ("eq", [False, True, False]),
        ("ne", [True, False, True]),
        ("lt", [True, False, False]),
        ("le", [True, True, False]),
        ("gt", [False, False, True]),
        ("ge", [False, True, True]),
    ],
)
def test_cross_field_comparison_retains_joint_constraint(
    backend_name, records, relation_name, expected
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    root = node(graph, "compare", left="x", right="y", relation=relation_name)
    lowered = lower_domain(
        domain(
            root,
            DomainField(name="x", data_type="int"),
            DomainField(name="y", data_type="int"),
        ),
        graph,
    )
    relation = ma.relation(
        build_backend_df(
            backend_name, {"id": [1, 2, 3], "x": [1, 2, 3], "y": [2, 2, 2]}
        )
    )
    assert records(truth(relation, lowered.predicate)) == [
        {"id": identifier, "allowed": allowed}
        for identifier, allowed in enumerate(expected, 1)
    ]
    assert lowered.projections == {}


def test_cross_field_or_does_not_project_independent_restrictions(
    backend_name, records
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    left = node(graph, "eq", field="x", value=scalar(1))
    right = node(graph, "eq", field="y", value=scalar(2))
    root = node(graph, "or", args=sorted([left, right]))
    lowered = lower_domain(
        domain(
            root,
            DomainField(name="x", data_type="int"),
            DomainField(name="y", data_type="int"),
        ),
        graph,
    )
    relation = ma.relation(
        build_backend_df(
            backend_name, {"id": [1, 2, 3], "x": [1, 7, 7], "y": [9, 2, 9]}
        )
    )
    assert records(truth(relation, lowered.predicate)) == [
        {"id": 1, "allowed": True},
        {"id": 2, "allowed": True},
        {"id": 3, "allowed": False},
    ]
    assert lowered.projections == {}


def test_unary_projection_does_not_claim_joint_satisfiability(backend_name, records):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    same = node(graph, "compare", left="x", right="y", relation="eq")
    different = node(graph, "compare", left="x", right="y", relation="ne")
    restriction = interval_node(graph, "x", 0, 10)
    root = node(graph, "and", args=sorted([same, different, restriction]))
    lowered = lower_domain(
        domain(
            root,
            DomainField(name="x", data_type="int"),
            DomainField(name="y", data_type="int"),
        ),
        graph,
    )
    relation = ma.relation(
        build_backend_df(backend_name, {"id": [1, 2], "x": [5, 5], "y": [5, 6]})
    )
    assert records(truth(relation, lowered.projections["x"].predicate)) == [
        {"id": 1, "allowed": True},
        {"id": 2, "allowed": True},
    ]
    assert records(truth(relation, lowered.predicate)) == [
        {"id": 1, "allowed": False},
        {"id": 2, "allowed": False},
    ]


@pytest.mark.parametrize("operation", ["interval", "lt", "le", "gt", "ge"])
def test_float_ordered_domain_operations_are_unsupported(operation):
    from mountainash_rules.engines.accumulate.domain import lower_domain
    from mountainash_rules.engines.accumulate.errors import UnsupportedDomainError

    graph = {}
    if operation == "interval":
        root = interval_node(graph, "x", 0.0, 1.0, dtype="float")
        fields = (DomainField(name="x", data_type="float"),)
    else:
        root = node(graph, "compare", left="x", right="y", relation=operation)
        fields = (
            DomainField(name="x", data_type="float"),
            DomainField(name="y", data_type="float"),
        )
    with pytest.raises(UnsupportedDomainError):
        lower_domain(domain(root, *fields), graph)


@pytest.mark.parametrize("gate", ["and", "or"])
def test_float_ordered_comparison_is_rejected_inside_dead_boolean_arm(gate):
    from mountainash_rules.engines.accumulate.domain import lower_domain
    from mountainash_rules.engines.accumulate.errors import UnsupportedDomainError

    graph = {}
    ordered = node(graph, "compare", left="x", right="y", relation="lt")
    constant = node(graph, "false" if gate == "and" else "true")
    root = node(graph, gate, args=sorted([ordered, constant]))
    with pytest.raises(UnsupportedDomainError):
        lower_domain(
            domain(
                root,
                DomainField(name="x", data_type="float"),
                DomainField(name="y", data_type="float"),
            ),
            graph,
        )


@pytest.mark.parametrize("relation_name", ["eq", "ne"])
def test_float_categorical_comparisons_remain_supported(
    backend_name, records, relation_name,
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    root = node(graph, "compare", left="x", right="y", relation=relation_name)
    lowered = lower_domain(
        domain(
            root,
            DomainField(name="x", data_type="float"),
            DomainField(name="y", data_type="float"),
        ),
        graph,
    )
    relation = ma.relation(build_backend_df(
        backend_name,
        {"id": [1, 2], "x": [1.5, 2.0], "y": [1.5, 3.0]},
    ))
    expected = (
        [True, False] if relation_name == "eq" else [False, True]
    )
    assert records(truth(relation, lowered.predicate)) == [
        {"id": identifier, "allowed": allowed}
        for identifier, allowed in enumerate(expected, 1)
    ]


@pytest.mark.parametrize("failure", ["root", "child", "language", "field"])
def test_unresolvable_or_unsupported_graph_fails_explicitly(failure):
    from mountainash_rules.engines.accumulate.domain import lower_domain
    from mountainash_rules.engines.accumulate.errors import (
        DomainError,
        UnsupportedDomainError,
    )

    graph = {}
    root = node(graph, "true")
    expected = DomainError
    if failure == "root":
        graph.clear()
    elif failure == "child":
        root = node(graph, "not", arg=root)
        graph.pop(next(identifier for identifier in graph if identifier != root))
    elif failure == "language":
        root = node(graph, "language", field="x", language_id="language:1:" + "0" * 64)
        expected = UnsupportedDomainError
    else:
        root = node(graph, "eq", field="undeclared", value=scalar(1))
    with pytest.raises(expected):
        lower_domain(domain(root, DomainField(name="x", data_type="int")), graph)


@pytest.mark.parametrize(
    "rounding, boundary",
    [("half_to_even", Decimal("0.004")), ("half_away_from_zero", Decimal("0.005"))],
)
def test_decimal_domain_boundary_and_projection_share_explicit_rounding(
    backend_name, records, rounding, boundary
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    root = interval_node(
        graph, "amount", Decimal("0.0045"), Decimal("0.010"), dtype="decimal"
    )
    field = DomainField(
        name="amount",
        data_type="decimal",
        numeric_dtype=DecimalDtype(10, 3),
        rounding=rounding,
    )
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name=name,
                context_field="amount",
                rule_field=f"rule_{name}",
                match_strategy="greater_than",
                data_type="decimal",
                numeric_dtype=DecimalDtype(10, 3),
                rounding=rounding,
            )
            for name in ("A", "B")
        ],
        domain=domain(root, field),
        domain_nodes=graph,
    )
    metadata = DimensionsMetadata.from_yaml(metadata.to_yaml())
    relation = ma.relation(
        build_backend_df(
            backend_name, {"id": [1, 2, 3], "raw": ["0.004", "0.005", "0.010"]}
        )
    )
    lowered = lower_domain(
        metadata.domain,
        metadata.domain_nodes,
        values={"amount": ma.col("raw")},
        source_dtypes={"amount": relation.schema["raw"]},
    )
    expected = [
        {"id": 1, "allowed": rounding == "half_to_even"},
        {"id": 2, "allowed": True},
        {"id": 3, "allowed": False},
    ]
    assert records(truth(relation, lowered.predicate)) == expected
    projection = lowered.projections["amount"]
    assert records(truth(relation, projection.predicate)) == expected
    endpoints = relation.select("id", projection.interval.lower.alias("lower"))
    assert native_schema(endpoints)["lower"] == DecimalDtype(10, 3)
    assert records(endpoints.sort("id")) == [
        {"id": identifier, "lower": boundary} for identifier in (1, 2, 3)
    ]


@pytest.mark.parametrize(
    "lower, upper, closed, values, expected",
    [
        (
            "0.0044",
            "0.00449",
            (True, True),
            ["0.003", "0.004", "0.005"],
            [False, True, False],
        ),
        (
            "0.0071",
            "0.0074",
            (False, True),
            ["0.006", "0.007", "0.008"],
            [False, False, False],
        ),
        (
            "-999.99",
            "999.99",
            (True, True),
            ["-999.99", "0", "999.99"],
            [True, True, True],
        ),
        (
            "-999.99",
            "999.99",
            (False, False),
            ["-999.99", "0", "999.99"],
            [False, True, False],
        ),
    ],
)
def test_decimal_collapsed_and_extreme_intervals_do_not_use_rank_holes(
    backend_name, records, lower, upper, closed, values, expected
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    dtype = DecimalDtype(5, 2) if "999" in lower else DecimalDtype(10, 3)
    graph = {}
    root = interval_node(
        graph, "x", Decimal(lower), Decimal(upper), closed=closed, dtype="decimal"
    )
    relation = ma.relation(
        build_backend_df(backend_name, {"id": [1, 2, 3], "x": values})
    )
    lowered = lower_domain(
        domain(
            root,
            DomainField(
                name="x",
                data_type="decimal",
                numeric_dtype=dtype,
                rounding="half_to_even",
            ),
        ),
        graph,
        source_dtypes=relation.schema,
    )
    wanted = [
        {"id": identifier, "allowed": allowed}
        for identifier, allowed in enumerate(expected, 1)
    ]
    assert records(truth(relation, lowered.predicate)) == wanted
    assert records(truth(relation, lowered.projections["x"].predicate)) == wanted


@pytest.mark.parametrize(
    "lower, upper, expected",
    [
        (None, Decimal("0.05"), [True, True, False]),
        (Decimal("0.04"), None, [False, True, True]),
        (None, None, [True, True, True]),
    ],
)
def test_decimal_unbounded_sides_are_structural_not_null_operands(
    backend_name,
    records,
    lower,
    upper,
    expected,
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    root = interval_node(graph, "x", lower, upper, dtype="decimal")
    relation = ma.relation(
        build_backend_df(
            backend_name,
            {"id": [1, 2, 3], "x": ["-999.99", "0.04", "999.99"]},
        )
    )
    lowered = lower_domain(
        domain(
            root,
            DomainField(
                name="x",
                data_type="decimal",
                numeric_dtype=DecimalDtype(5, 2),
                rounding="half_to_even",
            ),
        ),
        graph,
        source_dtypes=relation.schema,
    )
    wanted = [
        {"id": identifier, "allowed": allowed}
        for identifier, allowed in enumerate(expected, 1)
    ]
    assert records(truth(relation, lowered.predicate)) == wanted
    geometry = lowered.projections["x"].interval
    endpoints = relation.select(
        "id", geometry.lower.alias("lower"), geometry.upper.alias("upper")
    )
    actual_schema = native_schema(endpoints)
    assert actual_schema["lower"] == actual_schema["upper"] == DecimalDtype(5, 2)
    assert records(relation.filter(ma.lit(False)).filter(lowered.predicate)) == []


@pytest.mark.parametrize("bad", [Decimal("999.995"), Decimal("-999.995")])
def test_invalid_domain_literal_remains_reportable_for_typed_empty_input(
    backend_name, records, bad
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    root = interval_node(graph, "x", bad, bad, closed=(True, True), dtype="decimal")
    relation = ma.relation(
        build_backend_df(backend_name, {"id": [1], "x": ["0"]})
    ).filter(ma.lit(False))
    lowered = lower_domain(
        domain(
            root,
            DomainField(
                name="x",
                data_type="decimal",
                numeric_dtype=DecimalDtype(5, 2),
                rounding="half_to_even",
            ),
        ),
        graph,
        source_dtypes=relation.schema,
    )
    singleton = relation.group_by().agg(ma.col("id").count().alias("rows"))
    assert records(
        singleton.select(
            "rows",
            lowered.declaration_invalid["x"].alias("invalid"),
        )
    ) == [{"rows": 0, "invalid": True}]


@pytest.mark.parametrize("bad", [Decimal("999.995"), Decimal("-999.995")])
def test_post_round_domain_overflow_is_reported_separately_from_row_admission(
    backend_name, records, bad
):
    from mountainash_rules.engines.accumulate.domain import lower_domain

    graph = {}
    root = interval_node(graph, "x", bad, None, dtype="decimal")
    relation = ma.relation(build_backend_df(backend_name, {"id": [1], "x": ["0"]}))
    lowered = lower_domain(
        domain(
            root,
            DomainField(
                name="x",
                data_type="decimal",
                numeric_dtype=DecimalDtype(5, 2),
                rounding="half_to_even",
            ),
        ),
        graph,
        source_dtypes=relation.schema,
    )
    singleton = relation.group_by().agg(
        ma.col("id").count().alias("rows"),
        lowered.invalid["x"].any().alias("invalid_input"),
    )
    assert records(
        singleton.select(
            "rows",
            "invalid_input",
            lowered.declaration_invalid["x"].alias("invalid_domain"),
        )
    ) == [{"rows": 1, "invalid_input": False, "invalid_domain": True}]


def test_interval_intersection_keeps_endpoint_closure_and_structural_unboundedness(
    backend_name, records
):
    from mountainash_rules.engines.accumulate.domain import (
        Interval,
        intersect_intervals,
    )

    # Rows: exclusive touch, singleton touch, containment, disjoint,
    # left-unbounded, right-unbounded, and both-unbounded intersections.
    relation = ma.relation(
        build_backend_df(
            backend_name,
            {
                "id": [1, 2, 3, 4, 5, 6, 7],
                "alo": [0, 0, 0, 0, 999, 2, 999],
                "ahi": [5, 5, 10, 1, 4, 999, 999],
                "blo": [5, 5, 2, 2, 1, 0, 999],
                "bhi": [9, 9, 3, 3, 999, 3, 999],
                "alc": [True, True, True, True, False, False, False],
                "ahc": [False, True, True, True, True, False, False],
                "blc": [True, True, False, True, True, True, False],
                "bhc": [True, True, True, True, False, True, False],
                "alu": [False, False, False, False, True, False, True],
                "ahu": [False, False, False, False, False, True, True],
                "blu": [False, False, False, False, False, False, True],
                "bhu": [False, False, False, False, True, False, True],
            },
        )
    )

    def bounds(prefix):
        return Interval(
            lower=ma.col(prefix + "lo"),
            upper=ma.col(prefix + "hi"),
            lower_closed=ma.col(prefix + "lc"),
            upper_closed=ma.col(prefix + "hc"),
            lower_unbounded=ma.col(prefix + "lu"),
            upper_unbounded=ma.col(prefix + "hu"),
        )

    result = intersect_intervals(bounds("a"), bounds("b"))
    selected = relation.select(
        "id",
        result.nonempty.alias("nonempty"),
        result.lower_unbounded.alias("lower_unbounded"),
        result.upper_unbounded.alias("upper_unbounded"),
        result.lower_closed.alias("lower_closed"),
        result.upper_closed.alias("upper_closed"),
    )
    assert records(selected.sort("id")) == [
        {
            "id": 1,
            "nonempty": False,
            "lower_unbounded": False,
            "upper_unbounded": False,
            "lower_closed": True,
            "upper_closed": False,
        },
        {
            "id": 2,
            "nonempty": True,
            "lower_unbounded": False,
            "upper_unbounded": False,
            "lower_closed": True,
            "upper_closed": True,
        },
        {
            "id": 3,
            "nonempty": True,
            "lower_unbounded": False,
            "upper_unbounded": False,
            "lower_closed": False,
            "upper_closed": True,
        },
        {
            "id": 4,
            "nonempty": False,
            "lower_unbounded": False,
            "upper_unbounded": False,
            "lower_closed": True,
            "upper_closed": True,
        },
        {
            "id": 5,
            "nonempty": True,
            "lower_unbounded": False,
            "upper_unbounded": False,
            "lower_closed": True,
            "upper_closed": True,
        },
        {
            "id": 6,
            "nonempty": True,
            "lower_unbounded": False,
            "upper_unbounded": False,
            "lower_closed": False,
            "upper_closed": True,
        },
        {
            "id": 7,
            "nonempty": True,
            "lower_unbounded": True,
            "upper_unbounded": True,
            "lower_closed": False,
            "upper_closed": False,
        },
    ]
    concrete = (
        relation.filter(ma.col("id") < 7)
        .select("id", result.lower.alias("lower"), result.upper.alias("upper"))
        .sort("id")
    )
    assert records(concrete) == [
        {"id": 1, "lower": 5, "upper": 5},
        {"id": 2, "lower": 5, "upper": 5},
        {"id": 3, "lower": 2, "upper": 3},
        {"id": 4, "lower": 2, "upper": 1},
        {"id": 5, "lower": 1, "upper": 4},
        {"id": 6, "lower": 2, "upper": 3},
    ]
    actual_schema = native_schema(concrete)
    assert actual_schema["lower"] == relation.schema["alo"]
    assert actual_schema["upper"] == relation.schema["ahi"]


@pytest.mark.parametrize(
    "unbounded, expected_days",
    [
        ("lower", [(1, 9), (8, 9)]),
        ("upper", [(5, 10), (8, 12)]),
        ("both", [(1, 10), (8, 12)]),
    ],
)
def test_utc_unbounded_domain_intersects_utc_piece_rows_without_timezone_loss(
    backend_name,
    unbounded,
    expected_days,
):
    from mountainash_rules.engines.accumulate.domain import (
        Interval,
        intersect_intervals,
        lower_domain,
    )

    def instant(day):
        return dt.datetime(2026, 1, day, tzinfo=dt.timezone.utc)

    graph = {}
    lower = (
        None
        if unbounded in {"lower", "both"}
        else encode_scalar(
            instant(5),
            "datetime",
            timezone="utc",
        )
    )
    upper = (
        None
        if unbounded in {"upper", "both"}
        else encode_scalar(
            instant(9),
            "datetime",
            timezone="utc",
        )
    )
    root = node(
        graph,
        "interval",
        field="when",
        lower=lower,
        upper=upper,
        lower_closed=lower is not None,
        upper_closed=upper is not None,
    )
    lowered = lower_domain(
        domain(root, DomainField(name="when", data_type="datetime", timezone="utc")),
        graph,
    )
    relation = ma.relation(
        build_backend_df(
            backend_name,
            {
                "id": [1, 2],
                "piece_lower": [instant(1), instant(8)],
                "piece_upper": [instant(10), instant(12)],
            },
        )
    )
    pieces = Interval(
        lower=ma.col("piece_lower"),
        upper=ma.col("piece_upper"),
        lower_closed=ma.lit(True),
        upper_closed=ma.lit(False),
        lower_unbounded=ma.lit(False),
        upper_unbounded=ma.lit(False),
    )
    clipped = intersect_intervals(lowered.projections["when"].interval, pieces)
    actual = (
        relation.select(
            "id",
            clipped.lower.alias("lower"),
            clipped.upper.alias("upper"),
            clipped.lower_closed.alias("lower_closed"),
            clipped.upper_closed.alias("upper_closed"),
            clipped.nonempty.alias("nonempty"),
        )
        .sort("id")
        .to_pyarrow()
    )
    assert actual.to_pylist() == [
        {
            "id": identifier,
            "lower": instant(lo),
            "upper": instant(hi),
            "lower_closed": True,
            "upper_closed": unbounded == "lower",
            "nonempty": True,
        }
        for identifier, (lo, hi) in enumerate(expected_days, 1)
    ]
    assert actual.schema.field("lower").type.tz == "UTC"
    assert actual.schema.field("upper").type.tz == "UTC"
@pytest.mark.parametrize("precision", [2, 28])
def test_decimal_16_digit_candidate_extrema_are_exact_at_reduced_context(
    backend_name, records, precision,
):
    from decimal import localcontext

    from mountainash_rules.engines.accumulate.domain import feasible_assignments

    graph = {}
    declared = domain(
        node(graph, "true"),
        DomainField(
            name="amount",
            data_type="decimal",
            numeric_dtype=DecimalDtype(16, 0),
            rounding="half_to_even",
        ),
    )
    metadata = DimensionsMetadata(
        dimensions=[], domain=declared, domain_nodes=graph,
    )
    pairs = ma.relation(build_backend_df(backend_name, {"pair_id": [1]}))
    maximum = Decimal("9999999999999999")
    boundaries = ma.relation(build_backend_df(
        backend_name,
        {
            "pair_id": [1, 1, 1],
            "value": [
                Decimal(0),
                maximum,
                Decimal("-9999999999999999"),
            ],
        },
    ))

    with localcontext() as context:
        context.prec = precision
        assignments, value_columns, residual_columns = feasible_assignments(
            pairs, metadata, {}, {}, pair_key="pair_id",
            boundaries={"amount": boundaries},
        )
    value_name, residual_name = value_columns["amount"], residual_columns["amount"]
    candidates = {
        row["amount"]
        for row in records(assignments.select(
            ma.col(value_name).alias("amount"),
            ma.col(residual_name).alias("residual"),
        ))
        if row["residual"] == -1
    }
    assert {
        Decimal(0), Decimal(1), Decimal(-1), maximum, maximum - 1,
        Decimal("-9999999999999999"),
        Decimal("-9999999999999998"),
    } <= candidates
