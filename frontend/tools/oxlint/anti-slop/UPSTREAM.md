# Vendored anti-slop Oxlint plugin

Source: [dmmulroy/anti-slop](https://github.com/dmmulroy/anti-slop), copied from the
`install-anti-slop` skill bundle (`skills/install-anti-slop/assets/anti-slop`) installed
at `.agents/skills/install-anti-slop`.

- Upstream commit: unknown — the skill bundle does not record one. The bundle is
  identified by its `skills-lock.json` hash
  `1b9b5d3c0d9e0ef8dd7631f4d637e66b62a73e34dceaf4ae07c0d3de376fd7d7`.
- Installed: 2026-10-18, unmodified copy.
- Plugin paths: `index.ts` (generic, registered as `anti-slop`). `effect/` is copied but
  not registered — the frontend has no direct `effect` dependency.
- Runtime: `oxlint` and `@oxlint/plugins` pinned together at `1.88.0`; upgrade them in
  lockstep.
- Configuration: `frontend/.oxlintrc.json`, run by `npm run lint`.

## Intentional deviations

- Installed under `frontend/tools/oxlint/anti-slop/` instead of the repo root, because
  `frontend/` is the only JS/TS package and owns the Oxlint dependencies.
- Oxlint replaces ESLint: `.oxlintrc.json` also ports the ESLint, typescript-eslint, and React Hooks recommended rules.
