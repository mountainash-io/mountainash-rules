---
title: "Chapter 11: Extending and Maintaining Both Engines"
description: "Add a comparison, accumulator semantics, selection policy, or aggregate without losing the shared contracts that make both engines predictable."
---

# Chapter 11: Extending and Maintaining Both Engines

Extending Mountainash Rules means coordinating metadata, matching and the engine operations that consume them. A match strategy becomes a serialized metadata value, a ternary expression, and possibly a way to combine two rule conditions. A hit policy governs ordering and selection for both a single context and every context in a batch. An aggregate operation becomes a persisted lattice declaration and a fold performed while combinations expand.

This chapter is a change map for contributors and maintainers. It assumes the shared model from [Chapter 2](../02-shared-rule-model/index.md#the-dimension-class), the ternary and wildcard meanings from [Chapter 3](../03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match), and the public engine workflows in [Chapters 4–8](../04-expression-rules-engine/index.md#construct-an-engine-and-evaluate-a-context). It links to the implementation chapters rather than re-teaching their pipelines: [dimension compilation](../09-expression-engine-internals/index.md#dimensioncompiler), [selection](../09-expression-engine-internals/index.md#selectioninfo-dataclass), [coalescing](../10-accumulator-engine-internals/index.md#accumulatorcompiler), and [lattice expansion](../10-accumulator-engine-internals/index.md#level-expansion).

The runnable example observes the existing contracts. The recipes then identify the source changes, serialization consequences and behavioral checks required for each kind of extension.

## The change map

The two engines share the rule-table and dimension contracts, but they do not share every implementation path.

| Change | Shared contract | Engine-specific work | Persisted form |
|---|---|---|---|
| Filter match strategy | `MatchStrategy`, `Dimension` validation, ternary compiler, inferred output fields | Filter scoring is automatic after compilation; batch uses the same compiled expressions | `DimensionsMetadata` YAML |
| Accumulator-compatible strategy | Everything for a filter strategy | Compatibility, coalescing, NA flags, build schema and remapped apply metadata | Same metadata, plus lattice manifest metadata |
| Hit policy | `HitPolicy`, selection information and result reselection | Batch assertions and one-per-context cardinality are a separate implementation | `DimensionsMetadata` YAML |
| Aggregate operation | `AggregateOp` and `Aggregate` parsing | Singleton seed and expansion fold | Each lattice manifest's `aggregates` entries |

`mountainash_rules.__init__` already exports the enum classes, `Aggregate`, and both engines. Adding a member to an existing exported enum does **not** require a second package-root export. Renaming an existing string value is different: it breaks metadata or snapshot manifests that contain it.

## Current contracts in action

A metadata string round-trips through `DimensionsMetadata`; `FIRST` chooses the rank-one expression result; and `PRODUCT` folds two compatible accumulator rules. This observes existing behavior only.

```python
import polars as pl
from mountainash.relations import relation
from mountainash_rules import (
    Aggregate,
    AggregateOp,
    AccumulatorEngine,
    DataType,
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    HitPolicy,
    MatchStrategy,
)

metadata = DimensionsMetadata(dimensions=[
    Dimension(dimension_name="region", data_type=DataType.STR),
])
print(MatchStrategy("exact") is MatchStrategy.EXACT)
print(DimensionsMetadata.from_yaml(metadata.to_yaml()) == metadata)

rules = pl.DataFrame({
    "rule_name": ["specific", "fallback"],
    "region": ["AU", "<NA>"],
    "amount": [10.0, 4.0],
})
expression = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
selected = relation(
    expression.evaluate({"region": "AU"}, hit_policy=HitPolicy.FIRST).survivors
).to_polars()
print(selected.select("rule_name", "__rank").to_dicts())

accumulator = AccumulatorEngine(
    dimension_metadata=metadata,
    aggregates=[Aggregate(column_name="amount", operation=AggregateOp.PRODUCT)],
)
lattice = accumulator.build(rules)
rows = relation(lattice.combinations).to_polars()
print(rows.filter(pl.col("__prime_product") == 6).select("__agg_amount").to_dicts())
```

```text
True
True
[{'rule_name': 'specific', '__rank': 1}]
[{'__agg_amount': 40.0}]
```

The product row has prime product `2 × 3 = 6`, so it represents the two-rule combination and contains `10.0 × 4.0`. The wildcard rule remains compatible with the concrete `AU` rule. In the separate expression evaluation, `FIRST` selects `specific` because it is the earliest surviving input row. Specificity does not determine `FIRST` ordering; putting the fallback first would select the fallback.

<!-- concept:131 -->
## Backend purity enforcement {#backend-purity-enforcement}

Normal package code describes calculations with `mountainash.expressions` and carries out table operations through `mountainash.relations`. That is the implementation boundary introduced in [Chapter 1](../01-two-rule-engines/index.md#using-other-dataframe-libraries). It keeps shared matching and selection logic from depending directly on a DataFrame library.

The dependency direction is equally important:

```text
engines/accumulator  →  engines/filter  →  core
```

The accumulator creates an `ExpressionRulesEngine` for apply-phase matching; `core` must not import either engine, and the filter engine must not import the accumulator. The purity test does not check this direction, so review it separately when placing code.

`tests/test_backend_purity.py` parametrizes every non-dunder Python file under `src/mountainash_rules`. For each line beginning with `import` or `from`, it rejects a target beginning with `polars`, `ibis`, or `narwhals`, unless that **same line** has a `# allow: <reason>` tag. At the pinned source revision there are exactly three such tagged exceptions:

| Source location | Native operation | Reason recorded beside the import |
|---|---|---|
| `core/compiler.py`, `_compile_regex_per_row` | Polars expression for a rule-column regex pattern | Mountainash has no column-pattern `regex_contains` yet. |
| `engines/accumulator/engine.py`, module import | Empty-build schema seed | Mountainash has no backend-agnostic empty-frame support yet. |
| `engines/accumulator/lattice.py`, `Lattice.load` | `pl.read_parquet` snapshot read | Mountainash has no backend-agnostic file I/O for that Parquet read yet. |

The tag satisfies a syntactic exception, not a portability claim. A new native import requires a concrete missing abstraction, a same-line reason, and an operation-level test on the affected backend. Prefer adding the needed Mountainash expression or relation capability instead.

Static and semantic checks answer different questions:

- `hatch run test:test-target-quick tests/test_backend_purity.py` checks prohibited import spelling and allowed tags. It cannot detect dynamic imports, backend-specific behavior without an import, a wrong native operation, or a reversed internal dependency.
- A compiler or engine test checks ternary values, survival, rank, errors, and a backend's operation support. `ALL_BACKENDS` in `tests/conftest.py` has seven configured entries, but current tests intentionally record capability limits: list strategies are limited to the listed list-capable backends, and Ibis–Polars cannot perform the engine row-index operation.

The commands are defined by the current `hatch.toml`: `test-target-quick` expands to `pytest {args}` and does not add coverage. Use it after implementing a focused change, not as evidence that every backend supports a new expression.

## Recipe: add a filter match strategy

Start by deciding the rule-side shape and the ternary truth table. The compiler must return `1` for a concrete match, `0` for an unknown comparison, and `-1` for a contradiction. A strategy that cannot specify its treatment of the rule wildcard, an absent or `NOT_SET` context value, and each supported data type is not ready to add. The sentinel rules are developed in [Chapter 3](../03-matching-concepts/index.md#sentinel-values-telling-no-constraint-apart-from-no-answer).

For a filter-only strategy, make these changes together:

1. **Public vocabulary and serialization.** Add a lowercase `StrEnum` member to `MatchStrategy` in `core/constants.py`. `Dimension.match_strategy` is that enum, and `DimensionsMetadata.to_yaml()` serializes Pydantic's JSON-mode value. Add a YAML round-trip assertion in `tests/core/test_dimension_serialization.py`. Old code will not parse metadata containing the new value; therefore preserve every existing value unchanged.
2. **Metadata validation.** Extend `Dimension._validate_strategy_fields` in `core/dimension.py`. Put required strategy fields, forbidden fields, and data-type restrictions in the validator that constructs a `Dimension`; do not wait for a backend failure. The current range, string/regex, threshold, and set branches are the models. Add valid and invalid cases to `tests/core/test_dimension.py`, including every special field your strategy consumes.
3. **One ternary compiler dispatch.** Add a case in `DimensionCompiler.compile_dimension` and a `_compile_<strategy>` method in `core/compiler.py`. Build it with `mountainash.expressions`, reading `dim.resolved_rule_field` and `__ctx_<dimension_name>`. Reuse the typed sentinel helpers and the Boolean-null path where appropriate. Do not add strategy branches to survival, specificity, rank, `explain()`, or batch scoring: those consume the common ternary columns.
4. **Output-schema classification.** Inspect `selection_info_from_metadata` in `core/hit_policy.py`. Its range branch lists both bound columns as condition fields before `ANY` infers output columns. A new multi-column condition needs the equivalent classification; otherwise a condition column can be treated as a business output and make `ANY` report a false disagreement. A one-column strategy follows the existing non-`CONTEXT_REGEX` branch.
5. **Consumers and behavioral evidence.** `ExpressionRulesEngine` compiles metadata at construction and both `evaluate()` and `evaluate_batch()` reuse those expressions. `core/context.py` deliberately extracts context values by resolved field and declared `DataType`, not by strategy; leave it unchanged for a strategy that consumes the established scalar context contract. If the proposed contract genuinely needs a different context shape, extend `extract_context_values` and its `tests/core/test_context.py` cases as part of that design. Add direct ternary tests in `tests/core/test_compiler.py` for concrete match, contradiction, rule wildcard, and non-concrete context. Then exercise the engine through one single context and one batch context; batch agreement must include rule names, specificity, and rank, following `TestEvaluateBatchAgreement` in `tests/filter/test_batch_evaluation.py`.
6. **Backend scope.** Parametrize the compiler behavior over the supported backend set only where the Mountainash operation is available. A clean import scan cannot establish expression support. If the expression uses a list or column-valued string predicate, mirror the strategy-specific capability treatment in `tests/core/test_compiler.py` and document the resulting limit in the README strategy/backend section. A new set-shaped strategy must also explicitly reuse or extend `core/set_wildcard.py` plus the set-dimension registration in `ExpressionRulesEngine._validate_set_rules_once`; this preserves the reserved `[sentinel]` wildcard and rejects an embedded sentinel before matching.

A strategy can be complete for context matching without being accumulator-coalescible. Current examples include `NOT_EQUAL`, `EXACT_KEY`, string predicates, per-row `REGEX`, and `CONTEXT_REGEX`; do not manufacture pairwise combination semantics for them. That does not mean they have no accumulator consumers: `LatticeIndex` uses `EXACT_KEY` in its embedded routing engine. A change to that strategy must also exercise exact partition hits, wildcard fallback, missing keys and ambiguous routing.

## Recipe: make a strategy accumulator-compatible

Accumulator support is additional semantics, not an enum flag. It applies only to `CONSTRAINT` dimensions: `CONTEXT_KEY` dimensions partition rules and do not enter the compatibility compiler. First complete the filter recipe, because `apply()` rebuilds remapped dimension metadata and delegates context matching to the filter engine.

For every pair of compatible conditions, define a combined condition that means their intersection. The combined condition must be independent of operand order and of a valid regrouping. For example, membership lists intersect, exclusion lists unite, `GREATER_THAN` keeps the greater threshold, and `LESS_THAN` keeps the lesser threshold. A wildcard combines with a concrete condition to yield the concrete condition; two wildcards remain a wildcard.

Then change these actual extension points:

1. **Pairwise compiler.** Add the strategy to both `AccumulatorCompiler.compile_compatible` and `compile_coalesce` in `engines/accumulator/compiler.py`. Compatibility reads `co_<field>` from the combination so far and `<field>_rhs` from the candidate rule. Coalescing returns a list because a strategy may have more than one physical output column.
2. **Unknown-state representation.** Update `compile_coalesce_na_flag` when the strategy has multiple sentinel signals or a non-scalar wildcard. The generic scalar fallback is correct only for one ordinary rule field. A set-like strategy also needs its wildcard reservation, normalization, and no-null-element path represented in `_set_dims`, `_normalize_set_columns`, and `_create_anchor` in `engines/accumulator/engine.py`.
3. **Build and frontier schema.** Seed every new physical coalesced column in `_create_anchor`. Level retention follows `_columns_to_keep(current_level)`, so a column absent from the anchor schema can be lost during expansion. Extend `_co_fields` and `_na_flag_fields` to describe the full frontier fingerprint; omitting a field can make different constraints appear equivalent.
4. **Apply remapping.** Extend `_build_apply_metadata` so the temporary filter engine reads every `co_` field and retains every strategy-specific option. The generic scalar path is sufficient only for a strategy that needs no additional metadata; a scalar strategy with special fields must copy them into the reconstructed `Dimension` as well. `apply()` deliberately uses `HitPolicy.COLLECT`; a table-level selection policy is not carried into accumulator results.
5. **Evidence at two levels.** Add compiler cases in `tests/accumulator/test_compiler.py`: compatible and incompatible concrete pairs, left/right wildcard, both wildcards, coalesced value, and NA flag. Add a build-level example in `tests/accumulator/test_engine.py` or `test_correctness.py` proving that compatible rules form the intended combined constraint and incompatible rules never form one. Permute the input rows and compare effective combined constraints, not prime values or incidental row order.

The accumulator's rule-side wildcard is `UNKNOWN`, not the context-only `NOT_SET` numeric sentinel. The compiler documents this distinction because build input represents declared conditions, while context binding represents missing facts. Preserve it in every new compatibility predicate.

## Recipe: add a hit policy

A hit policy has three independent meanings: order, assertion, and cardinality. State all three before adding an enum member. For example, a policy might share default specificity ordering, assert that a full survivor set has a property, and keep all rows. Another might select one rank-one row. A policy must keep `__rule_index` as the final ascending tie-breaker so equal candidates still have a total order.

1. **Vocabulary and configuration.** Add the lowercase `HitPolicy` member in `core/constants.py`. If it needs a new metadata field, add it to `DimensionsMetadata`, validate it in `_validate_hit_policy`, and carry it from `selection_info_from_metadata` through `SelectionInfo`. Metadata YAML then serializes it with the existing model. Do not add a field when the policy can be expressed with the existing priority and output-field information.
2. **Single-context selection.** Update only the relevant functions in `core/hit_policy.py`: `ordering_keys`, `check_policy_config`, `check_priority`, `check_assertions`, `apply_cardinality`, and `selection_is_truncated`. `ExpressionRulesEngine._evaluate` and `RuleResult.select()` both call these helpers. A policy that discards candidates must mark a result truncated, so `select()` cannot pretend to reapply another policy to an incomplete candidate set.
3. **Batch selection.** Update `ExpressionRulesEngine._evaluate_batch_frame` in `engines/filter/engine.py` separately. It uses `ordering_keys`, but `_assert_batch_policy` performs assertions per `__context_id` and the final one-per-context branch performs cardinality. Assertions occur before `min_specificity` and `top_n_per_context`; preserve that ordering so a caller cannot hide a violation by trimming rows.
4. **Tests that compare the two paths.** Extend `tests/core/test_hit_policy.py` for ordering, empty and multi-survivor behavior, configuration failure, `RuleResult.select()`, and metadata YAML. Extend `TestBatchHitPolicies` in `tests/filter/test_batch_evaluation.py` with one context that passes and one that violates. The observable contract is the same policy applied per context, even though the batch code is not a call to the single-context assertion helper.

Accumulator `apply()` has no hit-policy parameter and constructs remapped metadata with `COLLECT`; adding an expression hit policy does not create an accumulator-selection API. That boundary is intentional: an accumulator result represents matching combinations, and callers can interpret or filter them through its established result contract.

## Recipe: add an aggregate operation

`AggregateOp` is deliberately a small set of commutative, associative folds: `sum`, `min`, `max`, and `product`. The expansion engine starts each singleton with the raw aggregate column, then folds in each right-hand rule while expanding a combination. An operation that needs extra state, such as an average needing a count as well as a total, does not fit this one-column model without a larger data-model design.

1. **Enum and validation.** Add the new lowercase value to `AggregateOp` in `engines/accumulator/aggregate.py`. `Aggregate.operation` is typed as that Pydantic enum; update `tests/accumulator/test_aggregate.py` to show that the value parses and an unknown value still raises `ValidationError`.
2. **Seed and fold.** Confirm that a singleton should start with the raw source value in `_create_anchor` in `engines/accumulator/engine.py`. Then add the fold case to `_expand_level`, using `mountainash.expressions` over `__agg_<column_name>` and `<column_name>_rhs`. If the operation needs a different seed, that is an explicit change to the aggregate data model and anchor representation, not a hidden special case in the fold.
3. **Snapshot compatibility.** `Lattice.save()` writes `Aggregate.model_dump(mode="json")` entries and `Lattice.load()` validates each with `Aggregate.model_validate`. The new code can load an old manifest if old enum meanings remain intact; an older package cannot load a manifest that names the new operation. Add a save/load behavioral test when the new operation is intended for persisted lattices.
4. **Meaningful numerical evidence.** Add a two-rule compatible combination with a hand-computable expected `__agg_<column>` in `tests/accumulator/test_engine.py`, alongside the existing min/max/product cases. Check both operand orders and a three-value regrouping where the operation's domain permits it. Aggregate-value overflow remains backend-defined; only the prime-product identity has the package's explicit `int64` overflow guard described in [Chapter 10](../10-accumulator-engine-internals/index.md#latticewidthexceedederror).

## Review the complete contract

The focused commands below come from the current Hatch configuration. They are examples of narrow evidence after implementing the corresponding change; select the affected files rather than treating a broad suite as a substitute for the stated invariant.

| Change | Focused command | What it establishes |
|---|---|---|
| New strategy semantics | `hatch run test:test-target-quick tests/core/test_compiler.py tests/core/test_dimension.py tests/core/test_dimension_serialization.py` | Ternaries, invalid metadata, and serialization for the strategy. |
| Accumulator support | `hatch run test:test-target-quick tests/accumulator/test_compiler.py tests/accumulator/test_engine.py` | Compatibility, coalescing, NA flags, and a real lattice result. |
| New hit policy | `hatch run test:test-target-quick tests/core/test_hit_policy.py tests/filter/test_batch_evaluation.py` | Single-context reselection and per-context batch behavior. |
| Backend boundary | `hatch run test:test-target-quick tests/test_backend_purity.py` | Direct-import rule only; pair it with the affected semantic test above. |

Keep the public description accurate as part of the change. The current README has the match-strategy, hit-policy, accumulator, serialization, and backend-capability sections; update the relevant one when the extension changes what a user can declare or which operation a backend supports. For a contributor workflow, `CONTRIBUTING.md` asks feature, chore, and bugfix pull requests to target `develop` and calls for review; it does not replace the package-specific evidence above.

## Sources and implementation notes

This chapter describes Rules revision `730a8583ee9d4fd6b52dc5350699eb66cc7487e9`. The references identify the implementation and behavioral checks relevant to each extension.

- [Constants and serialized enum values][constants-source]
- [Dimension validation and YAML serialization][dimension-source]
- [Ternary strategy compiler][compiler-source]
- [Policy selection helpers][policy-source]
- [Filter single and batch evaluation][filter-source]
- [Accumulator compatibility compiler][accumulator-compiler-source] and [build/apply engine](https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/engines/accumulator/engine.py)
- [Aggregate model][aggregate-source]
- [Lattice persistence][lattice-source]
- [Backend-purity enforcement test][purity-test-source]
- [Current Hatch test commands][hatch-source]

[constants-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/core/constants.py
[dimension-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/core/dimension.py
[compiler-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/core/compiler.py
[policy-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/core/hit_policy.py
[filter-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/engines/filter/engine.py
[accumulator-compiler-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/engines/accumulator/compiler.py
[aggregate-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/engines/accumulator/aggregate.py
[lattice-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/src/mountainash_rules/engines/accumulator/lattice.py
[purity-test-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/tests/test_backend_purity.py
[hatch-source]: https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/hatch.toml
