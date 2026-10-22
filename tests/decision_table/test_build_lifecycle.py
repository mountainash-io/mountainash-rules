"""Plan 2 Task 5: build orchestration, checkpoints, logging, cleanup, build identity."""

from __future__ import annotations

import dataclasses
import importlib
import logging
from contextlib import contextmanager
from decimal import Decimal

import polars as pl
import pytest
from mountainash import CheckpointPolicy
from mountainash.relations import Relation
from mountainash.relations.core.errors import MaterializationScopeClosedError
from mountainash.relations.core.relation_api import materialization_scope as scope_module
from mountainash.relations.core.relation_nodes import ReadRelNode

from mountainash_rules.core.aggregate import Aggregate, AggregateOp
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, DimensionsMetadata
from mountainash_rules.decision_table import build as build_module
from mountainash_rules.decision_table import stages
from mountainash_rules.decision_table.build import build_decision_table, compute_build_id
from mountainash_rules.decision_table.errors import DecisionTableError
from mountainash_rules.decision_table.prevalidate import ValidatedRules
from mountainash_rules.decision_table.table import DecisionTable

from tests.decision_table.construction_support import rows, validated_rules
from tests.decision_table.oracles.finite_completion import FiniteCompletionOracle
from tests.decision_table.table_fixtures import FIXTURES, built, dimension, score
from tests.decision_table.test_ordered_and_categorical_fields import (
    fees_rates_metadata,
    fees_rates_rules,
)
from tests.decision_table.test_stages import guard_host_extraction

M = MatchStrategy
BUILD_LOGGER = "mountainash_rules.decision_table.build"
POLICIES = {
    "every_stage": CheckpointPolicy.EVERY_STAGE,
    "never": CheckpointPolicy.NEVER,
    "every_2": CheckpointPolicy.every(2),
}


def small_patterns():
    """One pattern dimension beside a value dimension: cheap even when nothing is checkpointed."""
    code = ContextField(name="code", data_type=DataType.STR, wildcard="*")
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(code,),
        dimensions=(dimension("code", M.EXACT, "code"), dimension("pre", M.PREFIX, "code")),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["P1", "P2", "P3"],
        "scope": ["s"] * 3,
        "code": ["*", "*", "EU-1"],
        "pre": ["EU", "US", "*"],
        "score": [1, 2, 4],
    }
    return data, metadata


def snapshot_of(table: DecisionTable) -> dict:
    """Everything a table is, as normalized comparable values."""
    return {
        "entries": rows(table.entries),
        "entry_sources": rows(table.entry_sources),
        "pattern_conditions": rows(table.pattern_conditions),
        "entry_columns": list(table.entries.columns),
        "entry_metadata": table.entry_metadata.model_dump(),
        "source_metadata": table.source_metadata.model_dump(),
        "build_id": table.build_id,
    }


def build_logs(caplog):
    return [r for r in caplog.records if r.name == BUILD_LOGGER and hasattr(r, "build_stage")]


# ---------------------------------------------------------------------------
# Zero rules and empty output scopes
# ---------------------------------------------------------------------------


def typed_empty_fees_rates():
    return pl.DataFrame(
        {
            "rule_id": pl.Series([], dtype=pl.String),
            "scope": pl.Series([], dtype=pl.String),
            "region": pl.Series([], dtype=pl.String),
            "not_region": pl.Series([], dtype=pl.String),
            "fee": pl.Series([], dtype=pl.Int64),
            "rate": pl.Series([], dtype=pl.Decimal(16, 2)),
        }
    )


def test_a_zero_rule_table_is_valid_and_empty(backend_name):
    validated = validated_rules(backend_name, typed_empty_fees_rates(), fees_rates_metadata(negation=True))

    table = build_decision_table(validated)

    assert rows(table.entries) == []
    assert rows(table.entry_sources) == []
    assert rows(table.pattern_conditions) == []
    # the schema is the declared one: no contributor word columns, no invented outputs
    assert list(table.entries.columns) == ["entry_id", "scope", "region", "region_ne", "entry_sources_id", "fee", "rate"]
    assert list(table.entry_sources.columns) == ["scope", "entry_sources_id", "source_uuid"]
    assert list(table.pattern_conditions.columns) == ["column", "context_field", "strategy", "pattern"]
    assert not [c for c in table.entries.columns if c.startswith("__word")]
    assert [d.dimension_name for d in table.entry_metadata.dimensions] == ["region", "region_ne"]
    assert isinstance(table.build_id, str) and table.build_id


