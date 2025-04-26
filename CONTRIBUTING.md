# Contributing to Mountain Ash Utils - Rules

This document outlines the process for contributing to the project and provides guidelines to ensure a smooth collaboration.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Branching Strategy](#branching-strategy)
3. [Making Changes](#making-changes)
4. [Submitting a Pull Request](#submitting-a-pull-request)
5. [Code Review Process](#code-review-process)
6. [Coding Standards](#coding-standards)
7. [Testing](#testing)
8. [Documentation](#documentation)

## Getting Started

1. Fork the repository on GitHub.
2. Clone your fork locally: `git clone https://github.com/your-username/mountainash-utils-rules.git`
3. Add the original repository as a remote: `git remote add upstream https://github.com/mountainash-io/mountainash-utils-rules.git`
4. Create a new branch for your contribution (see [Branching Strategy](#branching-strategy)).

## Branching Strategy

We follow a git-flow branching methodology. The following branch naming conventions are allowed:

- `main`: The main production branch
- `develop`: The main development branch
- `feature/*`: For new features
- `chore/*`: For maintenance tasks
- `release/*`: For release preparation
- `hotfix/*`: For critical bug fixes in production
- `bugfix/*`: For non-critical bug fixes
- `renovate/*`: For dependency updates (automated)

### Protected Branches

The following branches are strictly protected and require code owner review and repository owner approval for pull requests:

- `main`
- `develop`
- `release/*`

## Making Changes

1. Ensure you're working on the correct branch (e.g., `feature/new-feature` for a new feature).
2. Make your changes in small, logical commits.
3. Follow the [Coding Standards](#coding-standards) of the project.
4. Add or update tests as necessary (see [Testing](#testing)).
5. Update documentation if required (see [Documentation](#documentation)).

## Submitting a Pull Request

1. Push your changes to your fork on GitHub.
2. Open a pull request against the appropriate branch:
   - For features, chores, and bugfixes, target the `develop` branch.
   - For hotfixes, target the `main` branch.
3. Provide a clear title and description for your pull request.
4. Reference any related issues in the pull request description.
5. Ensure all checks (tests, linting, etc.) pass successfully.

## Code Review Process

1. All pull requests require at least one review from a code owner.
2. For protected branches (`main`, `develop`, `release/*`), additional approval from a repository owner is required.
3. Address any feedback or comments provided during the review process.
4. Once approved, a maintainer will merge your pull request.

## Coding Standards

- Follow PEP 8 style guide for Python code.
- Use type hints where appropriate.
- Write clear, self-documenting code with meaningful variable and function names.
- Keep functions and methods focused and concise.
- Use comments sparingly, only when necessary to explain complex logic.

## Testing

- Write unit tests for all new functionality.
- Ensure all existing tests pass before submitting a pull request.
- Aim for high test coverage, especially for critical parts of the codebase.
- Use pytest for writing and running tests.
- For detailed information on how to run tests and our testing procedures, please refer to our TESTING.md file.

## Documentation

- Update the README.md file if your changes affect the project's setup or usage.
- Document new features or changes in behavior in the appropriate documentation files.
- Keep docstrings up-to-date for all public functions, classes, and modules.

