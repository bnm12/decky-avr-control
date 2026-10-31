# package.json is the version source of truth, bumped by the Release workflow

The Decky store reads the committed `package.json`, so its version must match the release. The Release workflow (manual dispatch with a `version` input) checks the format and refuses a tag that already exists. It then sets the version with `npm pkg set`, commits as `github-actions[bot]`, tags `v<version>`, pushes, and starts `build.yml` on the tag. The explicit `gh workflow run` is needed because pushes made with the built-in token don't trigger other workflows.

A tag build still stamps the tag's version into the zip, and it warns if `package.json` differs from the tag. A tag pushed by hand therefore produces a correctly numbered zip, but the warning shows that the store copy is stale.

## Consequences

Use the Release workflow for normal releases. A hand-pushed tag should only follow a commit that already bumped `package.json`.
