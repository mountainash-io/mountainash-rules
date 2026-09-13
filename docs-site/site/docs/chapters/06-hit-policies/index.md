---
title: "Chapter 6: Hit Policies"
description: "How hit policies select, validate, order, and limit surviving rules at the end of the ExpressionRulesEngine evaluation pipeline."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 6: Hit Policies

## Summary

This chapter explains hit policies: the selection semantics applied after rules have survived ternary matching and have been ranked by specificity. Hit policies are new since the June 2026 edition; before this chapter's feature was added, `evaluate()` always returned every survivor. You will learn the six `HitPolicy` enum members, how assertions and cardinality change a survivor set, how `SelectionInfo` lets `RuleResult.select()` re-apply a policy, and how schema-level metadata supplies a default policy and output configuration. Chapter 5 describes the matching and specificity stages; Chapter 7 describes the `RuleResult` wrapper that receives the selected frame.

---

<!-- concept:100 -->
## HitPolicy Enum

A **hit policy** answers the question, “What should happen when more than one rule survives?” The `HitPolicy` `StrEnum` in `core/constants.py` defines six choices. Each policy receives the survivor set produced by matching, then determines its ordering, whether it must validate an assertion, and whether the returned frame is reduced to one row.

The policy is the final selection stage of `ExpressionRulesEngine.evaluate()`. In the pipeline described in `CLAUDE.md`, Steps 1–3 bind the context, compute ternary dimension values, and calculate survival and specificity. Step 4, **Select**, filters to survivors, sorts using policy-specific ordering keys, assigns `__rank`, checks assertions, and applies cardinality. Only after this work does the engine collect the frame and wrap it in a `RuleResult`.

The six members and their effects are:

| Member | Survivor-set behavior | Cardinality | Primary ordering |
|---|---|---:|---|
| `HitPolicy.COLLECT` | Keep every survivor | All | Specificity descending, then original row order |
| `HitPolicy.UNIQUE` | Require at most one survivor | All if valid | Specificity descending, then original row order |
| `HitPolicy.FIRST` | Select the first row in the rule table | One | Original row order |
| `HitPolicy.PRIORITY` | Select the highest-priority row | One | `priority_field` descending, then specificity, then row order |
| `HitPolicy.ANY` | Require agreeing outputs, then select one | One | Specificity descending, then original row order |
| `HitPolicy.RULE_ORDER` | Keep every survivor in table order | All | Original row order |

`COLLECT` is the default when no policy is supplied by metadata or by the call. `UNIQUE` and `ANY` are assertion policies: they inspect the complete survivor set before cardinality truncates anything. `FIRST`, `PRIORITY`, and `ANY` are cardinality policies because they return at most one row. `RULE_ORDER` keeps all rows but deliberately ignores specificity when ordering them.

The enum values are stable strings, so code may use either enum members or their serialized values:

```python
from mountainash_rules import HitPolicy

assert HitPolicy.COLLECT.value == "collect"
assert HitPolicy("rule_order") is HitPolicy.RULE_ORDER
```

This separation between matching and selection is useful. A rule can survive because every dimension is either a hard match (`1`) or a wildcard (`0`), while the hit policy decides whether several such rules are acceptable, which one wins, or whether their outputs must agree.

<!-- concept:101 -->
## Collect Policy

`HitPolicy.COLLECT` keeps the complete survivor set. It is the default policy, and it is the policy to choose when the caller needs to inspect all applicable rules rather than collapse them to one answer.

“Collect” does not mean “return the input table unchanged.” Non-survivors have already been filtered out, and the survivors are ranked. The policy's cardinality operation is a no-op: no surviving row is removed solely because of the policy. The deterministic default order is specificity descending, followed by `__rule_index` ascending, so the most specific rule appears first and tied rules retain their original order.

The following example has a specific Australian rule and a wildcard fallback. Both survive for the `AU` context, so the default result contains both rows:

```python
import polars as pl
from mountainash_rules import (
    Dimension,
    DimensionsMetadata,
    ExpressionRulesEngine,
)

rules = pl.DataFrame({
    "rule_name": ["fallback", "au_specific"],
    "region": ["<NA>", "AU"],
    "price": [1.0, 2.0],
})
metadata = DimensionsMetadata(
    dimensions=[Dimension(dimension_name="region")],
)
engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
result = engine.evaluate({"region": "AU"})

assert result.count == 2
assert result.survivors["rule_name"].to_list() == ["au_specific", "fallback"]
```

