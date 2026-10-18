"""Public stored retrieval preserves admission, cell identity and typed results."""

import mountainash as ma
import pytest

from mountainash_rules import Dimension, DimensionsMetadata
from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.engines.accumulate.build import build
from mountainash_rules.engines.accumulate.errors import RequestValidationError
from tests.conftest import build_backend_df


def range_lattice(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="lo", range_max_field="hi",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    return build(ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "lo": [0, 5], "hi": [10, 15], "amount": [5, 2],
    }, "retrieval_rules")), metadata)


def test_query_retains_stored_matches_and_original_admission(backend_name):
    lattice = range_lattice(backend_name)
    result = lattice.query([
        {"request_id": "overlap", "context": {"x": 7}},
        {"request_id": "outside", "context": {"x": 16}},
        {"request_id": "invalid", "context": {"x": "7"}},
    ], include_contributors=True).fetch()
    assert result.requests.select("request_id", "admission_status").to_dicts() == [
        {"request_id": "overlap", "admission_status": "admitted"},
        {"request_id": "outside", "admission_status": "admitted"},
        {"request_id": "invalid", "admission_status": "invalid"},
    ]
    value = result.payload_columns["default"]["amount"]
    assert result.scope("default").select("request_id", value).to_dicts() == [
        {"request_id": "overlap", value: 7},
    ]
    assert result.scope("default").collect_schema()[value] == ma.MountainashDtype.I64
    assert result.contributors.select("source_uuid").sort("source_uuid").to_dicts() == [
        {"source_uuid": "A"}, {"source_uuid": "B"},
    ]


def test_query_partial_context_returns_cells_without_policy_reduction(backend_name):
    result = range_lattice(backend_name).query([
        {"request_id": 4, "context": {}},
    ]).fetch()
    value = result.payload_columns["default"]["amount"]
    assert result.scope("default").select(value).sort(value).to_dicts() == [
        {value: 2}, {value: 5}, {value: 7},
    ]


def test_query_empty_scope_selection_still_admits_requests(backend_name):
    result = range_lattice(backend_name).query([
        {"request_id": 9, "context": {"x": "bad"}},
        {"request_id": 2, "context": {}},
    ], scopes=[]).fetch()
    assert result.scopes == ()
    assert result.requests.select("request_id", "admission_status").to_dicts() == [
        {"request_id": 9, "admission_status": "invalid"},
        {"request_id": 2, "admission_status": "admitted"},
    ]


@pytest.mark.parametrize("ids", [[1, "1"], [1, 1], [None], [True]])
def test_record_identity_errors_abort_the_operation(backend_name, ids):
    lattice = range_lattice(backend_name)
    with pytest.raises(RequestValidationError):
        lattice.query([
            {"request_id": identifier, "context": {"x": 7}} for identifier in ids
        ]).fetch()


def test_query_datetime_awareness_without_explicit_domain_timezone(backend_name, tmp_path):
    from datetime import datetime, timezone
    import polars as pl
    import yaml

    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="at", data_type="datetime", match_strategy="range",
            range_min_field="lo", range_max_field="hi",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    lattice = build(ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "amount": [7],
        "lo": [datetime(2026, 1, 1, tzinfo=timezone.utc)],
        "hi": [datetime(2027, 1, 1, tzinfo=timezone.utc)],
    }, "aware_retrieval")), metadata)
    destination = tmp_path / "temporal"
    lattice.save(destination)
    lattice = type(lattice).load(destination)
    result = lattice.query([
        {"request_id": 1, "context": {"at": datetime(2026, 6, 1, tzinfo=timezone.utc)}},
        {"request_id": 2, "context": {"at": datetime(2026, 6, 1)}},
    ], include_cell_key=True).fetch()
    assert result.requests.select("request_id", "admission_status").to_dicts() == [
        {"request_id": 1, "admission_status": "admitted"},
        {"request_id": 2, "admission_status": "invalid"},
    ]
    value = result.payload_columns["default"]["amount"]
    assert result.scope("default").select("request_id", value).to_dicts() == [
        {"request_id": 1, value: 7},
    ]
    bounds = result.cell_identity_ordered_bounds["at"]
    assert bounds.select("lower_value", "upper_value").to_dicts() == [{
        "lower_value": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "upper_value": datetime(2027, 1, 1, tzinfo=timezone.utc),
    }]
    dtype = bounds.collect_schema()["lower_value"]
    assert dtype.precision == 6 and dtype.timezone_aware is True

    manifest_data = yaml.safe_load((destination / "manifest.yaml").read_text())
    descriptor = next(row for row in manifest_data["tables"] if row["role"] == "cell_ordered_bounds")
    path = destination / descriptor["path"]
    frame = pl.read_parquet(path)
    for incompatible in (pl.Datetime("ms", "UTC"), pl.Datetime("us")):
        frame.with_columns(pl.col("lower_value").cast(incompatible)).write_parquet(path)
        with pytest.raises(ValueError):
            type(lattice).load(destination)


