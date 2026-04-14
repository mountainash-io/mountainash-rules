# Rules Engine Landscape & Roadmap

**Status:** Analysis only. Not an implementation spec.
**Date:** 2026-04-07
**Author:** Nathaniel Ramm (with Claude)
**Companion to:** `2026-04-07-additive-rules-architecture-analysis.md`

---

## 1. Purpose & scope

The architecture-analysis doc describes two engine patterns that mountainash-utils-rules ships or proposes — the **filter engine** (existing, evaluates rules independently against a context) and the **accumulator engine** (proposed, builds a lattice of maximal consistent rule combinations and applies a context to it). This doc places those two engines in the wider landscape of rules-and-reasoning systems and uses that placement to seed a forward-looking roadmap of engine types Mountain Ash could build next.

The scope is **targeted**: only patterns that share machinery with one of our two engines are surveyed in detail. Adjacent fields (CEP, ontology reasoning, theorem proving) are mentioned only to clarify what we are *not*. The goal is not a comprehensive survey of rules engines as a field — that would be a textbook chapter — but a reference document that future designers, researchers, and contributors can use to (a) quickly locate any of our engines in the literature, (b) find prior art when designing extensions, and (c) understand which architectural choices are well-precedented and which are unusual.

The roadmap half (section 5) proposes three candidate future engines, each anchored in a gap surfaced by the survey and validated against a real Mountain Ash use case. Roadmap items are EXPLORATORY only; this doc commits to nothing.

The novelty question — *is the accumulator engine a new thing?* — is answered explicitly in section 4.2. The short answer is: **the synthesis is unclaimed; the elements are not.** Each piece of the accumulator architecture has a precedent somewhere in the literature, but their assembly as a business-rules-engine architecture appears not to exist as a named pattern. This is a more useful finding than "we are novel" because it gives the doc five concrete anchors into existing literature for future research.

---

## 2. The two patterns at a glance (recap)

For readers new to the architecture-analysis doc, the briefest possible recap:

- **Filter engine.** A rule is a proposition: *"given context C, does rule R fire?"* Rules are evaluated independently. Output cardinality equals input cardinality. The filter engine corresponds to the existing `RulesEngine`, `HybridRulesEngine`, and `VectorizedRulesEngine` implementations.
- **Accumulator engine.** A rule is a partial constraint that composes with other partial constraints. The engine has two phases: a context-free **Build** phase that produces the lattice of maximal consistent rule combinations for a partition, and a context-bound **Apply** phase that retrieves matching combinations. The accumulator is proposed, not built; the SQL function `pmx.sp_productpricingmatrix_discretion_combos` is its working precedent.

The two engines compose as a pipeline: **the accumulator builds; the filter ranks.**

For full mechanics, see `2026-04-07-additive-rules-architecture-analysis.md`.

---

## 3. The pattern landscape

Rules engines are not a single field. The term is used loosely across at least four communities — production rule systems, decision-table tools, logic programming, and constraint satisfaction — each with its own vocabulary, formalism, and folklore. This section organises the landscape around **patterns** rather than products. Systems are listed under each pattern as exemplars.

### 3.1 Filter-engine adjacent patterns

These patterns share machinery with the filter engine: each rule is independently evaluated against an input, results are aggregated by some collection or selection rule, and there is no inter-rule state.

#### Pattern: Stateless rule evaluation / decision tables

A decision table is a tabular encoding of independent rules: each row is a rule, columns are dimensions, cells are constraints, and an output column carries the rule's conclusion. Evaluation is row-by-row, with a *hit policy* (first-match, unique, priority, all matches, etc.) selecting the result. The pattern is the most widely deployed of all rules-engine patterns and the most directly comparable to our filter engine.

**Exemplars:**
- **DMN (Decision Model and Notation)** — the OMG standard. Formal semantics treated rigorously by Calvanese, Dumas et al. in their *Semantics and Analysis of DMN Decision Tables* paper (BPM 2016) [1] and the extended journal version [2]. The paper formalises hit policies, completeness, and consistency analysis for *single* tables but does not address combinations of tables — a gap discussed in section 4.2.
- **OPA / Rego** — Open Policy Agent, used widely for cloud authorisation policies. Rules are propositions about input documents; evaluation is independent per rule.
- **FEEL** — Friendly Enough Expression Language, the expression sub-language of DMN.
- **Excel-style lookup tables** and **GoRules** [3] — both end-user-friendly variants of decision tables.

**Relationship to our filter engine:** the filter engine *is* a decision-table evaluator with `MatchStrategy`-based dimensional matching, vectorised execution, and explicit ternary match values. The vocabulary and the evaluation model match.

#### Pattern: Production rule systems with salience-based conflict resolution

Production rules have the form *IF condition THEN action*. A working memory holds facts; an inference engine matches rule conditions against facts via the **Rete network** (an incremental matching algorithm devised by Charles Forgy in 1979); matched rules form an *agenda*; one is selected to fire via **conflict resolution**, which is universally implemented as a priority/salience ordering with tie-breaking heuristics (recency, specificity, lexicographic). The fired rule's action mutates working memory, triggering re-matching. Inference continues until the agenda is empty.

**Exemplars:**
- **Drools / JBoss BRMS / Red Hat Decision Manager** — the dominant Java production rule system [4].
- **CLIPS / Jess** — classic 1980s/90s production systems still in use.
- **Clara Rules** — Clojure production rule system, with explicit documentation of its salience-based conflict resolution [5].
- **Grule** — Go production rule engine [6].
- **OpenRules** — commercial Java decision management platform [7].

