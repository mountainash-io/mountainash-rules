---
title: "Chapter 1: Two Engines, One Rule Model"
description: "Rules as table rows, contexts as facts, and the two ways Mountainash Rules matches one against the other."
---

# Chapter 1: Two Engines, One Rule Model

A business decision is often a list of conditions: *an AU customer on the premium tier who spends over 1,000 gets 15% off*, *any AU customer gets 10%*, *everyone else gets nothing*. Written as `if` branches the list grows by editing code. Mountainash Rules keeps the list as **data**: each rule is a row in a table, each condition is a column, and a small declaration says how each column is compared with the facts you supply.

This chapter introduces the vocabulary and the two ways the package matches facts against rules. Later chapters take each in turn.

## Put the rules in a table

A **rules table** has one row per rule.

| rule_name | region | tier | spend_min | spend_max | discount |
|---|---|---|---:|---:|---:|
| premium_au | AU | premium | 1000 | 9999 | 15 |
| standard_au | AU | `*` | 0 | 9999 | 10 |
| global_fallback | `*` | `*` | *(null)* | *(null)* | 0 |

Columns play three roles:

- **Condition columns** (`region`, `tier`, `spend_min`, `spend_max`) hold what a rule requires.
- **Output columns** (`discount`) carry a rule's payload. The engines pass them through; they are never compared.
- **Identity columns** (`rule_name`, or `rule_id` and `scope` for a DecisionTable) say which rule a row is.

A **context** is the set of facts for one decision: `{"region": "AU", "tier": "premium", "spend": 1500}`. It is a dictionary, a pydantic model, or a row of a context table.

A **dimension** is one comparison between a context field and a rule column. `region` is compared by equality; `spend` is compared with the interval `[spend_min, spend_max]`. A **match strategy** names the comparison (`EXACT`, `RANGE`, `PREFIX`, and so on). The declarations that describe the fields and dimensions are collected in `DimensionsMetadata` ([Chapter 2](../02-shared-rule-model/index.md)).

## Match, don't branch

Each dimension scores a rule 1 (match), 0 (unknown or no constraint) or −1 (non-match). A rule **survives** when no dimension scores −1. Its **specificity** is the number of dimensions that scored 1, so a rule that names the customer's region and tier outranks one that names only the region. [Chapter 3](../03-matching-concepts/index.md) explains the scoring, wildcards and missing values.

Because every comparison is an expression over a column, the work for all rules happens in one pass, and Mountainash compiles it for the backend that holds the table. Polars and Ibis-DuckDB are the supported and tested backends.

## The two engines

| | `ExpressionRulesEngine` | `DecisionTable` |
|---|---|---|
| Input | rules table, `DimensionsMetadata` | rules table, `DimensionsMetadata` with `uuid_column`, `scope_column`, `outputs` |
| Preparation | compile the dimensions once | `prevalidate`, then `build_decision_table` |
| Matching | score **every rule** against the context | look the context up in **entries** that were precomputed from the rules |
| Result | the surviving *rules*, ranked, with a hit policy applied | every matching *entry*, with its specificity and folded outputs |
| Several rules apply | you choose with a hit policy (first, priority, unique, ...) | construction already folded their contributions; matching returns every entry |
| Checks | none: rules and contexts are evaluated as given | prevalidation of rules; context validation functions of your choosing |

Use the **expression engine** when the answer is a rule: *which rule applies, and why?* It explains every decision (which dimension matched, which was a wildcard, which excluded a rule) and works on small or frequently changing rule sets.

Use a **DecisionTable** when several rules can apply to one context and their outputs combine: fees that add up, rates built from a base and margins, scores that accumulate. Construction cuts the rules into non-overlapping cells once, folds each cell's contributions (`SUM`, `MIN`, `MAX`), and records which source rules built each cell. Matching then returns the cells that apply. A saved table can be served without rebuilding.

The DecisionTable evaluates its entries *through* the expression engine, so both share one set of match strategies and one null model.

## The five layers of a DecisionTable

A DecisionTable separates five responsibilities, each with its own input and output:

1. **Prevalidation** — `prevalidate(rules, metadata)` checks the authored rules and returns `ValidatedRules`, or raises `DataContractError`.
2. **Construction** — `build_decision_table(validated)` builds the `DecisionTable`.
3. **Post-validation** — deferred; the package does not provide it.
4. **Application** — `DecisionTable.match(contexts).fetch()` returns every matching entry.
5. **Business post-aggregation** — your own code combines the matches for your domain. A home-loan rate that adds a base rate and two margins is an example; the package defines no business API for it.

Context validation (`check_context_schema`, `validate_contexts`) is a separate capability that you run before matching. Chapters 7 and 8 cover the layers.

## Backends

Rules and contexts are Mountainash relations or native tables. Package code never imports Polars or Ibis; it builds Mountainash expressions and lets Mountainash compile them. The supported backends are Polars and Ibis-DuckDB (`duckdb` is pinned `<2.0`), and each behaviour test runs on both. The repository's `docs/backend-support.md` lists the matrix and the known upstream items. A context table is copied to the backend that holds the rules or entries, never the reverse.

## Next steps

[Chapter 2](../02-shared-rule-model/index.md) defines the fields, dimensions and metadata. [Chapter 3](../03-matching-concepts/index.md) explains how a dimension scores a rule.

## Source references

- `src/mountainash_rules/__init__.py` — the public import surface.
- `src/mountainash_rules/engines/filter/engine.py` — `ExpressionRulesEngine`.
- `src/mountainash_rules/decision_table/` — prevalidation, construction, application and persistence.
