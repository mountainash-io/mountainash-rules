# Mountainash Rules: approved reconciliation chapter plan

**Status: complete, verified eleven-chapter reconciliation candidate.** Chapters 1, 2 and 3 are user-approved, and Chapter 6 remains the approved batch sample. The remaining seven chapters and both appendices have been completed under the user's autonomous-completion authorization. Five parallel authors drafted the independent chapter slices; Main reviewed every chapter continuously, integrated the book, and resolved the findings from two independent technical reviewers. Commit, publication and promotion of a completed source-refresh baseline remain separate user gates.

## Source, manuscript and editorial basis

- Technical source: maintained Rules worktree at `730a8583ee9d4fd6b52dc5350699eb66cc7487e9`. Read the source and run examples; neither manuscript nor profile prose overrides it.
- Maintained manuscript: `bccfc1f2d1d2b1486661c75b7ba49c158f5a18bf`, published on develop through `07a7a8a2104b453871aa56cfd814c7f2376d07f7`. Preserve its accessible explanation and the 31 recently corrected primary concepts.
- Donor manuscript: `a862e3a53042058649aabb48b3ad31380d0426a0`; donor source basis `94659bb0c096485c87d329f09e944577427f129f`. Reuse diagrams and detailed examples selectively, not its compressed voice or obsolete source claims.
- Editorial references: Chapters 1 and 2 are approved examples of the direct, patient explanatory voice. The approved batch sample at `docs-site/site/docs/chapters/06-batch-evaluation/index.md`, originally Chapter 4, remains a reference for worked-example depth. Preserve the approved chapters' bodies, examples and diagrams unless the user requests changes.
- Profile input: current maintained manifest, all five audience facets, and relevant module/source evidence. The candidate profile remains historical; completion of this editorial reconciliation does not promote its source profile or refresh baseline.
- Canonical assignments and teaching dependencies: `docs-site/learning-graph/learning-graph.json`. The CSV mirrors IDs, labels, taxonomy and dependencies; it does not have a chapter column. Supporting per-concept reconciliation evidence is in `docs-site/reconciliation-crosswalk.json`, including all 133 stable IDs, original enrichment, accepted/donor section locations and excerpts, correction requirements and old URL/anchor mapping.

## Reader progression and menus

The four primary navigation groups are Shared foundations (1–3), The Expression Rules Engine (4–6), The Accumulator Engine (7–8), and Implementation and extension (9–11). Use the same names and order on the home-page contents and mobile/desktop menus. Chapters are children of these groups, not a flat list under Chapters. FAQ/glossary are supporting appendices.

Users receive practical shared-model and engine workflows in Chapters 1–8. Maintainers and contributors share the implementation/extension explanations in Chapters 9–11. Backend architecture is introduced concretely in Chapter 1, developed in Chapter 2 and the internals, and qualified at the operation boundary. The broader-hype facet supplies factual motivation only: no benchmark, hot-reload or universal-portability claims.

### Contents-page editorial decision

The contents page now links all eleven chapters and both appendices. It serves the reader: subjects, reading order and destinations. Approval status, concept mapping, manuscript history and verification evidence stay in these internal records.

Nineteen earlier chapter URLs now serve explicit reference landing pages, outside navigation and search. They preserve 416 heading fragments, including primary and non-primary sections, and link to the appropriate current concepts, chapters or book contents. Six replaced reference manuscripts are preserved byte-for-byte under `docs-site/archive/book-reconciliation-reference/chapters/`, outside the published source tree.

## Reconciliation workflow and future edits

Read `docs-site/editorial-brief.md` and this architecture before choosing a chapter or using its existing draft. The architecture determines the chapter's purpose, boundaries and primary teaching order. The canonical graph determines where each concept belongs and what it depends on. The crosswalk supplies evidence from both books; it is not an alternative mapping authority.

For each chapter:

