---
title: About
description: Who this manual is for, what you need to know, and how to get the most out of it
---

# About

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

- Define dimension schemas with the right match strategies for your data (exact, range, regex, set membership, and more)
- Evaluate a context against a rules table and retrieve ranked, specificity-scored results
- Read per-dimension explainability breakdowns to understand exactly why a rule matched or didn't
- Use the AccumulatorEngine to solve combinatorial problems -- find all valid rule combinations with accumulated totals and provenance
- Partition large rule sets with CONTEXT_KEY dimensions for fast, memory-efficient evaluation
- Choose between the ExpressionRulesEngine (single best match) and AccumulatorEngine (all valid combinations) for a given problem

## How to Navigate

- **Read in order** -- chapters are arranged in dependency order, so prerequisites are always covered before they are used
- **Use search** -- the search bar (top right) indexes every page; use it to jump to a specific term or class
- **Try the MicroSims** -- interactive simulations are the fastest way to build intuition for a new concept
- **Check the Learning Graph** -- the [Learning Graph](learning-graph/index.md) shows how concepts relate to each other

## About Mountainash Rules

Mountainash Rules is a vectorized, backend-agnostic business rules engine built on the [mountainash](https://github.com/mountainash-io/mountainash) expression and relation libraries. It provides two complementary engines: the ExpressionRulesEngine for single-pass filter evaluation (find the best matching rule) and the AccumulatorEngine for combinatorial lattice evaluation (find all valid rule combinations and their accumulated values).

Source code and issue tracker: [github.com/mountainash-io/mountainash](https://github.com/mountainash-io/mountainash)

## Author

Nathaniel Ramm
