---
status: accepted
---

# Build with pnpm 12, matching the Decky store

The Decky plugin database builds submissions with Decky CLI, which installs pnpm 12 and runs a frozen install (`docs/STORE-SUBMISSION.md`). Pin pnpm 12.16.1 in CI so CI, local builds and the store use the same package-manager version.

Change the pnpm version and lockfile together.
