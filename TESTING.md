# Testing Mountain Ash Utils - Rules

This document outlines the testing procedures for the Mountain Ash Utils - Rules project, including how to run tests locally and via GitHub Actions.

## Table of Contents

1. [Local Testing](#local-testing)
2. [Test Commands Reference](#test-commands-reference)
3. [Coverage Reports](#coverage-reports)
4. [GitHub Actions Testing](#github-actions-testing)
5. [Testing Dependencies](#testing-dependencies)

## Local Testing

We use [Hatch](https://hatch.pypa.io/) to manage our development environment and run tests. To run tests locally:

1. Ensure you have Hatch installed:
   ```bash
   pip install hatch
   ```

2. Run the comprehensive test suite (recommended for daily use):
   ```bash
   hatch run test:test
   ```
   This command runs tests with coverage and generates all coverage reports (JSON, XML, HTML) plus a terminal summary.

## Test Commands Reference

### Core Testing Commands (Use these daily)

- **Full test suite with coverage:**
  ```bash
  hatch run test:test
  ```
  Runs pytest with coverage, generates JSON/XML/HTML reports, and shows missing coverage.

- **GitHub Actions test with coverage:**
  ```bash
  hatch run test_github:test-cov
  ```
  Runs tests with coverage and generates XML output for CI.

- **Quick testing (no coverage overhead):**
  ```bash
  hatch run test:test-quick
  ```
  Fast iteration testing without coverage collection.

### Targeted Testing (For debugging specific issues)

- **Test specific files/tests with coverage:**
  ```bash
  hatch run test:test-target tests/test_rule_engine.py::TestRulesEngine::test_specific_method
  ```

- **Test specific files/tests without coverage (fastest):**
  ```bash
  hatch run test:test-target-quick tests/test_rule_engine.py
  ```

- **Test only changed files with coverage:**
  ```bash
  hatch run test:test-changed
  ```

- **Test only changed files without coverage:**
  ```bash
  hatch run test:test-changed-quick
  ```

### Specialized Testing

- **Performance benchmarks only:**
  ```bash
  hatch run test:test-perf
  ```

- **Test by markers:**
  ```bash
  hatch run test:test-unit        # Unit tests only
  hatch run test:test-integration # Integration tests only
  hatch run test:test-performance # Performance tests only
  ```

### CI/Reporting Commands

- **Full CI suite with structured reports:**
  ```bash
  hatch run test:test-ci
  ```
  Generates JSON test reports, JUnit XML, and all coverage formats.

## Coverage Reports

When you run tests with coverage, several output formats are generated:

### Local Coverage Files Generated

After running `hatch run test:test` or any coverage-enabled command, you'll find:

- **`coverage.json`** - Machine-readable coverage data in JSON format
- **`coverage.xml`** - Coverage data in XML format (for CI tools)
- **`htmlcov/`** - Complete HTML coverage report directory
  - Open `htmlcov/index.html` in your browser for interactive coverage exploration
- **`junit.xml`** - JUnit test results format
- **`pytest_report.json`** - Structured pytest results (when using `test-ci`)

### Inspecting Coverage Results

1. **Terminal Summary:** Coverage percentage and missing lines displayed after test completion

2. **HTML Report:** Open `htmlcov/index.html` in your browser for:
   - File-by-file coverage breakdown
   - Line-by-line highlighting of covered/uncovered code
   - Interactive navigation through your codebase

3. **JSON Analysis:** Use `coverage.json` for programmatic analysis:
   ```bash
   python -c "import json; print(json.load(open('coverage.json'))['totals']['percent_covered'])"
   ```

4. **Missing Coverage:** The terminal report shows specific line numbers that lack coverage

## GitHub Actions Testing

Our GitHub Actions workflow automatically runs tests on pull requests and pushes to specific branches. The workflow is defined in `.github/workflows/python-run-pytest.yml`.

Key points:
- Tests are run on Ubuntu 24.04 with Python 3.12
- The workflow is triggered on pull requests that modify `src/mountainash_utils_rules/**` files
- Uses the `test_github` environment defined in `hatch.toml`
- Automatically uploads coverage to Codecov

To manually trigger the tests in GitHub Actions:
1. Go to the "Actions" tab in the GitHub repository
2. Select the "Pytest Runner" workflow
3. Click "Run workflow" and select the branch you want to test
4. Choose the fallback branch for dependencies:
   - `develop` (default)
   - `main`
5. Click "Run workflow" to execute

## Testing Dependencies

One of the key features of our testing setup is the ability to test changes across multiple Mountain Ash repositories simultaneously. This is particularly useful when making changes that affect multiple packages.

To test dependency changes:
1. Create branches with identical names across all relevant Mountain Ash repositories.
2. Push your changes to these branches.
3. When you create a pull request or push to the branch in this repository, the GitHub Actions workflow will automatically use the matching branches from the dependency repositories.
4. If a matching branch doesn't exist for a dependency, the workflow falls back to using the branch specified in the workflow dispatch (either main or develop).

This allows you to test integrated changes across multiple packages before merging, with the flexibility to choose which version of dependencies to fall back on.

## Online Coverage Tracking

We use [Codecov](https://codecov.io/) to track code coverage across commits and pull requests. Coverage reports are automatically uploaded after successful test runs in GitHub Actions.

To view online coverage reports:
1. Go to the [Codecov dashboard](https://codecov.io/github/mountainash-io/mountainash-utils-rules) for this repository
2. Navigate through files to see detailed coverage information
3. View coverage trends over time and across branches
4. Review coverage changes in pull requests

We strive to maintain high code coverage. Please ensure that your contributions include appropriate test coverage.

## Development Dependencies

Our testing setup supports testing across multiple Mountain Ash repositories simultaneously, useful when making changes that affect multiple packages.

To test dependency changes:
1. Create branches with identical names across all relevant Mountain Ash repositories
2. Push your changes to these branches
3. When you create a pull request or push to the branch in this repository, the GitHub Actions workflow will automatically use the matching branches from dependency repositories
4. If a matching branch doesn't exist for a dependency, the workflow falls back to the specified branch (main or develop)

This allows you to test integrated changes across multiple packages before merging.
