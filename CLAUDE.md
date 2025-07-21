# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mountain Ash Utils Rules is a Python package that provides utility functions for rule-based systems. It enables flexible rule definition, evaluation, and matching across multiple dimensions with support for exact matching, range matching, and regex pattern matching.

## Architecture

### Core Components

- **RulesEngine**: The main engine that orchestrates rule evaluation and matching
- **RuleManager**: Manages rule storage and backend conversion for window function support
- **MetadataManager**: Handles dimension metadata and validation
- **ObservabilityManager**: Tracks intermediate rule evaluation states for debugging
- **MatchStrategyFactory**: Factory for creating appropriate match strategy implementations
- **BaseMatchStrategy**: Abstract base class for rule matching strategies
- **ContextHelper**: Utilities for context value extraction and type validation

### Package Structure

```
src/mountainash_utils_rules/
├── __init__.py              # Package exports and public API
├── __version__.py           # Version information
├── constants.py             # Constants and enums (MatchStrategy, RuleConstants, etc.)
├── context.py               # Context handling utilities
├── dimension.py             # Dimension metadata and management
├── engine.py                # Main RulesEngine implementation
├── observer.py              # Observability and debugging support
├── rule_manager.py          # Rule storage and backend management
└── rule_strategies.py       # Match strategy implementations

tests/
├── test_context_manager.py     # Context handling tests
├── test_metadata_manager.py    # Metadata management tests
├── test_rule_engine.py         # Main engine tests
├── test_rule_manager.py        # Rule management tests
├── test_rule_strategies.py     # Strategy pattern tests
└── test_tracability_manager.py # Observability tests

notebooks/
├── ruletest.ipynb           # Rule testing examples
├── test_development.ipynb   # Development testing
└── test_factory.ipynb       # Factory pattern examples
```


## Build/Test/Lint Commands
- Build: `hatch build`
- Lint: `hatch run ruff:check` or `hatch run ruff:fix` to auto-fix
- Tests: `hatch run test:test` or `hatch run test:cov` for coverage
- Single test: `pytest tests/path/to/test_file.py::TestClass::test_function -v`
- Type check: `hatch run mypy:check`

## Dependencies

### Core Dependencies
- **pandas>=2.2.0**: DataFrame operations and data manipulation
- **polars==1.16.0**: High-performance DataFrame library
- **ibis-framework[polars,pandas,sqlite,duckdb]==10.4.0**: SQL expression compiler with multiple backend support

### Internal Mountain Ash Dependencies
- **mountainash-data**: Core data abstraction layer providing BaseDataFrame and IbisDataFrame classes

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
- Formatting: Uses ruff for formatting and linting
- Imports: Standard lib first, third-party next, project imports last
- Types: Use typing annotations (e.g., `import typing as t`) for all functions
- Naming: CamelCase for classes, snake_case for functions/variables, UPPER_CASE for constants
- Error handling: Use ValueError for validation errors, custom exceptions for specific cases
- Documentation: Use Google-style docstrings for classes and methods
- Organization: Follow modular design with clear separation of concerns
- Testing: Create unit tests with appropriate markers (unit, integration, performance)

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

## Usage Example

```python
from mountainash_utils_rules import RulesEngine, DimensionsMetadata, Dimension, MatchStrategy
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

# Create RulesEngine instance
rules_engine = RulesEngine(rules=rules, dimension_metadata=dimension_metadata)

# Apply rules to a context
context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])

# Process the result
matched_rules = result.filter(ibis._.keep == True)
print(f"Number of matched rules: {matched_rules.count()}")
```

## Documentation Files

- **README.md**: Package overview, installation, and usage examples
- **CONTRIBUTING.md**: Contribution guidelines
- **TESTING.md**: Testing documentation and strategies
- **RELEASE.md**: Release process and versioning information
- **CLAUDE.md**: This file - development guidance for Claude Code

## License
MIT License
