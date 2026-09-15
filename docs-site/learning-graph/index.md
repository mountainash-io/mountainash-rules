# Internal editorial graph

This directory supports authoring and targeted refresh of the Mountainash Rules manual. It is not a reader-facing learning-path application and must remain outside `docs-site/site/docs/`.

## Editorial authority

The [confirmed brief](../editorial-brief.md) defines audience, depth and appendices. The [approved chapter plan](../chapter-plan.md) assigns every concept to one primary chapter block and orders prerequisites before dependants. Package profiles under `../profile/` and the source repository supply evidence; the historical course description is not an active generation prerequisite.

The approved graph contains 133 concepts and 226 teaching-dependency edges. Taxonomy categories describe subject areas; they do not determine chapter boundaries. Public API use is taught before implementation detail where that detail is not a genuine prerequisite.

## Maintained artifacts

- [JSON graph](learning-graph.json): canonical node enrichments, dependency edges, source evidence and chapter assignments.
- [CSV graph](learning-graph.csv): concept IDs, labels, dependencies and taxonomy categories. Graph reconciliation preserves existing enrichments rather than replacing them with a bare CSV conversion.
- [Concept list](concept-list.md): stable concept IDs and labels.
- [Taxonomy description](concept-taxonomy.md) and [distribution report](taxonomy-distribution.md): internal category documentation.
- [Graph analysis](quality-metrics.md): structural diagnostics, not a numerical gate for reader intent or chapter approval.

Deterministic maintenance uses the installed `ibook graph` and `ibook refresh` commands from Mountainash iBook tooling. See the repository README for the pinned installation and invocation contract. Do not restore copied helper implementations in this directory.

## Current appendices and retired artifacts

The only current FAQ is [the reader appendix](../site/docs/faq.md), with 78 questions. The [glossary](../site/docs/glossary.md) contains 97 terms. The old internal seventy-question FAQ and chatbot JSON were removed when the user requested complete retirement of the old book; their history remains in Git. Do not recreate those duplicate exports or treat their deletion as a successful format conversion.

The canonical graph assigns concepts to the eleven current chapter directories. `../site/refresh-state.json` remains a historical provenance receipt, not a current content-hash baseline for the replacement. A separately verified source/profile refresh must establish that baseline before further automated maintenance; do not restore retired chapter directories from its historical entries.

Refresh hashes and append-only generation history belong in `../site/refresh-state.json`. They record verified artifacts and source provenance, not merely successful directory creation or an attempted command.
