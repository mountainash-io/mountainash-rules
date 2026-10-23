# Book contents

This book explains how to define, evaluate and combine tabular rules with Mountainash Rules. It begins with the shared rule model, covers each engine's public API, and then examines implementation and extension.

Read the shared foundations first, then follow the part for the engine you need. The implementation chapters explain how those public workflows are built and extended.

## Shared foundations

1. **[Two Engines, One Rule Model](01-two-rule-engines/index.md)** — Rule tables, contexts and backends; matching a rule at a time compared with a precomputed DecisionTable; the five layers.
2. **[The Shared Rule Model: Tables, Contexts and Dimensions](02-shared-rule-model/index.md)** — `ContextField`, `Dimension`, `DimensionsMetadata`, `Aggregate` and the five flat metadata tables.
3. **[Matching Concepts: Strategies, Unknowns and Wildcards](03-matching-concepts/index.md)** — Ternary outcomes, declared wildcards, null and missing values, PRESENCE and the match strategies.

## The Expression Rules Engine

4. **[Using the Expression Rules Engine](04-expression-rules-engine/index.md)** — Engine construction, context evaluation, survival, specificity, explanations and Boolean inputs.
5. **[Expression Engine Hit Policies, Results and Explanations](05-expression-results-and-policies/index.md)** — Call-time hit policies, violations, re-selection and output fields.
6. **[Expression Engine Batch Evaluation](06-batch-evaluation/index.md)** — Evaluating a table of requests, per-context results, chunking and backends.

## The DecisionTable

7. **[Prevalidating Rules and Building a DecisionTable](07-decision-table-construction/index.md)** — `prevalidate`, findings, `build_decision_table`, entries, folding and provenance.
8. **[Validating Contexts, Matching and Persisting a DecisionTable](08-applying-decision-tables/index.md)** — Context validation, `match`, saving, loading and verifying, and the layer 5 worked example.

## Implementation and extension

9. **[Inside the Expression Rules Engine](09-expression-engine-internals/index.md)** — Dimension compilation, context binding, matching, ranking and selection.
10. **[Inside the DecisionTable](10-decision-table-internals/index.md)** — Column plans, regions, construction stages, contributor masks, derived entry metadata, application and persistence.
11. **[Extending and Maintaining the Package](11-extending-and-maintaining/index.md)** — Public boundaries, the change map, invariants, tests and a review checklist.

## Appendices

- **[FAQ](../faq.md)** — Answers to common questions about configuration, matching and engine behavior.
- **[Glossary](../glossary.md)** — Definitions of package terminology, with links to fuller explanations.
