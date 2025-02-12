![Pytest](https://github.com/mountainash-io/mountainash-utils-rules/actions/workflows/python-run-pytest.yml/badge.svg)
![Radon](https://github.com/mountainash-io/mountainash-utils-rules/actions/workflows/python-run-radon.yml/badge.svg)
![Ruff](https://github.com/mountainash-io/mountainash-utils-rules/actions/workflows/python-run-ruff.yml/badge.svg)
[![codecov](https://codecov.io/github/mountainash-io/mountainash-utils-rules/graph/badge.svg?token=URHATA84P6)](https://codecov.io/github/mountainash-io/mountainash-utils-rules)

# Mountain Ash - Utils - Rules

Mountain Ash - Utils - Rules is a Python package that provides utility functions for rule-based systems.

## Installation

You can install the package using pip:

```bash
pip install mountainash_utils_rules
```

## Dependencies

This package requires Python 3.10 or later. The main dependencies are:

- pandas==2.2.2
- polars==1.16.0
- ibis-framework[polars,pandas,sqlite,duckdb]==9.1.0

## Usage

Here's a basic example of how to use the `mountainash_utils_rules` package:

```python
from mountainash_utils_rules import RulesEngine, RuleMetadata, DimensionMetadata, RuleType
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

# Define rule metadata
rule_metadata = RuleMetadata(
    dimensions=[
        DimensionMetadata(dimension_name="DIM_1", rule_type=RuleType.EXACT, data_type="string"),
        DimensionMetadata(dimension_name="DIM_2", rule_type=RuleType.RANGE, data_type="int", range_min_field="DIM_2_MIN", range_max_field="DIM_2_MAX"),
        DimensionMetadata(dimension_name="DIM_3", rule_type=RuleType.REGEX, data_type="string")
    ]
)

# Create RulesEngine instance
rules_engine = RulesEngine(rules=rules, rule_metadata=rule_metadata)

# Apply rules to a context
context = Context(DIM_1="A", DIM_2=5, DIM_3="XYZ")
result = rules_engine.apply_context_rules_engine(context, ["DIM_1", "DIM_2", "DIM_3"])

# Process the result
matched_rules = result.filter(ibis._.keep == True)
print(f"Number of matched rules: {matched_rules.count()}")
print(f"First matched rule: {matched_rules.get_first_row_as_dict()['rule_name']}")
```

This example demonstrates how to create a RulesEngine, define rules and metadata, and apply them to a given context.

## Development

This project uses [Hatch](https://github.com/pypa/hatch) for development and testing. Make sure you have Hatch installed before proceeding.

### Running Tests

To run the tests, use the following Hatch commands:

```bash
# Run tests
hatch run test:test

# Run tests with coverage
hatch run test:cov

# Generate HTML coverage report
hatch run test:cov-html
```

### Linting and Type Checking

The project uses Ruff for linting and Mypy for type checking:

```bash
# Run Ruff linter
hatch run ruff:check

# Auto-fix Ruff linting issues
hatch run ruff:fix

# Run Mypy type checker
hatch run mypy:check
```

### Code Complexity Analysis

You can analyze the code complexity using Radon:

```bash
# Run Radon complexity check
hatch run radon:radon-cc

# Run Radon maintainability index
hatch run radon:radon-mi
```

## License

This project is licensed under the MIT License.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Issues

If you encounter any problems, please file an issue along with a detailed description.

## Links

- Documentation: https://github.com/mountainash-io/mountainash-utils-rules#readme
- Source Code: https://github.com/mountainash-io/mountainash-utils-rules
- Issue Tracker: https://github.com/mountainash-io/mountainash-utils-rules/issues