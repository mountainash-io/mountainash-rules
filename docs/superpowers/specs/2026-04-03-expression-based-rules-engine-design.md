# Expression-Based Rules Engine — Design Spec

**Date:** 2026-04-03
**Status:** Approved
**Scope:** Complete rearchitecture of mountainash-utils-rules to use mountainash

## Summary

Replace the iterative dimension-by-dimension rule evaluation engine with a single-pass expression-based architecture. Rules are compiled into backend-agnostic ternary expressions at construction time, then evaluated against contexts in one vectorized DataFrame operation.

This is a clean break — all existing engines (`RulesEngine`, `HybridRulesEngine`, `VectorizedRulesEngine`) and their supporting infrastructure are removed and replaced by a single `ExpressionRulesEngine`.

## Goals

1. **Eliminate iterative evaluation** — current engine applies ~10 mutate() calls per dimension; new engine evaluates all dimensions in a single `with_columns()` call
2. **Leverage mountainash** — build-then-compile pattern, ternary logic, backend agnosticism
3. **Backend-agnostic** — same engine works with Polars, Ibis, and Narwhals DataFrames; test primarily with Polars
4. **Dual API** — convenience path (DataFrame + dimension metadata) and advanced path (raw expressions)
5. **Built-in observability** — per-dimension ternary columns in results, no separate observer infrastructure
6. **Hierarchical rule support** — specificity-based ranking enables smart fallbacks from specific rules to general defaults

## Non-Goals

- Backward compatibility with existing engine APIs
- Supporting pandas DataFrames directly (Narwhals covers pandas interop)
- Batch context evaluation (evaluating many contexts at once — future enhancement)

## Architecture

### Package Structure

```
src/mountainash_rules/
├── __init__.py                  # Public API exports
├── __version__.py               # Version (unchanged)
├── constants.py                 # MatchStrategy enum, context column prefix, sentinel values
├── dimension.py                 # Dimension + DimensionsMetadata (Pydantic models)
├── compiler.py                  # Translates Dimension metadata -> expression templates
├── engine.py                    # ExpressionRulesEngine
├── result.py                    # RuleResult — ranked survivors with observability
└── context.py                   # Context value extraction
```

**Removed:**
- `rule_manager.py` — backend management no longer needed
- `rule_strategies.py` — match strategies become expression compilation in compiler.py
- `hybrid_engine.py`, `vectorized_engine.py`, `numpy_processor.py` — replaced by single engine
- `observer.py` — observability is built into result columns
- `enhanced_ternary_processor.py` — superseded

### Data Flow

**Phase 1 — Construction (once per rule set):**

```
DimensionsMetadata + Rules DataFrame
        |
    DimensionCompiler
        |
    Dict[str, Expression]   (one expression template per dimension)
```

**Phase 2 — Evaluation (per context):**

```
Context + Rules DataFrame + Expression Templates
        |
    1. Augment rules df with context literal columns (__ctx_DIM_1="A", __ctx_DIM_2=5, etc.)
        |
    2. Compile each dimension expression -> per-dimension ternary column (1/0/-1)
        |
    3. Survival filter: no dimension is FALSE (-1)
        |
    4. Specificity: count of TRUE (1) values across dimensions
        |
    5. Rank survivors by specificity DESC
        |
    RuleResult (survivors + ternary columns + ranking)
```

Steps 2-5 execute as a single chained DataFrame operation.

## Components

### DimensionCompiler (`compiler.py`)

Translates `Dimension` metadata into mountainash-expression templates. One method per match strategy.

Context values are referenced via placeholder columns with prefix `__ctx_`. These columns are populated at evaluation time by the engine.