def test_zero_rule_pattern_table_has_no_pattern_columns(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="code", data_type=DataType.STR, wildcard="*"),),
        dimensions=(dimension("pre", M.PREFIX, "code"),),
    )
    empty = pl.DataFrame({"rule_id": pl.Series([], dtype=pl.String), "pre": pl.Series([], dtype=pl.String)})

    table = build_decision_table(validated_rules(backend_name, empty, metadata))

    assert rows(table.entries) == []
    assert rows(table.pattern_conditions) == []
    assert table.entry_metadata.dimensions == ()


def test_a_populated_scope_beside_an_empty_output_scope(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(dimension("region", M.EXACT, "region"),),
        outputs=(
            Aggregate(scope="fees", output_name="fee", contribution_column="fee", data_type=DataType.INT),
            Aggregate(scope="levies", output_name="levy", contribution_column="levy", data_type=DataType.INT),
        ),
    )
    data = {
        "rule_id": ["F1", "F2"],
        "scope": ["fees", "fees"],
        "region": ["EU", "*"],
        "fee": [10, 1],
        "levy": [0, 0],
    }
    validated = validated_rules(backend_name, data, metadata)

    table = build_decision_table(validated)

    found = table.entries.to_dicts()
    assert {row["scope"] for row in found} == {"fees"}
    assert {(row["region"], row["fee"], row["levy"]) for row in found} == {("EU", 11, None), ("*", 1, None)}
    assert {row["scope"] for row in table.entry_sources.to_dicts()} == {"fees"}
    oracle = FiniteCompletionOracle(validated, {"region": ["EU", "US"]})
    oracle.check_equivalence(table)


# ---------------------------------------------------------------------------
# Checkpoint policies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["categorical_negation", "ordered", "conjunction", "small_patterns", "null_rules"]
)
def test_every_checkpoint_policy_builds_the_same_table(backend_name, name):
    data, metadata = small_patterns() if name == "small_patterns" else FIXTURES[name]()[:2]
    validated = validated_rules(backend_name, data, metadata)

    tables = {label: build_decision_table(validated, checkpoint=policy) for label, policy in POLICIES.items()}

    expected = snapshot_of(tables["never"])
    assert expected["entries"], "the fixture must build entries"
    for label, table in tables.items():
        assert snapshot_of(table) == expected, label


def test_a_checkpointed_intermediate_is_reused_not_recomputed(backend_name, monkeypatch):
    data, metadata, _, _ = FIXTURES["conjunction"]()
    validated = validated_rules(backend_name, data, metadata)
    checkpointed: list[tuple[Relation, Relation]] = []
    consumed: dict[str, Relation] = {}
    real_checkpoint = scope_module.MaterializationScopeHandle.checkpoint

    def recording_checkpoint(self, rel, *, replaces=None):
        result = real_checkpoint(self, rel, replaces=replaces)
        checkpointed.append((rel, result))
        return result

    monkeypatch.setattr(scope_module.MaterializationScopeHandle, "checkpoint", recording_checkpoint)

    def spy(name):
        real = getattr(stages, name)

        def wrapper(first, *args, **kwargs):
            consumed[name] = first
            return real(first, *args, **kwargs)

        monkeypatch.setattr(stages, name, wrapper)

    for name in ("membership_words", "word_tuple_ids"):
        spy(name)

    def run(policy):
        checkpointed.clear()
        consumed.clear()
        build_decision_table(validated, checkpoint=policy)
        return dict(consumed), list(checkpointed)

    every_consumed, every_checkpoints = run(CheckpointPolicy.EVERY_STAGE)
    never_consumed, never_checkpoints = run(CheckpointPolicy.NEVER)

    snapshots = [result for rel, result in every_checkpoints if result is not rel]
    assert len(snapshots) == len(every_checkpoints) > 0
    # stage d reads the very relation the checkpoint returned: a snapshot, not its lazy plan
    covered = every_consumed["membership_words"]
    assert any(covered is result for result in snapshots)
    assert isinstance(covered._node, ReadRelNode)
    assert any(every_consumed["word_tuple_ids"] is result for result in snapshots)
    assert isinstance(every_consumed["word_tuple_ids"]._node, ReadRelNode)
    # without checkpoints the same stage reads the lazy plan
    assert all(result is rel for rel, result in never_checkpoints)
    assert not isinstance(never_consumed["membership_words"]._node, ReadRelNode)


# ---------------------------------------------------------------------------
# Natural identifiers
# ---------------------------------------------------------------------------


