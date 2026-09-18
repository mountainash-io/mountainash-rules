"""Scoped exact-layout discovery and dense-word integrity controls."""

from dataclasses import replace

import pytest

from mountainash_rules import (
    Aggregate,
    Dimension,
    DimensionsMetadata,
    DomainDefinition,
    DomainField,
)
from mountainash_rules.core.codec import content_id
from mountainash_rules.core.contracts import ExactLimits, OperationBudget
from mountainash_rules.core.predicates import PredicateGraph
from mountainash_rules.engines.accumulator.compiler import (
    analyze_sources,
    prepare_sources,
)


def _limits(**overrides):
    values = dict(
        language=dict(
            max_input_bytes=100_000,
            max_nesting=64,
            max_nfa_states=4_096,
            max_states=4_096,
            max_transitions=65_536,
            max_work=1_000_000,
        ),
        max_input_bytes=10_000_000,
        max_output_bytes=10_000_000,
        max_work=10**12,
        max_live_bytes=100_000_000,
        max_predicate_nodes=100_000,
        max_dfa_states=100_000,
        max_dfa_transitions=1_000_000,
        max_theory_states=100_000,
        max_regions=1_000_000,
        max_scopes=100_000,
        max_source_scope_edges=1_000_000,
        max_contributor_edges=1_000_000,
        max_word_rows=1_000_000,
        max_numeric_bits=100_000,
        max_witnesses=100_000,
    )
    values.update(overrides)
    return ExactLimits(**values)


def _prepared(rows, *, limits=None):
    fields = [DomainField(name="x", data_type="int")]
    graph = PredicateGraph(
        fields, budget=OperationBudget(limits or _limits(), "layout")
    )
    domain = DomainDefinition(
        schema_version=1,
        domain_id="layout-domain",
        fields=fields,
        predicate_id=graph.interval("x", 0, 20, lower_closed=True, upper_closed=True),
    )
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(
                dimension_name="x",
                data_type="int",
                match_strategy="range",
                range_min_field="lo",
                range_max_field="hi",
            )
        ]
    )
    routing = {
        "schema_version": 1,
        "semantics": "exact-key-1",
        "key_dimensions": [],
        "partition_keys": [[]],
    }
    return prepare_sources(
        rows,
        graph=graph,
        domain=domain,
        metadata=metadata,
        aggregates=[
            Aggregate(
                column_name="amount",
                output_name="pricing.total",
                data_type="int",
                numeric_semantics="numeric-1",
            )
        ],
        ruleset_id="layout-domain",
        source_id_field="id",
        source_label_field=None,
        routing={"id": content_id("routing", routing), "payload": routing},
        regex_options=None,
    )


def _row(index, lo=0, hi=20):
    return {
        "id": f"00000000-0000-0000-0000-{index:012d}",
        "lo": lo,
        "hi": hi,
        "amount": index + 1,
    }


def test_unsegmented_layout_has_one_scope_and_omits_no_hit_vectors():
    from mountainash_rules.engines.accumulator.layout import (
        discover_scoped,
        materialize_layout,
        validate_layout,
    )

    prepared = _prepared([_row(1, 5, 10)])
    discovery = discover_scoped(prepared, key_values=[], segmentation_fields=())
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)

    assert len(discovery.scopes) == 1
    assert discovery.scopes[0].keys == ()
    assert len(layout.scopes) == 1
    assert len(layout.vectors) == 1
    assert len(layout.words) == 1
    validate_layout(analysis, layout, budget=prepared.graph.budget)


def test_segmented_scopes_cover_source_free_regions_and_keep_spanning_source_once():
    from mountainash_rules.engines.accumulator.layout import (
        discover_scoped,
        materialize_layout,
        validate_layout,
    )

    broad, narrow = _row(1), _row(2, 5, 10)
    prepared = _prepared([broad, narrow])
    discovery = discover_scoped(prepared, key_values=[], segmentation_fields=("x",))
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)

    assert len(discovery.scopes) == 3
    assert all(scope.keys[0].field == "x" for scope in discovery.scopes)
    assert broad["id"] in {
        source_id
        for scope in discovery.scopes
        for source_id in scope.applicable_source_ids
    }
    assert sum(not scope.applicable_source_ids for scope in discovery.scopes) == 0
    assert {vector.cell_id for vector in layout.vectors} == {
        cell.cell_id for cell in analysis.cells
    }
    validate_layout(analysis, layout, budget=prepared.graph.budget)

    assert layout.columns["scopes"] == (
        ("__scope_id", "utf8", False),
        ("__predicate_id", "utf8", False),
        ("__map_id", "utf8", False),
        ("__source_count", "int64", False),
        ("__word_count", "int64", False),
    )
    assert dict(layout.scopes[0])["__scope_id"] == layout.scopes[0].scope_id


def test_segmented_layout_retains_source_free_scope_and_rejects_dense_word_corruption():
    from mountainash_rules.engines.accumulator.layout import (
        ExactLayout,
        WordRow,
        discover_scoped,
        materialize_layout,
        validate_layout,
    )

    prepared = _prepared([_row(1, 5, 10)])
    discovery = discover_scoped(prepared, key_values=[], segmentation_fields=("x",))
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)

    assert any(not scope.applicable_source_ids for scope in discovery.scopes)
    assert len(layout.vectors) == 1
    word = layout.words[0]
    corrupt = ExactLayout(
        scopes=layout.scopes,
        scope_keys=layout.scope_keys,
        source_maps=layout.source_maps,
        vectors=layout.vectors,
        words=(replace(word, word_value=word.word_value | (1 << 62)),),
        segmentation_fields=layout.segmentation_fields,
    )
    with pytest.raises(ValueError, match="membership|unused"):
        validate_layout(analysis, corrupt, budget=prepared.graph.budget)


