# Extended Match Strategies — Design Spec

**Date:** 2026-04-07
**Status:** Approved
**Scope:** Expand the rules engine with 8 new match strategies plus a backend-agnostic rewrite of REGEX

## Summary

Extend `DimensionCompiler` from 3 match strategies (EXACT, RANGE, REGEX) to 11 by adding NOT_EQUAL, GREATER_THAN, LESS_THAN, PREFIX, SUFFIX, CONTAINS, SET_MEMBERSHIP, and SET_EXCLUSION. Rewrite REGEX to be backend-agnostic using the now-consistent `mountainash` string API.

This removes the only Polars-specific code path in `compiler.py` and gives rule authors a complete toolkit for common matching patterns without dropping to the advanced expressions API.

## Prerequisite (complete)

Upstream fixes in `mountainash` (completed 2026-04-07):
- `contains`, `regex_contains`, `strpos`, `count_substring`, `like` — removed silent `_extract_literal_value` calls; all now accept column references like `starts_with` / `ends_with` already did
- `t_is_in` / `t_is_not_in` — accept column references for list-type rule columns, not just Python literal lists

These fixes unblock backend-agnostic implementations of CONTAINS, REGEX, SET_MEMBERSHIP, and SET_EXCLUSION.

## Goals

1. **Complete toolkit** — cover common matching patterns (set membership, threshold, string matching) so rule authors rarely need the advanced expressions path
2. **Consistent backend-agnosticism** — all strategies compile cleanly to Polars, Ibis, and Narwhals
3. **Eliminate the Polars-specific regex workaround** — remove `pl.struct().map_elements()` and `import polars as pl` from `compiler.py`
4. **Preserve ternary semantics** — each strategy produces per-row ternary values (1/0/-1) with proper unknown-sentinel handling
5. **Minimal model changes** — no new Dimension fields, only validation rules

## Non-Goals

- Geographic match strategies (polygon-in-point, radius — deferred to a separate spec)
- Delimiter-based set parsing (users split strings into list columns upstream)
- Per-dimension case-sensitivity toggles (future enhancement)

## Architecture

### Strategy Catalog

| Strategy | Rule column | Context | Expression | Notes |
|---|---|---|---|---|
| **EXACT** | `"AU"` | `"AU"` | `rule.t_eq(ctx)` | Unchanged |
| **NOT_EQUAL** | `"EXCLUDED"` | `"AU"` | `rule.t_ne(ctx)` | New |
| **RANGE** | min/max cols | `50` | `min.t_le(ctx).t_and(max.t_ge(ctx))` | Unchanged |
| **GREATER_THAN** | `1000` | `1500` | `ctx.t_gt(rule)` | New (ctx > threshold) |
| **LESS_THAN** | `1000` | `500` | `ctx.t_lt(rule)` | New (ctx < threshold) |
| **PREFIX** | `"PRE-"` | `"PRE-001"` | `ctx.starts_with(rule)` wrapped | New |
| **SUFFIX** | `"-AUD"` | `"TXN-AUD"` | `ctx.ends_with(rule)` wrapped | New |
| **CONTAINS** | `"gold"` | `"gold_tier"` | `ctx.contains(rule)` wrapped | New |
| **REGEX** | `"^PRE.*"` | `"PRE-001"` | `ctx.regex_contains(rule)` wrapped | Rewritten — backend-agnostic |
| **SET_MEMBERSHIP** | `["AU","NZ"]` | `"AU"` | `ctx.t_is_in(rule)` | New — rule column is list-typed |
| **SET_EXCLUSION** | `["AU","NZ"]` | `"AU"` | `ctx.t_is_not_in(rule)` | New — rule column is list-typed |

**All 11 strategies are backend-agnostic. All support per-row patterns/thresholds/sets.**

### Ternary Handling Patterns

**Direct ternary** (EXACT, NOT_EQUAL, RANGE, GREATER_THAN, LESS_THAN, SET_MEMBERSHIP, SET_EXCLUSION):
- Use `ma.t_col(field, unknown=sentinels)` for both rule and context columns
- Ternary comparisons (`t_eq`, `t_ne`, `t_gt`, etc.) handle sentinel propagation natively
- Result is already -1/0/1

