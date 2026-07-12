# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mountain Ash Utils Rules is a high-performance Python package that provides revolutionary rule-based systems with multiple engine architectures. It features signed-integer ternary logic (-1/0/1), vectorized processing, and multiple performance-optimized engines including hybrid numpy/ibis processing and pure vectorized polars processing. The system achieves up to 93.9% performance improvements (16.40x speedup) through advanced mathematical optimization.

## Architecture

### Core Components

#### Original Architecture
- **RulesEngine**: The original engine that orchestrates rule evaluation and matching
- **RuleManager**: Manages rule storage and backend conversion for window function support
- **MetadataManager**: Handles dimension metadata and validation
- **ObservabilityManager**: Tracks intermediate rule evaluation states for debugging
- **MatchStrategyFactory**: Factory for creating appropriate match strategy implementations
- **BaseMatchStrategy**: Abstract base class for rule matching strategies
- **ContextHelper**: Utilities for context value extraction and type validation

#### Accumulator Engine
- **AccumulatorEngine**: Build/Apply engine that computes maximal consistent rule combinations with accumulated numerics. Python-controlled iteration with expression-based steps via `mountainash.relations`
- **AccumulatorCompiler**: Compiles `coalesce`, `compatible`, and NA flag expressions per dimension for the accumulator's recursive combination step
- **Lattice**: Data class wrapping the build-phase output — outermost rulesets as a backend DataFrame
- **AccumulatorResult**: Extends `RuleResult` with accumulator-specific accessors (accumulated numerics, provenance, combination depth)
- **Aggregate**: Pydantic model declaring a named numeric column and its monoidal operation (sum, min, max, product)
- **DimensionRole**: Enum (`CONSTRAINT` / `CONTEXT_KEY`) on `Dimension` — distinguishes lattice-partitioning dimensions from coalesced dimensions

#### Performance-Optimized Engines (Phases 2-3)
- **HybridRulesEngine**: Hybrid numpy/ibis engine with automatic optimization selection
- **NumpyRuleProcessor**: Vectorized numpy-based rule processor for performance
- **VectorizedRulesEngine**: Revolutionary polars-based engine achieving 93.9% performance improvement
- **PolarsRuleProcessor**: Pure vectorized polars processor with lazy evaluation

#### Ternary Logic Encoding
- Per-dimension match values use signed-integer ternary encoding: **1 = match, 0 = unknown, −1 = non-match**
- Defined and consumed in `constants.py`, `compiler.py`, and `result.py` (search for "ternary")
- Enables vectorized arithmetic combination of dimension match results across rules

## Match Strategies

The rules engine supports 11 match strategies via the `MatchStrategy` enum, compiled in `src/mountainash_rules/compiler.py`:

| Strategy | Rule Column Format | Data Type | Description |
|----------|-------------------|-----------|-------------|
| `EXACT` | Scalar value | any | Rule value equals context value |
| `NOT_EQUAL` | Scalar value | any | Rule value does not equal context value |
| `RANGE` | Two columns (min/max) | int, float | Context value within [min, max] |
| `GREATER_THAN` | Threshold value | int, float | Context value > rule threshold |
| `LESS_THAN` | Threshold value | int, float | Context value < rule threshold |
| `PREFIX` | Prefix string | str | Context value starts with rule |
| `SUFFIX` | Suffix string | str | Context value ends with rule |
| `CONTAINS` | Substring | str | Context value contains rule |
| `REGEX` | Pattern column | str | Rule column holds a per-row pattern (search semantics) |
| `CONTEXT_REGEX` | Literal `regex_pattern` on Dimension metadata | str | Global context validator shared by all rules |
| `SET_MEMBERSHIP` | List column | any | Context value is in rule's list |
| `SET_EXCLUSION` | List column | any | Context value is not in rule's list |

