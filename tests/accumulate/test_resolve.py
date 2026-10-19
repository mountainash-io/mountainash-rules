"""Direct resolution preserves null, uncertainty, routing, and request identity."""

from decimal import Decimal

import mountainash as ma
import pytest

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.contracts import DomainField
from tests.accumulate.test_domain import domain, enum_node
from tests.conftest import build_backend_df


def frame(backend, values):
    return ma.relation(build_backend_df(backend, values))


def payload_values(result, scope="default", output="total"):
    return result.payload(scope).select(
        "request_id", ma.col(result.payload_columns[scope][output]).alias("value"),
    ).sort("request_id").to_dicts()


@pytest.fixture(params=["direct", "stored"])
def resolve_operation(request):
    from mountainash_rules.engines.accumulate.build import build
    from mountainash_rules.engines.accumulate.resolve import resolve

    if request.param == "direct":
        return resolve

    def stored(rules, metadata, contexts, **options):
        return build(rules, metadata).resolve(contexts, **options)

    return stored


@pytest.mark.parametrize("operation,expected", [
    ("sum", [0, 0, 5, None]), ("min", [0, -10, 5, None]),
    ("max", [0, 10, 5, None]), ("product", [0, -100, 5, None]),
])
def test_complete_folds_distinguish_zero_cancellation_and_no_match(backend_name, operation, expected):
    from mountainash_rules.engines.accumulate.resolve import resolve

    rules = frame(backend_name, {
        "uuid": ["zero", "positive", "negative", "five"],
        "region": ["zero", "cancel", "cancel", "five"], "amount": [0, 10, -10, 5],
    })
    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[Dimension(dimension_name="region")],
        aggregates=[Aggregate(column_name="amount", output_name="total", data_type="int", operation=operation)])
    contexts = frame(backend_name, {"request_id": [1, 2, 3, 4], "region": ["zero", "cancel", "five", "absent"]})
    if operation == "product" and backend_name == "ibis-duckdb":
        from mountainash.core.types import BackendCapabilityError

        with pytest.raises(BackendCapabilityError):
            resolve(rules, metadata, contexts, include_contributors=True).fetch()
        return
    result = resolve(rules, metadata, contexts, include_contributors=True).fetch()
    assert payload_values(result) == [dict(request_id=index, value=value) for index, value in enumerate(expected, 1)]
    assert result.contributors.select("request_id", "source_uuid").sort("request_id", "source_uuid").to_dicts() == [
        {"request_id": 1, "source_uuid": "zero"},
        {"request_id": 2, "source_uuid": "negative"}, {"request_id": 2, "source_uuid": "positive"},
        {"request_id": 3, "source_uuid": "five"},
    ]
    assert result.status.select("request_id", "status").sort("request_id").to_dicts() == [
        {"request_id": index, "status": "resolved"} for index in range(1, 5)
    ]


@pytest.mark.parametrize("allowed,contribution,status,value", [
    (["EU", "US"], 0, "resolved", 0),
    (["APAC", "EU", "US"], 0, "unresolved", None),
    (["APAC", "EU", "US"], 10, "unresolved", None),
    (["APAC"], 10, "resolved", None),
])
def test_partial_agreement_includes_feasible_no_match(backend_name, allowed, contribution, status, value):
    from mountainash_rules.engines.accumulate.resolve import resolve

    graph = {}
    root = enum_node(graph, "region", allowed)
    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[Dimension(dimension_name="region", wildcard_values=[None])],
        aggregates=[Aggregate(column_name="amount", output_name="total", data_type="int")],
        domain=domain(root, DomainField(name="region", data_type="str")), domain_nodes=graph)
    rules = frame(backend_name, {"uuid": ["EU", "US"], "region": ["EU", "US"], "amount": [contribution, contribution]})
    result = resolve(rules, metadata, frame(backend_name, {"request_id": [9]})).fetch()
    assert result.status.select("status").to_dicts() == [{"status": status}]
    assert payload_values(result) == ([{"request_id": 9, "value": value}] if status == "resolved" else [])


