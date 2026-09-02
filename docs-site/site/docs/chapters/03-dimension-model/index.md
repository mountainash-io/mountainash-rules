---
title: "Chapter 3: Dimension Model"
description: "The Pydantic metadata layer that describes how rules table columns are interpreted, including roles, field resolution, validation, serialisable data types, temporal sentinels, and YAML schemas."
generated_by: claude skill chapter-content-generator
refreshed_by: claude skill textbook-refresh
date: 2026-09-02
version: 0.09
---

# Chapter 3: Dimension Model

## Summary

This chapter covers the dimension metadata layer — the Pydantic models that describe how each column in a rules table should be interpreted during evaluation. You will learn about the `DimensionRole` enum (CONSTRAINT vs CONTEXT_KEY), the `Dimension` class with its field resolution and validation, the `DimensionsMetadata` collection and its YAML round-trip, the `DataType` enum, temporal sentinels, and the data type constraints that govern which strategies apply to which column types.

---

<!-- concept:26 -->
<!-- concept:29 -->
## The Role of Dimension Metadata

A rules table is just a DataFrame — rows and columns with no inherent semantics. The dimension metadata layer gives meaning to those columns by declaring how each one should participate in evaluation. Without metadata, the engine cannot know which columns are conditions, which are outcomes, which store range bounds, or which require string pattern matching.

