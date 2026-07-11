# Hit Policies, Rule Priority, and Deterministic Tie-Breaking

> **Status:** APPROVED FOR PLANNING
> **Date:** 2026-07-12
> **Source:** Architectural review 2026-07-12 §2.1; backlog card
> `mountainash-central/01.principles/mountainash-rules/h.backlog/hit-policies-and-rule-priority.md`
> **Principles:** `two-engines-two-stages` (ENFORCED) — selection is a
> filter-engine concern; the accumulator never ranks. Hit policies are a thin
> named layer over the existing filter pipeline, not new engine machinery.
> **Depends on:** serialisable-dimension-metadata spec (StrEnums; new fields on
> `DimensionsMetadata` must serialise). **Depended on by:** babel
> lattice-schema-contract spec (DMN hitPolicy mapping).

## Problem

The filter engine's only selection semantics is "sort by `__specificity`
descending; ties broken by backend sort stability" — which is not stable
across the 7 supported backends, so `best_match` is non-deterministic on
ties. Production rule systems and DMN interchange need explicit hit policies:
UNIQUE, FIRST, PRIORITY, ANY, COLLECT, RULE ORDER. Babel's `DmnExporter`
hardcodes `hitPolicy="UNIQUE"`, which is wrong for any composed lattice.

## Design

### 1. `HitPolicy` StrEnum (`constants.py`)

```python
class HitPolicy(StrEnum):
    COLLECT = "collect"        # all survivors, specificity order (status quo, default)
    UNIQUE = "unique"          # assert ≤ 1 survivor
    FIRST = "first"            # single survivor, rule order wins
    PRIORITY = "priority"      # single survivor, priority_field wins
    ANY = "any"                # all survivors must agree on outputs; return one
    RULE_ORDER = "rule_order"  # all survivors, rule order
```

### 2. Placement: metadata first, call-site override

`DimensionsMetadata` gains:

```python
hit_policy: HitPolicy = HitPolicy.COLLECT
priority_field: str | None = None
output_fields: list[str] = Field(default_factory=list)  # used by ANY; see §5
```

