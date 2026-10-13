"""Independent artifact validation and explicit publication policy controls."""

from dataclasses import replace

import mountainash as ma
import pytest
from mountainash import materialization_scope
from mountainash.relations import relation

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.codec import content_id
from mountainash_rules.core.contracts import (
    ContextContract,
    ContextField,
    CoverageRequirement,
    DomainDefinition,
    DomainField,
    ResolutionProfile,
    Scope,
    ValidationPolicy,
)
from mountainash_rules.core.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engines.accumulate.build import build
from mountainash_rules.engines.accumulate.sources import check_sources
from mountainash_rules.engines.accumulate.validate import approve, validate
from tests.conftest import build_backend_df


def _metadata():
    return DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="int", match_strategy="range",
            range_min_field="minimum", range_max_field="maximum",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )


def _rules(backend_name):
    return ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "minimum": [0, 5], "maximum": [10, 15],
        "amount": [10, 5],
    }, "rules"))


@pytest.mark.parametrize("aliases", [False, True])
def test_validator_preserves_physical_axis_connectivity(backend_name, aliases):
    from tests.accumulate.test_domain import domain, interval_node, node

    fields = ("x", "x") if aliases else ("x", "y")
    dimensions = [
        Dimension(
            dimension_name=name, context_field=field, data_type="int",
            match_strategy="range", range_min_field=f"lo{index}", range_max_field=f"hi{index}",
        )
        for index, (name, field) in enumerate(zip(("first", "second"), fields))
    ]
    graph = {}
    declared_domain = None
    if not aliases:
        root = node(graph, "and", args=(
            interval_node(graph, "x", 0, 1, closed=(True, True)),
            interval_node(graph, "y", 10, 11, closed=(True, True)),
        ))
        declared_domain = domain(
            root, DomainField(name="x", data_type="int"), DomainField(name="y", data_type="int"),
        )
    metadata = DimensionsMetadata(
        uuid_field="uuid", dimensions=dimensions, domain=declared_domain, domain_nodes=graph,
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "lo0": [0], "hi0": [2 if aliases else 1],
        "lo1": [0 if aliases else 10], "hi1": [2 if aliases else 11], "amount": [7],
    }))
    lattice = build(rules, metadata)
    assert lattice.cells.count_rows() == 1
    assert validate(lattice, rules).findings.to_dicts() == []


def _provider_metadata(
    graph,
    provider_root,
    dimensions,
    fields,
    *,
    profile_dimensions=None,
    allow_dont_care=(),
    promise="definite_outcome",
    provenance="none",
    coverage_region=None,
):
    dimensions = tuple(dimensions)
    fields = tuple(fields)
    profile = ResolutionProfile(
        profile_id="quote",
        mode="resolve",
        output_fields=("default.amount",),
        provenance=provenance,
        dimensions=tuple(sorted(
            profile_dimensions or (dimension.dimension_name for dimension in dimensions)
        )),
        allow_dont_care=tuple(sorted(allow_dont_care)),
        promise=promise,
        on_unresolved="return",
    )
    contract = ContextContract(
        schema_version=1,
        contract_id="provider",
        fields=fields,
        domain_ref="D",
        profiles=(profile,),
    )
    policy = None
    if coverage_region is not None:
        routing_id = content_id("routing", {
            "schema_version": 1,
            "semantics": "exact-key-1",
            "key_dimensions": [],
            "partition_keys": [[]],
        })
        requirement = CoverageRequirement(
            requirement_id="covered-region",
            domain_ref="D",
            region_predicate_id=coverage_region,
            scope=Scope(
                partition_refs=({"routing_id": routing_id, "key_values": []},),
                domain_refs=("D",),
                profile_refs=(),
            ),
            severity="error",
        )
        policy = ValidationPolicy(
            schema_version=1,
            policy_id="provider-coverage",
            required_checks=(),
            coverage_requirements=(requirement,),
            diagnostic_rules=(),
        )
    provider_domain = DomainDefinition(
        schema_version=1,
        domain_id="D",
        fields=tuple(sorted(
            (DomainField(name=field.name, data_type=field.data_type) for field in fields),
            key=lambda field: field.name,
        )),
        predicate_id=provider_root,
    )
    return DimensionsMetadata(
        uuid_field="uuid",
        dimensions=list(dimensions),
        aggregates=[Aggregate(column_name="amount", data_type="int")],
        context_domains=[provider_domain],
        domain_nodes=graph,
        context_contracts=[contract],
        policy=policy,
    )


