---
title: "Chapter 7: Using the Exact Accumulator Engine"
description: "Analyze and authorize native sources, build immutable exact-cell artifacts, and resolve contract-bound contexts."
---

# Chapter 7: Using the Exact Accumulator Engine {#accumulatorengine}

`AccumulatorEngine` is the native exact-cell workflow. It does not select or
rank legacy rule combinations. Instead, an application analyzes its real source
material, obtains any required scoped warning approvals, passes the build gate,
and builds an immutable artifact. A later request resolves against that artifact
under a named context contract and resolution profile.

This chapter documents the available E6–E8 interfaces only. It does not claim
that E8 is complete or make a release or consumer-completeness claim.

## Inputs and limits are application-owned {#inputs-and-limits-are-application-owned}

Every operation takes the application's `ExactLimits`; there is no package
default or documentation ceiling. The same limits object (or another deliberate
application choice) is required for analysis, warning-approval attachment,
engine construction, save, load, and binding. Resource exhaustion raises a
typed `ExactResourceError`; it never returns a partial successful artifact or
outcome.

The application also owns these actual source-gate inputs:

- native rows, each with a stable UUID in the declared `source_id_field`;
- `DimensionsMetadata`, including the declared `ContextContract` records;
- complete native `Aggregate` declarations;
- `ruleset_id`, `source_id_field`, and `compilation_domain_ref`;
- domain definitions; predicate and language envelopes; routing; and a
  `ValidationPolicy`.

A native aggregate declares `column_name`, a qualified `output_name`,
`data_type`, and `numeric_semantics="numeric-1"` as one complete declaration.
A `DATETIME` declaration additionally names `timezone="naive"` or `"utc"`.
Incomplete flat aggregate metadata is not promoted to exact semantics.

## The source gate {#the-source-gate}

The following outline starts with application declarations; it intentionally
does not manufacture rows, UUIDs, policy, limits, approvals, trust, or a clean
report.

```python
from mountainash_rules import (
    analyze_sources,
    attach_warning_approvals,
    validate_build_input,
)

bundle = analyze_sources(rows, limits=limits, **source_inputs)
source_report = bundle.validation["reports"][0]

# These come from the application's review process.  Each WarningApproval names
# the report and warning IDs, authority and actor, and a scope covering them.
approvals = reviewed_warning_approvals
reviewed_bundle = attach_warning_approvals(bundle, approvals, limits=limits)

validated_build = validate_build_input(
    rows,
    bundle=reviewed_bundle,
    analysis_input_id=source_report.analysis_input_id,
    source_report_id=source_report.id,
    approvals=approvals,
    limits=limits,
    **source_inputs,
)
```

`analyze_sources()` produces a complete source report and its findings; it does
not decide that a warning is acceptable. The application review process creates
any `WarningApproval` records. `attach_warning_approvals()` validates and
stores those explicit scoped decisions without granting permission by itself.
`validate_build_input()` recomputes the current material identities, replays
retained evidence, checks the selected report and approvals, and returns
`ValidatedBuildInput` only when the actual source gate passes. A clean report
uses an empty approval sequence. Errors, incomplete checks, changed material,
or unapproved/mis-scoped warnings do not authorize a build.

## Build immutable native cells {#build-immutable-native-cells}

Pass the gate result as the required `validation=` argument. `AccumulatorEngine`
requires complete native metadata and aggregates plus explicit limits:

```python
from mountainash_rules import AccumulatorEngine

engine = AccumulatorEngine(
    dimension_metadata=source_inputs["metadata"],
    aggregates=source_inputs["aggregates"],
    limits=limits,
)
lattice = engine.build(rows, validation=validated_build)
```

The resulting `Lattice` contains native cells, source UUID identities, and
contributor relationships. Its `combinations` relation is useful for
inspection, but it is not a legacy ranked-results interface. Inspect a native
artifact with `artifact_kind` and its typed `partition_identity`; do not rely
on the removed `is_composed` representation test.

Dimensions with `CONTEXT_KEY` role define the declared partitions. Build every
partition with:

```python
lattices = engine.build_all(rows, validation=validated_build)
```

or build one declared partition with `partition_key=`. A selected key must name
exactly every configured context-key dimension; a key that was not declared by
source validation is rejected. The analysis/build path uses native cells,
source UUIDs, contributor sets, and scoped words rather than prime products,
levels, coalesced columns, or legacy ranking APIs.

## Resolve a bound context {#resolve-a-bound-context}

A build may carry evidence, but a request is authorized only through an active
binding. Application code always names `contract_id` and `profile_id`; it may
also supply `dont_care` only for fields that the selected profile explicitly
allows.

```python
result = engine.apply(
    lattice,
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

`context` is a mapping or Pydantic model. Context admission checks the selected
contract and profile, then resolution checks the provider domain and exact
cells. Direct `apply()` raises `InvalidContextError` for invalid request
material (the exception carries its normalized result) and
`UnresolvedContextError` when a profile rejects insufficient context.

Use `engine.index(lattices)` for repeated context-key routing. It validates a
nonempty coherent sequence of exact lattice views and then serves the same
contract/profile/mask interface:

```python
index = engine.index(lattices)
result = index.apply(
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

For batch work, `index.apply_batch()` requires the same contract/profile and
accepts optional `context_id_field`, `dont_care_field`, and `chunk_size`.
`batch.for_context(context_id)` raises `KeyError` for an unknown ID; it never
creates a synthetic outcome.

## Read an outcome, not a winner {#read-an-outcome-not-a-winner}

`AccumulatorResult` contains one normalized `OutcomeRecord`. It replaces
`best_combination`, ranked candidate frames, `accumulated()`, `provenance`, and
`depths`.

| Accessor | Meaning |
|---|---|
| `status`, `reason` | Defined resolution state and reason |
| `values` | Established outputs only for `status == "decision"` |
| `binding_id`, `contract_id`, `profile_id` | Binding and request labels |
| `cell_id`, `contributor_ids` | Definite identifiers, only when the decision established them |
| `may_have_no_match` | Whether the supplied facts leave an uncovered possibility |
| `observations`, `issues` | Admission observations and invalid-context diagnostics |
| `candidate_cells`, `candidate_contributors` | Possible native cells and contributor edges, never promoted to a decision |
| `lineage` | Definite requested-output lineage |

A candidate-mode profile returns `status == "candidates"` and exposes its
possible cells and contributors. `candidate_lineage(cell_id)` inspects one
candidate cell but does not convert it into definite lineage. `lineage` is
available only for definite contributors and only for the profile's requested
outputs.

Continue with [Chapter 8](../08-lattices-results-and-routing/index.md) for
binding views, native snapshots, and inspection boundaries.
