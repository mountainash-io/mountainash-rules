# mountainash-rules: approved chapter and section plan

**Status: APPROVED — 2026-09-12.** The user explicitly approved this eight-chapter structure and its documented teaching-dependency changes. This authorizes graph promotion, deliberate chapter remapping and writing the complete candidate book with the confirmed FAQ/glossary. It does not authorize publishing over the live book.

## Basis and deliberate changes

- Brief: `docs-site/editorial-brief.md`, confirmed by the user on 2026-09-12.
- Candidate source: `feat/chapter-focused-book` at `94659bb0c096485c87d329f09e944577427f129f`; package source tree matches the previous profile basis. Documentation edits remain uncommitted.
- Profiles: all five facets in `docs-site/profile/`; installed profile validation and manual-field preservation passed.
- Previous canonical graph: 130 concepts and 230 edges; retained source/enrichment is preserved in the approved graph.
- Approved graph: `docs-site/learning-graph/learning-graph.json` and its CSV, promoted from the reconciled/scored candidate (133 concepts, 235 edges). Installed reconciliation succeeded before approval. Chapter assignments are deliberately remapped to the approved coverage table below; source provenance and other enrichment remain intact.
- Three approved additions: 131 Backend Purity Enforcement; 132 CTX_PREFIX Column Injection Pattern; 133 LatticeWidthExceededError. Their source basis is respectively `tests/test_backend_purity.py`, `core/constants.py` plus context-binding/compilation uses, and `engines/accumulator/primes.py` plus its build-time raising path.
- Teaching prerequisites are not source-code call dependencies. Twelve implementation-only prerequisite edges are removed and ten meaningful teaching edges added, alongside the seven edges for new concepts. Every changed dependency is accounted for below and was included in the user's approval.

The first proposal largely repeated the old eleven module-shaped chapters. The approved plan instead groups work around authoring a library, making a decision, scoring contexts, and managing combinations, followed by detailed execution/search mechanisms and extension recipes. Eight chapters follow those reader tasks, not a target chapter count. The section names and slugs below are approved writing targets.

## Audience coverage and reading progression

| Facet | Primary treatment and shared explanations |
|---|---|
| users | Chapters 1–5 form a complete practical path: mental model, rule authoring, single decisions, batches, combinations/persistence/routing. Result semantics are taught with the API that returns them. |
| maintainers | Chapters 6–7 explain actual algorithms after Chapters 3–5 establish their observable contracts. Chapter 8 applies those invariants to changes and compatibility. |
| contributors | Chapter 8 provides concrete recipes, building on the shared implementation explanations in Chapters 6–7 rather than duplicating them in a separate audience track. |
| backend-architecture | Chapter 1 introduces portable abstractions; Chapters 6–7 explain how both engines use them; Chapter 8 states and examines the enforced backend boundary and documented exceptions. |
| broader-hype | Factual motivation and capability context in Chapter 1/front matter only. No standalone marketing chapter, unverified comparative claims or performance promises. |

The progression is concepts -> package use -> internals/extension. Users may stop after Chapter 5; maintainers and contributors continue. Each graph concept has one primary explanation; later implementation detail, recipes and appendix answers link back rather than duplicate concept markers. A short usage warning may point forward to an internal limit explanation without turning the search algorithm into an API prerequisite.

## Ordered chapters

### `01-rule-tables-and-decisions` — From Rule Tables to Decisions

**Purpose:** Establish the smallest shared mental model before asking the reader to configure an engine: rules are data, contexts supply facts, and matching can be true, false or unknown. A small worked rule table motivates the rest of the book; it is not an eleven-module tour.

**Audiences:** All readers; factual capability framing comes from broader-hype, not a marketing chapter.

**Depth and observable outcomes:** Explain ternary outcomes and the distinction between missing context and rule wildcards carefully. Introduce expressions and relations as portable public abstractions; defer compiler algorithms and backend-exemption mechanics.

**Why this boundary:** One shared vocabulary chapter avoids repeating the same sentinel, context and portability explanations separately for each engine.

**Selective reuse:** Select verified explanations from old 01-foundation-concepts and the existing home page. Do not inherit their full headings or promotional claims.

**Visual treatment:** A Mermaid data-flow diagram: rule library + context -> decision results. Use a small table, not an animation, for ternary truth cases.

**Source references:** `src/mountainash_rules/core/constants.py`, `src/mountainash_rules/core/context.py`, `README.md`, `docs-site/profile-readme.md`.

**Ordered sections and primary concepts:**

- **What a rule table represents** — 3 Match Strategy Patterns, 5 Vectorized Evaluation, 6 Backend-Agnostic Design, 7 DataFrame as Rule Store.
- **Matches, unknowns and wildcards** — 1 Ternary Logic, 2 Sentinel Values.
- **Context and portable execution** — 4 Pydantic Model Validation, 10 Context Object, 8 Mountainash Expressions, 9 Mountainash Relations.

