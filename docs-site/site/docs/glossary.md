# Glossary

Definitions are alphabetical. Each entry links to the chapter that explains the term in context. Stored values, matching outcomes and result interfaces are kept distinct; similarly named APIs are not necessarily interchangeable.

## AccumulatorCompiler

The compiler that constructs pairwise compatibility, coalescing and unconstrained-state expressions for accumulator constraints. Its supported strategy subset is narrower than the filter compiler's because combining two rules requires more than evaluating one predicate.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## AccumulatorEngine

The engine that builds compatible rule combinations and their aggregates into a lattice, then applies contexts to that artifact. Construction and application are separate operations with different costs and limits.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## AccumulatorResult

The result of applying a context to a lattice. It extends the ordinary result interface with aggregate-column access, prime-product provenance and stored combination levels; its rows represent combinations rather than individual source rules.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## Active dimensions

The logical dimensions selected for one evaluation. Their ternary outcomes determine survival and specificity, and their names identify the per-dimension explanation columns. An inactive dimension does not contribute to that evaluation's score.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Advanced construction

The filter-engine construction path in which the caller supplies a dictionary of compiled dimension expressions instead of dimension metadata. The caller then owns the expression and context-column contract that metadata-driven compilation would otherwise establish.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Aggregate

A configuration model naming a source column and the operation used to fold its values across a rule combination. It describes aggregation; lattice construction performs the actual fold.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## AggregateOp

The enum selecting sum, minimum, maximum or product accumulation. The current operations use a single-column fold. Their business-value arithmetic is separate from the checked multiplication used for combination identity.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## AmbiguousPartitionError

The routing error raised when multiple partitions tie at the best matching specificity. It is a KeyError subclass. Construction-time index validation can expose ambiguous routing before a request reaches the runtime selection path.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## ANY

The hit policy that requires survivors to agree on the selected business output fields, then retains one specificity-ranked representative. Agreement is an assertion over the relevant survivor set, not a rule for ignoring conflicting outputs.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Apply-phase caching

Reuse of a filter engine prepared for a particular lattice during repeated accumulator application. The cache depends on lattice identity, which is why callers must not mutate the lattice's exposed combinations frame.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## Backend

The concrete execution system behind a relation or expression, such as a supported Polars, Narwhals or Ibis configuration. Sharing an abstraction does not make every operation, dtype or strategy portable across every backend.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Backend conforming

Conversion of a prepared contexts relation to the execution backend used by the rules relation before a batch join. It is a backend-alignment step, not a new interpretation of the dimension metadata.

Read [Scoring Batches of Contexts](chapters/04-scoring-batches/index.md).

## Backend purity

The implementation discipline of using Mountainash abstractions rather than unreviewed native backend dependencies. The repository's import-line test enforces part of that discipline with documented exceptions; it does not prove complete behavioral portability.

Read [Extending and Maintaining the Engines](chapters/08-extending-and-maintaining/index.md).

## Batch context

One row of facts in the contexts frame supplied for batch evaluation. It has a context identifier so returned rule matches can be attributed to that row independently of other contexts.

Read [Scoring Batches of Contexts](chapters/04-scoring-batches/index.md).

## BatchRuleResult

The result wrapper for multiple evaluated contexts. It exposes returned rows, per-context counts and best matches, matched/unmatched identifiers, and an accessor that presents one context's retained rows as a RuleResult.

Read [Scoring Batches of Contexts](chapters/04-scoring-batches/index.md).

## Best match

The first row retained under the active policy's ordering, exposed as a DataFrame rather than a dictionary. It is not necessarily the highest-specificity rule, and an empty result has no winning row.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Boolean ternary comparison

The special exact/inequality compilation path in which a null rule or scalar context produces unknown, because neither True nor False can be reserved as an in-band wildcard. Batch missing-value preparation has a documented different boundary.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Canonical ordering

The accumulator expansion rule that admits a candidate only after the last-added rule in increasing prime order. It prevents the same rule set being generated through every permutation; it does not eliminate the need for order-independent merge semantics.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## Chunked evaluation

