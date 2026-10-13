# User Quickstart: mountainash-rules

This guide covers the two evaluation paths in `mountainash_rules`:

- **ExpressionRulesEngine** — direct context evaluation against a rules DataFrame.
- **AccumulatorEngine** — build validated exact cells, then resolve named contract/profile requests.

---

## Installation

```bash
git clone https://github.com/mountainash-io/mountainash-utils-rules.git
cd mountainash-utils-rules
hatch env create
```

Resolve the configured `mountainash` dependency at delivered merge `b9c0ab4ab6380712c89f27c8d083f658278d4593` or a descendant, together with `mountainash-data` and `mountainash-settings` (see `hatch.toml`). This is a delivered dependency revision requirement, not an implied release version or checkout layout.

---

## Part 1: ExpressionRulesEngine

### Concepts

Rules live in a DataFrame. Each row is a rule; each column is either a rule name, a dimension column, or metadata. The engine evaluates a **context** (a dict or Pydantic model) against every rule in one vectorised pass and returns surviving rules ranked by **specificity** — how many dimensions the rule constrains explicitly.

Wildcards are explicitly declared per dimension with `wildcard_values`, or mapped
from Boolean rule-state columns. There are no implicit sentinel values:
`"<NA>"`, `"<NOT_SET>"`, `-999999999` and `-999999998` are concrete values unless
that dimension declares them as markers. Marker comparison preserves the original
type and occurs before numeric conversion.
A wildcard dimension scores 0 (unknown) in ternary logic. The rule still survives, but ranks lower than a rule that explicitly matched that dimension.

---

### Step 1 — Define your rules DataFrame

```python
import polars as pl

rules = pl.DataFrame({
    "rule_name":  ["premium_au",  "standard_au", "global_fallback"],
    "region":     ["AU",          "AU",           "<NA>"],
    "tier":       ["premium",     "<NA>",         "<NA>"],
    "spend_min":  [1000,          0,              -999999999],
    "spend_max":  [9999,          9999,           -999999999],
})
```

This example declares `"<NA>"` and `-999999999` as markers below. Without those declarations, they remain ordinary rule values.

---

### Step 2 — Declare dimensions

```python
from mountainash_rules import Dimension, DimensionsMetadata, MatchStrategy

metadata = DimensionsMetadata(dimensions=[
    Dimension(
        dimension_name="region",
        match_strategy=MatchStrategy.EXACT,
        data_type=str,
        wildcard_values=["<NA>"],
    ),
    Dimension(
        dimension_name="tier",
        match_strategy=MatchStrategy.EXACT,
        data_type=str,
        wildcard_values=["<NA>"],
    ),
    Dimension(
        dimension_name="spend",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
        wildcard_values=[-999999999],
        range_min_field="spend_min",
        range_max_field="spend_max",
    ),
])
```

`dimension_name` is the field name on the context object. For RANGE, `range_min_field` and `range_max_field` are the column names in the rules DataFrame.

---

### Step 3 — Build the engine

```python
from mountainash_rules import ExpressionRulesEngine

engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)
```

Expressions are compiled once here. Build the engine at startup and reuse it for every evaluation.

Provide exactly one nonempty definition: `dimension_metadata` or `dimension_expressions`. Passing both is invalid even when the expressions dict is empty. Zero dimensions are rejected by the filter constructor, not by shared `DimensionsMetadata`; zero rules with a valid typed schema are supported.

---

### Step 4 — Evaluate a context

```python
from pydantic import BaseModel

class CustomerContext(BaseModel):
    region: str
    tier: str
    spend: int

context = CustomerContext(region="AU", tier="premium", spend=1500)
result = engine.evaluate(context)
```

`evaluate()` also accepts a plain dict:

```python
result = engine.evaluate({"region": "AU", "tier": "premium", "spend": 1500})
```

---

### Step 5 — Read results

`evaluate()` returns a `RuleResult`. All accessors return a DataFrame in the same backend as your input `rules`.

#### `survivors` — retained matching rules in policy order