def test_empty_scope_selection_keeps_partition_admission(backend_name):
    from mountainash_rules.engines.accumulate.resolve import resolve

    metadata = DimensionsMetadata(uuid_field="uuid", partition_field="market",
        dimensions=[Dimension(dimension_name="x", data_type="int")],
        aggregates=[Aggregate(column_name="amount", output_name="total", data_type="int")])
    rules = frame(backend_name, {"uuid": ["A"], "market": ["EU"], "x": [5], "amount": [7]})
    contexts = frame(backend_name, {"request_id": [8, 2], "market": ["EU", "unknown"], "x": [5, 5]})
    result = resolve(rules, metadata, contexts, scopes=[]).fetch()
    assert result.requests.select("request_id", "request_order", "admission_status").sort("request_order").to_dicts() == [
        {"request_id": 8, "request_order": 0, "admission_status": "admitted"},
        {"request_id": 2, "request_order": 1, "admission_status": "invalid"},
    ]
    assert result.status.to_dicts() == []
    assert result.payloads == {}
    with pytest.raises(KeyError):
        result.payload("default")


def test_decimal_sum_rounds_only_after_folding(backend_name):
    from mountainash import DecimalDtype
    from mountainash_rules.engines.accumulate.resolve import resolve

    metadata = DimensionsMetadata(uuid_field="uuid", dimensions=[Dimension(dimension_name="region")], aggregates=[
        Aggregate(column_name="amount", output_name="total", data_type="decimal", numeric_dtype=DecimalDtype(10, 3),
            output_numeric_dtype=DecimalDtype(10, 2), output_rounding="half_to_even"),
    ])
    rules = frame(backend_name, {"uuid": ["A", "B"], "region": ["EU", "EU"], "amount": [Decimal("0.004"), Decimal("0.004")]})
    result = resolve(rules, metadata, frame(backend_name, {"request_id": [1, 2], "region": ["EU", "US"]})).fetch()
    assert payload_values(result) == [{"request_id": 1, "value": Decimal("0.01")}, {"request_id": 2, "value": None}]


@pytest.mark.parametrize("data_type,value", [
    ("int", "17"), ("int", True), ("str", 17), ("bool", 1),
])
def test_request_admission_rejects_coercible_original_types(backend_name, data_type, value):
    from mountainash_rules.engines.accumulate import resolve

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type=data_type, rule_wildcard_field="any_x",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    source_value = {"int": 0, "str": "seed", "bool": True}[data_type]
    result = resolve(
        frame(backend_name, {"uuid": ["A"], "x": [source_value], "any_x": [True], "amount": [7]}),
        metadata, frame(backend_name, {"request_id": [7], "x": [value]}),
    ).fetch()
    assert result.requests.select("request_id", "admission_status", "reason_code").to_dicts() == [
        {"request_id": 7, "admission_status": "invalid", "reason_code": "invalid_value"},
    ]
    assert result.payload("default").to_dicts() == []


def controlled_metadata():
    graph = {}
    root = enum_node(graph, "region", ["EU", "US"])
    return DimensionsMetadata(
        uuid_field="uuid", dimensions=[Dimension(dimension_name="region")],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        context_domains=[domain(root, DomainField(name="region", data_type="str"))],
        domain_nodes=graph,
        context_contracts=[{
            "schema_version": 1, "contract_id": "P", "domain_ref": "D",
            "fields": [{"name": "region", "data_type": "str", "required": False}],
            "profiles": [{
                "profile_id": "decision", "mode": "resolve",
                "output_fields": ["default.amount"], "provenance": "none",
                "dimensions": ["region"], "allow_dont_care": ["region"],
                "promise": "allow_unresolved", "on_unresolved": "return",
            }],
        }],
    )


