---
title: Mountainash Rules
description: Author rule libraries, evaluate and explain decisions, combine compatible rules, and understand the engines behind them
---

[Back to the Mountainash ecosystem](https://docs.mountainash.io/)

# Mountainash Rules

Business rules often begin as a table: a pricing band, an eligibility condition, a configuration choice. `mountainash-rules` gives those rows explicit matching semantics and evaluates them against the facts in a context. The result is not just an answer; it carries the information needed to understand which rules survived and how a selection was made.

This manual follows that work from a first rule library to production-facing batch and combination workflows, then into the implementation and extension points. It assumes Python and basic DataFrame knowledge. Ternary matching, sentinels, and the Mountainash abstractions are introduced here rather than required beforehand.

## Choose the question you need to answer

| Your question | Start here |
|---|---|
| How do rule tables, contexts, unknowns and wildcards fit together? | [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md) |
| How do I describe and validate a rule library? | [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md) |
| How do I evaluate a context, choose a result and explain it? | [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md) |
| How do I score many contexts without confusing their results? | [Scoring Batches of Contexts](chapters/04-scoring-batches/index.md) |
| How do compatible rules combine, and how do I save or route their lattices? | [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md) |
| How are matching and selection implemented? | [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md) |
| How does the accumulator construct combinations and enforce its limits? | [Inside Combination Search](chapters/07-combination-search-internals/index.md) |
| What must I change and preserve when extending an engine? | [Extending and Maintaining the Engines](chapters/08-extending-and-maintaining/index.md) |

If you are new to the package, read the first five chapters in order. The final three explain the mechanisms behind the behavior you have already used. Maintainers and contributors share those explanations; extension recipes link back instead of presenting a second copy of the architecture.

## Two engines, different questions

**`ExpressionRulesEngine`** matches rules against a context, ranks survivors and applies a hit policy. Depending on the policy, the result may retain multiple matches, select a winner or reject an ambiguous rule library. Single-context and batch evaluation have explicit result interfaces; explainability should be read through the appropriate result or engine API.

**`AccumulatorEngine`** builds combinations of compatible rules, then applies a context to the resulting lattice. Aggregates, combination depth and contributing rules describe those results. Building, applying, partitioning and persisting a lattice are distinct operations with different costs and constraints.

Both engines use Mountainash's expression and relation abstractions. That is an architectural approach, not a promise that every operation supports every backend identically. The implementation chapters explain documented backend-specific paths and the boundaries that tests enforce.

## Examples, boundaries and reference material

The chapters use worked rule tables and Python examples to connect API calls to observable results. They also cover empty matches, policy violations, wildcard behavior, partition ambiguity and construction limits. Internal algorithms follow their practical motivation; learning a prime sieve is not a prerequisite for using a saved lattice.

Use the [FAQ](faq.md) for focused questions and the [glossary](glossary.md) for concise definitions linked to fuller explanations. The [chapter index](chapters/index.md) describes the complete reading sequence.

This manual covers the rules package, not Mountainash core compiler implementation, database administration, rule-authoring interfaces or deployment of your application. For package source and repository contribution guidance, see the [source repository](https://github.com/mountainash-io/mountainash-rules).

## Licence

The documentation's [licence and attribution](license.md) apply to this manual. The software has its own [repository licence](https://github.com/mountainash-io/mountainash-rules/blob/94659bb0c096485c87d329f09e944577427f129f/LICENSE); documentation licensing does not replace it.
