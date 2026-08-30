# python-starter

A minimal, opinionated skeleton with the 2026 "state of the art" baked in:
`uv` for env/deps, `ruff` + `ty` for lint/types, `bandit` + `pip-audit` +
`gitleaks` for security, `pytest` + `hypothesis` for testing, and
`structlog` + OpenTelemetry for telemetry — all wired into a GitHub
Actions pipeline that gates on every check before a build is produced.

> **Note on `ty`:** it's Astral's type checker and, as of mid-2026, still
> in beta (Pyrefly is Astral's own recommended stable alternative if you
> want something more mature). Reach for `ty` when you want the same
> single-vendor toolchain as `uv`/`ruff`, or when you're gradually typing
> a large legacy codebase — its gradual-typing guarantee means adding
> annotations to working code won't suddenly cascade into new errors
> elsewhere, which mypy doesn't guarantee. Swap `[tool.ty.rules]` for
> `[tool.mypy]` (see git history of this file) if you'd rather stay on
> mypy's more battle-tested defaults.

## Layout

```
.
├── CLAUDE.md                  # rules for AI coding assistants (see below)
├── pyproject.toml             # single source of truth: deps + all tool config
├── .pre-commit-config.yaml    # same checks, run locally before you even push
├── Dockerfile                 # multi-stage: Node builds frontend, Python serves it
├── .github/workflows/ci.yml   # lint → security → test → build/SBOM/sign
├── src/agentshowdown/
│   ├── main.py                 # FastAPI app: validation + structured logging
│   │                             (mounts frontend/dist as static files if built)
│   └── logging.py              # trace-correlated structured logging setup
├── tests/
│   └── test_main.py            # example + property-based tests
└── frontend/
    ├── src/
    │   ├── App.tsx              # example form calling the backend
    │   ├── App.test.tsx         # Testing Library component test
    │   └── api.ts               # typed fetch wrapper — the only place fetch() lives
    ├── vite.config.ts           # dev server proxies /api → uvicorn on :8000
    └── package.json
```

## Frontend dev workflow

Backend and frontend run as two processes locally, one process in
production (uvicorn serving both):

```bash
# terminal 1
uv run opentelemetry-instrument uvicorn agentshowdown.main:app --reload

# terminal 2
cd frontend && npm install && npm run dev
```

Vite's dev server proxies any `/api/*` call to `localhost:8000` (see
`vite.config.ts`), so there's no CORS configuration to maintain. In
production, `npm run build` outputs `frontend/dist`, and FastAPI mounts
it as static files directly — one container, one deployable, matching
what the Dockerfile builds.

## Quickstart

```bash
uv sync --all-groups        # installs locked deps, including dev tools
uv run pre-commit install   # local checks run on every commit
uv run ty check src/        # type check
uv run pytest               # run tests with coverage gate
uv run opentelemetry-instrument uvicorn agentshowdown.main:app --reload
```

## Making your coding assistant follow these practices

This is really a "make the rules load-bearing, not aspirational" problem.
A few things matter more than the wording of the instructions themselves:

1. **Put the rules in a file the assistant reads automatically, not in
   chat.** Claude Code reads `CLAUDE.md` at the start of every session in
   this repo; Cursor reads `.cursor/rules`; Copilot reads
   `.github/copilot-instructions.md`. Chat instructions get forgotten
   after context rolls over — a checked-in file doesn't. If you use more
   than one assistant, keep them as thin wrappers pointing at one shared
   file so they can't drift apart.

2. **Make violations fail loudly and locally, before CI.** Pre-commit
   hooks + a strict `ty`/`ruff` config mean the assistant sees the
   failure in its own terminal output and self-corrects in the same turn,
   instead of you finding out three commits later. An assistant that
   never sees its own mistakes doesn't learn the codebase's constraints
   within a session.

3. **State the non-negotiables as concrete commands, not vibes.** "Write
   clean code" gives an assistant nothing to check itself against. "Run
   `uv run ty check src/` before finishing and don't add `--ignore`
   blanket suppressions" is verifiable. Every bullet in `CLAUDE.md` above
   is written so the assistant can self-test it.

4. **Explicitly forbid the most common shortcut: silencing the check
   instead of fixing the problem.** Assistants under pressure to turn a
   task green will reach for `# noqa`, `--exit-zero`, lowering a coverage
   threshold, or adding a file to an exclusion list. Call this out by
   name as disallowed, and require it to be flagged to you instead of
   done silently — otherwise you get a repo that passes CI while quietly
   accumulating suppressed warnings.

5. **Give it a good example to copy, not just rules.** The
   `large_order_flagged` pattern in `main.py` and the `_add_trace_context`
   processor in `logging.py` exist so that "log a structured event for
   things worth alerting on" has a concrete referent. Assistants pattern-
   match against real code in the repo more reliably than they follow
   abstract prose.

6. **Review the diff, not just whether tests pass.** None of the above
   replaces actually reading what got written, especially around auth,
   input validation, and anything touching secrets — the instructions
   raise the floor, they don't remove the need for review.