Batch evaluation that processes successive slices of prepared contexts before combining the results. It bounds the size of each context–rule join, not the total matching work, complete input projection or final output size.

Read [Scoring Batches of Contexts](chapters/04-scoring-batches/index.md).

## Coalesce expression

An expression that merges two compatible constraints into their combined constraint. The operation depends on strategy: examples include choosing a concrete exact value, intersecting ranges and membership sets, or unioning exclusion sets.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## Coalesced column

A lattice column containing the effective constraint of a rule combination rather than one source rule's original value. Coalesced fields and their NA flags form the fingerprint used to distinguish matching behavior during frontier filtering.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## COLLECT

The hit policy that retains all survivors in descending specificity order, with original rule position as the final tie-break. A complete, untruncated COLLECT result is the intended starting point when several policies will be reapplied.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Combination depth

The size of a contributing rule set, represented internally by the zero-based __level column. A singleton is level 0, so contributing-rule count is level plus one. The depths accessor returns stored levels.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## Combination identity

The product of the distinct primes assigned to the rules in one combination. Within the partition's assignment, it identifies the rule set and supports divisibility checks, provided the product stays within its guarded integer range.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## Compatible expression

The pairwise predicate deciding whether two accumulator constraints can be combined under the strategy's implemented semantics. Every constraint dimension must permit the extension before the engine coalesces values and accumulates their aggregates.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## CONSTRAINT

The default dimension role for a matching condition. In accumulator construction it participates in compatibility and coalescing; in filter evaluation its compiled ternary outcome contributes to survival and specificity.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## CONTAINS

The string strategy that tests whether a rule-side string occurs within the bound context string. Its wrapper recognizes rule-side string sentinels; a missing context is not automatically converted into an unknown comparison outcome.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Context

The supplied facts against which a rule library is evaluated. A scalar context is commonly a dictionary or Pydantic model; a batch holds contexts as rows. Metadata resolves the fields used for each logical dimension.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Context binding

Creation of the context columns referenced by compiled expressions. Scalar evaluation broadcasts extracted values across rules; batch evaluation prepares context columns on a relation and joins them to rules. Missing-value behavior is not identical for every dtype.

Read [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md).

## Context field

The physical input name from which a dimension reads its value, resolved from context_field or the logical dimension name. It can differ from the rule-side column and the name used in explanation output.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Context identifier

The unique label associating batch result rows with an input context. The engine uses __context_id internally, either from a supplied identifier column or generated row positions. It is not a rule identifier or a survivor count.

Read [Scoring Batches of Contexts](chapters/04-scoring-batches/index.md).

## CONTEXT_KEY

The dimension role used by the accumulator to partition construction and routing. It does not automatically disable filter-engine matching: a filter engine still evaluates the dimension's configured strategy unless the dimension is excluded from the call.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## CONTEXT_REGEX

The strategy that applies one literal regex_pattern from dimension metadata to the context value for every rule. It is distinct from per-row REGEX and has no unknown branch or rule-side wildcard pattern.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Cross join

The relational pairing of every row from one input with every row from another. Batch evaluation pairs contexts with rules; accumulator expansion pairs current combinations with candidate rules. Filtering happens after this candidate-pair construction.

Read [Scoring Batches of Contexts](chapters/04-scoring-batches/index.md).

## CTX_PREFIX

The internal prefix, __ctx_, used to name injected context columns by logical dimension. Compiled expressions reference those names. They belong to the engine's reserved namespace rather than the application's rule or context schema.

Read [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md).

## DataFrame rule store

A table whose rows carry rule conditions and outputs while separate metadata defines their interpretation. Storing rules as rows does not itself specify comparison, wildcard, selection or combination semantics.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## DataType

The enum declaring a dimension's string, integer, float, Boolean, date or datetime type. It guides validation, sentinel selection and compilation; it does not by itself convert or validate every value in the underlying rule table.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Dimension

The configuration model for one logical matching dimension, combining its strategy, role, data type and physical field mappings. A range dimension uses two rule-side bounds rather than a single comparison column.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## DimensionCompiler