def _provider_point_rules(backend_name, amounts=(5, 5), points=(0, 1)):
    return ma.relation(build_backend_df(backend_name, {
        "uuid": [f"source-{index}" for index in range(len(points))],
        "minimum": list(points),
        "maximum": list(points),
        "amount": list(amounts),
    }, "provider-rules"))


def _range_provider(upper=2):
    from tests.accumulate.test_domain import interval_node

    graph = {}
    root = interval_node(graph, "x", 0, upper, closed=(True, True))
    dimension = Dimension(
        dimension_name="x",
        context_field="x",
        match_strategy="range",
        data_type="int",
        range_min_field="minimum",
        range_max_field="maximum",
    )
    field = ContextField(name="x", data_type="int", required=True)
    return graph, root, dimension, field


def test_validator_connects_boolean_wildcard_atoms_from_one_box(backend_name):
    metadata = DimensionsMetadata(
        uuid_field="uuid",
        dimensions=[Dimension(
            dimension_name="x", data_type="bool", rule_wildcard_field="any_x",
        )],
        aggregates=[Aggregate(column_name="amount", data_type="int")],
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "x": [False], "any_x": [True], "amount": [1],
    }, "bool-wildcard-rules"))

    report = validate(build(rules, metadata), rules)

    assert "disconnected_compound_cell" not in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


def test_profile_requires_jointly_feasible_equal_observations(backend_name):
    from tests.accumulate.test_domain import node

    graph = {}
    root = node(graph, "compare", left="x", right="y", relation="eq")
    dimensions = (
        Dimension(
            dimension_name="x", context_field="x", rule_field="x_min",
            data_type="int", match_strategy="range", range_min_field="x_min",
            range_max_field="x_max",
        ),
        Dimension(
            dimension_name="y", context_field="y", rule_field="y_min",
            data_type="int", match_strategy="range", range_min_field="y_min",
            range_max_field="y_max",
        ),
    )
    fields = (
        ContextField(name="x", data_type="int", required=True),
        ContextField(name="y", data_type="int", required=False),
    )
    metadata = _provider_metadata(
        graph, root, dimensions, fields, profile_dimensions=("x",),
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A", "B"], "x_min": [0, 0], "x_max": [10, 10],
        "y_min": [0, 5], "y_max": [4, 10], "amount": [1, 2],
    }, "correlated-provider-rules"))

    report = validate(build(rules, metadata), rules)

    assert "profile_counterexample" not in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


def test_provider_no_match_is_not_a_coverage_failure(backend_name):
    graph, root, dimension, field = _range_provider()
    metadata = _provider_metadata(
        graph, root, (dimension,), (field,), promise="allow_unresolved",
    )
    rules = _provider_point_rules(backend_name)
    report = validate(build(rules, metadata), rules)

    assert "coverage_gap" not in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


def test_authored_provider_coverage_gap_is_reported(backend_name):
    from tests.accumulate.test_domain import node, scalar

    graph, root, dimension, field = _range_provider()
    uncovered = node(graph, "eq", field="x", value=scalar(2))
    metadata = _provider_metadata(
        graph, root, (dimension,), (field,), promise="allow_unresolved",
        coverage_region=uncovered,
    )
    rules = _provider_point_rules(backend_name)
    report = validate(build(rules, metadata), rules)

    assert "coverage_gap" in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


@pytest.mark.parametrize(
    ("amounts", "has_counterexample"),
    [((5, 5), False), ((5, 6), True)],
    ids=("same-output", "different-output"),
)
def test_definite_profile_compares_requested_output_values(
    backend_name, amounts, has_counterexample,
):
    graph, root, dimension, field = _range_provider(upper=1)
    metadata = _provider_metadata(
        graph, root, (dimension,), (field,), allow_dont_care=("x",),
    )
    rules = _provider_point_rules(backend_name, amounts, points=(0, 1))
    report = validate(build(rules, metadata), rules)

    assert ("profile_counterexample" in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }) is has_counterexample


def test_required_unmasked_profile_observation_distinguishes_outputs(backend_name):
    graph, root, dimension, field = _range_provider(upper=1)
    metadata = _provider_metadata(graph, root, (dimension,), (field,))
    rules = _provider_point_rules(backend_name, amounts=(5, 6), points=(0, 1))
    report = validate(build(rules, metadata), rules)

    assert "profile_counterexample" not in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