**Backend support:**
- 9 strategies (EXACT, NOT_EQUAL, RANGE, GREATER_THAN, LESS_THAN, PREFIX, SUFFIX, CONTAINS, CONTEXT_REGEX) compile cleanly on Polars, Ibis, and Narwhals backends
- `REGEX` (per-row pattern column) currently uses a Polars-native workaround (`ma.native(pl.col(ctx).str.contains(pl.col(rule)))`) pending upstream column-pattern `regex_contains` support in mountainash
- `SET_MEMBERSHIP` and `SET_EXCLUSION` currently use a Polars-native workaround (`ma.native(pl.col(...).list.contains(...))`) pending upstream `t_is_in`/`t_is_not_in` support for list-column references in mountainash

**Unknown handling:** Sentinel values (`<NA>` for strings, `-999999999` for numerics) in either rule or context columns produce UNKNOWN (0) ternary results, which count as wildcards in ranking but do not eliminate the rule.

**Adding strategies:** The process is documented in `docs/superpowers/specs/2026-04-07-extended-match-strategies-design.md`. Pattern: add enum value, add validation rule in `dimension.py`, add `_compile_<strategy>` method in `compiler.py`, add test class in `tests/test_compiler.py`.

### Package Structure

```
src/mountainash_rules/
├── __init__.py              # Package exports and public API
├── __version__.py           # Version information
├── accumulator_compiler.py  # Coalesce/compatible/NA flag expression compilation
├── accumulator_engine.py    # AccumulatorEngine: build/apply for rule combinations
├── accumulator_result.py    # AccumulatorResult extending RuleResult
├── aggregate.py             # Aggregate model for numeric accumulation
├── constants.py             # Constants, enums (MatchStrategy, DimensionRole), ternary values
├── compiler.py              # DimensionCompiler for filter engine expressions
├── context.py               # Context handling utilities with batch optimization
├── dimension.py             # Dimension metadata (with role field) and management
├── engine.py                # ExpressionRulesEngine (filter engine)
├── lattice.py               # Lattice data class wrapping build output
├── primes.py                # Prime table and checked multiply for combination DNA
└── result.py                # RuleResult base class

tests/
├── benchmarks/              # Performance benchmarking framework
│   ├── backend_comparison.py   # Engine performance comparisons
│   ├── performance_framework.py # Benchmarking infrastructure
│   └── test_data_generator.py   # Test data generation utilities
├── test_context.py          # Context handling tests
├── test_hybrid_engine.py    # Hybrid engine tests
├── test_numpy_processor.py  # Numpy processor tests
├── test_vectorized_engine.py # Vectorized engine tests
├── test_rule_engine.py      # Original engine tests
├── test_rule_manager.py     # Rule management tests
├── test_rule_strategies.py  # Strategy pattern tests
└── [other test files]       # Additional test modules

docs/
├── planning/                # Strategic planning documents
│   ├── implementation_roadmap.md    # Phase-based development roadmap
│   ├── phase4_testing_plan.md       # Comprehensive testing strategy
│   ├── phase_5_additive_rules_engine.md # Future additive rules architecture
│   └── phase_6_tensor_trading_intelligence.md # Advanced tensor applications
├── retrospectives/          # Phase retrospectives and learnings
│   ├── phase1_retrospective.md      # Phase 1 achievements analysis
│   ├── phase2_retrospective.md      # Phase 2 achievements analysis
│   └── phase3_retrospective.md      # Phase 3 achievements analysis
└── future opportunities/    # Advanced research and market analysis
    ├── market_domination_strategy.md # Market positioning strategy
    └── prime_based_research_analysis.md # Academic validation research
```


## Build/Test/Lint Commands

### Core Development Commands
- **Build**: `hatch build`
- **Lint**: `hatch run ruff:check` or `hatch run ruff:fix` to auto-fix
- **Type check**: `hatch run mypy:check`
- **Complexity analysis**: `hatch run radon:radon-cc` or `hatch run radon:radon-mi`

### Testing Commands
- **Standard tests**: `hatch run test:test` (includes coverage, reports)
- **Quick tests**: `hatch run test:test-quick` (no coverage overhead)
- **Coverage only**: `hatch run test:test-cov`
- **Single test**: `hatch run test:test-target tests/path/to/test_file.py::TestClass::test_function`
- **Performance benchmarks**: `hatch run test:test-perf`
- **Changed files only**: `hatch run test:test-changed`
- **CI full suite**: `hatch run test:test-ci`