The metadata layer consists of three key elements: the `DimensionRole` enum (which classifies columns by purpose), the `Dimension` class (which describes a single column's evaluation behavior), and the `DimensionsMetadata` class (which validates a collection of dimensions as a coherent unit).

<!-- concept:23 -->
## DimensionRole Enum

The `DimensionRole` enum assigns a functional classification to each dimension, controlling how it participates in both the expression rules engine and the accumulator engine. There are exactly two roles.

```python
from enum import StrEnum

class DimensionRole(StrEnum):
    CONSTRAINT = "constraint"
    CONTEXT_KEY = "context_key"
```

The role determines the dimension's behavior at two different stages: during expression evaluation (Chapter 5) and during lattice construction (Chapters 7-9). Choosing the correct role for each dimension is a fundamental modelling decision.

<!-- concept:24 -->
### CONSTRAINT Role

A dimension with the CONSTRAINT role participates in ternary evaluation. During rule evaluation, its compiled expression compares the rule cell value against the context value and produces a ternary result (1, 0, or -1). The survival and specificity computations include this dimension.

In the accumulator engine, CONSTRAINT dimensions participate in coalesce logic — when two rules are combined into a lattice node, their CONSTRAINT values are merged (intersected) to produce a tighter constraint that represents both rules simultaneously.

Most dimensions in a typical rules table are CONSTRAINTs. Examples include region, product category, customer tier, and any other field that filters rules based on the evaluation context.

<!-- concept:25 -->
### CONTEXT_KEY Role

A dimension with the CONTEXT_KEY role does not participate in ternary evaluation at all. Instead, it acts as a partitioning dimension for the accumulator engine — the engine builds a separate lattice for each unique combination of CONTEXT_KEY values.

During expression engine evaluation, CONTEXT_KEY dimensions are ignored (they do not produce ternary columns). Their purpose emerges in the accumulator workflow: they define natural partitions in the rule set where rules in different partitions can never interact or combine.

Example use case: a rules table with a `product_line` dimension that partitions rules into independent groups. Rules for "insurance" never combine with rules for "banking" in the lattice, so `product_line` is a CONTEXT_KEY.

| Role | Expression Engine | Accumulator Engine |
|------|------------------|-------------------|
| CONSTRAINT | Produces ternary column, participates in survival/specificity | Participates in coalesce, combined with other rules |
| CONTEXT_KEY | Ignored | Partitions the lattice build |

## Dimension Class

The `Dimension` class is the core metadata unit — a Pydantic `BaseModel` that describes one column's evaluation behavior. Each instance captures the dimension's name, its field mappings, its match strategy, its data type, and its role.

The following fields are available on every Dimension:

- **`dimension_name`** (required, str): the logical name of the dimension, used as a key throughout the engine
- **`context_field`** (optional, str): the field name to look up in the context object (defaults to dimension_name)
- **`rule_field`** (optional, str): the column name in the rules DataFrame (defaults to dimension_name)
- **`match_strategy`** (MatchStrategy): the comparison operation (defaults to EXACT)
- **`data_type`** (DataType): a serialisable dimension type (`str`, `int`, `float`, `bool`, `date`, or `datetime`; defaults to `DataType.STR`)
- **`role`** (DimensionRole): CONSTRAINT or CONTEXT_KEY (defaults to CONSTRAINT)
- **`valid_values`** (list): optional list of allowed values for documentation/validation

Additionally, RANGE-specific fields are available:

- **`range_min_field`** (optional, str): column name for the lower bound
- **`range_max_field`** (optional, str): column name for the upper bound
- **`range_min_inclusive`** (bool): whether the lower bound is inclusive (default True)
- **`range_max_inclusive`** (bool): whether the upper bound is inclusive (default True)

And for context-regex dimensions:

- **`regex_pattern`** (optional, str): the literal regex pattern for context validation

The `valid_values` field deserves special mention. While it is not enforced during evaluation (the engine does not check context or rule values against this list), it serves as machine-readable documentation and can be used by external tools to generate UI dropdowns, validate rule data at import time, or produce data dictionaries.

<!-- concept:28 -->
The default values are chosen to minimize configuration burden for the most common case. A dimension with only `dimension_name` set uses EXACT strategy, `DataType.STR`, CONSTRAINT role, and resolves all field names to the dimension name. This means the simplest possible dimension definition is a single line:

```python
from mountainash_rules import DataType, Dimension, DimensionRole, MatchStrategy

dim = Dimension(dimension_name="region")
# Equivalent to:
# Dimension(
#     dimension_name="region",
#     context_field=None,          -> resolved_context_field = "region"
#     rule_field=None,             -> resolved_rule_field = "region"
#     match_strategy=MatchStrategy.EXACT,
#     data_type=DataType.STR,
#     role=DimensionRole.CONSTRAINT,
#     valid_values=[],
# )
```

Here is a complete example defining three dimensions with different strategies:

```python
from mountainash_rules import (
    DataType, Dimension, DimensionsMetadata, MatchStrategy, DimensionRole
)

metadata = DimensionsMetadata(dimensions=[
    Dimension(
        dimension_name="region",
        match_strategy=MatchStrategy.EXACT,
        data_type=DataType.STR,
    ),
    Dimension(
        dimension_name="order_value",
        match_strategy=MatchStrategy.RANGE,
        data_type=DataType.FLOAT,
        range_min_field="order_min",
        range_max_field="order_max",
    ),
    Dimension(
        dimension_name="product_line",
        match_strategy=MatchStrategy.EXACT,
        data_type=DataType.STR,
        role=DimensionRole.CONTEXT_KEY,
    ),
])
```

#### Diagram: Dimension Class Field Map

<iframe src="../../sims/dimension-class-fields/main.html" width="100%" height="500px" scrolling="no"></iframe>
<details markdown="1">
<summary>Dimension Class Field Map</summary>
Type: diagram
**sim-id:** dimension-class-fields<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive class diagram showing the Dimension model's fields, their types, defaults, and which fields are required for which strategies.

**Components:**
- Central node: "Dimension" class
- Grouped child nodes: Common fields (dimension_name, context_field, rule_field, match_strategy, data_type, role), RANGE fields (range_min_field, range_max_field, range_min_inclusive, range_max_inclusive), CONTEXT_REGEX fields (regex_pattern)
- Edge labels showing type and default value

**Interactions:** Click a strategy name (EXACT, RANGE, REGEX, etc.) in a sidebar to highlight which fields are required/optional for that strategy. Hover over a field node for a tooltip with its description and validation rules.

**Learning objective:** Identify which Dimension fields are required for each match strategy configuration (Bloom: Analyze)
</details>

<!-- concept:27 -->
## DimensionsMetadata

The `DimensionsMetadata` class is a validated container for a list of `Dimension` objects. It serves as the engine's configuration input — you construct a `DimensionsMetadata` instance and pass it to the `ExpressionRulesEngine` or `AccumulatorEngine` constructor. It acts as the single source of truth for how the engine interprets the rules DataFrame, including table-level hit-policy and output-field settings.

```python
from pydantic import BaseModel, Field

from mountainash_rules import Dimension, HitPolicy

class DimensionsMetadata(BaseModel):
    dimensions: list[Dimension]
    hit_policy: HitPolicy = HitPolicy.COLLECT
    priority_field: str | None = None
    output_fields: list[str] = Field(default_factory=list)
```

Beyond holding the list, `DimensionsMetadata` provides:

- **Table-level evaluation settings**: `hit_policy`, optional `priority_field`, and `output_fields` configure result selection and payload projection.
- **Uniqueness validation**: a `model_validator` ensures no two dimensions share the same `dimension_name`. Duplicate names would cause ambiguous column references during compilation, so they are rejected at construction time.
- **Lookup method**: `get_dimension(name)` retrieves a dimension by its logical name, raising `KeyError` if not found.

The uniqueness constraint is critical because the engine uses `dimension_name` as a key in multiple dictionaries — compiled expressions, ternary result columns, and coalesce mappings all reference dimensions by name.

```python
# This raises ValueError — duplicate dimension names
try:
    DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region"),
        Dimension(dimension_name="region"),  # Duplicate!
    ])
except ValueError as e:
    print(e)  # "Duplicate dimension names: {'region'}"
```

## Field Resolution

A common situation in production rule sets is that the logical dimension name differs from either the DataFrame column name or the context field name. For example, a dimension called `"customer_region"` might correspond to a DataFrame column named `"cust_region"` and a context field named `"region"`.

Field resolution is handled by two computed properties on the `Dimension` class:

- **`resolved_context_field`**: returns `context_field` if explicitly set, otherwise falls back to `dimension_name`
- **`resolved_rule_field`**: returns `rule_field` if explicitly set, otherwise falls back to `dimension_name`

This three-name architecture (dimension_name, context_field, rule_field) decouples the engine's internal references from both the data schema and the caller's context model. You can rename DataFrame columns or context fields without modifying every engine reference — just update the field mapping on the dimension.

```python
dim = Dimension(
    dimension_name="customer_region",
    context_field="region",           # Context uses "region"
    rule_field="cust_region",         # DataFrame uses "cust_region"
    match_strategy=MatchStrategy.EXACT,
)

assert dim.resolved_context_field == "region"
assert dim.resolved_rule_field == "cust_region"

# Without explicit fields, both resolve to dimension_name
dim2 = Dimension(dimension_name="tier")
assert dim2.resolved_context_field == "tier"
assert dim2.resolved_rule_field == "tier"
```

During compilation (Chapter 4), the compiler uses `resolved_rule_field` to reference the DataFrame column and constructs the context literal column as `__ctx_{dimension_name}`. During evaluation, the engine uses `resolved_context_field` (via `extract_context_values`) to look up values in the caller's context object.

## Dimension Validator

The `Dimension` class uses Pydantic's `@model_validator(mode="after")` to enforce strategy-specific constraints after all fields have been assigned. This validator catches configuration errors that simple type annotations cannot express.

The validator enforces the following rules:

1. **RANGE strategy** requires both `range_min_field` and `range_max_field` to be non-empty strings, and `data_type` must be numeric or temporal (`DataType.INT`, `FLOAT`, `DATE`, or `DATETIME`)
2. **REGEX, CONTEXT_REGEX, PREFIX, SUFFIX, CONTAINS** strategies require `data_type` to be `DataType.STR`
3. **CONTEXT_REGEX** strategy requires `regex_pattern` to be a non-empty string
4. Only `CONTEXT_REGEX` may set `regex_pattern`; per-row `REGEX` reads its pattern from the rule column, while other strategies reject the field
5. **GREATER_THAN and LESS_THAN** strategies require a numeric or temporal data type

Each constraint produces a descriptive `ValueError` message that identifies the dimension by name and explains exactly which requirement was violated. This makes debugging misconfigured rule sets straightforward — errors surface immediately at metadata construction time with clear diagnostic messages.

#### Diagram: Validation Decision Flow

<iframe src="../../sims/dimension-validator-flow/main.html" width="100%" height="450px" scrolling="no"></iframe>
<details markdown="1">
<summary>Validation Decision Flow</summary>
Type: workflow
**sim-id:** dimension-validator-flow<br/>
**Library:** p5.js<br/>
**Status:** Specified

**Purpose:** Interactive flowchart showing the validation logic path for a Dimension being constructed, with pass/fail outcomes at each check.

**Components:**
- Start node: "Dimension created"
- Decision diamonds for each validation check (RANGE fields present? data_type correct? regex_pattern consistent?)
- Green terminal: "Valid Dimension"
- Red terminals: specific ValueError messages

**Interactions:** Click a strategy button at the top to trace that strategy's path through the validator. Invalid configurations show the error message in a red callout. User can toggle individual fields on/off to see which combinations pass or fail.

**Learning objective:** Predict which dimension configurations will pass or fail validation (Bloom: Evaluate)
</details>

<!-- concept:30 -->
## Data Type Constraints

The `data_type` field on a `Dimension` is a `DataType` StrEnum. It determines which typed sentinel set the compiler uses and restricts which match strategies are valid for that dimension. The enum's values are lowercase strings so metadata can be serialised without inventing a backend-specific type encoding.

The relationship between data type and strategy is not arbitrary — it reflects fundamental semantic requirements:

- **Ordered comparisons** (`RANGE`, `GREATER_THAN`, `LESS_THAN`) require numeric or temporal types (`int`, `float`, `date`, or `datetime`) because they perform ordering operations.
- **String operations** (`PREFIX`, `SUFFIX`, `CONTAINS`, `REGEX`, `CONTEXT_REGEX`) require `DataType.STR` because they use string-specific operations.
- **Equality operations** (`EXACT`, `EXACT_KEY`, `NOT_EQUAL`) work with every `DataType` because they rely on equality checks.
- **Set operations** (`SET_MEMBERSHIP`, `SET_EXCLUSION`) work with string, numeric, and temporal types. Boolean set dimensions are rejected because there is no typed wildcard sentinel and a set over `{true, false}` is degenerate.

The data type controls sentinel selection during compilation:

| data_type | Unknown Sentinel | Not-Set Sentinel |
|-----------|------------------|------------------|
| `DataType.STR` | `"<NA>"` | `"<NOT_SET>"` |
| `DataType.INT` / `DataType.FLOAT` | `-999999999` | `-999999998` |
| `DataType.BOOL` | null-aware boolean handling | null-aware boolean handling |
| `DataType.DATE` | `date(1, 1, 1)` | `date(1, 1, 2)` |
| `DataType.DATETIME` | `datetime(1, 1, 1)` | `datetime(1, 1, 2)` |

This pairing ensures that ternary-aware columns (`t_col`) are constructed with the correct sentinel set. A numeric dimension must use numeric sentinel values, while a temporal dimension uses values of the corresponding date or datetime type.

!!! warning "Type Safety at the Data Level"
    The `data_type` field describes the expected type of values in the DataFrame column. The engine does not perform runtime type casting. If your DataFrame column contains strings but the dimension declares `DataType.INT`, numeric sentinel detection and comparisons cannot correctly interpret those values. Always ensure the declared `data_type` matches the actual DataFrame column type.

<!-- concept:94 -->
## DataType Enum

`DataType` is a `StrEnum` with six members: `STR`, `INT`, `FLOAT`, `BOOL`, `DATE`, and `DATETIME`. Their serialised values are the lowercase strings `str`, `int`, `float`, `bool`, `date`, and `datetime`, which makes a dimension schema readable in Python and stable in interchange formats.

Each member exposes `python_type`, the corresponding Python runtime class:

```python
from datetime import date, datetime

from mountainash_rules import DataType

assert DataType.STR.python_type is str
assert DataType.INT.python_type is int
assert DataType.FLOAT.python_type is float
assert DataType.BOOL.python_type is bool
assert DataType.DATE.python_type is date
assert DataType.DATETIME.python_type is datetime
```

Use enum members when constructing dimensions:

```python
from mountainash_rules import DataType, Dimension, MatchStrategy

effective = Dimension(
    dimension_name="effective_on",
    match_strategy=MatchStrategy.RANGE,
    data_type=DataType.DATE,
    range_min_field="starts_on",
    range_max_field="ends_on",
)
```

For migration compatibility, passing a raw Python class such as `data_type=str` is still accepted. The `_coerce_data_type` field validator maps it to the corresponding `DataType` member and emits a `DeprecationWarning`; new schemas should use `DataType.STR` (or the serialised value `"str"`) instead.

<!-- concept:95 -->
## Temporal Sentinels

Date and datetime dimensions use typed sentinels at the proleptic floor of their domains:

| Data type | UNKNOWN | NOT_SET |
|-----------|---------|---------|
| `DataType.DATE` | `date(1, 1, 1)` | `date(1, 1, 2)` |
| `DataType.DATETIME` | `datetime(1, 1, 1)` | `datetime(1, 1, 2)` |

These values are deliberately reserved below the business domain: ordinary business dates and timestamps do not live at year 1. `unknown_sentinel_for(DataType.DATE)` and `not_set_sentinel_for(DataType.DATE)` return the two `date` values; the corresponding `DATETIME` lookups return `datetime` values. The sentinels are therefore portable across supported DataFrame backends and remain orderable, which lets temporal range and threshold strategies use the same ternary comparison model as numeric dimensions.

Using typed floor values also avoids a separate null-handling code path. Unknown and missing context values stay in-band, so the compiler can recognize them through `sentinels_for(data_type)` while ordinary date/datetime comparisons remain typed and predictable.

<!-- concept:96 -->
## YAML Round-Trip

`DimensionsMetadata` can be defined and version-controlled as YAML rather than only as Python code. `to_yaml()` serialises metadata with defaults omitted, `from_yaml(text)` reconstructs it, and `to_yaml_file(path)` / `from_yaml_file(path)` provide the file-oriented equivalents. These methods use the package's `pyyaml` dependency and preserve enum values as their stable strings.

For example, this YAML schema declares an exact string dimension and a temporal range dimension:

```yaml
dimensions:
  - dimension_name: region
    data_type: str
    match_strategy: exact
  - dimension_name: effective_at
    data_type: datetime
    match_strategy: range
    range_min_field: starts_at
    range_max_field: ends_at
```

Load it in an application, or round-trip a Python definition for review and persistence:

```python
from mountainash_rules import DimensionsMetadata

metadata = DimensionsMetadata.from_yaml_file("rules-metadata.yaml")
yaml_text = metadata.to_yaml()
metadata.to_yaml_file("rules-metadata.normalized.yaml")
```

Because `DimensionsMetadata` validates each `Dimension` during loading, malformed strategy/type combinations and duplicate dimension names fail at schema-load time instead of surfacing during evaluation.

## Practical Example: Multi-Strategy Rule Set

To consolidate the concepts in this chapter, consider a complete metadata definition for a shipping cost rules table:

```python
from mountainash_rules import (
    DataType, Dimension, DimensionsMetadata, MatchStrategy, DimensionRole
)

shipping_metadata = DimensionsMetadata(dimensions=[
    # Partition dimension — separate lattice per carrier
    Dimension(
        dimension_name="carrier",
        data_type=DataType.STR,
        role=DimensionRole.CONTEXT_KEY,
    ),
    # String equality — destination country
    Dimension(
        dimension_name="destination_country",
        match_strategy=MatchStrategy.EXACT,
        data_type=DataType.STR,
    ),
    # Numeric range — package weight
    Dimension(
        dimension_name="weight",
        match_strategy=MatchStrategy.RANGE,
        data_type=DataType.FLOAT,
        range_min_field="weight_min",
        range_max_field="weight_max",
    ),
    # Set membership — allowed service levels
    Dimension(
        dimension_name="service_level",
        context_field="requested_service",
        rule_field="available_services",
        match_strategy=MatchStrategy.SET_MEMBERSHIP,
        data_type=DataType.STR,
    ),
])
```

This metadata defines four dimensions: one CONTEXT_KEY (carrier), one string EXACT (destination_country), one numeric RANGE (weight), and one SET_MEMBERSHIP (service_level with custom field resolution). The engine uses this metadata to compile expressions, partition lattices, and validate the rule set.

## How Metadata Maps to the Rules DataFrame

To understand how dimension metadata connects to the physical data, consider a concrete rules DataFrame for the shipping example above:

| rule_name | carrier | destination_country | weight_min | weight_max | available_services | shipping_cost |
|-----------|---------|-------------------|------------|------------|-------------------|---------------|
| domestic_light | express | AU | 0.0 | 5.0 | ["standard", "priority"] | 8.50 |
| domestic_heavy | express | AU | 5.1 | 30.0 | ["standard"] | 22.00 |
| international | express | \<NA\> | 0.0 | 20.0 | ["priority", "express"] | 45.00 |
| fallback | express | \<NA\> | -999999999 | -999999999 | ["standard"] | 15.00 |

Each dimension in the metadata maps to specific columns in this table:

- `carrier` (CONTEXT_KEY): maps to the `carrier` column — used for partitioning, not ternary evaluation
- `destination_country` (EXACT): maps to the `destination_country` column — sentinel `<NA>` acts as wildcard
- `weight` (RANGE): maps to `weight_min` and `weight_max` columns — sentinel -999999999 makes the fallback rule a wildcard on weight
- `service_level` (SET_MEMBERSHIP): maps to the `available_services` list column via custom field resolution (`rule_field="available_services"`, `context_field="requested_service"`)

The `shipping_cost` column is not referenced by any dimension — it is a payload column that passes through evaluation unchanged, appearing in the result alongside the ternary columns and rank.

#### Diagram: Metadata-to-DataFrame Mapping

<iframe src="../../sims/metadata-dataframe-mapping/main.html" width="100%" height="500px" scrolling="no"></iframe>
<details markdown="1">
<summary>Metadata-to-DataFrame Mapping</summary>
Type: diagram
**sim-id:** metadata-dataframe-mapping<br/>
**Library:** vis-network<br/>
**Status:** Specified

**Purpose:** Interactive visualization connecting dimension metadata definitions to their corresponding DataFrame columns, showing field resolution paths and role classifications.

**Components:**
- Left panel: Dimension metadata objects with their field values
- Right panel: DataFrame column headers
- Connecting arrows: solid lines from dimension to referenced columns, color-coded by role (blue for CONSTRAINT, orange for CONTEXT_KEY)
- Dashed arrows for field resolution overrides (context_field, rule_field)

**Interactions:** Click any dimension to highlight its connected DataFrame columns. Hover over arrows to see the resolution path (dimension_name -> resolved_rule_field -> actual column). Toggle between "compile-time view" (showing which columns the compiler reads) and "runtime view" (showing which context fields are extracted).

**Learning objective:** Map dimension metadata definitions to their physical DataFrame columns through field resolution (Bloom: Apply)
</details>

## Common Metadata Patterns

Experienced users encounter several recurring patterns when designing dimension metadata:

**Pattern 1: Simple Defaults.** When dimension_name, context_field, and rule_field all share the same name, no overrides are needed. This is the most common pattern for small rule sets.

```python
Dimension(dimension_name="region")  # All three names resolve to "region"
```

**Pattern 2: Schema Decoupling.** When the rules DataFrame and context model use different naming conventions, field resolution bridges the gap without requiring either side to change.

```python
Dimension(
    dimension_name="customer_tier",
    context_field="tier",          # Context model uses "tier"
    rule_field="cust_tier_code",   # DataFrame uses "cust_tier_code"
)
```

**Pattern 3: Multi-Column Dimensions.** RANGE dimensions reference two columns. The dimension_name is a logical identifier that does not correspond to any single column.

```python
Dimension(
    dimension_name="age_bracket",     # Logical name only
    context_field="customer_age",     # Context provides a single age value
    match_strategy=MatchStrategy.RANGE,
    data_type=DataType.INT,
    range_min_field="age_lower",      # DataFrame column for lower bound
    range_max_field="age_upper",      # DataFrame column for upper bound
)
```

**Pattern 4: Partition-Then-Constrain.** A rule set uses one or more CONTEXT_KEY dimensions for partitioning and several CONSTRAINT dimensions for evaluation within each partition.

```python
# Partition by product line, then evaluate on region and tier
DimensionsMetadata(dimensions=[
    Dimension(dimension_name="product_line", role=DimensionRole.CONTEXT_KEY),
    Dimension(dimension_name="region", role=DimensionRole.CONSTRAINT),
    Dimension(dimension_name="tier", role=DimensionRole.CONSTRAINT),
])
```

## Key Takeaways

- **DimensionRole** classifies dimensions as either CONSTRAINT (participates in ternary evaluation and coalesce) or CONTEXT_KEY (partitions the lattice, skipped during expression evaluation).
- The **Dimension class** is a Pydantic BaseModel capturing all metadata needed to compile and evaluate a single column: name, field mappings, strategy, `DataType`, and role.
- **DimensionsMetadata** validates unique dimension names, exposes `get_dimension(name)`, and carries table-level hit-policy/output settings.
- **Field resolution** decouples logical dimension names from physical column names and context field names via `resolved_context_field` and `resolved_rule_field` properties.
- The **dimension validator** catches strategy-specific configuration errors (missing range fields, incompatible data types, and invalid regex configuration) at construction time.
- **DataType** is the serialisable StrEnum for `str`, `int`, `float`, `bool`, `date`, and `datetime`; its `python_type` property maps each member to the corresponding Python class.
- **Temporal sentinels** reserve `date(1, 1, 1)` / `date(1, 1, 2)` and the corresponding datetime values below business data, preserving portable, orderable, in-band wildcard semantics.
- **YAML round-trip** methods let `DimensionsMetadata` schemas live in version-controlled YAML files and validate on load.
- A well-designed metadata definition is the single source of truth for how the rules engine interprets each column in the rules DataFrame.