def test_permitted_mask_can_break_a_required_profile_promise(backend_name):
    graph, root, dimension, field = _range_provider(upper=1)
    metadata = _provider_metadata(
        graph, root, (dimension,), (field,), allow_dont_care=("x",),
    )
    rules = _provider_point_rules(backend_name, amounts=(5, 6), points=(0, 1))
    report = validate(build(rules, metadata), rules)

    assert "profile_counterexample" in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


@pytest.mark.parametrize("provenance", ("contributors", "cell"))
def test_definite_profile_checks_requested_provenance(backend_name, provenance):
    graph, root, dimension, field = _range_provider(upper=1)
    metadata = _provider_metadata(
        graph, root, (dimension,), (field,),
        allow_dont_care=("x",), provenance=provenance,
    )
    rules = _provider_point_rules(backend_name, amounts=(5, 5), points=(0, 1))
    report = validate(build(rules, metadata), rules)

    assert "profile_counterexample" in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


def test_provider_profile_keeps_joint_domain_and_alias_observations(backend_name):
    from tests.accumulate.test_domain import node, scalar

    graph = {}
    x_zero = node(graph, "eq", field="x", value=scalar(0))
    y_zero = node(graph, "eq", field="y", value=scalar(0))
    x_one = node(graph, "eq", field="x", value=scalar(1))
    y_one = node(graph, "eq", field="y", value=scalar(1))
    root = node(graph, "or", args=(
        node(graph, "and", args=(x_zero, y_zero)),
        node(graph, "and", args=(x_one, y_one)),
    ))
    dimensions = (
        Dimension(
            dimension_name="x",
            context_field="x",
            rule_field="x_rule",
            data_type="int",
        ),
        Dimension(
            dimension_name="x_alias",
            context_field="x",
            rule_field="alias_rule",
            data_type="int",
        ),
        Dimension(
            dimension_name="y",
            context_field="y",
            rule_field="y_rule",
            data_type="int",
        ),
    )
    fields = (
        ContextField(name="x", data_type="int", required=True),
        ContextField(name="y", data_type="int", required=False),
    )
    metadata = _provider_metadata(
        graph, root, dimensions, fields,
        profile_dimensions=("x", "x_alias"),
        allow_dont_care=("x",),
    )
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["source-0", "source-1"],
        "x_rule": [0, 1],
        "alias_rule": [0, 1],
        "y_rule": [0, 1],
        "amount": [5, 6],
    }, "joint-provider-rules"))
    report = validate(build(rules, metadata), rules)

    assert "profile_counterexample" not in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }


def test_provider_coverage_gap_does_not_break_allow_unresolved_promise(backend_name):
    from tests.accumulate.test_domain import node, scalar

    graph, root, dimension, field = _range_provider()
    uncovered = node(graph, "eq", field="x", value=scalar(2))
    metadata = _provider_metadata(
        graph, root, (dimension,), (field,), promise="allow_unresolved",
        coverage_region=uncovered,
    )
    rules = _provider_point_rules(backend_name)
    report = validate(build(rules, metadata), rules)

    kinds = {row["kind"] for row in report.findings.select("kind").to_dicts()}
    assert "coverage_gap" in kinds
    assert "profile_counterexample" not in kinds


def _replace_owned_table(lattice, role, changed):
    descriptor = next(item for item in lattice.table_descriptors if item.role == role)
    with materialization_scope() as owner:
        owned = owner.detach(owner.capture(relation(changed.collect()))[0])
    tables = dict(lattice.tables)
    tables[descriptor.table_id] = owned
    return replace(lattice, tables=tables)


def _corrupt_fold(lattice):
    output = lattice.outputs["default"]
    wrong_value = ma.lit(999, dtype=output.schema["v0001"])
    return _replace_owned_table(
        lattice, "outputs", output.with_columns(wrong_value.alias("v0001")),
    )



