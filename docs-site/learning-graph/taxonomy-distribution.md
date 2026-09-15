# Taxonomy Distribution Report

## Overview

- **Total Concepts**: 133
- **Number of Taxonomies**: 9
- **Average Concepts per Taxonomy**: 14.8

## Distribution Summary

| Category | TaxonomyID | Count | Percentage | Status |
|----------|-----------|-------|------------|--------|
| Dimension Model | DIM | 26 | 19.5% | ✅ |
| Accumulator Engine | ACCUM | 23 | 17.3% | ✅ |
| Expression Rules Engine | EXPR | 21 | 15.8% | ✅ |
| Accumulator Lattice & Results | LATT | 14 | 10.5% | ✅ |
| Expression Engine Results | RESULT | 13 | 9.8% | ✅ |
| Foundation Concepts | FOUND | 10 | 7.5% | ✅ |
| Hit Policies | POLICY | 10 | 7.5% | ✅ |
| Supporting Modules | SUPP | 8 | 6.0% | ✅ |
| Batch Evaluation | BATCH | 8 | 6.0% | ✅ |

## Visual Distribution

```
Dimension Model           █████████  26 ( 19.5%)
Accumulator Engine        ████████  23 ( 17.3%)
Expression Rules Engine   ███████  21 ( 15.8%)
Accumulator Lattice & Res █████  14 ( 10.5%)
Expression Engine Results ████  13 (  9.8%)
Foundation Concepts       ███  10 (  7.5%)
Hit Policies              ███  10 (  7.5%)
Supporting Modules        ███   8 (  6.0%)
Batch Evaluation          ███   8 (  6.0%)
```

## Balance Analysis

### ✅ No Over-Represented Categories

All categories are under the 30% threshold. Good balance!

## Category Details

### Dimension Model (DIM)

**Count**: 26 concepts (19.5%)

**Concepts**:

- 11. MatchStrategy Enum
- 12. EXACT Strategy
- 13. NOT_EQUAL Strategy
- 14. RANGE Strategy
- 15. GREATER_THAN Strategy
- 16. LESS_THAN Strategy
- 17. PREFIX Strategy
- 18. SUFFIX Strategy
- 19. CONTAINS Strategy
- 20. REGEX Strategy
- 21. SET_MEMBERSHIP Strategy
- 22. SET_EXCLUSION Strategy
- 23. DimensionRole Enum
- 24. CONSTRAINT Role
- 25. CONTEXT_KEY Role
- *...and 11 more*

### Accumulator Engine (ACCUM)

**Count**: 23 concepts (17.3%)

**Concepts**:

- 59. AccumulatorCompiler
- 60. Compatible Expression
- 61. Coalesce Expression
- 62. Coalesce NA Flag
- 63. Compatible Exact
- 64. Compatible Range
- 65. Coalesce Exact
- 66. Coalesce Range
- 67. Coalesce Threshold
- 68. AccumulatorEngine
- 69. Prime Number Encoding
- 70. Prime Table Sieve
- 71. Get Prime Function
- 72. Checked Multiply
- 73. Anchor Creation
- *...and 8 more*

### Expression Rules Engine (EXPR)

**Count**: 21 concepts (15.8%)

**Concepts**:

- 31. DimensionCompiler
- 32. Compile Exact Expression
- 33. Compile Range Expression
- 34. Compile String Match
- 35. Compile Regex Expression
- 36. Compile Set Expression
- 37. Compile Threshold Expression
- 38. Sentinel-Aware Ternary
- 39. Context Value Extraction
- 40. ExpressionRulesEngine
- 41. Engine Construction
- 42. Convenience vs Advanced Path
- 43. Single-Pass Evaluation
- 44. Context Binding Phase
- 45. Dimension Expression Phase
- *...and 6 more*

### Accumulator Lattice & Results (LATT)

**Count**: 14 concepts (10.5%)

**Concepts**:

- 77. Lattice Class
- 78. Lattice Combinations
- 79. Lattice Partition Key
- 80. Coalesced Columns
- 81. NA Flag Columns
- 82. Combination Depth
- 83. AccumulatorResult Class
- 84. Accumulated Aggregates
- 85. Provenance Accessor
- 86. Depths Accessor
- 124. Lattice Save Method
- 125. Lattice Load Method
- 126. Lattice Is Composed
- 127. Aggregate Min Max Product

### Expression Engine Results (RESULT)

**Count**: 13 concepts (9.8%)

**Concepts**:

- 49. RuleResult Class
- 50. Survivors Accessor
- 51. Best Match Accessor
- 52. Count Accessor
- 53. Active Dimensions
- 54. RuleResult Explain Method
- 55. At Least Filter
- 56. Top N Filtering
- 57. Min Specificity Filter
- 58. Observability Columns
- 109. ExplainResult Class
- 110. Engine-Level Explain
- 111. RuleResult Select Method

### Foundation Concepts (FOUND)

**Count**: 10 concepts (7.5%)

**Concepts**:

- 1. Ternary Logic
- 2. Sentinel Values
- 3. Match Strategy Patterns
- 4. Pydantic Model Validation
- 5. Vectorized Evaluation
- 6. Backend-Agnostic Design
- 7. DataFrame as Rule Store
- 8. Mountainash Expressions
- 9. Mountainash Relations
- 10. Context Object

### Hit Policies (POLICY)

**Count**: 10 concepts (7.5%)

**Concepts**:

- 97. Table-Level Hit Policy Fields
- 100. HitPolicy Enum
- 101. Collect Policy
- 102. Unique Policy
- 103. First And Priority Policy
- 104. Any Policy
- 105. Rule Order Policy
- 106. SelectionInfo Dataclass
- 107. HitPolicyViolationError
- 108. Cardinality Application

### Supporting Modules (SUPP)

**Count**: 8 concepts (6.0%)

**Concepts**:

- 87. Aggregate Model
- 88. Partition Key Filtering
- 89. Build All Partitions
- 90. Apply Auto Selection
- 128. LatticeIndex Router
- 129. AmbiguousPartitionError
- 130. EXACT_KEY Partition Routing
- 131. Backend Purity Enforcement

### Batch Evaluation (BATCH)

**Count**: 8 concepts (6.0%)

**Concepts**:

- 112. BatchRuleResult Class
- 113. Evaluate Batch Method
- 114. Batch Context Preparation
- 115. Cross-Join Evaluation
- 116. Per-Context Ranking
- 117. Backend Conforming
- 118. Chunked Batch Evaluation
- 119. For Context Accessor

## Recommendations

- ✅ **Excellent balance**: Categories are evenly distributed (spread: 13.5%)
- ✅ **MISC category minimal**: Good categorization specificity

### Educational Use Recommendations

- Use taxonomy categories for color-coding in graph visualizations
- Design curriculum modules based on taxonomy groupings
- Create filtered views for focused learning paths
- Use categories for assessment organization
- Enable navigation by topic area in interactive tools

---

*Report generated by ibook graph taxonomy-report*
