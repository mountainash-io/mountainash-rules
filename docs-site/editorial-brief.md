# mountainash-rules Editorial Brief

**Status: CONFIRMED — 2026-09-12.** The user approved the proposed single-manual scope
with thorough usage and internals, and selected both FAQ and glossary appendices.
This confirms the editorial brief only. The fresh chapter/section plan requires
separate user approval before chapter prose is generated.

## Source basis

- Candidate worktree: `mountainash-rules-book-worktree`, branch `feat/chapter-focused-book`,
  source revision `94659bb0c096485c87d329f09e944577427f129f`. Candidate documentation is reviewed separately from this source baseline.
- Package source (`src/mountainash_rules/**/*.py`) is **byte-identical** between the profile's recorded
  basis (`7d0e3dcb747949bb2d7172a34135a7872b2ed55f`, 2026-09-02) and the current candidate HEAD — confirmed
  via `git diff --stat` (empty) and matching git tree object hashes
  (`9723c20ae6b9933ad8c2259cc0ffb8cf79fc3364`). No commit between those two revisions touches
  `src/mountainash_rules`; the only changes are to `README.md` and `.github/workflows/deploy-textbook.yml`.
  The profile in `docs-site/profile/` is therefore current for this candidate and needed only
  provenance/manifest updates plus completion of previously-missing profile fields (see
  `docs-site/profile/coverage.md`, "Candidate Revalidation (2026-09-12)"), not a source re-scan.
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

The existing book in `docs-site/site/docs/` remains read-only reference material for this work.
It has eleven chapters, from `01-foundation-concepts` through `11-lattice-structures-and-results`.
A new chapter/section plan must be derived from the confirmed brief and profile facets,
not inherited automatically from these chapter boundaries.

Within that accepted book and the surrounding repository, the following prose/examples are candidate reuse
sources, selectively, where still accurate against the confirmed-unchanged source above:

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

## Appendix candidates

- **FAQ**: `docs-site/site/docs/learning-graph/faq.md` contains substantial existing FAQ material.
  Review its answers against source before reusing them as a confirmed appendix at `site/docs/faq.md`.
  Absence from navigation does not establish that an MkDocs source page is unpublished.
  The unsupported `faq-chatbot-training.json` under `docs-site/learning-graph/` remains
  untouched; do not run it through the marker-only FAQ exporter.
- **Glossary**: no `glossary.md` exists under `docs-site/` for this book.
  The confirmed glossary appendix therefore needs to be produced, not migrated.
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

## Subsequent chapter-plan approval

On 2026-09-12 the user separately approved the eight-chapter plan and its documented
teaching-dependency changes in `docs-site/chapter-plan.md`. Candidate graph promotion,
deliberate chapter remapping and writing are authorised. Publication over the live book
still requires separate acceptance.