**The W3C Rule Interchange Format (RIF) Working Group** maintained a wiki listing conflict-resolution strategies across production rule engines [8]. **Every entry on that list is a variant of priority-based selection.** No mainstream production rule engine *combines* matching rules; they pick one.

**Relationship to our filter engine:** the filter engine is structurally a **degenerate production rule system** — no working memory, no inference cycle, no agenda, no salience. It evaluates rules once against a context and returns the per-rule match results. This is intentional: the use case is decision retrieval, not forward-chaining inference. Drools-class engines are over-engineered for what we need; our filter engine is the right level.

**Relationship to our accumulator engine:** the accumulator's outermost-frontier filter is a *radically different* answer to the conflict-resolution problem. Where production rule systems pick one matching rule by priority, the accumulator computes the joint action of every maximally consistent subset of matching rules. As section 4 argues, this is a **gap** in the production-rules tradition, not a research gap.

#### Pattern: Predicate trees and decision forests

Decision trees encode a rule set as a tree of predicates with leaves carrying outputs. Evaluation is a single root-to-leaf traversal, which is asymptotically faster than per-rule evaluation when the tree is balanced. Decision forests (multiple trees with majority voting) are the ML side of the same pattern.

**Exemplars:**
- **scikit-learn `DecisionTreeClassifier` / `RandomForestClassifier`** — when used as rule encoders rather than learned models.
- **Compiled DMN tables** — some DMN engines compile decision tables to tree form for faster evaluation.

**Relationship to our filter engine:** orthogonal but compatible. A future filter-engine optimisation could compile rules to a decision-tree representation for faster per-context evaluation when the rule set is large and the dimension cardinality is low. Not currently planned; mentioned for completeness.

### 3.2 Accumulator-engine adjacent patterns

These patterns share machinery with the accumulator engine: rules (or constraints) compose, the system computes some derived structure over multiple rules, and the final output reflects the joint state of many rules rather than the firing of one.

#### Pattern: CPQ configurators — the closest commercial precedent

**Configure-Price-Quote (CPQ)** systems handle product configuration for complex manufactured goods (machinery, vehicles, telecoms equipment). The core problem is identical to the accumulator engine's: given a large set of partial constraints (configuration rules, compatibility rules, pricing rules), compute the lattice of valid configurations and let a customer-facing application interactively query it. CPQ vendors take two architecturally distinct approaches:

**Configit — Virtual Tabulation (build/apply with BDDs).** Configit's *Virtual Tabulation* pre-computes the **entire valid configuration space** as a Binary Decision Diagram (BDD) — a compressed, canonical representation of a Boolean function — and ships the BDD to client applications, which query it interactively at sub-millisecond latency [9, 10, 11]. Configit's published technical paper describes this as *"BDD-based recursive and conditional modular interactive product configuration"* [12]. **This is the build/apply pattern in commercial production.** The BDD is the lattice; the build phase compiles it; the apply phase queries it. Different DNA from our prime products, same architectural shape.

**Tacton — constraint-based configuration with online CSP solving.** Tacton, by contrast, models configurations as a Constraint Satisfaction Problem and runs a CSP solver online as the customer makes selections [13, 14]. No precomputed lattice. Tacton's 1999 paper *The Tacton View of Configuration Tasks and Engines* [15] is the foundational document. Tacton argues constraint-based is more flexible than rule-based for very large configuration spaces; Configit argues precomputation is faster at query time. **The two approaches map directly onto a fundamental representation tradeoff that the accumulator engine's tier-selection ladder will eventually face: precompute once and cache, or solve online per query?**

**Cincom CPQ** [16] and other CPQ vendors largely follow one or the other paradigm.

**Why CPQ is the strongest commercial precedent:** Configit's Virtual Tabulation and the accumulator's Build/Apply split are *the same architectural pattern* applied to different domains (product configuration vs pricing discretion). Both build a lattice once per partition (product model / pricing context-key tuple), both query the lattice per user interaction, both face the same overflow / representation choices, both handle three-valued logic (constraint / forbidden / unspecified). **A deep technical study of how Configit's BDD compares to a prime-product lattice would be the single most valuable piece of follow-up research from this document.** Their published BDD paper [12] is the natural starting point.

#### Pattern: Datalog and recursive deductive databases

**Datalog** is a declarative logic programming language whose programs are sets of Horn clauses with bottom-up fixpoint evaluation. The fixpoint computation derives new facts from existing facts until nothing new can be derived. Recursive Common Table Expressions in SQL are a syntactic and semantic restriction of Datalog.

**Exemplars:**
- **Soufflé** — a high-performance Datalog engine used heavily in static-analysis and program verification.
- **RDFox** — semantic-web Datalog reasoner from Oxford.
- **LogicBlox** — commercial Datalog database (acquired by Infor).
- **Recursive CTEs in SQL** — Postgres, SQL Server, Snowflake, Databricks SQL all support them. Databricks recently added recursive CTEs explicitly to make Databricks SQL Turing-complete [17, 18]. Use cases include hierarchies, graphs, and ad-hoc rule combination [19, 20].

