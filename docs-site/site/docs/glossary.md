# Glossary

Definitions are alphabetical. Each entry links to the chapter that explains the term in context.

## Accumulator compiler

The private exact-accumulator pipeline that normalizes admitted source rows, compiles predicates, discovers scopes, produces disjoint cells, folds declared outputs, and retains the evidence needed for strict restoration. It is not a public pairwise-combination API.

Read [Inside the Exact Accumulator](chapters/10-accumulator-engine-internals/index.md).

## AccumulatorEngine

The engine that compiles source-validated rows into immutable exact cells and resolves normalized contexts under contract/profile bindings. Construction, source analysis, persistence, and resolution are separate bounded operations.

Read [The bounded lifecycle](chapters/10-accumulator-engine-internals/index.md#the-bounded-lifecycle).

## AccumulatorResult

The typed result of resolving one normalized context against a contract-bound exact lattice. It exposes the outcome, selected cell ID, decoded outputs, contributor UUIDs when definite, observations, issues, and non-promoting candidate inspection.

Read [Contract-bound resolution](chapters/10-accumulator-engine-internals/index.md#contract-bound-resolution).

## Active dimensions

The logical dimensions selected for one evaluation. Their ternary outcomes determine survival and specificity, and their names identify the per-dimension explanation columns. An inactive dimension does not contribute to that evaluation's score.

Read [Active Dimensions](chapters/04-expression-rules-engine/index.md#active_dimensions).

## Advanced construction

The filter-engine construction path in which the caller supplies a dictionary of compiled dimension expressions instead of dimension metadata. The caller then owns the expression and context-column contract that metadata-driven compilation would otherwise establish.

Read [Convenience vs Advanced Path](chapters/04-expression-rules-engine/index.md#convenience-vs-advanced-construction).

## Aggregate

An immutable declaration of a source column, fold operation, and—when used for exact artifacts—namespaced output name, data type, `numeric-1` semantics, and any required timezone. It describes output meaning; compilation performs the fold.

Read [Numeric-1 output folds](chapters/10-accumulator-engine-internals/index.md#numeric-1-output-folds).

## AggregateOp

The enum selecting exact-native `sum`, `min`, `max`, or `product` folds. Complete aggregate declarations also name the source column, namespaced output, data type, and `numeric-1` semantics.

Read [Numeric-1 output folds](chapters/10-accumulator-engine-internals/index.md#numeric-1-output-folds).

## AmbiguousPartitionError

The routing error raised when multiple partitions tie at the best matching specificity. It is a KeyError subclass. Construction-time index validation can expose ambiguous routing before a request reaches the runtime selection path.

Read [Routed exact views](chapters/08-lattices-results-and-routing/index.md#routed-exact-views).

## ANY

The hit policy that requires survivors to agree on the selected business output fields, then retains one specificity-ranked representative. Agreement is an assertion over the relevant survivor set, not a rule for ignoring conflicting outputs.

Read [Any Policy](chapters/05-expression-results-and-policies/index.md#any-survivors-that-must-agree).

## Artifact binding

The immutable association between an exact artifact and complete portable evidence for a contract. `Lattice.with_binding(binding, evidence=..., limits=...)` validates that evidence and returns a new view; it does not modify the source lattice or grant unverified application permission.

Read [Persistence and native restoration](chapters/10-accumulator-engine-internals/index.md#persistence-and-native-restoration).

## Backend

The concrete execution system behind a relation or expression, such as a supported Polars, Narwhals or Ibis configuration. Sharing an abstraction does not make every operation, dtype or strategy portable across every backend.

Read [Backend-Agnostic Design](chapters/01-two-rule-engines/index.md#using-other-dataframe-libraries).

## Backend conforming

Conversion of a prepared contexts relation to the execution backend used by the rules relation before a batch join. It is a backend-alignment step, not a new interpretation of the dimension metadata.

Read [Backend Conforming](chapters/06-batch-evaluation/index.md#use-compatible-table-backends).

## Backend purity

The implementation discipline of keeping applications on the package-root API and isolating codec, predicate, layout, persistence, and native-bridge details. It does not by itself prove portability or a complete artifact lifecycle.

Read [Keep the public boundary small](chapters/11-extending-and-maintaining/index.md#keep-the-public-boundary-small).

## Batch context

One row of facts in the contexts frame supplied for batch evaluation. It has a context identifier so returned rule matches can be attributed to that row independently of other contexts.

Read [Batch Context Preparation](chapters/06-batch-evaluation/index.md#keep-each-context-identifiable).

## BatchRuleResult

The result wrapper for multiple evaluated contexts. It exposes returned rows, per-context counts and best matches, matched/unmatched identifiers, and an accessor that presents one context's retained rows as a RuleResult.

Read [BatchRuleResult Class](chapters/06-batch-evaluation/index.md#read-the-result-as-a-batch).

## Best match

The row with the minimum retained rank under the active policy’s ordering, exposed as a DataFrame. Earlier filtering can remove rank 1, so the best retained row may have a larger rank. An empty result has no winning row.

Read [Best Match Accessor](chapters/04-expression-rules-engine/index.md#best_match).

## Boolean ternary comparison

The filter compiler's equality-family comparison that uses null to represent an unrestricted Boolean rule or missing context. `EXACT` and `NOT_EQUAL` produce unknown when either operand is null. `EXACT_KEY` permits the rule wildcard but rejects a missing context against a concrete rule.

Read [Bool Ternary Comparison](chapters/03-matching-concepts/index.md#bool-ternary-comparison).

## Canonical evidence

The canonical, typed records that bind source material, domains, predicates, reports, approvals, bindings, and artifact identity. The exact accumulator validates these records before building, binding, or restoring executable state.

Read [Source analysis is separate from compilation](chapters/10-accumulator-engine-internals/index.md#source-analysis-is-separate-from-compilation).

## Cell identity

The stable `cell_id` that names an exact compiled predicate region. It is distinct from `predicate_id`, which identifies the region's predicate, and `contributor_set_id`, which identifies its source-UUID membership.

Read [Normalized predicates and disjoint cells](chapters/10-accumulator-engine-internals/index.md#normalized-predicates-and-disjoint-cells).

## Chunked evaluation

Batch evaluation that processes successive slices of prepared contexts before combining the results. It bounds the size of each context–rule join, not the total matching work, complete input projection or final output size.

Read [Chunked Batch Evaluation](chapters/06-batch-evaluation/index.md#work-in-smaller-chunks).

## COLLECT

The hit policy that retains all survivors in descending specificity order, with original rule position as the final tie-break. A complete, untruncated COLLECT result is the intended starting point when several policies will be reapplied.

Read [Collect Policy](chapters/05-expression-results-and-policies/index.md#collect-the-default-unfiltered-ranking).

## Compiled cell

One nonempty, disjoint predicate region in an exact lattice. A cell has `cell_id`, `predicate_id`, a contributor-set ID, and declared output values. It is not an ordered combination of source rows.

Read [Normalized predicates and disjoint cells](chapters/10-accumulator-engine-internals/index.md#normalized-predicates-and-disjoint-cells).

## Compiled predicate

The normalized predicate derived from an admitted source row or exact cell and interpreted in its declared domain. Predicate meaning is checked during source analysis, cell compilation, binding, and strict restoration.

Read [Normalized predicates and disjoint cells](chapters/10-accumulator-engine-internals/index.md#normalized-predicates-and-disjoint-cells).

## Contributor set

The source-UUID membership relation associated with an exact cell. The compiled layout stores that membership through a scope-local source map and dense 63-bit words; lineage exposes contributor identity without relying on source order.

Read [Scoped narrow state](chapters/10-accumulator-engine-internals/index.md#scoped-narrow-state).




## CONSTRAINT

The default dimension role for filter matching. In exact accumulator metadata, dimensions contribute to source normalization and predicate construction according to their declared strategy; exact cell construction is not a filter-engine matching pass.

Read [CONSTRAINT Role](chapters/02-shared-rule-model/index.md#constraint-role).

## CONTAINS

The case-sensitive string strategy that searches for a literal rule-side string anywhere in the context. A reserved string marker in either operand produces unknown; the dot character has no regex meaning under this strategy.

Read [CONTAINS Strategy](chapters/03-matching-concepts/index.md#contains-strategy).

## Context

The supplied facts against which a rule library is evaluated. A scalar context is commonly a dictionary or Pydantic model; a batch holds contexts as rows. Metadata resolves the fields used for each logical dimension.

Read [Context Object](chapters/01-two-rule-engines/index.md#describe-one-delivery).

## Context binding

Creation of the columns referenced by compiled context expressions. Scalar evaluation broadcasts extracted values across rules; batch preparation projects context columns before joining. With dimension metadata, Boolean absence remains null and other declared types use typed `NOT_SET` sentinels; expressions-only scalar evaluation has no declared type and uses string `NOT_SET`.

Read [Context Binding Phase](chapters/09-expression-engine-internals/index.md#context-binding-phase).

## Context field

The physical input name from which a dimension reads its value, resolved from context_field or the logical dimension name. It can differ from the rule-side column and the name used in explanation output.

Read [Field Resolution](chapters/02-shared-rule-model/index.md#field-resolution).

## Context identifier

The non-null, unique label associating batch results with an input context. The engine stores it as `__context_id`, using a supplied identifier field or generated row positions. Supplied identifiers are validated across the complete input before chunking.

Read [Batch Context Preparation](chapters/06-batch-evaluation/index.md#keep-each-context-identifiable).

## CONTEXT_KEY

The dimension role used by the accumulator to partition construction and routing. It does not automatically disable filter-engine matching: a filter engine still evaluates the dimension's configured strategy unless the dimension is excluded from the call.

Read [CONTEXT_KEY Role](chapters/02-shared-rule-model/index.md#context_key-role).

## CONTEXT_REGEX

The strategy that applies one metadata-level regular expression to every rule’s context value. It produces a match for a concrete matching input and a non-match for other or missing inputs. There is no rule-side wildcard or unknown outcome.

Read [CONTEXT_REGEX Strategy](chapters/03-matching-concepts/index.md#context_regex-strategy).

## Cross join

The relational pairing of every row from one input with every row from another. Batch evaluation pairs contexts with rules; accumulator expansion pairs current combinations with candidate rules. Filtering happens after this candidate-pair construction.

Read [Cross-Join Evaluation](chapters/06-batch-evaluation/index.md#understand-the-work-behind-a-batch).

## CTX_PREFIX

The internal prefix, __ctx_, used to name injected context columns by logical dimension. Compiled expressions reference those names. They belong to the engine's reserved namespace rather than the application's rule or context schema.

Read [CTX_PREFIX Column Injection Pattern](chapters/09-expression-engine-internals/index.md#ctx_prefix-column-injection-pattern).

## DataFrame rule store

A table whose rows carry rule conditions and outputs while separate metadata defines their interpretation. Storing rules as rows does not itself specify comparison, wildcard, selection or combination semantics.

Read [DataFrame as Rule Store](chapters/01-two-rule-engines/index.md#put-the-rules-in-a-table).

## DataType

The enum declaring a dimension's string, integer, float, Boolean, date or datetime type. It guides validation, sentinel selection and compilation; it does not by itself convert or validate every value in the underlying rule table.

Read [DataType Enum](chapters/02-shared-rule-model/index.md#the-datatype-enum).

## Dimension

The configuration model for one logical matching dimension, combining its strategy, role, data type and physical field mappings. A range dimension uses two rule-side bounds rather than a single comparison column.

Read [Dimension Class](chapters/02-shared-rule-model/index.md#the-dimension-class).

## DimensionCompiler

The compiler translating dimension metadata into expression objects for filter evaluation. Its dispatch selects strategy-specific ternary behavior; evaluation later supplies the rule and context columns those expressions reference.

Read [DimensionCompiler](chapters/09-expression-engine-internals/index.md#dimensioncompiler).

## DimensionsMetadata

The configuration container for a library's dimensions and table-level selection settings. It supports lookup and YAML persistence. It contains neither the rule rows themselves nor a complete validation of their business meaning.

Read [DimensionsMetadata](chapters/02-shared-rule-model/index.md#dimensionsmetadata).

## EXACT

The sentinel-aware equality strategy for comparable scalar values. Recognized unknown operands yield ternary zero rather than an ordinary equality result. Boolean exact matching uses its own null-aware compilation branch.

Read [EXACT Strategy](chapters/03-matching-concepts/index.md#exact-strategy).

## EXACT_KEY

The asymmetric exact strategy used for partition routing. A rule-side UNKNOWN value acts as a wildcard, while a missing context is not wildcarded. Its raw-column conditional differs from the generic EXACT ternary-column comparison.

Read [EXACT_KEY Strategy](chapters/03-matching-concepts/index.md#exact_key-strategy).

## ExplainResult

The wrapper returned by engine-level explain(), containing every rule scored before selection. It exposes survival and specificity information without assuming that rows are filtered, ranked or governed by a hit policy.

Read [ExplainResult Class](chapters/05-expression-results-and-policies/index.md#the-explainresult-class).

## Expression

A representation of a computation to be executed by a backend, rather than an already-computed scalar result. Mountainash expressions describe column operations and can be composed into the compiler's matching and scoring transformations.

Read [Mountainash Expressions](chapters/02-shared-rule-model/index.md#mountainash-expressions-what-to-compute).

## ExpressionRulesEngine

The filter engine that evaluates rule rows against a context or batch, computes matching information and applies hit-policy selection. It also provides an unfiltered explanation path for diagnosing all rules.

Read [ExpressionRulesEngine](chapters/04-expression-rules-engine/index.md#construct-an-engine-and-evaluate-a-context).

## Field resolution

The mapping from a logical dimension to its actual rule and context fields. Explicit mappings allow different names on each side; range bounds are resolved separately from the scalar rule-field convention.

Read [Field Resolution](chapters/02-shared-rule-model/index.md#field-resolution).

## FIRST

The hit policy selecting the earliest surviving rule in the supplied input order. It does not prefer greater specificity. When no rule survives, it returns no winner rather than manufacturing a default.

Read [First And Priority Policy](chapters/05-expression-results-and-policies/index.md#first-and-priority-pick-a-single-winner).


## GREATER_THAN

The strict threshold strategy requiring the context value to exceed the rule value. Numeric and temporal types are supported by the metadata contract. Sentinel-aware comparison preserves the distinction between a known result and unknown.

Read [GREATER_THAN Strategy](chapters/03-matching-concepts/index.md#greater_than-strategy).

## Hit policy

The selection contract applied after matching: survivor ordering, assertions about the survivor set and returned cardinality. It changes how eligible rows are interpreted and selected, not the dimension predicate that made them eligible.

Read [HitPolicy Enum](chapters/05-expression-results-and-policies/index.md#choose-and-reapply-a-hit-policy).

## HitPolicyViolationError

The ValueError subclass reporting a violated UNIQUE or ANY assertion, with the policy and offending frame attached. It represents an inconsistent selection contract, not a normal no-match result.

Read [HitPolicyViolationError](chapters/05-expression-results-and-policies/index.md#hitpolicyviolationerror).


## Lattice

An immutable exact artifact with cells, contributors, source evidence, metadata, aggregate declarations, partition identity, and bindings. It can be saved, strictly restored, and resolved only when its exact state and required limits are present. Flat construction is inspection-only.

Read [Persistence and native restoration](chapters/10-accumulator-engine-internals/index.md#persistence-and-native-restoration).

## LatticeIndex

A reusable router over coherent exact lattices. It checks lattice and binding coherence, takes an exact-key fast path when possible, and raises `KeyError` for a missing route or `AmbiguousPartitionError` for an admissible best-specificity tie.

Read [Contract-bound resolution](chapters/10-accumulator-engine-internals/index.md#contract-bound-resolution).


## LESS_THAN

The strict threshold strategy requiring the context value to fall below the rule value. It mirrors GREATER_THAN with the comparison direction reversed and uses the same typed, sentinel-aware comparison approach.

Read [LESS_THAN Strategy](chapters/03-matching-concepts/index.md#less_than-strategy).

## Metadata serialization

Encoding and reconstructing configuration values, such as dimension metadata in YAML. Serialization preserves declared values, not the application's entire runtime state, and enum-string compatibility matters when artifacts move between code revisions.

Read [YAML Round-Trip](chapters/02-shared-rule-model/index.md#yaml-round-trip).

## Native restoration

The bounded reconstruction of an executable exact artifact from persisted typed relations and canonical evidence. Restoration validates manifest records, source/cell/layout integrity, and applicable semantic evidence; it does not promote malformed or flat data to an executable lattice.

Read [Persistence and native restoration](chapters/10-accumulator-engine-internals/index.md#persistence-and-native-restoration).

## NOT_EQUAL

The sentinel-aware inequality strategy, reversing the known equality outcome while retaining unknown handling. Its filter behavior does not establish an exact-accumulator predicate contract on its own.

Read [NOT_EQUAL Strategy](chapters/03-matching-concepts/index.md#not_equal-strategy).

## NOT_SET

The context-side missing-value convention, distinct from a rule author's UNKNOWN wildcard. Its representation is typed for scalar extraction, with separate Boolean handling. A stored NOT_SET value does not force ternary zero in every strategy or execution path.

Read [Sentinel Values](chapters/03-matching-concepts/index.md#sentinel-values-telling-no-constraint-apart-from-no-answer).

## Observability columns

The per-dimension __t_ columns and related scoring information used to inspect evaluation. Ordinary results can omit per-dimension ternaries; per-rule explanation then lacks the data it requires. These names are reserved for engine use.

Read [Observability Columns](chapters/04-expression-rules-engine/index.md#observability-columns).

## Partition

A declared source-validated slice selected by the full tuple of `CONTEXT_KEY` dimensions. Each exact lattice represents one such identity; routing selects among coherent artifact views before contract-bound resolution.

Read [Source analysis is separate from compilation](chapters/10-accumulator-engine-internals/index.md#source-analysis-is-separate-from-compilation).

## Partition key

The context-key assignment recorded for one exact lattice partition. A single-partition build must name every configured context-key dimension and match a source-validated key; the key does not replace contract-bound resolution.

Read [Source analysis is separate from compilation](chapters/10-accumulator-engine-internals/index.md#source-analysis-is-separate-from-compilation).

## PREFIX

The case-sensitive string strategy that checks whether a context starts with the rule-side value. A rule wildcard or missing context produces unknown. The stored prefix can be shorter than the complete context string.

Read [PREFIX Strategy](chapters/03-matching-concepts/index.md#prefix-strategy).


## PRIORITY

The hit policy choosing a survivor by descending priority, then descending specificity and ascending input rule position. It requires a priority field and retains at most one row after the applicable selection stages.

Read [First And Priority Policy](chapters/05-expression-results-and-policies/index.md#first-and-priority-pick-a-single-winner).

## Provenance

The source-UUID contributor information associated with an exact cell or a definite accumulator outcome. `lattice.contributors`, `lattice.lineage(cell_id)`, and result lineage retain that identity without requiring source order or a numeric encoding.

Read [Normalized predicates and disjoint cells](chapters/10-accumulator-engine-internals/index.md#normalized-predicates-and-disjoint-cells).

## RANGE

The strategy comparing a context value with configured lower and upper rule bounds, then combining their ternary outcomes. Inclusivity is configured per bound. An unspecified bound can leave a surviving comparison unknown rather than fully specific.

Read [RANGE Strategy](chapters/03-matching-concepts/index.md#range-strategy).

## Rank

The one-based position assigned under the active hit policy's ordering. Equal specificity does not mean equal rank, and filters applied after ranking can leave gaps. Batch ranks are calculated independently within each context identifier.

Read [Rank Assignment](chapters/04-expression-rules-engine/index.md#rank-ordering-and-breaking-ties).

## REGEX

The strategy reading a regular-expression pattern from each rule row and applying it to the context string. Its current compiler uses a documented Polars-native expression, unlike metadata-literal CONTEXT_REGEX.

Read [REGEX Strategy](chapters/03-matching-concepts/index.md#regex-strategy).

## Relation

Mountainash's table-operation abstraction over supported concrete backends. It provides operations such as projection, filtering, joining, sorting and collection, allowing engine code to describe work without directly using each backend's table API.

Read [Mountainash Relations](chapters/02-shared-rule-model/index.md#mountainash-relations-where-and-how-it-executes).

## Rule column

A physical field in the rule table, used either as a dimension input or as output data. Metadata classifies matching fields; selection output inference must not mistake an extra constraint column for a business output.

Read [Field Resolution](chapters/02-shared-rule-model/index.md#field-resolution).

## RULE_ORDER

The hit policy retaining survivors in their original input order rather than sorting them by specificity. Matching still determines eligibility; the score does not determine this policy's row ordering.

Read [Rule Order Policy](chapters/05-expression-results-and-policies/index.md#rule_order-preserve-declaration-order).

## RuleResult

The wrapper around the rows retained by single-context matching and selection. Its accessors expose those rows and their matching information; it is not an audit of rules already eliminated or removed from the result.

Read [RuleResult Class](chapters/04-expression-rules-engine/index.md#read-the-result-contract).

## SelectionInfo

The internal record carrying dimension-input fields, priority/output settings and result-state information used by policy selection. Re-selection also needs the relevant rows and columns; the record cannot reconstruct data that was already discarded.

Read [SelectionInfo Dataclass](chapters/09-expression-engine-internals/index.md#selectioninfo-dataclass).

## Sentinel

A reserved stored value used to encode a special state such as a rule wildcard or missing input. A sentinel is data representation, whereas unknown is a comparison outcome; their relationship is defined by each strategy.

Read [Sentinel Values](chapters/03-matching-concepts/index.md#sentinel-values-telling-no-constraint-apart-from-no-answer).

## Set normalization

Conversion of a set-valued rule cell to the shared canonical representation used for matching and comparison. Concrete lists are deduplicated and sorted; whole-cell null handling and reserved sentinel validation are explicit parts of the contract.

Read [Set Value Normalization](chapters/03-matching-concepts/index.md#set-value-normalization).

## Set wildcard

The one-element list containing the element type's UNKNOWN sentinel, used to represent an unconstrained set rule. It is not an ordinary set member or an arbitrary empty list; mixed reserved values are subject to validation.

Read [Set Wildcard Sentinel](chapters/03-matching-concepts/index.md#set-wildcard-sentinel).

## SET_EXCLUSION

The strategy accepting a scalar context value when it is absent from the rule's list. It shares set wildcard normalization with `SET_MEMBERSHIP`; filter matching remains independent of exact-accumulator predicate compilation.

Read [SET_EXCLUSION Strategy](chapters/03-matching-concepts/index.md#set_exclusion-strategy).

## SET_MEMBERSHIP

The strategy accepting a scalar context value when it occurs in the rule's list. A canonical wildcard list represents an unconstrained rule. Filter matching remains independent of exact-accumulator predicate compilation.

Read [SET_MEMBERSHIP Strategy](chapters/03-matching-concepts/index.md#set_membership-strategy).

## Snapshot

A persisted exact artifact whose typed native relations, canonical evidence, source records, cells, layouts, and bindings are restored under explicit limits. A successful executable load requires strict restoration; a flat or legacy lattice remains inspection-only.

Read [Persistence and native restoration](chapters/10-accumulator-engine-internals/index.md#persistence-and-native-restoration).

## Specificity

The count of active dimensions whose ternary outcome is a hard match. It is not a probability or a contributing-rule count. Some policies use it for ordering; others deliberately use different priorities.

Read [Specificity Scoring](chapters/04-expression-rules-engine/index.md#specificity-how-many-dimensions-matched).

## SUFFIX

The case-sensitive string strategy that checks whether a context ends with the rule-side value. Rule wildcards and missing contexts produce unknown, following the same reserved-string handling as `PREFIX` and `CONTAINS`.

Read [SUFFIX Strategy](chapters/03-matching-concepts/index.md#suffix-strategy).

## Survival

The eligibility condition that no active dimension reports a contradiction. A rule can survive with unknown outcomes and low specificity. Subsequent hit-policy assertions, ordering and truncation still determine whether it is returned.

Read [Survival Computation](chapters/04-expression-rules-engine/index.md#understand-survival-and-ranking).

## Survivor

A rule or combination that passes the active matching conditions. A returned survivor frame may contain only a policy-selected or filtered subset of all eligible rows; distinguish matching survival from final retention.

Read [Survivors Accessor](chapters/04-expression-rules-engine/index.md#survivors).

## Temporal sentinel

A reserved year-one date or datetime value used for UNKNOWN or NOT_SET in a temporal dimension. It preserves the declared temporal type; a string or numeric sentinel is not an interchangeable replacement.

Read [Temporal Sentinels](chapters/03-matching-concepts/index.md#temporal-sentinels).

## Ternary AND

The three-valued conjunction used to combine range-bound comparisons: a contradiction wins, two hard matches produce a hard match, and remaining combinations containing unknown produce unknown. This distinguishes surviving a bound from proving both bounds matched.

Read [Ternary Logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

## Ternary logic

The matching system with hard match, unknown and contradiction represented as 1, 0 and -1. It lets the engine distinguish confirmed agreement from non-contradiction without interpreting unknown as an ordinary Boolean true.

Read [Ternary Logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

## Threshold

A single ordered rule value used as a strict lower or upper condition on the context. The filter compiler maps `GREATER_THAN` and `LESS_THAN` to the corresponding ternary comparison.

Read [GREATER_THAN Strategy](chapters/03-matching-concepts/index.md#greater_than-strategy).

## Truncation

Loss of candidate rows through filtering, limiting or one-row selection. A result recorded as incomplete cannot safely reapply a policy over the original survivor population. Retaining a complete `COLLECT` result preserves the candidates needed for later selection.

Read [RuleResult Select Method](chapters/05-expression-results-and-policies/index.md#reapplying-a-policy-with-ruleresultselect).

## Unbounded range

A range with an unspecified lower or upper bound represented by the supported sentinel convention. This concerns whether a bound is present, not whether a present endpoint is inclusive. Inspect the resulting ternary outcome rather than assuming full specificity.

Read [RANGE Strategy](chapters/03-matching-concepts/index.md#range-strategy).

## UNIQUE

The hit policy asserting that at most one rule survives. Two survivors violate it even if their business outputs are identical; output agreement belongs to ANY. Zero survivors do not violate uniqueness.

Read [Unique Policy](chapters/05-expression-results-and-policies/index.md#unique-assert-at-most-one-survivor).

## UNKNOWN outcome

The ternary value zero: the comparison did not establish either a hard match or a contradiction. It contributes no specificity and does not alone eliminate a rule. Its cause must be interpreted from the strategy and inputs.

Read [Ternary Logic](chapters/03-matching-concepts/index.md#ternary-logic-match-unknown-and-non-match).

## UNKNOWN sentinel

The reserved rule-side representation of an unconstrained value where the strategy supports it. It is distinct from the numeric UNKNOWN outcome and from the context-side NOT_SET representation; typed helper functions provide the actual stored value.

Read [Sentinel Values](chapters/03-matching-concepts/index.md#sentinel-values-telling-no-constraint-apart-from-no-answer).

## Vectorization

Expression of matching as operations over columns or relations instead of an application-level loop over individual rows. It changes execution structure and can exploit backend capabilities; it does not remove work proportional to the data being processed.

Read [Vectorized Evaluation](chapters/01-two-rule-engines/index.md#comparing-rules-as-columns).

## Wildcard

A rule-side representation that leaves a supported matching condition unconstrained. Its encoding and interpretation depend on dtype and strategy. It is not a universal synonym for null, missing context or the literal string NOT_SET.

Read [Sentinel Values](chapters/03-matching-concepts/index.md#sentinel-values-telling-no-constraint-apart-from-no-answer).