```python
class DimensionCompiler:
    CTX_PREFIX = "__ctx_"

    def compile_dimensions(self, metadata: DimensionsMetadata) -> dict[str, Expression]:
        return {
            dim.dimension_name: self._compile_dimension(dim)
            for dim in metadata.dimensions
        }

    def _compile_dimension(self, dim: Dimension) -> Expression:
        match dim.match_strategy:
            case MatchStrategy.EXACT:
                return self._compile_exact(dim)
            case MatchStrategy.RANGE:
                return self._compile_range(dim)
            case MatchStrategy.REGEX:
                return self._compile_regex(dim)

    def _compile_exact(self, dim: Dimension) -> Expression:
        rule_col = ma.t_col(dim.rule_field, unknown={UNKNOWN, UNKNOWN_NUMERIC})
        ctx_col = ma.t_col(self.CTX_PREFIX + dim.dimension_name, unknown={UNKNOWN, UNKNOWN_NUMERIC})
        return rule_col.t_eq(ctx_col)

    def _compile_range(self, dim: Dimension) -> Expression:
        ctx_col = ma.t_col(self.CTX_PREFIX + dim.dimension_name, unknown={UNKNOWN_NUMERIC})
        min_col = ma.t_col(dim.range_min_field, unknown={UNKNOWN_NUMERIC})
        max_col = ma.t_col(dim.range_max_field, unknown={UNKNOWN_NUMERIC})
        lower = min_col.t_le(ctx_col) if dim.range_min_inclusive else min_col.t_lt(ctx_col)
        upper = max_col.t_ge(ctx_col) if dim.range_max_inclusive else max_col.t_gt(ctx_col)
        return lower.t_and(upper)

    def _compile_regex(self, dim: Dimension) -> Expression:
        rule_col = ma.t_col(dim.rule_field, unknown={UNKNOWN})
        ctx_col = ma.col(self.CTX_PREFIX + dim.dimension_name)
        return ctx_col.regex_contains(rule_col)
```

**Key design points:**

- `t_col()` with `unknown=` sentinel sets means `<NA>` and `-999999999` automatically become UNKNOWN(0) — no separate unknown-checking steps
- RANGE respects inclusive/exclusive bounds from metadata
- REGEX uses `regex_contains()` (search semantics, not anchored match)
- All expressions are backend-agnostic ASTs until compile time

### ExpressionRulesEngine (`engine.py`)

```python
class ExpressionRulesEngine:
    def __init__(
        self,
        rules: DataFrame,
        dimension_metadata: DimensionsMetadata = None,
        dimension_expressions: dict[str, Expression] = None,
    ):
        # Must provide exactly one of dimension_metadata or dimension_expressions
        if dimension_metadata:
            compiler = DimensionCompiler()
            self._expressions = compiler.compile_dimensions(dimension_metadata)
            self._metadata = dimension_metadata
        elif dimension_expressions:
            self._expressions = dimension_expressions
            self._metadata = None

        self._rules = rules

    def evaluate(
        self,
        context: BaseModel | dict,
        dimensions: list[str] | None = None,
        top_n: int | None = None,
        min_specificity: int | None = None,
        include_observability: bool = True,
    ) -> RuleResult:
        ...
```

**Parameters:**

| Parameter | Purpose |
|-----------|---------|
| `dimensions` | Subset of dimensions to evaluate (default: all) |
| `top_n` | Return top N matches by specificity (default: all survivors) |
| `min_specificity` | Minimum hard-match count to include (default: no minimum) |
| `include_observability` | Include per-dimension ternary columns in result (default: True) |

**Two construction paths:**
- **Convenience:** `dimension_metadata=DimensionsMetadata(...)` — compiler generates expressions
- **Advanced:** `dimension_expressions={"dim": ma.col(...).t_eq(...)}` — user provides expressions directly

### Evaluation Pipeline (single-pass chain)

