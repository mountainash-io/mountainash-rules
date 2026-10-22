# Glossary

Definitions are alphabetical. Each entry links to the chapter that explains the term in context.

## Active dimensions

The dimensions selected for one evaluation (`dimensions=...` or all of them). Their ternary outcomes determine survival and specificity; an inactive dimension contributes nothing.

Read [Limit and project](chapters/04-expression-rules-engine/index.md#limit-and-project).

## Aggregate

A pydantic declaration of one logical output of a scope: `scope`, `output_name`, `contribution_column`, `operation` (`SUM`, `MIN` or `MAX`) and a data type. It tells construction how to fold the contributions of the rules that cover an entry.

Read [Aggregate: an output fold](chapters/02-shared-rule-model/index.md#aggregate-an-output-fold).

## Application

Layer 4 of a DecisionTable. `DecisionTable.match(contexts, scopes=None).fetch()` evaluates the contexts it is given against the entries and returns every matching entry.

Read [Layer 4: match](chapters/08-applying-decision-tables/index.md#layer-4-match).

## Backend

The library or engine that executes table operations. Polars and Ibis-DuckDB are the supported and tested backends. Package code never imports them; it builds Mountainash relations and expressions.

Read [Backends](chapters/01-two-rule-engines/index.md#backends).

## BatchRuleResult

The result of `evaluate_batch`: retained rows for many contexts with `__context_id`, plus accessors such as `matched_context_ids`, `unmatched_context_ids(contexts)`, `counts_per_context`, `best_matches` and `for_context`.

Read [Read the batch result](chapters/06-batch-evaluation/index.md#read-the-batch-result).

## BooleanCoercion

Flags (`BINARY_NUMBERS`, `BOOLEAN_TEXT`, `NUMERIC_TRUTHINESS`) that widen which original values a `BOOL` field reads as true or false. The default `NONE` admits only real Booleans; other values are unknown.

Read [Boolean inputs](chapters/04-expression-rules-engine/index.md).

## build_decision_table

Layer 2. `build_decision_table(validated, *, checkpoint=...)` turns `ValidatedRules` into a `DecisionTable` by cutting each scope's rules into entries and folding contributions.

Read [Layer 2: construction](chapters/07-decision-table-construction/index.md#layer-2-construction).

## build_id

A SHA-256 over a table's rules and metadata. Equal inputs give equal ids; it is stored in the manifest of a saved table.

Read [The other parts of a table](chapters/07-decision-table-construction/index.md#the-other-parts-of-a-table).

## check_context_schema

Compares a pydantic class, a Relation's schema or a mapping with the declared context fields, without reading rows. Type mismatches raise one `ValueError`; a missing declared field or a too-fine DECIMAL scale only warns.

Read [Context validation](chapters/08-applying-decision-tables/index.md#context-validation).

## Column plan

One column group of `entries`: a positive column per field, a negation column per `NOT_EQUAL` or `SET_EXCLUSION` dimension, or an int8 required-outcome column per `TRUTHY`, `PRESENCE`, `OUTCOME`, `CONTEXT_REGEX` dimension and per distinct pattern.

Read [Column plans](chapters/10-decision-table-internals/index.md#column-plans).

## Context

The facts for one decision: a dictionary, a pydantic model, or a row of a context table. A DecisionTable context carries a `request_id`.

Read [The shared rule model](chapters/02-shared-rule-model/index.md#rules-and-contexts).

## ContextField

A typed fact that a context supplies: name, `DataType`, DECIMAL or DATETIME parameters, one wildcard marker, and an optional truthy mapping.

Read [ContextField: a typed fact](chapters/02-shared-rule-model/index.md#contextfield-a-typed-fact).

## Contributor words

The dynamic 63-bit `int64` words that hold, for each cell, the set of rules in its scope that cover it (one bit per rule).

Read [Contributor masks and folds](chapters/10-decision-table-internals/index.md#contributor-masks-and-folds).

## DataContractError

Raised by `prevalidate` when rules breach the data contract. `breaches` lists the blocking `Finding`s and `warnings` the non-blocking ones found at the same time.

Read [Layer 1: prevalidation](chapters/07-decision-table-construction/index.md#layer-1-prevalidation).

## DecisionTable

An immutable, physicalised rules table: `entries`, `entry_sources`, `pattern_conditions`, `source_metadata` and `build_id`. It is matched with `match`, and saved, loaded and verified.

Read [Prevalidating Rules and Building a DecisionTable](chapters/07-decision-table-construction/index.md).

## DecisionTableError

The error `DecisionTable.match` raises for backend failures and unknown scopes. It is a `ValueError`.

Read [Scopes](chapters/08-applying-decision-tables/index.md#scopes).

## Dimension

One comparison between a context field and a rule column through a match strategy.

Read [Dimension: one comparison](chapters/02-shared-rule-model/index.md#dimension-one-comparison).

## DimensionCompiler

Compiles each `Dimension` into a backend-agnostic ternary expression template at engine construction.

Read [Compile: dimensions become templates](chapters/09-expression-engine-internals/index.md#1-compile-dimensions-become-templates).

## DimensionsMetadata

The fields, dimensions and, for a DecisionTable, the `uuid_column`, `scope_column` and `outputs`. It converts to and from five flat relational tables.

Read [DimensionsMetadata: the whole declaration](chapters/02-shared-rule-model/index.md#dimensionsmetadata-the-whole-declaration).

## Don't-care

A rule cell that places no constraint on a dimension: it holds the field's declared wildcard marker and scores 0.

Read [Wildcards are declared, never inferred](chapters/03-matching-concepts/index.md#wildcards-are-declared-never-inferred).

## Entry

One covered cell of a scope in a DecisionTable: its cell columns, an `entry_id`, an `entry_sources_id` and the folded outputs.

Read [What an entry is](chapters/07-decision-table-construction/index.md#what-an-entry-is).

## entry_sources

The relation `(scope, entry_sources_id, source_uuid)` recording which authored rules built each entry. Entries with the same contributors in a scope share an `entry_sources_id`.

Read [Provenance: entry_sources](chapters/07-decision-table-construction/index.md#provenance-entry_sources).

## execute_on

The Mountainash join option that states where a join runs. Every join between contexts and rules or entries states it on the rules or entries side, so contexts are copied to the rules' backend and the rules never move.

Read [How it executes](chapters/08-applying-decision-tables/index.md#how-it-executes).

## ExpressionRulesEngine

The filter engine: it scores every rule against a context, keeps the rules no dimension contradicts, ranks them by specificity and applies a hit policy. It does not validate rules or contexts.

Read [Using the Expression Rules Engine](chapters/04-expression-rules-engine/index.md).

## ExplainResult

The result of `engine.explain(context)`: every rule scored (`__t_<dim>`, `__survived`, `__specificity`) with no survival filter, ranking or policy.

Read [Explain every rule](chapters/04-expression-rules-engine/index.md#explain-every-rule).

## Field family

How a DecisionTable cuts a field: `OrderedField` (intervals), `UnorderedField` (named values plus a wildcard piece) or `TruthyField` (int8 outcomes).

Read [Regions and field families](chapters/10-decision-table-internals/index.md#regions-and-field-families).

## Finding

One prevalidation result: `kind`, `severity` (`error` or `warning`), `detail` and `rule_uuids`.

Read [Layer 1: prevalidation](chapters/07-decision-table-construction/index.md#layer-1-prevalidation).

## Fold

Combining the contributions of every rule that covers a cell into one output with `SUM`, `MIN` or `MAX`. Each rule contributes once per covered cell.

Read [Folding](chapters/07-decision-table-construction/index.md#folding).

## Hit policy

The rule for choosing among surviving rules: `collect`, `rule_order`, `first`, `priority`, `unique` or `any`. It is a call-time argument.

Read [The six policies](chapters/05-expression-results-and-policies/index.md#the-six-policies).

## HitPolicyViolationError

Raised when `unique` or `any` is violated. `offending` holds the survivors (single evaluation) or a `__context_id`/`__n` summary (batch).

Read [Violations](chapters/05-expression-results-and-policies/index.md#violations).

## MatchOperation

The deferred object returned by `DecisionTable.match`; `fetch()` runs it.

Read [Layer 4: match](chapters/08-applying-decision-tables/index.md#layer-4-match).

## MatchResult

The result of `fetch()`: `matches`, a relation with one record per matching entry per request.

Read [Layer 4: match](chapters/08-applying-decision-tables/index.md#layer-4-match).

## MatchStrategy

The lowercase string enum of comparisons: `exact`, `not_equal`, `range`, `min`, `max`, `prefix`, `suffix`, `contains`, `regex`, `context_regex`, `set_membership`, `set_exclusion`, `truthy`, `outcome`, `presence`.

Read [Match strategies](chapters/03-matching-concepts/index.md#match-strategies).

## Missing value

A context value that is not supplied (absent key, unset pydantic field, absent column). It is unknown (0) on a value dimension, and unknown on a presence dimension.

Read [Missing and null context values](chapters/03-matching-concepts/index.md#missing-and-null-context-values).

## Observability columns

The `__t_<dimension>` columns of a result. They make each survivor's ternary outcomes visible and are required by `RuleResult.explain`.

Read [Read the result](chapters/04-expression-rules-engine/index.md#read-the-result).

## OUTCOME

A strategy that compares a required int8 ternary in the rule with an already computed ternary in the context: rule 0 or context 0 scores 0, equal non-zero 1, unequal non-zero −1. DecisionTable entries store pattern, TRUTHY and PRESENCE conditions as OUTCOME columns.

Read [Truthy mappings and outcomes](chapters/03-matching-concepts/index.md#truthy-mappings-and-outcomes).

## Pattern conditions

The relation recording what each derived pattern column of `entries` means: `column`, `context_field`, `strategy` and `pattern`.

Read [Column plans](chapters/10-decision-table-internals/index.md#column-plans).

## PRESENCE

A strategy that asks whether a field is null. Rule cells hold `IS_NULL`, `IS_NOT_NULL` or the wildcard; an explicit context null is a known fact.

Read [PRESENCE: asking whether a field is null](chapters/03-matching-concepts/index.md#presence-asking-whether-a-field-is-null).

## prevalidate

Layer 1. `prevalidate(rules, metadata)` captures the rules relation once, checks the data contract, and returns `ValidatedRules` or raises `DataContractError`.

Read [Layer 1: prevalidation](chapters/07-decision-table-construction/index.md#layer-1-prevalidation).

## Region

The semantic description of what one rule cell covers on one field (point, interval, everything, members, negation, matches, outcome). Strategies lower cells into regions and field families interpret them.

Read [Regions and field families](chapters/10-decision-table-internals/index.md#regions-and-field-families).

## RuleResult

The result of `evaluate`: `survivors`, `best_match`, `count`, `explain`, `at_least`, `active_dimensions` and `select`.

Read [Read the result](chapters/04-expression-rules-engine/index.md#read-the-result).

## Scope

A group of rules, named by `scope_column`, that is built and matched independently. Each scope produces its own entries and outputs.

Read [Scopes](chapters/07-decision-table-construction/index.md#scopes).

## Specificity

The number of dimensions that scored 1 for a rule or entry. Higher means more constrained.

Read [Ternary logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

## Ternary logic

The scoring of each dimension as 1 (match), 0 (unknown or don't-care) or −1 (non-match). A rule survives when no dimension scores −1.

Read [Ternary logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

## Truthy mapping

A `ContextField.truthy` dictionary from values (including `None`) to outcomes −1, 0 or 1, used by `TRUTHY` dimensions.

Read [Truthy mappings and outcomes](chapters/03-matching-concepts/index.md#truthy-mappings-and-outcomes).

## Unknown

A ternary 0 caused by a missing or null context value (as opposed to a don't-care rule cell). The rule stays possible and earns no specificity.

Read [Don't-care versus unknown](chapters/03-matching-concepts/index.md#dont-care-versus-unknown).

## validate_contexts

Checks a batch of contexts relationally and raises one `ValueError` listing every invalid value; otherwise returns its input unchanged. A context null is not an error.

Read [Context validation](chapters/08-applying-decision-tables/index.md#context-validation).

## ValidatedRules

The result of `prevalidate`: the captured `rules`, a copy of the metadata as `source_metadata`, and the non-blocking `warnings`.

Read [Layer 1: prevalidation](chapters/07-decision-table-construction/index.md#layer-1-prevalidation).

## verify_saved_table

The optional integrity check for a saved table: compares each listed file's byte size and SHA-256 with the manifest and returns a `VerificationReport`.

Read [Persistence](chapters/08-applying-decision-tables/index.md#persistence).

## Wildcard marker

The one typed value (or null) that a `ContextField` declares to mean "no constraint". It is never inferred.

Read [Wildcards are declared, never inferred](chapters/03-matching-concepts/index.md#wildcards-are-declared-never-inferred).