**String wrapper pattern** (PREFIX, SUFFIX, CONTAINS, REGEX):
- `starts_with`/`ends_with`/`contains`/`regex_contains` return boolean, not ternary
- Wrap with sentinel detection for ternary semantics:

```python
rule_is_sentinel = rule_col.__eq__(ma.lit(UNKNOWN)) | rule_col.__eq__(ma.lit(NOT_SET))
match = ctx_col.starts_with(rule_col)  # or ends_with / contains / regex_contains
return ma.when(rule_is_sentinel).then(0).when(match).then(1).otherwise(-1)
```

This mirrors the current REGEX pattern but uses pure expression operations (no `pl.struct` or `map_elements`).

## Components

### DimensionCompiler (`compiler.py`)

**Added methods:**

```python
def _compile_not_equal(self, dim: Dimension) -> BaseExpressionAPI:
    sentinels = self._sentinels_for_type(dim.data_type)
    rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
    ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
    return rule_col.t_ne(ctx_col)

def _compile_greater_than(self, dim: Dimension) -> BaseExpressionAPI:
    sentinels = self._sentinels_for_type(dim.data_type)
    rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
    ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
    return ctx_col.t_gt(rule_col)

def _compile_less_than(self, dim: Dimension) -> BaseExpressionAPI:
    sentinels = self._sentinels_for_type(dim.data_type)
    rule_col = ma.t_col(dim.resolved_rule_field, unknown=sentinels)
    ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
    return ctx_col.t_lt(rule_col)

def _compile_set_membership(self, dim: Dimension) -> BaseExpressionAPI:
    sentinels = self._sentinels_for_type(dim.data_type)
    ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
    rule_col = ma.col(dim.resolved_rule_field)
    return ctx_col.t_is_in(rule_col)

def _compile_set_exclusion(self, dim: Dimension) -> BaseExpressionAPI:
    sentinels = self._sentinels_for_type(dim.data_type)
    ctx_col = ma.t_col(CTX_PREFIX + dim.dimension_name, unknown=sentinels)
    rule_col = ma.col(dim.resolved_rule_field)
    return ctx_col.t_is_not_in(rule_col)

def _compile_string_match(self, dim: Dimension, op_name: str) -> BaseExpressionAPI:
    """Shared wrapper for PREFIX/SUFFIX/CONTAINS/REGEX."""
    rule_col = ma.col(dim.resolved_rule_field)
    ctx_col = ma.col(CTX_PREFIX + dim.dimension_name)
    rule_is_sentinel = (
        rule_col.__eq__(ma.lit(UNKNOWN)) | rule_col.__eq__(ma.lit(NOT_SET))
    )
    match = getattr(ctx_col, op_name)(rule_col)
    return ma.when(rule_is_sentinel).then(0).when(match).then(1).otherwise(-1)

def _compile_prefix(self, dim: Dimension) -> BaseExpressionAPI:
    return self._compile_string_match(dim, "starts_with")

def _compile_suffix(self, dim: Dimension) -> BaseExpressionAPI:
    return self._compile_string_match(dim, "ends_with")

def _compile_contains(self, dim: Dimension) -> BaseExpressionAPI:
    return self._compile_string_match(dim, "contains")
```

**Rewritten:** `_compile_regex` becomes a one-liner:

```python
def _compile_regex(self, dim: Dimension) -> BaseExpressionAPI:
    return self._compile_string_match(dim, "regex_contains")
```

The previous implementation with `pl.struct([...]).map_elements(...)` is deleted.

**Updated dispatch:**

```python
def compile_dimension(self, dim: Dimension) -> BaseExpressionAPI:
    match dim.match_strategy:
        case MatchStrategy.EXACT:          return self._compile_exact(dim)
        case MatchStrategy.NOT_EQUAL:      return self._compile_not_equal(dim)
        case MatchStrategy.RANGE:          return self._compile_range(dim)
        case MatchStrategy.GREATER_THAN:   return self._compile_greater_than(dim)
        case MatchStrategy.LESS_THAN:      return self._compile_less_than(dim)
        case MatchStrategy.PREFIX:         return self._compile_prefix(dim)
        case MatchStrategy.SUFFIX:         return self._compile_suffix(dim)
        case MatchStrategy.CONTAINS:       return self._compile_contains(dim)
        case MatchStrategy.REGEX:          return self._compile_regex(dim)
        case MatchStrategy.SET_MEMBERSHIP: return self._compile_set_membership(dim)
        case MatchStrategy.SET_EXCLUSION:  return self._compile_set_exclusion(dim)
        case _:
            raise ValueError(f"Unknown match strategy: {dim.match_strategy}")
```