### `02-authoring-rule-libraries` — Authoring and Evolving Rule Libraries

**Purpose:** Show how to author and evolve a usable rule library: give columns meaning, select matching semantics, reject invalid combinations and serialize metadata.

**Audiences:** Users first; contributors reuse the same public semantics when implementing strategies.

**Depth and observable outcomes:** Begin with a small dimension/metadata example, then develop the strategy families and their edge cases as sections of that task. Cover null/wildcard behavior, explicit field resolution, temporal types, bool restrictions, set normalization and YAML round-trip. Explain what each validation failure means rather than dumping Pydantic fields.

**Why this boundary:** Metadata, strategies and validation are one authoring activity; a standalone strategy catalogue before readers see any rule library is unnecessary.

**Selective reuse:** Combine verified material from old 02-match-strategies and 03-dimension-model, plus set-wildcard examples from old 04-dimension-compiler. Reorganize around authored rules, not source classes.

**Visual treatment:** Use strategy and invalid-combination tables with worked rule rows. No separate interactive surface is proposed.

**Source references:** `src/mountainash_rules/core/constants.py`, `src/mountainash_rules/core/dimension.py`, `src/mountainash_rules/core/set_wildcard.py`, `docs/user-quickstart.md`, `README.md`.

**Ordered sections and primary concepts:**

- **Give rule columns meaning** — 11 MatchStrategy Enum, 23 DimensionRole Enum, 24 CONSTRAINT Role, 25 CONTEXT_KEY Role, 26 Dimension Class, 27 DimensionsMetadata, 28 Field Resolution.
- **Choose matching semantics** — 12 EXACT Strategy, 91 EXACT_KEY Strategy, 13 NOT_EQUAL Strategy, 14 RANGE Strategy, 15 GREATER_THAN Strategy, 16 LESS_THAN Strategy, 17 PREFIX Strategy, 18 SUFFIX Strategy, 19 CONTAINS Strategy, 20 REGEX Strategy, 92 CONTEXT_REGEX Strategy, 21 SET_MEMBERSHIP Strategy, 22 SET_EXCLUSION Strategy, 93 Bool Ternary Comparison.
- **Validate, serialize and evolve a library** — 29 Dimension Validator, 30 Data Type Constraints, 94 DataType Enum, 95 Temporal Sentinels, 96 YAML Round-Trip, 98 Set Wildcard Sentinel, 99 Set Value Normalization.

### `03-evaluating-decisions` — Evaluating, Selecting and Explaining Decisions

**Purpose:** Take a rule library through one decision, selection and explanation. Keep engine construction, result interpretation and hit policies together so readers can finish a complete practical workflow.

**Audiences:** Users primary; maintainers get an explicit observable contract that the later implementation chapter must preserve.

**Depth and observable outcomes:** Provide source-backed end-to-end examples covering multiple/no survivors, specificity and tie ordering, policy assertions, default/reapplied policies, accessors and result filters. Distinguish RuleResult.explain from engine-level ExplainResult; explain what was discarded or retained and why. The survival section teaches the outcome rule over ternary values, not expression-tree construction.

**Why this boundary:** Construction, policy and results belong to a single reader task; three separate module-shaped chapters would interrupt the decision walkthrough.

**Selective reuse:** Synthesize old 05-expression-rules-engine, 06-hit-policies and 07-expression-engine-results, with accurate FAQ answers linked rather than copied twice.

**Visual treatment:** Mermaid showing match -> survive/rank -> policy -> returned/explained result, plus concrete result tables.

**Source references:** `src/mountainash_rules/engines/filter/engine.py`, `src/mountainash_rules/core/result.py`, `src/mountainash_rules/core/hit_policy.py`, `docs/user-quickstart.md`, `README.md`.

**Ordered sections and primary concepts:**

- **Construct an engine and evaluate a context** — 40 ExpressionRulesEngine, 41 Engine Construction, 42 Convenience vs Advanced Path, 43 Single-Pass Evaluation.
- **Understand survival and ranking** — 46 Survival Computation, 47 Specificity Scoring, 48 Rank Assignment.
- **Read the result contract** — 49 RuleResult Class, 50 Survivors Accessor, 51 Best Match Accessor, 52 Count Accessor, 53 Active Dimensions.
- **Choose and reapply a hit policy** — 100 HitPolicy Enum, 101 Collect Policy, 102 Unique Policy, 103 First And Priority Policy, 104 Any Policy, 105 Rule Order Policy, 107 HitPolicyViolationError, 97 Table-Level Hit Policy Fields, 111 RuleResult Select Method.
- **Explain a decision and refine the result** — 54 RuleResult Explain Method, 109 ExplainResult Class, 110 Engine-Level Explain, 58 Observability Columns, 55 At Least Filter, 56 Top N Filtering, 57 Min Specificity Filter.

### `04-scoring-batches` — Scoring Batches of Contexts