@pytest.mark.parametrize("case", [
    "unknown_request", "duplicate_control", "unknown_profile",
    "duplicate_mask", "unknown_mask_request", "unknown_dimension", "uncontracted_mask",
])
def test_request_controls_reject_invalid_references_and_masks(backend_name, case):
    from mountainash_rules.engines.accumulate.admission import bind_request_controls, project_contexts
    from mountainash_rules.engines.accumulate.errors import RequestValidationError

    metadata = controlled_metadata()
    requests = project_contexts(
        frame(backend_name, {"request_id": [1, 2], "region": ["EU", "US"]}), metadata,
    )
    ids = [99] if case == "unknown_request" else ([1, 1] if case == "duplicate_control" else [1])
    control = None if case == "uncontracted_mask" else frame(backend_name, {
        "request_id": ids, "contract_id": ["P"] * len(ids),
        "profile_id": ["missing" if case == "unknown_profile" else "decision"] * len(ids),
    })
    masks = None
    if "mask" in case or case == "unknown_dimension":
        mask_ids = [99] if case == "unknown_mask_request" else ([1, 1] if case == "duplicate_mask" else [1])
        masks = frame(backend_name, {
            "request_id": mask_ids,
            "dimension_name": ["missing" if case == "unknown_dimension" else "region"] * len(mask_ids),
        })
    with pytest.raises(RequestValidationError):
        bind_request_controls(requests, metadata, control, masks)


def test_request_controls_leave_unspecified_rows_uncontracted(backend_name):
    from mountainash_rules.engines.accumulate.admission import bind_request_controls, project_contexts

    metadata = controlled_metadata()
    requests = project_contexts(
        frame(backend_name, {"request_id": [1, 2], "region": ["EU", "US"]}), metadata,
    )
    control = frame(backend_name, {"request_id": [1], "contract_id": ["P"], "profile_id": ["decision"]})
    masks = frame(backend_name, {"request_id": [1], "dimension_name": ["region"]})
    bound = bind_request_controls(requests, metadata, control, masks)
    assert bound.select("request_id", "contract_id", "profile_id", "__mask_region").sort("request_id").to_dicts() == [
        {"request_id": 1, "contract_id": "P", "profile_id": "decision", "__mask_region": True},
        {"request_id": 2, "contract_id": None, "profile_id": None, "__mask_region": False},
    ]


def test_profile_output_projection_does_not_claim_unrequested_values(backend_name, resolve_operation):

    metadata = controlled_metadata()
    authored = metadata.model_dump()
    authored["aggregates"] = [
        Aggregate(column_name="amount", output_name="a", data_type="int").model_dump(),
        Aggregate(column_name="other", output_name="b", data_type="int").model_dump(),
    ]
    base = authored["context_contracts"][0]["profiles"][0]
    authored["context_contracts"][0]["profiles"] = [
        dict(base, profile_id="a_only", output_fields=["default.a"]),
        dict(base, profile_id="both", output_fields=["default.a", "default.b"]),
    ]
    metadata = DimensionsMetadata.model_validate(authored)
    rules = frame(backend_name, {
        "uuid": ["E", "U"], "region": ["EU", "US"], "amount": [10, 10], "other": [1, 2],
    })
    result = resolve_operation(
        rules, metadata, frame(backend_name, {"request_id": [1, 2]}),
        request_control=frame(backend_name, {
            "request_id": [1, 2], "contract_id": ["P", "P"], "profile_id": ["a_only", "both"],
        }),
    ).fetch()
    assert result.status.select("request_id", "status").sort("request_id").to_dicts() == [
        {"request_id": 1, "status": "resolved"}, {"request_id": 2, "status": "unresolved"},
    ]
    assert result.payload("default").to_dicts() == [{"request_id": 1, "v0001": 10, "v0002": None}]
    assert result.requested_outputs.sort("request_id", "output_name").to_dicts() == [
        {"request_id": 1, "scope": "default", "output_name": "a"},
        {"request_id": 2, "scope": "default", "output_name": "a"},
        {"request_id": 2, "scope": "default", "output_name": "b"},
    ]


