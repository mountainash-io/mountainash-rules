---
title: "Chapter 2: The Shared Rule Model: Tables, Contexts and Dimensions"
description: "Declare context fields, dimensions and metadata once; both engines read the same declarations."
---

# Chapter 2: The Shared Rule Model: Tables, Contexts and Dimensions

Both engines read the same three declarations: `ContextField`, `Dimension` and `DimensionsMetadata`. They are plain pydantic models, so a mistake in them fails when you construct them, before any rule is evaluated.

## ContextField: a typed fact

A `ContextField` describes one value that a context supplies.

```python
from mountainash_rules import ContextField, DataType

region = ContextField(name="region", data_type=DataType.STR, wildcard="*")
spend = ContextField(name="spend", data_type=DataType.INT, wildcard_declared=True)
amount = ContextField(
    name="amount", data_type=DataType.DECIMAL,
    precision=10, scale=2, rounding="half_to_even",
)
```

| Attribute | Meaning |
|---|---|
| `name` | The context key (a dictionary key, pydantic attribute or context-table column). |
| `data_type` | A `DataType`: `STR`, `INT`, `FLOAT`, `DECIMAL`, `BOOL`, `DATE` or `DATETIME`. A Python type such as `str` or `int` is accepted and converted. |
| `precision`, `scale`, `rounding` | Required for `DECIMAL` (precision 1–16, scale not above precision, rounding `"half_to_even"` or `"half_away_from_zero"`) and invalid for any other type. |
| `datetime_unit`, `timezone` | Required for `DATETIME` (`"s"`, `"ms"` or `"us"`; `"naive"` or `"utc"`) and invalid otherwise. |
| `wildcard` | The one marker a rule cell holds to say "no constraint". |
| `wildcard_declared` | Set it to declare that the wildcard is `None` (null). Setting `wildcard` to a value implies it. |
| `truthy` | An optional mapping from values (including `None`) to the outcomes `-1`, `0` or `1`, used by `TRUTHY` dimensions. |

The `FLOAT` type matches categorically but cannot be ordered; use `DECIMAL` for fractional ranges and thresholds. Context values for a `DECIMAL` field are reduced to the declared scale with the declared rounding, and `DATETIME` values are truncated to the declared unit, when the engine binds them.

A field declares **one** wildcard. Every dimension on the field uses it, so a `"*"` in the `region` column means the same thing for every comparison that reads `region`. A field that declares neither `wildcard` nor `wildcard_declared` has no wildcard, and every cell of a rule column read through it must hold a concrete value. [Chapter 3](../03-matching-concepts/index.md) covers null in detail.

The package also exports conventional marker values (`UNKNOWN` is `"<NA>"`, `UNKNOWN_NUMERIC`, `UNKNOWN_DATE`, `UNKNOWN_DATETIME` and `unknown_sentinel_for(data_type)`). They carry no meaning of their own: a value is a wildcard only where a field declares it.

## Dimension: one comparison

A `Dimension` connects a context field to a rule column through a match strategy.

```python
from mountainash_rules import Dimension, MatchStrategy

Dimension(dimension_name="region", context_field="region")           # EXACT by default
Dimension(
    dimension_name="spend", context_field="spend",
    match_strategy=MatchStrategy.RANGE,
    range_min_field="spend_min", range_max_field="spend_max",
)
Dimension(
    dimension_name="region_presence", context_field="region",
    match_strategy=MatchStrategy.PRESENCE,
)
```

| Attribute | Meaning |
|---|---|
| `dimension_name` | The dimension's name; it labels the `__t_<name>` ternary column and the `explain` output. Unique within the metadata. |
| `context_field` | The `ContextField` the dimension reads. |
| `rule_field` | The rule column holding the condition. Defaults to `dimension_name`. |
| `match_strategy` | A `MatchStrategy` (default `EXACT`). |
| `range_min_field`, `range_max_field` | For `RANGE`: the two endpoint columns. Both are required. |
| `regex_pattern` | For `CONTEXT_REGEX`: the single pattern applied to the context value. Invalid for every other strategy. |

Several dimensions can read one field: an `EXACT` dimension and a `PRESENCE` dimension on `region`, or a `RANGE` and a `MIN` on `term`. The strategies and their type requirements are the subject of [Chapter 3](../03-matching-concepts/index.md).