1. Read its boundary and complete primary list below. Resolve every ID against the canonical graph and the crosswalk. Read the full corresponding sections in both manuscript worktrees, not just labels, concept markers or the crosswalk's excerpts. Read relevant current profile descriptions and source evidence where technical claims need checking.
2. Identify what the reader should understand by the end of this chapter and what belongs later. Prerequisite introductions must be understandable here; references to later chapters do not excuse relying on unexplained ideas.
3. Read the corresponding maintained-textbook passages for their direct, patient explanatory voice and the approved batch sample for worked-example depth. Apply the brief's **Audience expectations and technical framing**, including **Direct, patient exposition**. Open with the package concept or API and its purpose; introduce scenarios only after establishing what they illustrate. Define concrete referents before using phrases such as "that metadata". Assume Python/DataFrame competence and retain consequential API boundaries without elementary reminders or implausible-misunderstanding asides. Do not substitute a last-minute humanizing pass for concept-led teaching.
4. Use the matching skill with the confirmed brief, canonical graph and completed candidate. Preserve the approved structure and explanatory voice when updating source-backed claims; do not restart from a rejected or historical manuscript.
5. Use examples and diagrams to explain the assigned concepts. Do not force every chapter into the previous delivery-charge walkthrough, require an arbitrary diagram count, or expand an introductory contrast into the full engine workflows assigned to Chapters 4 and 7.
6. Keep each stable primary marker with its substantial explanation. Show inputs, outputs and why the outputs occur. Check conceptual completeness against the actual concept descriptions, not a successful marker count.
7. Verify runnable examples and their stated results against the selected source, inspect rendered diagrams and links, and complete continuous editorial review. Technical checks do not replace prose review. The autonomous authorization covered this reconciliation; future requests define the scope of later changes.

The remaining seven chapters, FAQ, glossary, contents, navigation and concept destinations are integrated. Future edits must preserve the approved architecture and patient explanatory voice. Chapters 1, 2 and 6 received only cross-reference and historical-status maintenance during completion; their instructional bodies, examples and diagrams were retained. Chapter 3's final scoring diagram distinguishes scoring every row from retaining survivors; its prose and examples remain approved. Do not replace these chapters with reference manuscripts or placeholder pages.

### Chapter 1 boundary

Chapter 1 explains the two engines and the primitive structures they share. Its primary concepts are Vectorized Evaluation (5), DataFrame as Rule Store (7), Context Object (10) and Backend-Agnostic Design (6). Introduce rules, dimensions and metadata plainly enough to explain how the shared model fits together; their detailed primary treatment is in Chapter 2. Matching outcomes and wildcard semantics receive their detailed treatment in Chapter 3.

This assignment keeps the opening focused on shared foundations rather than API calls, hit policies, aggregate setup, lattice pruning or result accessors. The approved architecture calls for a small contrasting example. Full expression-engine use belongs in Chapter 4; full accumulator use belongs in Chapter 7. Keep the approved opening within that boundary rather than reintroducing the rejected workflow.

## Chapter boundaries and primary order

IDs denote primary explanations, not every mention. A short preview may introduce an idea needed to understand the current chapter without duplicating its later primary marker. Preserve original book-wide CIS/enrichment as historical data; do not invent per-chapter scores or use stale scores as a substitute for the architecture. Concepts require explanations, not just markers.

### Shared foundations

#### 1. Two Rule Engines, One Shared Model

**Destination:** `docs-site/site/docs/chapters/01-two-rule-engines/index.md`

Explain the two engines, the shared rule model and the four assigned foundations before relying on engine workflows. Use a small contrast to show individual rules versus compatible combinations. Introduce shared primitives in plain language; leave their full configuration details to Chapters 2–3 and the full engine walkthroughs to Chapters 4 and 7. The rejected delivery-charge draft is not the required structure.

**Primary explanation order:**

- 5 **Vectorized Evaluation** — `comparing-rules-as-columns`.
- 7 **DataFrame as Rule Store** — `put-the-rules-in-a-table`.
- 10 **Context Object** — `describe-one-delivery`.
- 6 **Backend-Agnostic Design** — `using-other-dataframe-libraries`.

#### 2. The Shared Rule Model: Tables, Contexts and Dimensions

**Destination:** `docs-site/site/docs/chapters/02-shared-rule-model/index.md`

