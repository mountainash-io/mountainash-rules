---
title: "Chapter 8: Exact Lattices, Bindings, Snapshots and Routing"
description: "Inspect native artifacts, attach immutable binding views, persist all snapshot records, and route contract-bound requests."
---

# Chapter 8: Exact Lattices, Bindings, Snapshots and Routing

A native `Lattice` is an immutable exact-cell artifact, not a materialized
legacy combination table. It carries source UUID identities, native cells,
contributor relationships, scoped-word layout, and retained validation
evidence. This chapter explains how to inspect, bind, save, load, and route
those artifacts without turning possible evidence into a decision.

The interfaces below are the available E6–E8 migration surface. They do not
assert that E8 is complete or make a release or consumer-completeness claim.

## Identify a native artifact {#identify-a-native-artifact}

Use `lattice.artifact_kind` to distinguish the current native form:

```python
if lattice.artifact_kind == "exact_cells":
    identity = lattice.partition_identity
```

`partition_identity` is typed exact routing material, not a display dictionary
or a substitute for authorization. A native lattice also exposes `artifact_id`,
`contributors`, `bindings`, `metadata`, `aggregates`, `count`, and an
inspection `combinations` relation. Do not use the removed `is_composed` flag,
prime products, depth, coalesced columns, or source-row position as artifact
identity or provenance.

Native cells are produced only through `AccumulatorEngine.build(...,
validation=validated_build)` or `build_all(..., validation=validated_build)`.
The source-gate workflow is covered in [Chapter 7](../07-accumulator-engine/index.md#the-source-gate).

## Immutable binding views {#immutable-binding-views}

A built or loaded exact artifact can receive a separately produced, complete
binding closure:

```python
bound_view = lattice.with_binding(
    binding,
    evidence=binding_evidence,
    limits=limits,
)
```

All three arguments are significant. `binding` is a `ContractBinding` for the
artifact. `binding_evidence` must contain exactly the complete portable
information for that supplied binding. `limits` is an application-owned
`ExactLimits` instance; there is no default. The operation validates the
binding/evidence closure, rejects competing bindings for an active contract,
and returns a new immutable view; a fully validated identical binding returns the existing view.

The original `lattice` remains unchanged. Loading evidence or merely possessing
a binding record does not grant permission. A request can use a contract/profile
only when the selected exact view has an active binding that authorizes that
profile.

```python
result = engine.apply(
    bound_view,
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

The mask is not a wildcard policy. It is a request mask and is accepted only
where the selected profile permits it.

## Native snapshot contract {#native-snapshot-contract}

Save and load are bounded, self-contained native operations:

```python
from mountainash_rules import Lattice

snapshot_path = bound_view.save(directory, limits=limits)
loaded = Lattice.load(snapshot_path, limits=limits)
```

A native snapshot has exactly 11 files:

1. `manifest.yaml`
2. `lattice.parquet`
3. `sources.parquet`
4. `contributors.parquet`
5. `scopes.parquet`
6. `scope_keys.parquet`
7. `source_maps.parquet`
8. `vectors.parquet`
9. `words.parquet`
10. `predicates.json`
11. `validation.json`

The manifest describes the native artifact and physical file schema. The Parquet
relations retain cells, source UUIDs, contributor edges, scopes, scoped-word
layout, and vectors. The JSON files retain the predicate and validation closure.
Loading validates and reconstructs that state; it does not re-run a build from
arbitrary source rows.

A loaded artifact is served by a compatible engine, not by an inferred default:

```python
compatible_engine = AccumulatorEngine(
    loaded.metadata,
    loaded.aggregates,
    limits=limits,
)
result = compatible_engine.apply(
    loaded,
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

Compatibility requires dimensions and aggregate declarations that agree with
the compiled artifact. The caller retains responsibility for choosing the
limits for this operation and for producer, actor, storage, and deployment
trust.

## Routed exact views {#routed-exact-views}

`CONTEXT_KEY` dimensions create the partition registry during source analysis.
Build all declared partitions, then construct one reusable index for repeated
requests:

```python
lattices = engine.build_all(rows, validation=validated_build)
index = engine.index(lattices)

result = index.apply(
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

The index accepts a nonempty sequence of coherent exact views. It routes by the
typed partition identities, then resolves through the selected binding and
profile. A missing route raises `KeyError`; an admissible best-specificity tie
raises `AmbiguousPartitionError`. These are routing failures, not fallback
successes.

For batches:

```python
batch = index.apply_batch(
    contexts,
    contract_id=contract_id,
    profile_id=profile_id,
    context_id_field="request_id",
    dont_care_field="request_mask",
    chunk_size=chunk_size,
)
```

All batch rows use the same named contract/profile. `batch.for_context` returns
the single result for a submitted ID, or raises its corresponding
`InvalidContextError`/`UnresolvedContextError`. Unknown IDs raise `KeyError`.
Candidate evidence is never promoted to a successful decision.

## Certainty, candidates, and lineage {#certainty-candidates-and-lineage}

A resolved `AccumulatorResult` should be read by status, not as a winner among
ranked rows:

- `decision` means the requested output values are established under the chosen
  binding/profile. `values` is then present; `cell_id` and `contributor_ids`
  appear only when the profile's provenance requirement is established.
- `candidates` is an explicit candidate-profile response. Inspect
  `candidate_cells` and `candidate_contributors`; those relations are possible
  evidence, not a decision.
- `unresolved` or `no_match` supplies no decision values. `may_have_no_match`
  records whether the supplied facts leave an uncovered possibility.
- `invalid_context` retains `issues` and observations. A resolve profile that
  elects to reject insufficient context surfaces its typed request error.

`lineage` is definite requested-output lineage. For a candidate cell, use
`candidate_lineage(cell_id)` and keep its status as candidate evidence. This is
why legacy `best_combination`, `accumulated()`, `provenance`, `depths`, and
rank-based result guidance do not describe this API.

## Flat data is inspection-only {#flat-data-is-inspection-only}

`Lattice.load()` can recognize an older flat snapshot shape. Such a lattice may
expose rows, metadata, aggregates, count, and partition-key information for
inspection. It is not an exact artifact: `artifact_kind` is not `"exact_cells"`,
and exact-cell operations require native state.

Do not serve flat/imported data through exact `apply`, `index`, or
`with_binding`, and do not treat it as build output. To serve an exact decision,
use a lattice from the analyzed-and-gated native build/load path.

## Phase5 migration handoff {#phase5-migration-handoff}

This documentation change is limited to the Rules live guidance. The remaining
external migration work belongs to its owners: Babel's base `LatticeView`, CSV
and DMN exporters/importers and manifest dispatch; and the service registry,
model, route, configuration, and fixtures. They must migrate their own
bound-artifact handling rather than infer legacy lattice state. They are not
modified here, and this chapter does not claim their completion.

Return to [Chapter 7](../07-accumulator-engine/index.md#read-an-outcome-not-a-winner)
for the source gate and result contract.