with a validator: `PRIORITY` requires `priority_field`. Metadata placement is
what babel needs for round-trip (the policy is a property of the *table*, and
`DimensionsMetadata` is the table's schema object). `evaluate()` gains
override parameters for one-off calls:

```python
def evaluate(self, context, ..., hit_policy: HitPolicy | None = None,
             priority_field: str | None = None) -> RuleResult
```

`None` means "use metadata's"; the expressions-only construction path (no
metadata) defaults to COLLECT.

### 3. Deterministic tie-break: `__rule_index`

`_evaluate` Step 1 gains `with_row_index(name="__rule_index")` on the rules
relation **before** any filtering, capturing input row order (the DMN "rule
order"). All orderings below end with `__rule_index` ascending as the final
key, making every policy — including the COLLECT status quo — deterministic
across backends. `__rule_index` is retained in results (it is the join key
babel and observability need); documented alongside `__rank`.

(`with_row_index` is already used for `__rank`, so this adds no new backend
surface; the existing ibis-polars xfails under mountainash#78 apply equally.)

### 4. Policy semantics

Applied after survival filtering, replacing the current single sort:

| Policy | Ordering | Cardinality | Violation |
|---|---|---|---|
| COLLECT | `__specificity` desc, `__rule_index` asc | all | — |
| RULE_ORDER | `__rule_index` asc | all | — |
| FIRST | `__rule_index` asc | `head(1)` | — |
| PRIORITY | `priority_field` desc, `__specificity` desc, `__rule_index` asc | `head(1)` | missing/null priority value in survivors → error |
| UNIQUE | as COLLECT | all | count > 1 → `HitPolicyViolationError` |
| ANY | as COLLECT | `head(1)` | survivors disagree on output fields → `HitPolicyViolationError` |

Notes:

- FIRST deliberately ignores specificity — DMN semantics is table order,
  full stop. Users wanting "most specific wins" keep COLLECT/`best_match`.
- `__rank` remains 1-based over the policy's ordering, so `best_match`
  (head(1)) automatically respects the active policy.
- `top_n`/`min_specificity` compose unchanged (applied after ranking, before
  cardinality truncation for FIRST/PRIORITY/ANY they are moot but legal).

### 5. UNIQUE / ANY violations

New module `src/mountainash_rules/hit_policy.py`:

```python
class HitPolicyViolationError(ValueError):
    def __init__(self, policy: HitPolicy, offending: t.Any, message: str): ...
    # .offending is a DataFrame of the violating survivor rows
```

- **UNIQUE**: raised when survivor count > 1; message lists `rule_name`s (or
  `__rule_index` when no `rule_name` column exists).
- **ANY**: output columns are `metadata.output_fields` when non-empty;
  otherwise every column that is not a dimension rule field (incl. RANGE
  min/max), not `priority_field`, not `rule_name`, and not `__`-prefixed.
  Violation = more than one distinct value-tuple across survivors (computed
  backend-agnostically via `unique().count_rows()` on the output projection).
  On success the first survivor row is returned.

Raising is the only runtime behaviour — no `on_violation="report"` mode
(YAGNI; static conflict detection is babel's conflicts validator's job, and
the error object already carries the offending rows for programmatic
handling).

### 6. Implementation shape

The policy logic lives in `hit_policy.py` as a pure function over a relation:

```python
def apply_hit_policy(rel, policy, *, priority_field, output_fields,
                     dimension_rule_fields) -> rel
```

called from `ExpressionRulesEngine._evaluate` between the survival filter and
the rank column. `RuleResult` additionally gains a post-hoc selector for
re-slicing an already-evaluated COLLECT result:

```python
RuleResult.select(policy: HitPolicy, priority_field: str | None = None) -> RuleResult
```

which re-applies ordering/cardinality/assertions on the stored DataFrame
(possible because `__specificity` and `__rule_index` are retained). This is
the Stage-2 hook for accumulator flows: `AccumulatorEngine.apply()` is
untouched and keeps producing COLLECT-style `AccumulatorResult`s; selecting
among combinations is a filter-stage `select(...)` call, honouring
`two-engines-two-stages`.

### 7. Babel mapping (interface only; details in lattice-schema-contract spec)

- Export: `hitPolicy` attribute from `lattice.metadata.hit_policy`, mapped
  `rule_order → "RULE ORDER"`, others uppercased. PRIORITY additionally
  requires DMN output-value ordering — exported as PRIORITY only when
  `priority_field` is present, else falls back to COLLECT with a warning.
- Import: DMN `hitPolicy` → `HitPolicy`; DMN PRIORITY synthesises
  `priority_field="__dmn_priority"` from output-value order. Unsupported DMN
  policies (OUTPUT ORDER, aggregating COLLECT-with-operator) → explicit
  `UnsupportedFormatFeatureError`, not silent downgrade (fail-closed, per
  babel's validator principle).

### Approaches considered

- **Policy on `evaluate()` only**: rejected — babel round-trip needs it
  serialised with the table; call-site-only placement forces every consumer
  to re-thread it.
- **Separate `select(policy=...)` post-processor only (no engine param)**:
  rejected as the sole mechanism — UNIQUE must be able to fail the evaluation
  itself, and metadata-declared policy should not require a second call to
  take effect. Kept as the *additional* re-slicing hook.
- **Building priority into the accumulator build phase**: rejected outright —
  violates `two-engines-two-stages`.

## Testing (TDD)

`tests/test_hit_policy.py` (new), RED first:

1. Determinism: two equal-specificity survivors → COLLECT order equals input
   rule order on every available backend fixture; repeated runs identical.
2. FIRST returns the earlier row even when a later row is more specific.
3. PRIORITY: higher `priority_field` wins over higher specificity;
   null priority in survivors → error naming the rule.
4. UNIQUE: single survivor passes; two survivors → `HitPolicyViolationError`
   whose `.offending` has 2 rows.
5. ANY: agreeing outputs → single row returned; disagreeing → error.
   `output_fields` override respected.
6. `RuleResult.select()` on a COLLECT result reproduces each policy's outcome.
7. Metadata validation: PRIORITY without `priority_field` → `ValueError`;
   YAML round-trip of `hit_policy`/`priority_field`/`output_fields`
   (extends the serialisation spec's round-trip test).
8. `evaluate(hit_policy=...)` overrides metadata's policy.

## Files touched

- `src/mountainash_rules/constants.py` (+HitPolicy), `dimension.py`
  (+3 fields, validator), `engine.py` (`__rule_index`, policy hook, params),
  `hit_policy.py` (new), `result.py` (`select`), `__init__.py` (exports).
- tests: `test_hit_policy.py` new; existing rank tests updated for the
  deterministic tie-break (order assertions may tighten, none loosen).

## Out of scope

- DMN COLLECT aggregation operators (sum/min/max/count hit-policy suffixes).
- Per-stage policies in DRG orchestration (P2 exploratory).
- Accumulator-side ranking of any kind.
