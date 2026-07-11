# Hit Policies and Rule Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `HitPolicy` (UNIQUE/FIRST/PRIORITY/ANY/COLLECT/RULE_ORDER), `priority_field`, and deterministic tie-breaking to the filter engine, per `docs/superpowers/specs/2026-07-12-hit-policies-design.md`.

**Architecture:** Policy logic lives in a new `hit_policy.py` module (pure functions + `SelectionInfo` + `HitPolicyViolationError`); `engine.py` threads it into the exact pipeline order (survival → policy sort → rank → assertions → min_specificity/top_n → cardinality); `RuleResult.select()` re-slices untruncated results. The accumulator gains only a defensive COLLECT pin.

**Tech Stack:** Python 3.12, mountainash expressions/relations, pydantic, polars fixtures.

## Global Constraints

- **Depends on the serialisable-metadata plan having landed** (StrEnums; `DimensionsMetadata` fields must serialise). If executing before it, park Task 6's YAML assertion.
- Per `two-engines-two-stages` (ENFORCED): no ranking machinery in the accumulator.
- Exact pipeline order: survival filter → policy ordering sort → `__rank` → UNIQUE/ANY assertions → `min_specificity` → `top_n` → cardinality (`head(1)` for FIRST/PRIORITY/ANY).
- Reserved engine columns: `__rule_index`, `__rank`, `__specificity`, `__survived`, `__t_*`, `__ctx_*` — collision in input rules raises `ValueError`.
- Zero survivors is never a violation, for every policy.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: HitPolicy enum + metadata fields

**Files:**
- Modify: `src/mountainash_rules/constants.py`, `src/mountainash_rules/dimension.py`
- Test: `tests/test_hit_policy.py` (create)

**Interfaces:**
- Produces: `HitPolicy(StrEnum)` (`"collect"`, `"unique"`, `"first"`, `"priority"`, `"any"`, `"rule_order"`); `DimensionsMetadata.hit_policy: HitPolicy = HitPolicy.COLLECT`, `priority_field: str | None = None`, `output_fields: list[str] = Field(default_factory=list)`; validator: PRIORITY requires priority_field.

- [ ] **Step 1: Write the failing tests** (create `tests/test_hit_policy.py`)

```python
"""Tests for hit policies, priority, and deterministic tie-breaking."""

import polars as pl
import pytest

from mountainash_rules.constants import HitPolicy, MatchStrategy
from mountainash_rules.dimension import Dimension, DimensionsMetadata
from mountainash_rules.engine import ExpressionRulesEngine


class TestHitPolicyEnum:
    def test_values(self):
        assert HitPolicy("rule_order") is HitPolicy.RULE_ORDER
        assert HitPolicy.COLLECT.value == "collect"


class TestMetadataFields:
    def test_defaults(self):
        md = DimensionsMetadata(dimensions=[Dimension(dimension_name="x")])
        assert md.hit_policy is HitPolicy.COLLECT
        assert md.priority_field is None
        assert md.output_fields == []

    def test_priority_requires_field(self):
        with pytest.raises(ValueError, match="priority_field"):
            DimensionsMetadata(
                dimensions=[Dimension(dimension_name="x")],
                hit_policy=HitPolicy.PRIORITY,
            )
```

- [ ] **Step 2: Run to verify failure** — `hatch run test:test-target tests/test_hit_policy.py -v` → ImportError on `HitPolicy`.

- [ ] **Step 3: Implement**

`constants.py`:

```python
class HitPolicy(StrEnum):
    """Selection semantics applied over surviving rules."""

    COLLECT = "collect"        # all survivors, specificity order (default)
    UNIQUE = "unique"          # assert <= 1 survivor
    FIRST = "first"            # single survivor, rule order wins
    PRIORITY = "priority"      # single survivor, priority_field wins
    ANY = "any"                # survivors must agree on outputs; return one
    RULE_ORDER = "rule_order"  # all survivors, rule order
```

`dimension.py` — add to `DimensionsMetadata`:

```python
    hit_policy: HitPolicy = HitPolicy.COLLECT
    priority_field: t.Optional[str] = None
    output_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_hit_policy(self) -> "DimensionsMetadata":
        if self.hit_policy == HitPolicy.PRIORITY and not self.priority_field:
            raise ValueError("hit_policy=priority requires priority_field")
        return self
```

- [ ] **Step 4: Run** → PASS; then `hatch run test:test-quick` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/constants.py src/mountainash_rules/dimension.py tests/test_hit_policy.py
git commit -m "feat: HitPolicy enum and table-level policy fields on DimensionsMetadata

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: hit_policy module — SelectionInfo, violations, ordering

**Files:**
- Create: `src/mountainash_rules/hit_policy.py`
- Test: `tests/test_hit_policy.py`

**Interfaces:**
- Produces (consumed by Tasks 3–5):

```python
@dataclass(frozen=True)
class SelectionInfo:
    dimension_rule_fields: tuple[str, ...]   # resolved, incl. RANGE min/max
    priority_field: str | None
    output_fields: tuple[str, ...]
    truncated: bool                          # top_n/min_specificity removed rows
    observability: bool

class HitPolicyViolationError(ValueError):
    def __init__(self, policy: HitPolicy, offending: t.Any, message: str): ...
    # .policy, .offending (DataFrame of violating rows)

def ordering_keys(policy, priority_field) -> list[tuple[str, bool]]:
    """[(column, descending), ...] ending with ('__rule_index', False)."""

def selection_info_from_metadata(metadata, priority_field, observability) -> SelectionInfo

def check_assertions(rel, policy, info) -> None      # raises on UNIQUE/ANY violation
def apply_cardinality(rel, policy) -> t.Any          # head(1) for FIRST/PRIORITY/ANY
def default_output_fields(columns, info) -> list[str]
```

- [ ] **Step 1: Write the failing tests** (append to `tests/test_hit_policy.py`)

```python
from mountainash_rules.hit_policy import (
    HitPolicyViolationError,
    SelectionInfo,
    default_output_fields,
    ordering_keys,
)


class TestOrderingKeys:
    def test_collect(self):
        assert ordering_keys(HitPolicy.COLLECT, None) == [
            ("__specificity", True), ("__rule_index", False),
        ]

    def test_first_and_rule_order_ignore_specificity(self):
        assert ordering_keys(HitPolicy.FIRST, None) == [("__rule_index", False)]
        assert ordering_keys(HitPolicy.RULE_ORDER, None) == [("__rule_index", False)]

    def test_priority(self):
        assert ordering_keys(HitPolicy.PRIORITY, "salience") == [
            ("salience", True), ("__specificity", True), ("__rule_index", False),
        ]


class TestDefaultOutputFields:
    def test_excludes_rule_condition_and_internal_columns(self):
        info = SelectionInfo(
            dimension_rule_fields=("region", "amt_min", "amt_max"),
            priority_field="salience",
            output_fields=(),
            truncated=False,
            observability=True,
        )
        cols = ["rule_name", "region", "amt_min", "amt_max", "salience",
                "price", "code", "__rank", "__specificity", "__rule_index",
                "__t_region"]
        assert default_output_fields(cols, info) == ["price", "code"]

    def test_explicit_output_fields_win(self):
        info = SelectionInfo((), None, ("price",), False, True)
        assert default_output_fields(["price", "code"], info) == ["price"]
```

- [ ] **Step 2: Run to verify failure** — ImportError on `mountainash_rules.hit_policy`.

- [ ] **Step 3: Implement** (create `src/mountainash_rules/hit_policy.py`)

