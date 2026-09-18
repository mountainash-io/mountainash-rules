---
title: "Chapter 11: Extending and Maintaining Both Engines"
description: "Preserve the public boundary, filter contracts, and the exact accumulator's source-to-restoration lifecycle."
---

# Chapter 11: Extending and Maintaining Both Engines

Mountainash Rules has two deliberately different execution models. The Expression Rules Engine remains a ternary matching and selection engine. The Accumulator Engine is an exact-artifact compiler and contract-bound resolver. A shared `Dimension` or `Aggregate` declaration does not imply a shared implementation path.

This chapter is for maintainers. It describes the current boundaries and the evidence an extension must preserve; it does not promise that an unfinished analysis, warning review, build, snapshot, or request will succeed.

## Keep the public boundary small

Applications import public types and functions from `mountainash_rules`. That root exports the engines, exact models (`ExactLimits`, `ValidatedBuildInput`, `ValidationBundle`, `WarningApproval`, contracts and profiles), source-analysis functions, lattices, results, aggregate declarations, and public error classes.

Do not make private module paths a second API. In particular, `_native`, source normalization, predicate compilation, narrow-layout relations, state materialization, persistence internals, and codec helpers are implementation boundaries. The native bridge accepts bounded data and exposes typed native failures; it is not an application extension surface.

A public API addition needs all of the following considered together:

1. root export and `__all__` membership where callers need it;
2. immutable model validation and canonical identifiers for persisted or evidence-bearing data;
3. explicit limits and typed errors at every operation that can allocate, decode, or reason; and
4. documentation that names the required arguments without inventing defaults or success outcomes.

Renaming serialized IDs, enum values, output names, or canonical fields is a compatibility change, not a cosmetic edit.

## Shared filter-engine maintenance

A change limited to filter matching stays in the filter path. Define its `Dimension` validation, ternary truth table, context representation, sentinel behavior, selection-field classification, scalar evaluation behavior, and batch behavior. Test observable outcomes—match, unknown, contradiction, invalid configuration, and per-context selection—rather than compiler wiring.

Do not add accumulator behavior merely because a filter strategy exists. The exact accumulator consumes normalized predicates rather than filter survivors. A strategy becomes accumulator-capable only after it has a precise authored-source interpretation, a normalized predicate representation, and proof-preserving lifecycle support described below.

Keep the two models distinct in documentation as well: ternary specificity and hit-policy ranking belong to the filter engine; exact cell IDs, contributor UUIDs, contracts, profiles, and outcomes belong to the accumulator.

## The exact accumulator change map

| Change | Required exact work | Evidence that must remain valid |
|---|---|---|
| Source field or dimension meaning | Normalize and validate source values; define predicates, domains, routing, and source origins. | Source report, findings, scoped policy checks, and replayed witnesses. |
| Predicate language or domain feature | Update predicate construction, reasoning, canonical serialization, and native codec support. | Cell non-emptiness, disjointness, source union/membership, profile consistency, and restoration replay. |
| Aggregate or output type | Supply a complete native declaration and exact `numeric-1` fold/storage behavior. | Output-fold proof, numeric-bit accounting, lineage, snapshot save/load, and runtime decoding. |
| Scope or segmentation feature | Preserve source-UUID mapping and scoped narrow-word allocation. | Word, scope, source-scope, contributor-edge, and live-memory limits. |
| Contract/profile behavior | Preserve contract bindings, profile authorization, context admission, and typed outcomes. | Bound-source and compiled evidence; `contract_id`/`profile_id` resolution. |
| Persistence format | Update canonical records and native relation restoration together. | Strict load validation and revalidation of semantic evidence. |

The table is intentionally end-to-end. Updating only a row representation, a relation schema, or a resolver branch leaves an exact artifact incomplete.

## Source gate before compilation

The source gate is part of the accumulator's semantics, not optional application ceremony.

1. Call `analyze_sources(...)` with the current rows, metadata, aggregates, source identity fields, domain and predicate material, routing, contracts, validation policy, and `ExactLimits`. Its `ValidationBundle` records source diagnostics.
2. Review only actual warning findings. If a warning is accepted, create a `WarningApproval` with the selected analysis input, source report, warning IDs, and a scope that covers those findings. Attach it with `attach_warning_approvals(..., limits=limits)`.
3. Call `validate_build_input(...)` with the same current material and selected IDs. It rechecks material identity and policy, replays retained witnesses, checks approval links and scope, and invokes the source permission gate.
4. Pass the returned `ValidatedBuildInput` as `validation=` to `AccumulatorEngine.build()` or `build_all()`.

This sequence separates a source report from build permission. Never manufacture approvals, widen an approval's scope, retain a report after its source material has changed, or treat source diagnostics as compiled-cell proof.

## Building exact cells

