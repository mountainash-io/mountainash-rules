---
title: "Chapter 8: Validating Contexts, Matching and Persisting a DecisionTable"
description: "Layer 4 and its companions: context validation, DecisionTable.match, saving and loading, and the layer 5 worked example."
---

# Chapter 8: Validating Contexts, Matching and Persisting a DecisionTable

A built `DecisionTable` serves contexts. This chapter checks contexts before matching, matches them, saves and reloads the table, and closes with the fifth layer: the business calculation that your own code performs over the matches.

## The example table

The running example is a home-loan rate. Three scopes produce three outputs: a base rate, an advertised margin and a discretionary margin.

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
table = build_decision_table(prevalidate(relation(rules), metadata))
```

## Contexts

A context is identified by a `request_id` and carries one attribute (or column) per context field it supplies. There is no base class and no generated model. Two forms are accepted:

- **A list of pydantic objects** with a `request_id` attribute. A field the object leaves unset is missing (unknown), which is different from an explicit `None`.
- **A Relation** (or native table) with a `request_id` column and a column per supplied field. An absent column is not supplied; a null cell is an explicit null.

```python
from pydantic import BaseModel

class LoanApplication(BaseModel):
    request_id: str | None = None
    product: str | None = None
    credit_band: str | None = None
    loan_amount: int | None = None

applications = [
    LoanApplication(request_id="r1", product="variable", credit_band="A", loan_amount=300_000),
    LoanApplication(request_id="r2", product="fixed", loan_amount=600_000),
    LoanApplication(request_id="r3"),
]
```

## Context validation

`DecisionTable.match` evaluates the contexts it is given and checks nothing, as does the expression engine. Checking is a separate capability with three levels, all aligned with the metadata:

1. **Your own pydantic model** validates each instance: types, required fields, business constraints. It knows nothing about rules.
2. **`check_context_schema(schema_or_class, metadata)`** compares a pydantic class, a Relation's schema or a mapping with the declared context fields, without reading any data.
3. **`validate_contexts(contexts, metadata)`** checks a batch relationally.

```python
import warnings
from mountainash_rules import check_context_schema, validate_contexts

check_context_schema(LoanApplication, metadata)           # aligned: returns None

class Typed(BaseModel):
    request_id: str
    product: str
    credit_band: str
    loan_amount: str                                       # declared as INT

try:
    check_context_schema(Typed, metadata)
except ValueError as error:
    print(error)
```

```text
context schema does not align with the metadata:
- field 'loan_amount': schema type str does not match declared int
```

`check_context_schema` raises **one** `ValueError` listing every mismatch: field types, DATETIME unit and timezone, wildcard markers and truthy values that the type cannot represent, and a missing `request_id`. Two conditions only warn (`warnings.warn`): a declared field that is absent from the schema, whose dimension is then unknown at evaluation, and a DECIMAL scale finer than declared, which the engine reduces at binding using the declared rounding.

```python
validated = validate_contexts(applications, metadata)     # returns its input unchanged

duplicated = [LoanApplication(request_id="x"), LoanApplication(request_id="x")]
try:
    validate_contexts(duplicated, metadata)
except ValueError as error:
    print(error)

frame = pl.DataFrame({"request_id": ["a", "b"], "loan_amount": [10.0, float("nan")]})
try:
    validate_contexts(relation(frame), metadata)
except ValueError as error:
    print(error)