Build and evolve shared metadata. Explain comparison names, declared types, roles, fields and validation before depending on them. Keep Pydantic configuration checks distinct from application input validation; use small local explanations of range/pattern fields without requiring the full strategy catalogue. Introduce expressions and relations at a usable level.

**Primary explanation order:**

- 3 **Match Strategy Patterns** — `rules-are-rows-comparisons-are-match-strategies`.
- 11 **MatchStrategy Enum** — `the-matchstrategy-enum`.
- 4 **Pydantic Model Validation** — `pydantic-models-validate-configuration-before-evaluation`.
- 94 **DataType Enum** — `the-datatype-enum`.
- 23 **DimensionRole Enum** — `the-dimensionrole-enum`.
- 24 **CONSTRAINT Role** — `constraint-role`.
- 25 **CONTEXT_KEY Role** — `context_key-role`.
- 26 **Dimension Class** — `the-dimension-class`.
- 28 **Field Resolution** — `field-resolution`.
- 30 **Data Type Constraints** — `data-type-constraints`.
- 29 **Dimension Validator** — `the-dimension-validator`.
- 27 **DimensionsMetadata** — `dimensionsmetadata`.
- 96 **YAML Round-Trip** — `yaml-round-trip`.
- 8 **Mountainash Expressions** — `mountainash-expressions-what-to-compute`.
- 9 **Mountainash Relations** — `mountainash-relations-where-and-how-it-executes`.

#### 3. Matching Concepts: Strategies, Unknowns and Wildcards

**Destination:** `docs-site/site/docs/chapters/03-matching-concepts/index.md`

Explain ternary outcomes, rule wildcards and missing facts, then work through scalar, string, temporal/Boolean and set comparisons. Explicitly separate expression-engine support from accumulator compatibility/coalescing support. Use concrete rows and interpreted outcomes, not a compressed enum catalogue.

**Primary explanation order:**

- 1 **Ternary Logic** — `ternary-logic-match-unknown-and-non-match`.
- 2 **Sentinel Values** — `sentinel-values-telling-no-constraint-apart-from-no-answer`.
- 95 **Temporal Sentinels** — `temporal-sentinels`.
- 12 **EXACT Strategy** — `exact-strategy`.
- 91 **EXACT_KEY Strategy** — `exact_key-strategy`.
- 13 **NOT_EQUAL Strategy** — `not_equal-strategy`.
- 14 **RANGE Strategy** — `range-strategy`.
- 15 **GREATER_THAN Strategy** — `greater_than-strategy`.
- 16 **LESS_THAN Strategy** — `less_than-strategy`.
- 17 **PREFIX Strategy** — `prefix-strategy`.
- 18 **SUFFIX Strategy** — `suffix-strategy`.
- 19 **CONTAINS Strategy** — `contains-strategy`.
- 20 **REGEX Strategy** — `regex-strategy`.
- 92 **CONTEXT_REGEX Strategy** — `context_regex-strategy`.
- 93 **Bool Ternary Comparison** — `bool-ternary-comparison`.
- 21 **SET_MEMBERSHIP Strategy** — `set_membership-strategy`.
- 98 **Set Wildcard Sentinel** — `set-wildcard-sentinel`.
- 99 **Set Value Normalization** — `set-value-normalization`.
- 22 **SET_EXCLUSION Strategy** — `set_exclusion-strategy`.

### The Expression Rules Engine

#### 4. Using the Expression Rules Engine

**Destination:** `docs-site/site/docs/chapters/04-expression-rules-engine/index.md`

Complete one expression-engine workflow from construction to result reading. Explain survival, specificity and rank, including multiple/no matches. Keep full policy selection and explanation in Chapter 5, compiler implementation in Chapter 9.

**Primary explanation order:**