```python
print(result.survivors)
# shape: (3, ...)
# rule_name        region  tier       spend_min  spend_max  __specificity  __rank  __t_region  __t_tier  __t_spend
# premium_au       AU      premium    1000       9999       3              1       1           1         1
# standard_au      AU      <NA>       0          9999       2              2       1           0         1
# global_fallback  <NA>    <NA>       -999999999 -999999999 0              3       0           0         0
```

The `__specificity` column counts dimensions that produced a hard match (ternary 1). The default COLLECT policy sorts by specificity descending; other policies can use source order or salience. `__rank` is 1-based and assigned before caller filters. Filtering does not rewrite it: rank 2 can be the first retained row.

The `__t_<dimension>` columns show per-dimension ternary results: **1** = match, **0** = wildcard/unknown, **-1** = non-match. They are included by default (`include_observability=True`).

#### `best_match` — the retained survivor with minimum policy rank

```python
print(result.best_match)
# Returns the top-1 row: premium_au
```

#### `count` — how many rules survived

```python
print(result.count)  # 3
```

#### `explain(rule_name)` — per-dimension breakdown for one rule

```python
print(result.explain("premium_au"))
# {"region": 1, "tier": 1, "spend": 1}

print(result.explain("standard_au"))
# {"region": 1, "tier": 0, "spend": 1}

print(result.explain("global_fallback"))
# {"region": 0, "tier": 0, "spend": 0}
```

Returns `{dimension_name: ternary_value}` where 1 = match, 0 = wildcard, -1 = non-match.

#### `at_least(n)` — only rules that explicitly matched at least n dimensions

```python
print(result.at_least(2))
# Returns premium_au (specificity 3) and standard_au (specificity 2)
# global_fallback (specificity 0) is excluded
```

---

### Filtering to top results

```python
# Return only the top 1 survivor
result = engine.evaluate(context, top_n=1)

# Return only rules that matched at least 2 dimensions
result = engine.evaluate(context, min_specificity=2)

# Evaluate only a subset of dimensions
result = engine.evaluate(context, dimensions=["region", "spend"])
```

Top-N selects positions among the rows remaining after `min_specificity`, not rows whose original rank is <= N. The same rule applies to batch `top_n_per_context`. Policy assertions run first, so a conflicting UNIQUE/ANY request still raises even with top-N zero or a threshold that would remove every match.

Limits and `min_specificity` must be nonnegative Python integers or `None`; Booleans, floats, strings, and negative values raise `ValueError`. A threshold greater than the active dimension count is valid.

Across `evaluate`, `evaluate_batch`, and `explain`, `dimensions=None` selects all dimensions in configured order. Explicit selections must be nonempty lists of distinct configured names. Empty lists, duplicate names, and malformed selections raise `ValueError`; a well-formed unknown name raises `KeyError`. Projection order is retained in `active_dimensions` and ternary presentation without adding a ranking tiebreak.

### Policies, output schemas and re-selection

Accepts `HitPolicy` members and exact lowercase strings (`collect`, `unique`, `first`, `priority`, `any`, `rule_order`). Evaluation `None` inherits metadata/default COLLECT; `select(None)` and invalid spellings/types raise `ValueError`. PRIORITY requires an existing priority field and rejects null priorities on survivors before limits; null priorities on nonmatching rules do not invalidate the request.

For metadata-backed engines, configure `DimensionsMetadata.output_fields`. A nonempty declaration must contain unique, nonempty names present in the original rule schema, even when rules are empty or ANY is not the default policy. An empty metadata list infers outputs, excluding all physical condition columns (including inactive dimensions and both RANGE bounds), effective priority, rule name and internal columns. CONTEXT_REGEX has no physical condition column. Explicit declarations can intentionally include otherwise excluded rule fields.

The expressions-only constructor has no metadata authority to infer outputs. Supply the keyword-only `output_fields` argument when constructing it to use ANY:

```python
engine = ExpressionRulesEngine(
    rules,
    dimension_expressions=expressions,
    output_fields=["price"],
)
```

Do not supply that argument together with metadata; configure metadata's field instead. Empty explicit constructor lists are invalid. There is no per-call output-schema override. Without an explicit expressions-only schema, ANY raises even on empty input. With metadata but no inferred outputs, zero/one survivor per context is accepted; multiple survivors require a nonempty output schema.