def test_requiredness_and_original_provider_domain_precede_permitted_masks(backend_name, resolve_operation):

    authored = controlled_metadata().model_dump()
    authored["context_contracts"][0]["fields"][0]["required"] = True
    metadata = DimensionsMetadata.model_validate(authored)
    rules = frame(backend_name, {"uuid": ["E", "U"], "region": ["EU", "US"], "amount": [10, 20]})
    result = resolve_operation(
        rules, metadata, frame(backend_name, {
            "request_id": [1, 2, 3, 4], "region": [None, "APAC", "EU", "EU"],
        }),
        request_control=frame(backend_name, {
            "request_id": [1, 2, 3, 4], "contract_id": ["P"] * 4, "profile_id": ["decision"] * 4,
        }),
        masks=frame(backend_name, {"request_id": [1, 2, 3], "dimension_name": ["region"] * 3}),
    ).fetch()
    assert result.requests.sort("request_order").select("admission_status").to_dicts() == [
        {"admission_status": status} for status in ["invalid", "invalid", "admitted", "admitted"]
    ]
    assert result.status.sort("request_id").select("status").to_dicts() == [
        {"status": status} for status in ["invalid", "invalid", "unresolved", "resolved"]
    ]
    assert result.payload("default").to_dicts() == [{"request_id": 4, "v0001": 10}]


def test_masked_alias_does_not_erase_another_observation_of_the_same_field(backend_name, resolve_operation):
    from tests.accumulate.test_domain import node, scalar

    graph = {}
    declared = domain(
        node(graph, "in", field="x", values=[scalar(1), scalar(2)]),
        DomainField(name="x", data_type="int"),
    )
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[
            Dimension(dimension_name=name, context_field="x", rule_field=name, data_type="int")
            for name in ["left", "right"]
        ],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        context_domains=[declared], domain_nodes=graph,
        context_contracts=[{
            "schema_version": 1, "contract_id": "P", "domain_ref": "D",
            "fields": [{"name": "x", "data_type": "int", "required": True}],
            "profiles": [{
                "profile_id": "decision", "mode": "resolve", "output_fields": ["default.amount"],
                "provenance": "none", "dimensions": ["left", "right"],
                "allow_dont_care": ["left", "right"], "promise": "allow_unresolved", "on_unresolved": "return",
            }],
        }],
    )
    rules = frame(backend_name, {"uuid": ["A", "B"], "left": [1, 2], "right": [1, 2], "amount": [5, 9]})
    result = resolve_operation(
        rules, metadata, frame(backend_name, {"request_id": [1, 2], "x": [1, 1]}),
        request_control=frame(backend_name, {
            "request_id": [1, 2], "contract_id": ["P", "P"], "profile_id": ["decision", "decision"],
        }),
        masks=frame(backend_name, {"request_id": [1, 2, 2], "dimension_name": ["left", "left", "right"]}),
    ).fetch()
    assert result.status.sort("request_id").select("status").to_dicts() == [
        {"status": "resolved"}, {"status": "unresolved"},
    ]
    assert result.payload("default").to_dicts() == [{"request_id": 1, "v0001": 5}]


@pytest.mark.parametrize("data_type,value,invalid", [
    ("int", 2**63, True),
    ("float", float("inf"), True),
    ("float", float("-inf"), True),
    ("float", float("nan"), False),
    ("int", float("nan"), False),
    ("decimal", float("nan"), True),
])
def test_original_numeric_admission_distinguishes_missing_from_invalid(backend_name, data_type, value, invalid):
    from mountainash_rules.engines.accumulate import resolve

    options = (
        dict(match_strategy="range", range_min_field="minimum", range_max_field="maximum",
             numeric_dtype=ma.DecimalDtype(10, 2), rounding="half_to_even",
             range_min_unbounded_field="lo_any", range_max_unbounded_field="hi_any")
        if data_type == "decimal" else {"rule_wildcard_field": "any_x"}
    )
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="x", data_type=data_type, **options)],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    source = (
        {"minimum": [Decimal("0.00")], "maximum": [Decimal("1.00")],
         "lo_any": [True], "hi_any": [True]}
        if data_type == "decimal" else
        {"x": [0.0 if data_type == "float" else 0], "any_x": [True]}
    )
    rules = frame(backend_name, {"uuid": ["A"], "amount": [7], **source})
    contexts = frame(backend_name, {"request_id": [1], "x": [value]})
    if value != value:
        # The Ibis fixture's pandas ingestion turns NaN into null; supply an
        # actual backend NaN so this exercises nonfinite admission, not null.
        contexts = contexts.with_columns(ma.lit(value, dtype=ma.MountainashDtype.FP64).alias("x"))
    result = resolve(rules, metadata, contexts).fetch()
    assert result.requests.select("admission_status", "reason_code").to_dicts() == [{
        "admission_status": "invalid" if invalid else "admitted",
        "reason_code": "invalid_value" if invalid else None,
    }]
    assert result.payload("default").to_dicts() == ([] if invalid else [{"request_id": 1, "v0001": 7}])


