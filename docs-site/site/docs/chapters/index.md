# Chapters

The manual moves from understanding rule data to using both engines, then to explaining and extending their implementation. Each chapter answers a reader task; closely related APIs are taught together rather than separated by source-module boundaries.

## Understand and use the package

1. **[From Rule Tables to Decisions](01-rule-tables-and-decisions/index.md)** — Establish the mental model: rule rows, contexts, ternary outcomes, sentinels and portable execution abstractions.
2. **[Authoring and Evolving Rule Libraries](02-authoring-rule-libraries/index.md)** — Give columns meaning, choose matching strategies, validate dimensions and serialize metadata. Work through the important type and wildcard boundaries.
3. **[Evaluating, Selecting and Explaining Decisions](03-evaluating-decisions/index.md)** — Follow one context through engine construction, matching, ranking, hit-policy selection, result access and the two explanation interfaces.
4. **[Scoring Batches of Contexts](04-scoring-batches/index.md)** — Prepare many contexts, understand the evaluation work, preserve per-context ranking and inspect chunked or individual results.
5. **[Combining, Persisting and Routing Rules](05-combining-and-persisting-rules/index.md)** — Build a lattice, apply contexts, inspect aggregates and provenance, use partitions and routing, and save or reload the artifact.

## Understand and change the implementation

6. **[Inside Expression and Batch Evaluation](06-expression-execution-internals/index.md)** — Trace metadata into expressions, context-column binding, strategy compilation and selection mechanics. Connect implementation details to the behavior already introduced.
7. **[Inside Combination Search](07-combination-search-internals/index.md)** — Study compatibility and coalescing, prime-encoded identities, lattice expansion and pruning. Distinguish construction limits and the invariants behind their remedies.
8. **[Extending and Maintaining the Engines](08-extending-and-maintaining/index.md)** — Apply the shared architecture in concrete strategy, aggregate and hit-policy extension recipes. Preserve observable behavior and the documented backend boundary.

These are different depths within one manual, not separate copies of the same material for different audiences. Later recipes and implementation explanations link to the earlier public contracts they preserve.

## Appendices

- **[FAQ](../faq.md)** — Focused answers with links to the relevant chapter explanations.
- **[Glossary](../glossary.md)** — Terminology used throughout the manual, with links for deeper reading.
