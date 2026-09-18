"""Exact, evidence-bound accumulator apply outcomes.

These cases deliberately assert source UUID memberships and native folded values;
no ranking, prime-product, or legacy best-match surface is part of the contract.
"""

from __future__ import annotations

import pytest

import mountainash_rules as rules
from tests.accumulator.source_analysis_fixtures import predicate
from tests.accumulator.exact_runtime_fixtures import build, declarations, uuid


TOTAL = rules.Aggregate(
    column_name="amount",
    output_name="pricing.total",
    data_type="int",
    numeric_semantics="numeric-1",
)


def _profile(
    *,
    profile_id="quote",
    mode="resolve",
    output_fields=("pricing.total",),
    provenance="none",
    dimensions=("x",),
    masks=(),
    promise="allow_unresolved",
    on_unresolved="return",
):
    values = dict(
        profile_id=profile_id,
        mode=mode,
        output_fields=list(output_fields),
        provenance=provenance,
        dimensions=sorted(dimensions),
        allow_dont_care=sorted(masks),
        promise="candidate_only" if mode == "candidates" else promise,
    )
    if mode == "resolve":
        values["on_unresolved"] = on_unresolved
    return rules.ResolutionProfile(**values)


def _contract(*, fields, profiles, contract_id="client"):
    return rules.ContextContract(
        schema_version=1,
        contract_id=contract_id,
        domain_ref="D",
        fields=[
            rules.ContextField(name=name, data_type=data_type, required=required)
            for name, data_type, required in sorted(fields)
        ],
        profiles=sorted(profiles, key=lambda profile: profile.profile_id),
    )


def _dimension(name, *, data_type="int", strategy="range", **kwargs):
    values = dict(dimension_name=name, data_type=data_type, match_strategy=strategy)
    if strategy == "range":
        values.update(range_min_field=f"{name}_lo", range_max_field=f"{name}_hi")
    values.update(kwargs)
    return rules.Dimension(**values)


def _row(number, *, lo=0, hi=20, amount=1, **values):
    return {"id": uuid(number), "x_lo": lo, "x_hi": hi, "amount": amount, **values}


def _build(rows, *, dimensions, contract, decisions=(), domain_node=None):
    kwargs = declarations(
        dimensions,
        [TOTAL],
        contracts=[contract],
        domain_node=domain_node,
    )
    return build(rows, kwargs, decisions=decisions)


def _candidate_memberships(result):
    """Return literal observable (value, contributor UUIDs) candidate facts."""
    values = {
        row["cell_id"]: row["pricing.total"]
        for row in result.candidate_cells.to_dicts()
    }
    contributors = {}
    for row in result.candidate_contributors.to_dicts():
        contributors.setdefault(row["cell_id"], []).append(row["source_id"])
    return sorted(
        (values[cell_id], tuple(sorted(source_ids)))
        for cell_id, source_ids in contributors.items()
    )


def test_c01_optional_fact_with_full_coverage_has_no_implicit_no_match():
    contract = _contract(
        fields=[("x", "int", False)],
        profiles=[_profile(mode="candidates", dimensions=("x",))],
    )
    engine, lattice = _build(
        [_row(1, lo=0, hi=10, amount=10), _row(2, lo=11, hi=20, amount=20)],
        dimensions=[_dimension("x")],
        contract=contract,
        domain_node={
            "op": "interval",
            "field": "x",
            "lower": {"type": "int", "value": "0"},
            "upper": {"type": "int", "value": "20"},
            "lower_closed": True,
            "upper_closed": True,
        },
    )

    result = engine.apply(lattice, {}, contract_id="client", profile_id="quote")

    assert result.status == "candidates"
    assert result.may_have_no_match is False
    assert _candidate_memberships(result) == [
        (10, (uuid(1),)),
        (20, (uuid(2),)),
    ]