### Benchmark Commands
- **Backend comparison**: `python tests/benchmarks/backend_comparison.py`
- **Comprehensive benchmarks**: `python run_comprehensive_benchmark.py`
- **Quick performance check**: `python quick_benchmark.py`

## Dependencies

### Core Dependencies
- **pandas>=2.2.0**: DataFrame operations and data manipulation
- **polars==1.16.0**: High-performance DataFrame library for vectorized processing
- **ibis-framework[polars,pandas,sqlite,duckdb]==10.4.0**: SQL expression compiler with multiple backend support
- **numpy**: High-performance numerical computing for vectorized operations

### Internal Mountain Ash Dependencies
- **mountainash-data**: Core data abstraction layer providing BaseDataFrame and IbisDataFrame classes
- **mountainash-dataframes**: Advanced DataFrame utilities and abstractions
- **mountainash-constants**: Shared constants and enums across Mountain Ash ecosystem

### Development Dependencies
- **pytest==8.3.5**: Testing framework
- **pytest-check, pytest-cov, pytest-mock**: Testing utilities for assertions, coverage, and mocking
- **ruff==0.3.7**: Code linting and formatting
- **mypy==1.10.1**: Static type checking
- **radon==6.0.1**: Code complexity analysis

## GitHub Actions Workflows

### Testing
- **python-run-pytest.yml**: Runs comprehensive test suite on pull requests
  - Supports Python 3.12 on Ubuntu 24.04
  - Includes coverage reporting via codecov
  - Loads and checks out Mountain Ash dependencies
  - Runs `hatch run test_github:test-cov` for coverage testing

- **python-run-ruff.yml**: Code linting and formatting checks
- **python-run-radon.yml**: Code complexity analysis

### Release Process
- **build-and-release-package.yml**: Automated release workflow
  - Triggers on merged pull requests to main/develop/release/feature/bugfix/hotfix branches
  - Supports production, RC, and beta releases via manual dispatch
  - Generates SBOMs (Software Bill of Materials)
  - Creates releases in GitHub and mountainash-wheels repository