**Purpose:** Extend a working single-context decision to many contexts while making per-context identity, ranking, backend alignment and chunking explicit.

**Audiences:** Users running larger workloads; maintainers responsible for batch/single-context consistency.

**Depth and observable outcomes:** Work through batch inputs and one context extracted from the result. Explain the cross-join work shape and chunking tradeoff without asserting unmeasured speedups. Show how policy and ranking remain per-context and what backend conforming means for accepted input types.

**Why this boundary:** Batch evaluation introduces a distinct scale/identity problem worth its own chapter, rather than hiding it in the single-context tutorial or compiler reference.

**Selective reuse:** Use the old 08-batch-evaluation complete workflow after verifying its calls and expected outputs against source.

**Visual treatment:** Mermaid showing contexts x rules -> grouped evaluation -> per-context results; tables expose grouping and chunk boundaries.

**Source references:** `src/mountainash_rules/engines/filter/engine.py`, `src/mountainash_rules/core/batch_result.py`, `README.md`.

**Ordered sections and primary concepts:**

- **Prepare a batch and understand the work** — 113 Evaluate Batch Method, 114 Batch Context Preparation, 115 Cross-Join Evaluation.
- **Rank and conform per-context results** — 116 Per-Context Ranking, 117 Backend Conforming, 112 BatchRuleResult Class.
- **Bound the work and inspect one context** — 118 Chunked Batch Evaluation, 119 For Context Accessor.

### `05-combining-and-persisting-rules` — Combining, Persisting and Routing Rules

**Purpose:** Teach the second engine as a practical build/apply workflow: combine compatible rules, inspect results, partition and route contexts, and persist the resulting lattice.

**Audiences:** Users needing combination/aggregation decisions; maintainers get the public artifact and failure contracts before learning the search implementation.

**Depth and observable outcomes:** Use a complete example with aggregate operations, build vs apply, coalesced values, NA flags, depth and provenance. Explain caching scope, keyed partitions, automatic routing and ambiguous routes. Demonstrate save/load contracts without assuming persistence solves every backend or overflow constraint. Flag construction limits with forward links to their detailed treatment in Chapter 7; no prime sieve is required before using a lattice.

**Why this boundary:** Build, inspect, apply, route and persist describe the life cycle of one artifact. Keep them together instead of forcing readers through an internal compiler chapter first.

**Selective reuse:** Extract the public workflow from old 10-accumulator-engine and 11-lattice-structures-and-results; leave search and prime-encoding explanations for Chapter 7.

**Visual treatment:** Mermaid separating build-time and apply-time operations, including saved lattice and partition routing. Lattice-result tables show contributing rules.

**Source references:** `src/mountainash_rules/engines/accumulator/engine.py`, `src/mountainash_rules/engines/accumulator/lattice.py`, `src/mountainash_rules/engines/accumulator/result.py`, `src/mountainash_rules/engines/accumulator/aggregate.py`, `docs/user-quickstart.md`.

**Ordered sections and primary concepts:**

- **Build compatible rule combinations** — 68 AccumulatorEngine, 87 Aggregate Model, 127 Aggregate Min Max Product, 77 Lattice Class, 78 Lattice Combinations.
- **Inspect the lattice artifact** — 80 Coalesced Columns, 81 NA Flag Columns, 82 Combination Depth, 126 Lattice Is Composed.
- **Apply a lattice and interpret aggregate results** — 83 AccumulatorResult Class, 84 Accumulated Aggregates, 85 Provenance Accessor, 86 Depths Accessor, 122 Apply-Phase Caching.
- **Partition and route contexts** — 79 Lattice Partition Key, 88 Partition Key Filtering, 89 Build All Partitions, 90 Apply Auto Selection, 128 LatticeIndex Router, 129 AmbiguousPartitionError, 130 EXACT_KEY Partition Routing.
- **Save and reload a lattice** — 124 Lattice Save Method, 125 Lattice Load Method.

### `06-expression-execution-internals` — Inside Expression and Batch Evaluation

**Purpose:** Explain how the already-understood decision contract is implemented with portable expressions and temporary context columns, then connect those expressions to evaluation and selection.

**Audiences:** Maintainers, contributors and backend-architecture readers; optional deeper reading for users.

**Depth and observable outcomes:** Trace actual source through DimensionCompiler dispatch, sentinel-aware expressions, context extraction, CTX_PREFIX injection, strategy-family compilation and the dimension-expression phase. Show where policy metadata/cardinality are applied. Relate batch behavior to the same mechanisms without duplicating its public tutorial. Compare conceptual outcomes to the actual expression operations and documented backend fallback.

**Why this boundary:** One implementation chapter follows the whole decision path across module boundaries. It is not an introductory compiler prerequisite for API usage.

**Selective reuse:** Use verified mechanisms from old 04-dimension-compiler and pipeline internals from old 05-expression-rules-engine and 06-hit-policies. The reader has now used these APIs; motivation precedes implementation.