- 40 **ExpressionRulesEngine** — `construct-an-engine-and-evaluate-a-context`.
- 41 **Engine Construction** — `constructing-an-engine`.
- 42 **Convenience vs Advanced Path** — `convenience-vs-advanced-construction`.
- 43 **Single-Pass Evaluation** — `evaluating-a-context-in-one-pass`.
- 46 **Survival Computation** — `understand-survival-and-ranking`.
- 47 **Specificity Scoring** — `specificity-how-many-dimensions-matched`.
- 48 **Rank Assignment** — `rank-ordering-and-breaking-ties`.
- 49 **RuleResult Class** — `read-the-result-contract`.
- 50 **Survivors Accessor** — `survivors`.
- 51 **Best Match Accessor** — `best_match`.
- 52 **Count Accessor** — `count`.
- 53 **Active Dimensions** — `active_dimensions`.
- 58 **Observability Columns** — `observability-columns`.

#### 5. Expression Engine Hit Policies, Results and Explanations

**Destination:** `docs-site/site/docs/chapters/05-expression-results-and-policies/index.md`

Explain the policy choices, schema assertions, result filters and the two explanation interfaces. Distinguish ordering, filtering, cardinality and candidate completeness. Show what a caller may safely reselect and what information was discarded.

**Primary explanation order:**

- 100 **HitPolicy Enum** — `choose-and-reapply-a-hit-policy`.
- 97 **Table-Level Hit Policy Fields** — `table-level-hit-policy-fields`.
- 101 **Collect Policy** — `collect-the-default-unfiltered-ranking`.
- 102 **Unique Policy** — `unique-assert-at-most-one-survivor`.
- 103 **First And Priority Policy** — `first-and-priority-pick-a-single-winner`.
- 104 **Any Policy** — `any-survivors-that-must-agree`.
- 105 **Rule Order Policy** — `rule_order-preserve-declaration-order`.
- 107 **HitPolicyViolationError** — `hitpolicyviolationerror`.
- 54 **RuleResult Explain Method** — `explain-a-decision-and-refine-the-result`.
- 55 **At Least Filter** — `at_least-filtering-the-returned-survivors`.
- 56 **Top N Filtering** — `top_n-limiting-how-many-survivors-return`.
- 57 **Min Specificity Filter** — `min_specificity-excluding-weak-matches`.
- 109 **ExplainResult Class** — `the-explainresult-class`.
- 110 **Engine-Level Explain** — `engine-level-explain-scoring-every-rule`.
- 111 **RuleResult Select Method** — `reapplying-a-policy-with-ruleresultselect`.

#### 6. Expression Engine Batch Evaluation

**Destination:** `docs-site/site/docs/chapters/06-batch-evaluation/index.md`

The approved batch sample is already at this destination. Preserve its body, four-request running example, two diagrams and ordered executable blocks. Only necessary cross-reference or review-status maintenance is in scope without a new editorial request. Preserve independent identity/rank, chunking limits, backend conversion, typed empties and selection restrictions.

**Primary explanation order:**

- 113 **Evaluate Batch Method** — `evaluate-a-table-of-requests`.
- 112 **BatchRuleResult Class** — `read-the-result-as-a-batch`.
- 114 **Batch Context Preparation** — `keep-each-context-identifiable`.
- 115 **Cross-Join Evaluation** — `understand-the-work-behind-a-batch`.
- 116 **Per-Context Ranking** — `rank-matches-within-each-request`.
- 117 **Backend Conforming** — `use-compatible-table-backends`.
- 118 **Chunked Batch Evaluation** — `work-in-smaller-chunks`.
- 119 **For Context Accessor** — `inspect-one-context-without-evaluating-it-again`.

### The Accumulator Engine

#### 7. Using the Accumulator Engine

**Destination:** `docs-site/site/docs/chapters/07-accumulator-engine/index.md`

Explain compatible combinations, aggregate definitions, coalesced conditions and build versus apply through a complete workflow. Show the broad wildcard result as well as more specific combinations. Do not require prime encoding or the search algorithm before using a lattice.

**Primary explanation order:**

- 68 **AccumulatorEngine** — `accumulatorengine`.
- 87 **Aggregate Model** — `the-aggregate-model`.
- 77 **Lattice Class** — `lattice-class`.
- 78 **Lattice Combinations** — `lattice-combinations`.
- 80 **Coalesced Columns** — `coalesced-columns`.
- 83 **AccumulatorResult Class** — `accumulatorresult-class`.
- 84 **Accumulated Aggregates** — `accumulated-aggregates`.
- 127 **Aggregate Min Max Product** — `aggregate-min-max-and-product`.

