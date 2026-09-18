"""Observable exact-cell edge cases for the accumulator runtime."""

import pytest

import mountainash_rules as rules
from tests.accumulator.exact_runtime_fixtures import (
    build,
    declarations,
    memberships,
    uuid,
)


def _aggregate(column, output, *, operation="sum", data_type="int"):
    return rules.Aggregate(
        column_name=column,
        output_name=output,
        operation=operation,
        data_type=data_type,
        numeric_semantics="numeric-1",
    )


def _candidate_contract(dimensions, outputs, *, contract_id="client"):
    return rules.ContextContract(
        schema_version=1,
        contract_id=contract_id,
        domain_ref="D",
        fields=[
            rules.ContextField(
                name=dimension.resolved_context_field,
                data_type=dimension.data_type,
                required=False,
            )
            for dimension in sorted(dimensions, key=lambda item: item.dimension_name)
        ],
        profiles=[
            rules.ResolutionProfile(
                profile_id="inspect",
                mode="candidates",
                output_fields=sorted(outputs),
                provenance="none",
                dimensions=sorted(
                    dimension.dimension_name
                    for dimension in dimensions
                    if dimension.role == rules.DimensionRole.CONSTRAINT
                    and dimension.match_strategy != rules.MatchStrategy.CONTEXT_REGEX
                ),
                allow_dont_care=[],
                promise="candidate_only",
            )
        ],
    )


def _build(
    rows,
    dimensions,
    aggregates=(),
    *,
    decisions=(),
    partition_keys=((),),
    partition_key=None,
):
    outputs = [aggregate.output_name for aggregate in aggregates]
    contract = _candidate_contract(dimensions, outputs)
    kwargs = declarations(
        dimensions,
        aggregates,
        contracts=[contract],
        partition_keys=partition_keys,
    )
    return build(rows, kwargs, decisions=decisions, partition_key=partition_key)


def _candidate_facts(result, output):
    values = {row["cell_id"]: row[output] for row in result.candidate_cells.to_dicts()}
    contributors = {}
    for row in result.candidate_contributors.to_dicts():
        contributors.setdefault(row["cell_id"], set()).add(row["source_id"])
    return sorted(
        (values[cell_id], tuple(sorted(source_ids)))
        for cell_id, source_ids in contributors.items()
    )


def _apply(engine, lattice, context):
    return engine.apply(
        lattice,
        context,
        contract_id="client",
        profile_id="inspect",
    )


class TestEmptyRules:
    def test_empty_build_keeps_declared_typed_output_column(self):
        dimensions = [rules.Dimension(dimension_name="channel")]
        aggregate = _aggregate("margin", "pricing.margin")
        _, lattice = _build([], dimensions, [aggregate])

        assert lattice.count == 0
        assert lattice.combinations.columns == [
            "cell_id",
            "predicate_id",
            "contributor_set_id",
            "pricing.margin",
        ]


class TestMultipleAggregates:
    def test_matching_sources_fold_each_declared_output_once(self):
        dimensions = [rules.Dimension(dimension_name="channel")]
        aggregates = [
            _aggregate("margin", "pricing.margin"),
            _aggregate("fee", "pricing.fee"),
        ]
        rows = [
            {"id": uuid(1), "channel": "A", "margin": 1, "fee": 10},
            {"id": uuid(2), "channel": "A", "margin": 2, "fee": 20},
            {"id": uuid(3), "channel": "B", "margin": 3, "fee": 30},
        ]
        engine, lattice = _build(
            rows,
            dimensions,
            aggregates,
            decisions=[
                ("duplicate_source", (uuid(1), uuid(2))),
                ("source_overlap", (uuid(1), uuid(2))),
            ],
        )

        margin = _apply(engine, lattice, {"channel": "A"})
        fee = _apply(engine, lattice, {"channel": "A"})

        assert _candidate_facts(margin, "pricing.margin") == [
            (3, (uuid(1), uuid(2))),
        ]
        assert _candidate_facts(fee, "pricing.fee") == [
            (30, (uuid(1), uuid(2))),
        ]

    @pytest.mark.parametrize(
        ("operation", "expected"),
        [("sum", 14), ("min", 4), ("max", 10), ("product", 40)],
    )
    def test_each_fold_operation_uses_exact_source_membership(
        self, operation, expected
    ):
        dimensions = [rules.Dimension(dimension_name="channel")]
        aggregate = _aggregate(
            "amount",
            f"pricing.{operation}",
            operation=operation,
        )
        rows = [
            {"id": uuid(1), "channel": "A", "amount": 10},
            {"id": uuid(2), "channel": rules.UNKNOWN, "amount": 4},
        ]
        engine, lattice = _build(
            rows,
            dimensions,
            [aggregate],
            decisions=[("source_overlap", (uuid(1), uuid(2)))],
        )

        result = _apply(engine, lattice, {"channel": "A"})

        assert _candidate_facts(result, f"pricing.{operation}") == [
            (expected, (uuid(1), uuid(2))),
        ]


class TestUnsupportedAggregateOperation:
    def test_unsupported_operation_is_rejected_before_source_admission(self):
        with pytest.raises(ValueError):
            rules.Aggregate(
                column_name="margin",
                output_name="pricing.median",
                operation="median",
                data_type="int",
                numeric_semantics="numeric-1",
            )