**Removed imports:** `import re`, `import polars as pl`. The compiler becomes pure expressions.

### Constants (`constants.py`)

Extend the enum:

```python
class MatchStrategy(Enum):
    EXACT = auto()
    NOT_EQUAL = auto()
    RANGE = auto()
    GREATER_THAN = auto()
    LESS_THAN = auto()
    PREFIX = auto()
    SUFFIX = auto()
    CONTAINS = auto()
    REGEX = auto()
    SET_MEMBERSHIP = auto()
    SET_EXCLUSION = auto()
```

### Dimension Model (`dimension.py`)

**No new fields.** Only validation rules in `_validate_strategy_fields`:

```python
# REGEX, PREFIX, SUFFIX, CONTAINS — require string data_type
if self.match_strategy in (
    MatchStrategy.REGEX,
    MatchStrategy.PREFIX,
    MatchStrategy.SUFFIX,
    MatchStrategy.CONTAINS,
):
    if self.data_type is not str:
        raise ValueError(
            f"Dimension '{self.dimension_name}' uses {self.match_strategy.name} "
            f"but data_type is {self.data_type.__name__}, expected str"
        )

# GREATER_THAN, LESS_THAN — require numeric data_type
if self.match_strategy in (MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN):
    if self.data_type not in (int, float):
        raise ValueError(
            f"Dimension '{self.dimension_name}' uses {self.match_strategy.name} "
            f"but data_type is {self.data_type.__name__}, expected int or float"
        )

# SET_MEMBERSHIP, SET_EXCLUSION — no data_type constraint
# (the rule column holds a list of values, which can be any type)
```

Existing RANGE, EXACT, NOT_EQUAL validation is unchanged.

## Column Contracts

**SET_MEMBERSHIP / SET_EXCLUSION:**
- The `rule_field` column must contain list-typed values (e.g., Polars `list[str]`, `list[int]`)
- Users with delimited strings must split them before constructing the engine
- The ctx value is a scalar that is tested against each row's list

**PREFIX / SUFFIX / CONTAINS / REGEX:**
- The `rule_field` column must contain string values (patterns/substrings)
- Sentinels (`<NA>`, `<NOT_SET>`) are honored and produce UNKNOWN (0)
- Patterns can differ per row — the backend's native string operation handles column-reference patterns

**GREATER_THAN / LESS_THAN:**
- The `rule_field` column contains the threshold for each rule
- `UNKNOWN_NUMERIC` sentinel (-999999999) produces UNKNOWN (0) via `t_col`

## Testing Strategy

### Unit tests — `tests/test_compiler.py`

One test class per strategy, mirroring the existing `TestExactCompilation` structure. Minimum 3 tests each:
- Happy path (match produces 1)
- Non-match produces -1
- Unknown sentinel produces 0

Specific additions:

```python
class TestNotEqualCompilation:
    def test_not_equal_mismatch_produces_true
    def test_not_equal_match_produces_false
    def test_not_equal_unknown_rule_produces_unknown

class TestGreaterThanCompilation:
    def test_greater_than_true                  # ctx > rule → 1
    def test_greater_than_false                 # ctx < rule → -1
    def test_greater_than_equal_is_false        # ctx == rule → -1 (strict)
    def test_greater_than_unknown_rule          # rule=-999999999 → 0

class TestLessThanCompilation:
    # mirror of GreaterThan
    ...

class TestPrefixCompilation:
    def test_prefix_match
    def test_prefix_no_match
    def test_prefix_unknown_rule_produces_unknown
    def test_prefix_per_row_different_patterns  # key test for per-row capability

class TestSuffixCompilation:
    # mirror of Prefix
    ...

class TestContainsCompilation:
    # mirror of Prefix
    ...

class TestRegexCompilation:
    # REWRITE existing tests — behavior preserved but implementation changed
    def test_regex_match
    def test_regex_search_semantics
    def test_regex_unknown_pattern_produces_unknown
    def test_regex_per_row_different_patterns   # new — proves backend-agnostic

class TestSetMembershipCompilation:
    def test_set_membership_match               # ctx="AU", rule=["AU","NZ"] → 1
    def test_set_membership_no_match            # ctx="US", rule=["AU","NZ"] → -1
    def test_set_membership_unknown_ctx         # ctx=<NA> → 0
    def test_set_membership_empty_list          # ctx="AU", rule=[] → -1

class TestSetExclusionCompilation:
    # mirror of SetMembership with flipped expectations
    ...
```

