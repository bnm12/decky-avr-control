# Pin pnpm 10 in CI and drop the packageManager field

With the `packageManager` field in `package.json`, pnpm 12 wrote a `packageManagerDependencies` section into `pnpm-lock.yaml`. On the GitHub runner, `pnpm install --frozen-lockfile` then failed with `ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE` ("Cannot update packageManagerDependencies with frozen-lockfile because the lockfile is not up to date"). Regenerating the lockfile did not fix it.

The fix pins pnpm 10 in `build.yml` through `pnpm/action-setup` (`version: 10`), removes the `packageManager` field, and commits an ordinary pnpm 10 lockfile. A local pnpm 12 still works with that lockfile.

## Consequences

Changing the pnpm version in CI is an explicit edit to `build.yml`. The `packageManager` field cannot be used to pin it.
