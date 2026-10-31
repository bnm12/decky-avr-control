---
status: accepted
---

# Build with pnpm 12, matching the Decky store

ADR-0006 pinned pnpm 10 in CI after a pnpm 12 lockfile broke a frozen install. Since then the committed lockfile has been written by pnpm 12.16.1: it records `packageManagerDependencies.pnpm`, and local `corepack pnpm` resolves to that version. The Decky plugin database builds submissions with Decky CLI, which installs pnpm 12 and runs a frozen install (`docs/STORE-SUBMISSION.md`). We therefore pin pnpm 12.16.1 in CI, so CI, local builds and the store all use the same pnpm.

This supersedes ADR-0006. Its rule still holds: change the pnpm version and the lockfile together, in one commit.
