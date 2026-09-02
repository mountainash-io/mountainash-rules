# Chapters

This textbook is organized into 11 chapters covering the mountainash-rules vectorized, backend-agnostic business rules engine. Chapters progress from foundational concepts through the dimension model, expression rules engine, hit-policy selection semantics, and batch evaluation, then into the accumulator engine, culminating in lattice structures and result handling.

## Chapter List

- [Chapter 1: Foundation Concepts](./01-foundation-concepts/index.md) — Ternary logic, sentinel values, vectorized evaluation, and the core abstractions underpinning the rules engine.
- [Chapter 2: Match Strategies](./02-match-strategies/index.md) — The MatchStrategy enum and all 12 strategy types for comparing dimension values against context, including the exact-key partition router and the per-row/context-level regex split.
- [Chapter 3: Dimension Model](./03-dimension-model/index.md) — The Dimension class, DimensionRole enum, DataType enum with temporal sentinels, YAML round-trip, DimensionsMetadata collection, field resolution, and validation.
- [Chapter 4: Dimension Compiler](./04-dimension-compiler/index.md) — Translating dimension metadata into backend-agnostic expression templates for rule evaluation, including the shared in-band set-wildcard sentinel.
- [Chapter 5: Expression Rules Engine](./05-expression-rules-engine/index.md) — The ExpressionRulesEngine class and its single-pass vectorized evaluation pipeline.
- [Chapter 6: Hit Policies](./06-hit-policies/index.md) — The HitPolicy enum, SelectionInfo, cardinality application, and post-hoc policy re-selection over evaluation results.
- [Chapter 7: Expression Engine Results](./07-expression-engine-results/index.md) — The RuleResult class with survivor accessors, specificity filtering, explainability, and the engine-level ExplainResult.
- [Chapter 8: Batch Evaluation](./08-batch-evaluation/index.md) — Scoring many contexts against the rules table in one vectorized pass with evaluate_batch and BatchRuleResult.
- [Chapter 9: Accumulator Compiler](./09-accumulator-compiler/index.md) — Compatible and coalesce expression compilation, including set-membership/set-exclusion support, for the accumulator lattice builder.
- [Chapter 10: Accumulator Engine](./10-accumulator-engine/index.md) — Prime number encoding, lattice building phases, apply-phase caching, and the frontier filter algorithm.
- [Chapter 11: Lattice Structures and Results](./11-lattice-structures-and-results/index.md) — The Lattice class with save/load persistence, AccumulatorResult, the LatticeIndex ternary-partition router, and end-to-end workflows.