def cell_profile_metadata(*, mode="resolve", values=(2, 12)):
    from mountainash_rules.core.codec import canonical_bytes
    from mountainash_rules.core.contracts import ContextContract
    from tests.accumulate.test_domain import node, scalar

    graph = {}
    encoded = sorted((scalar(value) for value in values), key=lambda value: canonical_bytes({"value": value}))
    predicate = (
        node(graph, "eq", field="x", value=encoded[0]) if len(encoded) == 1
        else node(graph, "in", field="x", values=encoded)
    )
    provider = domain(predicate, DomainField(name="x", data_type="int"))
    profile = dict(
        profile_id="decision", mode=mode, output_fields=["default.total"],
        provenance="cell", dimensions=["x"], allow_dont_care=["x"],
        promise="candidate_only" if mode == "candidates" else "allow_unresolved",
        on_unresolved=None if mode == "candidates" else "return",
    )
    return DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="x", data_type="int", match_strategy="range",
                              range_min_field="minimum", range_max_field="maximum")],
        aggregates=[Aggregate(column_name="amount", output_name="total", data_type="int")],
        context_domains=[provider], domain_nodes=graph,
        context_contracts=[ContextContract.model_validate(dict(
            schema_version=1, contract_id="P",
            fields=[dict(name="x", data_type="int", required=False)],
            domain_ref="D", profiles=[profile],
        ))],
    )


def test_cell_promise_distinguishes_components_without_clipping_identity_to_provider(backend_name, resolve_operation):
    contexts = frame(backend_name, {"request_id": [1, 2, 3], "x": [0, 2, 12]}).with_columns(
        ma.when(ma.col("request_id").eq(1)).then(ma.lit(None, dtype=ma.MountainashDtype.I64))
        .otherwise(ma.col("x")).alias("x"),
    )

    result = resolve_operation(
        frame(backend_name, {"uuid": ["A", "B"], "minimum": [0, 5],
                             "maximum": [20, 10], "amount": [10, 5]}),
        cell_profile_metadata(), contexts,
        request_control=frame(backend_name, {
            "request_id": [1, 2, 3], "contract_id": ["P"] * 3, "profile_id": ["decision"] * 3,
        }),
    ).fetch()
    assert result.status.sort("request_id").select(
        "status", "contributors_determined", "cell_determined",
    ).to_dicts() == [
        {"status": "unresolved", "contributors_determined": True, "cell_determined": False},
        {"status": "resolved", "contributors_determined": True, "cell_determined": True},
        {"status": "resolved", "contributors_determined": True, "cell_determined": True},
    ]
    assert result.payload("default").sort("request_id").to_dicts() == [
        {"request_id": 2, "v0001": 10}, {"request_id": 3, "v0001": 10},
    ]
    assert result.cell_identity_ordered_bounds["x"].sort("request_id").select(
        "request_id", "lower_value", "upper_value", "lower_inclusive", "upper_inclusive",
    ).to_dicts() == [
        {"request_id": 2, "lower_value": 0, "upper_value": 5, "lower_inclusive": True, "upper_inclusive": False},
        {"request_id": 3, "lower_value": 10, "upper_value": 20, "lower_inclusive": False, "upper_inclusive": True},
    ]