#### 8. Accumulator Lattices, Results and Routing

**Destination:** `docs-site/site/docs/chapters/08-lattices-results-and-routing/index.md`

Read lattice and result structures without mistaking source payload for combined values. Explain encoded provenance, zero-based depth, partition keys, routing, ambiguity, caching and save/load boundaries. Public limits are signposted here; their mechanisms follow in Chapter 10.

**Primary explanation order:**

- 79 **Lattice Partition Key** — `lattice-partition-key`.
- 81 **NA Flag Columns** — `na-flag-columns`.
- 82 **Combination Depth** — `combination-depth`.
- 85 **Provenance Accessor** — `provenance-accessor`.
- 86 **Depths Accessor** — `depths-accessor`.
- 88 **Partition Key Filtering** — `partition-key-filtering`.
- 89 **Build All Partitions** — `build-all-partitions`.
- 90 **Apply Auto Selection** — `apply-auto-selection`.
- 122 **Apply-Phase Caching** — `apply-phase-caching`.
- 124 **Lattice Save Method** — `lattice-save-method`.
- 125 **Lattice Load Method** — `lattice-load-method`.
- 126 **Lattice Is Composed** — `lattice-is-composed`.
- 128 **LatticeIndex Router** — `the-latticeindex-router`.
- 129 **AmbiguousPartitionError** — `ambiguouspartitionerror`.
- 130 **EXACT_KEY Partition Routing** — `exact_key-partition-routing`.

### Implementation and extension

#### 9. Inside the Expression Rules Engine

**Destination:** `docs-site/site/docs/chapters/09-expression-engine-internals/index.md`

Trace dimension compilation, typed context binding, strategy expressions, survival/ranking and selection metadata after their public contracts. Retain source-backed row-shape fixes and explain backend-native exceptions. Revisit batch internals through links rather than duplicating its eight primary markers.

**Primary explanation order:**

- 31 **DimensionCompiler** — `dimensioncompiler`.
- 32 **Compile Exact Expression** — `compile-exact-expression`.
- 33 **Compile Range Expression** — `compile-range-expression`.
- 34 **Compile String Match** — `compile-string-match`.
- 35 **Compile Regex Expression** — `compile-regex-expression`.
- 36 **Compile Set Expression** — `compile-set-expression`.
- 37 **Compile Threshold Expression** — `compile-threshold-expression`.
- 38 **Sentinel-Aware Ternary** — `sentinel-aware-ternary`.
- 39 **Context Value Extraction** — `context-value-extraction`.
- 44 **Context Binding Phase** — `context-binding-phase`.
- 45 **Dimension Expression Phase** — `dimension-expression-phase`.
- 106 **SelectionInfo Dataclass** — `selectioninfo-dataclass`.
- 108 **Cardinality Application** — `cardinality-application`.
- 132 **CTX_PREFIX Column Injection Pattern** — `ctx_prefix-column-injection-pattern`.

#### 10. Inside the Accumulator Engine

**Destination:** `docs-site/site/docs/chapters/10-accumulator-engine-internals/index.md`

Explain compatibility/coalescing, prime identities, anchors, expansion and frontier pruning. Distinguish prime-table capacity, combination identity overflow and aggregate-value overflow. Connect apply to the shared expression engine without calling the build output merely the globally largest rule sets.

**Primary explanation order:**