The first row is more specific (`__specificity == 1`), while the fallback has specificity zero. Use `result.survivors` when all candidates matter; use a different policy when the application requires one winner or an explicit consistency check.

<!-- concept:102 -->
## Unique Policy

`HitPolicy.UNIQUE` expresses an assertion: at most one rule may survive. Zero survivors are valid. One survivor is valid. More than one survivor is an error because the rule table contains an ambiguity for that context.

The engine checks uniqueness before applying any cardinality. Therefore, `UNIQUE` cannot hide ambiguity by taking the first row. When two rows survive, `check_assertions()` raises `HitPolicyViolationError` and includes the complete offending survivor frame on the exception. This makes the failure useful for diagnostics and for reporting the conflicting rules to an operator.

```python
from mountainash_rules import HitPolicy, HitPolicyViolationError

try:
    engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
except HitPolicyViolationError as exc:
    assert exc.policy is HitPolicy.UNIQUE
    offending_rows = exc.offending
    print(f"{len(offending_rows)} rules survived")
```

A context with no matching rule still passes the uniqueness assertion and returns an empty `RuleResult`:

```python
rules_without_au = pl.DataFrame({
    "rule_name": ["nz_only"],
    "region": ["NZ"],
    "price": [3.0],
})
engine_without_au = ExpressionRulesEngine(
    rules=rules_without_au,
    dimension_metadata=metadata,
)
empty = engine_without_au.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
assert empty.count == 0
```

Use `UNIQUE` when ambiguity indicates a data-quality or configuration defect. If multiple matches are expected and useful, `COLLECT` preserves them instead; if multiple rows may agree on the answer, `ANY` provides a different contract.

<!-- concept:103 -->
## First And Priority Policies

`FIRST` and `PRIORITY` both narrow the survivor set to one row, but they answer different questions. `FIRST` asks which surviving rule appeared earliest in the source table. `PRIORITY` asks which surviving rule has the greatest value in a caller-selected priority column.

`FIRST` orders by `__rule_index` ascending. The index is added from the original rules frame before filtering, so the policy is stable even after some rows have been eliminated. It intentionally ignores specificity:

```python
first_result = engine.evaluate(
    {"region": "AU"},
    hit_policy=HitPolicy.FIRST,
)
assert first_result.count == 1
assert first_result.best_match["rule_name"].to_list() == ["fallback"]
```

This result chooses `fallback` because it was row zero, even though `au_specific` is more specific. That behavior is appropriate when table order is the business-defined precedence.

`PRIORITY` requires a `priority_field`. Its ordering keys are, in order:

1. The priority column, descending.
2. `__specificity`, descending, as a tie-breaker.
3. `__rule_index`, ascending, as a deterministic final tie-breaker.

The priority column must be present in the rules frame. A call can provide it directly:

```python
priority_result = engine.evaluate(
    {"region": "AU"},
    hit_policy=HitPolicy.PRIORITY,
    priority_field="salience",
)
```

For example, if `fallback` has `salience=10` and `au_specific` has `salience=1`, `PRIORITY` selects `fallback`, even though specificity would place `au_specific` first under `COLLECT`:

```python
rules_with_priority = pl.DataFrame({
    "rule_name": ["fallback", "au_specific"],
    "region": ["<NA>", "AU"],
    "salience": [10, 1],
    "price": [1.0, 2.0],
})
priority_engine = ExpressionRulesEngine(
    rules=rules_with_priority,
    dimension_metadata=metadata,
)
priority_result = priority_engine.evaluate(
    {"region": "AU"},
    hit_policy=HitPolicy.PRIORITY,
    priority_field="salience",
)
assert priority_result.best_match["rule_name"].to_list() == ["fallback"]
```

Calling `ordering_keys(HitPolicy.PRIORITY, None)` raises `ValueError` because no priority column was supplied. The same requirement is validated when `DimensionsMetadata` is configured with `hit_policy=HitPolicy.PRIORITY`; metadata construction requires `priority_field` in that case.

<!-- concept:104 -->
## Any Policy

`HitPolicy.ANY` permits multiple survivors only when they agree on their outputs. It is useful when several conditions independently produce the same answer: the rule table may contain redundant or differently specific explanations, but the application should reject conflicting results.