```

```text
contexts are invalid:
- 'request_id' must be unique; duplicated: 'x'
contexts are invalid:
- field 'loan_amount': 1 value(s) is not finite; request_id 'b'
```

`validate_contexts` returns its input exactly as passed, or raises one `ValueError` that lists every invalid value. It checks a non-null, unique `request_id`; non-finite numbers; values that a field's truthy mapping does not cover; wildcard markers that are embedded among other list elements or that a truthy mapping also maps; and null elements inside list values. A context **null is not an error**: it is unknown. Lists of objects are converted with the same converter that `match` uses, so validation sees what matching will see.

## Layer 4: match

```python
operation = table.match(applications)                     # deferred
result = operation.fetch()                                # MatchResult(matches=<Relation>)
matches = result.matches.to_polars().sort("request_id", "scope", "entry_id")
print(matches.select("request_id", "scope", "entry_id", "specificity").rows())
```

```text
[('r1', 'advertised_margin', 0, 2), ('r1', 'base_rate', 2, 1), ('r1', 'discretionary_margin', 3, 1), ('r1', 'discretionary_margin', 4, 2), ('r2', 'base_rate', 2, 1), ('r2', 'discretionary_margin', 3, 1), ('r2', 'discretionary_margin', 4, 1), ('r3', 'advertised_margin', 0, 0), ('r3', 'advertised_margin', 1, 0), ('r3', 'base_rate', 2, 0), ('r3', 'discretionary_margin', 3, 0), ('r3', 'discretionary_margin', 4, 0)]
```

`table.match(contexts, scopes=None)` returns a deferred `MatchOperation`; `fetch()` runs it and returns `MatchResult(matches)`. `matches` has one record for **every** matching entry of every request:

| Column | Meaning |
|---|---|
| `request_id` | The request that matched. |
| `scope`, `entry_id` | The entry's scope and id. |
| `entry_sources_id` | The set of source rules behind the entry; join `table.entry_sources` for their UUIDs. |
| `specificity` | The number of dimensions that scored 1 for this request. |
| condition columns | The entry's cell, for example `loan_amount__lo` and `loan_amount__hi`. |
| outputs | The folded output of the entry's scope; the outputs of other scopes are null. |

Read the result:

- Application returns **every** matching entry. It does not fold them, rank-filter them or choose among them. Entries of one scope can overlap across dimensions, and which to prefer is a business decision (layer 5).
- Request `r1` matches the advertised-margin entry for amounts under 500,000 (specificity 2: product and amount range), the base-rate entry (1: only the amount range scores 1, product and credit band are wildcards) and two discretionary-margin entries: the credit band A one (specificity 2) and the wildcard one (1).
- `r3` supplied nothing, so every entry is *possible* at specificity 0, including both advertised-margin entries ([Chapter 3](../03-matching-concepts/index.md): missing is unknown, not a mismatch).
- A request that matches no entry has no record, and an empty request list gives an empty result.
- Specificity counts every dimension that scored 1, so an ordered dimension counts whenever the context falls inside the entry's interval, even when the rule did not constrain it.

### Scopes

```python
only_base = table.match(applications, scopes=["base_rate"]).fetch().matches
print(only_base.count_rows())
```

```text
3
```

`scopes` restricts matching to the named scopes; an unknown scope raises `DecisionTableError`. Selected scopes are filtered *before* the single engine call.

### How it executes

Application calls the expression engine once over the selected entries. The int8 outcome columns of `entries` (patterns, TRUTHY and PRESENCE) are scored from the contexts first and handed to the engine as context outcomes ([Chapter 10](../10-decision-table-internals/index.md)). Contexts that originate in Python are a small in-memory relation; every join with the entries states `execute_on` on the entries' side, so the contexts are copied to the entries' backend and the entries never leave it. Backend errors surface as `DecisionTableError`. Backend execution counts are logged at stage boundaries by `mountainash_rules.decision_table.apply`.

A table can be built on Ibis-DuckDB by passing a relation over a DuckDB table to `prevalidate`:

```python
import ibis

connection = ibis.duckdb.connect()
duck_rules = relation(connection.create_table("rules", rules))
duck_table = build_decision_table(prevalidate(duck_rules, metadata))
print(duck_table.build_id == table.build_id, duck_table.match(applications).fetch().matches.count_rows())
```

```text
True 12
```

The same rules and metadata give the same `build_id` on both backends, and the same matches.

## Persistence

```python
import tempfile
from pathlib import Path