**Relationship to the accumulator engine:** the SQL precedent (`sp_productpricingmatrix_discretion_combos`) is **structurally a Datalog program**. The recursive CTE is the fixpoint computation; the lattice is the derived-fact set; the join predicate is the rule body; the coalesce machinery is the variable unification. **The accumulator engine is, theoretically, a Datalog program with a custom subset-dominance filter on top.** Re-implementing the accumulator in Soufflé would be a useful sanity check on the gap-list operation set: any operation that doesn't translate cleanly into Datalog is something Soufflé would have already had to solve a different way, and worth understanding. There is also a recent Medium series exploring *"Postgres as a Rule Engine"* via Datalog-like extensions [21] that explicitly frames recursive CTEs as a rules-engine pattern.

#### Pattern: Constraint satisfaction / SAT / SMT

CSPs (Constraint Satisfaction Problems), SAT solvers (Boolean satisfiability), and SMT solvers (Satisfiability Modulo Theories) all share the core idea of finding variable assignments that satisfy a conjunction of constraints. They differ in expressiveness and algorithmic approach.

**Exemplars:**
- **MiniZinc / Choco / OR-Tools** — CSP solvers.
- **MiniSAT / Glucose / CaDiCaL** — SAT solvers.
- **Z3 / CVC5** — SMT solvers.

**Relationship to the accumulator engine:** each rule in the accumulator can be viewed as a conjunction of dimensional constraints, and the recursive coalesce step is a constraint conjunction. But the goal differs. CSPs/SMT solvers ask *"is there a satisfying assignment?"* and return one. The accumulator asks *"what are all the maximal consistent rule combinations?"* and returns the lattice of them. The CSP world has tools for the second question — **all-solutions enumeration** — but they are typically slower and less explored than satisfiability. Tacton's CPQ approach (above) is essentially online CSP; Configit's is the precomputed-lattice alternative.

#### Pattern: Dominance-Based Rough Set Approach (DRSA) — the closest academic precedent

The single most relevant academic literature for the accumulator engine is the **Dominance-based Rough Set Approach (DRSA)** developed primarily by Salvatore Greco, Benedetto Matarazzo, and Roman Słowiński since the late 1990s. DRSA generalises classical rough set theory by introducing a *dominance principle*: a decision rule is consistent with the dominance principle if, whenever object A dominates object B on all condition criteria, A's decision is at least as good as B's.

**Key papers:**
- **Greco, Matarazzo, Słowiński, Stefanowski (2007)** — *An algorithm for induction of decision rules consistent with the dominance principle* [22]. This is the foundational paper for **dominance-based rule induction** under multi-criteria decision support. The mathematical framework is directly applicable to the accumulator's outermost-frontier filter.
- **Susmaga (2003)** — *Generation of Exhaustive Set of Rules within Dominance-based Rough Set Approach* [23]. Algorithmic treatment of generating the complete rule set under DRSA.

**Relationship to the accumulator engine:** DRSA generates *decision rules* under dominance ordering; we *combine* decision rules under subset dominance. Different objects of dominance — DRSA dominates over criterion values, we dominate over rule-membership sets — but the same formal vocabulary (dominance principle, Pareto-style filtering, consistent rule generation). **A formal mapping from DRSA's framework to our outermost-frontier filter would significantly strengthen the accumulator's mathematical foundation** and is one of the highest-value follow-up research items from this document.

#### Pattern: Maximal Consistent Subsets in default unification

A separate academic lineage uses the exact phrase *"maximal consistent subsets"* to describe the same object the accumulator computes. **Robert Malouf's work on default unification** [24] in computational linguistics is the most direct hit:

> "Default unification operations combine strict information with information from one or more defeasible feature structures. Many such operations require finding the maximal subsets of a set of atomic constraints that are consistent."

Default unification appears in HPSG (Head-driven Phrase Structure Grammar) and other constraint-based grammar formalisms. The objects being unified are linguistic feature structures, not pricing rules, but the formal problem is identical: given a set of partial constraints, find the maximal subsets that are mutually consistent.

**Relationship to the accumulator engine:** **this is the closest formal-vocabulary precedent we found.** The terminology *"maximal consistent subsets"* is the exact name for what the accumulator's outermost-frontier filter computes within each fingerprint namespace. Adopting this vocabulary in the accumulator's documentation would connect the engine to a well-established formal tradition and make the algorithm searchable.

#### Pattern: Skyline queries / Pareto frontiers in SQL

The outermost-frontier filter — the prime-divisibility step that drops dominated rule combinations — is a special case of a **skyline query**, a well-established database operation that returns the Pareto-frontier of a relation under a multi-criteria preference ordering. Skyline queries have been studied since 2001 (Börzsönyi, Kossmann, Stocker) and are implemented in multiple database systems.

**Exemplars and references:**
- **Exasol Skyline SQL extension** — the only major commercial database with native skyline syntax [25].
- **rPref** — R package for computing Pareto frontiers and database preferences [26, 27].
- **Skyline queries integrated into Spark SQL** — Grasmann, Pichler, Selzer (TU Wien, 2022) [28].
- **Snowflake skyline-via-SQL recipes** [29].
- **Ciaccia, *Skyline queries, front and back*** — academic survey [30].