ANY compares complete output tuples: `(None, "yes")` agrees with itself but differs from `(None, "no")` or `(10, "yes")`. Single violations expose full survivor rows through `HitPolicyViolationError.offending`; batch violations expose every offending `__context_id` and `__n` (survivor count for UNIQUE, distinct tuple count for ANY). Chunked violations aggregate all offending chunks. Messages contain bounded examples, not the full diagnostic payload.

Re-selection requires a complete candidate basis:

- Unfiltered COLLECT, RULE_ORDER and successful UNIQUE results can call `select()`.
- Any supplied top-N/min-specificity, including a no-op limit or `min_specificity=0`, prevents later `select()`.
- FIRST, PRIORITY and ANY also prevent later `select()`, even on empty/singleton results.
- `select()` itself preserves this restriction across chains; batch `for_context()` inherits it, including after chunking. Re-evaluate to recover candidates rather than selecting from an incomplete result.

This is the conservative meaning of exported `SelectionInfo.truncated`: possibly incomplete, not necessarily fewer rows. `SelectionInfo.metadata_backed` defaults to False; manual callers relying on inference must opt in with complete condition metadata. Engine-created results provide it automatically. Priority overrides are retained immutably and update inferred output exclusions; explicit output declarations remain unchanged.

`include_observability=False` removes ternaries only; rank, specificity and source identity remain available for sound selection. Batch best-match accessors use minimum remaining rank and `for_context()` returns rank-ordered rows. Filter batch output is ordered by canonical context ID then rank, which need not be caller input order.

### Batch identity and empty inputs

```python
contexts = pl.DataFrame({"cid": ["B", "A"], "region": ["AU", "NZ"]})
batch = engine.evaluate_batch(contexts, context_id_field="cid", chunk_size=1)
batch.survivors["__context_id"]  # correlation values from cid, sorted with rank
batch.context_id_field         # "cid": source mapping, not an echoed column
batch.unmatched_context_ids(contexts)
```

Supplied IDs must be non-null and globally unique in an existing input column. Validation precedes backend conversion, partition routing and chunking, so duplicates across partitions or chunks are still invalid. IDs are not coerced to strings. Result rows use canonical `__context_id`; a rules-side column named `cid` remains ordinary rule data, not an echo from contexts.

With `context_id_field=None`, IDs are assigned once as original zero-based row positions. Unmatched rows leave gaps; partitioning and chunking do not restart or renumber IDs. Unordered database inputs have no portable replay order—provide explicit IDs for persistent correlation.

`matched_context_ids` and `counts_per_context` describe retained rows, not all pre-filter candidates. Top-N zero can make every submitted ID unmatched by these accessors. `unmatched_context_ids(original_contexts)` returns absent IDs sorted; it validates the recorded custom source field rather than guessing positions if that field is missing. For generated IDs, preserve original row order and count. The result does not store the full request universe, so `for_context()` returns the same typed empty view for unmatched and never-submitted IDs, with unchanged re-selection restrictions. The original unmodified input works for unmatched access after partitioned evaluation as well.


`chunk_size` must be `None` or a positive Python integer, excluding Boolean values. Typed empty contexts and typed empty rules produce the normal typed empty result, with requested observability, identity metadata, and conservative completeness state. Empty chunked input follows normal evaluation once; it does not fabricate a row or untyped schema. Empty input still validates policy configuration and reserved fields.

Chunking bounds the cross-product work per chunk, not total memory: prepared input, result frames, and grouped violation diagnostics are still accumulated. All offending UNIQUE/ANY contexts are reported before an exception is raised; no partial successful result is returned.

Boolean batch admission validates every submitted Boolean field before policy evaluation, chunk work, or accumulator index routing. An invalid concrete Boolean anywhere in the batch raises `ValueError` naming its field and active policy; it is not hidden by an earlier UNIQUE/ANY violation, no routing partition, empty rules, or a zero-result limit.

---

### Excluding observability columns

If you don't need the `__t_*` columns in the output:

```python
result = engine.evaluate(context, include_observability=False)
# survivors will not contain __t_region, __t_tier, __t_spend
```

---