Output-schema configuration is validated before any scoring happens, independent of how many rows will ultimately survive. With metadata attached, output columns may be inferred: the inference excludes dimension rule fields, `rule_name`, the configured priority field, and all columns whose names start with `__`. Without metadata — the expressions-only construction path — `output_fields` must be declared explicitly; `check_policy_config()` raises `ValueError` up front if none are present, even for a context where zero or one rule will survive.

Only once configuration passes does the engine count survivors. Zero or one survivor always passes, regardless of output agreement. If more than one survives, the engine compares the configured or inferred output columns by counting distinct rows over them. `check_assertions()` raises `HitPolicyViolationError` when that count is greater than one; with metadata-backed inference, an inferred output set that happens to be empty is likewise only an error once there is more than one survivor to compare against. If the outputs agree, cardinality keeps one representative row.

An explicit output field makes the contract clear:

```python
any_metadata = DimensionsMetadata(
    dimensions=[Dimension(dimension_name="region")],
    output_fields=["price"],
)
any_engine = ExpressionRulesEngine(
    rules=pl.DataFrame({
        "rule_name": ["specific", "fallback"],
        "region": ["AU", "<NA>"],
        "price": [5.0, 5.0],
    }),
    dimension_metadata=any_metadata,
)
any_result = any_engine.evaluate(
    {"region": "AU"},
    hit_policy=HitPolicy.ANY,
)
assert any_result.count == 1
```

If the two rows instead contain different prices, `ANY` raises. The exception's `offending` attribute contains the full pre-truncation survivor frame, so the conflicting rows and their context can be logged:

```python
conflicting_rules = pl.DataFrame({
    "rule_name": ["specific", "fallback"],
    "region": ["AU", "<NA>"],
    "price": [5.0, 7.0],
})
conflicting_engine = ExpressionRulesEngine(
    rules=conflicting_rules,
    dimension_metadata=any_metadata,
)
try:
    conflicting_engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.ANY)
except HitPolicyViolationError as exc:
    assert exc.policy is HitPolicy.ANY
    assert len(exc.offending) == 2
```

An expressions-only engine that never declares `output_fields` never reaches this comparison at all: the configuration check happens before scoring, protecting the assertion from accidentally comparing a condition column or an internal observability column no matter how many rules would have survived.

<!-- concept:105 -->
## Rule Order Policy

`HitPolicy.RULE_ORDER` keeps all survivors but presents them in their original table order. This policy differs from `COLLECT` in only one deliberate way: it does not let specificity reorder the rows. It also differs from `FIRST`: `FIRST` returns only the first survivor, while `RULE_ORDER` returns every survivor.

```python
rule_order_result = engine.evaluate(
    {"region": "AU"},
    hit_policy=HitPolicy.RULE_ORDER,
)
assert rule_order_result.count == 2
assert rule_order_result.survivors["rule_name"].to_list() == [
    "fallback",
    "au_specific",
]
```

This policy is appropriate when the table is already an ordered sequence of candidates and downstream code needs to inspect all applicable rows in that sequence. Internally, both `FIRST` and `RULE_ORDER` use the same ordering key, `[('__rule_index', False)]`; `apply_cardinality()` is what makes `FIRST` one-row and leaves `RULE_ORDER` untruncated.

The distinction is important for audit and migration work. Changing from `COLLECT` to `RULE_ORDER` does not change which rules survive, but it does change the order in which callers see them. If a caller takes the first row from the result, that change can alter behavior, so make the policy explicit rather than relying on incidental DataFrame order.

<!-- concept:106 -->
## SelectionInfo Dataclass

`SelectionInfo` is the small record that a `RuleResult` carries so it can re-apply a hit policy after evaluation. It preserves the schema facts needed by ordering and assertion logic instead of forcing `RuleResult` to reconstruct them from the result frame.

The dataclass has six fields:

| Field | Type | Purpose |
|---|---|---|
| `dimension_rule_fields` | `tuple[str, ...]` | Identifies condition columns to exclude from inferred outputs; range dimensions contribute both bound fields |
| `priority_field` | `str \| None` | Supplies the priority column for `PRIORITY` and excludes it from inferred outputs |
| `output_fields` | `tuple[str, ...]` | Explicit output columns for `ANY` agreement checks |
| `truncated` | `bool` | Records whether the survivor set is possibly incomplete for re-selection |
| `observability` | `bool` | Records whether per-dimension ternary columns were retained |
| `metadata_backed` | `bool` | Records whether the engine was built from `DimensionsMetadata`; `ANY` may only infer output columns when this is `True` |

