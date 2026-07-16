# Filter Engine Explain Support — Design

> **Backlog card:** mountainash-central/01.principles/mountainash-rules/h.backlog/filter-engine-explain-support.md (P2)
> **Blocks:** mountainash-rules-service explain endpoint (its spec names this card)
> **Date:** 2026-07-16

## Problem

`ExpressionRulesEngine.evaluate` unconditionally filters to survivors
(`__survived = least(*__t_) >= 0` → `.filter(...)` in `_evaluate` Step 4).
Answering "why didn't rule X match?" needs the ternary detail for **all**
rules, and consumers must not reimplement ternary compilation.

## Design: dedicated `explain()` (option 2 from the card)

A separate method, not a flag on `evaluate` — hit-policy ordering,
assertions, ranking, and cardinality are all survivor-set semantics and must
not run over (or be confused by) non-survivor rows.

```python
def explain(self, context, dimensions: list[str] | None = None) -> ExplainResult
```

Pipeline: identical to `evaluate` Steps 0–3 (reserved-column guard,
`__rule_index`, context binding, `__t_<dim>` ternaries, `__survived`,
`__specificity`) — extracted into a shared private helper
`_scored_relation(active_dims, context_values)` so `evaluate` and `explain`
cannot drift — then: drop `__ctx_*`, **no** survival filter, **no** ranking
(`__rank` is a selection concept; explain output has none), collect.

`ExplainResult` (new, `core/result.py`, exported from the package root):
wraps the frame with `.frame`, `.count` (all rules), `.active_dimensions`,
`.survivors` (rows where `__survived`), `.non_survivors`. Not a `RuleResult`
subclass — `RuleResult`'s selection-related surface (`best_match`,
`select()`, anything reading `__rank`/`SelectionInfo`) assumes a ranked
survivor frame, which explain output deliberately is not; the frame-level
accessors would work, but inheriting them would drag the selection API
along.

Output columns: original rule columns + `__rule_index` (stable rule
identity — the row order of the rules frame, kept deliberately so explain
rows can be joined back to `evaluate` output) + `__t_<dim>` per active
dimension + `__survived` (bool) + `__specificity`. Ternary semantics
unchanged: 1 match / 0 wildcard-unknown / −1 non-match.

Edge case: `explain` raises `ValueError` when the active dimension list is
empty (explicit guard — the scoring expressions are undefined over zero
dimensions; `evaluate` has the same latent limitation, unchanged here).

## Non-goals

- Batch explain (add when a consumer exists).
- Any change to `evaluate` behaviour or its suite.
- Hit-policy interaction: explain ignores policies entirely.

## Acceptance

- For a context missing one dimension of one rule, that rule's row shows
  exactly one `-1` ternary and `__survived == False`; survivors show the
  same ternaries `evaluate` would produce; full existing suite unchanged.