class TestSourceApplicability:
    def test_identical_sources_have_one_applicable_folded_cell(self):
        dimensions = [rules.Dimension(dimension_name="channel")]
        aggregate = _aggregate("margin", "pricing.margin")
        rows = [
            {"id": uuid(1), "channel": "A", "margin": 1},
            {"id": uuid(2), "channel": "A", "margin": 2},
            {"id": uuid(3), "channel": "A", "margin": 3},
        ]
        engine, lattice = _build(
            rows,
            dimensions,
            [aggregate],
            decisions=[
                (code, (uuid(left), uuid(right)))
                for code in ("duplicate_source", "source_overlap")
                for left, right in ((1, 2), (1, 3), (2, 3))
            ],
        )

        result = _apply(engine, lattice, {"channel": "A"})

        assert _candidate_facts(result, "pricing.margin") == [
            (6, (uuid(1), uuid(2), uuid(3))),
        ]

    def test_incompatible_sources_remain_separate_at_their_literal_contexts(self):
        dimensions = [rules.Dimension(dimension_name="channel")]
        aggregate = _aggregate("margin", "pricing.margin")
        rows = [
            {"id": uuid(1), "channel": "A", "margin": 1},
            {"id": uuid(2), "channel": "B", "margin": 2},
        ]
        engine, lattice = _build(rows, dimensions, [aggregate])

        assert _candidate_facts(
            _apply(engine, lattice, {"channel": "A"}), "pricing.margin"
        ) == [(1, (uuid(1),))]
        assert _candidate_facts(
            _apply(engine, lattice, {"channel": "B"}), "pricing.margin"
        ) == [(2, (uuid(2),))]

    def test_wildcard_and_concrete_source_apply_to_their_real_regions(self):
        dimensions = [rules.Dimension(dimension_name="channel")]
        aggregate = _aggregate("margin", "pricing.margin")
        rows = [
            {"id": uuid(1), "channel": "A", "margin": 1},
            {"id": uuid(2), "channel": rules.UNKNOWN, "margin": 2},
        ]
        engine, lattice = _build(
            rows,
            dimensions,
            [aggregate],
            decisions=[("source_overlap", (uuid(1), uuid(2)))],
        )

        assert _candidate_facts(
            _apply(engine, lattice, {"channel": "A"}), "pricing.margin"
        ) == [(3, (uuid(1), uuid(2)))]
        assert _candidate_facts(
            _apply(engine, lattice, {"channel": "B"}), "pricing.margin"
        ) == [(2, (uuid(2),))]


class TestNoConstraintDimensions:
    def test_partition_only_artifact_has_the_declared_combined_source_output(self):
        dimensions = [
            rules.Dimension(
                dimension_name="product_id",
                data_type="int",
                match_strategy="exact_key",
                role=rules.DimensionRole.CONTEXT_KEY,
            )
        ]
        aggregate = _aggregate("margin", "pricing.margin")
        rows = [
            {"id": uuid(1), "product_id": 1, "margin": 1},
            {"id": uuid(2), "product_id": 1, "margin": 2},
        ]
        engine, lattice = _build(
            rows,
            dimensions,
            [aggregate],
            partition_keys=((1,), (None,)),
            partition_key={"product_id": 1},
            decisions=[
                ("duplicate_source", (uuid(1), uuid(2))),
                ("singleton_boundary_overlap", (uuid(1), uuid(2))),
            ],
        )

        result = _apply(engine, lattice, {"product_id": 1})

        assert _candidate_facts(result, "pricing.margin") == [
            (3, (uuid(1), uuid(2))),
        ]
        assert set(memberships(lattice).values()) == {frozenset((uuid(1), uuid(2)))}


class TestOrderedConstraintApplicability:
    def test_overlapping_ranges_fold_only_where_both_literal_sources_apply(self):
        dimensions = [
            rules.Dimension(
                dimension_name="lvr",
                data_type="int",
                match_strategy="range",
                range_min_field="lvr_min",
                range_max_field="lvr_max",
            )
        ]
        aggregate = _aggregate("margin", "pricing.margin")
        rows = [
            {"id": uuid(1), "lvr_min": 50, "lvr_max": 100, "margin": 1},
            {"id": uuid(2), "lvr_min": 70, "lvr_max": 90, "margin": 2},
        ]
        engine, lattice = _build(
            rows,
            dimensions,
            [aggregate],
            decisions=[("source_overlap", (uuid(1), uuid(2)))],
        )

        assert _candidate_facts(
            _apply(engine, lattice, {"lvr": 60}), "pricing.margin"
        ) == [(1, (uuid(1),))]
        assert _candidate_facts(
            _apply(engine, lattice, {"lvr": 75}), "pricing.margin"
        ) == [(3, (uuid(1), uuid(2)))]

    @pytest.mark.parametrize(
        ("strategy", "field", "values", "context"),
        [
            ("greater_than", "score", [10, 20], {"score": 30}),
            ("less_than", "cap", [100, 50], {"cap": 40}),
        ],
    )
    def test_ordered_thresholds_fold_the_sources_applicable_to_context(
        self, strategy, field, values, context
    ):
        dimensions = [
            rules.Dimension(
                dimension_name=field,
                data_type="int",
                match_strategy=strategy,
            )
        ]
        aggregate = _aggregate("margin", "pricing.margin")
        rows = [
            {"id": uuid(1), field: values[0], "margin": 1},
            {"id": uuid(2), field: values[1], "margin": 2},
        ]
        engine, lattice = _build(
            rows,
            dimensions,
            [aggregate],
            decisions=[("source_overlap", (uuid(1), uuid(2)))],
        )

        result = _apply(engine, lattice, context)

        assert _candidate_facts(result, "pricing.margin") == [
            (3, (uuid(1), uuid(2))),
        ]