def sort_key(value):
    return (0,) if value is None else (1, tuple(value) if isinstance(value, list) else value)


@pytest.mark.parametrize("name", ["conjunction", "categorical_negation", "patterns", "null_rules"])
def test_entry_id_is_sorted_by_scope_and_cell_keys(backend_name, name):
    _, table, _, _ = built(backend_name, name)
    cell_columns = [
        c for c in table.entries.columns if c not in ("entry_id", "scope", "entry_sources_id", "fee", "rate", "score")
    ]

    found = table.entries.to_dicts()

    ids = sorted(row["entry_id"] for row in found)
    assert ids == list(range(len(found)))
    by_id = sorted(found, key=lambda row: row["entry_id"])
    keys = [(row["scope"], *(sort_key(row[c]) for c in cell_columns)) for row in by_id]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_entry_id_and_build_id_do_not_depend_on_rule_row_order(backend_name):
    data, metadata, _, _ = FIXTURES["conjunction"]()
    reversed_data = {name: list(reversed(values)) for name, values in data.items()}
    reversed_data = {
        name: (pl.Series(values, dtype=data[name].dtype) if isinstance(data[name], pl.Series) else values)
        for name, values in reversed_data.items()
    }

    first = build_decision_table(validated_rules(backend_name, data, metadata))
    second = build_decision_table(validated_rules(backend_name, reversed_data, metadata))

    assert snapshot_of(first) == snapshot_of(second)


def test_the_table_is_frozen_and_derives_entry_metadata_once(backend_name):
    _, table, _, _ = built(backend_name, "categorical")

    with pytest.raises(dataclasses.FrozenInstanceError):
        table.build_id = "other"
    assert table.entry_metadata is table.entry_metadata
    assert [f.name for f in dataclasses.fields(DecisionTable)] == [
        "entries", "source_metadata", "entry_sources", "pattern_conditions", "build_id",
    ]


def test_output_names_may_not_collide_with_table_columns(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(dimension("region", M.EXACT, "region"),),
        outputs=(Aggregate(output_name="entry_id", contribution_column="fee", data_type=DataType.INT),),
    )
    data = {"rule_id": ["a"], "region": ["EU"], "fee": [1]}

    with pytest.raises(DecisionTableError, match="entry_id"):
        build_decision_table(validated_rules(backend_name, data, metadata))


# ---------------------------------------------------------------------------
# Execution counts: logged at stage boundaries
# ---------------------------------------------------------------------------


class ExecutionSpy:
    """Counts the terminals that run a plan on the backend: scalar reads, snapshots, fingerprints.

    ``Relation`` has no single execution choke point (an Ibis snapshot compiles, then copies on
    the connection), so the spy wraps each terminal construction may use.
    """

    def __init__(self, monkeypatch):
        self.count = 0
        spy = self

        def counting(owner, name):
            real = getattr(owner, name)

            def counted(*args, **kwargs):
                spy.count += 1
                return real(*args, **kwargs)

            monkeypatch.setattr(owner, name, counted)

        counting(Relation, "count_rows")
        counting(Relation, "fingerprint")
        counting(scope_module.MaterializationScopeHandle, "snapshot")


@pytest.mark.parametrize("policy", ["every_stage", "never"])
def test_backend_execution_counts_are_logged_at_each_stage_boundary(backend_name, monkeypatch, caplog, policy):
    data, metadata = small_patterns()
    validated = validated_rules(backend_name, data, metadata)
    spy = ExecutionSpy(monkeypatch)

    with caplog.at_level(logging.INFO, logger=BUILD_LOGGER):
        build_decision_table(validated, checkpoint=POLICIES[policy])

    records = build_logs(caplog)
    assert [r.build_stage for r in records] == ["a", "b", "c", "d", "e", "f"]
    per_stage = {r.build_stage: r.executions for r in records}
    assert sum(per_stage.values()) == records[-1].executions_total
    # what is logged is what the backend actually executed
    assert records[-1].executions_total == spy.count
    # stages b and c are pure expression work; a executes only pattern counts (+ checkpoints)
    assert per_stage["b"] == per_stage["c"] == 0
    assert "executions" in records[0].getMessage()
    if policy == "never":
        assert per_stage["a"] == 1  # one distinct-pattern count per pattern dimension
        # word count, three terminal snapshots, one build fingerprint
        assert per_stage["d"] == 1 and per_stage["e"] == 0 and per_stage["f"] == 4
    else:
        # one region checkpoint per column plan (code + two patterns) beside the pattern count
        assert per_stage["a"] == 1 + 3 and per_stage["d"] > 1 and per_stage["e"] == 1


