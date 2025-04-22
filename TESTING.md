# Testing Mountain Ash Data Contracts

This document outlines the testing procedures for the Mountain Ash Utils Rules project, including how to run tests locally and via GitHub Actions.

## Table of Contents

1. [Local Testing](#local-testing)
2. [GitHub Actions Testing](#github-actions-testing)
3. [Testing Dependencies](#testing-dependencies)
4. [Code Coverage](#code-coverage)

## Local Testing

We use [Hatch](https://hatch.pypa.io/) to manage our development environment and run tests. To run tests locally:

1. Ensure you have Hatch installed:
   ```
   pip install hatch
   ```

2. Run the tests using Hatch:
   ```
   hatch run test:test
   ```

3. To run tests with coverage:
   ```
   hatch run test:cov
   ```

4. To generate a coverage HTML report:
   ```
   hatch run test:cov-html
   ```

## GitHub Actions Testing

Our GitHub Actions workflow automatically runs tests on pull requests and pushes to specific branches. The workflow is defined in `.github/workflows/pytest_github_action.yml`.

Key points:
- Tests are run on Ubuntu with Python 3.12.
- The workflow is triggered on pull requests to protected branches and via manual dispatch.
- It uses the `test_github` environment defined in `hatch.toml`.

To manually trigger the tests in GitHub Actions:
1. Go to the "Actions" tab in the GitHub repository.
2. Select the "Pytest" workflow.
3. Click "Run workflow" and select the branch you want to test.
4. You will see an option to choose the fallback branch for dependencies:
  - main (default)
  - develop
5. Select the branch you want to test and the desired fallback branch, then click "Run workflow".

## Testing Dependencies

One of the key features of our testing setup is the ability to test changes across multiple Mountain Ash repositories simultaneously. This is particularly useful when making changes that affect multiple packages.

To test dependency changes:
1. Create branches with identical names across all relevant Mountain Ash repositories.
2. Push your changes to these branches.
3. When you create a pull request or push to the branch in this repository, the GitHub Actions workflow will automatically use the matching branches from the dependency repositories.
4. If a matching branch doesn't exist for a dependency, the workflow falls back to using the branch specified in the workflow dispatch (either main or develop).

This allows you to test integrated changes across multiple packages before merging, with the flexibility to choose which version of dependencies to fall back on.

## Code Coverage

We use [Codecov](https://codecov.io/) to track code coverage. The coverage report is automatically uploaded to Codecov after successful test runs in GitHub Actions.

To view the coverage report:
1. Go to the [Codecov dashboard](https://codecov.io/github/mountainash-io/mountainash-utils-rules) for this repository.
2. Navigate through the files to see detailed coverage information.

We strive to maintain high code coverage. Please ensure that your contributions include appropriate test coverage.