The compiler translating dimension metadata into expression objects for filter evaluation. Its dispatch selects strategy-specific ternary behavior; evaluation later supplies the rule and context columns those expressions reference.

Read [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md).

## DimensionsMetadata

The configuration container for a library's dimensions and table-level selection settings. It supports lookup and YAML persistence. It contains neither the rule rows themselves nor a complete validation of their business meaning.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## EXACT

The sentinel-aware equality strategy for comparable scalar values. Recognized unknown operands yield ternary zero rather than an ordinary equality result. Boolean exact matching uses its own null-aware compilation branch.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## EXACT_KEY

The asymmetric exact strategy used for partition routing. A rule-side UNKNOWN value acts as a wildcard, while a missing context is not wildcarded. Its raw-column conditional differs from the generic EXACT ternary-column comparison.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## ExplainResult

The wrapper returned by engine-level explain(), containing every rule scored before selection. It exposes survival and specificity information without assuming that rows are filtered, ranked or governed by a hit policy.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Expression

A representation of a computation to be executed by a backend, rather than an already-computed scalar result. Mountainash expressions describe column operations and can be composed into the compiler's matching and scoring transformations.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## ExpressionRulesEngine

The filter engine that evaluates rule rows against a context or batch, computes matching information and applies hit-policy selection. It also provides an unfiltered explanation path for diagnosing all rules.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Field resolution

The mapping from a logical dimension to its actual rule and context fields. Explicit mappings allow different names on each side; range bounds are resolved separately from the scalar rule-field convention.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## FIRST

The hit policy selecting the earliest surviving rule in the supplied input order. It does not prefer greater specificity. When no rule survives, it returns no winner rather than manufacturing a default.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Frontier filter

The accumulator step removing a combination when a strict rule-set superset has the same coalesced fingerprint. Different fingerprints remain distinct even when one rule set contains another; this is not unconditional subset pruning.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## GREATER_THAN

The strict threshold strategy requiring the context value to exceed the rule value. Numeric and temporal types are supported by the metadata contract. Sentinel-aware comparison preserves the distinction between a known result and unknown.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Hit policy

The selection contract applied after matching: survivor ordering, assertions about the survivor set and returned cardinality. It changes how eligible rows are interpreted and selected, not the dimension predicate that made them eligible.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## HitPolicyViolationError

The ValueError subclass reporting a violated UNIQUE or ANY assertion, with the policy and offending frame attached. It represents an inconsistent selection contract, not a normal no-match result.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Integer-product overflow

A combination identity exceeding the maximum signed-int64 value. The accumulator checks the exact prime multiplication before accepting it. This protection does not extend to business-value aggregates stored in separate columns.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## Lattice

The artifact containing constructed combinations, their dimension metadata, aggregate definitions and optional partition key. It can be applied repeatedly or persisted. Its exposed combinations frame must be treated as immutable by callers.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## LatticeIndex

A reusable router over a collection of lattices. It combines an exact-key lookup with wildcard-aware partition evaluation and can validate ambiguity before use. It selects a lattice; it does not combine rules across partitions.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## LatticeWidthExceededError

The build error indicating that an admitted combination's prime product cannot fit signed int64. Despite the name, its bound is the product of assigned primes, not a universally fixed number of rules or rows.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## LESS_THAN

The strict threshold strategy requiring the context value to fall below the rule value. It mirrors GREATER_THAN with the comparison direction reversed and uses the same typed, sentinel-aware comparison approach.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Metadata serialization

Encoding and reconstructing configuration values, such as dimension metadata in YAML. Serialization preserves declared values, not the application's entire runtime state, and enum-string compatibility matters when artifacts move between code revisions.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## NA flag

The lattice flag indicating whether a combined constraint remains wholly unconstrained. For a range, both bounds must be unconstrained; a single sentinel bound does not make the entire dimension NA.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## NOT_EQUAL

The sentinel-aware inequality strategy, reversing the known equality outcome while retaining unknown handling. It is supported by the filter compiler but has no pairwise accumulator implementation at the documented source revision.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## NOT_SET

