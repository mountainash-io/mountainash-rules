# mountainash-rules Editorial Brief

**Status: CONFIRMED; approved architecture implemented as a complete, verified candidate.** The user approved the single-manual scope, thorough usage and internals, both appendices, and the eleven-chapter, four-part plan. Chapters 1, 2 and 3 are user-approved; Chapter 6 remains the approved worked-example reference. The other seven chapters were completed under autonomous authorization, with five parallel authors, Main's continuous editorial review and two independent technical reviews. All chapters and appendices are integrated. Commit, publication and promotion of a completed source-refresh baseline remain separate user gates.

## Start here for future editorial work

Use `docs-site/chapter-plan.md` as the writing workflow, including its chapter
boundaries, primary teaching order and Chapter 1 boundary. Read
`docs-site/learning-graph/learning-graph.json` for canonical concept assignments
and prerequisites, then `docs-site/reconciliation-crosswalk.json` for the matching
sections in both source manuscripts and their reconciliation requirements.
Read those full sections before drafting; the crosswalk excerpts are not a
replacement for them.

The approved voice references are Chapters 1 and 2. The approved worked-example
depth reference is `docs-site/site/docs/chapters/06-batch-evaluation/index.md`,
originally Chapter 4. Chapter 1 teaches the four assigned foundations. Retain that
boundary: a complete engine walkthrough does not replace a foundations chapter.

### Input locations

Paths below are relative to the reconciliation worktree root:

| Input | Location |
|---|---|
| Maintained manuscript and current profile | `../mountainash-rules-adaptive-refresh-worktree/docs-site/` |
| Selected current Rules source | `../mountainash-rules-adaptive-refresh-worktree/src/mountainash_rules/` |
| Original donor manuscript | `../mountainash-rules-book-worktree/docs-site/site/docs/chapters/` |
| Approved writing reference | `docs-site/site/docs/chapters/06-batch-evaluation/index.md` |
| Architecture and per-chapter workflow | `docs-site/chapter-plan.md` |
| Canonical concept graph | `docs-site/learning-graph/learning-graph.json` |
| Reconciliation evidence | `docs-site/reconciliation-crosswalk.json` |
| Output manuscript root | `docs-site/site/docs/chapters/` |

Resolve graph `source_path` values against the maintained source worktree when
checking current behavior, not against this candidate's older package checkout.
Read the full manuscript sections named by each crosswalk record. Existing
donor page directories do not override the final destinations in the plan.

## Source basis

- Reconciliation worktree: `mountainash-rules-book-reconciliation-worktree`, branch
  `docs/book-reconciliation-sample`. The approved batch sample is the editorial reference.
- Technical source: maintained worktree `mountainash-rules-adaptive-refresh-worktree`,
  source revision `730a8583ee9d4fd6b52dc5350699eb66cc7487e9`. Execute examples against
  that source, not this candidate's older package checkout.
- Maintained manuscript: `bccfc1f2d1d2b1486661c75b7ba49c158f5a18bf`, published through
  develop merge `07a7a8a2104b453871aa56cfd814c7f2376d07f7`.
- Donor manuscript: `a862e3a53042058649aabb48b3ad31380d0426a0`, with source basis
  `94659bb0c096485c87d329f09e944577427f129f`. Use its examples and diagrams selectively.
- Current profile evidence lives in the maintained worktree's `docs-site/profile/`.
  The candidate's canonical graph has the approved eleven-chapter mapping, but its
  source enrichment and CIS remain historical. The profile and refresh-state are
  unchanged. Graph mapping readiness does not mean manuscript or source-refresh
  completion.
- Package: `mountainash-rules` — a vectorized, backend-agnostic business rules engine with
  `ExpressionRulesEngine` for filtering, ranking and hit-policy selection, and `AccumulatorEngine`
  for compatible rule combinations; 21 profiled modules.

## Candidate audiences (from the package profile)

The profile (`docs-site/profile/facets/`) carries five audience facets. All five inform the
confirmed manual; their chapter/section allocation remains a planning decision:

| Facet | What it covers | Featured modules (count) |
|---|---|---|
| `users` | Defining dimensions, single-context/batch evaluation, hit policies, explainability, accumulator build/apply/persist/partition | 10 |
| `maintainers` | Ternary/sentinel invariants, both engines' internal pipelines, partition routing, persistence, backend-purity enforcement, test layout | 12 |
| `contributors` | Recipes for adding a match strategy, accumulator strategy, aggregate op, or hit policy; backend-purity and set-wildcard conventions to reuse; test patterns | 9 |
| `backend-architecture` | The shared dimension model and `mountainash.relations`/`expressions` protocol underlying both engines, prime-encoded lattice, ternary partition routing, backend-purity exceptions | 9 |
| `broader-hype` | Outward-facing capability narrative (backend portability, explainability, batch scale, combinatorial solving, persistence, governed ranking) — no internal implementation detail | 7 |

