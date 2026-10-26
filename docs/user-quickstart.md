# User Quickstart: mountainash-rules

`mountainash_rules` matches a context (a customer, a loan application, a transaction) against rules that are rows of a table. Everything is imported from the package root. It offers two engines:

- **`ExpressionRulesEngine`** — the filter engine. It evaluates a context, or a batch of contexts, against a rules table in one vectorised pass and returns the surviving rules ranked by specificity.
- **`DecisionTable`** — a rules table *physicalised* into non-overlapping entries so that matching is a lookup. `prevalidate` checks the authored rules, `build_decision_table` builds the table, `DecisionTable.match` returns every matching entry, and the table saves, loads and verifies. `check_context_schema` and `validate_contexts` check contexts before matching.

Polars and Ibis-DuckDB are the supported and tested backends ([backend support](backend-support.md)).

---

## Installation

```bash
git clone https://github.com/mountainash-io/mountainash-utils-rules.git
cd mountainash-utils-rules
hatch env create
```

Resolve the configured `mountainash` dependency at delivered merge `cc9942c223b0d1f7521f987676623d4ba69ea7c6` or a descendant containing PRs #413, #414, #419 and #420, together with `mountainash-files`, `mountainash-transport`, `mountainash-data` and `mountainash-settings` (see `hatch.toml` and [backend support](backend-support.md)). This is a delivered dependency revision requirement, not an implied release version or checkout layout.

---

## The rule model

A rules table has one row per rule. Three declarations describe how a row is read; together they are `DimensionsMetadata`:

| Declaration | Purpose |
|---|---|
| `ContextField` | A typed value the context supplies: `name`, `data_type`, and for DECIMAL `precision`/`scale`/`rounding`, for DATETIME `datetime_unit`/`timezone`. It also declares the field's one **wildcard marker** and, optionally, a **truthy** mapping. |
| `Dimension` | One way of comparing a context field with a rule column: `dimension_name`, `context_field`, `match_strategy`, and the rule column (`rule_field`, default the dimension name; `range_min_field`/`range_max_field` for RANGE; `regex_pattern` for CONTEXT_REGEX). Several dimensions can read one field. |
| `DimensionsMetadata(fields=..., dimensions=...)` | The fields and dimensions. For a DecisionTable it also names `uuid_column`, `scope_column` and the `outputs`. |

### Ternary matching

