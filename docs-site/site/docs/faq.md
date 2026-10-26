# FAQ

Focused answers for rule authors, application developers and maintainers. The linked chapters contain worked examples and implementation detail; the [glossary](glossary.md) defines recurring terms.

## General

### What is Mountainash Rules and what does it solve?

It represents business rules as table rows with explicit matching metadata. The Expression Rules Engine evaluates the rows against a context and applies a hit policy. A DecisionTable cuts the rows into non-overlapping entries once, folds the contributions of the rules that cover each entry, and then answers contexts by lookup.

Read [Two Engines, One Rule Model](chapters/01-two-rule-engines/index.md).

### When should I use the expression engine and when a DecisionTable?

Use `ExpressionRulesEngine` when the answer is a *rule*: which rule applies and why, with a hit policy choosing among survivors. Use a `DecisionTable` when several rules apply to one context and their outputs combine (fees that add, a rate built from a base and margins), or when you want to build once and serve many contexts, including from a saved table.

Read [The two engines](chapters/01-two-rule-engines/index.md#the-two-engines).

### Which backends are supported?

Polars and Ibis-DuckDB. Every backend-dependent behaviour test runs on both, and `duckdb` is pinned `<2.0`. Package code never imports a DataFrame library: it builds Mountainash relations and expressions.

Read [Backends](chapters/01-two-rule-engines/index.md#backends).

### Does the engine validate my rules or contexts?

No. `ExpressionRulesEngine` evaluates what it is given and lets backend errors propagate. For a DecisionTable, `prevalidate` checks the rules and `check_context_schema` and `validate_contexts` check contexts; `match` itself checks nothing.

Read [Construct an engine and evaluate a context](chapters/04-expression-rules-engine/index.md#construct-an-engine-and-evaluate-a-context).

### What is the role of pydantic?

`ContextField`, `Dimension`, `DimensionsMetadata` and `Aggregate` are pydantic models, so a mistake in a declaration fails when you construct it. Your own pydantic model can also serve as a context, with `request_id` and one attribute per context field.

Read [The shared rule model](chapters/02-shared-rule-model/index.md).

### What is a context?

The facts for one decision: a dictionary, a pydantic model, or a row of a context table. Each dimension reads the context field named by its `context_field`.

Read [Rules and contexts](chapters/02-shared-rule-model/index.md#rules-and-contexts).

## Ternary logic, wildcards and null

### What is ternary logic and why does the package use it?

Each dimension scores a rule 1 (match), 0 (unknown or don't-care) or −1 (non-match). A rule survives when no dimension scores −1; its specificity is the number of 1s. The third value lets a rule that places no constraint, or a context that omits a value, stay possible without earning specificity.

Read [Ternary logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

### How do I write a rule that matches everything?

Give every dimension of the rule the wildcard marker its field declares (for example `"*"`, or null where `wildcard_declared=True`). Such a rule survives for every context at specificity 0.

Read [Wildcards are declared, never inferred](chapters/03-matching-concepts/index.md#wildcards-are-declared-never-inferred).

### How do I declare a wildcard?

On the `ContextField`: `ContextField(name="region", data_type=DataType.STR, wildcard="*")`, or `wildcard_declared=True` with `wildcard=None` to make null the marker. A field has exactly one marker, used by every dimension on it.

Read [ContextField: a typed fact](chapters/02-shared-rule-model/index.md#contextfield-a-typed-fact).

### Is null a wildcard?

Only where the field declares it. A rule-side null on any other field is invalid data, which `prevalidate` reports.

Read [Wildcards are declared, never inferred](chapters/03-matching-concepts/index.md#wildcards-are-declared-never-inferred).

### What happens when the context is missing a value?

A missing or null context value scores 0 on a value dimension. The rule stays possible and earns no specificity; the engine neither drops the rule nor treats the value as a match. A `TRUTHY` field whose mapping covers `None` maps null to that outcome.

Read [Missing and null context values](chapters/03-matching-concepts/index.md#missing-and-null-context-values).

### How do I ask whether a field is null?

Use a `PRESENCE` dimension: rule cells `IS_NULL`, `IS_NOT_NULL` or the wildcard. An explicit context null is a known fact (1 against `IS_NULL`); an omitted field is unknown.

Read [PRESENCE](chapters/03-matching-concepts/index.md#presence-asking-whether-a-field-is-null).

### What is the difference between "don't-care" and "unknown"?

Both score 0. A don't-care is a rule cell with no constraint; unknown is a context value that was not supplied. A rule that requires `region = EU` is *possible* but unproven when the region is missing.

Read [Don't-care versus unknown](chapters/03-matching-concepts/index.md#dont-care-versus-unknown).

## Match strategies and dimensions

### Which match strategies exist?

`exact`, `not_equal`, `range`, `min`, `max`, `prefix`, `suffix`, `contains`, `regex`, `context_regex`, `set_membership`, `set_exclusion`, `truthy`, `outcome` and `presence`.

Read [Match strategies](chapters/03-matching-concepts/index.md#match-strategies).

### How do ranges and thresholds work?

`range` is a closed interval read from two columns (`range_min_field`, `range_max_field`), where a marker endpoint is unbounded. `min` scores 1 when the context is at least the rule's threshold and `max` when it is at most the threshold. All three need a numeric or temporal field; use `DECIMAL`, not `FLOAT`, for fractions.

Read [Match strategies](chapters/03-matching-concepts/index.md#match-strategies).

### How do the string and set strategies work?

`prefix`, `suffix` and `contains` test whether the context string starts with, ends with or contains the rule's string. `regex` applies a pattern from each rule row; `context_regex` applies one pattern from the dimension. `set_membership` and `set_exclusion` read a list column; null elements in a list are invalid.

Read [Match strategies](chapters/03-matching-concepts/index.md#match-strategies).

### What is a truthy mapping?

A `ContextField.truthy` dictionary from values (including `None`) to outcomes −1, 0 or 1. The rule's and the context's values both pass through it, and a `TRUTHY` dimension scores 1 when the two outcomes are equal and non-zero.

Read [Truthy mappings and outcomes](chapters/03-matching-concepts/index.md#truthy-mappings-and-outcomes).

### What data types can a field have?

`STR`, `INT`, `FLOAT`, `DECIMAL`, `BOOL`, `DATE` and `DATETIME`. `DECIMAL` needs precision, scale and rounding; `DATETIME` needs a unit and a timezone. `FLOAT` cannot be ordered.

Read [ContextField: a typed fact](chapters/02-shared-rule-model/index.md#contextfield-a-typed-fact).

### Can several dimensions read the same field?

Yes. For example an `EXACT` and a `PRESENCE` dimension on `region`, or a `RANGE` and a `MIN` on `term`. They share the field's wildcard marker.

Read [Dimension: one comparison](chapters/02-shared-rule-model/index.md#dimension-one-comparison).

### How is metadata serialised?

As five flat relational tables (`ruleset`, `fields`, `dimensions`, `outputs`, `truthy`) through `DimensionsMetadata.to_tables()` and `from_tables()`. Typed markers are stored as canonical text and parsed by the field's type.

Read [Metadata as five flat tables](chapters/02-shared-rule-model/index.md#metadata-as-five-flat-tables).

## ExpressionRulesEngine and results

### How does evaluation work?

The engine compiles each dimension once, binds the rules and the context, computes one ternary column per dimension, keeps the survivors, orders them by the hit policy, assigns `__rank` and applies limits, all in one relational chain.

Read [Inside the Expression Rules Engine](chapters/09-expression-engine-internals/index.md).

### What does a `RuleResult` offer?

`survivors`, `best_match`, `count`, `explain(rule_name)`, `at_least(n)`, `active_dimensions` and `select(policy, ...)`.

Read [Read the result](chapters/04-expression-rules-engine/index.md#read-the-result).

### How do I see why a rule did not match?

Call `engine.explain(context)`. It scores every rule with no filtering and exposes `__t_<dimension>`, `__survived` and `__specificity`.

Read [Explain every rule](chapters/04-expression-rules-engine/index.md#explain-every-rule).

### What are the `__t_<dimension>` columns?

Observability columns holding each dimension's ternary outcome for each survivor. Set `include_observability=False` to drop them; rank and specificity remain.

Read [Read the result](chapters/04-expression-rules-engine/index.md#read-the-result).

### How do `top_n`, `min_specificity` and `dimensions` work?

`min_specificity` keeps rows with at least that many hard matches; `top_n` then keeps at most that many rows; `dimensions` evaluates a subset of dimensions. `__rank` is assigned before filters and is not renumbered.

Read [Limit and project](chapters/04-expression-rules-engine/index.md#limit-and-project).

### How do I build an engine from custom expressions?

Pass `dimension_expressions` (a dictionary of ternary Mountainash expressions over `__ctx_<name>` columns) instead of metadata, and `output_fields` if you will use `any`.

Read [The expressions-only path](chapters/04-expression-rules-engine/index.md#the-expressions-only-path).

### How are Boolean inputs read?

`BOOL` fields read real Booleans. `BooleanCoercion` flags widen the accepted original values; a value that is still not a Boolean is unknown, not an error.

Read [Boolean inputs](chapters/04-expression-rules-engine/index.md#boolean-inputs).

## Hit policies

### What is a hit policy?

The rule for choosing among surviving rules: `collect`, `rule_order`, `first`, `priority`, `unique` or `any`. It, the `priority_field` and the `output_fields` are call-time arguments of `evaluate`, `evaluate_batch` and `RuleResult.select`.

Read [The six policies](chapters/05-expression-results-and-policies/index.md#the-six-policies).

### What does `HitPolicyViolationError` mean?

`unique` found more than one survivor, or `any` found survivors that disagree on `output_fields`. `error.offending` holds the survivors (single) or a `__context_id`/`__n` summary (batch).

Read [Violations](chapters/05-expression-results-and-policies/index.md#violations).

### Why can `select` refuse a result?

A result that was limited (`top_n`, `min_specificity`) or reduced to one row (`first`, `priority`, `any`) may be missing rows, so re-selection would be wrong. Re-evaluate with the complete candidate set.

Read [Re-select a result](chapters/05-expression-results-and-policies/index.md#re-select-a-result).

### How does `any` choose its output columns?

From `output_fields`, or, on a metadata-backed engine, every rule column that is not a dimension condition column, not `rule_name`, not the priority field and not internal. The expressions-only path requires explicit `output_fields`.

Read [Output fields](chapters/05-expression-results-and-policies/index.md#output-fields).

## Batch evaluation

### What does `evaluate_batch` do?

Scores every context of a table against every rule in one pass and ranks per context, returning a `BatchRuleResult`.

Read [Evaluate a table of requests](chapters/06-batch-evaluation/index.md#evaluate-a-table-of-requests).

### How are context ids handled?

`context_id_field` names the id column, which the engine does not check; without it, ids are zero-based row positions. Results always call the column `__context_id`.

Read [Context identity](chapters/06-batch-evaluation/index.md#context-identity).

### What does `chunk_size` change?

Only the work per step: contexts are evaluated in slices to bound the cross join, and results are identical.

Read [Chunking](chapters/06-batch-evaluation/index.md#chunking).

## DecisionTable

### What are the five layers?

Prevalidation, construction, post-validation (deferred), application, and business post-aggregation. Each has its own input and output.

Read [The five layers of a DecisionTable](chapters/01-two-rule-engines/index.md#the-five-layers-of-a-decisiontable).

### What does `prevalidate` check?

Declared columns and types, UUID and scope identity, null cells, contributions, strategy and type pairings, and duplicate or conflicting rules. It collects every breach and raises `DataContractError`, or returns `ValidatedRules`; unsatisfiable rules are warnings.

Read [Layer 1: prevalidation](chapters/07-decision-table-construction/index.md#layer-1-prevalidation).

### What is an entry?

One covered cell of a scope: its cell columns, an `entry_id`, an `entry_sources_id` and the folded outputs.

Read [What an entry is](chapters/07-decision-table-construction/index.md#what-an-entry-is).

### How are contributions folded?

Each rule contributes once to every cell it covers; the output of the cell is the `SUM` (default), `MIN` or `MAX` of the contributions, as declared by the `Aggregate`.

Read [Folding](chapters/07-decision-table-construction/index.md#folding).

### How do I find which rules built an entry?

Join `table.entry_sources` on `(scope, entry_sources_id)`; it lists the `source_uuid` of every contributing rule.

Read [Provenance: entry_sources](chapters/07-decision-table-construction/index.md#provenance-entry_sources).

### What does `match` return?

`MatchResult(matches)`: one record per matching entry per request, with `request_id`, `scope`, `entry_id`, `entry_sources_id`, `specificity`, the condition columns and the outputs. It does not fold, rank-filter or choose among entries.

Read [Layer 4: match](chapters/08-applying-decision-tables/index.md#layer-4-match).

### Does `match` validate contexts?

No. Run `check_context_schema` and `validate_contexts` first; a context null is unknown and is not rejected.

Read [Context validation](chapters/08-applying-decision-tables/index.md#context-validation).

### How do I choose among several matching entries?

That is layer 5: your own code, for your domain. The quickstart and Chapter 8 show picking the highest-specificity entry per scope and adding three outputs; it is an example, not behaviour of the package.

Read [Layer 5: business post-aggregation](chapters/08-applying-decision-tables/index.md#layer-5-business-post-aggregation).

### How do I save and load a table?

`table.save(path)`, `DecisionTable.load(path, connection=None)` and the optional `verify_saved_table(path)`. A saved table is eight Parquet files and a manifest written last.

Read [Persistence](chapters/08-applying-decision-tables/index.md#persistence).

### Why does a context table get copied but the entries do not?

Every join states `execute_on` on the entries' side, so a small context relation is copied to the entries' backend and the entries never leave it.

Read [How it executes](chapters/08-applying-decision-tables/index.md#how-it-executes).

## Maintenance

### What may change where when I add a strategy?

The enum and validation, the engine's compiler arm, a strategy class for prevalidation and construction, and tests on both backends.

Read [What changes where](chapters/11-extending-and-maintaining/index.md#what-changes-where).

### Which Mountainash workarounds remain?

#366 (integer division on DuckDB) and #376 (null ordering in windows). `duckdb` is pinned `<2.0` for #363.

Read [Known upstream workarounds](chapters/11-extending-and-maintaining/index.md#known-upstream-workarounds).
