---
title: Mountainash Rules
description: Learn the shared rule model, expression-engine evaluation, and DecisionTable construction, matching and persistence
---

[Back to the Mountainash ecosystem](https://docs.mountainash.io/)

# Mountainash Rules

Mountainash Rules matches facts against rules that are rows of a table. It provides two engines sharing one rule model. The **Expression Rules Engine** scores every rule against a context through backend expressions, ranks the survivors by specificity and applies a hit policy. A **DecisionTable** cuts rules into non-overlapping entries once, folds the contributions of the rules that cover each entry, and then answers contexts by lookup, returning every matching entry. Polars and Ibis-DuckDB are the supported and tested backends.

Both engines use rule tables, contexts, dimensions and metadata. The shared foundations explain these structures before developing each engine's workflows.

Start with [Chapter 1](chapters/01-two-rule-engines/index.md) for the two engines and their shared model, [Chapter 2](chapters/02-shared-rule-model/index.md) for dimension configuration, and [Chapter 3](chapters/03-matching-concepts/index.md) for matching semantics. For a complete worked workflow, continue to [the Expression Rules Engine](chapters/04-expression-rules-engine/index.md) or [the DecisionTable](chapters/07-decision-table-construction/index.md).

## Four parts, one manual

| Part | Chapters | What it explains |
|---|---|---|
| [Shared foundations](chapters/index.md#shared-foundations) | 1–3 | The two engines, their common structures, and matching concepts |
| [The Expression Rules Engine](chapters/index.md#the-expression-rules-engine) | 4–6 | Individual-rule evaluation, hit policies, explanations and batches |
| [The DecisionTable](chapters/index.md#the-decisiontable) | 7–8 | Prevalidation, construction, context validation, matching, persistence and the business post-aggregation example |
| [Implementation and extension](chapters/index.md#implementation-and-extension) | 9–11 | How each engine works and where changes belong |

The [complete book contents](chapters/index.md) describe all eleven chapters and both appendices.

## Reading and using the examples

The manual assumes Python and basic DataFrame familiarity. It explains dimensions, ternary matching, wildcards and DecisionTable terminology rather than requiring them beforehand. Examples show their inputs and expected results.

Use the [FAQ](faq.md) for focused questions and the [glossary](glossary.md) for definitions.

This book covers the Rules package, not a rule-authoring interface, application deployment or the full Mountainash core compiler. The [source repository](https://github.com/mountainash-io/mountainash-rules) provides package code and contribution guidance.

## Licence

The documentation's [licence and attribution](license.md) apply to this manual. The software has its own [repository licence](https://github.com/mountainash-io/mountainash-rules/blob/main/LICENSE); documentation licensing does not replace it.