**Visual treatment:** Mermaid tracing metadata -> compiled expressions -> bound context columns -> evaluation/selection, paired with actual source-backed expression examples.

**Source references:** `src/mountainash_rules/core/compiler.py`, `src/mountainash_rules/core/context.py`, `src/mountainash_rules/core/constants.py`, `src/mountainash_rules/core/hit_policy.py`, `src/mountainash_rules/engines/filter/engine.py`.

**Ordered sections and primary concepts:**

- **From metadata to expression trees** — 31 DimensionCompiler, 38 Sentinel-Aware Ternary, 39 Context Value Extraction.
- **Bind contexts through temporary columns** — 44 Context Binding Phase, 132 CTX_PREFIX Column Injection Pattern.
- **Compile the strategy families** — 32 Compile Exact Expression, 33 Compile Range Expression, 34 Compile String Match, 35 Compile Regex Expression, 36 Compile Set Expression, 37 Compile Threshold Expression.
- **Evaluate expressions and apply selection** — 45 Dimension Expression Phase, 106 SelectionInfo Dataclass, 108 Cardinality Application.

### `07-combination-search-internals` — Inside Combination Search

**Purpose:** Explain compatibility/coalescing algebra and the search that constructs the public lattice already used in Chapter 5, including separate identity-width and prime-table limits.

**Audiences:** Maintainers, contributors and backend-architecture readers.

**Depth and observable outcomes:** Treat exact/range/threshold/set compatibility and coalescing in depth, then prime identities, sieve/table access, checked multiplication, anchor creation, level expansion, canonical ordering and frontier filtering. Distinguish the prime table size cap from LatticeWidthExceededError, and distinguish guarded identity overflow from backend-defined aggregate-value overflow. Explain invariants, failure behavior and remedies; do not claim a universal fixed clique-width bound.

**Why this boundary:** Compatibility algebra and search jointly explain why a lattice contains exactly its combinations; they belong together after the practical build/apply chapter.

**Selective reuse:** Combine internal mechanisms from old 09-accumulator-compiler, 10-accumulator-engine and 11-lattice-structures-and-results. Cross-link public lattice/provenance explanations rather than repeating their primary exposition.

**Visual treatment:** A small Mermaid combination-search DAG showing anchors, expansion and pruning. Use a worked integer/product table for overflow limits.

**Source references:** `src/mountainash_rules/engines/accumulator/compiler.py`, `src/mountainash_rules/engines/accumulator/engine.py`, `src/mountainash_rules/engines/accumulator/primes.py`, `src/mountainash_rules/engines/accumulator/lattice.py`.

**Ordered sections and primary concepts:**

- **Compile compatibility predicates** — 59 AccumulatorCompiler, 60 Compatible Expression, 63 Compatible Exact, 64 Compatible Range, 120 Set Membership Compatible.
- **Coalesce compatible values and NA flags** — 61 Coalesce Expression, 62 Coalesce NA Flag, 65 Coalesce Exact, 66 Coalesce Range, 67 Coalesce Threshold, 121 Set Membership Coalesce.
- **Encode identity and guard integer bounds** — 69 Prime Number Encoding, 70 Prime Table Sieve, 71 Get Prime Function, 72 Checked Multiply, 123 Prime Table Size Cap.
- **Expand, order and prune the search** — 73 Anchor Creation, 74 Level Expansion, 75 Canonical Ordering Guard, 76 Frontier Filter, 133 LatticeWidthExceededError.

### `08-extending-and-maintaining` — Extending and Maintaining the Engines

**Purpose:** Turn the previous explanations into concrete extension and maintenance work without re-teaching every concept or copying repository contribution policy.

**Audiences:** Contributors and maintainers; backend-architecture readers review the enforced boundary.

**Depth and observable outcomes:** Provide four source-backed recipes: a filter match strategy, an accumulator strategy, an aggregate operation and a hit policy. Identify the code paths and observable tests that each change must address. Explain the actual backend-purity check, its documented exceptions and backend-parity expectations. Link repository contribution/process guidance rather than duplicating it. Only backend purity needs a new primary concept marker here; recipes apply concepts already explained.

**Why this boundary:** Applied change workflows deserve a synthesis chapter, not duplicated architecture chapters for each audience. The only single new primary concept does not limit the depth of these recipes.

**Selective reuse:** Use contributor/maintainer facet recipes and verify them against source/tests. Old chapter examples may illustrate mechanisms, not substitute for complete extension recipes.

**Visual treatment:** Use recipe/check tables and links to earlier diagrams. No redundant architecture diagram or MicroSim is needed.

**Source references:** `tests/test_backend_purity.py`, `src/mountainash_rules/core/constants.py`, `src/mountainash_rules/core/compiler.py`, `src/mountainash_rules/engines/accumulator/compiler.py`, `src/mountainash_rules/engines/accumulator/aggregate.py`, `src/mountainash_rules/core/hit_policy.py`, `CONTRIBUTING.md`.