### Context with missing fields

Metadata-backed evaluation keeps availability separate from the typed comparison value. An absent field, null or configured context marker is unavailable; it is not replaced by a NOT_SET sentinel. Original omission and supplied-null provenance remain distinct. Single, batch and chunked evaluation share these semantics. In a relation, a present column is supplied for every row, including its null cells.

Boolean dimensions default to `BooleanCoercion.NONE` (`0`), admitting only semantic `True`/`False` plus absence. The standalone filter supports these additional input domains:

| Flag | Value | Additional accepted values |
|------|-------|----------------------------|
| `BINARY_NUMBERS` | `1` | Finite numeric `0` and `1` only |
| `BOOLEAN_TEXT` | `2` | ASCII-whitespace-trimmed, ASCII-case `true`/`false`, and text `1`/`0` |
| `NUMERIC_TRUTHINESS` | `4` | Any finite number (`0` false; all other values true) |

Pass actual `BooleanCoercion` members to the filter, combining flags with `|`; non-enum settings and unknown bits raise `ValueError`. The accumulator accepts only the actual `BooleanCoercion.NONE` member and rejects all other policies at construction:

```python
from mountainash_rules import (
    AccumulatorEngine, BooleanCoercion, ExpressionRulesEngine,
)

boolean_inputs = (
    BooleanCoercion.BOOLEAN_TEXT | BooleanCoercion.NUMERIC_TRUTHINESS
)
filter_engine = ExpressionRulesEngine(
    rules=rules, dimension_metadata=metadata, boolean_coercion=boolean_inputs,
)
accumulator_engine = AccumulatorEngine(
    dimension_metadata=metadata, boolean_coercion=BooleanCoercion.NONE,
    limits=limits,  # application-owned ExactLimits
)
```

Classification never chains through another domain: numeric `2` is true with `NUMERIC_TRUTHINESS`, but text `"2"` is invalid even when text and truthiness are combined. Missing/`None` is absence; concrete numeric `0` requires a numeric flag, and empty text is invalid under every policy. Any other invalid concrete value raises `ValueError` naming the context field and policy. Backend capability errors remain explicit rather than falling back to another backend.

Semantic scalars include Python `bool` and NumPy `bool_`. Numeric flags admit Python/NumPy integers and floats, not Decimal, complex, bytes, or custom truthiness objects. Recognized backend nulls remain absent; concrete NaN/infinity is invalid. Rules sees values after Pydantic `model_dump` or dataframe ingestion and cannot recover types already changed by the caller.

Ordinary comparisons and string predicates treat unavailable context as ternary 0: the rule may survive, but that dimension earns no specificity. A former sentinel spelling is concrete when it is not declared as a marker, so it can match an ordinary string predicate.

Strict strategies differ: `CONTEXT_REGEX` rejects unavailable context with -1, even when its pattern would match `<NOT_SET>` or empty text. A concrete empty string is tested against the pattern normally. `EXACT_KEY` accepts only authored rule-side wildcards when context is unavailable; concrete partition keys do not match missing input. Unknown in another dimension never rescues a known non-match.

The expressions-only constructor has no datatype metadata. It binds missing inputs as null under the existing `__ctx_<name>` column names; custom expressions own their interpretation.

```python
# Context missing 'tier' — this ordinary EXACT dimension cannot eliminate rules
result = engine.evaluate({"region": "AU", "spend": 1500})
# tier dimension scores 0 for all rules; premium_au and standard_au still survive
# because their tier wildcard is also 0 — no rule is eliminated on this dimension
```

---

## Match Strategies

### Numeric strategies

```python
# RANGE: context value within [min, max] (inclusive by default)
Dimension(
    dimension_name="age",
    match_strategy=MatchStrategy.RANGE,
    data_type=int,
    range_min_field="age_min",
    range_max_field="age_max",
)

# GREATER_THAN: context value > rule threshold
Dimension(
    dimension_name="score",
    match_strategy=MatchStrategy.GREATER_THAN,
    data_type=int,
)

# LESS_THAN: context value < rule threshold
Dimension(
    dimension_name="risk",
    match_strategy=MatchStrategy.LESS_THAN,
    data_type=int,
)
```