**Relationship to the accumulator engine:** the prime-product divisibility test is **one specific implementation** of the skyline pattern, where the dominance relation is *prime-factor inclusion* (equivalent to set/multiset inclusion). The same filter could be implemented with bitset AND operations, sorted-tuple comparison, or a generic skyline-query operator if the host database supports one. **Naming the outermost-frontier filter as a skyline query under subset-inclusion dominance** is more honest than describing it as "the prime trick" and immediately makes it portable across DNA representations. The principle `c.identity-and-representation/outermost-frontier-as-pareto.md` in the principles directory already captures this; this section provides the citations.

#### Pattern: Tariff / rate engines and discount cascades

A grab-bag pattern from telecoms, insurance, utilities, and SaaS billing: combine multiple discount or tariff rules into a single applicable price for a customer. The literature is mostly industry whitepapers, vendor blog posts, and Stack Overflow answers; there is no canonical academic treatment. The discretion-margin SQL is itself in this family.

**Exemplars and references:**
- **Higson** — *How a Rules Engine Empowers Pricing Engines in Insurance* [31].
- **Redian Software** — *Insurance Pricing & Rating Engine 2026: Critical Tech Guide* [32].
- **Flyaps** — *Optimizing Telecom Operations: Custom Rating Engines for Roaming Wholesale, Telecom Consulting, and IoT SIM Tariffication* [33].
- **NetSuite** — *A Guide to Pricing Strategies in the Telecom Industry* [34].
- **Fractal Analytics** — *Underwriting logic reimagined: Conditional, explainable rule engines for modern insurance* [35].
- **GoRules Dynamic Tariff Engine template** [3] — a vendor-supplied rule template explicitly for telecom tariff composition.
- **Stack Overflow** — *How can I calculate a cascade sales discount scenario using TSQL?* [19] — a representative example of ad-hoc SQL solutions to the same family of problems the discretion-margin SQL solves.

**Relationship to the accumulator engine:** this is the **practical application domain** the accumulator was born in. The literature is sparse on architecture and rich on horror stories. A doc that named the architecture properly — *"a Build/Apply rules engine that computes maximal consistent rule combinations under dominance filtering, applicable to tariff composition"* — could be useful to this community on its own merits, not just to Mountain Ash.

### 3.3 Adjacent but different (brief contrasts)

For completeness, two patterns commonly bundled with rules engines that share *almost no machinery* with our two engines and should be mentioned only to clarify boundaries.

**Complex Event Processing (CEP) / Event-Condition-Action (ECA).** Stream-oriented engines (**Esper**, **Apache Flink CEP**) where rules detect *temporal patterns* in event streams — e.g. "fire if A is followed by B within 5 seconds and not preceded by C". These are temporal rather than constraint-driven, and their machinery (windowing, state management, watermarks) shares nothing with our two engines beyond the word "rule". CEP is mentioned here so future readers don't conflate it with our work. Note, however, that section 5's first roadmap candidate proposes a *temporal extension* to our engines that could draw on CEP machinery.

**Workflow / business process engines with embedded decision tables.** **Camunda**, **Activiti**, and similar BPM engines embed decision tables (often DMN-compliant) inside flow control. The rules-engine portion is a decision-table evaluator (covered above); the surrounding machinery is workflow orchestration. Mentioned only because the BRE field's marketing material often groups them together.

---

## 4. Where our two engines fit

### 4.1 The filter engine: a degenerate production rule system, well-precedented

The filter engine maps cleanly onto two existing patterns: **decision tables** (it is a decision-table evaluator with rich `MatchStrategy` semantics) and **production rule systems with all-matches hit policy** (it is structurally a Drools-class engine with the inference cycle and salience machinery removed). The choice to remove inference is intentional and well-aligned with how DMN is used in practice — most DMN tables are queried, not chained — and is a feature, not a deficit.

**The filter engine is not architecturally novel** and does not need to be. Its value lies in (a) the dimensional metadata layer, (b) the per-strategy match implementations, (c) the vectorised polars/ibis backends, and (d) the Mountain Ash-specific data abstractions. None of those are claims of novelty against the field; they are claims of fit for a specific organisational context.

### 4.2 The accumulator engine: a synthesis of known elements, unclaimed as a BRE architecture

The accumulator engine is a different story. **Each individual element of its architecture has a precedent in the literature**, but their assembly into a single business-rules-engine pattern appears not to exist as a named thing.

| Element | Precedent | Citation |
|---|---|---|
| Recursive constraint composition (coalesce + three-valued match) | Datalog, recursive CTEs, default unification | [17–21], [24] |
| Build/Apply phase split with precomputed solution space | Configit Virtual Tabulation (BDDs) | [9–12] |
| Maximal consistent subsets as the primary output | Default unification | [24] |
| Dominance-based filtering of derived rules | DRSA — Greco, Słowiński et al. | [22, 23] |
| Pareto frontier as the formal name for the outermost filter | Skyline queries in SQL | [25–30] |
| Per-rule prime identity for subset testing | Number-theory subset-enumeration tricks (not in BRE context) | [36–39] |
| Tariff/discount composition as the application domain | Insurance & telecom pricing literature | [31–35] |

**What appears unclaimed:** the assembly of these elements as a *business rules engine architecture*. Mainstream BREs — Drools, Clara, Jess, CLIPS, OpenRules, OPA, DMN tools — do not compose rules into rulesets. They handle rule conflict by salience or first-match. The W3C RIF Working Group's enumeration of conflict-resolution strategies across production rule engines [8] confirms this universally. Composition is not on the BRE field's radar.