**Ordered sections and primary concepts:**

- **Protect the backend boundary** — 131 Backend Purity Enforcement.
- **Add a match or accumulator strategy** — application/synthesis of earlier concepts; no duplicate primary marker.
- **Add an aggregate or hit policy** — application/synthesis of earlier concepts; no duplicate primary marker.
- **Prove behavior and maintain compatibility** — application/synthesis of earlier concepts; no duplicate primary marker.

## Appendices and navigation contract

Both appendices are confirmed in the brief; the chapter organization above is approved.

- **FAQ** at `site/docs/faq.md`: review the actual source `site/docs/learning-graph/faq.md` and internal legacy FAQ material. Organize retained accurate answers around authoring rules, decision/selection/explanation, batches, combination workflows and implementation/extension questions. Link answers to the relevant primary section in Chapters 2–8. Existing answers are reuse candidates, not automatically verified because source was unchanged. Unsupported `docs-site/learning-graph/faq-chatbot-training.json` remains untouched; no marker-export conversion is authorized.
- **Glossary** at `site/docs/glossary.md`: create a focused glossary from the confirmed concepts. Definitions link to the primary explanation: ternary/sentinel/context to Chapter 1; dimensions and strategies to Chapter 2; survival/specificity/policy/results to Chapter 3; batch/chunk/context identity to Chapter 4; lattice/partition/aggregate/provenance to Chapter 5; compiler/binding/CTX_PREFIX to Chapter 6; compatibility/coalescing/prime identity/search limits to Chapter 7; backend purity to Chapter 8.
- Root chapter paths are `docs-site/site/docs/chapters/<chapter-slug>/index.md`. Section labels in the coverage table below are intended headings; final MkDocs anchors must be checked against generated pages before appendix links are accepted. No provisional link is described as live.
- Prefer the six concrete Mermaid diagrams described above and worked tables. No MicroSim is currently proposed: the required explanations can be covered by inspectable examples and diagrams. If writing exposes a genuine interaction need, justify it explicitly rather than inheriting hypothetical examples as approved assets.
- Do not publish this plan, the brief, graph JSON/CSV, graph reports or viewer. No quiz generation. Existing hooks, licensing and site URL/branch behavior are preserved during the later approved content change.

## Reviewed prerequisite changes

Edges are dependent -> prerequisite. This is an explicitly approved editorial change, not a change hidden inside conversion. Only the following ten existing concepts change their immediate dependencies; all other old edges remain. Each old node's ID, label, source and unknown enrichment is preserved; chapter mappings are deliberately updated to the approved coverage table.

| Concept | Previous prerequisite IDs | Approved prerequisite IDs | Reason |
|---|---|---|---|
| 40 ExpressionRulesEngine | 31, 9, 39 | 27, 9, 10 | Constructing and using the public engine requires dimension metadata, the relation abstraction and an input context; compiler construction and context-extraction implementation are not usage prerequisites. |
| 46 Survival Computation | 45, 38, 1 | 43, 1 | Explain survival as the observable all-dimensions acceptance rule over ternary outcomes after evaluation, without requiring each compiled expression implementation. |
| 54 RuleResult Explain Method | 49, 38 | 49, 1 | Reading RuleResult.explain requires the result wrapper and ternary outcome meanings, not construction of sentinel-aware expression trees. |
| 58 Observability Columns | 43, 38 | 43, 1 | Interpreting observability columns requires evaluation and ternary outcome meanings, not expression-tree implementation. |
| 68 AccumulatorEngine | 59, 27, 40 | 27, 40 | The public accumulator workflow builds on shared metadata and the contrasting filtering workflow, not its internal compatibility compiler. |
| 77 Lattice Class | 76, 27 | 68, 27 | A lattice is the public artifact produced by the accumulator from dimension metadata; users do not need frontier-search implementation before using it. |
| 80 Coalesced Columns | 77, 61 | 77, 78 | Read coalesced lattice columns as the combined values of compatible rules; generating their coalesce expressions is later implementation detail. |
| 81 NA Flag Columns | 77, 62 | 77, 2 | Interpret lattice NA flags using the sentinel distinction; the internal coalesce-NA expression builder is not a reading prerequisite. |
| 82 Combination Depth | 77, 74 | 77, 78 | Combination depth counts participating rules in a lattice combination; the level-expansion algorithm explains its construction later. |
| 85 Provenance Accessor | 83, 72 | 83, 78 | The provenance accessor exposes contributing rule combinations; checked prime multiplication explains internal identity safety, not the accessor contract. |

The three additions depend on existing material: 131 -> 6, 8, 9; 132 -> 44, 8; 133 -> 72, 74. None renames or replaces an existing concept. The user's graph/plan approval includes these dependency choices; source-code dependency facts themselves are not changed.

## CIS and elaboration guidance

