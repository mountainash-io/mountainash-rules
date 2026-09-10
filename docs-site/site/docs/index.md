---
title: Mountainash Rules
description: A practitioner's manual for the mountainash-rules vectorized, backend-agnostic business rules engine
---


[← Back to Ecosystem](https://docs.mountainash.io/)
# Mountainash Rules

Define business rules as tabular data, evaluate them in a single vectorized pass on any backend, and get ranked results with full per-dimension explainability.

## Why a Guided Manual?

The API reference tells you *what* each class and method does. This manual explains *why* the engine works the way it does -- how ternary logic enables wildcard matching, why specificity ranking produces deterministic best-match results, and when to reach for the accumulator engine instead of the expression engine. Understanding the design makes the API intuitive rather than something you memorize.

## What's Inside

- **[Chapters](chapters/index.md)** -- 11 chapters covering foundations through accumulator lattice results, in dependency order

## Who This Is For

Python developers building configurable business logic -- pricing engines, eligibility checkers, configuration selectors, and combinatorial accumulation problems. If you work with rules stored as tabular data and need vectorized evaluation that runs on any backend, start with [About](#target-audience) to see whether this manual fits your background.


# Package Overview

Mountainash Rules is a vectorized, backend-agnostic business rules engine for Python. It evaluates business rules stored as tabular data -- pricing tiers, eligibility matrices, configuration profiles, compliance conditions -- in a single vectorized pass on any backend the mountainash expression engine reaches (Polars, DuckDB, Snowflake, ClickHouse). Every evaluation result includes a full per-dimension explainability breakdown, and when rules overlap, the AccumulatorEngine resolves all consistent combinations with provenance tracking.

## Target Audience

Python developers building configurable business logic systems who need to evaluate rules as tabular data -- pricing engines, eligibility checkers, configuration selectors, and combinatorial accumulation problems -- using a vectorized, backend-portable approach built on the mountainash expression library.

## Who This Is For

This manual is for Python developers who build configurable business logic systems -- pricing engines, eligibility screeners, configuration selectors, portfolio allocators, and similar problems where the rules live as tabular data and need to be evaluated programmatically. You might be a data engineer wiring rules into a pipeline, a backend developer replacing a sprawl of if/else trees, or an analyst who needs to understand why a particular rule matched.

## What You Should Already Know

You should be comfortable with:

- Python classes, enums, type hints, and Pydantic models
- DataFrame operations (filter, join, sort, group_by) in Polars or a similar library
- The mountainash expression API (`col`, `lit`, `when`, `t_col`, ternary logic)
- The mountainash relation system (`relation()`, `with_columns`, `filter`, `collect`)
- The idea of three-valued logic (TRUE / FALSE / UNKNOWN) -- you don't need deep expertise, but it shouldn't be entirely new

## What You'll Get Out of This

After working through this manual, you will know how to:

- Define dimension schemas with the right match strategies for your data (exact, exact-key, range, regex, set membership, and more)
- Evaluate a context against a rules table and retrieve ranked, specificity-scored results
- Read per-dimension explainability breakdowns to understand exactly why a rule matched or didn't
- Use the AccumulatorEngine to solve combinatorial problems -- find all valid rule combinations with accumulated totals and provenance
- Partition large rule sets with CONTEXT_KEY dimensions for fast, memory-efficient evaluation
- Choose between the ExpressionRulesEngine (single best match) and AccumulatorEngine (all valid combinations) for a given problem

## Prerequisites

- Intermediate Python programming (classes, enums, type hints, Pydantic models)
- Familiarity with DataFrame operations (filter, join, sort, group_by)
- Understanding of the mountainash expression API (col, lit, when, t_col, ternary logic)
- Basic understanding of the mountainash relation system (relation(), with_columns, filter, collect)
- Familiarity with three-valued (ternary) logic concepts (TRUE/FALSE/UNKNOWN)

## What This Manual Covers

1. **Foundation** -- Ternary logic semantics, match strategy patterns, Pydantic model validation, vectorized evaluation principles, sentinel values
2. **Match Strategies** -- The 12-member MatchStrategy enum: exact, exact-key (partition routing), range, greater/less-than, prefix/suffix/contains, per-row regex, context-level regex, set membership/exclusion, bool ternary comparison
3. **Dimension Model** -- Dimension class, DataType enum with temporal sentinels, YAML round-trip, DimensionsMetadata collection, field resolution, model validation
4. **Dimension Compiler** -- DimensionCompiler translation of dimension metadata into backend-agnostic expressions, the shared in-band set-wildcard sentinel
5. **Expression Rules Engine** -- ExpressionRulesEngine class, context extraction, single-pass evaluation pipeline, backend-agnostic operation
6. **Hit Policies** -- The HitPolicy enum (collect, unique, first, priority, any, rule_order), SelectionInfo, cardinality application, table-level policy configuration
7. **Expression Engine Results** -- RuleResult wrapper, survivor ranking, specificity scoring, per-rule explainability, and the engine-level ExplainResult
8. **Batch Evaluation** -- evaluate_batch for scoring many contexts in one vectorized pass, BatchRuleResult and its per-context accessors
9. **Accumulator Compiler** -- AccumulatorCompiler compatible/coalesce/NA-flag expressions, including set-membership and set-exclusion support
10. **Accumulator Engine** -- AccumulatorEngine class, prime encoding for combination identity, lattice building phases, apply-phase caching
11. **Lattice Structures and Results** -- Lattice class with save/load persistence, AccumulatorResult, accumulated aggregates (sum/min/max/product), the LatticeIndex ternary-partition router, supporting modules

## What This Manual Does Not Cover

- Mountainash core expression internals (AST nodes, function registry, visitor compilation)
- Mountainash relation backend implementations (Polars/Narwhals/Ibis compilation details)
- Database administration or SQL optimization
- Machine learning or statistical modeling
- Web framework integration or API serving
- Rule authoring UI or spreadsheet tooling

## Key Capabilities

**Rules that live where the data lives** -- Rule definitions are tabular data. Match strategies compile to mountainash expressions that evaluate directly against whatever backend holds the data -- Polars in a notebook, DuckDB in a pipeline, Snowflake in production. The rules don't move to the data; the expressions compile to the backend.

**Every evaluation explains itself** -- Ask why a rule matched and get a per-dimension breakdown: which dimensions matched, which were wildcards, and which excluded. Score the whole table at once with the engine-level explain(), or one already-surviving rule with RuleResult.explain(). Explainability is built into the evaluation, not a post-hoc analysis. Regulatory audit, pricing justification, eligibility appeals -- the explanation is always available.

**Combinatorial problems solved** -- When multiple rules can apply simultaneously -- overlapping discount tiers, interacting eligibility conditions, complex regulatory criteria -- the AccumulatorEngine pre-computes all consistent combinations. Each combination traces which rules contributed through prime-product provenance. Problems that would require exponential if/else trees become a single vectorized evaluation.

**Match strategies that cover real-world logic** -- Exact match, an exact-key partition router, ranges, greater/less than, prefix, suffix, contains, per-row regex, context-level regex, set membership, set exclusion. Real business rules need more than equality checks. Each strategy compiles to backend-native expressions, and wildcard dimensions match everything for graduated specificity.

**Rules that rank themselves -- and can be governed** -- Surviving rules are automatically ranked by specificity. A rule matching three dimensions explicitly is more specific than one matching two and wildcarding the third. Layer a hit policy on top to require a unique match, take the first by priority, or assert no conflicting outputs. The best match is always the most specific, and ties resolve deterministically.

**Backend portability from the expression engine** -- Because match strategies compile to mountainash expressions, and those expressions compile to Polars, Narwhals, and Ibis, rules evaluate on any backend the expression engine reaches. Store rule tables in Snowflake, evaluate against DuckDB, persist results to Iceberg. The rule definitions don't change.

**One context or a million, one call** -- evaluate_batch() scores an entire population of contexts against the rule table in a single vectorized pass and ranks survivors per context.

## Context

Mountainash Rules provides two complementary engines for evaluating business logic stored as tabular data. The ExpressionRulesEngine performs single-pass vectorized filter evaluation using mountainash's ternary expression system -- ideal for "find the best matching rule" problems like pricing lookup or configuration selection. The AccumulatorEngine solves the harder combinatorial problem of finding all maximal consistent subsets of rules -- ideal for accumulation problems like "which benefits apply and what is their total value." Both engines are backend-agnostic, operating through the mountainash expression and relation abstractions, though the AccumulatorEngine currently materializes to Polars for its lattice-building phase.