Per the implementation plan, the reading progression is **understand concepts → use the package →
understand/modify internals**, and every facet is a candidate input to that progression rather than a
guaranteed one-audience-one-chapter mapping. `broader-hype` is marketing/positioning framing, not a
technical audience with its own manual section; it is a source of headline framing for the introduction,
not a chapter. `contributors` and `maintainers` overlap heavily (both describe engine internals from a
"can change this" angle) and are candidates for a single combined internals treatment rather than two
parallel sections; exact boundaries will be proposed in the chapter plan.

## Reusable existing material (inventory, not a commitment)

The maintained book and original donor preview remain read-only references. This reconciliation worktree now contains the complete candidate. Earlier chapter addresses serve reference landing pages, not competing manuscripts; six replaced reference bodies are archived outside the published source tree.

Within that accepted book and the surrounding repository, the following prose/examples are candidate reuse
sources, selectively, where they remain accurate against the selected current source:

- `docs-site/site/docs/index.md` — book framing ("Why a Guided Manual?", "Key Capabilities", prerequisites)
  already narrates most of the `broader-hype` capability list and a `users`-oriented "What You'll Get" list.
  Reusable as introduction/framing material.
- `README.md` (repo root) — Quick Start, Match Strategies, Hit Policies, Batch Evaluation, Accumulator
  Engine, Serialisable Metadata, Backend Support sections: concise, current, code-example-bearing prose
  usable for the `users` track.
- `docs/user-quickstart.md` — longer worked walkthroughs for both engines (Parts 1 and 2), including
  partitioned-lattice usage; strong candidate for `users`-track worked examples.
- `CLAUDE.md` — "Architecture", "Filter engine pipeline", "Accumulator engine", "Backend purity (ENFORCED)"
  sections are the most concise existing statement of `maintainers`/`backend-architecture` material.
- `docs/superpowers/specs/*.md` (9 design docs) and matching `docs/superpowers/plans/*.md` — design
  rationale for hit policies, batch evaluation, accumulator correctness, lattice persistence, ternary
  partition routing, and set-sentinel wildcards. Useful as maintainer/contributor-depth source material and
  already cited as `recommended_docs`/`evidence` on the relevant profile concepts.
- `CONTRIBUTING.md` — branching/PR/review process; relevant only if a contributor-facing appendix or
  section is confirmed.
- Existing chapter prose (`docs-site/site/docs/chapters/*/index.md`) — candidate reuse material.
  Unchanged package source does not prove the prose is accurate; verify each reused explanation
  and example. Selective reuse does not commit us to the old chapter boundaries.
- `docs-site/profile-readme.md` — internal positioning/pitch prose (Vision, Use Cases, Architecture,
  Contributing, Maintaining) generated alongside an earlier profile pass and relocated here (byte-for-byte,
  2026-09-12) because it is not a valid package-documentation-profile output. Remains internal, not
  published; candidate source for introduction/framing prose and for the `broader-hype`/`contributors`
  sections once a structure is confirmed.

## Reconciled appendices

- **FAQ**: the current reader-facing appendix has 78 retained questions, with source-backed corrections and canonical chapter links. One false-premise Boolean question was renamed while retaining its original fragment. The internal seventy-question FAQ and chatbot JSON were verified as an equivalent historical pair and preserved byte-for-byte; they are not a newly generated export of the current FAQ. No unsupported marker-only conversion was used.
- **Glossary**: all 97 existing terms remain, with reconciled definitions and canonical primary destinations. There is one current glossary.
- Nineteen earlier chapter URLs remain usable through search-excluded reference landing pages retaining 416 heading fragments. Archive and internal planning/profile files remain outside the published source tree.
- No quiz or course-page content exists to migrate (the accepted book has none), consistent with removing
  that framing from this workflow rather than needing to strip it out here.

## Visual policy (default, per plan)

