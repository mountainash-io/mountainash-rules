"""Exact accumulator lattice routing, identity, inspection, and snapshot contracts."""

import mountainash_rules as rules
import polars as pl
import pytest

from mountainash_rules import (
    UNKNOWN,
    UNKNOWN_NUMERIC,
    AccumulatorEngine,
    Aggregate,
    Dimension,
    DimensionRole,
    DimensionsMetadata,
    Lattice,
    MatchStrategy,
)
from tests.accumulator.exact_runtime_fixtures import declarations, gate, uuid


ROUTING_TOTAL = rules.Aggregate(
    column_name="margin",
    output_name="pricing.margin",
    data_type="float",
    numeric_semantics="numeric-1",
)


class TestAggregate:
    def test_default_operation_is_sum(self):
        assert Aggregate(column_name="margin").operation == "sum"

    def test_explicit_operation(self):
        assert Aggregate(column_name="margin", operation="max").operation == "max"

    def test_column_name_required(self):
        with pytest.raises(Exception):
            Aggregate()


class TestLatticeInspection:
    def test_flat_lattice_remains_inspectable_but_has_no_exact_lineage(self):
        flat = Lattice(
            dataframe=pl.DataFrame({"rule_name": ["legacy"], "region": ["AU"]}),
            metadata=DimensionsMetadata(
                dimensions=[Dimension(dimension_name="region")]
            ),
            aggregates=[],
            partition_key=None,
        )

        assert flat.artifact_kind is None
        assert flat.count == 1
        assert flat.combinations.to_dicts() == [{"rule_name": "legacy", "region": "AU"}]
        with pytest.raises(ValueError, match="inspection-only"):
            flat.lineage("not-a-cell")


def _candidate_contract(dimensions, *, required=(), masks=()):
    return rules.ContextContract(
        schema_version=1,
        contract_id="client",
        domain_ref="D",
        fields=[
            rules.ContextField(
                name=dimension.resolved_context_field,
                data_type=dimension.data_type,
                required=dimension.resolved_context_field in required,
            )
            for dimension in sorted(
                dimensions, key=lambda item: item.resolved_context_field
            )
        ],
        profiles=[
            rules.ResolutionProfile(
                profile_id="inspect",
                mode="candidates",
                output_fields=["pricing.margin"],
                provenance="none",
                dimensions=sorted(
                    dimension.dimension_name
                    for dimension in dimensions
                    if dimension.role is rules.DimensionRole.CONSTRAINT
                    and dimension.match_strategy
                    is not rules.MatchStrategy.CONTEXT_REGEX
                ),
                allow_dont_care=sorted(masks),
                promise="candidate_only",
            )
        ],
    )


def _candidate_facts(result):
    cells = result.candidate_cells
    contributors = result.candidate_contributors
    assert cells is not None and contributors is not None
    values = {row["cell_id"]: row["pricing.margin"] for row in cells.to_dicts()}
    members = {}
    for row in contributors.to_dicts():
        members.setdefault(row["cell_id"], set()).add(row["source_id"])
    return sorted(
        (values[cell_id], tuple(sorted(source_ids)))
        for cell_id, source_ids in members.items()
    )


def _partition(lattices, **expected):
    return next(lattice for lattice in lattices if lattice.partition_key == expected)


def _wildcard_partition(lattices):
    return next(
        lattice
        for lattice in lattices
        if all(
            key["match"]["kind"] == "wildcard"
            for key in lattice.partition_identity["key_values"]
        )
    )


def _routing_artifacts(*, alias=False, tenant_required=True, margin_b=20.0):
    dimensions = [
        rules.Dimension(
            dimension_name="tenant",
            context_field="account" if alias else None,
            data_type="str",
            match_strategy="exact_key",
            role=rules.DimensionRole.CONTEXT_KEY,
        ),
        rules.Dimension(dimension_name="region", match_strategy="exact"),
        rules.Dimension(
            dimension_name="approved", data_type="bool", match_strategy="exact"
        ),
    ]
    contract = _candidate_contract(
        dimensions,
        required=(
            ("account" if alias else "tenant", "approved")
            if tenant_required
            else ("approved",)
        ),
        masks=("approved",),
    )
    rows = [
        {
            "id": uuid(1),
            "tenant": "A",
            "region": "AU",
            "approved": True,
            "margin": 10.0,
        },
        {
            "id": uuid(2),
            "tenant": "B",
            "region": "NZ",
            "approved": True,
            "margin": margin_b,
        },
    ]
    kwargs = declarations(
        dimensions,
        [ROUTING_TOTAL],
        contracts=[contract],
        partition_keys=(("A",), ("B",), (None,)),
    )
    validation = gate(rows, kwargs)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    return engine, engine.build_all(rows, validation=validation)


def _crossing_artifacts(*, covered, contracts=()):
    dimensions = [
        rules.Dimension(
            dimension_name="region",
            data_type="str",
            match_strategy="exact_key",
            role=rules.DimensionRole.CONTEXT_KEY,
        ),
        rules.Dimension(
            dimension_name="channel",
            data_type="str",
            match_strategy="exact_key",
            role=rules.DimensionRole.CONTEXT_KEY,
        ),
        rules.Dimension(dimension_name="product", match_strategy="exact"),
    ]
    partitions = [(None, "AU"), ("BROKER", None), (None, None)]
    if covered:
        partitions.insert(2, ("BROKER", "AU"))
    kwargs = declarations(
        dimensions,
        [ROUTING_TOTAL],
        contracts=contracts,
        partition_keys=partitions,
    )
    rows = [
        {
            "id": uuid(30),
            "region": "AU",
            "channel": "BROKER" if covered else rules.UNKNOWN,
            "product": "GOLD",
            "margin": 30.0,
        }
    ]
    validation = gate(rows, kwargs)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    return engine, engine.build_all(rows, validation=validation), dimensions