`AccumulatorEngine` requires `dimension_metadata` and `limits`; aggregate declarations and segmentation dimensions are explicit. `boolean_coercion` must be `BooleanCoercion.NONE`.

`build(rules, *, validation, partition_key=None)` produces the declared partition only after validation. Where context-key dimensions exist, `partition_key` must name every one and match a source-validated partition. `build_all(rules, *, validation)` prepares once and produces the declared partitions. Both paths create immutable exact artifacts; neither accepts a source report or raw approval list in place of `ValidatedBuildInput`.

A maintainer changing construction must preserve these invariants:

- every admitted source has a stable source UUID and normalized predicate;
- cells are nonempty and disjoint in the declared scope;
- contributor sets are represented by source UUIDs and scoped dense 63-bit words, not global ordinal or arithmetic identities;
- aggregate outputs are exact-native `numeric-1` folds, with declared types and bounded numeric workspace;
- every allocation and proof step reserves through the operation budget before retaining state; and
- compiled evidence is produced only after the complete set of compiled checks succeeds.

`ExactLimits` is the compatibility boundary for capacity. Add a new retained structure only with a corresponding counter or an explicit, conservative accounting rule. Do not bypass a limit with a lazy relation, a Python collection, a native buffer, or a fallback result.

## Runtime, bindings, and outcomes

Resolution requires an exact lattice whose dimensions and aggregate declarations agree with the engine:

```python
engine.apply(
    lattice,
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=None,
)
```

`contract_id` and `profile_id` are required named arguments. `dont_care` remains optional. The resolver admits context values against the selected contract/profile, uses their binding and domain permissions, and returns an `AccumulatorResult` carrying one typed outcome. It must preserve invalid and unresolved outcomes as typed errors when `raise_for_status()` is requested; it must not turn them into a default cell or an empty success.

For partition routing, maintain the coherent-lattice checks in `LatticeIndex`. Exact-key routing, fallback routing, and tie detection are distinct states. `AmbiguousPartitionError` and a missing route are meaningful errors, not conditions to resolve by arbitrary input order.

Candidate inspection must remain non-promoting: `candidate_cells`, `candidate_contributors`, and `candidate_lineage()` describe possible cells; only definite outcome contributors support result lineage.

## Numeric-1 aggregate maintenance

`Aggregate` declarations are immutable. Exact declarations require `output_name`, `data_type`, and `numeric_semantics="numeric-1"` together; output names are namespaced. `SUM` and `PRODUCT` are numeric only. A datetime aggregate requires an explicit `naive` or `utc` timezone.

When adding a fold or a storage type, define all of these before writing implementation code:

- accepted scalar domain and normalization;
- deterministic fold semantics and float rounding, if applicable;
- output encoding, decoding, lineage projection, and native storage type;
- numeric-bit and live-workspace reservation; and
- error behavior for unsupported, non-finite, malformed, or over-limit input.

A backend reduction, a host-language numeric fallback, or a silently rounded value is not an implementation of an exact fold.

## Strict native restoration

`Lattice.save(dir_path, *, limits)`, `Lattice.load(dir_path, *, limits)`, and `lattice.with_binding(binding, *, evidence, limits)` all require explicit limits. Save publishes the artifact's typed native relations and evidence. Load restores an executable lattice only by validating the manifest, restoring typed relations, checking source/cell/layout integrity, and replaying the evidence required for exact state. `with_binding` validates complete portable evidence and returns a new immutable view.

A `Lattice` created from flat data may be inspected, but it is legacy/flat data and is not executable. It must continue to fail exact-only operations such as application, binding, routing, and lineage rather than acquiring permissive behavior during load or migration work.

Changes to snapshot layout require an actual lifecycle proof: construct a validated artifact, save it with limits, load it with limits, bind complete evidence if the scenario requires it, and resolve a normalized context with explicit `contract_id` and `profile_id`. Inspect the outcome, cell identity, contributors, and outputs appropriate to that scenario. Do not replace this with a schema-only assertion or a claim about an unexercised backend.

## Review checklist

Before accepting an exact-accumulator extension, review the source-to-runtime chain:

- Is the authored source representation validated before any compilation?
- Does source analysis emit diagnostics independently from the build gate?
- Are each approval and binding tied to actual IDs and a covering scope?
- Are cells proved nonempty and disjoint, and is contributor provenance source-UUID based?
- Are all resources bounded, including codec buffers, native state, words, numeric workspace, and proof work?
- Does the snapshot restore the native artifact strictly and reject malformed or flat data for execution?
- Does runtime require the declared contract/profile identifiers and preserve error outcomes?
- Are all supported imports rooted at `mountainash_rules`?

These obligations complement, rather than replace, the filter engine's ternary and selection contracts. Keep each engine's documentation and tests focused on its own observable behavior.