### Backend agnosticism — `tests/test_compiler.py`

One parametrized smoke test per strategy:

```python
@pytest.mark.parametrize("backend", ["polars", "ibis", "narwhals"])
@pytest.mark.parametrize("strategy", [
    MatchStrategy.EXACT, MatchStrategy.NOT_EQUAL, MatchStrategy.RANGE,
    MatchStrategy.GREATER_THAN, MatchStrategy.LESS_THAN,
    MatchStrategy.PREFIX, MatchStrategy.SUFFIX, MatchStrategy.CONTAINS,
    MatchStrategy.REGEX, MatchStrategy.SET_MEMBERSHIP, MatchStrategy.SET_EXCLUSION,
])
def test_strategy_compiles_on_backend(backend, strategy):
    # Build minimal rule DataFrame in the named backend
    # Compile the strategy's expression
    # Assert compile() succeeds and returns a backend-native expression
```

### Validation tests — `tests/test_dimension.py`

New file (or additions to conftest-level tests):

```python
def test_greater_than_requires_numeric
def test_less_than_requires_numeric
def test_prefix_requires_string
def test_suffix_requires_string
def test_contains_requires_string
def test_regex_requires_string  # existing
def test_set_membership_any_data_type  # no validation error
```

### Integration tests — `tests/test_integration.py`

Add one new scenario that exercises the full catalog:

```python
class TestFraudDetectionScenario:
    """Uses EXACT, NOT_EQUAL, SET_MEMBERSHIP, GREATER_THAN, PREFIX together."""
    
    def test_hierarchical_fraud_rules_rank_correctly
    def test_most_specific_rule_wins_with_mixed_strategies
```

## Public API

No new public symbols beyond the new `MatchStrategy` enum values. All classes (`ExpressionRulesEngine`, `Dimension`, `DimensionsMetadata`, `RuleResult`, `DimensionCompiler`) keep their current interfaces.

```python
from mountainash_utils_rules import MatchStrategy

# All 11 available:
MatchStrategy.EXACT
MatchStrategy.NOT_EQUAL
MatchStrategy.RANGE
MatchStrategy.GREATER_THAN
MatchStrategy.LESS_THAN
MatchStrategy.PREFIX
MatchStrategy.SUFFIX
MatchStrategy.CONTAINS
MatchStrategy.REGEX
MatchStrategy.SET_MEMBERSHIP
MatchStrategy.SET_EXCLUSION
```

## Migration Notes

**Breaking change:** REGEX implementation changes from per-row native Polars (via `pl.struct().map_elements()`) to backend-agnostic `regex_contains(rule_col)`.

**Behavioral impact:** None expected. The new implementation uses Polars' `str.contains(pattern, literal=False)` under the hood for Polars backend, which is functionally equivalent to `re.search`. Existing REGEX rules continue to work unchanged.

**Users:** No migration required. New strategies are opt-in.

## Documentation Updates (end of work)

Update these principles documents:

1. **`mountainash-utils-rules` principles** — document all 11 strategies with:
   - Column value format
   - Expected `data_type`
   - Example rules and contexts
   - Ternary semantics

2. **`mountainash` principles** — document the string API consistency guarantee (all string comparison methods accept column references) and `t_is_in`/`t_is_not_in` list-column support.

3. **README.md** (if present) — update the strategy catalog table.

4. **CLAUDE.md** — update the Architecture section with the expanded strategy list.

## Dependencies

**No new dependencies.** The work leverages existing `mountainash` capabilities (now consistent after the upstream fixes).

**Removed dependencies:** `import polars as pl` and `import re` are removed from `compiler.py` — the compiler becomes pure expressions.
