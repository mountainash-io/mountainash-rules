# Filter Engine Explain Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ExpressionRulesEngine.explain(context)` returning every rule with its ternary detail, `__survived`, and `__specificity` — no survival filter, no hit-policy interaction.

**Architecture:** Extract `evaluate`'s Steps 0–3 into a shared `_scored_relation` helper, then `explain` = that helper minus filter/rank/policy, wrapped in a new `ExplainResult` (not a `RuleResult` — no `__rank`). Exported from the package root.

**Tech Stack:** mountainash expressions/relations only (backend-purity gate applies to every touched module).

## Global Constraints

- Repo: `/Users/nathanielramm/git/mountainash-io/mountainash-rules`, branch `develop`, commit per task.
- Suite: `hatch run test:test-quick` · single: `hatch run test:test-target <nodeid>` · lint: `hatch run ruff:check`.
- No polars/ibis/narwhals imports in touched modules (purity gate parametrises them automatically).
- Public names import from the package root; add new public names to `__init__.py` `__all__` (sorted) — `tests/test_public_api.py` enforces.
- After pushing, run `hatch env prune` in mountainash-rules-babel (its env holds a non-editable copy).
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Extract `_scored_relation` (pure refactor, suite-guarded)

**Files:**
- Modify: `src/mountainash_rules/engines/filter/engine.py` (`_evaluate`, new helper)

**Interfaces:**
- Produces: `self._scored_relation(active_dims: list[str], context_values: dict[str, t.Any]) -> Relation` — rules frame with `__rule_index`, `__ctx_*` literals, `__t_<dim>` ternaries, `__survived`, `__specificity`; NO filtering. Task 2 consumes it.

- [ ] **Step 1: Extract the helper**

Move `_evaluate` Steps 0–3 verbatim into:

```python
    def _scored_relation(self, active_dims: list[str], context_values: dict[str, t.Any]) -> t.Any:
        """Rules frame scored against a context: ternaries + __survived + __specificity, unfiltered."""
        rel = relation(self._rules)
        self._check_reserved(rel, "Rules")
        rel = rel.with_row_index(name="__rule_index")

        ctx_columns = [
            ma.lit(value).alias(f"{CTX_PREFIX}{name}")
            for name, value in context_values.items()
        ]
        rel = rel.with_columns(*ctx_columns)

        dim_columns = [
            self._expressions[dim_name].name.alias(f"__t_{dim_name}")
            for dim_name in active_dims
        ] if self._expressions else []
        rel = rel.with_columns(*dim_columns)

        t_cols = [ma.col(f"__t_{d}") for d in active_dims]
        if len(t_cols) == 1:
            survived_inner = t_cols[0]
        else:
            survived_inner = ma.least(*t_cols)
        survived = survived_inner.ge(ma.lit(0)).alias("__survived")
        specificity = functools.reduce(
            lambda a, b: a.add(b),
            [c.eq(ma.lit(1)).cast(int) for c in t_cols],
        ).alias("__specificity")
        return rel.with_columns(survived, specificity)
```

`_evaluate` Steps 0–3 collapse to `rel = self._scored_relation(active_dims, context_values)`; Steps 4–7 unchanged. Keep comments/behaviour identical — this is a move, not an edit.