#### Replacement authoring metadata

Fractional ordered declarations use `data_type="decimal"`, a Mountainash
`DecimalDtype(precision, scale)` and explicit `rounding="half_to_even"` or
`"half_away_from_zero"`. Ordered precision is 1–16 digits; scale is 0–precision.
FLOAT RANGE/GREATER_THAN/LESS_THAN declarations are no longer accepted.
The replacement also rejects FLOAT intervals and ordered field comparisons
(`lt`, `le`, `gt`, `ge`) in both source domains and provider domains, including
inside inactive Boolean branches. FLOAT categorical exact/set matching and
domain `eq`/`ne` comparisons remain supported. Integer and temporal ordering
remain supported.

Metadata JSON/YAML preserves `numeric_dtype` as the Mountainash numeric descriptor
(`kind`, `precision`, `scale`). A dimension's `wildcard_values` retains original
scalar types: no declarations (`[]`) differs from an explicit null marker
(`[None]`), and decimal markers are not rounded during serialization. Optional
rule mappings are `rule_wildcard_field`, `range_min_unbounded_field` and
`range_max_unbounded_field`.

Mapped Boolean flags are authoritative: `True` makes the associated payload
unused, including an otherwise invalid payload. Flags must be nonnull Booleans;
`False` combined with a declared marker is inconsistent authoring. RANGE endpoints
have independent unbounded flags. With no flags, declared markers determine state.

The filter converts admitted Decimal values once before comparing them. Native
integers, finite Decimal values representable with at most 38 source digits, and
plain ASCII decimal text are accepted; floating-point values, exponent notation,
whitespace and overflow are rejected.

```python
from decimal import Decimal
from mountainash import DecimalDtype

decimal_rules = pl.DataFrame({
    "rule": ["singleton", "upper"],
    "lo": ["0.004", "0.010"],
    "hi": ["0.004", "0.020"],
})
decimal_metadata = DimensionsMetadata(dimensions=[Dimension(
    dimension_name="amount", context_field="quoted",
    match_strategy="range", data_type="decimal",
    numeric_dtype=DecimalDtype(10, 3), rounding="half_to_even",
    range_min_field="lo", range_max_field="hi",
)])
decimal_engine = ExpressionRulesEngine(decimal_rules, decimal_metadata)
decimal_engine.evaluate({"quoted": Decimal("0.00449")})  # singleton
decimal_engine.evaluate({"quoted": Decimal("0.00451")})  # no survivor
decimal_engine.evaluate({"quoted": Decimal("0.01950")})  # upper
```

Replacement metadata also carries `uuid_field`, `partition_field`,
`outcome_scope_field`/`outcome_scopes`, default-scope `aggregates`, source `domain`,
`context_domains`, predicate-1 `domain_nodes`, retained `context_contracts` and
optional `policy`. Declared scope names and resolved output names are dot-free
for the `<scope>.<output>` wide view. The new aggregate model remains internal
until the coordinated public cutover; existing public `Aggregate` is still the
old accumulator model.

For DECIMAL SUM/MIN/MAX, final `output_numeric_dtype`/`output_rounding` conversion
is optional. Omitting it retains the native aggregate dtype and value; supplying
it converts the final result once. PRODUCT requires an explicit final output
dtype and rounding policy.

