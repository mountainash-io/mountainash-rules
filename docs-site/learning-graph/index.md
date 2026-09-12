# Internal editorial graph

This directory supports authoring and targeted refresh of the Mountainash Rules manual. It is not a reader-facing learning-path application and must remain outside `docs-site/site/docs/`.

## Editorial authority

The [confirmed brief](../editorial-brief.md) defines audience, depth and appendices. The [approved chapter plan](../chapter-plan.md) assigns every concept to one primary chapter block and orders prerequisites before dependants. Package profiles under `../profile/` and the source repository supply evidence; the historical course description is not an active generation prerequisite.

The approved graph contains 133 concepts and 235 teaching-dependency edges. Taxonomy categories describe subject areas; they do not determine chapter boundaries. Public API use is taught before implementation detail where that detail is not a genuine prerequisite.

## Maintained artifacts

- [JSON graph](learning-graph.json): canonical node enrichments, dependency edges, source evidence and chapter assignments.
- [CSV graph](learning-graph.csv): concept IDs, labels, dependencies and taxonomy categories. Graph reconciliation preserves existing enrichments rather than replacing them with a bare CSV conversion.
- [Concept list](concept-list.md): stable concept IDs and labels.
- [Taxonomy description](concept-taxonomy.md) and [distribution report](taxonomy-distribution.md): internal category documentation.
- [Graph analysis](quality-metrics.md): structural diagnostics, not a numerical gate for reader intent or chapter approval.

Deterministic maintenance uses the installed `ibook graph` and `ibook refresh` commands from Mountainash iBook tooling. See the repository README for the pinned installation and invocation contract. Do not restore copied helper implementations in this directory.

## FAQ history and format safety

`faq.md` and `faq-chatbot-training.json` retain the existing heading-based FAQ and its JSON representation as migration inputs. Their original 70 ordered category/question/answer records match after trimming only surrounding answer whitespace. The JSON SHA-256 at incorporation is `9bbc887c155f5429fe99365c30a081a03200c13674a3e53a38e1c986b707bb6f`.

The current reader appendix belongs at `../site/docs/faq.md`, not in a public graph directory. Editing that appendix does not implicitly convert or overwrite the legacy chatbot data. The packaged marker exporter supports paired-marker FAQs only; an empty projection of this nonempty legacy FAQ is not a valid migration.

Refresh hashes and append-only generation history belong in `../site/refresh-state.json`. They record verified artifacts and source provenance, not merely successful directory creation or an attempted command.