## DimensionsMetadata: the whole declaration

```python
from mountainash_rules import DimensionsMetadata

metadata = DimensionsMetadata(
    fields=(region, spend),
    dimensions=(
        Dimension(dimension_name="region", context_field="region"),
        Dimension(
            dimension_name="spend", context_field="spend",
            match_strategy=MatchStrategy.RANGE,
            range_min_field="spend_min", range_max_field="spend_max",
        ),
    ),
)
```

`DimensionsMetadata` checks, at construction, that:

- every dimension names a declared context field;
- field and dimension names are unique, and a field declared twice is declared identically;
- `MIN`, `MAX` and `RANGE` read numeric or temporal fields, and `FLOAT` is not ordered;
- the string strategies (`REGEX`, `CONTEXT_REGEX`, `PREFIX`, `SUFFIX`, `CONTAINS`) read `STR` fields;
- output declarations are unique within a scope.

It also carries three attributes that only a DecisionTable uses: `uuid_column` (the rule identity column), `scope_column` (the column that groups rules into scopes) and `outputs` (a tuple of `Aggregate` declarations). The expression engine ignores them. Hit policy, priority field and output fields are *not* metadata; they are arguments of each evaluation call ([Chapter 5](../05-expression-results-and-policies/index.md)).

`get_dimension(name)` returns one dimension; an unknown name raises `KeyError`.

## Aggregate: an output fold

An `Aggregate` declares one logical output of a scope, for a DecisionTable.

```python
from mountainash_rules import Aggregate, AggregateOp

fee = Aggregate(
    scope="fees", output_name="fee", contribution_column="fee",
    operation=AggregateOp.SUM, data_type=DataType.INT,
)
```

Rules in scope `fees` supply a `fee` contribution in the `fee` column; the output named `fee` is the `SUM` (default), `MIN` or `MAX` of the contributions of all rules that cover a cell. A `DECIMAL` output requires `precision`, `scale` and `rounding`; a `DATETIME` output requires `timezone`.

## Metadata as five flat tables

Metadata converts to and from five flat relations, which is how a DecisionTable persists it.

```python
tables = metadata.to_tables()        # dict of Relations
same = DimensionsMetadata.from_tables(tables)
assert same == metadata
```

| Table | One row per | Columns |
|---|---|---|
| `ruleset` | ruleset | `uuid_column`, `scope_column` |
| `fields` | context field | `name`, `data_type`, `precision`, `scale`, `rounding`, `datetime_unit`, `timezone`, `wildcard`, `wildcard_declared` |
| `dimensions` | dimension | `dimension_name`, `context_field`, `rule_field`, `match_strategy`, `range_min_field`, `range_max_field`, `regex_pattern` |
| `outputs` | output | `scope`, `output_name`, `contribution_column`, `operation`, `data_type`, `precision`, `scale`, `rounding`, `timezone` |
| `truthy` | truthy mapping entry | `context_field`, `value`, `outcome` |

Typed values, such as a wildcard marker or a truthy key, are stored as canonical text and parsed with the field's declared type on the way back, so a declared wildcard and a declared null wildcard both survive a round trip.

## Rules and contexts

**Rules.** The rules table can be a Polars frame, an Ibis table on DuckDB, or a Mountainash `Relation`. Which backend runs the evaluation is decided by the type of the rules you pass.

**Contexts.** For a single evaluation, a context is a dictionary or a pydantic model. For a pydantic model only the fields that were set are supplied: an unset field is *missing*, which is not the same as a field set to `None`. For batches and DecisionTable matching, contexts are a table (a Polars frame, an Ibis table or a `Relation`) or a list of pydantic objects that carry a `request_id`.

## Next steps

[Chapter 3](../03-matching-concepts/index.md) explains how each strategy scores a rule and how wildcards, null and missing values behave.

## Source references

- `src/mountainash_rules/core/dimension.py` — `ContextField`, `Dimension`, `DimensionsMetadata`.
- `src/mountainash_rules/core/aggregate.py` — `Aggregate`, `AggregateOp`.
- `src/mountainash_rules/core/metadata_tables.py` — the five flat tables and canonical marker text.
