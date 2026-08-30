# Instructions for AI coding assistants

This file is read by Claude Code and similar tools before working in this
repo. Follow it as strictly as the linters do — CI will reject anything
that violates it, so following it up front saves round trips.

## Non-negotiables

1. **Never hand-edit `uv.lock`.** Add/change deps with `uv add <pkg>` or
   `uv remove <pkg>`, then commit the lockfile diff. Never loosen a pin to
   "make something work" — fix the actual incompatibility instead.
2. **Every new function with a non-trivial signature gets a type
   annotation.** `ty check` runs in CI and checks unannotated function
   bodies too (it has no separate "strict" mode — this is the default
   behavior), so untyped code will fail the build, not just get flagged.
3. **No bare `except:`, no `eval`/`exec` on external input, no
   string-formatted SQL.** These are hard blocks in Bandit/Ruff `S` rules,
   not style suggestions — don't add `# noqa` to silence them without
   asking first.
4. **All external input is validated via a Pydantic model at the
   boundary**, not checked ad hoc with `if` statements deep in business
   logic.
5. **New dependencies must be justified in the PR description**: why it's
   needed, and a one-line note on its maintenance status (stars/last
   release aren't proof, but zero recent commits + solo maintainer is a
   flag worth calling out). Do not add a dependency for something the
   stdlib already does well.
6. **Every bug fix needs a regression test that fails on the old code and
   passes on the new code.** Don't just fix and move on.
7. **Log with `structlog`'s `log.info/warning/error(event_name, **kwargs)`
   pattern** (see `src/agentshowdown/logging.py`) — not `print()`, not
   `logging.info(f"...")` with interpolated strings. Structured fields,
   not formatted messages, are what make logs queryable later.
8. **Don't commit secrets, even fake-looking placeholders that resemble
   real key formats** (e.g. `sk-ant-...`) — gitleaks will flag them and
   it trains bad habits.

## Frontend-specific (`frontend/`)

- No `any` in TypeScript — ESLint's `@typescript-eslint/no-explicit-any`
  is a hard error, not a warning. If you don't know the type, define an
  interface or use `unknown` and narrow it.
- The API client (`frontend/src/api.ts`) is the *only* place `fetch`
  calls to the backend live. Don't call `fetch` directly from components
  — add a typed function there and import it, so error handling and
  types stay centralized.
- Every new component gets a test in `*.test.tsx` using Testing Library,
  querying by role/label like the existing `App.test.tsx` — not by CSS
  class or test-id unless there's no accessible role available.
- Don't add a state management library (Redux, Zustand, etc.) for
  something `useState`/`useReducer` already handles. Ask first if you
  think one is genuinely needed.

## Before finishing any task, run

```bash
uv run ruff check . --fix
uv run ruff format .
uv run ty check src/
uv run pytest
```

If working in `frontend/`, also run:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
```

If any of these fail, fix the failure — don't work around it (no
`--exit-zero`, no lowering the coverage threshold, no adding files to
tool exclusion lists) unless you flag the tradeoff to me explicitly and I
agree to it.

## When adding a new endpoint / entry point

- Add a Pydantic model for its input, even if it's "just one field."
- Add at least one happy-path test and one validation-failure test.
- If it does anything worth alerting on (rate limiting, large payloads,
  auth failures), log a distinct structured event for it — see
  `large_order_flagged` in `main.py` as the pattern to copy.

## What not to do

- Don't reach for a new framework/library to solve something `uv`, `ruff`,
  or the stdlib already solves.
- Don't disable a CI check to get a PR green. Fix the underlying issue or
  explain why the check is wrong for this case and let me decide.
- Don't write tests that just assert `mock.called` with no assertion on
  behavior — that's coverage theater, not a test.
