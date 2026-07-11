# Serialisable Dimension Metadata: StrEnums, DataType, YAML, Per-Row REGEX

> **Status:** APPROVED FOR PLANNING
> **Date:** 2026-07-12
> **Source:** Architectural review 2026-07-12 §5.2, §2.2, §2.3; backlog card
> `mountainash-central/01.principles/mountainash-rules/h.backlog/serializable-dimension-metadata.md`
> **Depends on:** nothing. **Depended on by:** hit-policies spec (adds fields to
> `DimensionsMetadata`), babel lattice-schema-contract spec (ships metadata as a
> sidecar artefact).

## Problem

`DimensionsMetadata` is the wire format between the engines and babel, but it
cannot travel as data:

- `Dimension.data_type: type` holds a live Python class.
- `MatchStrategy` / `DimensionRole` are `auto()` int enums — serialised values
  are meaningless and unstable under member reordering.
- No temporal types, though effective-dated rules are the most common
  production shape.
- REGEX is a literal pattern on the `Dimension` (a global context validator),
  while README/CLAUDE.md claim per-row patterns — real rule tables need the
  per-row form.
- `valid_values` has a mutable `[]` default and is read by nothing.
- Babel's CSV importer *infers* metadata (everything becomes EXACT); RANGE and
  set dimensions cannot be expressed through `import_lattice` at all.

This is a breaking change window: the package is pre-adoption (the rename just
landed), so we break now, once.

## Design

### 1. StrEnum migration (`constants.py`)

```python
class MatchStrategy(StrEnum):
    EXACT = "exact"
    NOT_EQUAL = "not_equal"
    RANGE = "range"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    CONTAINS = "contains"
    REGEX = "regex"                  # per-row pattern (see §4)
    CONTEXT_REGEX = "context_regex"  # literal pattern on metadata (current REGEX)
    SET_MEMBERSHIP = "set_membership"
    SET_EXCLUSION = "set_exclusion"

class DimensionRole(StrEnum):
    CONSTRAINT = "constraint"
    CONTEXT_KEY = "context_key"
```

Explicit lowercase values (not `auto()`) so the serialised form is stable
forever regardless of member order. Pydantic validates StrEnums by value and
`model_dump(mode="json")` emits the plain string (`"range"`); note plain
`model_dump()` emits the enum member itself — all serialisation paths in
this spec use `mode="json"`. Anyone comparing `.value` to an int breaks;
that is the point of doing this now.

### 2. `DataType` StrEnum replacing `data_type: type`

```python
class DataType(StrEnum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATE = "date"
    DATETIME = "datetime"

    @property
    def is_numeric(self) -> bool: ...   # INT, FLOAT
    @property
    def is_temporal(self) -> bool: ...  # DATE, DATETIME
    @property
    def python_type(self) -> type: ...  # str/int/float/bool/date/datetime
```

`Dimension.data_type: DataType = DataType.STR`, with a `mode="before"` field
validator accepting Python types (`{str: STR, int: INT, float: FLOAT,
bool: BOOL, date: DATE, datetime: DATETIME}`) and emitting a
`DeprecationWarning`. Existing call sites keep working through one release;
the shim is removed after the ecosystem repos migrate.

**Internal call-site sweep** — every `dim.data_type in (int, float)` becomes
`dim.data_type.is_numeric or dim.data_type.is_temporal` where the branch
selects the "orderable non-string" path, or plain `.is_numeric` where it
selects the numeric sentinel. Touched: `compiler.py::_sentinels_for_type`,
`accumulator_compiler.py::_sentinel_checks`, `accumulator_engine.py` anchor NA
flags, `context.py` sentinel selection, `dimension.py` validators — including
the three error messages formatting `self.data_type.__name__` (StrEnum
members have no meaningful `__name__`; they format as `.value` directly) —
and babel `dmn.py`: both `_type_ref` and `_feel_entry`'s `dtype == str` /
`dtype in (int, float)` comparisons (which the deprecation shim would
otherwise silently break). The implementation plan includes a
`grep -rn "data_type"` sweep across both repos as its own task so no site
is missed.

### 3. Temporal support

- `DATE`/`DATETIME` are valid for EXACT, NOT_EQUAL, RANGE, GREATER_THAN,
  LESS_THAN (they are orderable). Validation in `dimension.py` updates
  accordingly (RANGE/GT/LT accept numeric **or** temporal).