def test_dense_words_span_63_bit_boundaries_and_wordwise_remap_is_uuid_based():
    from mountainash_rules.engines.accumulator.layout import (
        decode_membership,
        discover_scoped,
        materialize_layout,
        remap_words,
        validate_layout,
        wordwise_intersection,
        wordwise_strict_subset,
        wordwise_union,
    )

    prepared = _prepared([_row(index) for index in range(128)])
    discovery = discover_scoped(prepared, key_values=[], segmentation_fields=())
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)

    scope = layout.scopes[0]
    words = tuple(word.word_value for word in layout.words)
    assert scope.source_count == 128
    assert scope.word_count == 3
    assert words == ((1 << 63) - 1, (1 << 63) - 1, 3)
    positions = {row.source_id: row.position for row in layout.source_maps}
    assert positions[_row(62)["id"]] == 62
    assert positions[_row(63)["id"]] == 63
    assert positions[_row(126)["id"]] == 126
    assert positions[_row(127)["id"]] == 127
    assert decode_membership(
        words,
        {row.source_id: row.position for row in layout.source_maps},
    ) == frozenset(source.source_id for source in analysis.sources)
    assert wordwise_union(words, (0, 0, 0)) == words
    assert wordwise_intersection(words, (0, (1 << 63) - 1, 0)) == (
        0,
        (1 << 63) - 1,
        0,
    )
    assert wordwise_strict_subset((1, 0, 0), words)
    reversed_positions = {
        row.source_id: scope.source_count - 1 - row.position
        for row in layout.source_maps
    }
    sparse = (1 << 62, 1, 2)
    remapped = remap_words(sparse, positions, reversed_positions)
    assert remapped == (1, 6, 0)
    assert decode_membership(remapped, reversed_positions) == {
        _row(62)["id"],
        _row(63)["id"],
        _row(127)["id"],
    }
    thousand_positions = {f"source-{index}": index for index in range(1_000)}
    thousand_words = ((1 << 63) - 1,) * 15 + ((1 << 55) - 1,)
    assert len(thousand_words) == 16
    assert decode_membership(thousand_words, thousand_positions) == frozenset(
        thousand_positions
    )
    validate_layout(analysis, layout, budget=prepared.graph.budget)


def test_reallocated_positions_cannot_reuse_the_old_map_identity():
    from mountainash_rules.engines.accumulator.layout import (
        discover_scoped,
        materialize_layout,
        validate_layout,
    )

    prepared = _prepared([_row(1), _row(2)])
    discovery = discover_scoped(prepared, key_values=[], segmentation_fields=())
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)
    corrupt = replace(
        layout,
        source_maps=tuple(
            replace(row, position=1 - row.position) for row in layout.source_maps
        ),
    )
    # All sources contribute, so the words and decoded set are unchanged. Only
    # the stale structural map hash reveals the altered physical allocation.
    with pytest.raises(ValueError):
        validate_layout(analysis, corrupt, budget=prepared.graph.budget)


def test_missing_vector_piece_rejects_even_when_cell_has_another_scope():
    from mountainash_rules.engines.accumulator.layout import (
        discover_scoped,
        materialize_layout,
        validate_layout,
    )

    prepared = _prepared([_row(1)])
    graph = prepared.graph
    domain = prepared.domain.model_copy(
        update={
            "predicate_id": graph.or_(
                graph.interval("x", 0, 10, lower_closed=True, upper_closed=True),
                graph.interval("x", 11, 20, lower_closed=True, upper_closed=True),
            )
        }
    )
    prepared = replace(
        prepared,
        domain=domain,
        domain_digest=content_id(
            "domain", domain.model_dump(mode="json", exclude_none=True)
        ),
    )
    discovery = discover_scoped(prepared, key_values=[], segmentation_fields=("x",))
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)
    assert len(analysis.cells) == 1
    assert len(layout.vectors) == 2
    removed = layout.vectors[0].vector_id
    corrupt = replace(
        layout,
        vectors=layout.vectors[1:],
        words=tuple(row for row in layout.words if row.vector_id != removed),
    )
    with pytest.raises(ValueError):
        validate_layout(analysis, corrupt, budget=prepared.graph.budget)


def test_actual_thousand_source_layout_retains_all_sixteen_dense_words():
    from mountainash_rules.engines.accumulator.layout import (
        discover_scoped,
        materialize_layout,
        validate_layout,
    )

    prepared = _prepared([_row(index) for index in range(1000)])
    discovery = discover_scoped(prepared, key_values=[], segmentation_fields=())
    analysis = analyze_sources(prepared, key_values=[], overlay=discovery.overlay)
    layout = materialize_layout(analysis, discovery)
    assert layout.scopes[0].word_count == 16
    assert tuple(word.word_value for word in layout.words) == ((1 << 63) - 1,) * 15 + (
        (1 << 55) - 1,
    )
    assert analysis.cells[0].contributors == tuple(
        _row(index)["id"] for index in range(1000)
    )
    assert analysis.cells[0].outputs["pricing.total"] == 500500
    validate_layout(analysis, layout, budget=prepared.graph.budget)
