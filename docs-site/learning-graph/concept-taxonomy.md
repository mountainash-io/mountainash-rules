# Concept Taxonomy

This internal taxonomy organizes the 133 mountainash-rules concepts into nine categories. The approved
2026-09-12 chapter plan groups these concepts by reader task, not by taxonomy category. The three additions
cover backend-purity enforcement, context-column injection and the lattice-width error boundary.

## Categories

### FOUND — Foundation Concepts

Prerequisites and external knowledge: ternary logic, sentinel value patterns, match strategy concepts, Pydantic
model validation, vectorized evaluation principles, backend-agnostic design patterns, and the mountainash
expression and relation libraries that the rules engine builds upon.

### DIM — Dimension Model

The dimension metadata layer: the MatchStrategy enum (EXACT, EXACT_KEY, NOT_EQUAL, RANGE, GREATER_THAN,
LESS_THAN, PREFIX, SUFFIX, CONTAINS, REGEX, CONTEXT_REGEX, SET_MEMBERSHIP, SET_EXCLUSION — with per-row REGEX
distinct from metadata-literal CONTEXT_REGEX), the DimensionRole enum (CONSTRAINT vs
CONTEXT_KEY), the DataType enum with temporal (date/datetime) sentinels, bool-dimension ternary comparison, the
Dimension Pydantic model with field resolution and validation, the DimensionsMetadata collection, and its YAML
round-trip serialization.

### EXPR — Expression Rules Engine

The single-pass filter evaluation engine: DimensionCompiler that translates Dimension metadata into
backend-agnostic expression templates (including the shared in-band set-wildcard sentinel and its
normalization), the ExpressionRulesEngine class with its construction paths, and the single-pass evaluation
pipeline (context binding, dimension expression application, survival computation, specificity scoring, rank
assignment, column cleanup). CTX_PREFIX names the injected columns that carry context values during evaluation.

### POLICY — Hit Policies

The selection-semantics layer applied to survivor sets: the HitPolicy enum (collect, unique, first, priority,
any, rule_order), SelectionInfo (what a result needs to re-apply a policy post-hoc), HitPolicyViolationError for
UNIQUE/ANY breaches, ordering-key computation, and cardinality application (truncating to a single survivor).
Configurable at the table level via DimensionsMetadata's hit-policy fields, or per evaluation call.

### RESULT — Expression Engine Results

Result accessors for the ExpressionRulesEngine: RuleResult wrapper with survivors, best_match, count,
active_dimensions, the per-rule explain method, at_least filtering, select (re-applying a hit policy
post-hoc), and the engine-level ExplainResult / explain() for scoring every rule against a context without
survival filtering.

### BATCH — Batch Evaluation

Scoring many contexts against the rules table in a single vectorized pass: evaluate_batch(), context
preparation and backend conforming, the cross-join evaluation strategy, per-context-id ranking (window-function
free), opt-in chunked evaluation, and the BatchRuleResult wrapper with its per-context accessors.

### ACCUM — Accumulator Engine

The combinatorial lattice-building engine: AccumulatorCompiler with compatible/coalesce/NA-flag expressions
(including set-membership/set-exclusion support), AccumulatorEngine with its build process (partition, prime
assignment, anchor creation, level expansion with canonical ordering, frontier filter for dominated combination
removal, apply-phase caching), and prime number encoding for unique combination identity with an explicit
per-partition rule cap and the LatticeWidthExceededError boundary.

### LATT — Accumulator Lattice & Results

The lattice data structure and accumulator result accessors: Lattice class with combinations, partition key,
coalesced columns, NA flags, combination depth, is_composed, and save/load snapshot persistence;
AccumulatorResult extending RuleResult with accumulated aggregates (sum, min, max, product), provenance (prime
products), and depth accessors.

### SUPP — Supporting Modules

Cross-cutting concerns: the Aggregate Pydantic model and its four operations, partition key filtering for
CONTEXT_KEY dimensions, build_all for multi-partition lattice construction, apply_auto for automatic
partition-key-based lattice selection, and the LatticeIndex ternary-partition router (with its
AmbiguousPartitionError and EXACT_KEY-powered routing) that generalizes partition selection beyond apply_auto's
single-lattice case.
Backend-purity enforcement and its documented exception are also tracked here.

## Taxonomy Summary Table

| TaxonomyID | Category Name | Concept Range | Count |
|------------|---------------|---------------|-------|
| FOUND | Foundation Concepts | 1-10 | 10 |
| DIM | Dimension Model | 11-30, 91-96 | 26 |
| EXPR | Expression Rules Engine | 31-48, 98-99, 132 | 21 |
| POLICY | Hit Policies | 97, 100-108 | 10 |
| RESULT | Expression Engine Results | 49-58, 109-111 | 13 |
| BATCH | Batch Evaluation | 112-119 | 8 |
| ACCUM | Accumulator Engine | 59-76, 120-123, 133 | 23 |
| LATT | Accumulator Lattice & Results | 77-86, 124-127 | 14 |
| SUPP | Supporting Modules | 87-90, 128-131 | 8 |