The context-side missing-value convention, distinct from a rule author's UNKNOWN wildcard. Its representation is typed for scalar extraction, with separate Boolean handling. A stored NOT_SET value does not force ternary zero in every strategy or execution path.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Observability columns

The per-dimension __t_ columns and related scoring information used to inspect evaluation. Ordinary results can omit per-dimension ternaries; per-rule explanation then lacks the data it requires. These names are reserved for engine use.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Partition

A subset of rules sharing an accumulator context-key assignment and built independently of other assignments. Choosing a partition establishes the scope of combination search; routing later decides which lattice receives a context.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## Partition key

The mapping from accumulator context-key dimensions to the values identifying one lattice's partition. It is stored with the lattice and used by construction and routing, separately from the coalesced constraint columns.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## PREFIX

The string strategy checking whether the bound context begins with the rule-side value. A rule sentinel yields unknown; a missing string context is otherwise compared literally rather than automatically treated as a wildcard.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Prime

A distinct prime number assigned to a source rule within a partition. Multiplying assigned primes encodes a combination, and divisibility reveals membership. The number is an internal identity component, not the business rule's public name.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## Prime table

The lookup containing the first 10,000 primes used for per-partition rule identities at this revision. Its size caps assigned rules, independently of the signed-int64 bound on products formed from those primes.

Read [Inside Combination Search](chapters/07-combination-search-internals/index.md).

## PRIORITY

The hit policy choosing a survivor by descending priority, then descending specificity and ascending input rule position. It requires a priority field and retains at most one row after the applicable selection stages.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Provenance

The prime-product identities of matching combinations, exposed by AccumulatorResult. Decoding their business meaning requires the source partition's rule-to-prime assignment; the accessor itself does not return a ready-made list of rule names.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## RANGE

The strategy comparing a context value with configured lower and upper rule bounds, then combining their ternary outcomes. Inclusivity is configured per bound. An unspecified bound can leave a surviving comparison unknown rather than fully specific.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Rank

The one-based position assigned under the active hit policy's ordering. Equal specificity does not mean equal rank, and filters applied after ranking can leave gaps. Batch ranks are calculated independently within each context identifier.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## REGEX

The strategy reading a regular-expression pattern from each rule row and applying it to the context string. Its current compiler uses a documented Polars-native expression, unlike metadata-literal CONTEXT_REGEX.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Relation

Mountainash's table-operation abstraction over supported concrete backends. It provides operations such as projection, filtering, joining, sorting and collection, allowing engine code to describe work without directly using each backend's table API.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Rule column

A physical field in the rule table, used either as a dimension input or as output data. Metadata classifies matching fields; selection output inference must not mistake an extra constraint column for a business output.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## RULE_ORDER

The hit policy retaining survivors in their original input order rather than sorting them by specificity. Matching still determines eligibility; the score does not determine this policy's row ordering.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## RuleResult

The wrapper around the rows retained by single-context matching and selection. Its accessors expose those rows and their matching information; it is not an audit of rules already eliminated or removed from the result.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## SelectionInfo

The internal record carrying dimension-input fields, priority/output settings and result-state information used by policy selection. Re-selection also needs the relevant rows and columns; the record cannot reconstruct data that was already discarded.

Read [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md).

## Sentinel

A reserved stored value used to encode a special state such as a rule wildcard or missing input. A sentinel is data representation, whereas unknown is a comparison outcome; their relationship is defined by each strategy.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Set normalization

Conversion of a set-valued rule cell to the shared canonical representation used for matching and comparison. Concrete lists are deduplicated and sorted; whole-cell null handling and reserved sentinel validation are explicit parts of the contract.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Set wildcard

The one-element list containing the element type's UNKNOWN sentinel, used to represent an unconstrained set rule. It is not an ordinary set member or an arbitrary empty list; mixed reserved values are subject to validation.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## SET_EXCLUSION

The strategy accepting a scalar context value when it is absent from the rule's list. It shares set wildcard normalization with SET_MEMBERSHIP. Accumulator coalescing unions concrete exclusion lists.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## SET_MEMBERSHIP

