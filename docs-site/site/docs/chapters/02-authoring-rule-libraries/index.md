---
title: "Chapter 2: Authoring and Evolving Rule Libraries"
description: "Give rule columns meaning with Dimension metadata, choose the right match strategy for each column, and validate, serialize and evolve a rule library safely."
---

# Chapter 2: Authoring and Evolving Rule Libraries

Chapter 1 showed that a rule table is just a DataFrame, and that matching a rule against a context produces one of three ternary outcomes. It deliberately left one question open: how does the engine know that the column named `region` should be compared for exact equality, while `order_total_min`/`order_total_max` together describe a numeric range? The answer is metadata. This chapter is about writing that metadata — the `Dimension` and `DimensionsMetadata` objects that turn an otherwise opaque DataFrame into a rule library the engine can compile and a person can review.

The chapter follows one running example, a small library of regional fulfillment discount rules, and grows it section by section: first by giving its columns meaning, then by working through every match strategy available for those columns, and finally by validating, serializing and evolving the library as a whole. Every Python block below continues the same session — later blocks reuse names defined earlier, and the whole sequence runs as one program.

## Give rule columns meaning

A DataFrame of rule rows carries no semantics of its own. Column names are strings; cell values are whatever the backend's dtype system allows. Before the engine can compile a single expression, something has to declare, for every column that participates in matching: what strategy compares it, what Python type its values hold, and which physical column names it actually lives in. That declaration is the `Dimension` model, and a validated collection of them is `DimensionsMetadata`. This section introduces both, plus the two smaller enums — `MatchStrategy` and `DimensionRole` — that a `Dimension` is built from.

<!-- concept:11 -->
### The MatchStrategy Enum

`MatchStrategy` is a `StrEnum` defined in `mountainash_rules.core.constants` and re-exported from the top-level `mountainash_rules` package. It has thirteen members, and every one of them names a distinct comparison semantic between a rule's cell value(s) and a context's value for that dimension:

```python
from enum import StrEnum

class MatchStrategy(StrEnum):
    EXACT = "exact"
    EXACT_KEY = "exact_key"
    NOT_EQUAL = "not_equal"
    RANGE = "range"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    REGEX = "regex"
    CONTEXT_REGEX = "context_regex"
    SET_MEMBERSHIP = "set_membership"
    SET_EXCLUSION = "set_exclusion"
```

Because it is a `StrEnum`, every member's runtime value is the lowercase string shown above, so a strategy round-trips through YAML or JSON without a custom encoder — `MatchStrategy.RANGE.value == "range"` and `MatchStrategy("range") is MatchStrategy.RANGE` both hold. `mountainash_rules.core.compiler.DimensionCompiler` (introduced in [Chapter 6](../06-expression-execution-internals/index.md)) dispatches on this enum with a `match` statement, one branch per member, so every strategy you assign to a `Dimension` has exactly one corresponding compiled expression shape. `EXACT` is the default: a `Dimension` created without an explicit `match_strategy` behaves as if you had written `match_strategy=MatchStrategy.EXACT`.

Every compiled strategy — regardless of which one you pick — produces the same shape of result: a ternary value, `1` (TRUE), `0` (UNKNOWN) or `-1` (FALSE), for each rule row against the current context, exactly as [Chapter 1](../01-rule-tables-and-decisions/index.md) defined. What differs between strategies is *how* that value is derived: by equality, by an ordered comparison, by a string operation, or by set membership. The rest of this chapter's second section works through each member's exact rule for producing that ternary value, with worked examples grounded in the compiler source.

<!-- concept:23 -->
### The DimensionRole Enum

Every `Dimension` also carries a `role`, drawn from a second, much smaller `StrEnum`:

```python
class DimensionRole(StrEnum):
    CONSTRAINT = "constraint"
    CONTEXT_KEY = "context_key"
```

`role` defaults to `CONSTRAINT`. Unlike `match_strategy`, which the filter engine's `DimensionCompiler` reads and dispatches on for every dimension, `role` is consumed by exactly one place in the codebase: `AccumulatorEngine.__init__` (`src/mountainash_rules/engines/accumulator/engine.py`), which partitions `dimension_metadata.dimensions` into two lists based on it — one for `CONTEXT_KEY` dimensions, one for everything else. `DimensionCompiler.compile_dimensions` and the filter engine's evaluation pipeline never inspect `role` at all: they compile and evaluate *every* dimension in `DimensionsMetadata.dimensions`, uniformly, according to its `match_strategy`. This is a precise and important distinction — it is easy to assume, from the name "context key", that such a dimension is somehow skipped during ordinary rule evaluation, and that assumption is wrong. `role` changes how the accumulator engine groups your rules; it does not change what `ExpressionRulesEngine` (Chapter 3) does with them.

<!-- concept:24 -->
#### CONSTRAINT Role

A `CONSTRAINT` dimension is an ordinary matching column. Whichever engine you use, it participates the way you would expect: the filter engine compiles its `match_strategy` into a ternary expression and folds the result into survival and specificity; the accumulator engine (Chapter 5) includes it in coalesce — when two compatible rules combine into one lattice node, their `CONSTRAINT` values are merged into a tighter combined constraint. `CONSTRAINT` is the default `role`, and the overwhelming majority of dimensions in a typical rule library use it: region, product category, order total, customer tier, and so on.

<!-- concept:25 -->
#### CONTEXT_KEY Role

A `CONTEXT_KEY` dimension marks a column the *accumulator* engine uses to partition the rule space: `AccumulatorEngine` builds one independent `Lattice` per unique combination of `CONTEXT_KEY` values it finds in the rules table, and rules in different partitions can never combine with one another. The filter engine, as established above, does not treat a `CONTEXT_KEY` dimension any differently from a `CONSTRAINT` one — it still compiles and evaluates it according to `match_strategy`, and it still contributes to survival and specificity there. `CONTEXT_KEY`'s effect is entirely scoped to `AccumulatorEngine`'s partitioning and routing behavior, covered in full in [Chapter 5](../05-combining-and-persisting-rules/index.md).