def test_c02_optional_facts_keep_exact_alternatives_and_no_match():
    dimensions = [
        _dimension("x"),
        _dimension("region", data_type="str", strategy="exact"),
    ]
    contract = _contract(
        fields=[("x", "int", True), ("region", "str", False)],
        profiles=[_profile(mode="candidates", dimensions=("x", "region"))],
    )
    engine, lattice = _build(
        [
            _row(1, lo=1, hi=1, amount=10, region="EU"),
            _row(2, lo=1, hi=1, amount=20, region="US"),
        ],
        dimensions=dimensions,
        contract=contract,
    )

    result = engine.apply(lattice, {"x": 1}, contract_id="client", profile_id="quote")

    assert result.status == "candidates"
    assert result.may_have_no_match is True
    assert _candidate_memberships(result) == [
        (10, (uuid(1),)),
        (20, (uuid(2),)),
    ]
    assert result.values is None
    assert result.cell_id is None
    assert result.contributor_ids is None


def test_c03_c09_raw_required_boolean_is_invalid_before_a_permitted_mask():
    dimensions = [_dimension("x", data_type="bool", strategy="exact")]
    contract = _contract(
        fields=[("x", "bool", True)],
        profiles=[_profile(dimensions=("x",), masks=("x",))],
    )
    engine, lattice = _build(
        [{"id": uuid(1), "x": False, "amount": 10}],
        dimensions=dimensions,
        contract=contract,
        decisions=[("profile_counterexample", ())],
    )

    valid = engine.apply(
        lattice,
        {"x": False},
        contract_id="client",
        profile_id="quote",
        dont_care=("x",),
    )
    assert (valid.status, valid.reason) == ("unresolved", "insufficient_context")

    for invalid in (0, 1, "false", None):
        with pytest.raises(rules.InvalidContextError) as error:
            engine.apply(
                lattice,
                {"x": invalid},
                contract_id="client",
                profile_id="quote",
                dont_care=("x",),
            )
        result = error.value.result
        assert result.status == "invalid_context"
        assert result.reason in {"invalid_type", "missing_required"}
        with pytest.raises(rules.InvalidContextError) as caught:
            result.raise_for_status()
        assert caught.value.outcome == result.outcome
        assert caught.value.result is result


def test_c04_one_cell_plus_implicit_no_hit_is_not_a_decision():
    contract = _contract(
        fields=[("x", "int", False)],
        profiles=[_profile(dimensions=("x",))],
    )
    engine, lattice = _build(
        [_row(1, lo=1, hi=1, amount=10)],
        dimensions=[_dimension("x")],
        contract=contract,
        decisions=[("profile_counterexample", ())],
    )

    result = engine.apply(lattice, {}, contract_id="client", profile_id="quote")

    assert result.status == "unresolved"
    assert result.reason == "insufficient_context"
    assert result.may_have_no_match is True
    assert _candidate_memberships(result) == [(10, (uuid(1),))]


def test_c05_overlap_keeps_alternative_cells_without_summing_them():
    contract = _contract(
        fields=[("x", "int", False)],
        profiles=[_profile(mode="candidates", dimensions=("x",))],
    )
    engine, lattice = _build(
        [_row(1, lo=0, hi=20, amount=5), _row(2, lo=10, hi=20, amount=10)],
        dimensions=[_dimension("x")],
        contract=contract,
        decisions=(("source_overlap", (uuid(1), uuid(2))),),
    )

    result = engine.apply(lattice, {}, contract_id="client", profile_id="quote")

    assert result.status == "candidates"
    assert _candidate_memberships(result) == [
        (5, (uuid(1),)),
        (15, (uuid(1), uuid(2))),
    ]


def test_c06_output_certainty_does_not_promote_cell_or_contributor_certainty():
    dimensions = [
        _dimension("x"),
        _dimension("region", data_type="str", strategy="exact"),
    ]
    output_contract = _contract(
        fields=[("x", "int", True), ("region", "str", False)],
        profiles=[_profile(dimensions=("x", "region"))],
    )
    rows = [
        _row(1, lo=1, hi=1, amount=10, region="EU"),
        _row(2, lo=1, hi=1, amount=10, region="US"),
    ]
    kwargs = declarations(dimensions, [TOTAL], contracts=[output_contract])
    regional_domain = predicate(
        {
            "op": "in",
            "field": "region",
            "values": [
                {"type": "str", "value": "EU"},
                {"type": "str", "value": "US"},
            ],
        }
    )
    kwargs["predicates"] = [regional_domain]
    kwargs["domains"] = [
        rules.DomainDefinition(
            schema_version=1,
            domain_id="D",
            fields=[
                rules.DomainField(name="region", data_type="str"),
                rules.DomainField(name="x", data_type="int"),
            ],
            predicate_id=regional_domain["id"],
        )
    ]
    engine, lattice = build(rows, kwargs)

    result = engine.apply(lattice, {"x": 1}, contract_id="client", profile_id="quote")

    assert result.status == "decision"
    assert result.values == {"pricing.total": 10}
    assert result.cell_id is None
    assert result.contributor_ids is None
    assert result.lineage is None


