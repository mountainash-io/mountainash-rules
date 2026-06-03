# Chapters

This textbook is organized into 9 chapters covering the mountainash-rules vectorized, backend-agnostic business rules engine. Chapters progress from foundational concepts through the dimension model, expression rules engine, and accumulator engine, culminating in lattice structures and result handling.

## Chapter List

- [Chapter 1: Foundation Concepts](./01-foundation-concepts/index.md) — Ternary logic, sentinel values, vectorized evaluation, and the core abstractions underpinning the rules engine.
- [Chapter 2: Match Strategies](./02-match-strategies/index.md) — The MatchStrategy enum and all 12 strategy types for comparing dimension values against context.
- [Chapter 3: Dimension Model](./03-dimension-model/index.md) — The Dimension class, DimensionRole enum, DimensionsMetadata collection, field resolution, and validation.
- [Chapter 4: Dimension Compiler](./04-dimension-compiler/index.md) — Translating dimension metadata into backend-agnostic expression templates for rule evaluation.
- [Chapter 5: Expression Rules Engine](./05-expression-rules-engine/index.md) — The ExpressionRulesEngine class and its single-pass vectorized evaluation pipeline.
- [Chapter 6: Expression Engine Results](./06-expression-engine-results/index.md) — The RuleResult class with survivor accessors, specificity filtering, and explainability.
- [Chapter 7: Accumulator Compiler](./07-accumulator-compiler/index.md) — Compatible and coalesce expression compilation for the accumulator lattice builder.
- [Chapter 8: Accumulator Engine](./08-accumulator-engine/index.md) — Prime number encoding, lattice building phases, and the frontier filter algorithm.
- [Chapter 9: Lattice Structures and Results](./09-lattice-structures-and-results/index.md) — The Lattice class, AccumulatorResult, supporting modules, and end-to-end workflows.
