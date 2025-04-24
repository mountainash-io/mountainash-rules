# Release Procedure

This document outlines the process for creating a new release of this package.

## Versioning System

We use Calendar Versioning (CalVer) with the format `YY.MM.MICRO`:
- `YY`: Last two digits of the year
- `MM`: Month (with leading zero if needed)
- `MICRO`: Incremental number for releases within the same month (starting at 0)

Examples:
- First release in April 2025: `25.04.0`
- Second release in April 2025: `25.04.1`
- First release in May 2025: `25.05.0`

## Release Process Overview

Our release process is **pull request driven**. Different types of releases are created based on the source and target branches:

| Source Branch | Target Branch | Release Type | Version Suffix | Example |
|---------------|---------------|--------------|----------------|---------|
| `release/*` or `hotfix/*` | `main` | Production | none | `25.04.0` |
| any branch | `develop` | Release Candidate | `rcN` | `25.04.0rc1` |
| `feature/*` or `bugfix/*` | any branch | Beta | `beta.feature-name.N` | `25.04.0beta.auth-fix.1` |

## Creating a Production Release

1. **Create a Release Branch**
   - Create a branch named `release/YY.MM.MICRO` from the `develop` branch
   - Example: `release/25.04.0`

2. **Update Version**
   - In the release branch, update the version in `src/mountainash_utils_rules/__version__.py`
   - Ensure it follows our CalVer format: `__version__ = 'YY.MM.MICRO'`
   - Commit and push this change

3. **Create Pull Request**
   - Create a pull request from your release branch (`release/YY.MM.MICRO`) to the `main` branch
   - The CI workflows will validate that only `release/*` or `hotfix/*` branches can target the `main` branch

4. **Review and Merge**
   - After review and approval, merge the pull request
   - This will trigger the build and release workflow automatically

5. **Monitor Release Process**
   - The workflow will:
     - Build the package
     - Generate SBOMs (Software Bill of Materials)
     - Create a GitHub release
     - Upload the wheel file and SBOMs as assets
     - Create and push a tag for the release

## Creating a Hotfix

If you need to create a hotfix for a production release:

1. **Create a Hotfix Branch**
   - Create a branch named `hotfix/YY.MM.MICRO` from the `main` branch
   - Example: `hotfix/25.04.1`

2. **Update Version**
   - Update the version in `src/mountainash_utils_rules/__version__.py`
   - Increment the micro version: `__version__ = 'YY.MM.MICRO+1'`
   - Commit and push your changes

3. **Create Pull Request**
   - Create a pull request from your hotfix branch to the `main` branch
   - After review and approval, merge the pull request

4. **Monitor Release Process**
   - The workflow will automatically create a production release

## Creating a Release Candidate

1. **Create Pull Request to Develop**
   - Create a pull request to the `develop` branch
   - After review and approval, merge the pull request

2. **Monitor Release Process**
   - The workflow will automatically:
     - Determine the next RC number for this version
     - Build the package with an RC suffix (e.g., `25.04.0rc1`)
     - Create a pre-release on GitHub

## Creating a Beta Release

1. **Create a Feature or Bugfix Branch**
   - Create a branch with the naming convention `feature/feature-name` or `bugfix/bug-name`

2. **Create Pull Request**
   - Create a pull request to any branch other than `main` or `develop`
   - After review and approval, merge the pull request

3. **Monitor Release Process**
   - The workflow will automatically:
     - Build the package with a beta suffix (e.g., `25.04.0beta.feature-name.1`)
     - Create a pre-release on GitHub

## Artifacts Produced by the Release Process

Each release generates the following artifacts:

- **Wheel file**: `mountainash_{package}-{version}-py3-none-any.whl`
- **Full SBOM**: `mountainash-{package}-{version}-sbom-full.xml`
- **Direct dependencies SBOM**: `mountainash-{package}-{version}-sbom-direct.xml`

## Notes and Troubleshooting

- The workflow checks for existing tags and releases. If a tag or release already exists for the version you're trying to release, the workflow will fail.
- If the release workflow fails, check the workflow logs for any error messages.
- Ensure that all necessary secrets and permissions are correctly set up in the repository settings.
- For any other issues, please contact the maintainers or create an issue in the repository.