- 59 **AccumulatorCompiler** — `accumulatorcompiler`.
- 60 **Compatible Expression** — `compatible-expression`.
- 61 **Coalesce Expression** — `coalesce-expression`.
- 62 **Coalesce NA Flag** — `coalesce-na-flag`.
- 63 **Compatible Exact** — `compatible-exact`.
- 64 **Compatible Range** — `compatible-range`.
- 65 **Coalesce Exact** — `coalesce-exact`.
- 66 **Coalesce Range** — `coalesce-range`.
- 67 **Coalesce Threshold** — `coalesce-threshold`.
- 69 **Prime Number Encoding** — `prime-number-encoding`.
- 70 **Prime Table Sieve** — `prime-table-sieve`.
- 71 **Get Prime Function** — `get-prime-function`.
- 72 **Checked Multiply** — `checked-multiply`.
- 73 **Anchor Creation** — `anchor-creation`.
- 74 **Level Expansion** — `level-expansion`.
- 75 **Canonical Ordering Guard** — `canonical-ordering-guard`.
- 76 **Frontier Filter** — `frontier-filter`.
- 120 **Set Membership Compatible** — `set-membership-compatible`.
- 121 **Set Membership Coalesce** — `set-membership-coalesce`.
- 123 **Prime Table Size Cap** — `prime-table-size-cap`.
- 133 **LatticeWidthExceededError** — `latticewidthexceedederror`.

#### 11. Extending and Maintaining Both Engines

**Destination:** `docs-site/site/docs/chapters/11-extending-and-maintaining/index.md`

Provide concrete strategy, hit-policy and aggregate extension recipes with the changes needed in shared versus engine-specific code. Reuse earlier primary explanations by links; this chapter has one new primary (backend-purity enforcement), not one concept per recipe. Keep meaningful behavioral verification distinct from import/static checks.

**Primary explanation order:**

- 131 **Backend Purity Enforcement** — `backend-purity-enforcement`.

## Reviewed teaching-dependency changes

Edges point from dependent to prerequisite. The canonical graph now contains the reviewed teaching graph: ten donor edges removed, one added and the other 225 retained. The table below records the rationale. This editorial remap preserves node identities, taxonomy, source enrichment and historical CIS; it does not claim a new source profile or completed manuscript refresh.

| Operation | Dependent | Prerequisite | Rationale |
|---|---|---|---|
| remove | 10 Context Object | 4 Pydantic Model Validation | A dictionary context can be understood and used without Pydantic. Introduce facts-versus-rules in Chapter 1; optional validated models follow in Chapter 2. |
| remove | 29 Dimension Validator | 14 RANGE Strategy | Configuration validation needs the Dimension and MatchStrategy vocabulary, not the complete runtime semantics of this strategy. Explain the required fields beside the validation example; retain the detailed comparison treatment in Chapter 3. |
| remove | 29 Dimension Validator | 20 REGEX Strategy | Configuration validation needs the Dimension and MatchStrategy vocabulary, not the complete runtime semantics of this strategy. Explain the required fields beside the validation example; retain the detailed comparison treatment in Chapter 3. |
| remove | 30 Data Type Constraints | 12 EXACT Strategy | Type/strategy compatibility is an authoring constraint. Explain the scalar type and strategy family at the validation site; do not require the later full matching walkthrough as a prerequisite. |
| remove | 30 Data Type Constraints | 14 RANGE Strategy | Type/strategy compatibility is an authoring constraint. Explain the scalar type and strategy family at the validation site; do not require the later full matching walkthrough as a prerequisite. |
| remove | 30 Data Type Constraints | 15 GREATER_THAN Strategy | Type/strategy compatibility is an authoring constraint. Explain the scalar type and strategy family at the validation site; do not require the later full matching walkthrough as a prerequisite. |
| remove | 30 Data Type Constraints | 16 LESS_THAN Strategy | Type/strategy compatibility is an authoring constraint. Explain the scalar type and strategy family at the validation site; do not require the later full matching walkthrough as a prerequisite. |
| remove | 30 Data Type Constraints | 17 PREFIX Strategy | Type/strategy compatibility is an authoring constraint. Explain the scalar type and strategy family at the validation site; do not require the later full matching walkthrough as a prerequisite. |
| remove | 30 Data Type Constraints | 20 REGEX Strategy | Type/strategy compatibility is an authoring constraint. Explain the scalar type and strategy family at the validation site; do not require the later full matching walkthrough as a prerequisite. |
| remove | 94 DataType Enum | 30 Data Type Constraints | Introduce DataType values before using them to explain type/strategy constraints; the previous direction inverted that teaching order. |
| add | 30 Data Type Constraints | 94 DataType Enum | Type-compatibility guidance relies on the shared DataType vocabulary introduced in the same chapter. |

