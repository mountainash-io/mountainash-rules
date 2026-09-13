# Package profile coverage

## Invocation and provenance

- Requested behavior: `incremental`; mode: `interactive`.
- Selected source: `730a8583ee9d4fd6b52dc5350699eb66cc7487e9`, fetched `origin/develop`, isolated detached worktree.
- Accepted profile source and unchanged book baseline: `7d0e3dcb747949bb2d7172a34135a7872b2ed55f`.
- Resuming skill/tool revision: `1dc336adb73d228aed73366150c53423765bba51`; the installed distribution and both constraints are pinned to that revision.
- Source analysis was reused from the earlier full profile scan at tooling revision `3961b7b734bfbca98a39084820af3fe2517d72af`, generated `2026-09-13T04:56:30.627032+00:00`. This run did not repeat that scan. All 50 earlier input digests, 28 candidate output digests and the complete original profile snapshot matched before reuse. The new invocation explicitly digests the original inputs and reused candidate artifacts.
- The earlier scan selected full scope because existing facets lacked required fields and had ten audience-membership inconsistencies. Seven of 21 source modules changed, exactly one third; the threshold alone did not select full scope. This recovery retains that verified work rather than changing discovery or editorial scope.
- The user approved relocating the inherited `docs-site/profile/README.md` to `docs-site/profile-readme.md` unchanged. Maintenance preparation performed the relocation before profile writes, with a recoverable original outside the profile root. No inbound references were found. Relocation does not endorse the overview's stale factual claims; its text was not corrected or published.
- The worktree was clean on creation; the manifest records a dirty documentation worktree after authorized relocation. Source bytes remain unchanged. The before-profile, reused-profile, invocation, result and command evidence are retained in the caller-owned transient run directory outside this profile root.

## Coverage accounting

| Measure | Count |
|---|---:|
| Discovered source modules | 21 |
| Profiled modules | 21 |
| Ignored modules | 0 |
| Missing profiles | 0 |
| Orphan profiles | 0 |
| Stale profile source hashes | 0 |
| Low-confidence modules | 0 |
| Requested audience facets | 5 |
| Preserved manual objects | 21 |

All module IDs and file mappings were retained. The 21 module records and five facets were reused byte-for-byte from the checked candidate. Every original `manual` object is preserved by value. Those original objects contain empty editorial fields; this does not demonstrate preservation of populated or unknown fields. No source discovery exclusions matched a Python file in the declared package root.

## Source delta and findings

Changed paths are `src/mountainash_rules/core/{batch_result,compiler,context,hit_policy,result,set_wildcard}.py` and `src/mountainash_rules/engines/filter/engine.py`. Six contain executable or API changes; `set_wildcard.py` has only a documentation primitive-name correction.

- Compiler behavior changed behind stable public methods: missing/sentinel context handling differs for ordinary predicates, strict CONTEXT_REGEX and rule-wildcard-aware EXACT_KEY; RANGE computes an explicit row-wise ternary minimum; set matching uses list.t_contains.
- Context binding preserves absent Booleans as null. Batch caller IDs are validated globally before conversion or chunking; generated IDs identify original positions within one submitted input.
- Policy configuration and survivor assertions precede caller limits. Re-selection requires complete candidate provenance, including after a no-op limit or singleton FIRST/PRIORITY/ANY selection.
- Batch accessors choose the minimum retained rank, not an assumed surviving rank 1. Rank order and source identity are separate contracts.
- Filter construction and dimension/limit inputs reject invalid boundaries explicitly, including typed empty input. A valid zero-rule table is not a zero-dimension engine.
- Unchanged accumulator modules depend on changed filter/results. Constraint-free accumulator apply is rejected by the delegated filter; no-key routing remains separate. The partitioned LatticeIndex batch wrapper does not automatically inherit complete filter batch ordering, global-ID or selection-metadata guarantees.
- The former profile's twelve strategies and thirty root exports were corrected to thirteen and thirty-two. These are profile fact corrections, not new source changes in this delta.

## Limitations and preservation

The five requested audience facets contain canonical concepts, evidence paths and documentation plans; every featured/concept module declares the corresponding audience. Test and source paths were inspected as evidence in the original scan, not executed as proof of runtime behavior.

Backend capability limits remain: expression support does not imply universal engine support; per-row REGEX is Polars-native and list/string predicates have backend-specific restrictions. Chunking bounds each evaluation, not all staging, retained results or diagnostics. Aggregate overflow is separate from prime-product width; lattice witness validation raises above its ceiling rather than sampling. Dynamic schema mutation after engine construction is not established by this analysis.

No source code, chapter, FAQ, public graph mirror, simulation, glossary or book state was changed by profiling. Existing heading-based FAQ Markdown and bespoke JSON are preservation inputs, not candidates for an implicit format conversion. No Rules test suite, backend matrix, book build or browser verification is claimed by this stage.

## Validation and handoff

The inherited README conflict was resolved by the authorized maintenance relocation, not a profile-writer permission expansion or validator exception. The previous failed result remains historical evidence, not a current success claim. This run validates the resulting profile with `--before-profile`, authors a new truthful completion result and validates that document with `--result`. Exact command exits and output accompany the transient result; only observed successful validation with review-required warnings resolved permits content-impact analysis.

A new profile source SHA is not a refreshed book. The graph/state baseline stays at `7d0e3dcb747949bb2d7172a34135a7872b2ed55f` until approved content work and preservation verification actually complete.
