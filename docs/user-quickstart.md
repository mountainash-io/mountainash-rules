# User Quickstart: mountainash-rules

This guide covers the two evaluation paths in `mountainash_rules`:

- **ExpressionRulesEngine** — direct context evaluation against a rules DataFrame.
- **AccumulatorEngine** — build a lattice of consistent rule combinations once, then evaluate any context against it.

---

## Installation

```bash
git clone https://github.com/mountainash-io/mountainash-utils-rules.git
cd mountainash-utils-rules
hatch env create
```

Requires sibling checkouts of `mountainash`, `mountainash-data`, and `mountainash-settings`
(see `hatch.toml` for path configuration).

---

## Part 1: ExpressionRulesEngine

### Concepts

Rules live in a DataFrame. Each row is a rule; each column is either a rule name, a dimension column, or metadata. The engine evaluates a **context** (a dict or Pydantic model) against every rule in one vectorised pass and returns surviving rules ranked by **specificity** — how many dimensions the rule constrains explicitly.

Wildcards are sentinel values that let a rule skip a dimension entirely:

| Data type | Wildcard sentinel |
|-----------|------------------|
| `str` | `"<NA>"` |
| `int` / `float` | `-999999999` |

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

`"<NA>"` and `-999999999` are the wildcard sentinels. A rule with a wildcard on a dimension matches any context value for that dimension.

---

### Step 2 — Declare dimensions

```python
from mountainash_rules import Dimension, DimensionsMetadata, MatchStrategy

metadata = DimensionsMetadata(dimensions=[
    Dimension(
        dimension_name="region",
        match_strategy=MatchStrategy.EXACT,
        data_type=str,
    ),
    Dimension(
        dimension_name="tier",
        match_strategy=MatchStrategy.EXACT,
        data_type=str,
    ),
    Dimension(
        dimension_name="spend",
        match_strategy=MatchStrategy.RANGE,
        data_type=int,
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

#### `survivors` — all matching rules, ranked best first

```python
print(result.survivors)
# shape: (3, ...)
# rule_name        region  tier       spend_min  spend_max  __specificity  __rank  __t_region  __t_tier  __t_spend
# premium_au       AU      premium    1000       9999       3              1       1           1         1
# standard_au      AU      <NA>       0          9999       2              2       1           0         1
# global_fallback  <NA>    <NA>       -999999999 -999999999 0              3       0           0         0
```

The `__specificity` column counts the number of dimensions that produced a hard match (ternary 1). Rules are sorted by specificity descending and assigned a 1-based `__rank`.

The `__t_<dimension>` columns show per-dimension ternary results: **1** = match, **0** = wildcard/unknown, **-1** = non-match. They are included by default (`include_observability=True`).

#### `best_match` — the single most specific survivor

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

---

### Excluding observability columns

If you don't need the `__t_*` columns in the output:

```python
result = engine.evaluate(context, include_observability=False)
# survivors will not contain __t_region, __t_tier, __t_spend
```

---

### Context with missing fields

For metadata-backed evaluation, an absent field or `None` binds to the datatype's NOT_SET sentinel; Boolean absence uses null instead of a string marker. Single, batch and chunked evaluation use the same matching semantics. Explicit UNKNOWN and NOT_SET markers remain distinct stored values. `False`, zero and an empty string are concrete inputs.

Ordinary comparisons and string predicates treat unavailable context as ternary 0: the rule may survive, but that dimension earns no specificity. In particular, PREFIX, SUFFIX, CONTAINS and per-row REGEX never match the spelling of a missing-value marker.

Strict strategies differ: `CONTEXT_REGEX` rejects unavailable context with -1, even when its pattern would match `<NOT_SET>` or empty text. A concrete empty string is tested against the pattern normally. `EXACT_KEY` accepts only authored rule-side wildcards when context is unavailable; concrete partition keys do not match missing input. Unknown in another dimension never rescues a known non-match.

The expressions-only constructor has no datatype metadata and retains string NOT_SET binding; custom expressions own its interpretation.

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
    data_type=float,
)

# LESS_THAN: context value < rule threshold
Dimension(
    dimension_name="risk",
    match_strategy=MatchStrategy.LESS_THAN,
    data_type=float,
)
```

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