The helper `selection_info_from_metadata(metadata, priority_field, observability)` collects these values. For each ordinary dimension it records `resolved_rule_field`; for a `RANGE` dimension it records `range_min_field` and `range_max_field`. A per-call `priority_field` takes precedence over `metadata.priority_field`. The helper initially sets `truncated=False`; `evaluate()` replaces that value with the actual truncation state before constructing `RuleResult`.

The `RuleResult.select()` signature is:

```python
def select(
    self,
    policy: HitPolicy | str,
    priority_field: str | None = None,
) -> RuleResult:
```

`policy` accepts either a `HitPolicy` member or its serialized string value; both are normalized through the same validation `evaluate()` uses, and an unrecognized string raises `ValueError` rather than silently falling back to `COLLECT`.

This enables a collect-first workflow. Evaluate once without truncating, then choose different policies for different consumers:

```python
collected = engine.evaluate({"region": "AU"})

first = collected.select(HitPolicy.FIRST)
ordered = collected.select(HitPolicy.RULE_ORDER)
priority = collected.select(HitPolicy.PRIORITY, priority_field="salience")
```

`select()` re-sorts the stored survivors, drops the old `__rank`, assigns a new one-based rank, checks assertions, and applies cardinality. It refuses to operate when selection information is absent or when `truncated` is true.

`truncated` is set conservatively, not by comparing row counts before and after. A result is marked truncated whenever `top_n` or `min_specificity` was requested — even if the limit happened to remove no rows — and whenever the applied policy is `FIRST`, `PRIORITY`, or `ANY`, even if only a single rule survived. `select()` cannot distinguish a genuinely narrowed survivor set from one that merely passed through a no-op limit or a singleton cardinality policy, so it treats both as incomplete evidence and refuses a second selection. Re-evaluate without truncating filters, using `COLLECT`, when a post-hoc policy needs complete evidence.

<!-- concept:107 -->
## HitPolicyViolationError

`HitPolicyViolationError` is a `ValueError` subclass for policy assertions that fail over a complete survivor set. `check_assertions(rel, policy, info)` raises it for two cases:

- `UNIQUE`: more than one row survives.
- `ANY`: more than one row survives and the selected output columns contain more than one distinct output.

The constructor has the exact shape `HitPolicyViolationError(policy: HitPolicy, offending: t.Any, message: str)`. The exception stores all three useful pieces of information: the inherited message for human-readable logs, `policy` for machine-readable branching, and `offending` for the materialized rows that violated the policy.

For `UNIQUE`, `offending` is the complete survivor frame whenever the row count exceeds one. For `ANY`, it is also the complete survivor frame, not only the rows that differ in one output column. This preserves enough context to diagnose overlapping conditions, compare specificity, and decide which rule definitions should be changed.

```python
from mountainash_rules import HitPolicy, HitPolicyViolationError

try:
    engine.evaluate({"region": "AU"}, hit_policy=HitPolicy.UNIQUE)
except HitPolicyViolationError as exc:
    print(exc)                       # hit_policy=unique but 2 rules survived
    print(exc.policy.value)          # "unique"
    print(exc.offending.columns)     # original and computed survivor columns
```

Zero survivors always pass both assertions. A missing output definition for `ANY` is a configuration error and raises `ValueError` with guidance to provide `output_fields`; it is not a disagreement between outputs and therefore is not a `HitPolicyViolationError`.

Assertions run before cardinality. That ordering is a safety property: a policy cannot make an invalid rule table appear valid by truncating it first.

<!-- concept:108 -->
## Cardinality Application

**Cardinality** is the number of rows allowed in the final selected result. The `apply_cardinality(rel, policy)` function implements the final row-count rule over the already ordered and validated relation. Its behavior is intentionally small:

```python
def apply_cardinality(rel: t.Any, policy: HitPolicy) -> t.Any:
    if policy in (HitPolicy.FIRST, HitPolicy.PRIORITY, HitPolicy.ANY):
        return rel.head(1)
    return rel
```

`FIRST`, `PRIORITY`, and `ANY` call `.head(1)`, so they return one row when a survivor exists and an empty frame when none exists. `COLLECT`, `UNIQUE`, and `RULE_ORDER` return the relation without a cardinality reduction. `UNIQUE` is already known to contain zero or one row because `check_assertions()` ran first; `COLLECT` and `RULE_ORDER` intentionally preserve all rows.

The full selection order is therefore:

