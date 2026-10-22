"""A field mapping both null and non-null truthy values builds, matches and round-trips."""

from __future__ import annotations

from mountainash_rules.core.aggregate import Aggregate
from mountainash_rules.core.constants import DataType, MatchStrategy
from mountainash_rules.core.dimension import ContextField, Dimension, DimensionsMetadata
from mountainash_rules.decision_table.build import build_decision_table
from mountainash_rules.decision_table.table import DecisionTable

from tests.decision_table.apply_support import context_model, fetch
from tests.decision_table.construction_support import validated_rules

Answer = context_model(answer=str)


def null_truthy_rules():
    field = ContextField(name="answer", data_type=DataType.STR, wildcard="*", truthy={"yes": 1, None: -1})
    metadata = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(field,),
        dimensions=(
            Dimension(dimension_name="agree", context_field="answer", match_strategy=MatchStrategy.TRUTHY),
        ),
        outputs=(Aggregate(scope="s", output_name="score", contribution_column="score", data_type=DataType.INT),),
    )
    data = {"rule_id": ["Y", "ANY"], "scope": ["s", "s"], "agree": ["yes", "*"], "score": [1, 2]}
    return data, metadata


def test_a_field_mapping_null_and_non_null_truthy_values_builds_and_matches(backend_name, tmp_path):
    data, metadata = null_truthy_rules()

    table = build_decision_table(validated_rules(backend_name, data, metadata))

    contexts = [Answer(request_id=1, answer="yes"), Answer(request_id=2, answer=None), Answer(request_id=3)]
    matched = {}
    for row in fetch(table, contexts):
        matched.setdefault(row["request_id"], set()).add(row["score"])
    # "yes" maps to 1 and explicit null to -1, so null misses the entry requiring "yes" (score 1+2);
    # an omitted answer is unknown and matches both entries
    assert matched == {1: {2, 3}, 2: {2}, 3: {2, 3}}

    table.save(str(tmp_path / "saved"))
    loaded = DecisionTable.load(str(tmp_path / "saved"))
    assert loaded.build_id == table.build_id
    assert fetch(loaded, contexts) == fetch(table, contexts)


def test_the_build_id_is_independent_of_truthy_declaration_order(backend_name):
    data, metadata = null_truthy_rules()
    reordered = ContextField(name="answer", data_type=DataType.STR, wildcard="*", truthy={None: -1, "yes": 1})
    other = DimensionsMetadata(
        uuid_column="rule_id",
        scope_column="scope",
        fields=(reordered,),
        dimensions=metadata.dimensions,
        outputs=metadata.outputs,
    )

    first = build_decision_table(validated_rules(backend_name, data, metadata))
    second = build_decision_table(validated_rules(backend_name, data, other))

    assert first.build_id == second.build_id

