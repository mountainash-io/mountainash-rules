# Taxonomy Distribution Report

## Overview

- **Total Concepts**: 130
- **Number of Taxonomies**: 9
- **Average Concepts per Taxonomy**: 14.4

## Distribution Summary

| Category | TaxonomyID | Count | Percentage | Status |
|----------|-----------|-------|------------|--------|
| DIM | DIM | 26 | 20.0% | ✅ |
| ACCUM | ACCUM | 22 | 16.9% | ✅ |
| EXPR | EXPR | 20 | 15.4% | ✅ |
| LATT | LATT | 14 | 10.8% | ✅ |
| RESULT | RESULT | 13 | 10.0% | ✅ |
| Foundation Concepts - Prerequisites | FOUND | 10 | 7.7% | ✅ |
| POLICY | POLICY | 10 | 7.7% | ✅ |
| BATCH | BATCH | 8 | 6.2% | ✅ |
| SUPP | SUPP | 7 | 5.4% | ✅ |

## Visual Distribution

```
DIM                       ██████████  26 ( 20.0%)
ACCUM                     ████████  22 ( 16.9%)
EXPR                      ███████  20 ( 15.4%)
LATT                      █████  14 ( 10.8%)
RESULT                    █████  13 ( 10.0%)
Foundation Concepts - Pre ███  10 (  7.7%)
POLICY                    ███  10 (  7.7%)
BATCH                     ███   8 (  6.2%)
SUPP                      ██   7 (  5.4%)
```

## Balance Analysis

### ✅ No Over-Represented Categories

All categories are under the 30% threshold. Good balance!

## Category Details

### DIM (DIM)

**Count**: 26 concepts (20.0%)

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

### ACCUM (ACCUM)

**Count**: 22 concepts (16.9%)

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
- *...and 7 more*

### EXPR (EXPR)

**Count**: 20 concepts (15.4%)

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
- *...and 5 more*

### LATT (LATT)

**Count**: 14 concepts (10.8%)

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

### RESULT (RESULT)

**Count**: 13 concepts (10.0%)

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

### Foundation Concepts - Prerequisites (FOUND)

**Count**: 10 concepts (7.7%)

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

### POLICY (POLICY)

**Count**: 10 concepts (7.7%)

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

### BATCH (BATCH)

**Count**: 8 concepts (6.2%)

**Concepts**:

- 112. BatchRuleResult Class
- 113. Evaluate Batch Method
- 114. Batch Context Preparation
- 115. Cross-Join Evaluation
- 116. Per-Context Ranking
- 117. Backend Conforming
- 118. Chunked Batch Evaluation
- 119. For Context Accessor

### SUPP (SUPP)

**Count**: 7 concepts (5.4%)

**Concepts**:

- 87. Aggregate Model
- 88. Partition Key Filtering
- 89. Build All Partitions
- 90. Apply Auto Selection
- 128. LatticeIndex Router
- 129. AmbiguousPartitionError
- 130. EXACT_KEY Partition Routing

## Recommendations

- ✅ **Excellent balance**: Categories are evenly distributed (spread: 14.6%)
- ✅ **MISC category minimal**: Good categorization specificity

### Educational Use Recommendations

- Use taxonomy categories for color-coding in graph visualizations
- Design curriculum modules based on taxonomy groupings
- Create filtered views for focused learning paths
- Use categories for assessment organization
- Enable navigation by topic area in interactive tools

---

*Report generated by learning-graph-reports/taxonomy_distribution.py*