def test_c07_c18_a_zero_contribution_is_a_real_definite_hit():
    contract = _contract(
        fields=[("x", "int", True)],
        profiles=[_profile(dimensions=("x",), provenance="contributors")],
    )
    engine, lattice = _build(
        [_row(1, lo=1, hi=1, amount=0)],
        dimensions=[_dimension("x")],
        contract=contract,
    )

    result = engine.apply(lattice, {"x": 1}, contract_id="client", profile_id="quote")

    assert result.status == "decision"
    assert result.values == {"pricing.total": 0}
    assert result.contributor_ids == (uuid(1),)
    assert result.may_have_no_match is False
    assert result.lineage.to_dicts() == [
        {
            "output_name": "pricing.total",
            "column_name": "amount",
            "operation": "sum",
            "source_id": uuid(1),
            "source_label": None,
        }
    ]


def test_c08_masks_and_output_projection_recompute_the_whole_candidate_set():
    contract = _contract(
        fields=[("x", "int", True)],
        profiles=[
            _profile(mode="candidates", dimensions=("x",), masks=("x",)),
            _profile(
                profile_id="identity",
                mode="candidates",
                output_fields=(),
                dimensions=("x",),
                masks=("x",),
            ),
        ],
    )
    engine, lattice = _build(
        [_row(1, lo=1, hi=1, amount=10), _row(2, lo=2, hi=2, amount=20)],
        dimensions=[_dimension("x")],
        contract=contract,
    )

    observed = engine.apply(lattice, {"x": 1}, contract_id="client", profile_id="quote")
    masked = engine.apply(
        lattice,
        {"x": 1},
        contract_id="client",
        profile_id="quote",
        dont_care=("x",),
    )
    projected = engine.apply(
        lattice,
        {"x": 1},
        contract_id="client",
        profile_id="identity",
        dont_care=("x",),
    )

    assert _candidate_memberships(observed) == [(10, (uuid(1),))]
    assert _candidate_memberships(masked) == [(10, (uuid(1),)), (20, (uuid(2),))]
    assert all(
        set(row) == {"cell_id", "predicate_id", "contributor_set_id"}
        for row in projected.candidate_cells.to_dicts()
    )
    assert {
        row["source_id"] for row in projected.candidate_contributors.to_dicts()
    } == {
        uuid(1),
        uuid(2),
    }


def test_c11_resolve_return_withhold_and_reject_have_distinct_outcomes():
    profiles = [
        _profile(profile_id="return", dimensions=("x",), on_unresolved="return"),
        _profile(profile_id="withhold", dimensions=("x",), on_unresolved="withhold"),
        _profile(profile_id="reject", dimensions=("x",), on_unresolved="reject"),
    ]
    contract = _contract(fields=[("x", "int", False)], profiles=profiles)
    engine, lattice = _build(
        [_row(1, lo=1, hi=1, amount=10)],
        dimensions=[_dimension("x")],
        contract=contract,
        decisions=[("profile_counterexample", ())],
    )

    returned = engine.apply(lattice, {}, contract_id="client", profile_id="return")
    withheld = engine.apply(lattice, {}, contract_id="client", profile_id="withhold")
    with pytest.raises(rules.UnresolvedContextError) as error:
        engine.apply(lattice, {}, contract_id="client", profile_id="reject")
    rejected = error.value.result

    assert (returned.status, returned.reason) == ("unresolved", "insufficient_context")
    assert (withheld.status, withheld.reason) == ("no_match", "insufficient_context")
    assert (rejected.status, rejected.reason) == ("rejected", "insufficient_context")
    with pytest.raises(rules.UnresolvedContextError) as caught:
        rejected.raise_for_status()
    assert caught.value.outcome == rejected.outcome
    assert caught.value.result is rejected