The strategy accepting a scalar context value when it occurs in the rule's list. A canonical wildcard list represents an unconstrained rule. Accumulator compatibility and coalescing use the intersection of concrete membership sets.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Snapshot

A persisted lattice directory containing a Parquet frame and YAML manifest. Loading restores the stored artifact rather than rebuilding combinations, and currently reads the frame with Polars rather than restoring its original backend.

Read [Combining, Persisting and Routing Rules](chapters/05-combining-and-persisting-rules/index.md).

## Specificity

The count of active dimensions whose ternary outcome is a hard match. It is not a probability or a contributing-rule count. Some policies use it for ordering; others deliberately use different priorities.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## SUFFIX

The string strategy checking whether the bound context ends with the rule-side value. It uses the same rule-sentinel wrapper as PREFIX and CONTAINS, with the corresponding ending-string comparison.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Survival

The eligibility condition that no active dimension reports a contradiction. A rule can survive with unknown outcomes and low specificity. Subsequent hit-policy assertions, ordering and truncation still determine whether it is returned.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Survivor

A rule or combination that passes the active matching conditions. A returned survivor frame may contain only a policy-selected or filtered subset of all eligible rows; distinguish matching survival from final retention.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## Temporal sentinel

A reserved year-one date or datetime value used for UNKNOWN or NOT_SET in a temporal dimension. It preserves the declared temporal type; a string or numeric sentinel is not an interchangeable replacement.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## Ternary AND

The three-valued conjunction used to combine range-bound comparisons: a contradiction wins, two hard matches produce a hard match, and remaining combinations containing unknown produce unknown. This distinguishes surviving a bound from proving both bounds matched.

Read [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md).

## Ternary logic

The matching system with hard match, unknown and contradiction represented as 1, 0 and -1. It lets the engine distinguish confirmed agreement from non-contradiction without interpreting unknown as an ordinary Boolean true.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Threshold

A single ordered rule value used as a strict lower or upper condition on the context. The compiler maps GREATER_THAN and LESS_THAN to the corresponding ternary comparison; accumulator coalescing retains the stricter compatible threshold.

Read [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md).

## Truncation

Reduction of the retained survivor population by a limit, filter or one-row selection. Later re-selection cannot recover removed rows. The implementation records some truncation states explicitly, so keep a complete COLLECT base when alternative policies are required.

Read [Inside Expression and Batch Evaluation](chapters/06-expression-execution-internals/index.md).

## Unbounded range

A range with an unspecified lower or upper bound represented by the supported sentinel convention. This concerns whether a bound is present, not whether a present endpoint is inclusive. Inspect the resulting ternary outcome rather than assuming full specificity.

Read [Authoring and Evolving Rule Libraries](chapters/02-authoring-rule-libraries/index.md).

## UNIQUE

The hit policy asserting that at most one rule survives. Two survivors violate it even if their business outputs are identical; output agreement belongs to ANY. Zero survivors do not violate uniqueness.

Read [Evaluating, Selecting and Explaining Decisions](chapters/03-evaluating-decisions/index.md).

## UNKNOWN outcome

The ternary value zero: the comparison did not establish either a hard match or a contradiction. It contributes no specificity and does not alone eliminate a rule. Its cause must be interpreted from the strategy and inputs.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## UNKNOWN sentinel

The reserved rule-side representation of an unconstrained value where the strategy supports it. It is distinct from the numeric UNKNOWN outcome and from the context-side NOT_SET representation; typed helper functions provide the actual stored value.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Vectorization

Expression of matching as operations over columns or relations instead of an application-level loop over individual rows. It changes execution structure and can exploit backend capabilities; it does not remove work proportional to the data being processed.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).

## Wildcard

A rule-side representation that leaves a supported matching condition unconstrained. Its encoding and interpretation depend on dtype and strategy. It is not a universal synonym for null, missing context or the literal string NOT_SET.

Read [From Rule Tables to Decisions](chapters/01-rule-tables-and-decisions/index.md).