```python
def _evaluate(self, augmented_df, active_dims):
    # Step 1: Compile each dimension expression into a named ternary column
    dim_columns = [
        self._expressions[dim_name]
            .name.alias(f"__t_{dim_name}")
            .compile(augmented_df, booleanizer=None)  # Raw -1/0/1
        for dim_name in active_dims
    ]

    # Step 2: All ternary columns + survival + specificity + rank in one chain
    t_cols = [col(f"__t_{d}") for d in active_dims]

    result = (
        augmented_df
        .with_columns(dim_columns)                                       # All dimensions at once
        .with_columns(
            min_horizontal(*t_cols).ge(0).alias("__survived"),           # No -1 present
            sum_horizontal(*[c.eq(1) for c in t_cols]).alias("__specificity"),  # Count of hard matches
        )
        .filter(col("__survived"))                                       # Keep survivors only
        .sort("__specificity", descending=True)                          # Most specific first
        .with_row_index("__rank", offset=1)                              # 1-based ranking
        .drop("__survived", *ctx_columns)                                # Clean up temp columns
    )
    return result
```

**Performance characteristics:**
- Zero iteration — all dimensions in a single `with_columns()` call
- Backend-native regex — no pandas materialization
- Lazy evaluation — backend optimizes the entire chain before executing
- Ternary logic is arithmetic — survival is `min >= 0`, specificity is `sum(x == 1)`
- Context binding is cheap — literal columns are scalar broadcasts in lazy evaluation

### RuleResult (`result.py`)

```python
class RuleResult:
    def __init__(self, dataframe: DataFrame, active_dimensions: list[str]):
        self._df = dataframe
        self._active_dimensions = active_dimensions

    @property
    def survivors(self) -> DataFrame:
        """All rules that survived, ranked by specificity."""
        return self._df

    @property
    def best_match(self) -> DataFrame:
        """Single most specific surviving rule."""
        return self._df.head(1)

    @property
    def specificity_scores(self) -> DataFrame:
        """Survivors with specificity and dimension breakdown."""
        ...

    def explain(self, rule_name: str) -> dict[str, int]:
        """Per-dimension ternary values for a specific rule.
        Returns e.g. {"DIM_1": 1, "DIM_2": 1, "DIM_3": 0}
        """
        ...

    def at_least(self, n: int) -> DataFrame:
        """Rules with specificity >= n."""
        ...

    @property
    def count(self) -> int:
        """Number of survivors."""
        ...
```

**Result DataFrame columns:**

| Column | Source | Description |
|--------|--------|-------------|
| *original rule columns* | Rules df | Passed through unchanged |
| `__t_{DIM_NAME}` | Per-dimension expression | Ternary value: 1 (match), 0 (unknown/wildcard), -1 (not present in survivors) |
| `__specificity` | `sum_horizontal(__t_* == 1)` | Count of hard matches |
| `__rank` | Row number by specificity DESC | 1 = most specific |

The `__` prefix prevents collision with rule columns. `__t_*` columns are omitted when `include_observability=False`.

### Ternary Logic Mapping

The current prime-based system (2/3/5) is replaced by mountainash' integer sentinels:

| Concept | Old (prime) | New (expressions) |
|---------|-------------|-------------------|
| Hard match | PRIME_TRUE = 3 | TRUE = 1 |
| Unknown/wildcard | PRIME_UNKNOWN = 5 | UNKNOWN = 0 |
| Non-match | PRIME_FALSE = 2 | FALSE = -1 |

Sentinel values (`<NA>`, `-999999999`) are handled by `t_col(unknown={...})` — the expression library converts them to UNKNOWN(0) automatically.

**Survival logic:** A rule survives if no dimension evaluates to FALSE (-1). Equivalently: `min(all dimension ternary values) >= 0`.

**Specificity ranking:** Count of TRUE (1) values across dimensions. More hard matches = more specific = higher priority. This enables hierarchical fallback: a general rule with many unknowns (0s) survives but ranks below a specific rule with hard matches (1s).

## Public API

