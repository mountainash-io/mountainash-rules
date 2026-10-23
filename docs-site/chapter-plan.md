# Book plan

The book in `docs-site/site/docs/` is a practitioner and maintainer manual for the current `mountainash_rules` API. It has eleven chapters in four parts and two appendices. The user quickstart (`docs/user-quickstart.md`), `docs/backend-support.md` and `AGENTS.md` describe the same API in the repository; keep the four consistent.

| Part | Chapter | Directory | Teaches |
|---|---|---|---|
| Shared foundations | 1 | `01-two-rule-engines` | Rule tables, contexts, the two engines, the five DecisionTable layers, backends |
| | 2 | `02-shared-rule-model` | `ContextField`, `Dimension`, `DimensionsMetadata`, `Aggregate`, five flat metadata tables |
| | 3 | `03-matching-concepts` | Ternary logic, declared wildcards, null and missing values, PRESENCE, OUTCOME, strategies |
| Expression Rules Engine | 4 | `04-expression-rules-engine` | Construction, evaluation, results, explain, Boolean inputs |
| | 5 | `05-expression-results-and-policies` | Call-time hit policies, violations, re-selection, output fields |
| | 6 | `06-batch-evaluation` | `evaluate_batch`, identity, chunking, backends |
| DecisionTable | 7 | `07-decision-table-construction` | `prevalidate`, findings, `build_decision_table`, entries, folds, provenance |
| | 8 | `08-applying-decision-tables` | Context validation, `match`, persistence, layer 5 worked example |
| Implementation and extension | 9 | `09-expression-engine-internals` | Compile, bind, score, rank, batch |
| | 10 | `10-decision-table-internals` | Column plans, regions, stages a–f, masks, entry metadata, application |
| | 11 | `11-extending-and-maintaining` | Public boundary, change map, invariants, tests, review checklist |

Appendices: `faq.md` and `glossary.md`.

## Writing rules

- Describe only the current design. Do not narrate removed concepts.
- Every code block must run against the current package on Polars. Show the printed output in a following `text` block and keep it exact.
- Examples use `ContextField` declarations with an explicit wildcard marker; a rule cell is never a null unless the field declares null as its wildcard.
- Link to source files by repository path in backticks, not by commit-pinned URL.
- Supported and tested backends are Polars and Ibis-DuckDB only.

## Maintenance

When the public API changes, update in one change: the affected chapters, the glossary and FAQ entries that name it, `docs/user-quickstart.md`, `docs/backend-support.md` if backends are involved, and `AGENTS.md`. Chapters link to each other by heading anchor; check anchors after renaming a heading.