**The closest commercial precedent — Configit Virtual Tabulation** — explicitly does build/apply with a precomputed lattice, but it solves *product configuration*, not *rule combination for pricing decisions*. The application domain is different and the publishable framing is different. A practitioner searching for "rules engine that combines rules" would not currently find Configit.

**The closest academic precedent — DRSA** — explicitly does dominance-based decision rule generation, but it operates on the *induction* of rules from data, not on the *combination* of pre-existing rules at runtime. A practitioner searching for "dominance-based rules engine" would not currently find a runtime engine architecture.

The honest claim, then, is:

> The accumulator engine is a **novel synthesis of well-precedented elements**, occupying a gap in the business-rules-engine field. The pattern is not new mathematically — DRSA, Configit, default unification, and skyline queries each cover one face of it — but the assembly of these into a runtime BRE architecture, with a clear Build/Apply split, multi-valued dimensional coalesce, and an explicit pipeline composition with a downstream filter engine, does not appear to have a name in the literature.

This is more useful than a flat novelty claim because it gives the doc five concrete anchors for further research: study DRSA's mathematical framework, study Configit's BDD compression and query semantics, study Malouf's default unification for vocabulary, study skyline-query implementations in databases for filter algorithms, and study the tariff-engine industry for application-domain validation.

### 4.3 The pipeline (filter + accumulator composition) is genuinely unusual

The architecture-analysis doc's key observation — *"the accumulator builds, the filter ranks"* — describes a **pipeline composition** of two engines that does not appear in any system surveyed here. CPQ tools have a query layer over the precomputed configuration space, but the query layer is hand-coded UI logic, not a second rules engine. DRSA's rule induction produces rules that are then evaluated by some downstream system, but the literature treats induction and evaluation as separate concerns, not as a pipeline. Drools-class engines are monolithic.

**The composition itself — using a second rules engine to rank/select among the first engine's outputs — is the most genuinely unusual architectural claim in the mountainash-utils-rules design.** It is also the easiest to lose sight of, because each engine in isolation looks unremarkable. Section 5 treats it as the foundation for the first roadmap candidate.

---

## 5. Roadmap: three candidate future engines

Each candidate is anchored in a gap surfaced by the survey (section 3) and validated against a real Mountain Ash use case. All three are EXPLORATORY; no commitments are implied. They are presented in rough order of architectural alignment with the existing two engines, not in priority order.

### Candidate 1 — Temporal Rules Engine

**The gap.** Both our engines are time-blind. A rule says what is true for some context, not *when* it is true or *how it changed*. CEP/ECA systems handle temporal patterns but in a stream-oriented way that doesn't fit batch + interactive query. DMN, OPA, DRSA, and CPQ tools all assume the rule set is a snapshot. There is no widely-used pattern for *"a rule that applies during March, with a higher discretionary margin tier from the 15th onwards, but only for customers who have been with us > 90 days"*, and no engine that lets the same rule registry be queried at multiple effective dates.

**The architecture.** Add a temporal dimension role alongside `CONTEXT_KEY` and `CONSTRAINT` (per the principle `natural-keys-vs-dimensions.md`): **`TEMPORAL`**. A `TEMPORAL` dimension carries a validity interval (or recurrence pattern), and the engine's matching machinery accepts an `as_of` parameter that filters rules to those valid at that point. The accumulator engine's Build phase can then materialise lattices per (partition, time-bucket), with a smart re-Build trigger when the temporal state of any contributing rule changes.

**The Mountain Ash use case.** Pricing discretion changes over time. Promotional rates expire. Customer aging buckets shift. The current SQL has none of this; the team works around it by re-materialising the entire ruleset with hard-coded effective dates. A temporal engine would replace the workaround with a first-class capability. Banker training, audit compliance, and "what would this customer have been priced last quarter" queries all become possible.

**Pre-existing machinery to draw on.** CEP literature for temporal-pattern semantics (windowing, allen-interval algebra). Bitemporal database literature for the as-of query model. The accumulator engine's Build/Apply split makes temporal caching more tractable than it would be in a pure filter engine — the lattice is already partition-keyed, adding a time dimension to the partition is conceptually small.

### Candidate 2 — Inverse Rules Engine

**The gap.** Both our engines are *evaluative* — given a context, what rules apply? Nobody appears to have a runtime engine that answers the **inverse**: given a desired outcome, what contexts would produce it? CSP/SMT solvers do this in principle but require the rule set to be re-encoded as constraints and the question to be re-encoded as a satisfiability query — a heavyweight, one-off translation that bears no resemblance to runtime use. DRSA does dominance-based rule induction from data, which is also unrelated. The closest commercial analogue is product-search-by-feature in CPQ tools, but those are typically faceted-search UIs over the precomputed configuration space, not full inverse evaluation.

**The architecture.** Use the accumulator's lattice as a **precomputed query target for inverse queries**. Given a desired margin, walk the outermost rulesets in margin order and return the fingerprints of those that produce a margin within the target band. Each fingerprint is a *characterisation of the context class* that would receive that margin: customers in segment X with LVR in band Y on product Z. For the filter engine, build a parallel inverse index by inverting the per-dimension match strategies (a `RANGE` dimension's inverse is the union of the rule's intervals; an `EXACT` dimension's inverse is the value set; a `REGEX` is harder and may need approximation).

