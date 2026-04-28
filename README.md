# mountainash-utils-rules

![Python](https://img.shields.io/badge/python-3.12-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Backend](https://img.shields.io/badge/backends-7-purple)

A rules engine for evaluating business rules against a context and ranking matches by specificity. Define dimensions with match strategies (exact, range, regex, prefix, set membership, etc.), pass a context, and get back the most specific matching rules — ranked, explained, and ready to use.

Built on [mountainash](https://github.com/mountainash-io/mountainash) expressions for backend-agnostic evaluation. Rules compile once; contexts evaluate in a single vectorised pass. Supports Polars, Pandas, Ibis (DuckDB, SQLite, Polars), and Narwhals backends with zero code changes.

## Quick Start

```python
import polars as pl
from mountainash_utils_rules import (
    ExpressionRulesEngine, Dimension, DimensionsMetadata, MatchStrategy,
)
from pydantic import BaseModel

# 1. Define your rules as a DataFrame
rules = pl.DataFrame({
    "rule_name": ["premium_au", "standard", "fallback"],
    "region":    ["AU",         "AU",       "<NA>"],       # <NA> = wildcard
    "spend_min": [1000,         0,          -999999999],   # -999999999 = wildcard
    "spend_max": [9999,         999,        -999999999],
})

# 2. Declare how each dimension matches
metadata = DimensionsMetadata(dimensions=[
    Dimension(dimension_name="region", match_strategy=MatchStrategy.EXACT, data_type=str),
    Dimension(
        dimension_name="spend", match_strategy=MatchStrategy.RANGE, data_type=int,
        range_min_field="spend_min", range_max_field="spend_max",
    ),
])

# 3. Build the engine (compiles expressions once)
engine = ExpressionRulesEngine(rules=rules, dimension_metadata=metadata)

# 4. Evaluate a context
class CustomerContext(BaseModel):
    region: str
    spend: int

result = engine.evaluate(CustomerContext(region="AU", spend=1500))

print(result.count)                # 2 — premium_au and fallback survive
print(result.best_match)           # premium_au (matches both dimensions)
print(result.explain("premium_au"))  # {"region": 1, "spend": 1} — both match
print(result.explain("fallback"))    # {"region": 0, "spend": 0} — both wildcard
```

## Match Strategies

| Strategy | Rule Column | Description |
|----------|-------------|-------------|
| `EXACT` | Scalar value | Context value equals rule value |
| `NOT_EQUAL` | Scalar value | Context value differs from rule value |
| `RANGE` | Two columns (min/max) | Context value within [min, max] |
| `GREATER_THAN` | Threshold | Context value > rule threshold |
| `LESS_THAN` | Threshold | Context value < rule threshold |
| `PREFIX` | String | Context value starts with rule value |
| `SUFFIX` | String | Context value ends with rule value |
| `CONTAINS` | Substring | Context value contains rule value |
| `REGEX` | Pattern | Context value matches rule pattern |
| `SET_MEMBERSHIP` | List | Context value is in rule's list |
| `SET_EXCLUSION` | List | Context value is not in rule's list |

Wildcard values (`<NA>` for strings, `-999999999` for numerics) produce an UNKNOWN result — the rule is not eliminated but scores lower on specificity.

## Backend Support

The engine is backend-agnostic. Pass any supported DataFrame type as `rules`:

| Backend | Type |
|---------|------|
| Polars | `pl.DataFrame` |
| Pandas | `pd.DataFrame` |
| Narwhals (Polars) | `nw.from_native(pl.DataFrame(...))` |
| Narwhals (Pandas) | `nw.from_native(pd.DataFrame(...))` |
| Ibis (DuckDB) | `ibis.duckdb.connect().create_table(...)` |
| Ibis (Polars) | `ibis.polars.connect().create_table(...)` |
| Ibis (SQLite) | `ibis.sqlite.connect().create_table(...)` |

All backends produce identical results. Polars is recommended for performance.

## Installation

```bash
# Development install with hatch
git clone https://github.com/mountainash-io/mountainash-utils-rules.git
cd mountainash-utils-rules
hatch env create
```

Requires sibling checkouts of `mountainash`, `mountainash-data`, and `mountainash-settings` (see `hatch.toml` for path configuration).

## Development

| Command | Description |
|---------|-------------|
| `hatch run test:test-quick` | Run tests (no coverage) |
| `hatch run test:test` | Run tests with coverage reports |
| `hatch run test:test-target-quick tests/path.py -v` | Run specific tests |
| `hatch run test:test-perf` | Run performance benchmarks |
| `hatch run test:test-perf-save` | Benchmarks + save JSON baseline |
| `hatch run ruff:check` | Lint |
| `hatch run ruff:fix` | Lint + auto-fix |
| `hatch run mypy:check` | Type check |
| `hatch run radon:radon-cc` | Cyclomatic complexity |

## Architecture

The engine uses **signed-integer ternary logic** (-1 = non-match, 0 = unknown, 1 = match) to evaluate each rule dimension independently, then combines results in a single vectorised pass:

1. **Compile** — `DimensionCompiler` converts dimension metadata into backend-agnostic expression templates at construction time.
2. **Bind** — Context values are injected as literal columns alongside the rules DataFrame.
3. **Evaluate** — All dimension expressions execute in one `with_columns` call, producing a ternary value per dimension per rule.
4. **Rank** — Rules with any -1 are eliminated. Survivors are ranked by **specificity** (count of 1s). More specific rules rank higher.

The engine and result layer use only `mountainash.relations` and `mountainash.expressions` — no direct backend imports. See [CLAUDE.md](CLAUDE.md) for full architectural details.

## Mountain Ash Ecosystem

This package is part of the [Mountain Ash](https://github.com/mountainash-io) data framework ecosystem.

## License

MIT — see [LICENSE](LICENSE) for details.
