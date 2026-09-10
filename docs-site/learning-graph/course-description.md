---
title: Mountainash Rules Package Description
description: A detailed description of the mountainash-rules vectorized, backend-agnostic business rules engine
quality_score: 87
---

# Mountainash Rules Package Description

## Title

Mountainash Rules: Vectorized, Backend-Agnostic Business Rules Engine

## Target Audience

Python developers building configurable business logic systems who need to evaluate rules as tabular data — pricing engines, eligibility checkers, configuration selectors, and combinatorial accumulation problems — using a vectorized, backend-portable approach built on the mountainash expression library.

## Prerequisites

- Intermediate Python programming (classes, enums, type hints, Pydantic models)
- Familiarity with DataFrame operations (filter, join, sort, group_by)
- Understanding of the mountainash expression API (col, lit, when, t_col, ternary logic)
- Basic understanding of the mountainash relation system (relation(), with_columns, filter, collect)
- Familiarity with three-valued (ternary) logic concepts (TRUE/FALSE/UNKNOWN)

## Topics Covered

1. **Foundation** — Ternary logic semantics, match strategy patterns, Pydantic model validation, vectorized evaluation principles, sentinel values
2. **Dimension Model** — Dimension class, DimensionsMetadata collection, MatchStrategy enum (11 types), DimensionRole enum, field resolution, model validation
3. **Expression Rules Engine** — ExpressionRulesEngine class, DimensionCompiler, context extraction, single-pass evaluation pipeline, backend-agnostic operation
4. **Expression Engine Results** — RuleResult wrapper, survivor ranking, specificity scoring, best-match selection, per-rule explainability, at_least filtering
5. **Accumulator Engine** — AccumulatorEngine class, AccumulatorCompiler, prime encoding for combination identity, lattice building phases (partition, anchor, expand, frontier filter)
6. **Accumulator Lattice** — Lattice data structure, coalesced dimension columns, NA flags, combination depth tracking, partition key isolation
7. **Accumulator Results** — AccumulatorResult class, accumulated aggregates, provenance via prime products, lattice application via remapped metadata
8. **Supporting Modules** — Context extraction, Aggregate model, partition keys, CONTEXT_KEY vs CONSTRAINT roles, backend portability patterns

## Topics Excluded

- Mountainash core expression internals (AST nodes, function registry, visitor compilation)
- Mountainash relation backend implementations (Polars/Narwhals/Ibis compilation details)
- Database administration or SQL optimization
- Machine learning or statistical modeling
- Web framework integration or API serving
- Rule authoring UI or spreadsheet tooling

## Learning Outcomes

After studying this package, developers will be able to:

### Remember

- List the 11 MatchStrategy enum values and their data type constraints
- Identify the two DimensionRole values (CONSTRAINT, CONTEXT_KEY) and their purposes
- Name the four sentinel values and their string/numeric variants
- Recall the five phases of accumulator lattice building (partition, prime assignment, anchor, expand, frontier filter)
- List the three key columns in RuleResult (__survived, __specificity, __rank)

### Understand

- Explain how ternary logic (1/0/-1) enables "don't care" wildcard matching in rule evaluation
- Describe the single-pass vectorized evaluation pipeline from context binding through survival filtering
- Explain how prime number encoding provides unique combination identity and subset detection via modular arithmetic
- Describe the difference between the ExpressionRulesEngine (filter-based) and AccumulatorEngine (combinatorial lattice) approaches
- Explain how the DimensionCompiler translates Dimension metadata into backend-agnostic expression templates

### Apply

- Define DimensionsMetadata with mixed match strategies (EXACT, RANGE, PREFIX, SET_MEMBERSHIP)
- Configure and run ExpressionRulesEngine to evaluate a context against a rules DataFrame
- Use RuleResult accessors to retrieve ranked survivors, best match, and per-rule explanations
- Build an AccumulatorEngine with CONTEXT_KEY partitioning and sum aggregates
- Apply a context to a pre-built lattice and retrieve accumulated values with provenance

### Analyze

- Analyze how sentinel values propagate through ternary expressions to produce UNKNOWN outcomes
- Compare compatible and coalesce expression semantics in the accumulator compiler
- Evaluate the frontier filter algorithm for removing dominated combinations
- Analyze the trade-offs between ExpressionRulesEngine (fast single-pass) and AccumulatorEngine (exhaustive combinatorial)

### Evaluate

- Assess whether a business problem requires filter-based or accumulator-based rule evaluation
- Judge appropriate MatchStrategy selection for different dimension data types and semantics
- Evaluate the int64 prime product overflow boundary and its impact on partition sizing
- Assess when CONTEXT_KEY partitioning is necessary versus single-partition evaluation

### Create

- Design custom DimensionsMetadata schemas for domain-specific rule evaluation problems
- Implement end-to-end rule evaluation pipelines combining both engines
- Build partition-aware accumulator workflows with build_all and apply_auto
- Create explainability reports using RuleResult.explain and AccumulatorResult.provenance

## Context

Mountainash Rules provides two complementary engines for evaluating business logic stored as tabular data. The ExpressionRulesEngine performs single-pass vectorized filter evaluation using mountainash's ternary expression system — ideal for "find the best matching rule" problems like pricing lookup or configuration selection. The AccumulatorEngine solves the harder combinatorial problem of finding all maximal consistent subsets of rules — ideal for accumulation problems like "which benefits apply and what is their total value." Both engines are backend-agnostic, operating through the mountainash expression and relation abstractions, though the AccumulatorEngine currently materializes to Polars for its lattice-building phase.
