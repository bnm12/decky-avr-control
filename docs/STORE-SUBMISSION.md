# Decky store submission checklist

Use this checklist before submission, and confirm requirements against the
linked upstream sources.

## Readiness checklist

| Requirement | Source |
|---|---|---|
| Public source repository; no private, deliberately obfuscated, or unverifiable binary-only implementation. | [Submission policy](https://github.com/SteamDeckHomebrew/wiki/blob/ec9d4cf8b07cdc7bc7706fd118d68a272f653cb4/plugin-dev/submitting-plugins.md#L46-L58) |
| `LICENSE` or `LICENSE.MD` must be present. Template-derived plugins must retain the template's BSD-3-Clause license, with the chosen license at its top. | [Submission policy](https://github.com/SteamDeckHomebrew/wiki/blob/ec9d4cf8b07cdc7bc7706fd118d68a272f653cb4/plugin-dev/submitting-plugins.md#L60-L63); [template license](https://github.com/SteamDeckHomebrew/decky-plugin-template/blob/90d0780e882a17f5714fc6de044c645f22608290/LICENSE#L1-L31) |
| Store metadata supplies `name`, `author`, `publish.description`, `publish.tags`, and a reachable image URL. The store requires an HTTP(S) image response with PNG, JPEG, WebP, or AVIF content type (not SVG). | [Database CI metadata extraction](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/.github/workflows/build-plugins.yml#L335-L395); [store submission model](https://github.com/SteamDeckHomebrew/decky-plugin-store/blob/1899e87ad2ba13eb1f390f51cfcb7ba5850f345b/plugin_store/api/models/submit.py#L7-L15); [image fetcher](https://github.com/SteamDeckHomebrew/decky-plugin-store/blob/1899e87ad2ba13eb1f390f51cfcb7ba5850f345b/plugin_store/cdn.py#L17-L22) |
| Include the template's `api_version` and a `flags` list; only request flags the plugin actually needs. | [Official template manifest](https://github.com/SteamDeckHomebrew/decky-plugin-template/blob/90d0780e882a17f5714fc6de044c645f22608290/plugin.json#L1-L11); [Decky manifest parser](https://github.com/SteamDeckHomebrew/decky-loader/blob/ffd103b9c1a4ce5bf305e7c0998eda6e05457dad/backend/decky_loader/plugin/plugin.py#L30-L46) |
| Set the submitted `package.json` version before database submission; the database reads this version. | [Database README](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/README.md#L7-L14); [ADR-0007](adr/0007-package-json-version-source-of-truth.md) |
| Submit an addition/update PR with the correct template, maintainership/license/uniqueness/backend declarations, and testing evidence. | [PR template chooser](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/.github/pull_request_template.md#L1-L9); [addition template](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/.github/PULL_REQUEST_TEMPLATE/plugin_addition.md#L25-L77) |
| Test a normal Python-backend/React plugin on the latest SteamOS Stable **or** Beta branch and provide the report. Preview is for specified custom-backend/prebuilt-binary cases. | [Review and testing guide](https://github.com/SteamDeckHomebrew/wiki/blob/ec9d4cf8b07cdc7bc7706fd118d68a272f653cb4/plugin-dev/review-and-testing.md#L28-L40); [addition template testing section](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/.github/PULL_REQUEST_TEMPLATE/plugin_addition.md#L62-L77); [CI checkbox validation](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/.github/workflows/pr-labeler.yml#L85-L116) |
| Capture real-device screenshots and avoid implying unverified driver support. Screenshots are not a separate formal database gate; the addition form requires testing evidence. | [Project feature 12](research/deepening-options.md#part-2--feature-options-that-fit); [addition template testing section](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/.github/PULL_REQUEST_TEMPLATE/plugin_addition.md#L62-L77) |
| Database review checks the submodule, package/dependency names, lockfile, remote code, and build contents. Its review guide asks for `lockfileVersion: 9.0`. | [Review and testing guide](https://github.com/SteamDeckHomebrew/wiki/blob/ec9d4cf8b07cdc7bc7706fd118d68a272f653cb4/plugin-dev/review-and-testing.md#L17-L24) |

### pnpm version

CI, local corepack pnpm and the store builder all use pnpm 12 with a frozen install of the committed lockfile ([ADR-0017](adr/0017-build-with-pnpm-12-like-the-store.md); [builder image](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/builder/Dockerfile#L1-L11)).

## Owner steps: release, pin, and open the database PR

1. Merge the release-ready changes to the repository's default branch.
2. In GitHub **Actions → Release → Run workflow**, enter the next version as
   `X.Y.Z` (no leading `v`). Or run
   `gh workflow run release.yml -f version=X.Y.Z`. The workflow updates
   `package.json`, commits and tags `vX.Y.Z`, then starts the Build workflow.
   Do not edit the version or create the tag by hand; this is the release
   process in [ADR-0007](adr/0007-package-json-version-source-of-truth.md).
3. Confirm the tagged build succeeds and publishes `avr-control.zip`. Record the
   release tag's commit SHA.
4. Fork
   [`SteamDeckHomebrew/decky-plugin-database`](https://github.com/SteamDeckHomebrew/decky-plugin-database)
   and create a branch. From the database repository root, add this plugin as a
   submodule and pin it to the release tag's commit:

   ```sh
   git submodule add https://github.com/bnm12/decky-avr-control.git plugins/avr-control
   git -C plugins/avr-control checkout vX.Y.Z
   git add .gitmodules plugins/avr-control
   git commit -m "Add AVR Control"
   git push -u origin <branch>
   ```

   The database PR must point to the exact released commit; the submodule
   gitlink, not a moving branch name, records that commit.
5. Open a PR from the fork to the database's default branch using the **New
   plugin addition** template. Include the release version/SHA, complete every
   declaration, attach the Stable/Beta test report and the real-device
   screenshots, and identify which receivers have been hardware tested. Select
   the applicable testing checkbox as requested by the current
   form; remove the irrelevant Stable/Beta or Preview option. Database CI
   rejects PRs with both or neither Stable/Beta checkbox selected.

## Primary sources

- [Deckbrew: submitting plugins](https://github.com/SteamDeckHomebrew/wiki/blob/ec9d4cf8b07cdc7bc7706fd118d68a272f653cb4/plugin-dev/submitting-plugins.md)
- [Deckbrew: review and testing](https://github.com/SteamDeckHomebrew/wiki/blob/ec9d4cf8b07cdc7bc7706fd118d68a272f653cb4/plugin-dev/review-and-testing.md)
- [Plugin database README and contribution flow](https://github.com/SteamDeckHomebrew/decky-plugin-database/blob/7d5cd9b15494169999656ee83e31bacb55728204/README.md)
- [Official Decky plugin template manifest](https://github.com/SteamDeckHomebrew/decky-plugin-template/blob/90d0780e882a17f5714fc6de044c645f22608290/plugin.json)
- [Official Decky plugin template package metadata](https://github.com/SteamDeckHomebrew/decky-plugin-template/blob/90d0780e882a17f5714fc6de044c645f22608290/package.json)
