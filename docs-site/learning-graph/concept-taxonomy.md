# Concept Taxonomy

This taxonomy organizes the 90 mountainash-rules concepts into 7 categories aligned with the package's architecture layers.

## Categories

### FOUND — Foundation Concepts

Prerequisites and external knowledge: ternary logic, sentinel value patterns, match strategy concepts, Pydantic model validation, vectorized evaluation principles, backend-agnostic design patterns, and the mountainash expression and relation libraries that the rules engine builds upon.

### DIM — Dimension Model

The dimension metadata layer: the MatchStrategy enum with its 11 strategy types (EXACT, NOT_EQUAL, RANGE, GREATER_THAN, LESS_THAN, PREFIX, SUFFIX, CONTAINS, REGEX, SET_MEMBERSHIP, SET_EXCLUSION), the DimensionRole enum (CONSTRAINT vs CONTEXT_KEY), the Dimension Pydantic model with field resolution and validation, and the DimensionsMetadata collection.

### EXPR — Expression Rules Engine

The single-pass filter evaluation engine: DimensionCompiler that translates Dimension metadata into backend-agnostic expression templates, the ExpressionRulesEngine class with its construction paths, and the six-phase evaluation pipeline (context binding, dimension expression application, survival computation, specificity scoring, rank assignment, column cleanup).

### RESULT — Expression Engine Results

Result accessors for the ExpressionRulesEngine: RuleResult wrapper with survivors, best_match, count, active_dimensions, explain (per-rule ternary breakdown), at_least filtering, and the engine-level top_n, min_specificity, and observability column options.

### ACCUM — Accumulator Engine

The combinatorial lattice-building engine: AccumulatorCompiler with compatible/coalesce/NA-flag expressions, AccumulatorEngine with its five-phase build process (partition, prime assignment, anchor creation, level expansion with canonical ordering, frontier filter for dominated combination removal), and prime number encoding for unique combination identity.

### LATT — Accumulator Lattice & Results

The lattice data structure and accumulator result accessors: Lattice class with combinations, partition key, coalesced columns, NA flags, and combination depth; AccumulatorResult extending RuleResult with accumulated aggregates, provenance (prime products), and depth accessors.

### SUPP — Supporting Modules

Cross-cutting concerns: the Aggregate Pydantic model for sum accumulation, partition key filtering for CONTEXT_KEY dimensions, build_all for multi-partition lattice construction, and apply_auto for automatic partition-key-based lattice selection.

## Taxonomy Summary Table

| TaxonomyID | Category Name | Concept Range | Count |
|------------|---------------|---------------|-------|
| FOUND | Foundation Concepts | 1-10 | 10 |
| DIM | Dimension Model | 11-30 | 20 |
| EXPR | Expression Rules Engine | 31-48 | 18 |
| RESULT | Expression Engine Results | 49-58 | 10 |
| ACCUM | Accumulator Engine | 59-76 | 18 |
| LATT | Accumulator Lattice & Results | 77-86 | 10 |
| SUPP | Supporting Modules | 87-90 | 4 |