# REGEX: context value matches a fixed pattern defined on the dimension
# The pattern is metadata-level — all rules share the same pattern.
Dimension(
    dimension_name="sku",
    match_strategy=MatchStrategy.REGEX,
    data_type=str,
    regex_pattern=r"^[A-Z]{3}-\d{4}$",
)
```

> **REGEX limitation:** `regex_pattern` is declared on the `Dimension`, not per-row. Every rule in the engine applies the same pattern. This is a global context validator, not a per-rule filter.

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

> **Backend note:** SET_MEMBERSHIP and SET_EXCLUSION currently use a Polars-native implementation. They may not work on all backends.

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

## Part 2: AccumulatorEngine

Use `AccumulatorEngine` when you need to find the most constrained rule *combination* that is consistent with a context — not just the best individual rule.

### When to use it

- Rules define constraints that accumulate (e.g. multiple fee rules that all apply to a transaction).
- You want the most specific *set* of rules that all agree with each other.
- You need numeric aggregation across matched rules (e.g. sum of applicable charges).

### How it works

1. **Build phase** (`build`): Computes all maximal consistent rule combinations from the rules DataFrame. Output is a `Lattice`.
2. **Apply phase** (`apply`): Evaluates a context against the lattice in one vectorised pass. Output is an `AccumulatorResult`.

Build once. Apply many times.

---

### Quick example

```python
import polars as pl
from mountainash_rules import (
    AccumulatorEngine, Aggregate,
    Dimension, DimensionsMetadata, MatchStrategy, DimensionRole,
)

rules = pl.DataFrame({
    "rule_name":  ["base_fee",  "au_fee",    "premium_fee"],
    "region":     ["<NA>",      "AU",        "<NA>"],
    "tier":       ["<NA>",      "<NA>",      "premium"],
    "fee":        [10,          5,           20],
})

metadata = DimensionsMetadata(dimensions=[
    Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
    Dimension(dimension_name="tier",   match_strategy=MatchStrategy.EXACT, data_type=str),
])

engine = AccumulatorEngine(
    dimension_metadata=metadata,
    aggregates=[Aggregate(column_name="fee", operation="sum")],
)

# Build the lattice (do this once at startup)
lattice = engine.build(rules)

# Apply a context
result = engine.apply(lattice, {"region": "AU", "tier": "premium"})

print(result.count)            # number of matching combinations
print(result.best_combination) # most specific matching combination
print(result.accumulated("fee"))  # summed fee for each matching combination
print(result.provenance)       # prime products identifying which rules contributed
print(result.depths)           # number of rules in each combination
```

---

### Partitioned lattices (CONTEXT_KEY dimensions)

If your rules are segmented by a fixed context attribute (e.g. product type, country), use `DimensionRole.CONTEXT_KEY` to partition the lattice:

```python
metadata = DimensionsMetadata(dimensions=[
    Dimension(
        dimension_name="product",
        match_strategy=MatchStrategy.EXACT,
        data_type=str,
        role=DimensionRole.CONTEXT_KEY,   # partitions the build
    ),
    Dimension(dimension_name="tier", match_strategy=MatchStrategy.EXACT, data_type=str),
])

engine = AccumulatorEngine(dimension_metadata=metadata)

# Build all partitions in one call
lattices = engine.build_all(rules)

# Apply — automatically selects the right lattice by partition key
result = engine.apply_auto(lattices, {"product": "loans", "tier": "premium"})
```

---

### AccumulatorEngine limitations

| Limitation | Detail |
|-----------|--------|
| Supported strategies | EXACT, RANGE, GREATER_THAN, LESS_THAN only. String/set strategies raise `ValueError`. |
| Aggregate operations | Only `"sum"` is currently implemented. |
| Rules per partition | ~500 maximum. Larger partitions raise `IndexError` from the prime table. |
| Lattice persistence | No built-in serialisation. Cache and reload the `Lattice` object yourself if needed. |

---

## Summary

| You want to… | Use |
|---|---|
| Find all rules that match a context, ranked | `ExpressionRulesEngine.evaluate()` |
| Get the single best-matching rule | `result.best_match` |
| Explain why a rule matched | `result.explain(rule_name)` |
| Filter by minimum match specificity | `result.at_least(n)` or `evaluate(min_specificity=n)` |
| Find all consistent rule combinations | `AccumulatorEngine.build()` + `apply()` |
| Sum a numeric column across matched rules | `AccumulatorResult.accumulated("column_name")` |
| Trace which rules contributed to a combination | `AccumulatorResult.provenance` |