- [ ] **Step 2: Full suite green (the refactor's safety net)**

Run: `cd /Users/nathanielramm/git/mountainash-io/mountainash-rules && hatch run test:test-quick`
Expected: 745 passed / 36 skipped / 31 xfailed — identical to baseline. Any change in counts = the move altered behaviour; fix before proceeding.

- [ ] **Step 3: Commit**

```bash
cd /Users/nathanielramm/git/mountainash-io/mountainash-rules
git add src/mountainash_rules/engines/filter/engine.py
git commit -m "refactor: extract _scored_relation from _evaluate (explain groundwork)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `ExplainResult` + `engine.explain()`

**Files:**
- Modify: `src/mountainash_rules/core/result.py` (add `ExplainResult`)
- Modify: `src/mountainash_rules/engines/filter/engine.py` (add `explain`)
- Test: `tests/filter/test_explain.py` (new)

**Interfaces:**
- Consumes: `_scored_relation` (Task 1); `extract_context_values(context, active_dims, metadata=...)`; `CTX_PREFIX`.
- Produces: `ExplainResult(dataframe, active_dimensions)` with `.frame`, `.count`, `.active_dimensions`, `.survivors`, `.non_survivors`; `ExpressionRulesEngine.explain(context, dimensions=None) -> ExplainResult`. Task 3 exports `ExplainResult` from the root.

- [ ] **Step 1: Write the failing tests**

Create `tests/filter/test_explain.py`:

```python
"""Tests for ExpressionRulesEngine.explain — all-rules ternary detail."""

import polars as pl
import pytest

from mountainash_rules import (
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
    ExplainResult,
    MatchStrategy,
    UNKNOWN,
)


@pytest.fixture
def engine():
    metadata = DimensionsMetadata(
        dimensions=[
            Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type="str"),
            Dimension(dimension_name="channel", match_strategy=MatchStrategy.EXACT, data_type="str"),
        ]
    )
    rules = pl.DataFrame(
        {
            "rule_name": ["both_match", "one_miss", "wildcard"],
            "region": ["AU", "AU", UNKNOWN],
            "channel": ["BROKER", "DIRECT", UNKNOWN],
        }
    )
    return ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)


