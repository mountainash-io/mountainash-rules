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

**Reserved column namespace:** the engine claims `__rule_index`, `__rank`,
`__specificity`, `__survived`, `__t_*`, and `__ctx_*`. If the input rules
frame already contains any of these, `evaluate()` raises `ValueError`
naming the colliding columns (today's behaviour silently aliases over
them). Documented in the engine docstring.

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

**Exact pipeline order** (replaces the current filter→sort→rank sequence):

1. survival filter
2. policy ordering sort (per table above)
3. `__rank` assignment (1-based over the policy ordering)
4. UNIQUE/ANY assertions — evaluated **here**, over the full survivor set,
   before any truncation can mask violations
5. `min_specificity` filter, then `top_n` truncation
6. policy cardinality (`head(1)` for FIRST/PRIORITY/ANY)

Notes:

- FIRST deliberately ignores specificity — DMN semantics is table order,
  full stop. Users wanting "most specific wins" keep COLLECT/`best_match`.
- COLLECT's ordering is a deliberate mountainash **deviation from DMN**
  (which specifies arbitrary order for Collect): we guarantee
  specificity-then-rule-order determinism. Documented as such wherever DMN
  interchange is discussed.
- **PRIORITY is a local extension, not DMN 1.3 PRIORITY.** DMN priority
  comes from the ordered output-values list, not a numeric rule column.
  Ours is the numeric-salience form common in production engines. Babel may
  only claim DMN-PRIORITY equivalence when `priority_field` was derived
  from DMN output-value order (see §7); otherwise the mapping is lossy and
  must say so.
- `__rank` respects the active policy's ordering, so `best_match` (head(1))
  automatically follows it.
- **Zero survivors is never a violation**: every policy — including UNIQUE
  and ANY — returns an empty result. There is no DMN default-output
  concept in the engine (out of scope; babel notes this on import of
  tables with defaults).
- Empty rule set behaves identically to zero survivors.

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
  The default inference requires metadata — on the expressions-only
  construction path (`self._metadata is None`) there is no authoritative
  list of rule-condition fields, so ANY **requires explicit
  `output_fields`** there and raises `ValueError` otherwise (never guesses).
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

called from `ExpressionRulesEngine._evaluate` per the pipeline order in §4.
`RuleResult` additionally gains a post-hoc selector for re-slicing an
already-evaluated COLLECT result:

```python
RuleResult.select(policy: HitPolicy, priority_field: str | None = None) -> RuleResult
```

For `select()` to be sound, `RuleResult` must carry more than the frame:

- **Selection info**: the engine passes a small `SelectionInfo` value object
  into every `RuleResult` — dimension rule fields (resolved, incl. RANGE
  min/max), `priority_field`, `output_fields`, and a `truncated: bool` flag
  set when `top_n` or `min_specificity` removed rows, plus whether
  observability columns were kept. Without this, ANY's default output
  inference is impossible post-hoc (`active_dimensions` alone cannot recover
  `rule_field`/`range_*_field` remaps).
- **Truncation guard**: `select()` raises `ValueError` when
  `truncated=True` — a truncated frame can silently mask UNIQUE/ANY
  violations and mis-pick FIRST/PRIORITY winners. Re-evaluate without
  `top_n` instead. Similarly `select()` requires `__specificity` and
  `__rule_index` to be present (i.e. not usable after
  `include_observability`-style stripping of internals, though those two
  columns are retained by default).

This is the Stage-2 hook for accumulator flows: `AccumulatorEngine.apply()`
keeps producing COLLECT-style `AccumulatorResult`s; selecting among
combinations is a filter-stage `select(...)` call, honouring
`two-engines-two-stages`. One defensive change **is** made to the
accumulator: `_build_apply_metadata()` explicitly sets
`hit_policy=HitPolicy.COLLECT` on the metadata it constructs, so a
user-set policy on the metadata passed to `AccumulatorEngine` can never
leak into the internal apply path (today it wouldn't — the method builds
fresh metadata — but the explicit pin protects against future refactors).

### 7. Babel mapping (interface only; details in lattice-schema-contract spec)

- Export: `hitPolicy` attribute from `lattice.metadata.hit_policy`, mapped
  `rule_order → "RULE ORDER"`, others uppercased. PRIORITY is exported as
  DMN PRIORITY only when babel can also emit the ordered output-values list
  that DMN derives priority from (i.e. the priority column has enumerable
  values); otherwise export falls back to COLLECT with a warning, because
  our numeric-salience PRIORITY has no faithful DMN encoding (see §4 note).
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
6. `RuleResult.select()` on an untruncated COLLECT result reproduces each
   policy's outcome; `select()` on a `top_n`-truncated result raises
   `ValueError`; ANY via `select()` without metadata and without
   `output_fields` raises.
6a. Zero survivors: UNIQUE and ANY return empty results without raising.
6b. Reserved-column collision: rules frame containing `__rank` →
    `ValueError` at evaluate.
7. Metadata validation: PRIORITY without `priority_field` → `ValueError`;
   YAML round-trip of `hit_policy`/`priority_field`/`output_fields`
   (extends the serialisation spec's round-trip test).
8. `evaluate(hit_policy=...)` overrides metadata's policy.

## Files touched

- `src/mountainash_rules/constants.py` (+HitPolicy), `dimension.py`
  (+3 fields, validator), `engine.py` (`__rule_index`, reserved-column
  check, policy hook, params, `SelectionInfo` construction),
  `hit_policy.py` (new: policy function, `SelectionInfo`,
  `HitPolicyViolationError`), `result.py` (`select`, selection info),
  `accumulator_engine.py` (`_build_apply_metadata` pins COLLECT),
  `__init__.py` (exports).
- tests: `test_hit_policy.py` new; existing rank tests updated for the
  deterministic tie-break (order assertions may tighten, none loosen).

## Out of scope

- DMN COLLECT aggregation operators (sum/min/max/count hit-policy suffixes).
- Per-stage policies in DRG orchestration (P2 exploratory).
- Accumulator-side ranking of any kind.