### Branch Strategy
- `main`: Production releases (only release/* and hotfix/* branches)
- `develop`: Development and RC releases
- `feature/*`, `bugfix/*`, `hotfix/*`: Feature branches
- Protected branches require code owner approval

## Code Style Guidelines
- **Formatting**: Uses ruff for formatting and linting
- **Imports**: Standard lib first, third-party next, project imports last
- **Types**: Use typing annotations (e.g., `import typing as t`) for all functions
- **Naming**: CamelCase for classes, snake_case for functions/variables, UPPER_CASE for constants
- **Error handling**: Use ValueError for validation errors, custom exceptions for specific cases
- **Documentation**: Use Google-style docstrings for classes and methods
- **Organization**: Follow modular design with clear separation of concerns
- **Testing**: Create unit tests with appropriate markers (unit, integration, performance, benchmark)
- **Performance**: Maintain mathematical precision while optimizing for speed
- **Ternary logic**: Use the signed-integer encoding (1 match, 0 unknown, −1 non-match) for per-dimension match values

## Development Environments

### Hatch Environments
- `default`: Local development
- `test`: Local testing with extended pytest plugins
- `test_github`: GitHub Actions testing
- `build_github`: GitHub Actions building
- `ruff`: Linting and formatting
- `radon`: Complexity analysis
- `mypy`: Type checking

## Versioning Strategy

Uses CalVer (Calendar Versioning) with semantic versioning:
- Format: `YYYY.MM.MICRO`
- Release candidate: `YYYY.MM.0`
- Production: `YYYY.MM.1`
- Patches: `YYYY.MM.X`

## Engine Selection and Usage

### Performance-Optimized Engine Selection

```python
from mountainash_rules import (
    # Original engine
    RulesEngine,
    # Performance engines
    create_ultra_performance_engine,  # VectorizedRulesEngine - 93.9% improvement
    create_performance_optimized_engine,  # HybridRulesEngine - 75.2% improvement
    create_reliability_focused_engine,  # Fallback with error handling
    # Core components
    DimensionsMetadata, Dimension, MatchStrategy
)
from mountainash_data import DataFrameFactory
import polars as pl
from pydantic import BaseModel

# Define your context model
class Context(BaseModel):
    DIM_1: str
    DIM_2: int
    DIM_3: str

# Create sample rules
rules_df = pl.DataFrame({
    "rule_name": ["rule_1", "rule_2", "rule_3"],
    "DIM_1": ["A", "B", "C"],
    "DIM_2_MIN": [0, 10, 20],
    "DIM_2_MAX": [9, 19, 29],
    "DIM_3": ["X.*", "Y.*", "Z.*"]
})
rules = DataFrameFactory.create_ibis_dataframe_object_from_dataframe(rules_df, ibis_backend_schema="polars")

# Define dimension metadata
dimension_metadata = DimensionsMetadata(
    dimensions=[
        Dimension(dimension_name="DIM_1", match_strategy=MatchStrategy.EXACT, data_type=str),
        Dimension(dimension_name="DIM_2", match_strategy=MatchStrategy.RANGE, data_type=int, range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
        Dimension(dimension_name="DIM_3", match_strategy=MatchStrategy.REGEX, data_type=str)
    ]
)

# Choose engine based on requirements:

# Ultra-high performance (93.9% improvement)
rules_engine = create_ultra_performance_engine(rules=rules, dimension_metadata=dimension_metadata)

# OR balanced performance with reliability
# rules_engine = create_performance_optimized_engine(rules=rules, dimension_metadata=dimension_metadata)

# OR original engine for compatibility
# rules_engine = RulesEngine(rules=rules, dimension_metadata=dimension_metadata)

# Apply rules to a context
context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])

# Process the result
matched_rules = result.filter(ibis._.keep == True)
print(f"Number of matched rules: {matched_rules.count()}")
```

### Performance Benchmarking

```python
# Run comprehensive performance comparison
from tests.benchmarks.backend_comparison import TestBackendPerformance

# Compare all engines
benchmarker = TestBackendPerformance()
benchmarker.test_backend_initialization()
benchmarker.test_performance_comparison()
```

## Key Innovation: Ternary Match Logic

The system encodes per-dimension match results using a signed-integer ternary scheme:
- **1**: Condition matches
- **0**: Condition unknown / dimension absent from rule
- **−1**: Condition does not match

This enables:
- Vectorized arithmetic combination of dimension results across rules
- Cheap aggregation (sum/min) for whole-rule match decisions
- Up to 16.40x performance improvements via the polars/ibis backends

> **Historical note:** earlier planning documents describe a prime-based encoding (PRIME_TRUE=2, PRIME_FALSE=3, PRIME_UNKNOWN=5). That scheme was never implemented in the source — the actual encoding is the signed-integer one above. A separate prime-product mechanism is proposed for the *additive/accumulator* engine described in `docs/superpowers/specs/`, but it is unrelated to per-dimension ternary values: it identifies *combinations of rules*, not match outcomes.

## Performance Architecture Evolution

### Phase 1: Context Optimization (27.8% improvement)
- Batch context extraction
- Prime-based ternary flag optimization
- DuckDB backend migration

### Phase 2: Hybrid Processing (75.2% improvement)  
- Numpy vectorization for small datasets
- Ibis fallback for complex operations
- Automatic optimization selection

### Phase 3: Vectorized Engine (93.9% improvement)
- Pure polars lazy evaluation
- Advanced query plan optimization
- Multi-core parallel processing
- Intelligent rule ordering and caching

## Documentation Files

- **README.md**: Package overview, installation, and usage examples
- **CLAUDE.md**: This file - development guidance for Claude Code
- **docs/planning/**: Strategic roadmaps and future phases
- **docs/retrospectives/**: Phase achievement analyses
- **docs/future opportunities/**: Market research and advanced concepts
- **tests/benchmarks/**: Performance testing framework

## License
MIT License