**The Mountain Ash use case.** "Show me the customer profile that would qualify for our most aggressive discount." "If a banker offers margin X, what customer attributes must hold?" "Find the segment of contexts where our pricing differs from the competitor by more than 0.50%." All are inverse queries the current system cannot answer without ad-hoc analytics work. A first-class inverse engine would turn the rules registry into a *queryable model of the bank's pricing surface*, useful for sales tools, product design, and competitive analysis. The accumulator engine's lattice makes this dramatically cheaper than it would be with a filter-engine-only architecture — the lattice is already a finite, indexed structure ready to be queried backwards.

**Pre-existing machinery to draw on.** Configit's Virtual Tabulation querying — their BDD supports both forward configuration ("does this work?") and reverse queries ("what works?"), and is the most directly applicable precedent. SAT-solver model enumeration (`#SAT`, model counting). Inverse-index techniques from search engines.

### Candidate 3 — Probabilistic / Learned Rules Hybrid

**The gap.** Both our engines treat rules as crisp constraints. There is no place for *"this customer is 78% likely to be a price-sensitive segment"* or *"this rule was learned from historical data with 0.85 confidence"*. The BRE field and the ML field have largely solved this with hybrid systems — Drools with Bayesian extensions, decision trees that compile to rules, OPA with policy-as-data — but the integration is consistently bolted-on rather than native. DRSA's rough-set foundation has a probabilistic variant (the *Variable Consistency DRSA*) which is the closest formal precedent.

**The architecture.** Extend the per-dimension ternary encoding (`-1/0/1`) to a continuous match value in `[-1, +1]`, where `0` remains "unknown" but values in `(0, 1)` represent partial/probabilistic match strength. The filter engine's vectorised aggregation already uses arithmetic combination of ternary values — extending to continuous values is mostly an arithmetic change, not an architectural one. The accumulator engine's coalesce machinery would need new semantics for combining continuous-match dimensions (probably product of confidences for AND, with a threshold for the compatibility check). Learned rules — e.g. decision trees compiled from historical data — could be ingested into the same rule registry and combined with hand-authored rules in the same lattice.

**The Mountain Ash use case.** Risk-based pricing. Hand-authored discretion rules currently define the explicit pricing matrix; ML-derived models predict customer churn, default risk, propensity to accept. Today these live in completely separate systems and are joined at the application layer. A hybrid engine would let them live in one rule registry, combine in one lattice, and produce one pricing decision with the contributions of each rule traceable through the prime-product DNA.

**Pre-existing machinery to draw on.** Variable Consistency DRSA (probabilistic rough sets). Markov Logic Networks and other probabilistic logic frameworks. The PSL (Probabilistic Soft Logic) literature. The filter engine's existing ternary encoding is already arithmetic-friendly and almost trivially extends to continuous values.

---

## 6. Open research threads

Items that would significantly strengthen mountainash-utils-rules' theoretical foundations or unlock high-value engine extensions, presented as research questions rather than commitments.

1. **Formal mapping from DRSA to the accumulator's outermost-frontier filter.** The Greco/Słowiński framework for dominance-based rule generation is the closest mathematical precedent. A formal mapping would let the accumulator inherit DRSA's proofs of completeness, soundness, and minimality, and would connect Mountain Ash to a productive academic community. **Highest-value research thread.**

2. **Comparative study of Configit's BDD lattice vs the accumulator's prime-product lattice.** Both implement the same architectural pattern (Build/Apply with a precomputed solution space) using different DNA representations. A side-by-side technical study — query latency, memory footprint, update cost, multiset support — would inform the accumulator's representation choices and probably surface ideas neither team has considered alone. Configit's published BDD paper [12] is the natural starting point.

3. **Adopting "maximal consistent subsets" as the formal vocabulary in the accumulator's documentation.** Malouf's default-unification work [24] uses the exact phrase for the exact object. Adopting the vocabulary connects Mountain Ash to a formal tradition with searchable literature and 30 years of intellectual history. Low-cost change with disproportionate documentation value.

4. **Skyline-query implementation alternatives for the outermost-frontier filter.** The prime-divisibility test is one of several possible implementations of the same Pareto-frontier operation. A benchmark across prime-divisibility, bitset-AND (set-only), generic skyline-query operators (where the host database supports them), and sorted-tuple comparison would clarify the representation tradeoffs and produce concrete numbers for the principle `representation-fits-host-language.md`.

5. **Pipeline composition of rules engines as a distinct architectural pattern.** The "accumulator builds, filter ranks" pattern does not appear in any surveyed system. Whether it has a name elsewhere — perhaps in workflow engines, in decision-cascade ML systems, or in hybrid symbolic/connectionist AI literature — is worth one more focused literature search. If genuinely unclaimed, it deserves a short paper of its own.

6. **The tariff/rate-engine industry as an audience for the accumulator pattern.** The literature in this space is sparse on architecture and rich on horror stories. A blog post or whitepaper aimed at insurance and telecom architects, framing the accumulator engine in their vocabulary, could be valuable both as marketing and as field validation of the pattern's generality.

---

## 7. References

Inline citation numbers refer to this section. URLs were verified during the exa search performed on 2026-04-07; future link rot is possible.

### Decision tables and DMN

