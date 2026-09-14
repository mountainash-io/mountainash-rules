---
title: Mountainash Rules Package Description
description: Internal scope and reader prerequisites for the four-part, eleven-chapter Mountainash Rules manual
---

# Mountainash Rules Package Description

This is an internal input to book rebuilding, not a published chapter or a separate chapter plan. Use `docs-site/editorial-brief.md` and `docs-site/chapter-plan.md` as the editorial instructions. Canonical concept assignments and prerequisites are in `docs-site/learning-graph/learning-graph.json`; reconciliation evidence from both manuscripts is in `docs-site/reconciliation-crosswalk.json`.

## Target audience

Python developers using Mountainash Rules, and maintainers and contributors who need to understand or extend it. The manual teaches shared concepts, then practical use of each engine, then implementation and extension. It combines the maintained book's explanations with useful worked examples and diagrams from the donor book, checked against the selected current source.

## Reader prerequisites

Readers need basic Python and familiarity with dictionaries and DataFrames. Pydantic, ternary matching, dimension metadata, and the Mountainash expression and relation APIs are material to explain in the book, not assumed knowledge before Chapter 1. More advanced implementation detail belongs in Part 4.

## Approved architecture

### Shared foundations

1. **Two Rule Engines, One Shared Model**: explain the two engines and their shared primitive structures, with a small contrasting example. Teach the four assigned foundational concepts without turning the opening into the full engine walkthroughs.
2. **The Shared Rule Model: Tables, Contexts and Dimensions**: describe and validate dimensions, field mappings, types, roles and metadata; introduce expression and relation abstractions at a usable level.
3. **Matching Concepts: Strategies, Unknowns and Wildcards**: explain matching outcomes and strategy families, including differences in engine support.

### The Expression Rules Engine

4. **Using the Expression Rules Engine**: evaluate a context and interpret individual rule results through a complete workflow.
5. **Expression Engine Hit Policies, Results and Explanations**: choose and explain results, distinguish ordering from filtering, and understand reselection limits.
6. **Expression Engine Batch Evaluation**: evaluate many contexts while preserving their identity, ranking and results. The existing batch sample is approved and is the writing reference.

### The Accumulator Engine

7. **Using the Accumulator Engine**: build compatible combinations, define aggregates, and apply contexts through a complete workflow.
8. **Accumulator Lattices, Results and Routing**: interpret combined conditions, aggregate values and provenance; route requests and save or load lattices.

### Implementation and extension

9. **Inside the Expression Rules Engine**: explain compilation, binding, comparison expressions and result selection after their public behavior.
10. **Inside the Accumulator Engine**: explain compatibility, coalescing, combination identities, expansion and pruning, then connect application to the expression engine.
11. **Extending and Maintaining Both Engines**: provide extension and maintenance recipes, revisiting earlier concepts where needed.

FAQ and glossary are supporting appendices, not additional parts. The four parts also govern the actual menu structure.

## Scope boundaries

Do not expand this manual into Mountainash core AST or backend implementation documentation, database administration, machine learning, web-framework integration, or a rule-authoring UI. Explain the shared abstractions sufficiently for using and understanding the Rules package.

Describe supported behavior precisely. A shared metadata model does not imply identical strategy support in both engines. Backend abstraction does not imply that every operation stays native on every backend. Column operations still process data, and building combinations is a different workload from evaluating a context.

## Rebuilding status

All 133 concepts now have canonical assignments to the eleven chapters. That mapping is preparation for rebuilding, not evidence that the prose is complete. Chapter 1 was rejected twice and must be rebuilt from its assigned concepts and architectural purpose. Do not use its existing outline as the template. The approved batch chapter, originally Chapter 4 and now Chapter 6, remains the editorial reference.

The user will invoke the appropriate skill separately. Keep the selected technical source distinct from the candidate's historical profile and source checkout; follow the paths and provenance in the editorial brief. No commit or publication is authorized by this preparation.