@pytest.mark.parametrize("value,candidate_count", [(2, 1), (100, 0)])
def test_candidate_profile_never_turns_one_or_zero_candidates_into_a_decision(backend_name, value, candidate_count, resolve_operation):

    result = resolve_operation(
        frame(backend_name, {"uuid": ["A", "B"], "minimum": [0, 5],
                             "maximum": [20, 10], "amount": [10, 5]}),
        cell_profile_metadata(mode="candidates", values=(value,)),
        frame(backend_name, {"request_id": [1], "x": [value]}),
        request_control=frame(backend_name, {
            "request_id": [1], "contract_id": ["P"], "profile_id": ["decision"],
        }),
    ).fetch()
    assert result.status.select("status", "disposition", "reason_code").to_dicts() == [
        {"status": "unresolved", "disposition": "candidates", "reason_code": "candidate_query"},
    ]
    assert result.payload("default").to_dicts() == []
    assert result.candidates.count_rows() == candidate_count


def test_stored_resolution_reads_retained_outputs_after_reload_without_refolding(backend_name, tmp_path):
    from dataclasses import replace
    from mountainash_rules.engines.accumulate.build import build

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="x", data_type="int", match_strategy="range",
                              range_min_field="minimum", range_max_field="maximum")],
        aggregates=[Aggregate(column_name="amount", output_name="total", data_type="int")],
    )
    lattice = build(frame(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [20, 10], "amount": [10, 5],
    }), metadata)
    output_id = next(item.table_id for item in lattice.table_descriptors if item.role == "outputs")
    tables = dict(lattice.tables)
    tables[output_id] = tables[output_id].with_columns((ma.col("v0001") + 100).alias("v0001"))
    retained = replace(lattice, tables=tables).publish()
    retained.save(tmp_path / "stored")
    restored = type(lattice).load(tmp_path / "stored")
    result = restored.resolve(
        frame(backend_name, {"request_id": [1, 2, 3], "x": [2, 7, 100]}),
        include_contributors=True,
    ).fetch()
    assert result.payload("default").sort("request_id").to_dicts() == [
        {"request_id": 1, "v0001": 110}, {"request_id": 2, "v0001": 115},
        {"request_id": 3, "v0001": None},
    ]
    assert result.contributors.sort("request_id", "source_uuid").to_dicts() == [
        {"request_id": 1, "scope": "default", "source_uuid": "A"},
        {"request_id": 2, "scope": "default", "source_uuid": "A"},
        {"request_id": 2, "scope": "default", "source_uuid": "B"},
    ]


def test_declared_dont_care_requires_permission_for_an_observed_profile_dimension(backend_name):
    from mountainash_rules.engines.accumulate.resolve import resolve

    authored = controlled_metadata().model_dump()
    authored["dimensions"][0]["wildcard_values"] = ["*"]
    base = authored["context_contracts"][0]["profiles"][0]
    authored["context_contracts"][0]["profiles"] = [
        dict(base, profile_id="allow"),
        dict(base, profile_id="forbid", allow_dont_care=[]),
        dict(base, profile_id="project_away", dimensions=[], allow_dont_care=[]),
    ]
    result = resolve(
        frame(backend_name, {"uuid": ["E", "U"], "region": ["EU", "US"], "amount": [10, 10]}),
        DimensionsMetadata.model_validate(authored),
        frame(backend_name, {"request_id": [1, 2, 3], "region": ["*"] * 3}),
        request_control=frame(backend_name, {
            "request_id": [1, 2, 3], "contract_id": ["P"] * 3,
            "profile_id": ["allow", "forbid", "project_away"],
        }),
    ).fetch()
    assert result.status.sort("request_id").select("status").to_dicts() == [
        {"status": "resolved"}, {"status": "invalid"}, {"status": "resolved"},
    ]
    assert result.payload("default").sort("request_id").to_dicts() == [
        {"request_id": 1, "v0001": 10}, {"request_id": 3, "v0001": 10},
    ]