```python
"""Hit-policy selection layer over the filter engine's survivor pipeline."""

from __future__ import annotations

import typing as t
from dataclasses import dataclass

import mountainash.expressions as ma

from mountainash_rules.constants import HitPolicy, MatchStrategy

if t.TYPE_CHECKING:
    from mountainash_rules.dimension import DimensionsMetadata


@dataclass(frozen=True)
class SelectionInfo:
    """What a RuleResult needs to re-apply policies post-hoc."""

    dimension_rule_fields: tuple[str, ...]
    priority_field: str | None
    output_fields: tuple[str, ...]
    truncated: bool
    observability: bool


class HitPolicyViolationError(ValueError):
    """A UNIQUE or ANY assertion failed over the survivor set."""

    def __init__(self, policy: HitPolicy, offending: t.Any, message: str) -> None:
        super().__init__(message)
        self.policy = policy
        self.offending = offending


def selection_info_from_metadata(
    metadata: "DimensionsMetadata | None",
    priority_field: str | None,
    observability: bool,
) -> SelectionInfo:
    fields: list[str] = []
    md_priority: str | None = None
    md_outputs: tuple[str, ...] = ()
    if metadata is not None:
        for d in metadata.dimensions:
            if d.match_strategy == MatchStrategy.RANGE:
                fields.extend([d.range_min_field, d.range_max_field])
            else:
                fields.append(d.resolved_rule_field)
        md_priority = metadata.priority_field
        md_outputs = tuple(metadata.output_fields)
    return SelectionInfo(
        dimension_rule_fields=tuple(fields),
        priority_field=priority_field or md_priority,
        output_fields=md_outputs,
        truncated=False,
        observability=observability,
    )


def ordering_keys(
    policy: HitPolicy, priority_field: str | None
) -> list[tuple[str, bool]]:
    """[(column, descending), ...]; always ends with __rule_index ascending."""
    if policy in (HitPolicy.FIRST, HitPolicy.RULE_ORDER):
        return [("__rule_index", False)]
    if policy == HitPolicy.PRIORITY:
        if not priority_field:
            raise ValueError("hit_policy=priority requires priority_field")
        return [
            (priority_field, True),
            ("__specificity", True),
            ("__rule_index", False),
        ]
    # COLLECT, UNIQUE, ANY share the deterministic default ordering
    return [("__specificity", True), ("__rule_index", False)]


def default_output_fields(columns: list[str], info: SelectionInfo) -> list[str]:
    if info.output_fields:
        return [c for c in columns if c in info.output_fields]
    excluded = set(info.dimension_rule_fields) | {"rule_name"}
    if info.priority_field:
        excluded.add(info.priority_field)
    return [
        c for c in columns
        if c not in excluded and not c.startswith("__")
    ]


def check_assertions(rel: t.Any, policy: HitPolicy, info: SelectionInfo) -> None:
    """Raise HitPolicyViolationError for UNIQUE/ANY breaches. Zero survivors pass."""
    if policy == HitPolicy.UNIQUE:
        if rel.count_rows() > 1:
            offending = rel.collect()
            raise HitPolicyViolationError(
                policy, offending,
                f"hit_policy=unique but {rel.count_rows()} rules survived",
            )
    elif policy == HitPolicy.ANY:
        if rel.count_rows() > 1:
            outputs = default_output_fields(rel.columns, info)
            if not outputs:
                raise ValueError(
                    "hit_policy=any requires output_fields when no metadata "
                    "is available to infer them"
                )
            distinct = (
                rel.select(*[ma.col(c) for c in outputs]).unique().count_rows()
            )
            if distinct > 1:
                raise HitPolicyViolationError(
                    policy, rel.collect(),
                    f"hit_policy=any but survivors disagree on outputs {outputs}",
                )


def apply_cardinality(rel: t.Any, policy: HitPolicy) -> t.Any:
    if policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY):
        return rel.head(1)
    return rel
```

Note: `check_assertions` calls `count_rows()` twice in the UNIQUE branch for message clarity — hoist to a local if the relation is single-consumption in any backend (check how existing code reuses relations after `count_rows()`; `_expand_level` already does count-then-continue, so reuse is safe).