def test_validator_detects_split_cells_when_cell_id_order_opposes_atom_order(
    backend_name,
):
    metadata = _metadata()
    rules = ma.relation(build_backend_df(backend_name, {
        "uuid": ["A"], "minimum": [0], "maximum": [1], "amount": [1],
    }, "split-cell-rules"))
    lattice = build(rules, metadata)
    cell = lattice.cells
    boxes = lattice.cell_boxes
    bounds = lattice.cell_ordered_bounds["x"]
    split_cells = ma.concat([
        cell.with_columns(ma.lit(1, dtype=cell.schema["cell_id"]).alias("cell_id")),
        cell.with_columns(ma.lit(0, dtype=cell.schema["cell_id"]).alias("cell_id")),
    ])
    split_boxes = ma.concat([
        boxes.with_columns(
            ma.lit(1, dtype=boxes.schema["cell_id"]).alias("cell_id"),
            ma.lit(1, dtype=boxes.schema["box_id"]).alias("box_id"),
        ),
        boxes.with_columns(
            ma.lit(0, dtype=boxes.schema["cell_id"]).alias("cell_id"),
            ma.lit(0, dtype=boxes.schema["box_id"]).alias("box_id"),
        ),
    ])
    split_bounds = ma.concat([
        bounds.with_columns(
            ma.lit(1, dtype=bounds.schema["cell_id"]).alias("cell_id"),
            ma.lit(1, dtype=bounds.schema["box_id"]).alias("box_id"),
            ma.lit(0, dtype=bounds.schema["lower_value"]).alias("lower_value"),
            ma.lit(0, dtype=bounds.schema["upper_value"]).alias("upper_value"),
            ma.lit(True).alias("lower_inclusive"),
            ma.lit(True).alias("upper_inclusive"),
            ma.lit("bounded").alias("lower_state"),
            ma.lit("bounded").alias("upper_state"),
        ),
        bounds.with_columns(
            ma.lit(0, dtype=bounds.schema["cell_id"]).alias("cell_id"),
            ma.lit(0, dtype=bounds.schema["box_id"]).alias("box_id"),
            ma.lit(1, dtype=bounds.schema["lower_value"]).alias("lower_value"),
            ma.lit(1, dtype=bounds.schema["upper_value"]).alias("upper_value"),
            ma.lit(True).alias("lower_inclusive"),
            ma.lit(True).alias("upper_inclusive"),
            ma.lit("bounded").alias("lower_state"),
            ma.lit("bounded").alias("upper_state"),
        ),
    ])
    corrupted = _replace_owned_table(lattice, "cells", split_cells)
    corrupted = _replace_owned_table(corrupted, "cell_boxes", split_boxes)
    corrupted = _replace_owned_table(
        corrupted, "cell_ordered_bounds", split_bounds,
    )

    report = validate(corrupted, rules)

    assert "split_connected_component" in {
        row["kind"] for row in report.findings.select("kind").to_dicts()
    }

def test_independent_validation_accepts_unmodified_native_build(backend_name):
    lattice = build(_rules(backend_name), _metadata())

    report = validate(lattice, _rules(backend_name))

    assert report.findings.count_rows() == 0
    assert report.checks
    assert all(
        check.outcome == "passed" and check.finding_count == check.emitted_count == 0
        for check in report.checks
    )



def test_strict_publication_accepts_complete_current_reports_and_approved_warnings(backend_name):
    rules = _rules(backend_name)
    metadata = _metadata()
    lattice = build(rules, metadata)
    source_report = check_sources(rules, metadata)
    artifact_report = validate(lattice, rules)
    warning_ids = tuple(
        row["finding_id"] for row in source_report.findings.filter(
            ma.col("severity").eq("warning"),
        ).select("finding_id").to_dicts()
    )

    published = lattice.publish(
        policy="strict",
        source_report=source_report,
        artifact_report=artifact_report,
        approvals=(approve(source_report, warning_ids),),
    )

    assert published.published is True
    assert published.version_id == lattice.version_id
    assert lattice.published is False



def _strict_reports(lattice, rules, metadata):
    source_report = check_sources(rules, metadata)
    artifact_report = validate(lattice, rules)
    warning_ids = tuple(
        row["finding_id"] for row in source_report.findings.filter(
            ma.col("severity").eq("warning"),
        ).select("finding_id").to_dicts()
    )
    approvals = (approve(source_report, warning_ids),) if warning_ids else ()
    return source_report, artifact_report, approvals


def test_strict_publication_rejects_omitted_output_dependency(backend_name):
    rules = _rules(backend_name)
    metadata = _metadata()
    lattice = build(rules, metadata)
    source_report, artifact_report, approvals = _strict_reports(lattice, rules, metadata)
    dependencies = dict(artifact_report.binding.dependencies)
    dependencies["outputs"] = ()
    incomplete_report = replace(
        artifact_report,
        binding=replace(artifact_report.binding, dependencies=dependencies),
    )

    with pytest.raises(ValueError, match="omits current artifact dependencies"):
        lattice.publish(
            policy="strict",
            source_report=source_report,
            artifact_report=incomplete_report,
            approvals=approvals,
        )

    assert lattice.published is False


