# Book contents

This book explains how to define, evaluate and combine tabular rules with Mountainash Rules. It begins with the shared rule model, covers each engine's public API, and then examines implementation and extension.

Read the shared foundations first, then follow the part for the engine you need. The implementation chapters explain how those public workflows are built and extended.

## Shared foundations

1. **[Two Rule Engines, One Shared Model](01-two-rule-engines/index.md)** — Column-wise evaluation, rule tables, contexts and DataFrame backends; individual rule matching compared with compatible rule combinations.
2. **[The Shared Rule Model: Tables, Contexts and Dimensions](02-shared-rule-model/index.md)** — Dimension definitions, field mappings, types, roles and validation; metadata in YAML; expression and relation APIs.
3. **[Matching Concepts: Strategies, Unknowns and Wildcards](03-matching-concepts/index.md)** — Matching outcomes, comparison strategies and the treatment of unrestricted conditions and missing values.

## The Expression Rules Engine

4. **[Using the Expression Rules Engine](04-expression-rules-engine/index.md)** — Engine construction, context evaluation, rule survival, specificity and the basic result interface.
5. **[Expression Engine Hit Policies, Results and Explanations](05-expression-results-and-policies/index.md)** — Selecting among matches, inspecting decisions, explaining comparisons, and filtering or reselecting results.
6. **[Expression Engine Batch Evaluation](06-batch-evaluation/index.md)** — Evaluating a table of requests, preserving context identity, reading per-context results, and understanding chunking and backend boundaries.

## The Accumulator Engine

7. **[Using the Accumulator Engine](07-accumulator-engine/index.md)** — Building compatible rule combinations, declaring aggregates and applying contexts through a worked example.
8. **[Accumulator Lattices, Results and Routing](08-lattices-results-and-routing/index.md)** — Inspecting combinations and provenance, routing contexts, and saving or loading lattices.

## Implementation and extension

9. **[Inside the Expression Rules Engine](09-expression-engine-internals/index.md)** — Dimension compilation, context binding, matching, ranking and selection.
10. **[Inside the Accumulator Engine](10-accumulator-engine-internals/index.md)** — Compatibility, coalescing, prime-based combination identities, search and pruning; reuse of expression matching during context application.
11. **[Extending and Maintaining Both Engines](11-extending-and-maintaining/index.md)** — Shared-model changes, engine-specific extensions and verification of observable behavior.

## Appendices

- **[FAQ](../faq.md)** — Answers to common questions about configuration, matching and engine behavior.
- **[Glossary](../glossary.md)** — Definitions of package terminology, with links to fuller explanations.