[1] Calvanese, D., Dumas, M., Laurson, Ü., Maggi, F. M., Montali, M., & Teinemaa, I. (2016). *Semantics and Analysis of DMN Decision Tables.* In: Business Process Management (BPM 2016), Springer LNCS, pp. 217–233. arXiv: <https://arxiv.org/abs/1603.07466>. Springer: <https://link.springer.com/chapter/10.1007/978-3-319-45348-4_13>. PDF: <https://kodu.ut.ee/~dumas/pubs/bpm2016DMN.pdf>.

[2] Calvanese, D., Dumas, M., Laurson, Ü., Maggi, F. M., Montali, M., & Teinemaa, I. *Semantics, Analysis and Simplification of DMN Decision Tables.* Information Systems. PDF: <https://kodu.ut.ee/~dumas/pubs/DMNSimplify.pdf>. ScienceDirect: <https://www.sciencedirect.com/science/article/abs/pii/S0306437916306330>.

[3] GoRules — *Dynamic Tariff Engine Template* and decision-rules platform: <https://gorules.io/industries/telco/templates/dynamic-tarrif-engine>. *Top 10 Business Rule Engines for 2026:* <https://www.decisionrules.io/en/articles/top-10-business-rule-engines/>.

### Production rule systems and conflict resolution

[4] Red Hat Decision Manager — *JBoss Rules 5 Reference Guide, Default Conflict Resolution Strategies*: <https://docs.redhat.com/ja/documentation/red_hat_decision_manager/5/html/jboss_rules_5_reference_guide/default_conflict_resolution_strategies>.

[5] Clara Rules — *Conflict Resolution and Salience*: <http://www.clara-rules.org/docs/conflictsalience/>.

[6] Hyperjump Grule — issue #89, *How to execute all the selected rules' actions instead of highest salience*: <https://github.com/hyperjumptech/grule-rule-engine/issues/89>. Confirms salience is the universal default.

[7] OpenRules — Rule Engine documentation: <http://openrules.com/ruleengine.htm>.

[8] W3C RIF Working Group Wiki — *Conflict Resolution Strategies*: <https://www.w3.org/2005/rules/wg/wiki/Conflict_Resolution_Strategies>. Comprehensive enumeration of strategies across production rule engines, all of which are variants of priority/salience.

### CPQ configurators

[9] Configit — *Virtual Tabulation Configuration Technology*: <https://configit.com/virtual-tabulation/>.

[10] Configit — *Valid Configurations in seconds with Virtual Tabulation*: <https://configit.com/learn/blog/valid-configurations-virtual-tabulation>.

[11] Configit — *Product Configuration Explained: A Guide to Virtual Tabulation, Rules, and Constraints* (Daniel Joseph Barry, 2025): <https://configit.com/learn/blog/product-configuration-explained>.

[12] Configit — *BDD-based Recursive and Conditional Modular Interactive Product Configuration*: <https://configit.com/learn/resources/bdd-based-recursive-and-conditional-modular-interactive-product-configuration/>. **Foundational technical paper for the BDD-based build/apply pattern.**

[13] Tacton — *Constraint-Based vs. Rules-Based Configuration: The Advantage for Complex Manufacturing*: <https://www.tacton.com/cpq-blog/constraint-based-vs-rules-based-configuration/>.

[14] Tacton — *What kind of configuration does Tacton use—rule-based or constraint-based?*: <https://www.tacton.com/faq/what-kind-of-configuration-does-tacton-use-rule-based-or-constraint-based/>.

[15] Orsvärn, K., & Axling, T. (1999). *The Tacton View of Configuration Tasks and Engines.* AAAI 1999 Workshop. PDF: <https://cdn.aaai.org/Workshops/1999/WS-99-05/WS99-05-025.pdf>.

[16] Cincom — *How CPQ Product Configurators Simplify Complex Customizations*: <https://www.cincom.com/blog/cpq/how-cpq-product-configurators-simplify-complex-customizations/>.

### Datalog and recursive CTEs

[17] Databricks — *Introducing Recursive Common Table Expressions: Making Databricks SQL Turing Complete* (2025): <https://www.databricks.com/blog/introducing-recursive-common-table-expressions-databricks>.

[18] Databricks SQL SME — *Driving Business Insights with Recursive CTEs in DBSQL*: <https://medium.com/dbsql-sme-engineering/driving-business-insights-with-recursive-ctes-in-dbsql-00ad222fa0be>.

[19] Stack Overflow — *How can I calculate a cascade sales discount scenario using TSQL?*: <https://stackoverflow.com/questions/20619405/how-can-i-calculate-a-cascade-sales-discount-scenario-using-tsql>.

[20] *Recursive Join in SQL: Practical Patterns for Hierarchies, Graphs, and Safe Recursion* — TheLinuxCode: <https://thelinuxcode.com/recursive-join-in-sql-practical-patterns-for-hierarchies-graphs-and-safe-recursion/>.

[21] *Omnigres (Extended Postgres Datalog) — Postgres as a Rule Engine* (CMCC Deepdive, 2025): <https://medium.com/cmcc-deepdive/3-omnigres-extended-postgres-datalog-postgres-as-a-rule-engine-0bf2c41db3fc>.

### Dominance-Based Rough Set Approach (DRSA)

[22] Greco, S., Matarazzo, B., Słowiński, R., & Stefanowski, J. *An algorithm for induction of decision rules consistent with the dominance principle.* PDF: <https://www.cs.put.poznan.pl/jstefanowski/pub/dominance.pdf>.