## Existing URLs and split explanations

The JSON crosswalk records every accepted/donor chapter URL and every primary anchor with a final destination. Several old pages split across the new chapters: for example, donor authoring splits into shared-model and matching chapters, decisions splits into engine usage and policy/results, and combinations splits into accumulator usage and lattice/routing. Do not choose one convenient new page and pretend it contains all the old material.

All eleven current chapters occupy their final URLs. Current chapter and appendix links use canonical destinations. The nineteen earlier URLs have static reference landing pages rather than blanket redirects: a server cannot route a URL fragment, and several earlier pages split across multiple current chapters.

All 416 retained heading fragments and their local destinations pass the built-site audit. Reference pages are intentionally absent from navigation and search. The historical seventy-question internal FAQ and chatbot JSON remain unchanged as a paired legacy export; they are not presented as the current seventy-eight-question FAQ export.

## Corrections and reuse safeguards

The crosswalk flags all 31 previously refreshed primary concepts and records concrete requirements for each. Examples include string non-concrete guards, CONTEXT_REGEX rejection, row-shape-safe range/survival expressions, Boolean null binding, constructor/output-schema validation, minimum retained ranks, complete-candidate reselection, global batch identity checks, input/output chunking limits and accumulator cached application/routing. Apply corrections to repeated claims and examples as well as primary sections.

The opening workflow additionally verifies a frontier detail that a short API summary can obscure: an unrestricted singleton can remain alongside a more specific pair because their combined-condition fingerprints differ. Preserve this in accumulator explanations and diagrams. Provenance is encoded identity, not automatically decoded rule names; __level is zero-based.

## Completed verification and remaining gates

- All 133 primary concepts occur exactly once, in their assigned chapters and approved order. All 226 teaching dependencies point backward in that order.
- All 107 Python blocks execute against the maintained source at `730a8583ee9d4fd6b52dc5350699eb66cc7487e9`, using Python 3.12.12 and Polars 1.44.2. Published text/YAML outputs and the batch chapter's displayed tables were checked. This is not a claim that every backend was exercised.
- The strict MkDocs build passes. Its 37 HTML pages contain no duplicate IDs or broken local references across 5,063 checked links and resources, with the configured `/mountainash-rules/` deployment prefix respected. Twenty cited source-file paths resolve in the maintained checkout.
- All twelve Mermaid diagrams were visually inspected. Desktop and 390-pixel mobile layouts, the nested mobile chapter menu, FAQ-to-glossary navigation and an earlier-fragment-to-current-concept path were exercised in Chromium.
- The current FAQ retains 78 questions in their original category order, with one false-premise Boolean question corrected while preserving its old anchor. The glossary retains all 97 terms. Source corrections include selection provenance, scalar versus batch violation frames, partition-filter omissions, Boolean accumulator limitations, context-key-only builds, and snapshot configuration/provenance boundaries.
- Two independent reviewers reported sixteen findings; all were resolved in the chapters and repeated appendix claims. Focused accumulator probes confirmed the consequential construction and persistence boundaries rather than assuming the intended API contract.
- Forty-three graph/profile/refresh-related files and the six archived reference bodies retain their recorded hashes. Historical node enrichment, CIS, profiles, refresh-state and the paired seventy-question legacy FAQ artifacts were not rewritten.
- Evidence: `/home/nathanielramm/.cache/claude-tmp/rules-book-completion-bu_fhgei/`. `docs-site/reconciliation-crosswalk.json` records current delivery status, per-chapter verification and URL migration alongside preserved historical evidence.
- Keep the candidate uncommitted and unpublished. A truthful profile/refresh-baseline update and disposable bounded-refresh exercise remain separately gated; editorial completion is not a completed source refresh.

## Marker-location handling

The input manuscripts use markers before headings, after headings, and grouped markers for shared explanations. The crosswalk records the actual heading/anchor and marker line separately. For example, accepted concepts 8 and 9 share adjacent markers but have separate expression/relation headings; concept 28 has a displaced marker and a later explicit Field Resolution heading. Do not infer a concept's destination from the next heading after every marker.