1. Filter out rows where `__survived` is false.
2. Sort using `ordering_keys()` for the selected policy.
3. Assign one-based `__rank`.
4. Check `UNIQUE` or `ANY` assertions over the full survivor relation.
5. Apply optional `min_specificity` and `top_n` filters.
6. Apply policy cardinality with `.head(1)` where required.
7. Drop temporary context and survival columns, then collect and wrap the frame.

Because assertions precede cardinality, `ANY` validates all agreeing survivors even though only one representative row is returned. The returned row is the first row after policy ordering; the policy does not merge output values into a new synthetic row.

<!-- concept:97 -->
## Table-Level Hit Policy Fields

Hit-policy configuration belongs naturally beside dimension definitions because the policy describes the schema's selection contract. `DimensionsMetadata` carries three table-level fields:

```python
class DimensionsMetadata(BaseModel):
    dimensions: list[Dimension]
    hit_policy: HitPolicy = HitPolicy.COLLECT
    priority_field: str | None = None
    output_fields: list[str] = Field(default_factory=list)
```

`hit_policy` sets the default for evaluations made by an engine constructed with this metadata. `priority_field` names the rules column used when the default or an evaluation override is `PRIORITY`. `output_fields` identifies the payload columns that `ANY` must compare. When `hit_policy=HitPolicy.PRIORITY`, metadata validation raises `ValueError` unless `priority_field` is supplied.

Configure the policy once on the schema when every call should follow the same contract:

```python
metadata = DimensionsMetadata(
    dimensions=[Dimension(dimension_name="region")],
    hit_policy=HitPolicy.PRIORITY,
    priority_field="salience",
    output_fields=["price"],
)
engine = ExpressionRulesEngine(rules=rules_with_priority, dimension_metadata=metadata)

# Uses PRIORITY and salience from metadata.
result = engine.evaluate({"region": "AU"})
```

A per-call `evaluate()` value takes precedence over the metadata policy. Its signature includes both selection parameters:

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
```

When `hit_policy` is `None`, the engine uses `self._metadata.hit_policy` if metadata exists; the expressions-only construction path defaults to `HitPolicy.COLLECT`. Supplying `hit_policy=HitPolicy.COLLECT` explicitly overrides a metadata default such as `FIRST`:

```python
metadata_first = DimensionsMetadata(
    dimensions=[Dimension(dimension_name="region")],
    hit_policy=HitPolicy.FIRST,
)
engine_first = ExpressionRulesEngine(
    rules=rules,
    dimension_metadata=metadata_first,
)

metadata_choice = engine_first.evaluate({"region": "AU"})
explicit_override = engine_first.evaluate(
    {"region": "AU"},
    hit_policy=HitPolicy.COLLECT,
)
assert metadata_choice.count == 1
assert explicit_override.count == 2
```

The same precedence applies to `priority_field`: a non-`None` per-call value overrides `metadata.priority_field`. This lets one schema define a normal default while a particular evaluation chooses a different priority column. The output-field list remains metadata-backed selection information for `ANY`; using explicit metadata keeps that comparison stable across calls.

## Key Takeaways

- **`HitPolicy`** defines six survivor-selection contracts: `COLLECT`, `UNIQUE`, `FIRST`, `PRIORITY`, `ANY`, and `RULE_ORDER`.
- **`COLLECT`** is the default and keeps every survivor in specificity order; **`RULE_ORDER`** keeps every survivor in original table order.
- **`UNIQUE`** raises on more than one survivor, while zero survivors pass the assertion.
- **`FIRST`** chooses the earliest surviving source row; **`PRIORITY`** chooses the greatest `priority_field`, then uses specificity and row order as tie-breakers.
- **`ANY`** checks that surviving rows agree on configured or inferred output fields before returning one representative row.
- **`SelectionInfo`** preserves dimension fields, priority and output configuration, truncation state, and observability state so `RuleResult.select()` can safely re-apply a policy.
- **`HitPolicyViolationError`** is a catchable `ValueError` carrying the violated policy and complete offending survivor rows.
- **Cardinality** is applied after assertions: `FIRST`, `PRIORITY`, and `ANY` use `.head(1)`; the other policies preserve their full validated relation.
- **`DimensionsMetadata`** configures `hit_policy`, `priority_field`, and `output_fields` once at the schema level, while per-call `evaluate()` arguments override metadata defaults.
- Hit-policy selection is Step 4 of evaluation, between Chapter 5's specificity ranking and Chapter 7's `RuleResult` accessors.