def test_c12_impossible_definite_profile_is_rejected_by_the_real_build_gate():
    contract = _contract(
        fields=[("x", "int", False)],
        profiles=[_profile(dimensions=("x",), promise="definite_outcome")],
    )
    kwargs = declarations([_dimension("x")], [TOTAL], contracts=[contract])
    rows = [_row(1, lo=1, hi=1, amount=10)]

    bundle = rules.analyze_sources(rows, **kwargs)

    assert {
        (finding.check_id, finding.code) for finding in bundle.validation["findings"]
    } == {
        ("profiles", "profile_counterexample"),
    }
    with pytest.raises(ValueError):
        rules.validate_build_input(
            rows,
            bundle=bundle,
            analysis_input_id=bundle.validation["reports"][0].analysis_input_id,
            source_report_id=bundle.validation["reports"][0].id,
            approvals=[],
            **kwargs,
        )


def test_c15_batch_preserves_input_order_per_context_masks_and_empty_projection():
    contract = _contract(
        fields=[("x", "int", True)],
        profiles=[
            _profile(
                mode="candidates", output_fields=(), dimensions=("x",), masks=("x",)
            )
        ],
    )
    engine, lattice = _build(
        [_row(1, lo=1, hi=1, amount=10), _row(2, lo=2, hi=2, amount=20)],
        dimensions=[_dimension("x")],
        contract=contract,
    )

    index = engine.index([lattice])
    batch = index.apply_batch(
        [{"request": "second", "x": 2}, {"request": "first", "x": 1}],
        contract_id="client",
        profile_id="quote",
        context_id_field="request",
        dont_care_field=None,
    )

    assert batch.context_ids.to_dicts() == [
        {"__context_id": "second"},
        {"__context_id": "first"},
    ]
    assert [item.status for item in batch.records.values()] == [
        "candidates",
        "candidates",
    ]
    assert all(
        set(row) == {"cell_id", "predicate_id", "contributor_set_id"}
        for row in batch.for_context("first").candidate_cells.to_dicts()
    )
    assert batch.for_context("second").candidate_contributors.to_dicts() == [
        {
            "cell_id": batch.for_context("second").candidate_cells.to_dicts()[0][
                "cell_id"
            ],
            "source_id": uuid(2),
        },
    ]
    with pytest.raises(KeyError):
        batch.for_context("missing")


def test_c17_domain_and_guard_admission_never_create_candidates():
    dimensions = [
        _dimension("x"),
        rules.Dimension(
            dimension_name="name",
            data_type="str",
            match_strategy="context_regex",
            regex_pattern="^A",
        ),
    ]
    contract = _contract(
        fields=[("x", "int", True), ("name", "str", True)],
        profiles=[_profile(mode="candidates", dimensions=("x",))],
    )
    engine, lattice = _build(
        [{**_row(1, lo=1, hi=1, amount=10), "name": "ANY"}],
        dimensions=dimensions,
        contract=contract,
        domain_node={
            "op": "interval",
            "field": "x",
            "lower": {"type": "int", "value": "0"},
            "upper": {"type": "int", "value": "20"},
            "lower_closed": True,
            "upper_closed": True,
        },
    )

    with pytest.raises(rules.InvalidContextError) as error:
        engine.apply(
            lattice, {"x": 1, "name": "B"}, contract_id="client", profile_id="quote"
        )
    bad_guard = error.value.result
    with pytest.raises(rules.InvalidContextError) as error:
        engine.apply(
            lattice, {"x": 21, "name": "A"}, contract_id="client", profile_id="quote"
        )
    out_of_domain = error.value.result

    assert (bad_guard.status, bad_guard.reason, bad_guard.candidate_cells) == (
        "invalid_context",
        "guard_failed",
        None,
    )
    assert (
        out_of_domain.status,
        out_of_domain.reason,
        out_of_domain.candidate_cells,
    ) == (
        "invalid_context",
        "domain_contradiction",
        None,
    )


def test_c18_null_contribution_is_rejected_at_the_real_source_build_boundary():
    contract = _contract(
        fields=[("x", "int", True)],
        profiles=[_profile(dimensions=("x",))],
    )

    with pytest.raises(ValueError):
        _build(
            [_row(1, lo=1, hi=1, amount=None)],
            dimensions=[_dimension("x")],
            contract=contract,
        )