from mountainash_rules import DecisionTable, verify_saved_table

location = str(Path(tempfile.mkdtemp()) / "home_loans")
table.save(location)
report = verify_saved_table(location)
print(report.ok, report.build_id == table.build_id, report.mismatched, report.missing)

loaded = DecisionTable.load(location)
print(loaded.build_id == table.build_id, loaded.match(applications).fetch().matches.count_rows())
```

```text
True True () ()
True 12
```

`save(path, *, storage_profile=None)` writes a directory of eight Parquet files plus a manifest:

| File | Content |
|---|---|
| `entries.parquet`, `entry_sources.parquet`, `pattern_conditions.parquet` | The construction relations. |
| `ruleset`, `fields`, `dimensions`, `outputs`, `truthy` (`.parquet`) | The five flat metadata tables ([Chapter 2](../02-shared-rule-model/index.md)). |
| `manifest.json` | The `build_id` and, for each data file, its name, byte size and SHA-256. |

Each data file is written independently, replacing any earlier file of that name, and the **manifest is written last**. A save that was interrupted therefore has no manifest, or has the manifest of an earlier save. `path` can be a local directory or anything `mountainash-transport` resolves (for example `s3://bucket/tables/home_loans`), with `storage_profile` naming or supplying the storage profile. Files are exchanged through `mountainash-files`.

`DecisionTable.load(path, *, connection=None, storage_profile=None)` reads the manifest's `build_id` and the eight files by name, **without checking them**. With no `connection` the three construction relations load into in-process Polars; with an Ibis-DuckDB `connection` they load onto it, and you keep the connection open while the table is in use. The five metadata tables always load host-side. `table.entry_metadata` is derived from the saved parts, so a loaded table reads exactly as the built one did.

`verify_saved_table(path, *, storage_profile=None)` is the separate, optional integrity check. It compares each listed file's size and SHA-256 with the manifest and returns a `VerificationReport(build_id, mismatched, missing)` with an `ok` property; it loads no relations. A missing or unreadable manifest, and storage errors other than a file being absent, raise.

## Layer 5: business post-aggregation

Application returns matching entries. How several of them combine is the business domain's job. A home-loan rate takes the output of each of its three scopes and adds them up. The records below stand in for `MatchResult.matches` rows for one application, trimmed to the columns used, so the example is self-contained.

!!! note "Illustrative, not DecisionTable behaviour"
    Choosing the highest-specificity entry of each scope, and adding the three values, is *this example's* rule. A different business might pick the lowest margin, require a single entry, or weight the values. None of that changes what `match` returns.

```python
from decimal import Decimal

# Illustrative MatchResult.matches records: every matching entry, with its entry_id,
# scope, specificity and the scope's output value.
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
print(rate)
```

```text
0.0525
```

The result is `0.04 + 0.01 + 0.0025 = 0.0525`, or 5.25%. The records follow request `r1` of the table above: its scope outputs live in the `base_rate`, `advertised_margin` and `discretionary_margin` columns of `result.matches`, where only the record's own scope column is non-null, so `value` is `row[row["scope"]]`. The discretionary scope has two matching entries; the example keeps entry 4 (credit band A, specificity 2) over the wildcard entry 3 (specificity 1).

Each record also carries `entry_id` and `entry_sources_id`, so a downstream step can always trace its choice back to the authored rules through `table.entry_sources`.

## Next steps

[Chapter 9](../09-expression-engine-internals/index.md) and [Chapter 10](../10-decision-table-internals/index.md) look inside the two engines.

## Source references

- `src/mountainash_rules/context_validation.py` — `check_context_schema`, `validate_contexts`.
- `src/mountainash_rules/decision_table/apply.py` — `MatchOperation`, `MatchResult`, `contexts_to_relation`.
- `src/mountainash_rules/decision_table/persistence.py` — `save_table`, `load_table`, `verify_saved_table`, `VerificationReport`.
