# Learning Graph Quality Metrics Report

Computed from the canonical graph after installing the approved eleven-chapter mapping. These structural checks do not establish manuscript quality, editorial acceptance or a completed source refresh. Existing node enrichment and CIS were preserved, not recalculated.

## Overview

- Concepts: 133
- Dependency edges: 226
- Foundational concepts (no prerequisites): 7
- Concepts with prerequisites: 126
- Terminal concepts (have prerequisites; nothing depends on them): 52
- Orphaned concepts: 0
- Average prerequisites per concept: 1.70
- Connected components: 1

## Architecture and order checks

- All 133 stable IDs and labels are preserved.
- Each concept has exactly one of the eleven approved chapter assignments.
- All 226 prerequisites precede their dependents in the chapter plan, including within-chapter order.
- No duplicate edges, self-dependencies or cycles.
- JSON and CSV agree on IDs, labels, taxonomy and dependencies. Chapter assignments live in JSON; the CSV format has no chapter column.

| Chapter | Primary concepts |
|---|---:|
| 1. Two Rule Engines, One Shared Model | 4 |
| 2. The Shared Rule Model: Tables, Contexts and Dimensions | 15 |
| 3. Matching Concepts: Strategies, Unknowns and Wildcards | 19 |
| 4. Using the Expression Rules Engine | 13 |
| 5. Expression Engine Hit Policies, Results and Explanations | 15 |
| 6. Expression Engine Batch Evaluation | 8 |
| 7. Using the Accumulator Engine | 8 |
| 8. Accumulator Lattices, Results and Routing | 15 |
| 9. Inside the Expression Rules Engine | 14 |
| 10. Inside the Accumulator Engine | 21 |
| 11. Extending and Maintaining Both Engines | 1 |

Chapter 11 teaches extension and maintenance recipes by revisiting earlier concepts; its one new primary is not a limit on the chapter’s scope.

## Foundational concepts

- 1: Ternary Logic
- 3: Match Strategy Patterns
- 4: Pydantic Model Validation
- 5: Vectorized Evaluation
- 6: Backend-Agnostic Design
- 10: Context Object
- 94: DataType Enum

## Longest prerequisite path

12 concepts, 11 edges. One longest path is:

1. Match Strategy Patterns (3)
2. MatchStrategy Enum (11)
3. Dimension Class (26)
4. DimensionsMetadata (27)
5. ExpressionRulesEngine (40)
6. Single-Pass Evaluation (43)
7. Survival Computation (46)
8. Specificity Scoring (47)
9. Rank Assignment (48)
10. HitPolicy Enum (100)
11. Unique Policy (102)
12. HitPolicyViolationError (107)

## Most frequently required concepts

| Concept | Direct dependents |
|---|---:|
| 11: MatchStrategy Enum | 15 |
| 2: Sentinel Values | 13 |
| 49: RuleResult Class | 9 |
| 77: Lattice Class | 9 |
| 100: HitPolicy Enum | 9 |
| 43: Single-Pass Evaluation | 8 |
| 8: Mountainash Expressions | 7 |
| 27: DimensionsMetadata | 7 |
| 1: Ternary Logic | 6 |
| 31: DimensionCompiler | 6 |

## Prerequisite count distribution

| Prerequisites | Concepts |
|---:|---:|
| 0 | 7 |
| 1 | 53 |
| 2 | 57 |
| 3 | 11 |
| 4 | 2 |
| 5 | 1 |
| 6 | 1 |
| 7 | 1 |

## Provenance

The crosswalk retains the original graph metadata, enrichment and edges, plus the rationale for removing ten edges and adding one. The current graph metadata separately records the target technical source and the inherited enrichment source. Profiles and refresh-state remain unchanged.