def test_counts_cover_non_pattern_builds_too(backend_name, monkeypatch, caplog):
    validated = validated_rules(backend_name, fees_rates_rules(), fees_rates_metadata())
    spy = ExecutionSpy(monkeypatch)

    with caplog.at_level(logging.INFO, logger=BUILD_LOGGER):
        build_decision_table(validated, checkpoint=CheckpointPolicy.NEVER)

    records = build_logs(caplog)
    assert [r.executions for r in records] == [0, 0, 0, 1, 0, 4]
    assert records[-1].executions_total == spy.count == 5


# ---------------------------------------------------------------------------
# Construction starts from the captured relation
# ---------------------------------------------------------------------------


def set_dimension_validated(backend_name):
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(ContextField(name="region", data_type=DataType.STR, wildcard="*"),),
        dimensions=(
            dimension("region", M.SET_MEMBERSHIP, "region"),
            dimension("not_regions", M.SET_EXCLUSION, "region"),
        ),
        outputs=(score("s"),),
    )
    data = {
        "rule_id": ["a", "b"],
        "scope": ["s", "s"],
        "region": [["EU", "US"], ["*"]],
        "not_regions": [["*"], ["AU", "US"]],
        "score": [1, 2],
    }
    return validated_rules(backend_name, data, metadata)


def test_construction_starts_from_the_captured_relation_without_validation_or_fetches(backend_name, monkeypatch):
    validated = set_dimension_validated(backend_name)
    schema_calls: list[dict] = []
    real_schema = Relation.collect_schema

    def recording_schema(self, *args, **kwargs):
        schema_calls.append(kwargs)
        return real_schema(self, *args, **kwargs)

    sources = []
    real_check = stages.check_sources

    def recording_check(rules, metadata):
        sources.append(rules)
        return real_check(rules, metadata)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("construction must not validate")

    def no_resnapshot(self, *_args, **_kwargs):
        raise AssertionError("construction must not re-capture the rules")

    monkeypatch.setattr(importlib.import_module("mountainash_rules.decision_table.prevalidate"), "prevalidate", forbidden)
    monkeypatch.setattr(build_module, "prevalidate", forbidden, raising=False)
    monkeypatch.setattr(Relation, "collect_schema", recording_schema)
    monkeypatch.setattr(Relation, "snapshot", no_resnapshot)
    monkeypatch.setattr(stages, "check_sources", recording_check)
    counted = guard_host_extraction(monkeypatch, allow_count=True)

    table = build_decision_table(validated)

    assert sources and all(rules is validated.rules for rules in sources)
    assert all(not call.get("allow_execution", False) for call in schema_calls)
    assert counted, "the word count is the one scalar construction reads"
    assert {f.name for f in dataclasses.fields(ValidatedRules)} == {"rules", "source_metadata", "warnings"}
    assert table.entries is not None


def test_set_dimensions_bind_without_executing_stage_a(backend_name, monkeypatch):
    validated = set_dimension_validated(backend_name)
    spy = ExecutionSpy(monkeypatch)

    stages.stage_a_boundaries(validated)

    assert spy.count == 0


# ---------------------------------------------------------------------------
# Failure at a stage boundary closes the scope's owned snapshots
# ---------------------------------------------------------------------------


class ScopeSpy:
    """Remembers the build's scope handle and every snapshot a checkpoint took."""

    def __init__(self, monkeypatch):
        self.handles = []
        self.snapshots: list[Relation] = []
        real_scope = build_module.materialization_scope
        real_checkpoint = scope_module.MaterializationScopeHandle.checkpoint
        spy = self

        @contextmanager
        def recording_scope(**kwargs):
            with real_scope(**kwargs) as handle:
                spy.handles.append(handle)
                yield handle

        def recording_checkpoint(handle, rel, *, replaces=None):
            result = real_checkpoint(handle, rel, replaces=replaces)
            if result is not rel:
                spy.snapshots.append(result)
            return result

        monkeypatch.setattr(build_module, "materialization_scope", recording_scope)
        monkeypatch.setattr(scope_module.MaterializationScopeHandle, "checkpoint", recording_checkpoint)