The shared filter Decimal/state paths have executable Polars and Ibis-DuckDB
coverage. The replacement accumulator remains internal; its public package-root
cutover is separate. Per-row REGEX on the filter uses public expression-valued
`str.regex_contains`, qualified on Polars and Ibis-DuckDB against Mountainash
commit `4a4991253fb629a2b9ede26f6f1cef12755109ef` (including the #364 repair).
There is no Rules-native backend escape.

Internal replacement snapshots support ordinary unchecked publication and
save/load of stored geometry, contributions and outputs. Saving requires a new
directory; it does not replace an existing version. Native table attachment
captures independent relations rather than rebuilding or refolding sources.
Validation remains a separate, explicitly invoked operation. Attachment accepts
optional existing evidence; omitted report contents remain unavailable historical
references, not passing reports or a reason to reject ordinary attachment.
Geometry tables use resolved physical context fields, including aliases; retained
source-state tables use logical dimension names. Both full-source and
contribution-only updates return a new artifact and leave the parent unchanged.

Source checks report a missing configured identity column once, even on an empty
source. Checks requiring that identity return `not_run`, not `passed`; independent
checks still run. Findings do not invent subject identities. A missing configured
scope also prevents scoped contribution checks. Strict publication requires
complete reports and therefore rejects `not_run`; unchecked publication and
save/load preserve the factual report.

Internal direct and stored resolution share original-value admission, explicit
profile selection and observation masks. Stored resolution reads retained outputs
rather than refolding sources. Requested cell identities retain the original
source-domain region, not its intersection with a provider query: two disconnected
components can agree on outputs and contributors while cell identity is uncertain.
The correlated-domain connected-component case recorded in the replacement backlog
remains deferred; the replacement is not ready for public cutover.

For incomplete direct requests, resolution branches on feasible source applicability
under the selected provider and source domains. It retains no-match alternatives
and avoids expanding independent wildcard-only dimensions. Cell provenance is a
separate intersection against the full cells for the feasible contributor sets;
one contributor-set witness does not prove a unique connected cell.
Construction also removes regions infeasible under the joint source domain:
with `D: x < y`, a rule restricted to `(x=2, y=1)` contributes no stored cell
or output, while `(x=0, y=1)` remains eligible.

Accumulator request admission preserves original scalar types: for example, text
`"17"` is not an INT and `True` is not an INT. This does not change the standalone
filter's existing non-Boolean, non-Decimal coercion behavior.
INT concrete values must fit the signed 64-bit universe, including when supplied
in an unsigned native column. FLOAT infinities are invalid. Native floating-point
NaN is unavailable context for INT/FLOAT fields, but remains invalid for DECIMAL;
it is not a concrete value that a mask can make valid.
DATETIME admission distinguishes naive and aware values before matching: a concrete
awareness mismatch is an invalid request, not an implicit timezone assumption.
Aware scalar/range values normalize to UTC without stripping timezone information.
UTC source-overlap witnesses now pass on both targets with the Mountainash #369
timestamp precision repair at the qualified commit above. Automatic per-element
timezone conversion for non-UTC aware DATETIME rule sets is deferred; supply
UTC-normalized aware datetime lists instead. This niche requirement no longer
gates Plan 1. Ordinary set matching and scalar/range datetime support remain in
scope. No backend escape or timezone-stripping conversion is provided.


### String strategies

```python
# PREFIX: context value starts with rule value
Dimension(
    dimension_name="product_code",
    match_strategy=MatchStrategy.PREFIX,
    data_type=str,
)

# SUFFIX: context value ends with rule value
Dimension(
    dimension_name="email",
    match_strategy=MatchStrategy.SUFFIX,
    data_type=str,
)

# CONTAINS: rule value appears anywhere in context value
Dimension(
    dimension_name="description",
    match_strategy=MatchStrategy.CONTAINS,
    data_type=str,
)

# CONTEXT_REGEX: context value matches a fixed pattern defined on the dimension
# The pattern is metadata-level — all rules share the same pattern.
Dimension(
    dimension_name="sku",
    match_strategy=MatchStrategy.CONTEXT_REGEX,
    data_type=str,
    regex_pattern=r"^[A-Z]{3}-\d{4}$",
)
```

`CONTEXT_REGEX` is a global context validator, not a per-rule pattern. `REGEX` instead reads each rule's pattern column and currently uses the documented Polars-native fallback.

### NOT_EQUAL

```python
# Context value must differ from rule value
Dimension(
    dimension_name="status",
    match_strategy=MatchStrategy.NOT_EQUAL,
    data_type=str,
)
```

A rule with `status = "blocked"` will match any context where `status != "blocked"`.

### SET_MEMBERSHIP and SET_EXCLUSION

Rules can declare a list column:

```python
rules = pl.DataFrame({
    "rule_name":     ["allowed_regions", "blocked_countries"],
    "region_list":   [["AU", "NZ", "SG"], ["US", "CN"]],
})

# SET_MEMBERSHIP: context value is in the rule's list
Dimension(
    dimension_name="region",
    match_strategy=MatchStrategy.SET_MEMBERSHIP,
    data_type=str,
    rule_field="region_list",
)

# SET_EXCLUSION: context value is NOT in the rule's list
Dimension(
    dimension_name="country",
    match_strategy=MatchStrategy.SET_EXCLUSION,
    data_type=str,
    rule_field="blocked_countries",
)
```

An empty list is concrete: membership matches nothing and exclusion excludes
nothing. A singleton declared marker represents a wildcard; a mixed marker/value
list is invalid unless a mapped wildcard flag makes the payload unused.

> **Backend note:** SET_MEMBERSHIP and SET_EXCLUSION use mountainash's native list membership expressions with separate availability state. Use Polars or Ibis-DuckDB for engine evaluation. Pandas/Narwhals reject column-valued needles, SQLite has no list column type, and Ibis-Polars cannot execute engine row indexing. See [backend support](../README.md#backend-support).

---

## Column name remapping

By default, `dimension_name` is used as both the context field name and the rule column name. Use `context_field` and `rule_field` to remap:

```python
# Context has 'customer_region'; rules have 'geo'
Dimension(
    dimension_name="region",
    context_field="customer_region",
    rule_field="geo",
    match_strategy=MatchStrategy.EXACT,
    data_type=str,
)
```

---

## Backend selection

Pass any supported DataFrame as `rules`. The result returns in the same backend.

```python
# Polars (recommended)
engine = ExpressionRulesEngine(rules=pl.DataFrame(...), dimension_metadata=metadata)

# Pandas via Narwhals
import narwhals as nw
import pandas as pd
engine = ExpressionRulesEngine(rules=nw.from_native(pd.DataFrame(...)), dimension_metadata=metadata)

# Ibis (DuckDB)
import ibis
con = ibis.duckdb.connect()
table = con.create_table("rules", pl.DataFrame(...).to_pandas())
engine = ExpressionRulesEngine(rules=table, dimension_metadata=metadata)
```

---

## Part 2: Exact AccumulatorEngine

Use `AccumulatorEngine` for an authorized, contract-bound exact-cell artifact.
It is not the legacy combination/ranking API: source material is analyzed and
gated before build, and an application resolves a named contract/profile
against the resulting immutable lattice.

### Inputs the application must own

There are no default resource ceilings. Create and retain an `ExactLimits`
instance appropriate to the application, then pass it to the engine and every
analysis, approval-attachment, snapshot, or binding operation. The source
workflow also needs the application's:

- native source rows with a stable UUID in the declared `source_id_field`;
- `DimensionsMetadata`, including its `ContextContract` declarations;
- complete native `Aggregate` declarations (`output_name`, `data_type`, and
  `numeric_semantics="numeric-1"` together);
- `ruleset_id`, source-ID field, compilation domain, domains, predicate and
  language envelopes, routing, and `ValidationPolicy`.

`analyze_sources()` accepts those inputs explicitly. It returns a
`ValidationBundle` containing findings and a complete source report; it does
not make an approval decision.

### Authorize the current source material

The following is a runnable handoff once `source_inputs` is the application's
complete dictionary of the inputs above, `rows` is its current native source
material, and `limits` is its chosen `ExactLimits`. It deliberately does not
invent records, limits, policy, reviewer identity, trust, or a successful
analysis.

```python
from mountainash_rules import (
    analyze_sources,
    attach_warning_approvals,
    validate_build_input,
)

bundle = analyze_sources(rows, limits=limits, **source_inputs)
source_report = bundle.validation["reports"][0]

# The application obtains these only from its review process. Each approval is
# scoped to findings in source_report and carries its authority and actor.
approvals = reviewed_warning_approvals
reviewed_bundle = attach_warning_approvals(
    bundle,
    approvals,
    limits=limits,
)

validated_build = validate_build_input(
    rows,
    bundle=reviewed_bundle,
    analysis_input_id=source_report.analysis_input_id,
    source_report_id=source_report.id,
    approvals=approvals,
    limits=limits,
    **source_inputs,
)
```

For a clean report, `reviewed_warning_approvals` is empty. For warnings, the
application must create explicit scoped `WarningApproval` records before the
gate can pass. Errors, incomplete checks, stale material, and missing or
mis-scoped approvals do not produce `ValidatedBuildInput`.

### Build, route, and resolve

```python
from mountainash_rules import AccumulatorEngine

engine = AccumulatorEngine(
    dimension_metadata=source_inputs["metadata"],
    aggregates=source_inputs["aggregates"],
    limits=limits,
)
lattice = engine.build(rows, validation=validated_build)

result = engine.apply(
    lattice,
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

`contract_id` and `profile_id` must name a binding active on `lattice`;
`dont_care` is accepted only for fields permitted by that profile. For
context-key dimensions, use `build_all(rows, validation=validated_build)` or
call `build(..., partition_key=...)` with exactly every declared key. Repeated
routed requests use `engine.index(lattices)`, then:

```python
result = index.apply(
    context,
    contract_id=contract_id,
    profile_id=profile_id,
    dont_care=dont_care,
)
```

`index.apply_batch()` takes the same named contract/profile and optionally
`context_id_field`, `dont_care_field`, and `chunk_size`. `batch.for_context()`
raises `KeyError` for an unknown context ID; it does not fabricate an outcome.

### Read certainty and evidence

`AccumulatorResult` exposes one normalized outcome:

| Accessor | Meaning |
|---|---|
| `status`, `reason` | The resolution state and its defined reason |
| `values` | Established output values for a `decision`; otherwise `None` |
| `may_have_no_match` | Whether supplied facts leave an uncovered possibility |
| `binding_id`, `contract_id`, `profile_id` | The permission and request labels used |
| `cell_id`, `contributor_ids` | Definite identity/provenance, available only for a decision when established |
| `issues`, `observations` | Admission diagnostics and normalized request observations |
| `candidate_cells`, `candidate_contributors` | Possible cells/contributor edges; never a promoted decision |
| `lineage` | Definite requested-output lineage only |

Candidate-mode profiles report `status="candidates"` and make the candidate
relations available. For an inspected candidate, use
`candidate_lineage(cell_id)`; it remains candidate evidence rather than
definite lineage. `raise_for_status()` returns normal outcomes but raises the
typed request error for `invalid_context` or a profile that rejected unresolved
input.

### Bind, save, load, and inspect

Binding is additive and immutable:

```python
bound_view = lattice.with_binding(
    binding,
    evidence=binding_evidence,
    limits=limits,
)
```

The original lattice remains unchanged. `binding_evidence` must be the complete
portable evidence for that supplied binding; loading it alone grants no
permission.

Save or load a native artifact with explicit limits:

```python
from mountainash_rules import Lattice

snapshot = bound_view.save(snapshot_directory, limits=limits)
loaded = Lattice.load(snapshot, limits=limits)
compatible_engine = AccumulatorEngine(
    loaded.metadata,
    loaded.aggregates,
    limits=limits,
)
```

The compatible engine must have dimensions and aggregate declarations matching
the compiled artifact. A native snapshot has 11 files: `manifest.yaml`; eight
Parquet relations—`lattice`, `sources`, `contributors`, `scopes`, `scope_keys`,
`source_maps`, `vectors`, and `words`; and `predicates.json` plus
`validation.json`. Inspect native artifacts via `artifact_kind` and typed
`partition_identity`, not legacy `is_composed`. Flat legacy/imported lattices
are inspection-only: their rows can be read, but they cannot serve exact build,
binding, routing, or application.

---

## Summary

| You want to… | Use |
|---|---|
| Find all rules that match a context, ranked | `ExpressionRulesEngine.evaluate()` |
| Get the single best-matching rule | `result.best_match` |
| Explain why a rule matched | `result.explain(rule_name)` |
| Filter by minimum match specificity | `result.at_least(n)` or `evaluate(min_specificity=n)` |
| Analyze and authorize native source material | `analyze_sources()` → scoped review → `validate_build_input()` |
| Build an exact artifact | `AccumulatorEngine.build(..., validation=validated_build)` |
| Resolve a contract-bound context | `engine.apply(..., contract_id=..., profile_id=..., dont_care=...)` |