Prefer Mermaid diagrams for architecture/pipeline explanations (e.g. the filter engine's compile/bind/
evaluate/select pipeline, the accumulator's build/apply phases, partition routing). Add a MicroSim only
where the approved content calls for genuine interaction (e.g. an interactive specificity-ranking or
lattice-combination explorer) — not by default, and not as a placeholder scaffold if included.

## Confirmed editorial decisions

The user confirmed these choices on 2026-09-12 through the pilot brief interview:

1. **Audience and depth: thorough usage and internals.** Cover all five facets in one manual:
   concepts, practical package use, then internals and extension work. Include substantial
   worked usage examples and comparably thorough architecture, maintenance and extension
   explanations. Use capability/positioning material for factual introductory context, not
   a separate marketing chapter. Share explanations across audiences.
2. **FAQ appendix: included.** Review and reorganise accurate existing answers around the
   new material, linking to fuller chapter/section explanations. Inclusion is not a promise
   to preserve every answer unchanged.
3. **Glossary appendix: included.** Define terminology readers need and link terms to fuller
   chapter explanations.

Standing policies: concept-driven chapter/section boundaries rather than one chapter per
facet; selective reuse of verified material; Mermaid first; MicroSims only where interaction
adds explanatory value; no quizzes; internal graph and planning artifacts excluded from
the published site. Exact chapters and sections require separate chapter-plan approval.

## Audience expectations and technical framing

Assume a technically capable reader with Python and basic DataFrame experience.
Explain unfamiliar package concepts and their consequences without teaching elementary
programming habits or correcting implausible misunderstandings.

- Describe the package's capabilities, APIs, behavior and design decisions directly.
  Show how an enum configures a comparison; do not tell readers that they need not
  recreate the enum. Explain what an engine returns; do not add disclaimers that it
  will not physically pack or send a parcel.
- Keep worked examples concrete and explanations thorough. Respect for the audience
  means removing condescending framing, not compressing substantive teaching into
  unexplained jargon.
- Retain limitations that affect implementation or interpretation: metadata validation
  versus application input validation, backend support, conversion costs, missing-value
  semantics and engine-specific behavior. State the relevant contract and consequence
  rather than inventing a naive mistake for the reader to avoid.
- Avoid obvious real-world disclaimers, elementary reminders, playful corrective
  asides and repeated reassurance about what readers do not need to implement.
  Prefer a positive account of what the API does over unnecessary "not X" contrasts.
- During drafting and review, ask whether each aside adds a package-specific fact or
  a consequential boundary. If it merely explains the obvious or talks down to the
  reader, remove it. Apply this standard to headings, examples and summaries as well
  as the main prose.

### Direct, patient exposition

Use the maintained textbook's direct explanatory voice as the prose standard.
The approved batch sample remains a reference for worked-example depth, not a
requirement to imitate its scenario-led opening.

- Open chapters and sections by naming the package concept or API being explained,
  defining its purpose and stating the relevant scope. Establish the subject before
  introducing a business scenario.
- Introduce examples as examples of an already explained concept. State their inputs,
  operation and intended result explicitly; do not assume the reader knows an
  unstated workflow or why a fictional business situation matters.
- Give references such as "that metadata", "these relationships" and "the model" a
  concrete, established referent. Mentioning metadata is not the same as defining
  a metadata object. Name the concept or object when a pronoun would obscure it.
- Explain prerequisites locally enough to make the current passage understandable.
  Cross-references provide further detail; they must not replace the explanation
  or imply that an object has already been introduced when it has not.
- Patient writing develops the subject in a clear sequence. It does not require
  narrative hooks, abstract scene-setting, rhetorical suspense or vague promises
  about what the chapter will eventually explain.

## Reconciliation approval and execution

The earlier eight-chapter candidate plan is superseded by the user-approved eleven
chapters and four parts in `docs-site/chapter-plan.md`. The canonical mapping is
`docs-site/learning-graph/learning-graph.json`; supporting reconciliation evidence
and URL migration decisions are in `docs-site/reconciliation-crosswalk.json`.
The original approval and editorial principles are in the central repository at
`04.planning/mountainash/superpowers/specs/2026-09-13-book-reconciliation-principles.md`.

Preserve the maintained book's explanatory warmth and the approved sample's worked-example depth. The shared primitives and both engines are explicit in the opening and menus. Independent drafting may be parallel; Main retains interpretation, continuous editorial review and integration ownership.

Completion evidence is recorded in `docs-site/chapter-plan.md` and `docs-site/reconciliation-crosswalk.json`: 107 executed Python blocks, 133 ordered primary explanations, 226 backward teaching dependencies, twelve visually checked diagrams, strict build success, and zero broken local references. The selected source is `730a8583ee9d4fd6b52dc5350699eb66cc7487e9`; Python 3.12.12 and Polars 1.44.2 were exercised. Canonical enrichment, historical CIS, profiles, refresh-state and the legacy FAQ pair remain unchanged. Do not mistake this editorial completion for user approval of publication or a promoted source-refresh baseline.