- [ ] **Step 4: Run** → `hatch run test:test-target tests/test_hit_policy.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/hit_policy.py tests/test_hit_policy.py
git commit -m "feat: hit_policy module - SelectionInfo, ordering, assertions, violations

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Engine — __rule_index, reserved-column check, deterministic COLLECT

**Files:**
- Modify: `src/mountainash_rules/engine.py` (`_evaluate`)
- Test: `tests/test_hit_policy.py`

**Interfaces:**
- Produces: every evaluation adds `__rule_index` (input row order) before filtering; sort key becomes `(__specificity desc, __rule_index asc)`; reserved-column collision raises `ValueError`. `__rule_index` is retained in results.

- [ ] **Step 1: Write the failing tests**

```python
def _tie_rules():
    # two rules with identical specificity for any matching context
    return pl.DataFrame({
        "rule_name": ["second_in_table", "first_in_table"],
        "region": ["AU", "AU"],
        "price": [2.0, 1.0],
    })[::-1]  # ensure row order is first_in_table, second_in_table


def _region_md(**kwargs):
    return DimensionsMetadata(
        dimensions=[Dimension(dimension_name="region")], **kwargs
    )


class TestDeterministicTieBreak:
    def test_collect_ties_broken_by_rule_order(self):
        rules = pl.DataFrame({
            "rule_name": ["r_late", "r_early"],
            "region": ["AU", "AU"],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        result = engine.evaluate({"region": "AU"})
        rows = result.survivors.to_dicts()
        assert [r["rule_name"] for r in rows] == ["r_late", "r_early"]
        assert [r["__rule_index"] for r in rows] == [0, 1]
        assert [r["__rank"] for r in rows] == [1, 2]


class TestReservedColumns:
    def test_reserved_column_in_rules_raises(self):
        rules = pl.DataFrame({
            "rule_name": ["r"], "region": ["AU"], "__rank": [9],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        with pytest.raises(ValueError, match="__rank"):
            engine.evaluate({"region": "AU"})
```

(Drop the `_tie_rules` helper above if unused — the inline frame in the test is the actual fixture.)

- [ ] **Step 2: Run to verify failure** — `__rule_index` KeyError / no ValueError raised.

- [ ] **Step 3: Implement** in `_evaluate`:

At the top, after `rel = relation(self._rules)`:

```python
        RESERVED = ("__rule_index", "__rank", "__specificity", "__survived")
        colliding = [
            c for c in rel.columns
            if c in RESERVED or c.startswith(("__t_", CTX_PREFIX))
        ]
        if colliding:
            raise ValueError(
                f"Rules frame contains reserved engine columns: {colliding}"
            )
        rel = rel.with_row_index(name="__rule_index")
```

Change the Step-4 sort to a stable two-key sort. Check the relations API signature first (`python -c "import inspect, mountainash.relations as r; print(inspect.signature(r.Relation.sort))"`); with a multi-column signature:

```python
        rel = (
            rel
            .filter(ma.col("__survived"))
            .sort(["__specificity", "__rule_index"], descending=[True, False])
            .with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )
```

If `sort` takes only a single column + flag, chain two stable sorts (`sort("__rule_index")` then `sort("__specificity", descending=True)`) **only if the backend sort is documented stable** — otherwise construct a single composite: this is the one place the plan allows an implementation decision; record which form was used in the commit message.

- [ ] **Step 4: Run** the new tests → PASS; existing engine tests → `hatch run test:test-target tests/test_engine.py tests/test_result.py -v` → PASS (results gain a `__rule_index` column; fix any column-set assertions accordingly).

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engine.py tests/
git commit -m "feat: deterministic tie-break via __rule_index; reserved-column guard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Engine — full policy pipeline

**Files:**
- Modify: `src/mountainash_rules/engine.py` (`evaluate` signature, `_evaluate` pipeline)
- Test: `tests/test_hit_policy.py`

**Interfaces:**
- Consumes: Task 2's `ordering_keys`, `check_assertions`, `apply_cardinality`, `selection_info_from_metadata`.
- Produces: `evaluate(context, ..., hit_policy: HitPolicy | None = None, priority_field: str | None = None)`; `None` = metadata's policy (COLLECT on the expressions-only path). `RuleResult` gains `selection_info` (Task 5 finishes the accessor work).

- [ ] **Step 1: Write the failing tests**

```python
class TestPolicySemantics:
    def _rules(self):
        return pl.DataFrame({
            "rule_name": ["generic", "specific"],
            "region": ["<NA>", "AU"],      # generic is a wildcard
            "salience": [10, 1],
            "price": [1.0, 2.0],
        })

    def test_first_ignores_specificity(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        result = engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.FIRST)
        assert result.count == 1
        assert result.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_priority_beats_specificity(self):
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        result = engine.evaluate(
            {"region": "AU"},
            hit_policy=HitPolicy.PRIORITY, priority_field="salience",
        )
        assert result.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_unique_violation(self):
        from mountainash_rules.hit_policy import HitPolicyViolationError
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        with pytest.raises(HitPolicyViolationError) as exc_info:
            engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
        assert exc_info.value.policy is HitPolicy.UNIQUE
        assert len(exc_info.value.offending) == 2

    def test_unique_zero_survivors_passes(self):
        engine = ExpressionRulesEngine(
            rules=pl.DataFrame({"rule_name": ["r"], "region": ["NZ"]}),
            dimension_metadata=_region_md(),
        )
        result = engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
        assert result.count == 0

    def test_any_agreeing_outputs_returns_one(self):
        rules = pl.DataFrame({
            "rule_name": ["a", "b"], "region": ["AU", "<NA>"], "price": [5.0, 5.0],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        result = engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY)
        assert result.count == 1

    def test_any_disagreeing_outputs_raises(self):
        from mountainash_rules.hit_policy import HitPolicyViolationError
        engine = ExpressionRulesEngine(
            rules=self._rules(), dimension_metadata=_region_md()
        )
        with pytest.raises(HitPolicyViolationError):
            engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY)

    def test_metadata_policy_applies_and_override_wins(self):
        md = _region_md(hit_policy=HitPolicy.FIRST)
        engine = ExpressionRulesEngine(rules=self._rules(), dimension_metadata=md)
        assert engine.evaluate({"region": "AU"}).count == 1          # FIRST from metadata
        assert engine.evaluate(
            {"region": "AU"}, hit_policy=HitPolicy.COLLECT
        ).count == 2                                                  # override
```

Note for the implementer on `test_any_disagreeing_outputs_raises`: `salience` is not the declared priority field in this call, so it counts as an output column and disagrees (10 vs 1) along with `price` — the violation is expected regardless of price.

- [ ] **Step 2: Run to verify failure** — `evaluate() got an unexpected keyword argument 'hit_policy'`.

- [ ] **Step 3: Implement**

`evaluate` signature and resolution:

```python
    def evaluate(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
        top_n: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
        hit_policy: HitPolicy | None = None,
        priority_field: str | None = None,
    ) -> RuleResult:
        ...
        if hit_policy is None:
            hit_policy = (
                self._metadata.hit_policy if self._metadata else HitPolicy.COLLECT
            )
        info = selection_info_from_metadata(
            self._metadata, priority_field, include_observability
        )
```

`_evaluate` replaces the sort/rank/filter block with the exact order:

```python
        # Step 4: survival filter, policy ordering, rank
        keys = ordering_keys(hit_policy, info.priority_field)
        rel = rel.filter(ma.col("__survived"))
        rel = rel.sort([k for k, _ in keys], descending=[d for _, d in keys])
        rel = (
            rel.with_row_index(name="__rank")
            .with_columns(ma.col("__rank").add(ma.lit(1)).alias("__rank"))
        )

        # Step 5: assertions over the FULL survivor set (pre-truncation)
        check_assertions(rel, hit_policy, info)

        # Step 6: optional filters, then cardinality
        truncated = False
        if min_specificity is not None:
            before = rel.count_rows()
            rel = rel.filter(ma.col("__specificity").ge(ma.lit(min_specificity)))
            truncated = truncated or rel.count_rows() < before
        if top_n is not None:
            before = rel.count_rows()
            rel = rel.head(top_n)
            truncated = truncated or before > top_n
        rel = apply_cardinality(rel, hit_policy)
```

`_evaluate` returns `(rel.collect(), truncated)`; `evaluate` builds
`RuleResult(dataframe=df, active_dimensions=active_dims, selection_info=dataclasses.replace(info, truncated=truncated))` (Task 5 adds the parameter — until then, stash it on the instance as `result._selection_info = ...` is NOT allowed; do Tasks 4 and 5 in one review cycle if the intermediate state would fail tests, or land Task 4 passing `selection_info` through a temporary keyword that Task 5 formalises — the plan intends Tasks 4+5 as one PR-sized unit executed back-to-back).

- [ ] **Step 4: Run** → `hatch run test:test-target tests/test_hit_policy.py -v` → PASS; full quick suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/engine.py tests/test_hit_policy.py
git commit -m "feat: hit-policy pipeline in ExpressionRulesEngine.evaluate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: RuleResult.select() with truncation guard

**Files:**
- Modify: `src/mountainash_rules/result.py`, `src/mountainash_rules/engine.py` (pass `selection_info`)
- Test: `tests/test_hit_policy.py`

**Interfaces:**
- Consumes: `SelectionInfo`, `ordering_keys`, `check_assertions`, `apply_cardinality`.
- Produces: `RuleResult.__init__(dataframe, active_dimensions, selection_info: SelectionInfo | None = None)`; `RuleResult.select(policy, priority_field=None) -> RuleResult`; raises `ValueError` when `selection_info.truncated` or when `__specificity`/`__rule_index` absent.

- [ ] **Step 1: Write the failing tests**

```python
class TestResultSelect:
    def _collect_result(self, **eval_kwargs):
        rules = pl.DataFrame({
            "rule_name": ["generic", "specific"],
            "region": ["<NA>", "AU"],
            "salience": [10, 1],
            "price": [1.0, 2.0],
        })
        engine = ExpressionRulesEngine(rules=rules, dimension_metadata=_region_md())
        return engine.evaluate({"region": "AU"}, **eval_kwargs)

    def test_select_first_from_collect(self):
        result = self._collect_result()
        first = result.select(HitPolicy.FIRST)
        assert first.count == 1
        assert first.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_select_priority_from_collect(self):
        result = self._collect_result()
        best = result.select(HitPolicy.PRIORITY, priority_field="salience")
        assert best.best_match.to_dicts()[0]["rule_name"] == "generic"

    def test_select_unique_raises_on_two_survivors(self):
        from mountainash_rules.hit_policy import HitPolicyViolationError
        with pytest.raises(HitPolicyViolationError):
            self._collect_result().select(HitPolicy.UNIQUE)

    def test_select_refuses_truncated_result(self):
        result = self._collect_result(top_n=1)
        with pytest.raises(ValueError, match="truncated"):
            result.select(HitPolicy.UNIQUE)
```

- [ ] **Step 2: Run to verify failure** — `AttributeError: 'RuleResult' object has no attribute 'select'`.

- [ ] **Step 3: Implement**

`result.py`:

```python
from mountainash_rules.constants import HitPolicy
from mountainash_rules.hit_policy import (
    SelectionInfo,
    apply_cardinality,
    check_assertions,
    ordering_keys,
)


class RuleResult:
    def __init__(
        self,
        dataframe: t.Any,
        active_dimensions: list[str],
        selection_info: SelectionInfo | None = None,
    ) -> None:
        self._df = dataframe
        self._active_dimensions = active_dimensions
        self._selection_info = selection_info

    def select(
        self, policy: HitPolicy, priority_field: str | None = None
    ) -> "RuleResult":
        """Re-apply a hit policy over an untruncated COLLECT result."""
        info = self._selection_info
        if info is None:
            raise ValueError(
                "select() requires a result produced by evaluate() with "
                "metadata (no selection info available)"
            )
        if info.truncated:
            raise ValueError(
                "select() on a truncated result (top_n/min_specificity was "
                "applied); re-evaluate without truncation instead"
            )
        rel = relation(self._df)
        for required in ("__specificity", "__rule_index"):
            if required not in rel.columns:
                raise ValueError(f"select() requires the {required} column")
        pf = priority_field or info.priority_field
        keys = ordering_keys(policy, pf)
        rel = rel.sort([k for k, _ in keys], descending=[d for _, d in keys])
        check_assertions(rel, policy, info)
        rel = apply_cardinality(rel, policy)
        return RuleResult(
            dataframe=rel.collect(),
            active_dimensions=self._active_dimensions,
            selection_info=info,
        )
```

`engine.py::evaluate` passes `selection_info=...` (finalising Task 4's hand-off) — the `__rank` column of a `select()`ed result is stale relative to the new ordering; recompute it inside `select()` by dropping and re-adding `__rank` with the same `with_row_index` + add-1 idiom used in `_evaluate`.

- [ ] **Step 4: Run** → PASS; quick suite → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mountainash_rules/result.py src/mountainash_rules/engine.py tests/test_hit_policy.py
git commit -m "feat: RuleResult.select re-slices untruncated results by policy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Accumulator COLLECT pin + serialisation round-trip + exports

**Files:**
- Modify: `src/mountainash_rules/accumulator_engine.py` (`_build_apply_metadata`), `src/mountainash_rules/__init__.py`
- Test: `tests/test_hit_policy.py`, `tests/test_dimension_serialization.py`

**Interfaces:**
- Produces: `_build_apply_metadata()` pins `hit_policy=HitPolicy.COLLECT`; `HitPolicy`, `HitPolicyViolationError`, `SelectionInfo` exported from the package root; policy fields round-trip through YAML.

- [ ] **Step 1: Write the failing tests**

```python
class TestAccumulatorCollectPin:
    def test_apply_metadata_pins_collect(self):
        from mountainash_rules.accumulator_engine import AccumulatorEngine
        md = DimensionsMetadata(
            dimensions=[Dimension(dimension_name="x")],
            hit_policy=HitPolicy.FIRST,
        )
        engine = AccumulatorEngine(dimension_metadata=md)
        assert engine._build_apply_metadata().hit_policy is HitPolicy.COLLECT


def test_hit_policy_yaml_round_trip():
    md = DimensionsMetadata(
        dimensions=[Dimension(dimension_name="x")],
        hit_policy=HitPolicy.PRIORITY,
        priority_field="salience",
        output_fields=["price"],
    )
    assert DimensionsMetadata.from_yaml(md.to_yaml()) == md


def test_package_exports():
    from mountainash_rules import HitPolicy as HP
    from mountainash_rules import HitPolicyViolationError, SelectionInfo  # noqa: F401
    assert HP("first") is HP.FIRST
```

- [ ] **Step 2: Run to verify failure** — apply metadata inherits default (passes trivially?) — no: the test with `hit_policy=FIRST` on the outer metadata passes only if the pin is explicit... `_build_apply_metadata` constructs `DimensionsMetadata(dimensions=dims)` fresh, so it already defaults to COLLECT and the pin test PASSES immediately. That is expected: the assertion documents the invariant. The export test FAILS (names not in `__init__`). Run and record which fail.

- [ ] **Step 3: Implement**

`accumulator_engine.py::_build_apply_metadata` return becomes explicit:

```python
        return DimensionsMetadata(
            dimensions=dims,
            hit_policy=HitPolicy.COLLECT,  # never inherit a table policy here
        )
```

`__init__.py` — add `HitPolicy` to the constants import block and `HitPolicyViolationError`, `SelectionInfo` from `mountainash_rules.hit_policy`, and extend `__all__`.

- [ ] **Step 4: Run** the three tests + `hatch run test:test-quick` → PASS.

- [ ] **Step 5: Commit and push**

```bash
git add src/mountainash_rules/ tests/
git commit -m "feat: COLLECT pin in accumulator apply metadata; package exports

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin develop
```