@pytest.mark.parametrize("field", ["request_id", "__record_id", "__record_order"])
def test_record_transport_preserves_authored_context_field_names(backend_name, field):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(dimension_name="axis", context_field=field,
                              data_type="int", match_strategy="exact")],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    lattice = build(ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "axis": [5], "amount": [7],
    }, "transport_collision")), metadata)
    result = lattice.query([
        {"request_id": 50, "context": {field: 5}},
        {"request_id": 51, "context": {}},
        {"request_id": 52, "context": {field: 11}},
    ]).fetch()
    assert result.scope("default").select("request_id", "v0001").sort("request_id").to_dicts() == [
        {"request_id": 50, "v0001": 7}, {"request_id": 51, "v0001": 7},
    ]


def test_query_mask_cannot_legalize_invalid_original_values(backend_name):
    from tests.accumulate.test_resolve import controlled_metadata

    lattice = build(ma.relation(build_backend_df(backend_name, {
        "uuid": ["E", "U", "A"], "region": ["EU", "US", "APAC"], "amount": [1, 2, 3],
    }, "query_profiles")), controlled_metadata())
    result = lattice.query([
        {"request_id": name, "context": {"region": value}, "contract_id": "P",
         "profile_id": "decision", "masks": ["region"]}
        for name, value in (("masked", "EU"), ("outside", "APAC"), ("bad", 7))
    ]).fetch()
    assert result.requests.select("request_id", "admission_status").to_dicts() == [
        {"request_id": "masked", "admission_status": "admitted"},
        {"request_id": "outside", "admission_status": "invalid"},
        {"request_id": "bad", "admission_status": "invalid"},
    ]
    assert result.scope("default").select("request_id", "v0001").sort("v0001").to_dicts() == [
        {"request_id": "masked", "v0001": 1},
        {"request_id": "masked", "v0001": 2},
    ]


def test_query_corrupt_output_fails_before_any_result_publication(backend_name):
    from dataclasses import replace

    lattice = range_lattice(backend_name)
    descriptor = next(item for item in lattice.table_descriptors if item.role == "outputs")
    tables = dict(lattice.tables)
    tables[descriptor.table_id] = lattice.outputs["default"].filter(ma.lit(False))
    operation = replace(lattice, tables=tables).query([
        {"request_id": 1, "context": {"x": 7}},
        {"request_id": 2, "context": {"x": 16}},
    ])
    with pytest.raises(ValueError) as failure:
        operation.fetch()
    assert not isinstance(failure.value, RequestValidationError)


def test_empty_batch_retains_typed_selected_scope(backend_name):
    result = range_lattice(backend_name).query([], include_cell_key=True).fetch()
    assert result.requests.to_dicts() == []
    assert result.scope("default").to_dicts() == []
    assert result.scope("default").collect_schema()["v0001"] == ma.MountainashDtype.I64


def test_native_duplicate_identifiers_fail_at_fetch(backend_name):
    lattice = range_lattice(backend_name)
    operation = lattice.query(ma.relation(build_backend_df(backend_name, {
        "request_id": [1, 1], "x": [7, 16],
    }, "duplicate_query_ids")))
    with pytest.raises(RequestValidationError):
        operation.fetch()