@pytest.mark.parametrize(
    "stage",
    ["stage_c_coverage", "stage_d_membership", "stage_e_entry_sources", "stage_f_entries"],
)
def test_a_failure_at_a_stage_boundary_closes_the_scope_and_its_snapshots(backend_name, monkeypatch, stage):
    data, metadata, _, _ = FIXTURES["conjunction"]()
    validated = validated_rules(backend_name, data, metadata)
    spy = ScopeSpy(monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"injected at {stage}")

    monkeypatch.setattr(build_module, stage, boom)

    with pytest.raises(RuntimeError, match=f"injected at {stage}"):
        build_decision_table(validated, checkpoint=CheckpointPolicy.EVERY_STAGE)

    assert len(spy.handles) == 1
    assert spy.snapshots, "stage a checkpoints must have been taken before the failure"
    with pytest.raises(MaterializationScopeClosedError):
        spy.handles[0].snapshot(validated.rules)
    # the newest snapshot is still owned by the scope at the failure, so closing invalidated it
    with pytest.raises(Exception):
        spy.snapshots[-1].count_rows()


def test_after_a_successful_build_only_the_terminal_relations_outlive_the_scope(backend_name, monkeypatch):
    data, metadata, grid, _ = FIXTURES["categorical_negation"]()
    validated = validated_rules(backend_name, data, metadata)
    spy = ScopeSpy(monkeypatch)

    table = build_decision_table(validated)

    assert spy.snapshots
    with pytest.raises(Exception):
        spy.snapshots[-1].count_rows()
    assert table.entries.count_rows() > 0
    assert table.entry_sources.count_rows() > 0
    assert table.pattern_conditions.count_rows() == 0
    FiniteCompletionOracle(validated, grid).check_equivalence(table)


def test_a_failure_while_building_the_id_still_leaves_no_open_scope(backend_name, monkeypatch):
    validated = validated_rules(backend_name, fees_rates_rules(), fees_rates_metadata())
    spy = ScopeSpy(monkeypatch)

    def boom(_validated):
        raise RuntimeError("no fingerprint")

    monkeypatch.setattr(build_module, "compute_build_id", boom)

    with pytest.raises(RuntimeError, match="no fingerprint"):
        build_decision_table(validated)

    with pytest.raises(MaterializationScopeClosedError):
        spy.handles[0].snapshot(validated.rules)


# ---------------------------------------------------------------------------
# build_id
# ---------------------------------------------------------------------------


def test_identical_inputs_build_identical_ids(backend_name):
    first = build_decision_table(validated_rules(backend_name, fees_rates_rules(), fees_rates_metadata()))
    second = build_decision_table(validated_rules(backend_name, fees_rates_rules(), fees_rates_metadata()))

    assert first.build_id == second.build_id
    assert len(first.build_id) == 64 and int(first.build_id, 16) >= 0


def test_the_id_follows_declared_rule_columns_and_metadata_only(backend_name):
    base = validated_rules(backend_name, fees_rates_rules(), fees_rates_metadata())
    expected = compute_build_id(base)

    extra = {**fees_rates_rules(), "note": ["x", "y", "z"]}  # undeclared column
    assert compute_build_id(validated_rules(backend_name, extra, fees_rates_metadata())) == expected

    changed_cell = fees_rates_rules()
    changed_cell["fee"] = pl.Series([10, 2, None], dtype=pl.Int64)
    assert compute_build_id(validated_rules(backend_name, changed_cell, fees_rates_metadata())) != expected

    changed_rule = fees_rates_rules()
    changed_rule["rule_id"] = ["F1", "F2", "R2"]
    assert compute_build_id(validated_rules(backend_name, changed_rule, fees_rates_metadata())) != expected

    metadata = fees_rates_metadata()
    other = metadata.model_copy(
        update={"outputs": (metadata.outputs[0].model_copy(update={"operation": AggregateOp.MAX}), metadata.outputs[1])}
    )
    assert compute_build_id(validated_rules(backend_name, fees_rates_rules(), other)) != expected

    reordered = {name: list(reversed(values)) for name, values in fees_rates_rules().items()}
    reordered["fee"] = pl.Series([None, 1, 10], dtype=pl.Int64)
    reordered["rate"] = pl.Series([Decimal("-0.10"), None, None], dtype=pl.Decimal(16, 2))
    assert compute_build_id(validated_rules(backend_name, reordered, fees_rates_metadata())) == expected


def test_the_id_is_computed_without_extracting_rule_rows(backend_name, monkeypatch):
    validated = set_dimension_validated(backend_name)
    guard_host_extraction(monkeypatch, allow_count=False)

    assert compute_build_id(validated)