@pytest.mark.parametrize("source_aware", [False, True])
@pytest.mark.parametrize("context_aware", [False, True])
def test_datetime_awareness_controls_request_admission(backend_name, source_aware, context_aware):
    import datetime as dt
    from mountainash_rules.engines.accumulate.resolve import resolve

    source_zone = dt.timezone.utc if source_aware else None
    context_zone = dt.timezone.utc if context_aware else None
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="x", data_type="datetime", match_strategy="range",
                              range_min_field="minimum", range_max_field="maximum")],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    result = resolve(
        frame(backend_name, {
            "uuid": ["A"], "minimum": [dt.datetime(2026, 1, 1, tzinfo=source_zone)],
            "maximum": [dt.datetime(2026, 1, 3, tzinfo=source_zone)], "amount": [7],
        }), metadata,
        frame(backend_name, {"request_id": [1], "x": [dt.datetime(2026, 1, 2, tzinfo=context_zone)]}),
    ).fetch()
    compatible = source_aware == context_aware
    assert result.requests.select("admission_status").to_dicts() == [
        {"admission_status": "admitted" if compatible else "invalid"}
    ]
    assert result.status.select("status").to_dicts() == [
        {"status": "resolved" if compatible else "invalid"}
    ]
    if compatible:
        assert payload_values(result, output="amount") == [{"request_id": 1, "value": 7}]
    else:
        assert result.payload("default").to_dicts() == []


@pytest.mark.parametrize("source_zone", ["UTC", "America/New_York"])
@pytest.mark.parametrize("context_zone", ["UTC", "America/New_York"])
@pytest.mark.parametrize("strategy", ["exact", "range", "set_membership", "set_exclusion"])
def test_aware_datetime_operands_preserve_instants_across_timezones(
    backend_name, source_zone, context_zone, strategy,
):
    import datetime as dt
    from zoneinfo import ZoneInfo
    from mountainash_rules.engines.accumulate.resolve import resolve

    if backend_name == "polars" and source_zone != "UTC" and strategy.startswith("set_"):
        pytest.skip("Owner-deferred non-UTC DATETIME set normalization; backlog item 295")

    instant = dt.datetime(2026, 1, 1, 5, 30, tzinfo=dt.timezone.utc)
    source_value = instant.astimezone(ZoneInfo(source_zone))
    rules = {"uuid": ["A"], "amount": [7]}
    options = {}
    if strategy == "range":
        rules.update(minimum=[source_value], maximum=[source_value])
        options = {"range_min_field": "minimum", "range_max_field": "maximum"}
    else:
        rules["x"] = [[source_value]] if strategy.startswith("set_") else [source_value]
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="x", data_type="datetime",
                              match_strategy=strategy, **options)],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    result = resolve(
        frame(backend_name, rules), metadata,
        frame(backend_name, {
            "request_id": [1, 2],
            "x": [value.astimezone(ZoneInfo(context_zone)) for value in
                  (instant, instant + dt.timedelta(hours=1))],
        }),
    ).fetch()
    assert result.status.sort("request_id").select("status").to_dicts() == [
        {"status": "resolved"}, {"status": "resolved"},
    ]
    expected = [None, 7] if strategy == "set_exclusion" else [7, None]
    assert payload_values(result, output="amount") == [
        {"request_id": index, "value": value} for index, value in enumerate(expected, 1)
    ]


