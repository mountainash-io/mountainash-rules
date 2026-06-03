# Mountainash Rules Concept List

Total concepts: 90

## Foundation Concepts (1-10)

1. Ternary Logic
2. Sentinel Values
3. Match Strategy Patterns
4. Pydantic Model Validation
5. Vectorized Evaluation
6. Backend-Agnostic Design
7. DataFrame as Rule Store
8. Mountainash Expressions
9. Mountainash Relations
10. Context Object

## Dimension Model (11-30)

11. MatchStrategy Enum
12. EXACT Strategy
13. NOT_EQUAL Strategy
14. RANGE Strategy
15. GREATER_THAN Strategy
16. LESS_THAN Strategy
17. PREFIX Strategy
18. SUFFIX Strategy
19. CONTAINS Strategy
20. REGEX Strategy
21. SET_MEMBERSHIP Strategy
22. SET_EXCLUSION Strategy
23. DimensionRole Enum
24. CONSTRAINT Role
25. CONTEXT_KEY Role
26. Dimension Class
27. DimensionsMetadata
28. Field Resolution
29. Dimension Validator
30. Data Type Constraints

## Expression Rules Engine (31-48)

31. DimensionCompiler
32. Compile Exact Expression
33. Compile Range Expression
34. Compile String Match
35. Compile Regex Expression
36. Compile Set Expression
37. Compile Threshold Expression
38. Sentinel-Aware Ternary
39. Context Value Extraction
40. ExpressionRulesEngine
41. Engine Construction
42. Convenience vs Advanced Path
43. Single-Pass Evaluation
44. Context Binding Phase
45. Dimension Expression Phase
46. Survival Computation
47. Specificity Scoring
48. Rank Assignment

## Expression Engine Results (49-58)

49. RuleResult Class
50. Survivors Accessor
51. Best Match Accessor
52. Count Accessor
53. Active Dimensions
54. Explain Method
55. At Least Filter
56. Top N Filtering
57. Min Specificity Filter
58. Observability Columns

## Accumulator Engine (59-76)

59. AccumulatorCompiler
60. Compatible Expression
61. Coalesce Expression
62. Coalesce NA Flag
63. Compatible Exact
64. Compatible Range
65. Coalesce Exact
66. Coalesce Range
67. Coalesce Threshold
68. AccumulatorEngine
69. Prime Number Encoding
70. Prime Table Sieve
71. Get Prime Function
72. Checked Multiply
73. Anchor Creation
74. Level Expansion
75. Canonical Ordering Guard
76. Frontier Filter

## Accumulator Lattice & Results (77-86)

77. Lattice Class
78. Lattice Combinations
79. Lattice Partition Key
80. Coalesced Columns
81. NA Flag Columns
82. Combination Depth
83. AccumulatorResult Class
84. Accumulated Aggregates
85. Provenance Accessor
86. Depths Accessor

## Supporting Modules (87-90)

87. Aggregate Model
88. Partition Key Filtering
89. Build All Partitions
90. Apply Auto Selection