```python
# Engine
from mountainash_rules import ExpressionRulesEngine

# Metadata (convenience path)
from mountainash_rules import Dimension, DimensionsMetadata, MatchStrategy

# Result
from mountainash_rules import RuleResult

# Compiler (advanced users)
from mountainash_rules import DimensionCompiler
```

**Convenience path:**
```python
engine = ExpressionRulesEngine(
    rules=my_polars_df,
    dimension_metadata=DimensionsMetadata(dimensions=[
        Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(dimension_name="amount", match_strategy=MatchStrategy.RANGE, data_type=float,
                  range_min_field="amount_min", range_max_field="amount_max"),
    ])
)
result = engine.evaluate(context={"region": "AU", "amount": 150.0})
best = result.best_match
```

**Advanced path:**
```python
import mountainash.expressions as ma

engine = ExpressionRulesEngine(
    rules=my_polars_df,
    dimension_expressions={
        "region": ma.t_col("region", unknown={"<NA>"}).t_eq(ma.t_col("__ctx_region", unknown={"<NA>"})),
        "margin": ma.col("base_rate").multiply(ma.col("margin_pct")),
    }
)
result = engine.evaluate(context={"region": "AU", "margin": 0.05})
```

**Removed from public API:**
- `RulesEngine`, `HybridRulesEngine`, `VectorizedRulesEngine`
- `create_ultra_performance_engine`, `create_performance_optimized_engine`, `create_reliability_focused_engine`
- `RuleManager`, `ObservabilityManager`, `MatchStrategyFactory`
- `RuleTrinaryFlags`, `RuleConstants`
- `BaseMatchStrategy` and all strategy subclasses

## Testing Strategy

```
tests/
├── test_compiler.py          # DimensionCompiler: metadata -> expressions
├── test_engine.py            # ExpressionRulesEngine: evaluate() behavior
├── test_result.py            # RuleResult: ranking, explain, filtering
├── test_integration.py       # End-to-end scenarios with real rule sets
└── conftest.py               # Shared fixtures
```

**test_compiler.py:**
- EXACT compiles to `t_eq`, handles unknowns as UNKNOWN(0)
- RANGE compiles to `t_le/t_ge` with inclusive/exclusive variants
- REGEX compiles to `regex_contains`, handles unknown patterns
- Expressions are backend-agnostic (compile to Polars, Ibis, Narwhals)

**test_engine.py:**
- Survival: rules with any FALSE dimension are eliminated
- Specificity ranking: more hard matches = higher rank
- Hierarchical fallback: general rule survives but ranks below specific
- Subset dimensions: evaluating with partial context
- `top_n` and `min_specificity` filtering
- Advanced path: custom expressions work same as metadata-compiled
- Multi-backend: same results on Polars, Ibis, Narwhals

**test_result.py:**
- `best_match` returns top-ranked survivor
- `explain()` returns correct per-dimension ternary values
- `at_least(n)` filters by specificity
- Observability columns present/absent based on flag

**test_integration.py:**
- Pricing carve-out: general rate -> client-specific -> product-specific override
- Entity pool: hierarchical rules with increasing specificity
- No match: all rules eliminated, empty result
- Tie handling: multiple rules with same specificity

**All tests parametrized across backends:**
```python
@pytest.mark.parametrize("backend", ["polars", "ibis", "narwhals"])
```

## Dependencies

**Added:**
- `mountainash` — core expression library

**Retained:**
- `polars` — primary test backend
- `ibis-framework` — secondary backend support
- `narwhals` — tertiary backend support
- `pydantic` — Dimension/DimensionsMetadata models

**Removed (no longer directly used):**
- `numpy` — was used by numpy_processor
- `pandas` — was used by regex strategy's row-by-row evaluation

## Migration Notes

- No downstream consumers — clean break, no migration period needed
- Existing test data (rules DataFrames, dimension metadata) can be reused with the new engine
- Regex patterns may need `^` prefix added if they relied on implicit start-anchoring from `re.match()`
- Version bump to new CalVer major to signal the breaking change
