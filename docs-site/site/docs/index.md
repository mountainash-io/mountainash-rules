---
title: Mountainash Rules
description: Learn the shared rule model, expression-engine evaluation, and the exact accumulator prototype
---

[Back to the Mountainash ecosystem](https://docs.mountainash.io/)

# Mountainash Rules

Mountainash Rules provides two engines sharing a rule model. The **Expression Rules Engine** matches, ranks, and selects individual rules through backend expressions. The **Accumulator Engine** prototype constructs exact cells with accumulated values and contributor provenance, then resolves contexts against them. Its local Python/native construction is not accepted delivery of the original backend-executed product architecture.

Both engines use rule tables, contexts, dimensions and metadata. The shared foundations explain these structures before developing each engine's workflows.

Start with [Chapter 1](chapters/01-two-rule-engines/index.md) for the two engines and their shared model, [Chapter 2](chapters/02-shared-rule-model/index.md) for dimension configuration, and [Chapter 3](chapters/03-matching-concepts/index.md) for matching semantics. For a complete worked workflow, continue to [the Expression Rules Engine](chapters/04-expression-rules-engine/index.md) or [the Accumulator Engine](chapters/07-accumulator-engine/index.md).

## Four parts, one manual

The menu follows these four parts. Read the shared foundations first, then choose the engine section for the kind of result you need. Implementation details follow the practical workflows.

| Part | Chapters | What it explains |
|---|---|---|
| [Shared foundations](chapters/index.md#shared-foundations) | 1–3 | The two engines, their common primitive structures, and matching concepts |
| [The Expression Rules Engine](chapters/index.md#the-expression-rules-engine) | 4–6 | Individual-rule evaluation, hit policies, explanations and batches |
| [The Accumulator Engine](chapters/index.md#the-accumulator-engine) | 7–8 | Exact prototype cells, outputs, provenance, routing, and persistence |
| [Implementation and extension](chapters/index.md#implementation-and-extension) | 9–11 | How each engine works and where shared changes differ from engine-specific extensions |

The [complete book contents](chapters/index.md) describe all eleven chapters and both appendices.

## Reading and using the examples

The manual assumes Python and basic DataFrame familiarity. It explains dimensions, ternary matching, wildcards and lattice terminology rather than requiring them beforehand. Examples show their inputs and expected results, with diagrams where a relationship or process benefits from a picture.

Backend support depends on the strategies and engine operations in use. The worked examples identify their source revision and backend and explain relevant support boundaries.

Use the [FAQ](faq.md) for focused questions and the [glossary](glossary.md) for definitions.

This book covers the Rules package, not a rule-authoring interface, application deployment or the full Mountainash core compiler. The [source repository](https://github.com/mountainash-io/mountainash-rules) provides package code and contribution guidance.

## Licence

The documentation's [licence and attribution](license.md) apply to this manual. The software has its own [repository licence](https://github.com/mountainash-io/mountainash-rules/blob/730a8583ee9d4fd6b52dc5350699eb66cc7487e9/LICENSE); documentation licensing does not replace it.