[23] Susmaga, R. (2003). *Generation of Exhaustive Set of Rules within Dominance-based Rough Set Approach.* Academia.edu: <https://www.academia.edu/92905175/Generation_of_Exhaustive_Set_of_Rules_within_Dominance_based_Rough_Set_Approach>.

### Maximal consistent subsets

[24] Malouf, R. *Maximal Consistent Subsets.* San Diego State University. Computational Linguistics. (Default unification in feature-structure grammars.) Search the *Computational Linguistics* journal for the title; the paper is reachable via Silverchair's watermark service from the Computational Linguistics archive.

### Skyline queries and Pareto frontiers in SQL

[25] Exasol — *Skyline SQL extension*: <https://docs.exasol.com/db/7.1/advanced_analytics/skyline.htm>.

[26] Roocks, P. (2016). *Computing Pareto Frontiers and Database Preferences with the rPref Package.* The R Journal: <https://journal.r-project.org/articles/RJ-2016-054/>. PDF: <https://journal.r-project.org/articles/RJ-2016-054/RJ-2016-054.pdf>.

[27] *Computing Pareto Frontiers and Database Preferences with the rPref Package* (article landing): <https://journal.r-project.org/articles/RJ-2016-054/>.

[28] Grasmann, L., Pichler, R., & Selzer, A. (2022). *Integration of Skyline Queries into Spark SQL.* TU Wien. arXiv: <https://export.arxiv.org/pdf/2210.03718v1.pdf>.

[29] *Skyline or Pareto Front using SQL* — Query Optimization in Snowflake: <https://qosf.com/skyline-or-pareto-frontier-using-SQL.html>.

[30] Ciaccia, P. *Skyline queries, front and back.* Academia.edu: <https://www.academia.edu/63853690/Skyline_queries_front_and_back>.

### Tariff / pricing rules engines (industry)

[31] Higson — *How a Rules Engine Empowers Pricing Engines in Insurance*: <https://www.higson.io/blog/how-a-rules-engine-empowers-pricing-engines-in-insurance>.

[32] Redian Software — *Insurance Pricing & Rating Engine 2026: Critical Tech Guide*: <https://www.rediansoftware.com/insurance-pricing-rating-engine-2026-critical-technology/>.

[33] Flyaps — *Optimizing Telecom Operations: Custom Rating Engines for Roaming Wholesale, Telecom Consulting, and IoT SIM Tariffication*: <https://flyaps.com/blog/building-a-custom-rating-engine/>.

[34] NetSuite — *A Guide to Pricing Strategies in the Telecom Industry*: <https://www.netsuite.com/portal/resource/articles/accounting/telecom-pricing.shtml>.

[35] Fractal Analytics — *Underwriting logic reimagined: Conditional, explainable rule engines for modern insurance*: <https://fractal.ai/article/underwriting-logic-reimagined>.

### Prime-product subset enumeration (number theory)

[36] CSTheory Stack Exchange — *Enumeration given a product of primes*: <https://cstheory.stackexchange.com/questions/1597/enumeration-given-a-product-of-primes>.

[37] GeeksforGeeks — *Counting Subsets with prime product property*: <https://www.geeksforgeeks.org/dsa/counting-subsets-with-prime-product-property/>.

[38] Stack Overflow — *How to calculate the number of coprime subsets of the set {1,2,3,...,n}*: <https://stackoverflow.com/questions/18790284/how-to-calculate-the-number-of-coprime-subsets-of-the-set-1-2-3-n>.

[39] Mathematics Stack Exchange — *Storing a natural number as a set of its Nth prime factors, how much data is used?*: <https://math.stackexchange.com/questions/1343273/storing-a-natural-number-as-a-set-of-its-nth-prime-factors-how-much-data-is-use>.

### Other

[40] Materialize — *Rules execution engine pattern*: <https://materialize.com/docs/transform-data/patterns/rules-engine>.

[41] *Business as Rulesual: A Benchmark and Framework for Business Rule Flow Modeling with LLMs* (2025–2026): <https://arxiv.org/abs/2505.18542>. Recent arXiv paper exploring LLMs for business rule modelling — a different direction than this doc considers but relevant context.

[42] Casanova, M. A. (2004). *Algorithms for analysing related constraint business rules.* ScienceDirect: <https://www.sciencedirect.com/science/article/abs/pii/S0169023X04000163>.

---

## 8. Closing note

This document deliberately stops at survey and roadmap. It is intended to seed research, not to commit Mountain Ash to any particular extension. The two strongest research threads, in this author's judgement, are:

1. **A deep technical comparison with Configit's BDD-based Virtual Tabulation** — the closest commercial precedent for the accumulator engine, sharing the build/apply architecture with a different DNA representation.
2. **A formal mapping from DRSA's dominance-based rule induction framework to the accumulator's outermost-frontier filter** — the closest academic precedent, with productive mathematical machinery the accumulator could inherit.

Both should be pursued before committing to an accumulator-engine implementation, because either could surface design constraints (or enable optimisations) that the architecture-analysis doc has not yet considered.

The roadmap candidates in section 5 are independent of the accumulator commitment: a temporal extension to the existing filter engine, an inverse-query engine, or a probabilistic/learned-rules hybrid could each be explored without first building the accumulator. They are presented in section 5 because they each connect to a gap that the survey made visible, and because the principles directory and the architecture-analysis doc together give Mountain Ash an unusually strong foundation for any of them.