def test_strict_publication_reports_survive_save_load_and_remain_current(backend_name, tmp_path):
    rules = _rules(backend_name)
    metadata = _metadata()
    lattice = build(rules, metadata)
    source_report, artifact_report, approvals = _strict_reports(lattice, rules, metadata)
    published = lattice.publish(
        policy="strict", source_report=source_report,
        artifact_report=artifact_report, approvals=approvals,
    )

    destination = tmp_path / "strict-evidence"
    published.save(destination)
    restored = type(lattice).load(destination)
    restored_source, restored_artifact = restored.evidence

    republished = restored.publish(
        policy="strict",
        source_report=restored_source,
        artifact_report=restored_artifact,
        approvals=restored.approvals,
    )

    assert republished.published is True
    assert tuple(report.report_id for report in republished.evidence) == tuple(
        report.report_id for report in restored.evidence
    )


def test_repeated_strict_publication_deduplicates_reports_and_saves(backend_name, tmp_path):
    rules = _rules(backend_name)
    metadata = _metadata()
    lattice = build(rules, metadata)
    source_report, artifact_report, approvals = _strict_reports(lattice, rules, metadata)
    first = lattice.publish(
        policy="strict", source_report=source_report,
        artifact_report=artifact_report, approvals=approvals,
    )

    repeated = first.publish(
        policy="strict", source_report=source_report,
        artifact_report=artifact_report, approvals=approvals,
    )
    expected_ids = (source_report.report_id, artifact_report.report_id)
    assert tuple(report.report_id for report in repeated.evidence) == expected_ids

    destination = tmp_path / "repeated-strict-evidence"
    repeated.save(destination)
    restored = type(lattice).load(destination)

    assert tuple(report.report_id for report in restored.evidence) == expected_ids

def test_independent_validation_detects_a_missing_source_union_cell(backend_name):
    rules = _rules(backend_name)
    lattice = build(rules, _metadata())
    victim = lattice.cells.to_dicts()[0]["cell_id"]
    cells = lattice.cells.filter(~ma.col("cell_id").eq(victim))
    corrupted = _replace_owned_table(lattice, "cells", cells)

    report = validate(corrupted, rules)

    assert report.findings.count_rows() > 0


def test_independent_validation_detects_wrong_stored_membership(backend_name):
    rules = _rules(backend_name)
    lattice = build(rules, _metadata())
    contributors = lattice.contributors.with_columns(
        ma.when(ma.col("source_uuid").eq("A")).then("B")
        .otherwise(ma.col("source_uuid")).alias("source_uuid")
    )
    corrupted = _replace_owned_table(lattice, "contributors", contributors)

    report = validate(corrupted, rules)

    assert report.findings.count_rows() > 0

def test_independent_validation_detects_corrupt_stored_fold(backend_name):
    rules = _rules(backend_name)
    lattice = build(rules, _metadata())
    corrupted = _corrupt_fold(lattice)

    report = validate(corrupted, rules)

    assert report.findings.count_rows() > 0
    assert "fold_mismatch" in {row["kind"] for row in report.findings.select("kind").to_dicts()}


def test_unchecked_publication_remains_available_with_hard_artifact_findings(backend_name):
    rules = _rules(backend_name)
    lattice = build(rules, _metadata())
    corrupted = _corrupt_fold(lattice)
    report = validate(corrupted, rules)

    published = corrupted.publish(artifact_report=report)

    assert published.published is True
    assert corrupted.published is False
    assert published.outputs["default"].to_dicts() == corrupted.outputs["default"].to_dicts()


def test_explicit_strict_publication_rejects_hard_findings_without_locking_candidate(backend_name):
    rules = _rules(backend_name)
    lattice = build(rules, _metadata())
    corrupted = _corrupt_fold(lattice)
    report = validate(corrupted, rules)

    with pytest.raises(Exception):
        corrupted.publish(policy="strict", artifact_report=report)

    assert corrupted.outputs["default"].count_rows() > 0
    assert corrupted.published is False


def test_approval_cannot_hide_a_hard_artifact_finding(backend_name):
    rules = _rules(backend_name)
    lattice = build(rules, _metadata())
    corrupted = _corrupt_fold(lattice)
    report = validate(corrupted, rules)
    hard_ids = tuple(
        row["finding_id"] for row in report.findings.filter(ma.col("severity").eq("error"))
        .select("finding_id").to_dicts()
    )

    assert hard_ids
    with pytest.raises(ValueError):
        approve(report, hard_ids)