class TestExactRouting:
    def test_specific_and_declared_empty_default_are_exact_outcomes(self):
        engine, lattices = _routing_artifacts()
        index = engine.index(lattices)

        specific = index.apply(
            {"tenant": "A", "region": "AU", "approved": True},
            contract_id="client",
            profile_id="inspect",
        )
        default = index.apply(
            {"tenant": "other", "region": "AU", "approved": True},
            contract_id="client",
            profile_id="inspect",
        )

        assert _candidate_facts(specific) == [(10.0, (uuid(1),))]
        assert default.status == "candidates"
        assert default.candidate_cells.count_rows() == 0

    def test_index_rejects_selected_views_missing_a_reachable_partition(self):
        engine, lattices = _routing_artifacts()

        with pytest.raises(ValueError):
            engine.index(
                [
                    _partition(lattices, tenant="A"),
                    _wildcard_partition(lattices),
                ]
            )

    def test_index_rejects_mixed_source_generations_with_matching_contract_labels(self):
        engine, lattices = _routing_artifacts()
        _, rebuilt = _routing_artifacts(margin_b=21.0)
        with pytest.raises(ValueError):
            engine.index(
                [
                    _partition(lattices, tenant="A"),
                    _partition(rebuilt, tenant="B"),
                    _wildcard_partition(lattices),
                ]
            )

    def test_crossing_key_patterns_are_rejected_without_policy_bypass(self):
        engine, lattices, _ = _crossing_artifacts(covered=False)

        with pytest.raises(rules.AmbiguousPartitionError):
            engine.index(lattices)

    def test_exact_cover_resolves_crossing_pair_without_routing_error(self):
        dimensions = [
            rules.Dimension(
                dimension_name="region",
                data_type="str",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(
                dimension_name="channel",
                data_type="str",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(dimension_name="product", match_strategy="exact"),
        ]
        contract = _candidate_contract(dimensions, required=("region", "channel"))
        engine, lattices, _ = _crossing_artifacts(covered=True, contracts=[contract])

        result = engine.index(lattices).apply(
            {"region": "AU", "channel": "BROKER", "product": "GOLD"},
            contract_id="client",
            profile_id="inspect",
        )

        assert _candidate_facts(result) == [(30.0, (uuid(30),))]

    def test_three_key_partial_cover_defers_lower_score_tie_until_routing_finishes(
        self,
    ):
        dimensions = [
            rules.Dimension(
                dimension_name="region",
                data_type="str",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(
                dimension_name="channel",
                data_type="str",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(
                dimension_name="tier",
                data_type="int",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(dimension_name="product", match_strategy="exact"),
        ]
        contract = _candidate_contract(
            dimensions, required=("region", "channel", "tier")
        )
        rows = [
            {
                "id": uuid(31),
                "region": "AU",
                "channel": "BROKER",
                "tier": rules.UNKNOWN_NUMERIC,
                "product": "GOLD",
                "margin": 31.0,
            }
        ]
        kwargs = declarations(
            dimensions,
            [ROUTING_TOTAL],
            contracts=[contract],
            partition_keys=(
                (None, "AU", None),
                ("BROKER", None, None),
                ("BROKER", "AU", None),
                (None, None, None),
            ),
        )
        validation = gate(rows, kwargs)
        engine = rules.AccumulatorEngine(
            kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
        )
        index = engine.index(engine.build_all(rows, validation=validation))

        result = index.apply(
            {"region": "AU", "channel": "BROKER", "tier": 1, "product": "GOLD"},
            contract_id="client",
            profile_id="inspect",
        )

        assert result.status == "candidates"

    def test_exact_artifact_retains_native_candidate_inspection(self):
        engine, lattices = _routing_artifacts()
        lattice = _partition(lattices, tenant="A")
        result = engine.apply(
            lattice,
            {"tenant": "A", "region": "AU", "approved": True},
            contract_id="client",
            profile_id="inspect",
        )
        (cell_id,) = [row["cell_id"] for row in result.candidate_cells.to_dicts()]

        assert lattice.artifact_kind == "exact_cells"
        assert lattice.partition_identity["key_values"][0]["dimension_name"] == "tenant"
        assert {
            row["source_id"] for row in result.candidate_lineage(cell_id).to_dicts()
        } == {uuid(1)}
        with pytest.raises(KeyError):
            result.candidate_lineage("not-a-candidate")

    def test_index_requires_distinct_exact_artifacts(self):
        engine, lattices = _routing_artifacts()
        exact = _partition(lattices, tenant="A")
        flat = Lattice(
            dataframe=exact.combinations,
            metadata=exact.metadata,
            aggregates=exact.aggregates,
            partition_key=exact.partition_key,
        )

        with pytest.raises(ValueError):
            engine.index([])
        with pytest.raises(ValueError, match="Duplicate"):
            engine.index([*lattices, exact])
        with pytest.raises(ValueError, match="inspection-only"):
            engine.index([exact, flat])


class TestExactBatchIdentity:
    def test_native_ids_correlate_outcomes_and_unknown_lookup_is_keyerror(self):
        engine, lattices = _routing_artifacts()
        index = engine.index(lattices)
        contexts = pl.DataFrame(
            {
                "request_id": ["a", "b", "default"],
                "tenant": ["A", "B", "other"],
                "region": ["AU", "NZ", "AU"],
                "approved": [True, True, True],
            }
        )

        batch = index.apply_batch(
            contexts,
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
            chunk_size=1,
        )

        assert list(batch.records) == ["a", "b", "default"]
        assert _candidate_facts(batch.for_context("a")) == [(10.0, (uuid(1),))]
        assert _candidate_facts(batch.for_context("b")) == [(20.0, (uuid(2),))]
        assert (
            batch.for_context("a").outcome
            == index.apply(
                {"tenant": "A", "region": "AU", "approved": True},
                contract_id="client",
                profile_id="inspect",
            ).outcome
        )
        with pytest.raises(KeyError):
            batch.for_context("never-submitted")

    @pytest.mark.parametrize(
        "contexts",
        [
            pl.DataFrame(
                {
                    "request_id": [7, 7],
                    "tenant": ["A", "B"],
                    "region": ["AU", "NZ"],
                    "approved": [True, True],
                }
            ),
            pl.DataFrame(
                {
                    "request_id": [7, None],
                    "tenant": ["A", "B"],
                    "region": ["AU", "NZ"],
                    "approved": [True, True],
                }
            ),
        ],
        ids=["duplicate-across-partitions", "null-id"],
    )
    def test_native_ids_are_rejected_globally_before_routing(self, contexts):
        engine, lattices = _routing_artifacts()
        with pytest.raises(ValueError):
            engine.index(lattices).apply_batch(
                contexts,
                contract_id="client",
                profile_id="inspect",
                context_id_field="request_id",
            )

    @pytest.mark.parametrize("field", ["missing", "", False, 0])
    def test_invalid_identity_field_is_not_regenerated(self, field):
        engine, lattices = _routing_artifacts()
        with pytest.raises(ValueError):
            engine.index(lattices).apply_batch(
                pl.DataFrame({"tenant": ["A"], "region": ["AU"], "approved": [True]}),
                contract_id="client",
                profile_id="inspect",
                context_id_field=field,
            )

    def test_reserved_canonical_identity_cannot_be_generated_over(self):
        engine, lattices = _routing_artifacts()
        with pytest.raises(ValueError):
            engine.index(lattices).apply_batch(
                pl.DataFrame(
                    {
                        "__context_id": [42],
                        "tenant": ["A"],
                        "region": ["AU"],
                        "approved": [True],
                    }
                ),
                contract_id="client",
                profile_id="inspect",
            )

    def test_aliased_key_and_typed_empty_native_ids_are_preserved(self):
        engine, lattices = _routing_artifacts(alias=True)
        index = engine.index(lattices)
        batch = index.apply_batch(
            pl.DataFrame(
                {
                    "request_id": [51],
                    "account": ["A"],
                    "region": ["AU"],
                    "approved": [True],
                }
            ),
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
        )
        empty = index.apply_batch(
            pl.DataFrame(
                schema={
                    "request_id": pl.Int64,
                    "account": pl.Utf8,
                    "region": pl.Utf8,
                    "approved": pl.Boolean,
                }
            ),
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
        )

        assert _candidate_facts(batch.for_context(51)) == [(10.0, (uuid(1),))]
        assert empty.context_ids.collect().schema["__context_id"] == pl.Int64
        assert list(empty.records) == []

    def test_empty_mapping_batch_preserves_declared_id_and_mask_columns(self):
        engine, lattices = _routing_artifacts()

        batch = engine.index(lattices).apply_batch(
            {
                "request_id": pl.Series("request_id", [], dtype=pl.Int64),
                "mask": pl.Series("mask", [], dtype=pl.List(pl.Utf8)),
            },
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
            dont_care_field="mask",
        )

        assert batch.context_ids.collect().schema["__context_id"] == pl.Int64
        assert list(batch.records) == []

    def test_out_of_domain_key_is_row_local_invalid_without_a_partition(self):
        dimensions = [
            rules.Dimension(
                dimension_name="tenant",
                data_type="str",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(dimension_name="product", match_strategy="exact"),
        ]
        contract = _candidate_contract(dimensions, required=("tenant",))
        rows = [
            {
                "id": uuid(40),
                "tenant": "A",
                "product": "GOLD",
                "margin": 40.0,
            }
        ]
        kwargs = declarations(
            dimensions,
            [ROUTING_TOTAL],
            contracts=[contract],
            domain_node={
                "op": "eq",
                "field": "tenant",
                "value": {"type": "str", "value": "A"},
            },
            partition_keys=(("A",),),
        )
        validation = gate(rows, kwargs)
        engine = rules.AccumulatorEngine(
            kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
        )
        index = engine.index(engine.build_all(rows, validation=validation))

        batch = index.apply_batch(
            [
                {"request_id": "valid", "tenant": "A", "product": "GOLD"},
                {"request_id": "outside", "tenant": "B", "product": "GOLD"},
            ],
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
        )

        assert _candidate_facts(batch.for_context("valid")) == [(40.0, (uuid(40),))]
        assert batch.records["outside"].status == "invalid_context"
        assert batch.records["outside"].reason == "domain_contradiction"
        assert batch.records["outside"].binding_id is None
        with pytest.raises(rules.InvalidContextError) as error:
            batch.for_context("outside")
        assert error.value.outcome is batch.records["outside"]


class TestBooleanAndR8Routing:
    def _artifacts(self, *, flag_required=True, return_validation=False):
        dimensions = [
            rules.Dimension(
                dimension_name="flag",
                data_type="bool",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(
                dimension_name="approved", data_type="bool", match_strategy="exact"
            ),
        ]
        contract = _candidate_contract(
            dimensions,
            required=(*(("flag",) if flag_required else ()), "approved"),
            masks=("approved",),
        )
        rows = [
            {"id": uuid(11), "flag": True, "approved": True, "margin": 10.0},
            {"id": uuid(12), "flag": False, "approved": True, "margin": 20.0},
        ]
        kwargs = declarations(
            dimensions,
            [ROUTING_TOTAL],
            contracts=[contract],
            partition_keys=((True,), (False,), (None,)),
        )
        validation = gate(rows, kwargs)
        engine = rules.AccumulatorEngine(
            kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
        )
        lattices = engine.build_all(rows, validation=validation)
        if return_validation:
            return engine, lattices, rows, validation
        return engine, lattices

    def test_required_boolean_key_missing_and_invalid_have_pre_route_parity(self):
        engine, lattices = self._artifacts()
        exact = _partition(lattices, flag=True)
        index = engine.index(lattices)

        for context, reason in (
            ({"approved": True}, "missing_required"),
            ({"flag": 0, "approved": True}, "invalid_type"),
        ):
            with pytest.raises(rules.InvalidContextError) as indexed:
                index.apply(context, contract_id="client", profile_id="inspect")
            with pytest.raises(rules.InvalidContextError) as direct:
                engine.apply(exact, context, contract_id="client", profile_id="inspect")

            assert (
                indexed.value.result.status
                == direct.value.result.status
                == "invalid_context"
            )
            assert indexed.value.result.reason == direct.value.result.reason == reason
            assert indexed.value.result.binding_id is None
            assert direct.value.result.binding_id == exact.bindings[0].id

        batch = index.apply_batch(
            [
                {"request_id": "missing", "approved": True},
                {"request_id": "invalid", "flag": 0, "approved": True},
            ],
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
        )
        for context_id, reason in (
            ("missing", "missing_required"),
            ("invalid", "invalid_type"),
        ):
            assert batch.records[context_id].status == "invalid_context"
            assert batch.records[context_id].reason == reason
            assert batch.records[context_id].binding_id is None
            with pytest.raises(rules.InvalidContextError) as error:
                batch.for_context(context_id)
            assert error.value.outcome is batch.records[context_id]

    def test_optional_boolean_key_absence_routes_only_to_the_wildcard_default(self):
        engine, lattices, rows, validation = self._artifacts(
            flag_required=False,
            return_validation=True,
        )
        wildcard = next(
            lattice
            for lattice in lattices
            if lattice.partition_identity["key_values"][0]["match"]["kind"]
            == "wildcard"
        )

        assert wildcard.partition_key == {"flag": None}
        assert engine.build(
            rows, validation=validation, partition_key={"flag": None}
        ).partition_key == {"flag": None}
        result = engine.index(lattices).apply(
            {"approved": True},
            contract_id="client",
            profile_id="inspect",
        )

        assert result.status == "candidates"
        assert result.candidate_cells.count_rows() == 0

    def test_r8_pre_route_and_post_selection_errors_keep_correct_bindings(self):
        engine, lattices = self._artifacts()
        exact = _partition(lattices, flag=True)
        index = engine.index(lattices)
        valid = {"flag": True, "approved": True}
        post_selection_invalid = {"flag": True, "approved": 0}
        pre_route_invalid = {"approved": True}

        direct = engine.apply(exact, valid, contract_id="client", profile_id="inspect")
        indexed = index.apply(valid, contract_id="client", profile_id="inspect")
        automatic = engine.apply_auto(
            lattices, valid, contract_id="client", profile_id="inspect"
        )
        assert direct.outcome == indexed.outcome == automatic.outcome

        post_errors = []
        for invoke in (
            lambda: engine.apply(
                exact,
                post_selection_invalid,
                contract_id="client",
                profile_id="inspect",
            ),
            lambda: index.apply(
                post_selection_invalid, contract_id="client", profile_id="inspect"
            ),
            lambda: engine.apply_auto(
                lattices,
                post_selection_invalid,
                contract_id="client",
                profile_id="inspect",
            ),
        ):
            with pytest.raises(rules.InvalidContextError) as failure:
                invoke()
            assert failure.value.result.binding_id == exact.bindings[0].id
            post_errors.append(failure.value.outcome)
        assert post_errors[0] == post_errors[1] == post_errors[2]

        with pytest.raises(rules.InvalidContextError) as direct_pre:
            engine.apply(
                exact, pre_route_invalid, contract_id="client", profile_id="inspect"
            )
        with pytest.raises(rules.InvalidContextError) as indexed_pre:
            index.apply(pre_route_invalid, contract_id="client", profile_id="inspect")
        with pytest.raises(rules.InvalidContextError) as automatic_pre:
            engine.apply_auto(
                lattices,
                pre_route_invalid,
                contract_id="client",
                profile_id="inspect",
            )
        assert direct_pre.value.result.binding_id == exact.bindings[0].id
        assert indexed_pre.value.result.binding_id is None
        assert automatic_pre.value.outcome == indexed_pre.value.outcome

        batch = index.apply_batch(
            [
                {"request_id": "valid", **valid},
                {"request_id": "post", **post_selection_invalid},
                {"request_id": "pre", **pre_route_invalid},
            ],
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
            chunk_size=1,
        )
        assert batch.records["valid"] == direct.outcome
        assert batch.records["post"] == post_errors[0]
        assert batch.records["pre"] == indexed_pre.value.outcome
        for context_id, expected in (
            ("post", post_errors[0]),
            ("pre", indexed_pre.value.outcome),
        ):
            with pytest.raises(rules.InvalidContextError) as error:
                batch.for_context(context_id)
            assert error.value.outcome is batch.records[context_id] == expected
            if context_id == "pre":
                assert error.value.result.candidate_cells is None
                assert error.value.result.candidate_contributors is None


# ---------------------------------------------------------------------------
# E8 native snapshot conformance


_NATIVE_DATA_FILES = (
    "lattice.parquet",
    "sources.parquet",
    "contributors.parquet",
    "predicates.json",
    "validation.json",
    "scopes.parquet",
    "scope_keys.parquet",
    "source_maps.parquet",
    "vectors.parquet",
    "words.parquet",
)
_NATIVE_FILES = (*_NATIVE_DATA_FILES, "manifest.yaml")


def _source_gated_lattice(*, labels=None, secondary_output=False):
    """Use the actual E5 handoff, never a fabricated clean/bound bundle."""
    from tests.accumulator.source_analysis_fixtures import (
        approve,
        case,
        contract,
        gate as validate_build_input,
        row,
    )

    provider = contract(promise="definite_outcome", required=True, masked=False)
    kwargs, _ = case(contracts=[provider])
    rows = [
        {**row(1, 0, 10, amount=10), "other": 1},
        {**row(2, 10, 20, amount=20), "other": 2},
    ]
    if labels is not None:
        kwargs["source_label_field"] = "label"
        rows = [{**source, "label": label} for source, label in zip(rows, labels)]
    if secondary_output:
        kwargs["aggregates"] = [
            *kwargs["aggregates"],
            rules.Aggregate(
                column_name="other",
                output_name="pricing.other",
                data_type="int",
                numeric_semantics="numeric-1",
            ),
        ]

    analyzed = rules.analyze_sources(rows, **kwargs)
    assert [finding.code for finding in analyzed.validation["findings"]] == [
        "singleton_boundary_overlap"
    ]
    warning = analyzed.validation["findings"][0]
    approval = approve(analyzed, [warning.id], scope=warning.scope)
    attached = rules.attach_warning_approvals(
        analyzed, [approval], limits=kwargs["limits"]
    )
    source_bytes = rules.encode_validation_bundle(attached, limits=kwargs["limits"])
    decoded = rules.decode_validation_bundle(source_bytes, limits=kwargs["limits"])
    validation = validate_build_input(rows, kwargs, decoded, [approval])
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    (lattice,) = engine.build_all(rows, validation=validation)
    return {
        "approval": approval,
        "engine": engine,
        "lattice": lattice,
        "limits": kwargs["limits"],
        "source_bytes": source_bytes,
        "source_report": decoded.validation["reports"][0],
    }


@pytest.fixture
def native_snapshot():
    return _source_gated_lattice(
        labels=("duplicate label", "duplicate label"), secondary_output=True
    )


def _save_native(lattice, directory, limits):
    return lattice.save(directory, limits=limits)


def _load_native(directory, limits):
    return Lattice.load(directory, limits=limits)


def _manifest(path):
    import yaml

    return yaml.safe_load((path / "manifest.yaml").read_text(encoding="utf-8"))


def _write_manifest(path, manifest):
    import yaml

    (path / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )


def _refresh_file_digest(path, manifest, filename):
    import hashlib

    entry = next(item for item in manifest["files"] if item["path"] == filename)
    entry["sha256"] = hashlib.sha256((path / filename).read_bytes()).hexdigest()
    _write_manifest(path, manifest)


class TestNativeSnapshot:
    def test_build_save_load_retains_source_gate_and_expanded_evidence(
        self, native_snapshot, tmp_path
    ):
        lattice = native_snapshot["lattice"]
        limits = native_snapshot["limits"]
        out = _save_native(lattice, tmp_path / "snapshot", limits)

        assert {child.name for child in out.iterdir()} == set(_NATIVE_FILES)
        manifest = _manifest(out)
        assert manifest["artifact_kind"] == "exact_cells"
        assert manifest["native_schema_version"] == 1
        assert {entry["path"] for entry in manifest["files"]} == set(_NATIVE_DATA_FILES)

        loaded = _load_native(out, limits)
        import json

        expanded = rules.ValidationBundle.model_validate(
            {
                "schema_version": 1,
                "metadata": manifest["dimensions"],
                "aggregates": manifest["aggregates"],
                "routing": manifest["partition"]["routing"],
                "context_contracts": manifest["context_contracts"],
                "predicates": json.loads((out / "predicates.json").read_bytes()),
                "validation": json.loads((out / "validation.json").read_bytes()),
            }
        )
        expanded_bytes = rules.encode_validation_bundle(expanded, limits=limits)
        assert rules.decode_validation_bundle(expanded_bytes, limits=limits) == expanded
        assert (
            rules.encode_validation_bundle(
                rules.decode_validation_bundle(
                    native_snapshot["source_bytes"], limits=limits
                ),
                limits=limits,
            )
            == native_snapshot["source_bytes"]
        )
        source_report = next(
            report
            for report in expanded.validation["reports"]
            if report.id == native_snapshot["source_report"].id
        )
        approval = next(
            item
            for item in expanded.validation["approvals"]
            if item.id == native_snapshot["approval"].id
        )
        assert source_report.model_dump(mode="json") == native_snapshot[
            "source_report"
        ].model_dump(mode="json")
        assert approval.model_dump(mode="json") == native_snapshot[
            "approval"
        ].model_dump(mode="json")
        assert loaded.artifact_id == lattice.artifact_id
        assert loaded.partition_identity == lattice.partition_identity
        assert loaded.bindings[0].source_report_id == source_report.id
        assert loaded.bindings[0].approval_ids == (approval.id,)

        original = native_snapshot["engine"].apply(
            lattice, {"x": 10}, contract_id="client", profile_id="quote"
        )
        reloaded = native_snapshot["engine"].apply(
            loaded, {"x": 10}, contract_id="client", profile_id="quote"
        )
        assert reloaded.outcome == original.outcome
        assert reloaded.values == original.values

    def test_lineage_preserves_duplicate_and_absent_labels_and_requested_outputs(
        self, native_snapshot, tmp_path
    ):
        loaded = _load_native(
            _save_native(
                native_snapshot["lattice"],
                tmp_path / "snapshot",
                native_snapshot["limits"],
            ),
            native_snapshot["limits"],
        )
        result = native_snapshot["engine"].apply(
            loaded, {"x": 10}, contract_id="client", profile_id="quote"
        )

        assert result.lineage is not None
        requested = result.lineage.to_dicts()
        full = loaded.lineage(result.cell_id).to_dicts()
        assert {row["output_name"] for row in requested} == {"pricing.total"}
        assert {row["output_name"] for row in full} == {
            "pricing.total",
            "pricing.other",
        }
        duplicate_labels = {row["source_id"]: row["source_label"] for row in full}
        assert set(duplicate_labels.values()) == {"duplicate label"}

        unlabeled = _source_gated_lattice(
            labels=("duplicate label", None), secondary_output=True
        )
        unlabeled_loaded = _load_native(
            _save_native(
                unlabeled["lattice"],
                tmp_path / "unlabeled",
                unlabeled["limits"],
            ),
            unlabeled["limits"],
        )
        unlabeled_result = unlabeled["engine"].apply(
            unlabeled_loaded, {"x": 10}, contract_id="client", profile_id="quote"
        )
        absent_labels = {
            row["source_id"]: row["source_label"]
            for row in unlabeled_loaded.lineage(unlabeled_result.cell_id).to_dicts()
        }
        assert set(absent_labels.values()) == {None, "duplicate label"}

    @pytest.mark.parametrize("filename", _NATIVE_FILES)
    def test_required_native_file_cannot_be_missing(
        self, native_snapshot, tmp_path, filename
    ):
        out = _save_native(
            native_snapshot["lattice"], tmp_path / "snapshot", native_snapshot["limits"]
        )
        (out / filename).unlink()

        with pytest.raises((FileNotFoundError, ValueError)):
            _load_native(out, native_snapshot["limits"])

    def test_corrupted_payload_and_native_schema_are_rejected(
        self, native_snapshot, tmp_path
    ):
        out = _save_native(
            native_snapshot["lattice"], tmp_path / "corrupt", native_snapshot["limits"]
        )
        with (out / "sources.parquet").open("ab") as stream:
            stream.write(b"corruption")
        with pytest.raises(ValueError):
            _load_native(out, native_snapshot["limits"])

        out = _save_native(
            native_snapshot["lattice"], tmp_path / "schema", native_snapshot["limits"]
        )
        manifest = _manifest(out)
        frame = pl.read_parquet(out / "lattice.parquet")
        lattice_entry = next(
            item for item in manifest["files"] if item["path"] == "lattice.parquet"
        )
        first_column = lattice_entry["columns"][0]["name"]
        frame.rename({first_column: f"{first_column}_wrong"}).write_parquet(
            out / "lattice.parquet"
        )
        _refresh_file_digest(out, manifest, "lattice.parquet")
        with pytest.raises(ValueError):
            _load_native(out, native_snapshot["limits"])

    def test_unknown_native_version_is_rejected(self, native_snapshot, tmp_path):
        out = _save_native(
            native_snapshot["lattice"], tmp_path / "snapshot", native_snapshot["limits"]
        )
        manifest = _manifest(out)
        manifest["native_schema_version"] = 2
        _write_manifest(out, manifest)

        with pytest.raises(ValueError):
            _load_native(out, native_snapshot["limits"])

    @pytest.mark.parametrize(
        "payload",
        [
            "artifact_kind: exact_cells\nartifact_kind: exact_cells\n",
            "anchor: &shared {value: 1}\nartifact_kind: *shared\n",
        ],
        ids=["duplicate-key", "yaml-alias"],
    )
    def test_manifest_rejects_ambiguous_yaml(self, native_snapshot, tmp_path, payload):
        out = _save_native(
            native_snapshot["lattice"], tmp_path / "snapshot", native_snapshot["limits"]
        )
        (out / "manifest.yaml").write_text(payload, encoding="utf-8")

        with pytest.raises(ValueError):
            _load_native(out, native_snapshot["limits"])

    def test_manifest_path_and_symlinks_are_rejected(self, native_snapshot, tmp_path):
        out = _save_native(
            native_snapshot["lattice"], tmp_path / "path", native_snapshot["limits"]
        )
        manifest = _manifest(out)
        manifest["files"][0]["path"] = "../lattice.parquet"
        _write_manifest(out, manifest)
        with pytest.raises(ValueError):
            _load_native(out, native_snapshot["limits"])

        out = _save_native(
            native_snapshot["lattice"], tmp_path / "links", native_snapshot["limits"]
        )
        (out / "sources.parquet").unlink()
        (out / "sources.parquet").symlink_to(out / "lattice.parquet")
        with pytest.raises(ValueError):
            _load_native(out, native_snapshot["limits"])
        root_link = tmp_path / "root-link"
        root_link.symlink_to(out, target_is_directory=True)
        with pytest.raises(ValueError):
            _load_native(root_link, native_snapshot["limits"])

    def test_reordered_parquet_rows_change_digest_but_not_artifact_or_apply(
        self, native_snapshot, tmp_path
    ):
        lattice = native_snapshot["lattice"]
        out = _save_native(lattice, tmp_path / "snapshot", native_snapshot["limits"])
        manifest = _manifest(out)
        digest_before = next(
            item["sha256"]
            for item in manifest["files"]
            if item["path"] == "lattice.parquet"
        )
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pq.read_table(out / "lattice.parquet")
        pq.write_table(
            table.take(pa.array(range(table.num_rows - 1, -1, -1))),
            out / "lattice.parquet",
        )
        _refresh_file_digest(out, manifest, "lattice.parquet")
        digest_after = next(
            item["sha256"]
            for item in _manifest(out)["files"]
            if item["path"] == "lattice.parquet"
        )
        loaded = _load_native(out, native_snapshot["limits"])

        assert digest_after != digest_before
        assert loaded.artifact_id == lattice.artifact_id
        assert (
            native_snapshot["engine"]
            .apply(loaded, {"x": 10}, contract_id="client", profile_id="quote")
            .outcome
            == native_snapshot["engine"]
            .apply(lattice, {"x": 10}, contract_id="client", profile_id="quote")
            .outcome
        )

    def test_writer_refuses_existing_target_and_cleans_output_exhaustion(
        self, native_snapshot, tmp_path
    ):
        target = tmp_path / "snapshot"
        out = _save_native(
            native_snapshot["lattice"], target, native_snapshot["limits"]
        )
        manifest_bytes = (out / "manifest.yaml").read_bytes()
        with pytest.raises(ValueError):
            _save_native(native_snapshot["lattice"], target, native_snapshot["limits"])
        assert (out / "manifest.yaml").read_bytes() == manifest_bytes

        exhausted = tmp_path / "exhausted"
        with pytest.raises(rules.ExactResourceError):
            _save_native(
                native_snapshot["lattice"],
                exhausted,
                rules.ExactLimits.model_validate(
                    {
                        **native_snapshot["limits"].model_dump(mode="python"),
                        "max_output_bytes": 0,
                    }
                ),
            )
        assert not exhausted.exists()

    def test_mid_write_failure_cleans_the_owned_staging_directory(
        self, native_snapshot, tmp_path, monkeypatch
    ):
        from mountainash_rules.engines.accumulator import snapshot_io

        target = tmp_path / "snapshot"
        write_table = snapshot_io.pq.write_table
        calls = 0

        def fail_second_parquet_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second-file write failure")
            return write_table(*args, **kwargs)

        monkeypatch.setattr(snapshot_io.pq, "write_table", fail_second_parquet_write)
        with pytest.raises(OSError, match="injected second-file write failure"):
            _save_native(native_snapshot["lattice"], target, native_snapshot["limits"])
        assert calls == 2
        assert not target.exists()


def _native_boolean_snapshot_artifacts(*, flag_required=True):
    dimensions = [
        rules.Dimension(
            dimension_name="flag",
            data_type="bool",
            match_strategy="exact_key",
            role=rules.DimensionRole.CONTEXT_KEY,
        ),
        rules.Dimension(
            dimension_name="approved", data_type="bool", match_strategy="exact"
        ),
    ]
    contract = _candidate_contract(
        dimensions,
        required=(*(("flag",) if flag_required else ()), "approved"),
        masks=("approved",),
    )
    rows = [
        {"id": uuid(11), "flag": True, "approved": True, "margin": 10.0},
        {"id": uuid(12), "flag": False, "approved": True, "margin": 20.0},
    ]
    kwargs = declarations(
        dimensions,
        [ROUTING_TOTAL],
        contracts=[contract],
        partition_keys=((True,), (False,), (None,)),
    )
    validation = gate(rows, kwargs)
    engine = rules.AccumulatorEngine(
        kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
    )
    return engine, engine.build_all(rows, validation=validation), kwargs["limits"]


class TestNativeSnapshotRouting:
    def test_empty_native_default_has_typed_partition_identity_and_restores_routing(
        self, tmp_path
    ):
        engine, lattices, limits = _native_boolean_snapshot_artifacts(
            flag_required=False
        )
        default = _partition(lattices, flag=None)
        out = _save_native(default, tmp_path / "default", limits)
        loaded = _load_native(out, limits)

        assert loaded.artifact_kind == "exact_cells"
        assert loaded.count == 0
        assert loaded.partition_identity == default.partition_identity
        key = loaded.partition_identity["key_values"][0]
        assert key["dimension_name"] == "flag"
        assert key["match"] == {"kind": "wildcard"}
        result = engine.index(
            [
                *[lattice for lattice in lattices if lattice is not default],
                loaded,
            ]
        ).apply(
            {"flag": None, "approved": True}, contract_id="client", profile_id="inspect"
        )
        assert result.status == "candidates"
        assert result.candidate_cells.count_rows() == 0

    def test_default_partition_restores_unknown_key_source_for_indexed_apply(
        self, tmp_path
    ):
        """A source wildcard is authored UNKNOWN, not an unavailable context value."""
        dimensions = [
            rules.Dimension(
                dimension_name="tenant",
                data_type="str",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            ),
            rules.Dimension(dimension_name="region", match_strategy="exact"),
        ]
        contract = _candidate_contract(dimensions)
        rows = [
            {
                "id": uuid(13),
                "tenant": rules.UNKNOWN,
                "region": "AU",
                "margin": 13.0,
            }
        ]
        kwargs = declarations(
            dimensions,
            [ROUTING_TOTAL],
            contracts=[contract],
            partition_keys=((None,),),
        )
        validation = gate(rows, kwargs)
        engine = rules.AccumulatorEngine(
            kwargs["metadata"], kwargs["aggregates"], limits=kwargs["limits"]
        )
        (default,) = engine.build_all(rows, validation=validation)

        restored = _load_native(
            _save_native(default, tmp_path / "default-source", kwargs["limits"]),
            kwargs["limits"],
        )
        result = engine.index([restored]).apply(
            {"tenant": "other", "region": "AU"},
            contract_id="client",
            profile_id="inspect",
        )

        assert _candidate_facts(result) == [(13.0, (uuid(13),))]

    def test_after_load_boolean_admission_and_mixed_batch_keep_sibling_parity(
        self, tmp_path
    ):
        engine, lattices, limits = _native_boolean_snapshot_artifacts()
        restored = [
            _load_native(
                _save_native(lattice, tmp_path / f"part-{index}", limits), limits
            )
            for index, lattice in enumerate(lattices)
        ]
        original_index = engine.index(lattices)
        restored_index = engine.index(restored)
        specific = _partition(restored, flag=False)
        assert specific.partition_identity["key_values"][0]["match"] == {
            "kind": "value",
            "value": {"type": "bool", "value": False},
        }
        valid = {"flag": False, "approved": True}
        assert (
            restored_index.apply(
                valid, contract_id="client", profile_id="inspect"
            ).outcome
            == original_index.apply(
                valid, contract_id="client", profile_id="inspect"
            ).outcome
        )
        with pytest.raises(rules.InvalidContextError):
            restored_index.apply(
                {"flag": 0, "approved": True},
                contract_id="client",
                profile_id="inspect",
            )

        batch = restored_index.apply_batch(
            [
                {"request_id": "valid", **valid},
                {"request_id": "invalid", "flag": 0, "approved": True},
            ],
            contract_id="client",
            profile_id="inspect",
            context_id_field="request_id",
            chunk_size=1,
        )
        assert (
            batch.for_context("valid").outcome
            == original_index.apply(
                valid, contract_id="client", profile_id="inspect"
            ).outcome
        )
        assert batch.records["invalid"].status == "invalid_context"
        assert batch.records["invalid"].binding_id is None
        with pytest.raises(rules.InvalidContextError):
            batch.for_context("invalid")


def test_legacy_snapshot_remains_bounded_inspection_and_refuses_exact_apply(tmp_path):
    import yaml

    from tests.accumulator.source_analysis_fixtures import limits

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    pl.DataFrame({"rule_name": ["legacy"], "region": ["AU"]}).write_parquet(
        legacy / "lattice.parquet"
    )
    (legacy / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "dimensions": {
                    "dimensions": [
                        {
                            "dimension_name": "region",
                            "match_strategy": "exact",
                            "data_type": "str",
                        }
                    ]
                },
                "aggregates": [{"column_name": "margin", "operation": "sum"}],
                "partition_key": None,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    inspected = _load_native(legacy, limits())
    assert inspected.artifact_kind is None
    assert inspected.aggregates == [Aggregate(column_name="margin", operation="sum")]
    assert inspected.combinations.to_dicts() == [
        {"rule_name": "legacy", "region": "AU"}
    ]
    engine = rules.AccumulatorEngine(inspected.metadata, [], limits=limits())
    with pytest.raises(ValueError, match="inspection-only"):
        engine.apply(
            inspected, {"region": "AU"}, contract_id="client", profile_id="quote"
        )


def test_snapshot_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    import os
    import subprocess
    import sys

    os.mkfifo(tmp_path / "manifest.yaml")
    program = (
        "import sys; from mountainash_rules import Lattice; "
        "from tests.accumulator.source_analysis_fixtures import limits; "
        "Lattice.load(sys.argv[1], limits=limits())"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode != 0
    assert "ValueError" in completed.stderr