Each dimension scores a rule **1** (match), **0** (unknown or don't-care) or **-1** (non-match). A rule survives when no dimension scores -1; its **specificity** is the number of dimensions that scored 1.

### Wildcards and null

- A rule cell that places no constraint holds the field's declared **wildcard marker**, for example `ContextField(name="region", data_type=DataType.STR, wildcard="*")`. A field has exactly one marker and every dimension on the field uses it. A wildcard scores 0.
- **Null is never an implicit wildcard.** To make null the marker, declare it: `ContextField(name="spend", data_type=DataType.INT, wildcard_declared=True)` (`wildcard` stays `None`). An unconstrained cell is then null, and for RANGE a marker endpoint is unbounded on that side. Without that declaration a null in a rule cell is invalid data, which `prevalidate` reports.
- A context value that is missing (an omitted key, an unset pydantic field, an absent column) or null scores 0 on a value dimension: the rule is possible, and earns no specificity.
- `PRESENCE` is the dimension that asks "is this field null?". Rule cells hold `IS_NULL`, `IS_NOT_NULL` or the wildcard; a context scores 1 for an explicit null, -1 for a supplied value, 0 when the field is missing.
- `UNKNOWN`, `UNKNOWN_NUMERIC`, `UNKNOWN_DATE`, `UNKNOWN_DATETIME` and `unknown_sentinel_for(data_type)` are conventional marker values you may declare as a field's `wildcard`. They have no meaning unless a field declares them.

---

## Part 1: ExpressionRulesEngine

### Step 1 — Define the rules

```python
import polars as pl

rules = pl.DataFrame({
    "rule_name":  ["premium_au", "standard_au", "global_fallback"],
    "region":     ["AU",         "AU",          "*"],
    "tier":       ["premium",    "*",           "*"],
    "spend_min":  pl.Series([1000, 0, None], dtype=pl.Int64),
    "spend_max":  pl.Series([9999, 9999, None], dtype=pl.Int64),
    "discount":   [15, 10, 0],
})
```

`"*"` is the wildcard of the two string fields and null is the wildcard of `spend`, as declared next. The rules may equally be a Mountainash `Relation` or an Ibis table on DuckDB.

### Step 2 — Declare fields and dimensions

```python
from mountainash_rules import (
    ContextField, DataType, Dimension, DimensionsMetadata, MatchStrategy,
)

metadata = DimensionsMetadata(
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="tier", data_type=DataType.STR, wildcard="*"),
        ContextField(name="spend", data_type=DataType.INT, wildcard_declared=True),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="tier", context_field="tier"),
        Dimension(
            dimension_name="spend", context_field="spend",
            match_strategy=MatchStrategy.RANGE,
            range_min_field="spend_min", range_max_field="spend_max",
        ),
    ),
)
```

`match_strategy` defaults to `EXACT`. `rule_field` defaults to the dimension name, so `region` reads the `region` column.

### Step 3 — Build the engine

```python
from mountainash_rules import ExpressionRulesEngine

engine = ExpressionRulesEngine(rules, metadata)
```

Expressions are compiled once, here; build the engine at startup and reuse it. Provide exactly one of `dimension_metadata` or `dimension_expressions` (hand-built Mountainash expressions, one per dimension). The engine does not validate the rules or the contexts: rules are evaluated as given and backend errors propagate as the backend's own errors. Run `prevalidate` (Part 2) on rules and `validate_contexts` on contexts when you want those checks.

### Step 4 — Evaluate a context

```python
from pydantic import BaseModel

class CustomerContext(BaseModel):
    region: str
    tier: str
    spend: int

result = engine.evaluate(CustomerContext(region="AU", tier="premium", spend=1500))
result = engine.evaluate({"region": "AU", "tier": "premium", "spend": 1500})  # a dict works too
```

For a pydantic model, only fields that were set are supplied; unset fields are missing (unknown).

### Step 5 — Read the result

`evaluate()` returns a `RuleResult`; its frames are in the backend of your rules.

```python
print(result.survivors.select("rule_name", "__specificity", "__rank"))
# premium_au       3  1
# standard_au      2  2
# global_fallback  0  3

result.best_match          # the top-ranked row: premium_au
result.count               # 3
result.explain("standard_au")   # {"region": 1, "tier": 0, "spend": 1}
result.at_least(2)         # premium_au and standard_au
```

`survivors` also carries the rule's own columns, `__rule_index`, and one `__t_<dimension>` ternary column per dimension (switch off with `include_observability=False`). The default `COLLECT` policy orders by specificity, highest first. `__rank` is 1-based and assigned before caller filters.

To see why a rule did not survive, `engine.explain(context)` scores every rule without filtering:

```python
engine.explain({"region": "AU", "spend": 5}).frame.select(
    "rule_name", "__t_region", "__t_tier", "__t_spend", "__survived", "__specificity"
)
# premium_au       1  0  -1  false  1
# standard_au      1  0   1  true   2
# global_fallback  0  0   0  true   0
```

### Missing and null context values

```python
result = engine.evaluate({"region": "AU", "spend": 1500})   # no tier
result.survivors.select("rule_name", "__specificity").rows()
# [("premium_au", 2), ("standard_au", 2), ("global_fallback", 0)]
```

`premium_au` stays possible because the missing `tier` scores 0, but it earns no specificity for that dimension. An explicit `None` behaves the same on a value dimension. A PRESENCE dimension tells the two apart (the worked example from the explicit don't-care principle, with its own rules and metadata):

```python
null_rules = pl.DataFrame({
    "rule_name":       ["R1", "R2", "R3"],
    "region":          ["EU", "*", "*"],
    "region_presence": ["*", "IS_NULL", "IS_NOT_NULL"],
    "vip":             [None, None, True],
})
null_metadata = DimensionsMetadata(
    fields=(
        ContextField(name="region", data_type=DataType.STR, wildcard="*"),
        ContextField(name="vip", data_type=DataType.BOOL, wildcard_declared=True),
    ),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(dimension_name="region_presence", context_field="region",
                  match_strategy=MatchStrategy.PRESENCE),
        Dimension(dimension_name="vip", context_field="vip"),
    ),
)
null_engine = ExpressionRulesEngine(null_rules, null_metadata)

for context in ({"region": "EU", "vip": True}, {"region": None, "vip": False}, {}):
    print(context, null_engine.evaluate(context).survivors.select("rule_name", "__specificity").rows())
# {'region': 'EU', 'vip': True}   [('R3', 2), ('R1', 1)]
# {'region': None, 'vip': False}  [('R2', 1), ('R1', 0)]
# {}                              [('R1', 0), ('R2', 0), ('R3', 0)]
```

An explicit null scores 1 for `IS_NULL` (R2) and 0 for the value dimension (R1 stays possible); an empty context leaves every dimension unknown.

### Limits and projections

```python
context = {"region": "AU", "tier": "premium", "spend": 1500}   # with the Step 1-3 engine
result = engine.evaluate(context, top_n=1)               # at most one row, after min_specificity
result = engine.evaluate(context, min_specificity=2)     # rows with at least two hard matches
result = engine.evaluate(context, dimensions=["region", "spend"])   # evaluate a subset
```

`top_n` and `min_specificity` must be non-negative integers or `None`. `dimensions=None` selects all dimensions in configured order; an unknown name raises `KeyError` and a malformed list `ValueError`.

### Hit policies

A hit policy chooses among the survivors. It is a **call-time** argument of `evaluate`, `evaluate_batch` and `RuleResult.select`, as are `priority_field` and `output_fields`:

| Policy | Result |
|---|---|
| `collect` (default) | every survivor, in specificity order |
| `rule_order` | every survivor, in rule order |
| `first` | one survivor; rule order wins |
| `priority` | one survivor; the highest `priority_field` wins |
| `unique` | at most one survivor, else `HitPolicyViolationError` |
| `any` | survivors must agree on `output_fields`, else `HitPolicyViolationError`; one is returned |

```python
engine.evaluate(context, hit_policy="first")
engine.evaluate(context, hit_policy="priority", priority_field="discount")
engine.evaluate(context, hit_policy="unique")        # raises HitPolicyViolationError: 3 survive
engine.evaluate(context, hit_policy="any", output_fields=["discount"])   # raises: outputs disagree
```

`output_fields` names the rule columns that `any` compares; it is not a projection. Policies accept `HitPolicy` members or their lowercase string values.

`result.select(policy, priority_field=None, output_fields=None)` re-applies a policy to an existing result, provided the result still holds its complete candidate basis: unfiltered `collect`, `rule_order` and successful `unique` results can be re-selected; a result created with `top_n`, `min_specificity`, `first`, `priority` or `any` cannot. Re-evaluate instead.

### Batches

```python
contexts = pl.DataFrame({
    "cid":    ["B", "A", "C"],
    "region": ["AU", "NZ", "AU"],
    "tier":   ["premium", "standard", None],
    "spend":  [1500, 50, None],
})
batch = engine.evaluate_batch(contexts, context_id_field="cid", chunk_size=2)

batch.survivors            # __context_id, rule columns, __rank, __specificity, ...
batch.matched_context_ids  # ["A", "B", "C"]
batch.unmatched_context_ids(contexts)   # []
batch.best_matches         # top-ranked row per context
batch.counts_per_context
batch.for_context("B")     # a RuleResult for one context
```

`evaluate_batch(contexts, *, context_id_field=None, dimensions=None, hit_policy=None, priority_field=None, output_fields=None, top_n_per_context=None, min_specificity=None, include_observability=True, chunk_size=None)` scores every context against every rule in one pass and ranks per context. `chunk_size` bounds the cross-product per chunk. Without `context_id_field`, ids are the zero-based row positions. The engine does no identity checking: ids you supply must be non-null and unique, which `validate_contexts` checks for a `request_id` column. A one-row batch agrees with the single-context `evaluate`.

### Rules as a Relation, and other backends

`ExpressionRulesEngine` accepts a Mountainash `Relation`. Contexts in Polars, on the same connection, or on another connection are joined to Relation rules on the rules' side (`execute_on`), so the rules never leave their backend. See [backend support](backend-support.md).

---

## Match strategies

| Strategy | Rule value | Scores 1 when |
|---|---|---|
| `exact`, `not_equal` | a value of the field's type | the context equals / differs from it |
| `range` | two columns, `range_min_field`, `range_max_field` | the context lies in the closed interval; a marker endpoint is unbounded |
| `min`, `max` | a threshold | the context is `>=` / `<=` it (inclusive) |
| `prefix`, `suffix`, `contains` | a string | the context string starts with, ends with or contains it |
| `regex` | a per-rule pattern column | the context string matches the rule's pattern |
| `context_regex` | `regex_pattern` on the Dimension | the context string matches that one pattern; 0 for a missing context value |
| `set_membership`, `set_exclusion` | a list column | the context is in / not in the list; null elements in a list are invalid |
| `truthy` | a value mapped through the field's `truthy` mapping | the rule's and the context's mapped outcomes are equal and non-zero |
| `presence` | `IS_NULL`, `IS_NOT_NULL` or the wildcard | see the null model above |
| `outcome` | an int8 ternary (-1, 0, 1) | the context supplies an already computed ternary and the two are equal and non-zero |

`OUTCOME` scores 0 when either outcome is 0, including when the field explicitly declares 0 as its wildcard.

`min`, `max` and `range` need a numeric or temporal field. FLOAT fields cannot be ordered; use DECIMAL. The string strategies need a STR field. Patterns use Mountainash `str.regex_contains`.

```python
# A truthy field: its mapping gives each value (including None) a ternary outcome.
ContextField(name="vip", data_type=DataType.STR, truthy={"yes": 1, "no": -1, None: -1})
Dimension(dimension_name="vip", context_field="vip", rule_field="required",
          match_strategy=MatchStrategy.TRUTHY)
```

Only available context values participate in non-null truthy mappings. For example, a DECIMAL mapping containing `Decimal("0") → 1` matches a supplied zero, not an omitted value, explicit null or declared wildcard. Explicit null uses a declared `None` mapping independently of mapping order; omission is always unknown. This applies to both the filter engine and DecisionTable matching.

### Field types

| `DataType` | Notes |
|---|---|
| `STR`, `INT`, `FLOAT`, `BOOL` | FLOAT supports categorical matching, not ordering |
| `DECIMAL` | requires `precision` (1–16), `scale` and `rounding` (`"half_to_even"` or `"half_away_from_zero"`); context values are reduced to the declared scale with that rounding when bound |
| `DATE`, `DATETIME` | DATETIME requires `datetime_unit` (`"s"`, `"ms"`, `"us"`) and `timezone` (`"naive"`, `"utc"`); context values are truncated to the unit when bound. An omitted or null DATETIME value is unknown (0) for either timezone, including an untyped null column |

```python
from decimal import Decimal

decimal_rules = pl.DataFrame({
    "rule": ["singleton", "upper"],
    "lo": [Decimal("0.004"), Decimal("0.010")],
    "hi": [Decimal("0.004"), Decimal("0.020")],
})
decimal_metadata = DimensionsMetadata(
    fields=(ContextField(name="quoted", data_type=DataType.DECIMAL,
                         precision=10, scale=3, rounding="half_to_even",
                         wildcard_declared=True),),
    dimensions=(Dimension(dimension_name="amount", context_field="quoted",
                          match_strategy=MatchStrategy.RANGE,
                          range_min_field="lo", range_max_field="hi"),),
)
decimal_engine = ExpressionRulesEngine(decimal_rules, decimal_metadata)
decimal_engine.evaluate({"quoted": Decimal("0.00449")})   # "singleton": rounds to 0.004
decimal_engine.evaluate({"quoted": Decimal("0.00451")})   # rounds to 0.005: no survivor
```

### Boolean inputs

A BOOL field reads actual booleans. `BooleanCoercion` widens the original values the engine reads as true or false; combine flags with `|` and pass them to the constructor:

```python
from mountainash_rules import BooleanCoercion

engine = ExpressionRulesEngine(
    rules, metadata,
    boolean_coercion=BooleanCoercion.BOOLEAN_TEXT | BooleanCoercion.BINARY_NUMBERS,
)
```

| Flag | Additional values read as Boolean |
|---|---|
| `BINARY_NUMBERS` | numeric `0` and `1` |
| `BOOLEAN_TEXT` | `"true"`/`"false"` in any ASCII case, `"1"`, `"0"` |
| `NUMERIC_TRUTHINESS` | any finite number (0 false, others true) |

A value that is not read as Boolean is unknown (0); the engine does not reject it. Use `validate_contexts` to check the data you pass in.

---

## Part 2: DecisionTable

A `DecisionTable` serves rules with five layers, each with its own input and output:

| # | Layer | Entry point |
|---|---|---|
| 1 | Prevalidation | `prevalidate(rules, metadata) -> ValidatedRules` |
| 2 | Construction | `build_decision_table(validated) -> DecisionTable` |
| 3 | Post-validation | deferred; not provided |
| 4 | Application | `DecisionTable.match(contexts, scopes=None).fetch() -> MatchResult` |
| 5 | Business post-aggregation | your own code over `MatchResult.matches` |

Context validation (`check_context_schema`, `validate_contexts`) runs before layer 4. Layers are separate calls; `match` does not validate.

### Example: a home-loan rate

Three **scopes** produce three outputs: `base_rate`, `advertised_margin`, `discretionary_margin`. Each rule belongs to one scope, has a UUID, and contributes a value to its scope's output.

```python
from decimal import Decimal

import polars as pl
from mountainash.relations import relation

from mountainash_rules import (
    Aggregate, ContextField, DataType, Dimension, DimensionsMetadata, MatchStrategy,
    build_decision_table, prevalidate,
)

def margin(name):
    return Aggregate(
        scope=name, output_name=name, contribution_column=name,
        data_type=DataType.DECIMAL, precision=8, scale=4, rounding="half_to_even",
    )

metadata = DimensionsMetadata(
    uuid_column="rule_id",
    scope_column="scope",
    fields=(
        ContextField(name="product", data_type=DataType.STR, wildcard="*"),
        ContextField(name="credit_band", data_type=DataType.STR, wildcard="*"),
        ContextField(name="loan_amount", data_type=DataType.INT, wildcard_declared=True),
    ),
    dimensions=(
        Dimension(dimension_name="product", context_field="product"),
        Dimension(dimension_name="credit_band", context_field="credit_band"),
        Dimension(
            dimension_name="loan_amount", context_field="loan_amount",
            match_strategy=MatchStrategy.RANGE,
            range_min_field="amount_min", range_max_field="amount_max",
        ),
    ),
    outputs=(margin("base_rate"), margin("advertised_margin"), margin("discretionary_margin")),
)

def amounts(*values):
    return pl.Series(values, dtype=pl.Decimal(8, 4))

rules = pl.DataFrame({
    "rule_id":     ["B1", "A1", "A2", "D1", "D2"],
    "scope":       ["base_rate", "advertised_margin", "advertised_margin",
                    "discretionary_margin", "discretionary_margin"],
    "product":     ["*", "variable", "variable", "*", "*"],
    "credit_band": ["*", "*", "*", "A", "*"],
    "amount_min":  pl.Series([None, None, 500_000, None, None], dtype=pl.Int64),
    "amount_max":  pl.Series([None, 499_999, None, None, None], dtype=pl.Int64),
    "base_rate":            amounts(Decimal("0.04"), None, None, None, None),
    "advertised_margin":    amounts(None, Decimal("0.01"), Decimal("0.0075"), None, None),
    "discretionary_margin": amounts(None, None, None, Decimal("0.0025"), Decimal("0")),
})
```

### Layer 1 — Prevalidate

```python
validated = prevalidate(relation(rules), metadata)   # ValidatedRules(rules, source_metadata, warnings)
```

`prevalidate` captures the rules once and checks declared columns and types, UUID and scope identity, field interpretation, contribution data and strategy/type pairings. It collects every breach. Errors raise `DataContractError`:

```python
from mountainash_rules import DataContractError

conflicting = rules.with_columns(pl.lit("*").alias("credit_band"))   # D1 and D2 now differ only in output
try:
    prevalidate(relation(conflicting), metadata)
except DataContractError as error:
    error.breaches    # (Finding(kind='conflicting_rule', severity='error',
                      #          detail="Conflicting normalized rules in scope 'discretionary_margin'",
                      #          rule_uuids=('D1', 'D2')),)
    error.warnings    # non-blocking findings found at the same time
```

Rules with the same normalized conditions are duplicates (same outputs) or conflicts (different outputs); both block construction, as do duplicate UUIDs within a scope, invalid contributions, and incompatible interpretations of one field. A rule that can never match, or that combines `IS_NULL` with a concrete value on one field, is a warning in `validated.warnings`; construction continues. A `Finding` has `kind`, `severity`, `detail` and `rule_uuids`.

### Layer 2 — Build

```python
table = build_decision_table(validated)
```

Construction receives no context. It cuts each scope's rules into **entries**: one row per covered cell, with the cell's condition columns and the folded outputs. Where several rules cover a cell, their contributions to the same output are folded (`SUM`, `MIN` or `MAX`, per `Aggregate.operation`, default `SUM`), once per contributing rule.

| Attribute | Content |
|---|---|
| `table.entries` | one row per entry: `entry_id`, `scope`, `entry_sources_id`, condition columns, outputs |
| `table.entry_sources` | `(scope, entry_sources_id, source_uuid)`: which source rules built each entry |
| `table.pattern_conditions` | what each derived pattern column means |
| `table.source_metadata` | the metadata of the authored rules |
| `table.build_id` | a hash of the rules and metadata |

`entries` and `entry_sources` are Mountainash relations on the backend that built them. Join `entry_sources` when you need the source-rule identities of a match.

### Check the contexts

A context is a pydantic object with `request_id` and attributes named after the context fields (no base class is needed), or a Relation with a `request_id` column and a column per field.

```python
from pydantic import BaseModel

from mountainash_rules import check_context_schema, validate_contexts

class LoanApplication(BaseModel):
    request_id: str
    product: str | None = None
    credit_band: str | None = None
    loan_amount: int | None = None

check_context_schema(LoanApplication, metadata)       # compares types with the field declarations

applications = [
    LoanApplication(request_id="r1", product="variable", credit_band="A", loan_amount=300_000),
]
validate_contexts(applications, metadata)             # returns its input, or raises ValueError
```

`check_context_schema(schema_or_class, metadata)` compares a pydantic class, a Relation schema or a mapping with the metadata without reading any row. A type mismatch is an error and raises one `ValueError` listing them all; a declared field absent from the schema, or a DECIMAL scale finer than declared, is a warning (`warnings.warn`).

`validate_contexts(contexts, metadata)` checks a batch relationally: null or duplicate `request_id`, non-finite numbers, values no truthy mapping covers, a wildcard marker embedded among other list elements or also mapped by a truthy mapping, and null elements inside lists. One `ValueError` lists every invalid value. A context null is *not* an error: it is unknown. The caller's own pydantic model remains the place for business constraints.

### Layer 4 — Match

```python
operation = table.match(applications)           # deferred; scopes=None means every scope
result = operation.fetch()                      # MatchResult(matches=<Relation>)

for row in result.matches.to_dicts():
    print(row["scope"], row["entry_id"], row["specificity"],
          row["base_rate"], row["advertised_margin"], row["discretionary_margin"])
# (row order is not guaranteed)
# advertised_margin     0  2  None  Decimal('0.0100')  None
# discretionary_margin  4  2  None  None               Decimal('0.0025')
# base_rate             2  1  Decimal('0.0400')  None  None
# discretionary_margin  3  1  None  None               Decimal('0.0000')
```

`table.match(contexts, scopes=["base_rate"])` restricts the match to the named scopes. `matches` has one record per matching entry per request: `request_id`, `scope`, `entry_id`, `entry_sources_id`, `specificity`, the entry's condition columns and its logical outputs (columns of other scopes are null). A request with no matching entry has no record. Matching does not fold, rank-filter or choose among entries; entries in one scope can overlap on some dimensions, and choosing among them is layer 5. Specificity is the engine's count of dimensions that scored 1, so an ordered dimension counts whenever the context falls inside the entry's interval.

`fetch()` raises `DecisionTableError` for backend failures. Contexts built from Python objects are a small in-process relation; the join with the entries runs on the entries' backend, which the contexts are copied to, and the entries never move. `match` does not check that `request_id` values are unique; `validate_contexts` does. Backend executions are logged at stage boundaries by the `mountainash_rules.decision_table.build` and `.apply` loggers.

### Save, load and verify

```python
table.save("/data/rates/home_loans")                    # a directory; replaces an earlier save
loaded = DecisionTable.load("/data/rates/home_loans")   # entries load into in-process Polars

import ibis
connection = ibis.duckdb.connect("rates.duckdb")
on_duckdb = DecisionTable.load("/data/rates/home_loans", connection=connection)

from mountainash_rules import verify_saved_table
report = verify_saved_table("/data/rates/home_loans")   # VerificationReport
report.ok, report.build_id, report.mismatched, report.missing
```

A saved table is eight Parquet files (`entries`, `entry_sources`, `pattern_conditions` and the five flat metadata tables `ruleset`, `fields`, `dimensions`, `outputs`, `truthy`) and a `manifest.json` recording the `build_id` and each file's size and SHA-256. The manifest is written last. Paths may be local or anything `mountainash-transport` resolves (for example `s3://bucket/tables/home_loans`); pass `storage_profile` (a profile name or object) for remote storage. `load` reads without checking; `verify_saved_table` compares each listed file's bytes with the manifest and reports every mismatched or missing file, and a missing manifest raises. With a `connection`, keep it open while the table is in use.

`DimensionsMetadata.to_tables()` and `DimensionsMetadata.from_tables(tables)` convert metadata to and from those five relations.

### Layer 5 — Business post-aggregation

The application layer returns matching entries; interpreting several of them is the business domain's job. A home-loan rate combines three outputs, each chosen from its own scope. The records below stand in for `MatchResult.matches` rows (trimmed to the columns used) so the example is self-contained. **Choosing the highest-specificity entry of each scope, and adding the three values, is this example's rule — not DecisionTable behaviour.**

```python
from decimal import Decimal

# Illustrative MatchResult.matches records for one application: every matching entry,
# with its entry_id, scope, specificity and the scope's output value.
matches = [
    {"entry_id": 2, "scope": "base_rate",            "specificity": 1, "value": Decimal("0.04")},
    {"entry_id": 0, "scope": "advertised_margin",    "specificity": 2, "value": Decimal("0.01")},
    {"entry_id": 3, "scope": "discretionary_margin", "specificity": 1, "value": Decimal("0.0000")},
    {"entry_id": 4, "scope": "discretionary_margin", "specificity": 2, "value": Decimal("0.0025")},
]

# Illustrative downstream step: per output scope, keep the highest-specificity entry.
chosen = {}
for entry in matches:
    best = chosen.get(entry["scope"])
    if best is None or entry["specificity"] > best["specificity"]:
        chosen[entry["scope"]] = entry

# Domain formula: the application rate is the sum of the three selected values.
rate = (
    chosen["base_rate"]["value"]
    + chosen["advertised_margin"]["value"]
    + chosen["discretionary_margin"]["value"]
)
assert rate == Decimal("0.0525")   # 0.04 + 0.01 + 0.0025 = 5.25%
```

With the Part 2 table, `result.matches.to_dicts()` gives the same four entries: each output column of a record is non-null only for the record's own scope, so `value` above is `row[row["scope"]]`. A different business might pick the lowest margin, require a single entry, or take the entry with the greatest discount; none of that changes what `match` returns.

---

## Troubleshooting

- **A rule never wins.** Check `engine.explain(context)` for a `-1` in one `__t_<dimension>` column.
- **`DataContractError`.** Read `error.breaches`: each `Finding` names the kind and the rule UUIDs.
- **A field is ignored.** A context field absent from the context is unknown; `check_context_schema` warns when a declared field is missing from the context schema.
- **`HitPolicyViolationError`.** `unique` and `any` raise when the survivors do not satisfy them; `error.offending` holds the survivor rows.