A natural example is a `carrier` column on a shipping rules table: rules for `"express"` and rules for `"standard"` describe genuinely separate combination spaces, so marking `carrier` as `CONTEXT_KEY` tells the accumulator engine to build a separate lattice for each carrier rather than attempting to combine an express rule with a standard one. The table below summarizes the split:

| Role | Filter engine (`ExpressionRulesEngine`) | Accumulator engine (`AccumulatorEngine`) |
|---|---|---|
| `CONSTRAINT` | Compiled and evaluated like any dimension; contributes to survival and specificity | Participates in coalesce — combined (intersected) with compatible rules |
| `CONTEXT_KEY` | Compiled and evaluated identically — `role` is not read | Partitions the rule space; one lattice built per unique value combination |

<!-- concept:26 -->
### The Dimension Class

`Dimension` (`mountainash_rules.core.dimension`) is the Pydantic `BaseModel` that ties a `MatchStrategy`, a `DimensionRole`, a data type and a set of field names together for one logical column. Its fields are:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `dimension_name` | `str` | required | The logical name used as a key everywhere the engine references this dimension |
| `context_field` | `str \| None` | `None` | Field to read from the context object; falls back to `dimension_name` |
| `rule_field` | `str \| None` | `None` | Column to read from the rules DataFrame; falls back to `dimension_name` |
| `match_strategy` | `MatchStrategy` | `EXACT` | The comparison semantic (Section 2 of this chapter) |
| `data_type` | `DataType` | `DataType.STR` | The serializable Python type (this chapter's third section) |
| `role` | `DimensionRole` | `CONSTRAINT` | Filter vs. accumulator classification (above) |
| `valid_values` | `list[str \| int \| float \| bool]` | `[]` | Declarative domain of context values; **not enforced by either engine** — it is documentation consumed by external tooling such as `mountainash-rules-babel`'s coverage validation |
| `range_min_field` / `range_max_field` | `str \| None` | `None` | Rule columns holding the lower/upper bound, for `RANGE` |
| `range_min_inclusive` / `range_max_inclusive` | `bool` | `True` / `True` | Whether each `RANGE` bound is inclusive |
| `regex_pattern` | `str \| None` | `None` | A single literal pattern, for `CONTEXT_REGEX` only |

Here is the small metadata example this chapter builds on throughout. It defines three dimensions for a regional fulfillment discount library: a partition key (`carrier`), a plain equality constraint (`region`), and a numeric range (`order_total`):

```python
from mountainash_rules import (
    DataType, Dimension, DimensionRole, DimensionsMetadata, MatchStrategy,
)

fulfillment_dims = DimensionsMetadata(dimensions=[
    Dimension(
        dimension_name="carrier",
        role=DimensionRole.CONTEXT_KEY,
        match_strategy=MatchStrategy.EXACT_KEY,
        data_type=DataType.STR,
        rule_field="carrier_code",
        context_field="requested_carrier",
    ),
    Dimension(
        dimension_name="region",
        match_strategy=MatchStrategy.EXACT,
        data_type=DataType.STR,
    ),
    Dimension(
        dimension_name="order_total",
        match_strategy=MatchStrategy.RANGE,
        data_type=DataType.FLOAT,
        range_min_field="order_total_min",
        range_max_field="order_total_max",
    ),
])

assert [d.dimension_name for d in fulfillment_dims.dimensions] == [
    "carrier", "region", "order_total",
]
assert fulfillment_dims.get_dimension("region").match_strategy is MatchStrategy.EXACT
assert fulfillment_dims.get_dimension("order_total").role is DimensionRole.CONSTRAINT
```

Note that `region` sets nothing beyond `dimension_name`, `match_strategy` and `data_type`: with no explicit `context_field` or `rule_field`, both resolve to `"region"`, and `role` defaults to `CONSTRAINT`. `carrier`, by contrast, sets every optional field it needs — it is a `CONTEXT_KEY`, uses `EXACT_KEY` (the strategy built specifically for wildcard-on-rule-side, no-wildcard-on-context-side partition keys — covered in this chapter's second section), and reads from differently-named columns on each side. `order_total` shows the two extra fields `RANGE` requires: without both `range_min_field` and `range_max_field`, the `Dimension` fails to construct at all, as this chapter's third section demonstrates.

Now attach a matching rules DataFrame, using the exact column names the metadata above resolves to, and confirm the two agree:

```python
import polars as pl

fulfillment_rules = pl.DataFrame({
    "rule_name":        ["express_au_large", "standard_any", "fallback"],
    "carrier_code":     ["express",           "<NA>",         "<NA>"],
    "region":           ["AU",                "AU",           "<NA>"],
    "order_total_min":  [500.0,               0.0,            -999999999.0],
    "order_total_max":  [999999999.0,         499.99,         -999999999.0],
})

rule_columns = set(fulfillment_rules.columns)
for dim in fulfillment_dims.dimensions:
    if dim.match_strategy is MatchStrategy.RANGE:
        # RANGE reads range_min_field/range_max_field, not resolved_rule_field
        assert dim.range_min_field in rule_columns
        assert dim.range_max_field in rule_columns
    else:
        assert dim.resolved_rule_field in rule_columns
```

`<NA>` is the string sentinel from Chapter 1 acting as a rule-side wildcard on `region`; `-999999999` is the numeric sentinel playing the same role for `order_total`'s bounds. This library is not yet handed to an engine — Chapter 3 covers construction and evaluation — but it is already a complete, internally consistent authoring artifact: every dimension's resolved column names are present in the DataFrame, and nothing about the metadata or the data is malformed.

<!-- concept:27 -->
### DimensionsMetadata

`DimensionsMetadata` is the validated container you actually hand to an engine. Beyond `dimensions: list[Dimension]`, it carries three table-level fields that affect result selection rather than per-dimension matching: `hit_policy: HitPolicy = HitPolicy.COLLECT`, `priority_field: str | None = None`, and `output_fields: list[str] = []`. These three are read by the filter engine's selection logic, not by anything in this chapter — [Chapter 3](../03-evaluating-decisions/index.md) covers hit policies and output projection in full; this chapter only needs you to know they live here, alongside the dimension list, as the single object that fully describes how a rules table should be read.

`DimensionsMetadata` enforces one invariant of its own at construction time, independent of anything inside a single `Dimension`: every `dimension_name` in the list must be unique. This matters because `dimension_name` is used as a dictionary key throughout both engines — compiled expressions, ternary result columns and coalesce mappings are all keyed by it, so a duplicate would make later lookups ambiguous. The check runs as a Pydantic `model_validator(mode="after")` and raises before you ever reach an engine:

```python
from pydantic import ValidationError

try:
    DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region"),
        Dimension(dimension_name="region"),
    ])
except ValidationError as exc:
    assert "Duplicate dimension names" in str(exc)
```

`DimensionsMetadata` also exposes `get_dimension(name)`, used above, which raises `KeyError` for an unknown name rather than returning `None` — a lookup by a name that was never declared is a programming error, not a normal "not found" case, so it fails loudly:

```python
try:
    fulfillment_dims.get_dimension("nonexistent")
except KeyError as exc:
    assert "not found" in str(exc)
```

<!-- concept:28 -->
### Field Resolution

`dimension_name`, `context_field` and `rule_field` are deliberately three separate names, not one. A dimension's logical name (used everywhere inside the engine) need not match either the column name in your rules DataFrame or the attribute name on your context object — and in a library maintained by more than one team, it usually won't. Two computed properties resolve the actual names to use:

```python
carrier_dim = fulfillment_dims.get_dimension("carrier")
assert carrier_dim.resolved_rule_field == "carrier_code"
assert carrier_dim.resolved_context_field == "requested_carrier"

region_dim = fulfillment_dims.get_dimension("region")
assert region_dim.resolved_rule_field == "region"
assert region_dim.resolved_context_field == "region"
```

`resolved_rule_field` returns `rule_field` if set, otherwise `dimension_name`; `resolved_context_field` does the same for `context_field`. `carrier` shows the decoupled case: the rules DataFrame calls the column `carrier_code`, while callers construct contexts with a `requested_carrier` attribute — the dimension named `"carrier"` bridges the two without either side needing to change to match the other. `region` shows the common case: with no overrides, all three names coincide. The compiler (Chapter 6) uses `resolved_rule_field` to read the DataFrame column and `resolved_context_field` (via context-value extraction) to look up context attributes; this chapter's authoring work is exactly choosing which of the two, or the shared default, is correct for each dimension.

## Choose matching semantics

With the metadata vocabulary established, this section works through every `MatchStrategy` member, in the order declared on the enum, with the exact ternary rule the compiler implements for each — grounded in `mountainash_rules/core/compiler.py`. Every strategy shares one property from Chapter 1: it produces `1`, `0` or `-1`, never a raw boolean, so it composes uniformly with survival and specificity regardless of which strategy a given dimension uses.

<!-- concept:12 -->
### EXACT Strategy

`EXACT` is the default. It compiles to a sentinel-aware equality check: `rule_col.t_eq(ctx_col)`, where both sides are read through `ma.t_col(field, unknown=sentinels_for(data_type))`. `t_col` treats a cell equal to that data type's sentinel set as ternary-unknown automatically, so `t_eq` needs no separate wildcard branch. Using `region` from `fulfillment_dims`:

| Rule cell (`region`) | Context value | Result | Why |
|---|---|---|---|
| `"AU"` | `"AU"` | `1` (TRUE) | Values match |
| `"AU"` | `"US"` | `-1` (FALSE) | Values differ |
| `"<NA>"` | `"AU"` | `0` (UNKNOWN) | Rule is wildcard |
| `"AU"` | `"<NOT_SET>"` | `0` (UNKNOWN) | Context missing |
| `"<NA>"` | `"<NOT_SET>"` | `0` (UNKNOWN) | Both are sentinels |

A rule row with `<NA>` in an `EXACT` dimension never rejects a context on that dimension, but it also never claims a hard match there — exactly the "don't care" behavior Chapter 1 introduces sentinels to provide. `EXACT` is unconditionally valid for every `DataType` — there is no data-type restriction on it — but for `DataType.BOOL` specifically, the compiler substitutes a different rule; see "Bool Ternary Comparison" below.

<!-- concept:91 -->
### EXACT_KEY Strategy

`EXACT_KEY` is a deliberately asymmetric variant of exact matching, built for `CONTEXT_KEY`-style partition columns. Unlike `EXACT`, it does not wrap either side in a sentinel-aware `t_col`; it compares the rule and context columns directly, and only checks the rule side against the literal `unknown_sentinel_for(data_type)` value (or `is_null()` for `DataType.BOOL`, which has no typed sentinel). A specific rule key equals a specific context key for TRUE; a rule-side sentinel is UNKNOWN (a wildcard partition); but a *context*-side sentinel is treated as an ordinary non-matching value, not a wildcard — it produces FALSE against a specific rule key:

| Rule cell (`carrier_code`) | Context value | Result | Why |
|---|---|---|---|
| `"express"` | `"express"` | `1` (TRUE) | Specific keys are equal |
| `"express"` | `"standard"` | `-1` (FALSE) | Specific keys differ |
| `"<NA>"` | `"express"` | `0` (UNKNOWN) | Rule-side wildcard |
| `"express"` | `"<NOT_SET>"` | `-1` (FALSE) | A context sentinel is **not** a wildcard here |

That last row is the point of `EXACT_KEY`: a missing or unknown context value must never accidentally select a specific partition. `EXACT_KEY` is the strategy `LatticeIndex` relies on for accumulator partition routing (Chapter 5) — it is why `fulfillment_dims`'s `carrier` dimension uses it rather than plain `EXACT`, even though the two behave identically whenever neither side is a sentinel.

<!-- concept:13 -->
### NOT_EQUAL Strategy

`NOT_EQUAL` inverts `EXACT`: `rule_col.t_ne(ctx_col)`, with the same `t_col` sentinel wrapping on both sides. A new dimension for the fulfillment library — excluding one customer tier from a rule — demonstrates it:

```python
excluded_tier_dim = Dimension(
    dimension_name="excluded_tier",
    match_strategy=MatchStrategy.NOT_EQUAL,
    data_type=DataType.STR,
    rule_field="excluded_tier_code",
)
```

| Rule cell | Context value | Result | Why |
|---|---|---|---|
| `"trial"` | `"pro"` | `1` (TRUE) | Values differ (not excluded) |
| `"trial"` | `"trial"` | `-1` (FALSE) | Values match (excluded) |
| `"<NA>"` | `"pro"` | `0` (UNKNOWN) | Rule is wildcard |
| `"pro"` | `"<NOT_SET>"` | `0` (UNKNOWN) | Context missing |

`NOT_EQUAL` is a natural fit for "applies to everything except this one value" rules; sentinel handling is identical to `EXACT` because both compile through the same `t_col`-wrapped comparison, differing only in `t_eq` versus `t_ne`.

<!-- concept:14 -->
### RANGE Strategy

`RANGE` checks whether the context value falls within an interval described by **two** rule columns, `range_min_field` and `range_max_field`, rather than the single column every other scalar strategy reads. Each bound is compiled with its own inclusivity flag — `range_min_inclusive`/`range_max_inclusive`, both defaulting to `True` — and the two resulting ternary comparisons are combined with ternary AND (`t_and`, the same Kleene AND from [Chapter 1](../01-rule-tables-and-decisions/index.md)):

$$
\text{min} \le \text{context} \le \text{max} \quad \text{(both bounds inclusive, the default)}
$$

The `order_total` dimension in `fulfillment_dims` demonstrates this with a context value of `750.0`:

| Rule | `order_total_min` | `order_total_max` | Bound check | Result |
|---|---|---|---|---|
| `express_au_large` | 500.0 | 999999999.0 | $500 \le 750 \le 999999999$ | `1` (TRUE) |
| `standard_any` | 0.0 | 499.99 | $0 \le 750$ but $750 \not\le 499.99$ | `-1` (FALSE) |
| `fallback` | -999999999.0 | -999999999.0 | both bounds are the wildcard sentinel | `0` (UNKNOWN) |

Because each bound is read through the same sentinel-aware `t_col` as `EXACT`, a bound column holding the numeric sentinel `-999999999.0` contributes `UNKNOWN` on that side rather than a real comparison; ternary AND then means a hard `FALSE` on either bound makes the whole dimension `FALSE`, while an `UNKNOWN` bound combined with a `TRUE` bound leaves the dimension `UNKNOWN` rather than `TRUE`. `RANGE` requires **both** `range_min_field` and `range_max_field` — there is no "lower bound only" form. To get greater-than-or-equal-only semantics without a second explicit constraint, point `range_max_field` at a column filled entirely with that data type's wildcard sentinel, so the upper bound never actually constrains anything.

<!-- concept:15 -->
### GREATER_THAN Strategy

`GREATER_THAN` is a strict, single-threshold comparison: `ctx_col.t_gt(rule_col)` — the context value must be strictly greater than the rule's threshold value. A `loyalty_years` dimension shows it:

```python
loyalty_years_dim = Dimension(
    dimension_name="loyalty_years",
    match_strategy=MatchStrategy.GREATER_THAN,
    data_type=DataType.INT,
)
```

| Rule cell (threshold) | Context value | Result |
|---|---|---|
| 3 | 5 | `1` (TRUE) |
| 3 | 3 | `-1` (FALSE) — equality is not greater-than |
| 3 | 1 | `-1` (FALSE) |
| -999999999 | 5 | `0` (UNKNOWN) — rule is wildcard |

`GREATER_THAN` requires an orderable `data_type` — `INT`, `FLOAT`, `DATE` or `DATETIME` — the same requirement `RANGE` has; this chapter's third section shows exactly what happens when that requirement is violated.

<!-- concept:16 -->
### LESS_THAN Strategy

`LESS_THAN` mirrors `GREATER_THAN`: `ctx_col.t_lt(rule_col)`, also strict. A `package_weight` dimension caps orders under a weight threshold:

```python
package_weight_dim = Dimension(
    dimension_name="package_weight",
    match_strategy=MatchStrategy.LESS_THAN,
    data_type=DataType.FLOAT,
)
```

| Rule cell (threshold) | Context value | Result |
|---|---|---|
| 5.0 | 3.2 | `1` (TRUE) |
| 5.0 | 5.0 | `-1` (FALSE) — equality is not less-than |
| 5.0 | 8.0 | `-1` (FALSE) |
| -999999999.0 | 3.2 | `0` (UNKNOWN) |

Like `GREATER_THAN`, `LESS_THAN` requires an orderable `data_type` and is strict at the boundary — use `RANGE` (with an inclusive bound) if you need "at or below" instead.

<!-- concept:17 -->
### PREFIX Strategy

`PREFIX` and the three strategies after it — `SUFFIX`, `CONTAINS`, `REGEX` — share one compiled shape: a sentinel check on the rule cell (`<NA>` or `<NOT_SET>` produces `0` directly, before any string operation runs), followed by the actual string test, mapped to `1`/`-1`. `PREFIX` checks `ctx_col.str.starts_with(rule_col)`:

```python
sku_prefix_dim = Dimension(
    dimension_name="sku_prefix",
    match_strategy=MatchStrategy.PREFIX,
    data_type=DataType.STR,
    rule_field="sku_prefix_pattern",
)
```

| Rule cell | Context value | Result |
|---|---|---|
| `"ELEC-"` | `"ELEC-4410"` | `1` (TRUE) |
| `"ELEC-"` | `"FURN-2201"` | `-1` (FALSE) |
| `"<NA>"` | `"ELEC-4410"` | `0` (UNKNOWN) |

`PREFIX`, along with `SUFFIX`, `CONTAINS`, `REGEX` and `CONTEXT_REGEX`, requires `data_type=DataType.STR`; there is no numeric or temporal form of any string strategy.

<!-- concept:18 -->
### SUFFIX Strategy

`SUFFIX` checks `ctx_col.str.ends_with(rule_col)` — useful for domain suffixes, file extensions, or postal-code tails:

```python
email_domain_dim = Dimension(
    dimension_name="email_domain",
    match_strategy=MatchStrategy.SUFFIX,
    data_type=DataType.STR,
)
```

| Rule cell | Context value | Result |
|---|---|---|
| `".com.au"` | `"orders@shop.com.au"` | `1` (TRUE) |
| `".com.au"` | `"orders@shop.co.uk"` | `-1` (FALSE) |
| `"<NA>"` | `"orders@shop.com.au"` | `0` (UNKNOWN) |

<!-- concept:19 -->
### CONTAINS Strategy

`CONTAINS` is the most permissive string strategy — `ctx_col.str.contains(rule_col)` matches the rule value anywhere in the context string:

```python
promo_tag_dim = Dimension(
    dimension_name="promo_tag",
    match_strategy=MatchStrategy.CONTAINS,
    data_type=DataType.STR,
)
```

| Rule cell | Context value | Result |
|---|---|---|
| `"clearance"` | `"summer_clearance_2026"` | `1` (TRUE) |
| `"clearance"` | `"loyalty_bonus"` | `-1` (FALSE) |
| `"<NA>"` | `"summer_clearance_2026"` | `0` (UNKNOWN) |

<!-- concept:20 -->
### REGEX Strategy

`REGEX` is the per-row string strategy: each rule row supplies its own pattern, so different rules can accept different textual shapes for the same context field. The compiler currently implements this with a Polars-native `str.contains` expression rather than a portable `mountainash` operation, because `mountainash`'s `regex_contains` only accepts a literal pattern, not a column-valued one — the method is explicitly tagged as a backend fallback, and non-Polars backends fail at evaluation time rather than compilation time for this one strategy.

```python
promo_code_dim = Dimension(
    dimension_name="promo_code",
    match_strategy=MatchStrategy.REGEX,
    data_type=DataType.STR,
    rule_field="promo_code_pattern",
)
```

| Rule cell (pattern) | Context value | Result |
|---|---|---|
| `r"^AU-\d{4}$"` | `"AU-4471"` | `1` (TRUE) |
| `r"^AU-\d{4}$"` | `"US-4471"` | `-1` (FALSE) |
| `"<NA>"` | `"AU-4471"` | `0` (UNKNOWN) |

`regex_pattern` (the `Dimension` field) must stay unset for `REGEX` — the per-row pattern lives in the rule column, not on the metadata. This chapter's third section shows the validator error you get if you set both.

<!-- concept:92 -->
### CONTEXT_REGEX Strategy

`CONTEXT_REGEX` is different from the four strategies above it: instead of reading a pattern per rule row, it stores one literal pattern on the `Dimension` itself, in `regex_pattern`, and applies `ctx_col.str.regex_contains(dim.regex_pattern)` uniformly. Every rule gets the *same* ternary outcome for this dimension — there is no rule-side wildcard, and consequently no `0` branch at all; the compiled expression is `when(match).then(1).otherwise(-1)`.

```python
postal_format_dim = Dimension(
    dimension_name="postal_code_format",
    match_strategy=MatchStrategy.CONTEXT_REGEX,
    data_type=DataType.STR,
    regex_pattern=r"^\d{4}$",
)
```

| Context value | Result |
|---|---|
| `"3000"` | `1` (TRUE) — matches the pattern |
| `"ZZ-3000"` | `-1` (FALSE) — does not match |

Use `CONTEXT_REGEX` for a global format or eligibility check that every rule shares (validate the postal code format once, for the whole table); use `REGEX` when different rules genuinely need different patterns. Both cannot apply to the same dimension at once — the validator rejects setting `regex_pattern` on a `REGEX` dimension, precisely because that would leave it ambiguous which pattern (the literal one, or the per-row one) should govern.

<!-- concept:21 -->
### SET_MEMBERSHIP Strategy

`SET_MEMBERSHIP` reads a **list-typed** rule cell and tests whether the context value is one of its elements: `ctx_col.t_is_in(rule_col)`, after the rule column is normalized (this chapter's third section, "Set Wildcard Sentinel" and "Set Value Normalization", covers exactly what normalization does and why it exists).

```python
allowed_services_dim = Dimension(
    dimension_name="allowed_services",
    match_strategy=MatchStrategy.SET_MEMBERSHIP,
    data_type=DataType.STR,
    rule_field="allowed_services",
    context_field="requested_service",
)
```

| Rule cell (list) | Context value | Result | Why |
|---|---|---|---|
| `["standard", "priority"]` | `"priority"` | `1` (TRUE) | Value is in the list |
| `["standard", "priority"]` | `"express"` | `-1` (FALSE) | Value is not in the list |
| `["standard", "priority"]` | `"<NOT_SET>"` | `0` (UNKNOWN) | Context value is a sentinel |
| the wildcard list (see below) | any value | `0` (UNKNOWN) | Rule list is a wildcard — checked before membership |

A concrete list that does not contain the context value is a hard `FALSE`, not `UNKNOWN` — an empty or non-matching list is a real constraint, distinct from "no constraint at all". `SET_MEMBERSHIP` accepts every `data_type` except `BOOL`; the third section explains why boolean set dimensions are rejected outright.

<!-- concept:22 -->
### SET_EXCLUSION Strategy

`SET_EXCLUSION` is the complement: `ctx_col.t_is_not_in(rule_col)`, TRUE when the context value is **not** present in the rule's list. Sentinel and wildcard handling are identical to `SET_MEMBERSHIP` — both share the same normalization and wildcard-detection logic; only the final membership test differs.

```python
sanctioned_regions_dim = Dimension(
    dimension_name="sanctioned_regions",
    match_strategy=MatchStrategy.SET_EXCLUSION,
    data_type=DataType.STR,
)
```

| Rule cell (list) | Context value | Result |
|---|---|---|
| `["XX", "YY"]` | `"AU"` | `1` (TRUE) — not sanctioned |
| `["XX", "YY"]` | `"XX"` | `-1` (FALSE) — sanctioned |
| `["XX", "YY"]` | `"<NOT_SET>"` | `0` (UNKNOWN) |

<!-- concept:93 -->
### Bool Ternary Comparison

`DataType.BOOL` has no in-band typed sentinel: neither `True` nor `False` can safely mean "don't care" for a Boolean field, unlike strings, numbers or temporal values, which each have a reserved out-of-domain sentinel. So for `EXACT` and `NOT_EQUAL` on a Boolean dimension specifically, `DimensionCompiler` substitutes a different rule entirely — `_compile_bool_ternary` — rather than the sentinel-aware `t_col`/`t_eq`/`t_ne` path used for every other data type: a `null` on either the rule side or the context side is the don't-care state, and only two non-null Booleans are actually compared.

```python
is_member_dim = Dimension(
    dimension_name="is_member",
    match_strategy=MatchStrategy.EXACT,
    data_type=DataType.BOOL,
)
```

| Rule cell | Context value | Result | Why |
|---|---|---|---|
| `True` | `True` | `1` (TRUE) | Non-null Booleans equal |
| `True` | `False` | `-1` (FALSE) | Non-null Booleans differ |
| `null` | `True` | `0` (UNKNOWN) | Null rule is the wildcard |
| `False` | `null` | `0` (UNKNOWN) | Null context is don't-care |

`_compile_bool_ternary` is invoked with `"__eq__"` from `_compile_exact` and `"__ne__"` from `_compile_not_equal`, so `NOT_EQUAL` on a Boolean dimension follows exactly the same null-is-wildcard rule, just with the comparison inverted. Every other `MatchStrategy` — including `SET_MEMBERSHIP`/`SET_EXCLUSION`, which reject `BOOL` outright — either has no meaningful Boolean form or does not need this substitution.

A library rarely uses one strategy in isolation. Assembling every dimension defined in this section into one `DimensionsMetadata` — a complete, if illustrative, rule library — is itself a valid, useful check: it proves every dimension_name is unique and every `Dimension` individually passed its own validation.

```python
full_catalog_dims = DimensionsMetadata(dimensions=[
    fulfillment_dims.get_dimension("carrier"),
    fulfillment_dims.get_dimension("region"),
    fulfillment_dims.get_dimension("order_total"),
    excluded_tier_dim,
    loyalty_years_dim,
    package_weight_dim,
    sku_prefix_dim,
    email_domain_dim,
    promo_tag_dim,
    promo_code_dim,
    postal_format_dim,
    allowed_services_dim,
    sanctioned_regions_dim,
    is_member_dim,
])

assert len(full_catalog_dims.dimensions) == 14
assert {d.match_strategy for d in full_catalog_dims.dimensions} == set(MatchStrategy)
```

That last assertion is worth pausing on: this one library, built purely by authoring metadata, already exercises every member of `MatchStrategy`.

## Validate, serialize and evolve a library

A rule library is not static: dimensions get added, strategies get changed, and metadata gets checked into version control next to (or instead of) Python code. This section covers the mechanisms that keep that evolution safe — the validator that rejects impossible strategy/type combinations, the `DataType` enum and its temporal sentinels, YAML persistence, and the two set-dimension helpers that keep list-valued wildcards unambiguous.

<!-- concept:29 -->
### The Dimension Validator

Every constraint demonstrated informally in the previous section — `RANGE` needing both bound fields, string strategies needing `DataType.STR`, `CONTEXT_REGEX` needing a pattern — is enforced by one place: a single Pydantic `@model_validator(mode="after")` method on `Dimension`, `_validate_strategy_fields`. Because it runs in `mode="after"`, it sees every field already assigned (including defaults), so it can check combinations across fields rather than validating each field in isolation. Each failure it raises is a `pydantic.ValidationError` — a subclass of the built-in `ValueError`, so ordinary `except ValueError` handling catches it — carrying a message that names the offending dimension and states exactly which requirement was violated. There is no separate validation pass or "compile-time-only" check: an invalid `Dimension` cannot be constructed at all, so a malformed rule library fails at metadata-authoring time, long before it reaches an engine or a production evaluation.

<!-- concept:30 -->
### Data Type Constraints

The validator's checks are not arbitrary busywork; each one reflects a real semantic requirement of the corresponding strategy. The following table is the complete set, drawn directly from `_validate_strategy_fields`:

| If `match_strategy` is... | Then... | Otherwise the validator raises, and you should... |
|---|---|---|
| `RANGE` | `range_min_field` and `range_max_field` must both be set, and `data_type` must be `INT`, `FLOAT`, `DATE` or `DATETIME` | Add both bound fields, or switch to an orderable `data_type` |
| `REGEX`, `CONTEXT_REGEX`, `PREFIX`, `SUFFIX`, `CONTAINS` | `data_type` must be `DataType.STR` | Change `data_type` to `STR`, or pick a non-string strategy |
| `CONTEXT_REGEX` | `regex_pattern` must be a non-empty string | Set a literal `regex_pattern` on the dimension |
| anything other than `CONTEXT_REGEX` | `regex_pattern` must be unset | Remove `regex_pattern`; for per-row patterns, use `REGEX` and put the pattern in the rule column instead |
| `GREATER_THAN`, `LESS_THAN` | `data_type` must be `INT`, `FLOAT`, `DATE` or `DATETIME` | Switch to an orderable `data_type` |
| `SET_MEMBERSHIP`, `SET_EXCLUSION` | `data_type` must not be `BOOL` | Use `EXACT`/`NOT_EQUAL` (with the Bool Ternary Comparison rule) instead — there is no typed wildcard sentinel for a Boolean list, and a set over `{true, false}` is degenerate anyway |

Every other `data_type`/`match_strategy` pairing not listed above is accepted — in particular, `EXACT`, `EXACT_KEY` and `NOT_EQUAL` work with every `DataType` including `BOOL`, and `SET_MEMBERSHIP`/`SET_EXCLUSION` accept any non-Boolean type. Five representative failures, each with the actual message the validator produces:

```python
attempts = []

try:
    Dimension(dimension_name="x", match_strategy=MatchStrategy.RANGE, data_type=DataType.INT)
except ValidationError as exc:
    attempts.append("range missing fields: " + exc.errors()[0]["msg"])

try:
    Dimension(dimension_name="x", match_strategy=MatchStrategy.PREFIX, data_type=DataType.INT)
except ValidationError as exc:
    attempts.append("prefix wrong type: " + exc.errors()[0]["msg"])

try:
    Dimension(dimension_name="flags", match_strategy=MatchStrategy.SET_MEMBERSHIP, data_type=DataType.BOOL)
except ValidationError as exc:
    attempts.append("bool set: " + exc.errors()[0]["msg"])

try:
    Dimension(dimension_name="x", match_strategy=MatchStrategy.CONTEXT_REGEX, data_type=DataType.STR)
except ValidationError as exc:
    attempts.append("missing pattern: " + exc.errors()[0]["msg"])

try:
    Dimension(dimension_name="x", match_strategy=MatchStrategy.EXACT, data_type=DataType.STR, regex_pattern="^foo")
except ValidationError as exc:
    attempts.append("stray pattern: " + exc.errors()[0]["msg"])

for message in attempts:
    print(message)
assert len(attempts) == 5
```

Each of these five constructions raises before a `Dimension` object ever exists — there is no partially-built, invalid `Dimension` to accidentally pass downstream.

<!-- concept:94 -->
### The DataType Enum

`DataType` is the `StrEnum` behind every `data_type` field used throughout this chapter — six members, `STR`, `INT`, `FLOAT`, `BOOL`, `DATE`, `DATETIME`, serialized as the matching lowercase strings. Two properties make it useful beyond membership checks: `is_numeric` (`INT` or `FLOAT`) and `is_temporal` (`DATE` or `DATETIME`) are exactly what the validator table above calls "orderable"; `python_type` maps each member to its actual Python runtime class:

```python
from datetime import date, datetime

assert DataType.STR.python_type is str
assert DataType.INT.python_type is int
assert DataType.FLOAT.python_type is float
assert DataType.BOOL.python_type is bool
assert DataType.DATE.python_type is date
assert DataType.DATETIME.python_type is datetime
assert DataType.INT.is_numeric and DataType.DATE.is_temporal
```

For backward compatibility, `data_type=str` (the raw Python class, not `DataType.STR`) is still accepted: a `field_validator(mode="before")` maps it through `PYTHON_TO_DATATYPE` to the corresponding enum member and emits a `DeprecationWarning`. New metadata should always use the `DataType` member (or its string value) directly; the raw-class form exists only so older schemas keep working.

<!-- concept:95 -->
### Temporal Sentinels

`DATE` and `DATETIME` dimensions cannot use the string sentinels `"<NA>"`/`"<NOT_SET>"` or the numeric sentinels `-999999999`/`-999999998` — those are the wrong Python type. Instead, `mountainash_rules.core.constants` reserves values at the proleptic floor of each temporal domain, deliberately below where any real business date or timestamp lives:

| Data type | UNKNOWN sentinel | NOT_SET sentinel |
|---|---|---|
| `DataType.DATE` | `date(1, 1, 1)` | `date(1, 1, 2)` |
| `DataType.DATETIME` | `datetime(1, 1, 1)` | `datetime(1, 1, 2)` |

`sentinels_for(DataType.DATE)` returns `{date(1,1,1), date(1,1,2)}`; `unknown_sentinel_for`/`not_set_sentinel_for` return the single relevant value for each side. Because these sentinels are ordinary, orderable `date`/`datetime` values rather than nulls, `RANGE`, `GREATER_THAN` and `LESS_THAN` work on temporal dimensions through the exact same `t_col`-based comparison machinery as numeric ones — no separate null-handling branch is needed. A temporal range dimension for this library's promotional window looks like:

```python
import datetime as dt

from mountainash_rules import sentinels_for, unknown_sentinel_for

assert sentinels_for(DataType.DATE) == {dt.date(1, 1, 1), dt.date(1, 1, 2)}
assert unknown_sentinel_for(DataType.DATETIME) == dt.datetime(1, 1, 1)

effective_at_dim = Dimension(
    dimension_name="effective_at",
    match_strategy=MatchStrategy.RANGE,
    data_type=DataType.DATETIME,
    range_min_field="effective_at_min",
    range_max_field="effective_at_max",
)
assert effective_at_dim.data_type.is_temporal
```

<!-- concept:96 -->
### YAML Round-Trip

`DimensionsMetadata` round-trips through YAML without a custom schema: `to_yaml()` serializes with `model_dump(mode="json", exclude_defaults=True)` — defaults are omitted so a schema stays forward-compatible as new optional fields are added later — and `from_yaml(text)` reconstructs an equal object with `model_validate(yaml.safe_load(text))`. `to_yaml_file`/`from_yaml_file` are the path-based equivalents.

```python
yaml_text = full_catalog_dims.to_yaml()
reloaded = DimensionsMetadata.from_yaml(yaml_text)
assert reloaded == full_catalog_dims

import tempfile, pathlib
with tempfile.TemporaryDirectory() as tmp:
    saved_path = full_catalog_dims.to_yaml_file(pathlib.Path(tmp) / "fulfillment-dims.yaml")
    assert DimensionsMetadata.from_yaml_file(saved_path) == full_catalog_dims
```

Because loading runs the same `Dimension`/`DimensionsMetadata` validation as constructing objects directly in Python, a YAML file with a missing `range_max_field` or an unsupported strategy/type pairing fails at load time with the same descriptive message shown in "Data Type Constraints" above — not later, during evaluation. This is what makes it practical to keep a rule library's structural definition in a YAML file, reviewed and version-controlled independently of the rules DataFrame itself and of any application code that loads it.

<!-- concept:98 -->
### Set Wildcard Sentinel

`SET_MEMBERSHIP` and `SET_EXCLUSION` cannot use a null cell as their wildcard the way scalar strategies use a sentinel value, because a list column's "no constraint" state has to be representable identically across every supported backend, and a bare null list does not survive that requirement portably. `mountainash_rules.core.set_wildcard` — the shared module both the filter engine and the accumulator engine import, so the two cannot silently diverge — instead represents a set-dimension wildcard **in-band**, as a concrete single-element list: `[unknown_sentinel_for(dim.data_type)]`. For a `STR` dimension like `allowed_services`, the wildcard is the list `["<NA>"]`, never a null cell and never an empty list.

This representation only works because the sentinel value is reserved: a *concrete* rule list is never allowed to contain it as one element among several. `validate_set_columns` (called by both engines before evaluation) raises `ValueError` if it finds a non-wildcard list embedding the sentinel — for `allowed_services`, a rule list such as `["<NA>", "standard"]` would fail with a message identifying the dimension and stating that the sentinel is only valid as the sole element. A second, narrower check, `validate_set_no_null_elements`, rejects an element-level null inside a concrete list (for example `["standard", None]`) — but this check runs **only** on the accumulator's Polars-internal build path, because it relies on `list.drop_nulls`, which the Ibis backend does not support; the filter engine does not call it, since its own membership operator tolerates a null element by simply never matching it.

| Rule list | Meaning | Valid? |
|---|---|---|
| `["standard", "priority"]` | Concrete constraint — matches either value | Yes |
| `["<NA>"]` | Wildcard — matches any context value | Yes |
| `null` (whole-cell) | Also the wildcard, before normalization (next section) | Yes |
| `["<NA>", "standard"]` | Sentinel embedded alongside a real value | No — `validate_set_columns` raises |
| `["standard", None]` | A null element inside a concrete list | No, on the accumulator build path — `validate_set_no_null_elements` raises |

<!-- concept:99 -->
### Set Value Normalization

Set values need exactly one canonical representation before they can be matched, fingerprinted for lattice combination, or compared for equality — two rule rows that list the same countries in a different order should be recognized as the same constraint. `normalize_set_expr(dim, col)` is the single normalizer both engines use: a whole-cell null becomes the wildcard list from the previous section (`[unknown_sentinel_for(dim.data_type)]`), while a concrete list is passed through `canonicalize_set_expr`, which sorts and deduplicates it (`col.list.unique().list.sort()`). The operation is idempotent — normalizing an already-normalized column is a no-op — so it is safe to apply defensively without tracking whether a given column has already been through it.

The practical effect for authoring is: you may write a wildcard cell for a `SET_MEMBERSHIP`/`SET_EXCLUSION` dimension either as a plain null or as the explicit one-element sentinel list — both normalize to the same thing — and you never need to pre-sort or de-duplicate a concrete rule list yourself, since normalization runs before any comparison or combination happens. This normalization is also why the reserved-sentinel and null-element checks in "Set Wildcard Sentinel" matter: they run against the same in-band representation normalization produces, so the wildcard detection that both `SET_MEMBERSHIP` and `SET_EXCLUSION` rely on stays unambiguous no matter how a rule author originally wrote the cell.

---

This chapter turned an unlabeled DataFrame into a validated, serializable rule library: `Dimension` and `DimensionsMetadata` gave its columns meaning, every `MatchStrategy` member defined a precise ternary comparison rule, and the validator, `DataType` enum, temporal sentinels, YAML round-trip and set-wildcard machinery made that library safe to evolve and share. [Chapter 3](../03-evaluating-decisions/index.md) picks the library up from here — constructing an `ExpressionRulesEngine` from it, evaluating a context, and reading the resulting survivors, ranking and explanation.