- New sentinels in `constants.py`, chosen from the proleptic floor where no
  business data lives:

  ```python
  UNKNOWN_DATE = datetime.date(1, 1, 1)
  NOT_SET_DATE = datetime.date(1, 1, 2)
  UNKNOWN_DATETIME = datetime.datetime(1, 1, 1)
  NOT_SET_DATETIME = datetime.datetime(1, 1, 2)
  TEMPORAL_DATE_SENTINELS = {UNKNOWN_DATE, NOT_SET_DATE}
  TEMPORAL_DATETIME_SENTINELS = {UNKNOWN_DATETIME, NOT_SET_DATETIME}
  ```

- A module-level `sentinels_for(data_type: DataType) -> set` in `constants.py`
  replaces the two private `_sentinels_for_type` copies in `compiler.py` and
  the inline checks in `accumulator_compiler.py`/`context.py`. `context.py`'s
  missing-field path returns the matching `NOT_SET_*` sentinel per type.
- This promotes backlog item E1 (temporal don't-cares) to implemented for the
  filter engine **and** the accumulator. The accumulator's comparison
  expressions (`greatest`/`least`/`lt`/`gt`) are type-agnostic, but its
  sentinel handling is **not** currently: `accumulator_compiler.py`
  hard-codes `UNKNOWN_NUMERIC` in `compile_coalesce_na_flag`,
  `_range_sentinel_checks`, and `_coalesce_range`/`_coalesce_threshold`
  literals, and `accumulator_engine.py::_create_anchor` does the same. All
  of these switch to `sentinels_for(dim.data_type)` / a per-type
  `unknown_sentinel_for(dim.data_type)` lookup as part of this change —
  temporal RANGE in the accumulator is in scope, not assumed free.
- `BOOL` is valid for EXACT/NOT_EQUAL only. Booleans have no in-band sentinel;
  don't-care is expressed as null. Two coordinated pieces: (a)
  `context.py::extract_context_values` must **preserve `None`** for
  `DataType.BOOL` dimensions instead of substituting the string `NOT_SET`
  sentinel (which would type-clash in a boolean column); (b) the compiler
  wraps bool dimensions so null rule/context values yield ternary 0 — if
  `ma.t_col` already maps nulls to UNKNOWN this is free; otherwise
  `_compile_exact` adds an explicit `is_null → 0` `when` branch for
  `DataType.BOOL`. (Implementation verifies which during the RED phase; both
  paths are specified so there is no ambiguity.)

### 4. Per-row REGEX

Split the current single strategy:

- **`REGEX`** (repurposed): the rule column holds a per-row pattern string,
  with the same sentinel → UNKNOWN handling as PREFIX/SUFFIX/CONTAINS.
  Mechanics caveat: mountainash's `regex_contains` currently accepts only a
  literal pattern, not a column reference, so this cannot simply reuse
  `_compile_string_match(dim, "regex_contains")`. Implementation follows the
  established SET_MEMBERSHIP precedent: a backend-native workaround
  (`ma.native(...)` — e.g. polars `str.contains(pl.col(...))`, DuckDB
  `regexp_matches(col, pattern_col)`) behind the same compiled-expression
  interface, pending upstream mountainash support for column-valued regex
  patterns (tracked as a mountainash issue filed with this change). Backends
  without a native path raise a clear `NotImplementedError` naming the
  strategy and backend. `regex_pattern` on the Dimension is **forbidden**
  for this strategy.
- **`CONTEXT_REGEX`** (new name for current behaviour): literal
  `regex_pattern` on the Dimension, acting as a global context validator; the
  existing `_compile_regex` implementation moves here unchanged.

Validator changes in `dimension.py`: `regex_pattern` required non-empty for
CONTEXT_REGEX, forbidden otherwise (including REGEX — a clear migration error
message points to CONTEXT_REGEX). README and CLAUDE.md's match-strategy tables
are corrected as part of this change.

Neither form is accumulator-supported (unchanged). Babel's DMN exporter emits
per-row REGEX patterns as plain strings with the existing S-FEEL caveat.

### 5. YAML / JSON round-trip (`dimension.py`)

```python
class DimensionsMetadata(BaseModel):
    def to_yaml(self) -> str
    @classmethod
    def from_yaml(cls, text: str) -> DimensionsMetadata
    def to_yaml_file(self, path: Path) -> Path
    @classmethod
    def from_yaml_file(cls, path: Path) -> DimensionsMetadata
```

Implemented as
`yaml.safe_dump(self.model_dump(mode="json", exclude_defaults=True), sort_keys=False)`
and `cls.model_validate(yaml.safe_load(text))` — all field types are now
JSON-scalar (StrEnums, str, bool, list), so no custom encoders. Omitting
defaults keeps files minimal and forward-compatible (a file written today
validates after new optional fields are added); round-trip equality still
holds because pydantic re-applies defaults on load. JSON comes free via
pydantic's `model_dump_json`/`model_validate_json`; no wrapper methods
needed. **New core dependency: `pyyaml`** (ubiquitous, stdlib-adjacent;
acceptable for the package's role as the metadata authority).

### 6. `valid_values`

Kept, as a declarative domain for the *context* values of a dimension:

- `valid_values: list[str | int | float | bool] = Field(default_factory=list)`
  — fixes the mutable-default footgun and constrains entries to YAML/JSON
  scalar types so serialisation cannot fail on arbitrary objects (temporal
  domains are declared as ISO strings).
- The engines continue to ignore it (documented in the docstring). Its
  consumer is babel's coverage validator (backlog D2), which needs
  per-dimension domains to enumerate uncovered contexts.
- Serialised like every other field.

### 7. Babel importer accepts explicit metadata

`CsvImporter.import_lattice(path, *, metadata: DimensionsMetadata | None = None, ...)`:
when provided, inference is skipped entirely and the metadata is attached
as-is (RANGE min/max columns validated present in the CSV; missing columns →
`ValueError` naming them). When `metadata` is a `str | Path` ending in
`.yaml`/`.yml`, it is loaded via `from_yaml_file` — or, when the file is a
babel `LatticeManifest` (the sidecar format babel ships, which *embeds* this
spec's `DimensionsMetadata` payload — see the babel lattice-schema-contract
spec, which owns the sidecar format), the embedded payload is used.
Inference remains the no-metadata fallback, now emitting `DataType` values.

### Approaches considered

- **Keep `data_type: type` + custom pydantic serialisers**: rejected — the
  YAML would contain `"builtins.str"`-style strings anyway; an enum is the
  honest wire type and gives `is_numeric`/`is_temporal` a home.
- **Single REGEX with a `pattern_source` flag**: rejected — two behaviours
  under one name is exactly what made the current docs wrong; distinct enum
  members are self-describing in serialised form.
- **TOML instead of YAML**: rejected — babel sidecars want comments and
  nesting; YAML is the incumbent in the mountainash ecosystem configs.

## Testing (TDD)

`tests/test_dimension_serialization.py` (new) + updates:

1. StrEnum values: `MatchStrategy("range") is MatchStrategy.RANGE`;
   `model_dump()` emits `"range"`.
2. Deprecation shim: `Dimension(data_type=int)` warns `DeprecationWarning`
   and yields `DataType.INT`.
3. Round-trip: `DimensionsMetadata.from_yaml(md.to_yaml()) == md` for a
   metadata set covering every strategy, RANGE inclusivity flags, roles,
   `context_field`/`rule_field` remaps, and `valid_values`.
4. Temporal: RANGE dimension over `DataType.DATE` matches/excludes contexts
   through `ExpressionRulesEngine`; an `UNKNOWN_DATE` bound yields ternary 0
   (UNKNOWN), which survives filtering — i.e. the bound is unconstrained;
   missing date context field → `NOT_SET_DATE` → UNKNOWN. Explicit backend
   regression tests on polars **and** ibis-duckdb assert that
   `date(1,1,1)`-family sentinels store and compare correctly (proleptic
   minimum dates are an untested corner of both backends).
5. Bool: EXACT bool dimension with null rule value → ternary 0; RANGE bool →
   `ValueError`.
6. Per-row REGEX: rules column with distinct patterns matches per-row;
   sentinel pattern → UNKNOWN. CONTEXT_REGEX preserves the old tests
   (rename them). `Dimension(match_strategy=REGEX, regex_pattern=...)` →
   `ValueError` naming CONTEXT_REGEX.
7. Babel: CSV import with explicit YAML metadata produces a lattice whose
   dimensions include RANGE (previously impossible); missing range column →
   `ValueError`.

## Files touched

- `src/mountainash_rules/constants.py`, `dimension.py`, `compiler.py`,
  `context.py`, `accumulator_compiler.py`, `accumulator_engine.py`
- `pyproject.toml` (+`pyyaml`), README.md, CLAUDE.md (strategy table)
- babel: `importers/csv_.py`, `exporters/dmn.py` (`_type_ref` takes DataType)
- tests: new `test_dimension_serialization.py`; sweep of existing tests
  constructing `Dimension(data_type=str)` (leave using the shim where the
  warning is filtered, migrate the rest)

## Out of scope

- Semver/version-aware match strategies (backlog E2).
- `valid_values` enforcement in the engines.
- DMN FEEL date literals in babel export (follow-up once temporal lands).
