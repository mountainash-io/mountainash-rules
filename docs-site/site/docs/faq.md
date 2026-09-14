# FAQ

Focused answers for rule authors, application developers and maintainers. The linked chapters contain worked examples and implementation detail; the [glossary](glossary.md) defines recurring terms.

## General Architecture and Design

### What is mountainash-rules and what problems does it solve?

Mountainash Rules represents business rules as table rows with explicit matching metadata. The Expression Rules Engine evaluates those rows against a context and applies a hit policy. The Accumulator Engine builds compatible combinations and their aggregate values for later application to contexts.

Read [Vectorized Evaluation](chapters/01-two-rule-engines/index.md#comparing-rules-as-columns).

### What are the two engines and when should I use each one?

Use `ExpressionRulesEngine` to match, rank and select individual rule rows. Use `AccumulatorEngine` when several compatible rules contribute together and you need their combined constraints, aggregates and provenance. The accumulator has a separate construction phase; it does not merely sum the filter engine's current survivors.

Read [AccumulatorEngine](chapters/07-accumulator-engine/index.md#accumulatorengine).

### What does "backend-agnostic" mean in the context of mountainash-rules?

The normal implementation uses Mountainash expressions and relations instead of directly coding against one DataFrame library. That boundary has documented exceptions and backend-specific limitations, notably per-row `REGEX`. Supported relation types do not guarantee that every strategy, dtype and operation works identically on every backend.

Read [Backend-Agnostic Design](chapters/01-two-rule-engines/index.md#using-other-dataframe-libraries).

### What is the role of Pydantic in mountainash-rules?

Pydantic validates configuration models such as `Dimension`, `DimensionsMetadata` and `Aggregate`. It catches declared field/type/strategy constraints and invalid enum values. It is not a substitute for checking the actual rule table's columns, reserved values and business meaning. Metadata validation and data validation are separate responsibilities.

Read [Pydantic Model Validation](chapters/02-shared-rule-model/index.md#pydantic-models-validate-configuration-before-evaluation).

### Why use a rule table rather than condition chains?

A table makes repeated decision structure explicit as data: rows can be reviewed, versioned and compared without encoding each rule as another branch. Dimension metadata supplies the comparison semantics, while results expose matching information. This works when the decision fits the package's tabular model; it does not remove the need to review rule changes or manage application state.

Read [DataFrame as Rule Store](chapters/01-two-rule-engines/index.md#put-the-rules-in-a-table).

### What is a Context Object and how is it used?

A context supplies the facts against which rules are evaluated, commonly as a dictionary or Pydantic model. Metadata maps each logical dimension to its context field. Missing values are normalized before comparison, but the resulting behavior depends on the strategy. For batches, each context is a row with an identifier rather than one shared dictionary.

Read [Context Object](chapters/01-two-rule-engines/index.md#describe-one-delivery).

### How are rules stored and what format do they use?

Rules are rows in a supported relation or DataFrame. Metadata identifies each strategy's input columns: scalar strategies generally use one rule field, while `RANGE` uses two bounds. Other columns can carry outputs. Use the declared data types and reserved sentinel conventions; a visually empty cell is not a universal portable wildcard.

Read [DataFrame as Rule Store](chapters/01-two-rule-engines/index.md#put-the-rules-in-a-table).

### How is the package organized?

Shared models, context handling, comparison compilation and result selection live under `core/`. The filter and accumulator implementations live under `engines/`; the accumulator also has lattice, aggregate, result and prime-identity modules. Dependencies run from accumulator to filter to core. Application imports use the package root.

Read [Backend Purity Enforcement](chapters/11-extending-and-maintaining/index.md#backend-purity-enforcement).

## Ternary Logic and Sentinel Values

### What is ternary logic and why does mountainash-rules use it?

Matching uses three numeric outcomes: `1` for a hard match, `0` for unknown, and `-1` for a contradiction. This lets a rule remain eligible without claiming a definite match on every dimension. Unknown is an evaluation outcome, not a synonym for every null, missing field or stored sentinel in every strategy.

Read [Ternary Logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

### What are sentinel values and how do they work?

Sentinels are reserved values that represent rule-side UNKNOWN or context-side NOT_SET. Strings, numbers, dates and datetimes have distinct typed pairs; Boolean exact comparisons use null handling instead. Use the helper functions rather than substituting arbitrary magic values. Keep sentinel values out of legitimate business data and check each strategy's treatment of them.

Read [Sentinel Values](chapters/03-matching-concepts/index.md#sentinel-values-telling-no-constraint-apart-from-no-answer).

### How does ternary logic affect rule matching outcomes?

A rule survives when no active dimension yields `-1`. Specificity counts the dimensions yielding `1`; unknown outcomes add nothing. Ranking then follows the hit policy, so a higher-specificity survivor does not necessarily win under `FIRST`, `PRIORITY` or `RULE_ORDER`. Survival, specificity and selection answer different questions.

Read [Survival Computation](chapters/04-expression-rules-engine/index.md#understand-survival-and-ranking).

### Can I have a rule that matches everything (a default rule)?

For strategies that recognize rule-side wildcards, an unconstrained row can serve as a fallback and typically contributes no specificity on those dimensions. Use typed scalar sentinels or the documented set wildcard list. Do not assume this works for `CONTEXT_REGEX`, which has no rule-side wildcard branch, or that a fallback guarantees the policy will select it.

Read [Sentinel Values](chapters/03-matching-concepts/index.md#sentinel-values-telling-no-constraint-apart-from-no-answer).

### What happens when the context is missing a value for a dimension?

An omitted field or Python `None` is normalized to the declared type’s `NOT_SET` value; Boolean absence remains null. The selected strategy then determines the outcome. Ordinary ternary comparisons and row-based string strategies produce unknown, while `EXACT_KEY` rejects a missing context against a specific key and `CONTEXT_REGEX` rejects it for every rule.

Read [Context Value Extraction](chapters/09-expression-engine-internals/index.md#context-value-extraction).

## Match Strategies and Dimensions

### Which MatchStrategy members are available?

There are thirteen members: `EXACT`, `EXACT_KEY`, `NOT_EQUAL`, `RANGE`, `GREATER_THAN`, `LESS_THAN`, `PREFIX`, `SUFFIX`, `CONTAINS`, `REGEX`, `CONTEXT_REGEX`, `SET_MEMBERSHIP` and `SET_EXCLUSION`. Their input types and wildcard rules differ. The accumulator supports only implemented compatibility/coalescing combinations; in particular, its `EXACT` construction path does not correctly support Boolean-null wildcards.

Read [MatchStrategy Enum](chapters/02-shared-rule-model/index.md#the-matchstrategy-enum).

### How does the EXACT match strategy work?

`EXACT` compares a rule value with its context value using ternary-aware equality. Ordinary equality yields a hard match, inequality a contradiction, and recognized sentinel operands an unknown outcome. Boolean exact matching uses explicit null checks. `EXACT_KEY` is a separate asymmetric strategy, not an interchangeable spelling of `EXACT`.

Read [EXACT Strategy](chapters/03-matching-concepts/index.md#exact-strategy).

### How does the RANGE match strategy work?

`RANGE` compares the context with two rule columns using the dimension's lower and upper inclusivity flags, then combines the outcomes with ternary AND. A sentinel bound is unknown rather than a hard match: with one unspecified bound and one passing bound, the dimension can survive with outcome `0`. Endpoint inclusivity and an unspecified bound are different concepts.

Read [RANGE Strategy](chapters/03-matching-concepts/index.md#range-strategy).

### How do the string match strategies (PREFIX, SUFFIX, CONTAINS, REGEX) work?

`PREFIX` and `SUFFIX` test the start or end of the context string; `CONTAINS` searches for literal text anywhere within it. `REGEX` reads a pattern from each rule row and currently uses Polars. These four strategies return unknown for a rule wildcard or missing context. `CONTEXT_REGEX` instead stores one pattern in metadata and rejects missing input.

Read [PREFIX Strategy](chapters/03-matching-concepts/index.md#prefix-strategy).

### How do SET_MEMBERSHIP and SET_EXCLUSION strategies work?

The rule cell holds a list and the context supplies a scalar. `SET_MEMBERSHIP` accepts membership; `SET_EXCLUSION` accepts non-membership. A reserved one-element list containing the typed UNKNOWN sentinel represents an unconstrained rule. Boolean set dimensions are rejected. In accumulator construction, membership sets intersect and exclusion sets union, with normalization and validation protecting their representation.

Read [SET_MEMBERSHIP Strategy](chapters/03-matching-concepts/index.md#set_membership-strategy).

### What is the DimensionRole enum and what are CONSTRAINT and CONTEXT_KEY?

`CONSTRAINT` is the ordinary matching role. In accumulator metadata, `CONTEXT_KEY` identifies the fields used to separate construction into partitions; those keys are not coalesced as constraints. The filter engine does not automatically exclude a dimension merely because its role is `CONTEXT_KEY`: it still compiles and evaluates its configured strategy.

Read [DimensionRole Enum](chapters/02-shared-rule-model/index.md#the-dimensionrole-enum).

### What is the Dimension class and how do I configure it?

A `Dimension` combines a logical name, match strategy, data type, role and field mappings. Use `rule_field` and `context_field` when physical names differ from the logical name; use the two bound fields for `RANGE`. Strategy-specific validation ensures required configuration is present. Begin with the worked library rather than inferring argument names from a prose label.

Read [Dimension Class](chapters/02-shared-rule-model/index.md#the-dimension-class).

### What is DimensionsMetadata and why is it a collection?

`DimensionsMetadata` holds the dimension collection and table-level hit-policy settings, including priority and output-field information. It offers dimension lookup and YAML serialization. Treat it as configuration for a rule library, not as the rules themselves or a complete validator of the underlying DataFrame.

Read [DimensionsMetadata](chapters/02-shared-rule-model/index.md#dimensionsmetadata).

### What are Data Type Constraints for match strategies?

Ordered strategies require appropriate numeric or temporal values, string strategies require strings, and set strategies require supported non-Boolean element types. `RANGE` needs two bounds; `CONTEXT_REGEX` needs a literal pattern. The chapter's validation table states the actual model checks. Valid metadata still does not guarantee that your data columns have the intended dtype or values.

Read [Data Type Constraints](chapters/02-shared-rule-model/index.md#data-type-constraints).

## ExpressionRulesEngine and Results

### How does the ExpressionRulesEngine evaluation pipeline work?

The metadata construction path compiles dimension expressions once. Evaluation binds context columns, computes per-dimension ternaries, derives survival and specificity, orders survivors, checks policy assertions and applies result limits/cardinality. Engine-level `explain()` stops before selection. Batch evaluation shares the expressions but prepares contexts and implements per-context selection differently.

Read [Dimension Expression Phase](chapters/09-expression-engine-internals/index.md#dimension-expression-phase).

### What is the DimensionCompiler and what does it produce?

`DimensionCompiler` translates dimension metadata into expression objects; it does not evaluate a rule table itself. Its dispatch targets produce the ternary operations used later by the engine. Some families share helpers, while `EXACT_KEY`, Boolean handling and the two regex strategies require distinct branches. Context values arrive through the column names those expressions reference.

Read [DimensionCompiler](chapters/09-expression-engine-internals/index.md#dimensioncompiler).

### What is specificity scoring and how does ranking work?

Specificity is the number of active dimensions with outcome `1`. Default `COLLECT` ordering sorts descending by that score and then ascending by original rule position. Ranks are sequential positions, not shared dense ranks for equal specificity. Other policies change the ordering, so inspect the policy before interpreting rank as specificity.

Read [Specificity Scoring](chapters/04-expression-rules-engine/index.md#specificity-how-many-dimensions-matched).

### What is the RuleResult class and what accessors does it provide?

`RuleResult` wraps the returned survivor frame and exposes `survivors`, `best_match`, `count` and `active_dimensions`. `explain(rule_name)` reads one retained rule's ternaries; `at_least(n)` returns a filtered DataFrame; `select()` reapplies selection when the necessary information remains. It is not an unfiltered record of every rule that was evaluated.

Read [RuleResult Class](chapters/04-expression-rules-engine/index.md#read-the-result-contract).

### How does the explain method work and what does it show?

`RuleResult.explain(rule_name)` looks up that name in the retained result and returns a dimension-to-ternary dictionary. It cannot explain a rule absent from that result, and it requires the ternary columns to remain available. Use engine-level `explain(context)` when diagnosing eliminated rules or comparing all rule outcomes.

Read [RuleResult Explain Method](chapters/05-expression-results-and-policies/index.md#explain-a-decision-and-refine-the-result).

### What is the difference between the convenience and advanced paths?

The convenience constructor compiles `dimension_metadata`. The advanced constructor accepts an already-compiled `dimension_expressions` dictionary. Both require rules, and exactly one configuration path must be supplied. Advanced expressions must follow the context-column and ternary contracts. Without dimension metadata, `ANY` also requires explicit `output_fields` at construction.

Read [Convenience vs Advanced Path](chapters/04-expression-rules-engine/index.md#convenience-vs-advanced-construction).

### How do the at_least, top_n, and min_specificity filters work?

`result.at_least(n)` returns a native frame containing retained rows with specificity at least n; it leaves the original result unchanged. `top_n` and `min_specificity` are evaluation arguments applied after ranking. They can leave gaps in retained ranks, and their result is conservatively marked incomplete for later `select()` calls.

Read [At Least Filter](chapters/05-expression-results-and-policies/index.md#at_least-filtering-the-returned-survivors).

### What are the observability columns and how can I use them?

The per-dimension `__t_<name>` columns carry matching outcomes; `__specificity` and `__rank` describe scoring and ordering. They are useful for inspection but belong to the engine's reserved namespace. `include_observability=False` removes per-dimension ternaries from ordinary results, so code that calls per-rule `explain()` must retain them.

Read [Observability Columns](chapters/04-expression-rules-engine/index.md#observability-columns).

### What is the engine-level explain() method, and how is it different from RuleResult.explain()?

`engine.explain(context)` scores every rule without survivor filtering, sorting, rank assignment or hit-policy selection. It returns `ExplainResult`, not `RuleResult`. In contrast, `result.explain(rule_name)` reads one rule from an already-selected survivor result. Choose the former to understand rejection and the latter to inspect a retained match.

Read [Engine-Level Explain](chapters/05-expression-results-and-policies/index.md#engine-level-explain-scoring-every-rule).

### How can I inspect ExplainResult when diagnosing a rule table?

Use `ExplainResult.frame`, `survivors` and `non_survivors` to compare the per-dimension ternaries, `__survived` and `__specificity`. A rejected rule can still have positive specificity because another dimension matched. There is no rank or selected winner: this interface shows scoring before selection.

Read [ExplainResult Class](chapters/05-expression-results-and-policies/index.md#the-explainresult-class).

### How can separate consumers share one evaluation?

Keep one complete `COLLECT` result with its selection provenance and ranking columns, then let consumers call `select()` for their required policies. Keep observability columns as well if consumers need per-rule explanations. Limiting filters and the one-row policies `FIRST`, `PRIORITY` and `ANY` mark a result as possibly truncated. A complete `COLLECT` result containing just one survivor remains eligible for reselection.

Read [RuleResult Select Method](chapters/05-expression-results-and-policies/index.md#reapplying-a-policy-with-ruleresultselect).

## Hit Policies

### What is a hit policy and why does it exist?

A hit policy defines what to do with the survivor set: how to order it, what assertions it must satisfy, and whether to keep all rows or one. It is applied after matching rather than changing a dimension's comparison. Choosing a policy makes assumptions such as uniqueness or output agreement explicit.

Read [HitPolicy Enum](chapters/05-expression-results-and-policies/index.md#choose-and-reapply-a-hit-policy).

### What are the six hit policies, and when should I use each?

`COLLECT` retains specificity-ranked survivors. `UNIQUE` requires at most one. `FIRST` chooses the earliest declared survivor. `PRIORITY` orders by descending priority, then specificity and rule position, and chooses one. `ANY` requires output agreement before choosing one specificity-ranked row. `RULE_ORDER` retains all survivors in declaration order. All can return no rows when nothing survives.

Read [HitPolicy Enum](chapters/05-expression-results-and-policies/index.md#choose-and-reapply-a-hit-policy).

### What does HitPolicyViolationError mean, and when is it raised?

`HitPolicyViolationError` reports a violated `UNIQUE` or `ANY` assertion. It carries the policy and an offending frame, which can be inspected rather than treated as a generic empty result. Assertions consider the full relevant survivor set before optional truncation; reducing `top_n` does not repair a contradictory policy.

Read [HitPolicyViolationError](chapters/05-expression-results-and-policies/index.md#hitpolicyviolationerror).

### How do I configure a hit policy at the table level versus per call?

Store the library's default `hit_policy`, `priority_field` and `output_fields` in `DimensionsMetadata`. Evaluation can override the policy and priority field for one call. `PRIORITY` needs a priority field, and `ANY` needs meaningful output-field selection. Explicit output fields are preferable when inference would accidentally include auxiliary columns.

Read [Table-Level Hit Policy Fields](chapters/05-expression-results-and-policies/index.md#table-level-hit-policy-fields).

### Why can re-selection not recover discarded survivors?

`select()` uses only the rows already retained and requires selection provenance and ranking columns. Results from `FIRST`, `PRIORITY`, `ANY`, `top_n` or `min_specificity` are conservatively marked possibly truncated and reject reselection, even if no row happened to be lost. Keep a complete `COLLECT`, `UNIQUE` or `RULE_ORDER` result when another policy will be needed.

Read [RuleResult Select Method](chapters/05-expression-results-and-policies/index.md#reapplying-a-policy-with-ruleresultselect).

### How do ordering, assertions, and cardinality interact in a hit policy?

Ordering establishes deterministic positions, assertions validate the full survivor population, and optional filters/cardinality determine what is returned. These stages must not be rearranged casually: asserting uniqueness after taking one row would conceal ambiguity. The batch path performs the corresponding checks per context with its own grouped implementation.

Read [Cardinality Application](chapters/09-expression-engine-internals/index.md#cardinality-application).

## Batch Evaluation

### What does evaluate_batch() do, and when should I use it instead of evaluate()?

`evaluate_batch()` evaluates a contexts frame against the same rules and returns a `BatchRuleResult`. It prepares context columns, pairs contexts with rules, and ranks/selects survivors per context. Use it for frame-based workloads, but account for the context-by-rule intermediate relation and documented dtype/strategy boundaries instead of assuming it is always faster than a loop.

Read [Evaluate Batch Method](chapters/06-batch-evaluation/index.md#evaluate-a-table-of-requests).

### How is BatchRuleResult different from RuleResult?

`BatchRuleResult` contains rows from several contexts. Its `count` counts returned rows, not successful contexts; `best_matches`, `counts_per_context` and the matched/unmatched ID accessors expose per-context information. `for_context(id)` returns an ordinary `RuleResult` over one context's retained rows. Context identity remains explicit throughout.

Read [BatchRuleResult Class](chapters/06-batch-evaluation/index.md#read-the-result-as-a-batch).

### How does evaluate_batch() rank rules independently for each context without window functions?

The implementation sorts by context ID and policy keys, assigns a global row index, computes each context group's minimum index, joins those group bases back, and subtracts the base to obtain a 1-based per-context rank. It does not create one Python result frame per context or require a backend window function for that calculation.

Read [Per-Context Ranking](chapters/06-batch-evaluation/index.md#rank-matches-within-each-request).

### What is chunked batch evaluation for, and does it change results?

`chunk_size` limits how many prepared contexts enter each cross join. The complete projection is prepared first, and successful chunk results are concatenated. Chunking does not reduce the total matching work or promise a speedup. The chapter demonstrates whole/chunked equality for its workload and aggregation of policy violations across chunks; dtype and selection boundaries still apply.

Read [Chunked Batch Evaluation](chapters/06-batch-evaluation/index.md#work-in-smaller-chunks).

### How are batch contexts prepared and conformed to the rules backend?

Preparation resolves context fields, generates or validates non-null unique context IDs, fills typed missing values and projects needed columns. Boolean absence remains null in both scalar and batch paths. Identity validation covers the complete input before chunking. The prepared contexts relation is then conformed to the rules backend before joining.

Read [Batch Context Preparation](chapters/06-batch-evaluation/index.md#keep-each-context-identifiable).

### How do hit policies and filters apply in a batch?

Ordering, policy assertions and returned cardinality are per context. `min_specificity` removes weak rows, and `top_n_per_context` limits preassigned rank positions rather than renumbering survivors after filtering. A `UNIQUE` or `ANY` violation is checked before those limits can hide it. Read the batch contract rather than assuming every scalar filter combination is interchangeable.

Read [Per-Context Ranking](chapters/06-batch-evaluation/index.md#rank-matches-within-each-request).

## AccumulatorEngine and Lattice

### What problem does the AccumulatorEngine solve that the ExpressionRulesEngine cannot?

The accumulator combines rule constraints and aggregates before a context is applied. It can represent several contributing rules as one compatible combination, with coalesced fields and provenance. Filtering individual rows alone does not construct that artifact or define how their constraints and values should combine.

Read [AccumulatorEngine](chapters/07-accumulator-engine/index.md#accumulatorengine).

### How does prime number encoding work in the AccumulatorEngine?

Each rule in a partition gets a distinct prime; a combination's identity is their product. Divisibility detects membership and subset relationships while the product remains within the guarded signed-int64 range. Attribution requires the original partition order or a separate prime-to-source mapping. Snapshots preserve the numeric products, not that mapping.

Read [Prime Number Encoding](chapters/10-accumulator-engine-internals/index.md#prime-number-encoding).

### What are the phases of lattice building?

Construction filters a partition, assigns prime identities, creates singleton anchors and expands them with compatible rules in canonical order. It concatenates the generated levels and removes dominated combinations with equivalent coalesced fingerprints. Without constraint dimensions there is no fingerprint, so all generated combinations remain; that configuration also cannot be applied. Applying a valid lattice happens later and is not another construction pass.

Read [Level Expansion](chapters/10-accumulator-engine-internals/index.md#level-expansion).

### What is the frontier filter and why is it needed?

The frontier filter removes a combination only when a strict superset of its rules has the same coalesced fingerprint. Different constraints can match different contexts and must remain distinguishable. With no constraint dimensions, the fingerprint is empty and pruning is bypassed entirely. The normal frontier is not simply the globally largest rule set or a list of maximal cliques.

Read [Frontier Filter](chapters/10-accumulator-engine-internals/index.md#frontier-filter).

### What are compatible and coalesce expressions?

A compatibility expression admits pairs under a strategy's combination rules; a coalesce expression represents their combined constraint. Supported exact values must agree unless wildcarded, ranges and membership sets intersect, and exclusion sets union. Every accumulator strategy needs both operations and correct unconstrained-state flags. Boolean-null `EXACT` wildcards do not currently have that complete build implementation.

Read [Compatible Expression](chapters/10-accumulator-engine-internals/index.md#compatible-expression).

### What is the Lattice class and what does it contain?

`Lattice` holds the combinations frame, dimension metadata, aggregate definitions and an optional partition key. Inspect its `count`, `combinations`, `is_composed`, metadata and aggregate properties as appropriate. Treat the frame as immutable: apply-phase engines are cached against the lattice object's identity.

Read [Lattice Class](chapters/07-accumulator-engine/index.md#lattice-class).

### What are coalesced columns and NA flag columns?

Coalesced columns store a combination's effective constraints. NA flags record whether those constraints remain wholly unconstrained. For ranges, inspect both bounds and the dimension's NA flag; one sentinel bound does not make the whole range unconstrained. These columns are construction outputs, not independent rule-authoring inputs to mutate after caching.

Read [Coalesced Columns](chapters/07-accumulator-engine/index.md#coalesced-columns).

### What is combination depth and why does it matter?

`__level` is zero-based: a singleton has level 0, a pair level 1 and a triple level 2. Thus contributing-rule count is level plus one. The result's `depths` accessor returns these stored levels, not an independently calculated rule count. Do not confuse combination depth with specificity or with the number of rows in a result.

Read [Combination Depth](chapters/08-lattices-results-and-routing/index.md#combination-depth).

### How do partition keys work in the AccumulatorEngine?

Correct isolation requires every context-key dimension and a non-`None` stored value. `build()` checks only for a truthy dictionary and skips filters for absent or `None` entries, which can mix intended partitions. `build_all()` discovers complete tuples but shares that `None` limitation, so avoid Boolean wildcard partitions. Without configured context-key dimensions, a supplied key is stored but does not filter rules. Application still requires at least one constraint dimension.

Read [Partition Key Filtering](chapters/08-lattices-results-and-routing/index.md#partition-key-filtering).

### What are the prime-table and int64 combination limits?

The prime table supports 10,000 rule identities per partition. Independently, any admitted combination's prime product must fit signed int64. Larger assigned primes can overflow with fewer rules, so there is no generally safe fifteen-rule threshold. Increasing the table cap does not enlarge the product type. Distinguish the resulting `IndexError` from `LatticeWidthExceededError`.

Read [Prime Table Size Cap](chapters/10-accumulator-engine-internals/index.md#prime-table-size-cap).

### How does the AccumulatorResult differ from RuleResult?

`AccumulatorResult` describes matching combinations and adds native-frame projections for accumulated values, prime-product provenance and zero-based levels. It also has `best_combination`, an alias for `best_match`. Apply results do not carry `SelectionInfo`, so the inherited `select()` method raises `ValueError`; use the supported accessors and frame filtering instead.

Read [AccumulatorResult Class](chapters/07-accumulator-engine/index.md#accumulatorresult-class).

### What is the Aggregate model and how is it used?

`Aggregate` names a source column and one fold operation: sum, minimum, maximum or product. Construction seeds each singleton from its source value and folds values when rules combine. The model does not perform aggregation by itself. Aggregate arithmetic has backend-defined overflow behavior; the prime-product guard does not protect business values.

Read [Aggregate Model](chapters/07-accumulator-engine/index.md#the-aggregate-model).

### How do Lattice.save() and Lattice.load() provide persistence?

`save()` writes `lattice.parquet` and `manifest.yaml`; `load()` reads the frame and validates the stored dimension and aggregate models without rerunning construction. It does not establish that the frame agrees with the manifest. After loading, use an equivalently configured engine, such as `AccumulatorEngine(loaded.metadata, loaded.aggregates)`. Tracking columns and the partition key survive, but the prime-to-source mapping is not saved. Loading currently uses Polars rather than restoring the original backend.

Read [Lattice Save Method](chapters/08-lattices-results-and-routing/index.md#lattice-save-method).

### How does LatticeIndex route partitions, and how is it different from apply_auto()?

`apply_auto()` constructs routing metadata for an occasional call without exhaustive ambiguity validation. A reusable `LatticeIndex` keeps an exact-key lookup and a wildcard-aware meta-engine; its default construction validates ambiguity exhaustively within the witness limit. Equal-best matches still raise `AmbiguousPartitionError` at runtime if validation was disabled.

Read [LatticeIndex Router](chapters/08-lattices-results-and-routing/index.md#the-latticeindex-router).

## Practical Usage and Best Practices

### How do I define a simple rule evaluation with ExpressionRulesEngine?

Create a rules DataFrame and `DimensionsMetadata`, construct the engine through the metadata path, and pass a context to `evaluate()`. The worked chapter follows a discount library through survival, specificity, tie-breaking, result accessors and a no-match case.

Read [ExpressionRulesEngine](chapters/04-expression-rules-engine/index.md#construct-an-engine-and-evaluate-a-context).

### How do I set up the AccumulatorEngine for a benefits accumulation problem?

Declare supported constraint strategies and the payload columns to aggregate, build the lattice, then apply a context. Read the accumulated columns as values belonging to each matching combination. The worked service-task example shows a broad baseline alongside regional combinations and demonstrates all four aggregate operations.

Read [AccumulatorEngine](chapters/07-accumulator-engine/index.md#accumulatorengine).

### How should I choose between EXACT, RANGE, and other match strategies for a dimension?

Choose the predicate your data actually expresses: equality/inequality, an ordered interval or threshold, a string pattern, or membership in a list. Then check its allowed types, field layout and sentinel behavior. If the library will also feed the accumulator, choose from its supported subset or explicitly separate filter-only behavior.

Read [Match Strategy Patterns](chapters/02-shared-rule-model/index.md#rules-are-rows-comparisons-are-match-strategies).

### How do I make rules explainable for audit or compliance purposes?

Retain the rule library version, input context, selected policy and relevant result data in your application's own audit design. Use per-rule explanation for retained matches and engine-level explanation to diagnose all rules. The library supplies observability; it does not itself establish regulatory compliance or preserve your application's historical inputs.

Read [Engine-Level Explain](chapters/05-expression-results-and-policies/index.md#engine-level-explain-scoring-every-rule).

### What are common pitfalls when designing rule tables?

Common mistakes include confusing rule and context field names, using reserved values as legitimate data, assuming a missing context always means unknown, treating every null as a wildcard, choosing unsupported accumulator strategies, and leaving `ANY` output fields ambiguous. Validate metadata, inspect a small representative table and exercise the actual strategy/backend combinations you depend on.

Read [Dimension Validator](chapters/02-shared-rule-model/index.md#the-dimension-validator).

### How do I handle rules that should not combine in the AccumulatorEngine?

Express a genuine conflicting constraint or place rules in semantically separate partitions. If two rules are compatible according to all configured accumulator constraints, the engine has no hidden business reason to keep them apart. Do not abuse sentinel values, row order or aggregate values as an undocumented exclusion mechanism.

Read [Compatible Expression](chapters/10-accumulator-engine-internals/index.md#compatible-expression).

### How do I evaluate many contexts against the same rule table efficiently?

Reuse the constructed filter engine and consider `evaluate_batch()` for frame-based contexts. Choose chunk size from the size of the intermediate relation and the output you retain, not a universal benchmark. For accumulator workflows, reuse a built or loaded lattice and its apply path instead of rebuilding combinations for every request.

Read [Evaluate Batch Method](chapters/06-batch-evaluation/index.md#evaluate-a-table-of-requests).

### Can I use both engines together in a single evaluation pipeline?

Yes, when your application has distinct decisions that justify both. The accumulator already uses filter-engine behavior internally when applying coalesced constraints, so not every workflow needs an extra filter pass. If you compose public results yourself, define the handoff explicitly: a selected individual rule row and a matching combination represent different objects.

Read [AccumulatorResult Class](chapters/07-accumulator-engine/index.md#accumulatorresult-class).

### What should I know about performance and scalability?

Measure the workload you intend to run. Filter work depends on rules, dimensions, contexts, strategies and backend behavior. Accumulator construction depends strongly on compatibility and the combinations that survive expansion; partition count alone is not enough. The integer-product limits are correctness bounds, not performance promises, and aggregate overflow is a separate concern.

Read [Level Expansion](chapters/10-accumulator-engine-internals/index.md#level-expansion).

### How does mountainash-rules handle null values in rule table columns?

Null handling is typed and strategy-specific. Boolean exact matching uses null as unknown; scalar sentinel-aware comparisons use their declared reserved values; set normalization can turn a whole-cell null into the canonical wildcard list. Null list elements and reserved sentinels inside ordinary sets have their own validation rules. Do not normalize every column with one blanket replacement.

Read [Set Value Normalization](chapters/03-matching-concepts/index.md#set-value-normalization).

### How do I validate that my DimensionsMetadata and rule table are consistent?

Validate the metadata model, then separately check that every required rule field exists with the intended dtype and sentinel representation. `RANGE` requires its two bound columns rather than treating `resolved_rule_field` as a data column. Check reserved engine names and run a representative evaluation. Pydantic configuration success is not proof that the table satisfies the business contract.

Read [Dimension Validator](chapters/02-shared-rule-model/index.md#the-dimension-validator).

## Input boundaries and extension work

### Does a missing context always produce UNKNOWN?

No. A missing context normally produces unknown for equality, range, threshold, row-based string and set comparisons. `EXACT_KEY` rejects it against a concrete rule key but permits a rule wildcard; `CONTEXT_REGEX` rejects it for every rule. Required-field validation remains an application decision.

Read [Ternary Logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

### How are missing Boolean values handled in scalar and batch evaluation? {#why-do-missing-boolean-batch-inputs-differ-from-scalar-evaluation}

Both paths preserve Boolean absence as null. For Boolean `EXACT` and `NOT_EQUAL`, a null context produces unknown against both concrete rule values. `EXACT_KEY` rejects a null context against a concrete rule, while a null rule remains a wildcard. Missing input is distinct from the concrete value `False`.

Read [Bool Ternary Comparison](chapters/03-matching-concepts/index.md#bool-ternary-comparison).

### What must change when I add a filter strategy?

Add its enum value, configuration validation and compiler dispatch, then prove the ternary behavior on the backends you support. A strategy using several rule columns also needs those columns classified correctly for inferred ANY output fields. The extension recipe names these separate integration points so a comparison does not work while selection silently misinterprets its inputs.

Read [Adding a filter strategy](chapters/11-extending-and-maintaining/index.md#recipe-add-a-filter-match-strategy).

### Must every filter strategy also support the accumulator?

No. A filter predicate can be complete without pairwise combination semantics. Accumulator support requires compatible, coalesce and unconstrained-state behavior that preserves the combined constraint, including order-independent meaning. Unsupported strategies should retain their explicit error rather than silently fall back to a different operation.

Read [Adding accumulator compatibility](chapters/11-extending-and-maintaining/index.md#recipe-make-a-strategy-accumulator-compatible).

### What makes a valid aggregate extension?

The current model is a binary fold over one source column, seeded from the first contributing rule. Check the operation, seed, valid value domain and order-independent meaning. A running average needs additional state and is not obtained by repeatedly averaging two values. Preserve the documented distinction between aggregate arithmetic and guarded combination identity.

Read [Adding an aggregate operation](chapters/11-extending-and-maintaining/index.md#recipe-add-an-aggregate-operation).

### Where must a new hit policy be implemented?

Update the enum and the relevant shared ordering, assertion and cardinality logic. Also update the batch path: it shares ordering but implements per-context assertions and truncation separately. Add companion metadata validation if needed and preserve the deterministic final rule-index tie-break. A scalar-only change does not complete the batch contract.

Read [Adding a hit policy](chapters/11-extending-and-maintaining/index.md#recipe-add-a-hit-policy).

### What does the backend-purity test actually prove?

It checks source import lines for prohibited native backend packages unless an accepted same-line allowance is present. It does not prove behavioral portability, detect every indirect native call or enforce internal dependency direction. Review exception reasons and exercise supported backends; a passing import guard is only one part of that evidence.

Read [Backend Purity Enforcement](chapters/11-extending-and-maintaining/index.md#backend-purity-enforcement).

### Can older code read newly serialized enum values?

Not necessarily. New code may remain able to read old artifacts while old code rejects an enum value introduced later. Match strategies and hit policies appear in dimension metadata; aggregate operations also appear in lattice manifests. Renaming or removing an existing string can break saved libraries. Review compatibility in both directions, not just whether serialization succeeds today.

Read [YAML Round-Trip](chapters/02-shared-rule-model/index.md#yaml-round-trip).