Scores below come from the actual installed graph reconciliation operation, using the retained rule `CIS(x) = 1 + sum(CIS(d) for direct dependents d)`. The book-wide maximum is **615**, at concept 3. Use **that one maximum across all chapters**; do not normalize independently within a chapter or substitute direct edge count for CIS.

CIS is a topology-derived planning aid, not proof that a topic is objectively important or permission to omit a low-score API/error. Follow the retained chapter-content-generator's book-wide elaboration policy. Give reused foundational concepts sufficient worked explanation; preserve the confirmed thorough usage and internals treatment even where a leaf has CIS 1. The primary-assignment table records actual scores and order. Concrete word counts and every worked example are writing decisions under this approved-scope proposal, not invented measurements here.

## Primary coverage and prerequisite order

One row per concept. `Order` is the global primary-explanation order within the ordered chapters/sections above; it is not a concept-ID renumbering. Main checked all **133** primary assignments and all **235** candidate edges: no duplicate/missing concept and no prerequisite placed at or after its dependent. Chapter 8's recipe sections apply previously taught concepts; their size is not determined by having one new primary concept.

| Concept ID | Chapter slug | Section | Order | CIS |
|---|---|---|---|---|
| 3 | 01-rule-tables-and-decisions | What a rule table represents | 1 | 615 |
| 5 | 01-rule-tables-and-decisions | What a rule table represents | 2 | 408 |
| 6 | 01-rule-tables-and-decisions | What a rule table represents | 3 | 176 |
| 7 | 01-rule-tables-and-decisions | What a rule table represents | 4 | 179 |
| 1 | 01-rule-tables-and-decisions | Matches, unknowns and wildcards | 5 | 76 |
| 2 | 01-rule-tables-and-decisions | Matches, unknowns and wildcards | 6 | 32 |
| 4 | 01-rule-tables-and-decisions | Context and portable execution | 7 | 366 |
| 10 | 01-rule-tables-and-decisions | Context and portable execution | 8 | 123 |
| 8 | 01-rule-tables-and-decisions | Context and portable execution | 9 | 174 |
| 9 | 01-rule-tables-and-decisions | Context and portable execution | 10 | 124 |
| 11 | 02-authoring-rule-libraries | Give rule columns meaning | 11 | 365 |
| 23 | 02-authoring-rule-libraries | Give rule columns meaning | 12 | 249 |
| 24 | 02-authoring-rule-libraries | Give rule columns meaning | 13 | 1 |
| 25 | 02-authoring-rule-libraries | Give rule columns meaning | 14 | 8 |
| 26 | 02-authoring-rule-libraries | Give rule columns meaning | 15 | 239 |
| 27 | 02-authoring-rule-libraries | Give rule columns meaning | 16 | 202 |
| 28 | 02-authoring-rule-libraries | Give rule columns meaning | 17 | 1 |
| 12 | 02-authoring-rule-libraries | Choose matching semantics | 18 | 35 |
| 91 | 02-authoring-rule-libraries | Choose matching semantics | 19 | 2 |
| 13 | 02-authoring-rule-libraries | Choose matching semantics | 20 | 1 |
| 14 | 02-authoring-rule-libraries | Choose matching semantics | 21 | 17 |
| 15 | 02-authoring-rule-libraries | Choose matching semantics | 22 | 11 |
| 16 | 02-authoring-rule-libraries | Choose matching semantics | 23 | 11 |
| 17 | 02-authoring-rule-libraries | Choose matching semantics | 24 | 6 |
| 18 | 02-authoring-rule-libraries | Choose matching semantics | 25 | 3 |
| 19 | 02-authoring-rule-libraries | Choose matching semantics | 26 | 3 |
| 20 | 02-authoring-rule-libraries | Choose matching semantics | 27 | 8 |
| 92 | 02-authoring-rule-libraries | Choose matching semantics | 28 | 1 |
| 21 | 02-authoring-rule-libraries | Choose matching semantics | 29 | 9 |
| 22 | 02-authoring-rule-libraries | Choose matching semantics | 30 | 3 |
| 93 | 02-authoring-rule-libraries | Choose matching semantics | 31 | 1 |
| 29 | 02-authoring-rule-libraries | Validate, serialize and evolve a library | 32 | 1 |
| 30 | 02-authoring-rule-libraries | Validate, serialize and evolve a library | 33 | 3 |
| 94 | 02-authoring-rule-libraries | Validate, serialize and evolve a library | 34 | 2 |
| 95 | 02-authoring-rule-libraries | Validate, serialize and evolve a library | 35 | 1 |
| 96 | 02-authoring-rule-libraries | Validate, serialize and evolve a library | 36 | 1 |
| 98 | 02-authoring-rule-libraries | Validate, serialize and evolve a library | 37 | 4 |
| 99 | 02-authoring-rule-libraries | Validate, serialize and evolve a library | 38 | 1 |
| 40 | 03-evaluating-decisions | Construct an engine and evaluate a context | 39 | 114 |
| 41 | 03-evaluating-decisions | Construct an engine and evaluate a context | 40 | 2 |
| 42 | 03-evaluating-decisions | Construct an engine and evaluate a context | 41 | 1 |
| 43 | 03-evaluating-decisions | Construct an engine and evaluate a context | 42 | 54 |
| 46 | 03-evaluating-decisions | Understand survival and ranking | 43 | 38 |
| 47 | 03-evaluating-decisions | Understand survival and ranking | 44 | 37 |
| 48 | 03-evaluating-decisions | Understand survival and ranking | 45 | 32 |
| 49 | 03-evaluating-decisions | Read the result contract | 46 | 16 |
| 50 | 03-evaluating-decisions | Read the result contract | 47 | 1 |
| 51 | 03-evaluating-decisions | Read the result contract | 48 | 1 |
| 52 | 03-evaluating-decisions | Read the result contract | 49 | 1 |
| 53 | 03-evaluating-decisions | Read the result contract | 50 | 1 |
| 100 | 03-evaluating-decisions | Choose and reapply a hit policy | 51 | 15 |
| 101 | 03-evaluating-decisions | Choose and reapply a hit policy | 52 | 1 |
| 102 | 03-evaluating-decisions | Choose and reapply a hit policy | 53 | 2 |
| 103 | 03-evaluating-decisions | Choose and reapply a hit policy | 54 | 2 |
| 104 | 03-evaluating-decisions | Choose and reapply a hit policy | 55 | 2 |
| 105 | 03-evaluating-decisions | Choose and reapply a hit policy | 56 | 1 |
| 107 | 03-evaluating-decisions | Choose and reapply a hit policy | 57 | 1 |
| 97 | 03-evaluating-decisions | Choose and reapply a hit policy | 58 | 1 |
| 111 | 03-evaluating-decisions | Choose and reapply a hit policy | 59 | 1 |
| 54 | 03-evaluating-decisions | Explain a decision and refine the result | 60 | 2 |
| 109 | 03-evaluating-decisions | Explain a decision and refine the result | 61 | 2 |
| 110 | 03-evaluating-decisions | Explain a decision and refine the result | 62 | 1 |
| 58 | 03-evaluating-decisions | Explain a decision and refine the result | 63 | 1 |
| 55 | 03-evaluating-decisions | Explain a decision and refine the result | 64 | 1 |
| 56 | 03-evaluating-decisions | Explain a decision and refine the result | 65 | 1 |
| 57 | 03-evaluating-decisions | Explain a decision and refine the result | 66 | 1 |
| 113 | 04-scoring-batches | Prepare a batch and understand the work | 67 | 7 |
| 114 | 04-scoring-batches | Prepare a batch and understand the work | 68 | 5 |
| 115 | 04-scoring-batches | Prepare a batch and understand the work | 69 | 3 |
| 116 | 04-scoring-batches | Rank and conform per-context results | 70 | 2 |
| 117 | 04-scoring-batches | Rank and conform per-context results | 71 | 1 |
| 112 | 04-scoring-batches | Rank and conform per-context results | 72 | 2 |
| 118 | 04-scoring-batches | Bound the work and inspect one context | 73 | 1 |
| 119 | 04-scoring-batches | Bound the work and inspect one context | 74 | 1 |
| 68 | 05-combining-and-persisting-rules | Build compatible rule combinations | 75 | 48 |
| 87 | 05-combining-and-persisting-rules | Build compatible rule combinations | 76 | 3 |
| 127 | 05-combining-and-persisting-rules | Build compatible rule combinations | 77 | 1 |
| 77 | 05-combining-and-persisting-rules | Build compatible rule combinations | 78 | 22 |
| 78 | 05-combining-and-persisting-rules | Build compatible rule combinations | 79 | 5 |
| 80 | 05-combining-and-persisting-rules | Inspect the lattice artifact | 80 | 1 |
| 81 | 05-combining-and-persisting-rules | Inspect the lattice artifact | 81 | 1 |
| 82 | 05-combining-and-persisting-rules | Inspect the lattice artifact | 82 | 2 |
| 126 | 05-combining-and-persisting-rules | Inspect the lattice artifact | 83 | 1 |
| 83 | 05-combining-and-persisting-rules | Apply a lattice and interpret aggregate results | 84 | 5 |
| 84 | 05-combining-and-persisting-rules | Apply a lattice and interpret aggregate results | 85 | 1 |
| 85 | 05-combining-and-persisting-rules | Apply a lattice and interpret aggregate results | 86 | 1 |
| 86 | 05-combining-and-persisting-rules | Apply a lattice and interpret aggregate results | 87 | 1 |
| 122 | 05-combining-and-persisting-rules | Apply a lattice and interpret aggregate results | 88 | 1 |
| 79 | 05-combining-and-persisting-rules | Partition and route contexts | 89 | 1 |
| 88 | 05-combining-and-persisting-rules | Partition and route contexts | 90 | 6 |
| 89 | 05-combining-and-persisting-rules | Partition and route contexts | 91 | 5 |
| 90 | 05-combining-and-persisting-rules | Partition and route contexts | 92 | 1 |
| 128 | 05-combining-and-persisting-rules | Partition and route contexts | 93 | 3 |
| 129 | 05-combining-and-persisting-rules | Partition and route contexts | 94 | 1 |
| 130 | 05-combining-and-persisting-rules | Partition and route contexts | 95 | 1 |
| 124 | 05-combining-and-persisting-rules | Save and reload a lattice | 96 | 2 |
| 125 | 05-combining-and-persisting-rules | Save and reload a lattice | 97 | 1 |
| 31 | 06-expression-execution-internals | From metadata to expression trees | 98 | 13 |
| 38 | 06-expression-execution-internals | From metadata to expression trees | 99 | 1 |
| 39 | 06-expression-execution-internals | From metadata to expression trees | 100 | 3 |
| 44 | 06-expression-execution-internals | Bind contexts through temporary columns | 101 | 2 |
| 132 | 06-expression-execution-internals | Bind contexts through temporary columns | 102 | 1 |
| 32 | 06-expression-execution-internals | Compile the strategy families | 103 | 2 |
| 33 | 06-expression-execution-internals | Compile the strategy families | 104 | 2 |
| 34 | 06-expression-execution-internals | Compile the strategy families | 105 | 2 |
| 35 | 06-expression-execution-internals | Compile the strategy families | 106 | 2 |
| 36 | 06-expression-execution-internals | Compile the strategy families | 107 | 2 |
| 37 | 06-expression-execution-internals | Compile the strategy families | 108 | 2 |
| 45 | 06-expression-execution-internals | Evaluate expressions and apply selection | 109 | 1 |
| 106 | 06-expression-execution-internals | Evaluate expressions and apply selection | 110 | 2 |
| 108 | 06-expression-execution-internals | Evaluate expressions and apply selection | 111 | 1 |
| 59 | 07-combination-search-internals | Compile compatibility predicates | 112 | 31 |
| 60 | 07-combination-search-internals | Compile compatibility predicates | 113 | 11 |
| 63 | 07-combination-search-internals | Compile compatibility predicates | 114 | 5 |
| 64 | 07-combination-search-internals | Compile compatibility predicates | 115 | 5 |
| 120 | 07-combination-search-internals | Compile compatibility predicates | 116 | 2 |
| 61 | 07-combination-search-internals | Coalesce compatible values and NA flags | 117 | 16 |
| 62 | 07-combination-search-internals | Coalesce compatible values and NA flags | 118 | 1 |
| 65 | 07-combination-search-internals | Coalesce compatible values and NA flags | 119 | 5 |
| 66 | 07-combination-search-internals | Coalesce compatible values and NA flags | 120 | 5 |
| 67 | 07-combination-search-internals | Coalesce compatible values and NA flags | 121 | 5 |
| 121 | 07-combination-search-internals | Coalesce compatible values and NA flags | 122 | 1 |
| 69 | 07-combination-search-internals | Encode identity and guard integer bounds | 123 | 14 |
| 70 | 07-combination-search-internals | Encode identity and guard integer bounds | 124 | 7 |
| 71 | 07-combination-search-internals | Encode identity and guard integer bounds | 125 | 5 |
| 72 | 07-combination-search-internals | Encode identity and guard integer bounds | 126 | 3 |
| 123 | 07-combination-search-internals | Encode identity and guard integer bounds | 127 | 1 |
| 73 | 07-combination-search-internals | Expand, order and prune the search | 128 | 5 |
| 74 | 07-combination-search-internals | Expand, order and prune the search | 129 | 4 |
| 75 | 07-combination-search-internals | Expand, order and prune the search | 130 | 1 |
| 76 | 07-combination-search-internals | Expand, order and prune the search | 131 | 1 |
| 133 | 07-combination-search-internals | Expand, order and prune the search | 132 | 1 |
| 131 | 08-extending-and-maintaining | Protect the backend boundary | 133 | 1 |

## Approval and next actions

The user approved the eight-chapter reader journey, its section boundaries and explicit teaching-dependency changes on 2026-09-12. FAQ/glossary inclusion and thorough usage/internals were confirmed previously. No unresolved identity conflict is hidden behind successful validation.

Promote the reviewed internal graph and CSV in the candidate worktree and deliberately remap chapter assignments to the approved coverage table, preserving source and unknown metadata. Validate that promotion with the installed tools, then write every approved chapter and appendix. Retire intermediate proposal/candidate files after promotion. Verify the complete candidate build, real pages/diagrams, excluded publication surfaces and targeted refresh before P9 is claimed. Publishing remains a separate acceptance gate.
