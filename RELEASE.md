# Release Procedure

This document outlines the process for creating a new release of the mountainash-utils-rules package.

## Prerequisites

- You have push access to the main repository.
- You have the necessary permissions to create releases on GitHub.
- You have [Hatch](https://hatch.pypa.io/) installed locally.

## Release Process

1. **Update Version**
   - Navigate to `src/mountainash_utils_rules/__version__.py`
   - Update the `__version__` variable with the new version number
   - Ensure the version number follows the specified semantic versioning format:
     - Year and month: `YYYYMM`
     - Release candidate: `YYYYMM.0.0`
     - Prod release: `YYYYMM.1.0`
     - Updates to candidate or prod release: `YYYYMM.1.x`
   - Commit this change to the `main` branch

2. **Push Changes**
   - Push your changes to the `main` branch on GitHub
   - This will trigger the release workflow

3. **Monitor Workflow**
   - Go to the "Actions" tab in the GitHub repository
   - You should see the "Release with SBOMs" workflow running
   - Monitor the workflow for any errors

4. **Verify Release**
   - Once the workflow completes successfully, go to the "Releases" section of the repository
   - You should see a new release created with the version number you specified
   - Verify that the following assets are attached to the release:
     - Wheel file (`mountainash_utils_rules-{version}-py3-none-any.whl`)
     - Full SBOM (`mountainash-utils-rules-{version}-sbom-full.xml`)
     - Direct dependencies SBOM (`mountainash-utils-rules-{version}-sbom-direct.xml`)

5. **Release Branch**
   - The workflow will create a new `release-{version}` branch
   - This branch can be used for any hotfixes if needed

## Hotfix Process

If you need to create a hotfix for an existing release:

1. Check out the release branch for the version you want to hotfix:
   ```
   git checkout release-X.Y.Z
   ```

2. Create a new branch for your hotfix:
   ```
   git checkout -b hotfix-X.Y.Z.1
   ```

3. Make your changes and update the version in `__version__.py` to `X.Y.Z.1`

4. Commit your changes and push the hotfix branch

5. Create a pull request to merge the hotfix branch into the release branch

6. Once the pull request is merged, the release workflow will be triggered automatically

## Notes

- The workflow checks for existing tags and releases. If a tag or release already exists for the version you're trying to release, the workflow will fail.
- The workflow generates two types of Software Bill of Materials (SBOM):
  - Full SBOM: Includes all dependencies
  - Direct SBOM: Includes only direct dependencies
- The workflow uses Hatch to manage the build environment and dependencies
- The release process includes checking out several related repositories. Ensure that the necessary access tokens are configured in the repository secrets.

## Troubleshooting

If the release workflow fails:

1. Check the workflow logs for any error messages
2. Ensure that the version number in `__version__.py` is unique and has not been used before
3. Verify that all necessary secrets and permissions are correctly set up in the repository settings

For any other issues, please contact the maintainers or create an issue in the repository.