def test_inline_and_explicit_channels_accept_extra_reordered_columns(backend_name):
    from tests.accumulate.test_resolve import controlled_metadata

    lattice = build(ma.relation(build_backend_df(backend_name, {
        "uuid": ["E", "U"], "region": ["EU", "US"], "amount": [1, 2],
    }, "mixed_channel_rules")), controlled_metadata())
    controls = ma.relation({
        "extra": ["ignored"], "profile_id": ["decision"], "request_id": [2], "contract_id": ["P"],
    })
    masks = ma.relation({
        "dimension_name": ["region"], "extra": ["ignored"], "request_id": [2],
    })
    result = lattice.query([
        {"request_id": 1, "context": {"region": "EU"}, "contract_id": "P",
         "profile_id": "decision", "masks": ["region"]},
        {"request_id": 2, "context": {"region": "EU"}},
    ], request_control=controls, masks=masks).fetch()
    assert result.scope("default").select("request_id", "v0001").sort("request_id", "v0001").to_dicts() == [
        {"request_id": 1, "v0001": 1}, {"request_id": 1, "v0001": 2},
        {"request_id": 2, "v0001": 1}, {"request_id": 2, "v0001": 2},
    ]


def test_wrong_typed_mask_retains_request_validation_error(backend_name):
    lattice = range_lattice(backend_name)
    with pytest.raises(RequestValidationError):
        lattice.query([{"request_id": 1, "context": {"x": 7}}],
                      masks=ma.relation({"request_id": [1], "dimension_name": [42]})).fetch()


@pytest.mark.parametrize("invalid_row", [False, True])
def test_mixed_channel_validation_tracks_original_rows_not_empty_schema(backend_name, invalid_row):
    from tests.accumulate.test_resolve import controlled_metadata

    lattice = build(ma.relation(build_backend_df(backend_name, {
        "uuid": ["E", "U"], "region": ["EU", "US"], "amount": [1, 2],
    }, "mixed_mask_rules")), controlled_metadata())
    masks = ma.relation(build_backend_df(backend_name, {
        "request_id": [1], "dimension_name": [42],
    }, "mixed_mask_channel"))
    if not invalid_row:
        masks = masks.filter(ma.lit(False))
    operation = lattice.query([{
        "request_id": 1, "context": {"region": "EU"}, "contract_id": "P",
        "profile_id": "decision", "masks": ["region"],
    }], masks=masks)
    if invalid_row:
        with pytest.raises(RequestValidationError):
            operation.fetch()
    else:
        result = operation.fetch()
        assert result.scope("default").select("v0001").sort("v0001").to_dicts() == [
            {"v0001": 1}, {"v0001": 2},
        ]


@pytest.mark.parametrize(
    "partition,wrong,unknown",
    [("EU", 7, "elsewhere"), ("EU", b"EU", "elsewhere"), (7, "7", 8)],
)
def test_partition_admission_preserves_valid_rows_beside_bad_raw_keys(backend_name, partition, wrong, unknown):
    metadata = DimensionsMetadata(
        uuid_field="uuid", partition_field="market",
        dimensions=[Dimension(dimension_name="x", data_type="int", match_strategy="exact")],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "market": [partition], "x": [5], "amount": [7],
    }, "partition_retrieval"))
    if isinstance(partition, int):
        rules = rules.with_columns(ma.col("market").cast(ma.MountainashDtype.I32))
    lattice = build(rules, metadata)
    result = lattice.query([
        {"request_id": 1, "context": {"market": partition, "x": 5}},
        {"request_id": 2, "context": {"market": None, "x": 5}},
        {"request_id": 3, "context": {"x": 5}},
        {"request_id": 4, "context": {"market": wrong, "x": 5}},
        {"request_id": 5, "context": {"market": unknown, "x": 5}},
    ]).fetch()
    assert result.requests.select("request_id", "admission_status", "reason_code").to_dicts() == [
        {"request_id": 1, "admission_status": "admitted", "reason_code": None},
        *[{"request_id": request_id, "admission_status": "invalid", "reason_code": "invalid_partition"}
          for request_id in (2, 3, 4, 5)],
    ]
    assert result.scope("default").select("request_id", "v0001").to_dicts() == [
        {"request_id": 1, "v0001": 7},
    ]