def test_direct_source_resolution_captures_before_deferred_fetch():
    import ibis
    from mountainash_rules.engines.accumulate.resolve import resolve

    connection = ibis.duckdb.connect()
    native = connection.create_table(
        "captured_source", {"uuid": ["A"], "region": ["EU"], "amount": [7]},
    )
    metadata = DimensionsMetadata(
        uuid_field="uuid", dimensions=[Dimension(dimension_name="region")],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(native)
    contexts = frame("polars", {"request_id": [1], "region": ["EU"]})
    operation = resolve(rules, metadata, contexts, include_contributors=True)
    connection.raw_sql('UPDATE captured_source SET amount = 99')
    assert connection.table("captured_source").execute()["amount"].tolist() == [99]
    result = operation.fetch(include_matches=True)
    connection.drop_table("captured_source")
    del operation, rules, contexts
    assert payload_values(result, output="amount") == [{"request_id": 1, "value": 7}]
    assert result.contributors.select("source_uuid").to_dicts() == [{"source_uuid": "A"}]
    assert result.matches.version_id is None
    assert result.matches.scope("default").select("source_uuid", "v0001").to_dicts() == [
        {"source_uuid": "A", "v0001": 7},
    ]


def test_stored_decisions_keep_no_match_alternatives_and_profile_domain(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    metadata = controlled_metadata()
    lattice = build(frame(backend_name, {
        "uuid": ["E", "U"], "region": ["EU", "US"], "amount": [7, 7],
    }), metadata)
    records = [
        {"request_id": 1, "context": {"region": "EU"}},
        {"request_id": 2, "context": {"region": "APAC"}},
        {"request_id": 3, "context": {}},
        {"request_id": 4, "context": {}, "contract_id": "P", "profile_id": "decision"},
    ]
    result = lattice.resolve(records).fetch(include_matches=True)
    assert result.status.select("request_id", "status").sort("request_id").to_dicts() == [
        {"request_id": 1, "status": "resolved"},
        {"request_id": 2, "status": "resolved"},
        {"request_id": 3, "status": "unresolved"},
        {"request_id": 4, "status": "resolved"},
    ]
    assert payload_values(result, output="amount") == [
        {"request_id": 1, "value": 7},
        {"request_id": 2, "value": None},
        {"request_id": 4, "value": 7},
    ]
    assert result.matches.scope("default").select("request_id", "v0001").sort("request_id").to_dicts() == [
        {"request_id": 1, "v0001": 7},
        {"request_id": 3, "v0001": 7}, {"request_id": 3, "v0001": 7},
        {"request_id": 4, "v0001": 7}, {"request_id": 4, "v0001": 7},
    ]


def test_direct_combined_retains_contributions_not_folded_or_fabricated_cells(backend_name):
    from mountainash_rules.engines.accumulate.resolve import resolve

    metadata = controlled_metadata()
    result = resolve(
        frame(backend_name, {
            "uuid": ["E1", "E2", "U"], "region": ["EU", "EU", "US"],
            "amount": [7, -7, 9],
        }), metadata,
        frame(backend_name, {
            "request_id": [1, 2, 3], "region": ["EU", None, "APAC"],
        }),
    ).fetch(include_matches=True)
    assert payload_values(result, output="amount") == [
        {"request_id": 1, "value": 0}, {"request_id": 3, "value": None},
    ]
    assert result.matches.scope("default").select(
        "request_id", "source_uuid", "v0001",
    ).sort("request_id", "source_uuid").to_dicts() == [
        {"request_id": 1, "source_uuid": "E1", "v0001": 7},
        {"request_id": 1, "source_uuid": "E2", "v0001": -7},
        {"request_id": 2, "source_uuid": "E1", "v0001": 7},
        {"request_id": 2, "source_uuid": "E2", "v0001": -7},
        {"request_id": 2, "source_uuid": "U", "v0001": 9},
    ]
    assert result.matches.cell_identity is None
    assert "cell_id" not in result.matches.scope("default").columns

def test_stored_empty_scopes_preserve_requested_empty_evidence(backend_name):
    from mountainash_rules.engines.accumulate.build import build

    lattice = build(frame(backend_name, {
        "uuid": ["E"], "region": ["EU"], "amount": [7],
    }), controlled_metadata())
    result = lattice.resolve(
        [{"request_id": 1, "context": {"region": "EU"}}], scopes=[],
        include_contributors=True, include_candidates=True, include_cell_key=True,
    ).fetch(include_matches=True)
    assert result.requests.select("request_id", "admission_status").to_dicts() == [
        {"request_id": 1, "admission_status": "admitted"},
    ]
    for name in (
        "contributors", "candidates", "cell_identity", "cell_identity_boxes",
        "cell_identity_contributors",
    ):
        evidence = getattr(result, name)
        assert evidence is not None
        assert evidence.to_dicts() == []
    assert result.status.to_dicts() == []
    assert result.matches.contributors.to_dicts() == []