class TestExplain:
    def test_returns_every_rule(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        assert isinstance(result, ExplainResult)
        assert result.count == 3  # non-survivors included

    def test_non_survivor_names_the_failing_dimension(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        rows = {r["rule_name"]: r for r in result.frame.to_dicts()}
        miss = rows["one_miss"]
        assert miss["__survived"] is False
        assert miss["__t_region"] == 1      # matched
        assert miss["__t_channel"] == -1    # the reason it failed

    def test_wildcard_rows_report_zero_ternary(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        rows = {r["rule_name"]: r for r in result.frame.to_dicts()}
        assert rows["wildcard"]["__t_region"] == 0
        assert rows["wildcard"]["__survived"] is True
        assert rows["wildcard"]["__specificity"] == 0

    def test_survivor_accessors_split_correctly(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        survivor_names = {r["rule_name"] for r in result.survivors.to_dicts()}
        non_survivor_names = {r["rule_name"] for r in result.non_survivors.to_dicts()}
        assert survivor_names == {"both_match", "wildcard"}
        assert non_survivor_names == {"one_miss"}

    def test_no_rank_and_no_ctx_columns(self, engine):
        result = engine.explain({"region": "AU", "channel": "BROKER"})
        cols = result.frame.columns
        assert "__rank" not in cols
        assert not any(c.startswith("__ctx_") for c in cols)

    def test_agrees_with_evaluate_survivor_set(self, engine):
        ctx = {"region": "AU", "channel": "BROKER"}
        eval_names = {
            r["rule_name"]
            for r in engine.evaluate(ctx).survivors.to_dicts()
        }
        explain_names = {
            r["rule_name"] for r in engine.explain(ctx).survivors.to_dicts()
        }
        assert explain_names == eval_names

    def test_unknown_dimension_raises(self, engine):
        with pytest.raises(KeyError):
            engine.explain({"region": "AU"}, dimensions=["nope"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nathanielramm/git/mountainash-io/mountainash-rules && hatch run test:test-target tests/filter/test_explain.py -v`
Expected: FAIL — `ImportError: cannot import name 'ExplainResult'`.

- [ ] **Step 3: Implement**

`core/result.py` — append (after `RuleResult`):

```python
class ExplainResult:
    """Every rule scored against a context — ternaries, __survived, __specificity.

    No selection has been applied: there is no __rank and hit policies are
    not consulted. Not a RuleResult subclass — RuleResult accessors assume
    ranked survivor frames.
    """

    def __init__(self, dataframe: t.Any, active_dimensions: list[str]) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions

    @property
    def frame(self) -> t.Any:
        return self._df

    @property
    def active_dimensions(self) -> list[str]:
        return self._active_dimensions

    @property
    def count(self) -> int:
        return relation(self._df).count_rows()

    @property
    def survivors(self) -> t.Any:
        return relation(self._df).filter(ma.col("__survived")).collect()

    @property
    def non_survivors(self) -> t.Any:
        return (
            relation(self._df)
            .filter(ma.col("__survived").eq(ma.lit(False)))
            .collect()
        )
```

(`result.py` already imports `relation` and `ma`; verify at the top of the file.)

`engines/filter/engine.py` — add after `evaluate`:

```python
    def explain(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
    ) -> ExplainResult:
        """Score every rule against a context without filtering or ranking.

        Returns all rules with __t_<dim> ternaries, __survived, and
        __specificity. Hit policies are not consulted — explain answers
        "why did/didn't each rule match", not "which rule wins".
        """
        all_dim_names = list(self._expressions.keys()) if self._expressions else []
        active_dims = dimensions if dimensions else all_dim_names
        for dim_name in active_dims:
            if dim_name not in all_dim_names:
                raise KeyError(f"Dimension '{dim_name}' not found in expressions")

        context_values = extract_context_values(
            context, active_dims, metadata=self._metadata
        )
        rel = self._scored_relation(active_dims, context_values)
        rel = rel.drop(*[f"{CTX_PREFIX}{d}" for d in active_dims])
        return ExplainResult(
            dataframe=rel.collect(),
            active_dimensions=active_dims,
        )
```

(import `ExplainResult` alongside `RuleResult` from `mountainash_rules.core.result`).

Root export (part of this task — the tests import from the root):
in `src/mountainash_rules/__init__.py`, import `ExplainResult` from
`mountainash_rules.core.result` and insert `"ExplainResult"` into `__all__`
(sorted); add `"ExplainResult"` to `PUBLIC_NAMES` in
`tests/test_public_api.py`.

- [ ] **Step 4: Run tests — new file, then full suite**

Run: `cd /Users/nathanielramm/git/mountainash-io/mountainash-rules && hatch run test:test-target tests/filter/test_explain.py -v`
Expected: all 7 PASS. Then `hatch run test:test-quick`: 752 passed (745 + 7) / 36 skipped / 31 xfailed, and `hatch run ruff:check` clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/nathanielramm/git/mountainash-io/mountainash-rules
git add src/mountainash_rules/core/result.py src/mountainash_rules/engines/filter/engine.py src/mountainash_rules/__init__.py tests/filter/test_explain.py tests/test_public_api.py
git commit -m "feat: ExpressionRulesEngine.explain + ExplainResult (all-rules ternary detail)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Docs + push + downstream refresh

**Files:**
- Modify: `CLAUDE.md` (filter engine pipeline section)

- [ ] **Step 1: Document**

In CLAUDE.md's "Filter engine pipeline" section, append:

```markdown
`explain(context, dimensions=None)` → `ExplainResult`: every rule scored
(ternaries + `__survived` + `__specificity`), no survival filter, no rank,
no hit-policy interaction. Shares Steps 1–3 with `evaluate` via
`_scored_relation`.
```

- [ ] **Step 2: Verify, push, refresh babel env, flip the card**

Run: `hatch run test:test-quick && hatch run ruff:check` — green/clean.

```bash
cd /Users/nathanielramm/git/mountainash-io/mountainash-rules
git add CLAUDE.md
git commit -m "docs: explain() in filter-engine pipeline notes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
cd /Users/nathanielramm/git/mountainash-io/mountainash-rules-babel && hatch env prune && hatch run test:test-quick
```

Expected: babel 79 passed. Flip `mountainash-central/.../mountainash-rules/h.backlog/filter-engine-explain-support.md` to DONE with the commit refs, and update the rules-service `explain-endpoint.md` card/spec from "blocked upstream" to naming `explain()` as available.
