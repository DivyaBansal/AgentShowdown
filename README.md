# agentshowdown

Give the same coding task to several AI coding agents at once — each in its
own sandbox — then compare what they produced: the diff, whether tests and
lint pass, how long it took, how many tokens it burned.

## What it does

You describe a **feature** (an id, a description, acceptance criteria) and
point agentshowdown at a target git repo. For every `(feature × agent)` pair
it:

1. Creates one Docker sandbox via the [`sbx`](#requirements) CLI, named
   `arena-<feature>-<agent>[-<label>]`, and clones the repo into it.
2. Launches the agent **headless** (non-interactive) with a prompt telling it
   to work on a dedicated branch `agent/<feature>/<agent>[-<label>]`, commit
   as it goes, and write a small JSON status file when it's done or blocked.
3. Polls the sandbox until the agent signals `done` (or `stuck` /
   `awaiting_input` / a crash / a timeout). Polling is two-tier: a cheap
   host-side `sbx ls` for liveness, a rarer `cat` of the status file inside
   the container.
4. On `done`, everything the sandbox is needed for happens in one shot, then
   the sandbox is released and the rest runs on the host: fetch the agent's
   branch, `git diff --numstat` against the base, run the repo's test and
   lint commands in a throwaway `git worktree`, and — only if all of that
   passes — push the branch to `origin`. A pull request is opened only if you
   asked for one.
5. Records the run to SQLite and tears the sandbox down.

Two comparison axes:

- **Different agents, one feature** — claude vs codex vs … on the same issue.
- **One agent, twice** — the same agent with `run_label: a` / `run_label: b`,
  to see how much of the difference between two runs is just variance.

Runs are **reattach-aware**: re-running skips jobs that already succeeded or
are waiting on you, reattaches to ones still in flight, and only relaunches
the failures. To force a completed job to run again, give it a new
`run_label`.

## Where it came from

agentshowdown started as a Python-service starter template, and that
toolchain is still the backbone: `uv` for envs/deps, `ruff` + `ty` for
lint/types, `bandit` + `pip-audit` + `gitleaks` for security, `pytest` +
`hypothesis` for tests, `structlog` for logging — all gated by a GitHub
Actions pipeline. The house rules that keep it that way are in
[CLAUDE.md](CLAUDE.md).

The orchestrator itself was ported from the `langlearn` project (a
German-practice terminal app), which is also the bundled demo target.

## Requirements

- **Python 3.12+** and [`uv`](https://docs.astral.sh/uv/)
- **`sbx`** — the Docker-backed sandbox CLI, with its daemon running
  (`sbx daemon start`). agentshowdown shells out to it for every
  sandbox operation.
- **`gh`** (GitHub CLI), authenticated — needed to push branches and open
  PRs.
- A **target git repo** on disk with a test command you can run.
- The **agent CLIs** you want to compare, installed in the sandbox image
  (`claude`, `codex`, …).

## Quickstart (CLI)

```bash
uv sync --all-groups
sbx daemon start
sbx secret set github --command "gh auth token"   # PAT injected into sandboxes

# Optional: to run claude against local ollama models, enable the model feature flag
sbx settings set feature.model true

# Run one feature across claude and codex, two sandboxes in parallel
uv run python -m backend.orchestrator \
  --config   demo/langlearn/config.yaml \
  --features demo/langlearn/features.yaml \
  run --feature-id add-separable-verbs --max-concurrency 2

# Watch progress in another terminal
uv run python -m backend.orchestrator \
  --config demo/langlearn/config.yaml status --watch

# If an agent stopped to ask a question (status: awaiting_input)
uv run python -m backend.orchestrator \
  --config demo/langlearn/config.yaml --features demo/langlearn/features.yaml \
  answer arena-add-separable-verbs-codex "Use the existing VerbDrill base class."

# Run the whole backlog, including the same-agent-twice variance check
uv run python -m backend.orchestrator \
  --config demo/langlearn/config.yaml --features demo/langlearn/features.yaml \
  run --all
```

The CLI's built-in defaults are `--config config/config.yaml` /
`--features config/features.yaml`, and **the repo ships no `config/`
directory** — pass the `demo/langlearn/` files (or your own) explicitly.

Subcommands: `run` (`--feature-id ID` repeatable, or `--all`;
`--max-concurrency N`), `status` (`--watch`), `answer <sandbox> <text>`.

## Web UI (the "lab notebook")

A React single-page app styled as a ruled lab notebook, in two tabs.

**Compare** — compose a comparison, watch jobs stream in live over SSE as
pinned index cards, read a head-to-head chart (wall-clock duration / tokens /
diff size), open any job to see its log and a CPU + memory trace, filter the
board by repository, and manage sandboxes (ping, wipe-all).

**Setup** — point agentshowdown at a repository and configure it without
editing YAML by hand:

- Pick a **folder on this machine**, or paste a **GitHub URL** to clone (in
  the background, with progress over the same SSE stream).
- Config and features are read from and written to
  **`<repo>/.agentshowdown/`**, so a repository carries its own settings and a
  clone that already has them prefills every form immediately.
- Edit config in grouped fields, and features — including the *full* agent
  spec: model, run label, `env`, `kit`, `provider`, and a custom `command`.
- **Import features from GitHub issues** when the repo has a GitHub remote.
  Each becomes a feature with a deterministic `issue-<n>` id, so re-importing
  updates rather than duplicates; a task list in the body becomes acceptance
  criteria.
- **Store secrets** through a dialog that hands them to `sbx secret set`.
  agentshowdown never writes a secret to disk: prefer a command (`gh auth
  token`) or a `op://` reference, which store a *pointer* sbx resolves on
  demand.

Job history lives in **one database shared by every workspace**
(`AGENTSHOWDOWN_JOBS_DB`, else `~/.agentshowdown/jobs.sqlite3`), so runs stay
comparable across repositories; each job records the repo it ran against.

Development runs as **two processes**:

```bash
uv run uvicorn backend.main:app --reload --host 127.0.0.1 \
  --timeout-graceful-shutdown 5                             # API on :8000
cd frontend && npm install && npm run dev                   # SPA on :5173
```

> **Keep `--timeout-graceful-shutdown`.** On reload, uvicorn stops accepting
> and then waits for in-flight responses to finish. An open `/events` SSE
> stream is in-flight for as long as the browser tab is, so without a bound
> the reload never completes: the old worker never exits, no new one starts,
> and every request — including the setup page's *Inspect* — hangs in the
> kernel accept queue until the frontend's 20s timeout fires. `stream.py`
> caps a stream at `MAX_STREAM_SECONDS` for the same reason; the flag makes
> the reload immediate rather than merely bounded.

> **Bind it to localhost.** agentshowdown has no authentication and, by
> design, accepts a filesystem path, a git URL and shell commands from its
> HTTP client and acts on them **on the host** — a `test_command` with
> `verify_on: host` runs via `bash -c` outside any sandbox. Treat the port as
> equivalent to a shell: do not expose it to a network or run it on a shared
> machine. If you must, set `AGENTSHOWDOWN_WORKSPACE_ROOT` to confine which
> directories can be selected, `AGENTSHOWDOWN_ALLOWED_CLONE_HOSTS` to restrict
> clone sources (default `github.com`), and put an authenticating proxy in
> front.

Vite's dev server proxies `/api/*` to `:8000` (stripping the prefix), so
there's no CORS to configure.

`docker build` produces a **single image** that serves the built SPA and the
API from `:8000` — see [Status & limitations](#status--limitations) for the
reverse-proxy caveat.

## Configuration

Two YAML files. Working examples live in [`demo/langlearn/`](demo/langlearn/).

**`config.yaml`** — the target repo, GitHub settings, the default agent, and
run policy:

```yaml
repo_path: ../langlearn

github:
  repo: DivyaBansal/LangLearn
  pat_secret_name: github
  base_branch: main
  open_pr: false                 # push the branch, but don't open a PR

agent:
  sbx_agent: claude              # used when a feature names no agents
  model: claude-haiku-4-5
  dangerously_skip_permissions: true

run:
  status_file: ".agent_status.json"
  liveness_interval_seconds: 3   # cheap host-side poll
  poll_interval_seconds: 5       # in-sandbox status read
  timeout_minutes: 60
  max_concurrency: 3
  verify_on: host                # "host" (throwaway worktree) | "sandbox"
  test_command: "bash tests/test_german_practice.sh"
  lint_command: ""
  telemetry: false               # opt-in CPU/memory sampling
  remove_sandbox_on_success: true
  remove_sandbox_on_failure: true

state_db_path: "./agentshowdown_state.sqlite3"

agents:                          # optional: per-agent-id defaults
  claude:
    model: claude-sonnet-5
```

**`features.yaml`** — the backlog:

```yaml
features:
  - id: add-separable-verbs
    description: |
      Add separable-verb practice to the German terminal app...
    acceptance_criteria:
      - A new practice mode covers at least 20 separable verbs.
      - tests/test_german_practice.sh still passes.
    agents:                      # omit -> one job on the config's default agent
      - agent_id: claude
        model: claude-haiku-4-5
      - agent_id: codex
        model: gpt-5.6-sol

  - id: add-inseparable-verbs
    description: |
      Add inseparable-verb practice...
    acceptance_criteria: [ ... ]
    agents:                      # same agent twice -> run_label required
      - agent_id: claude
        run_label: a
      - agent_id: claude
        run_label: b
```

These are also editable from the web UI's Setup tab, which writes them to
`<repo>/.agentshowdown/`. Files written by the UI carry a generated header and
do not preserve hand-written comments.

Per-agent overrides (`config.yaml`'s `agents:` map and each feature's
`agents:` entries): `model`, `provider`, `kit`, `env`,
`dangerously_skip_permissions`, plus `run_label` and `command` on a feature
entry. Resolution precedence for each field: **feature agent entry →
per-agent default → built-in default → `config.yaml` fallback**. `kit` and
`env` are *merged* across levels rather than replaced (`env` key-by-key,
spec wins).

`env` is a `NAME: value` mapping baked into the sandbox as `sbx create -e`
flags — e.g. point `claude` at a local Ollama by setting
`ANTHROPIC_BASE_URL: http://host.docker.internal:11434` and
`ANTHROPIC_AUTH_TOKEN: ollama`, with `model:` then naming any tag that
Ollama serves. Setting a base-URL var (`ANTHROPIC_BASE_URL`,
`OPENAI_BASE_URL`, `OPENAI_API_BASE`) also waives the `KNOWN_MODELS` check
for that job, since the endpoint serves its own model names.

The HTTP layer resolves these three ways, in order: an explicit
`AGENTSHOWDOWN_CONFIG` / `AGENTSHOWDOWN_FEATURES` override; the active
workspace's `<repo>/.agentshowdown/`; else the checked-in `demo/langlearn/`
preset. The bundled preset is **read-only** — saving from the UI always writes
into the selected repository, never over the checked-in demo.

| Variable | Default | What it does |
|---|---|---|
| `AGENTSHOWDOWN_CONFIG` / `_FEATURES` | unset | Pin an exact config/features file, overriding the workspace |
| `AGENTSHOWDOWN_HOME` | `~/.agentshowdown` | Workspace registry, clones, and the shared jobs database |
| `AGENTSHOWDOWN_JOBS_DB` | `<home>/jobs.sqlite3` | The one database every workspace records into |
| `AGENTSHOWDOWN_WORKSPACE_ROOT` | unset | Confine selectable repositories to one directory tree |
| `AGENTSHOWDOWN_ALLOWED_CLONE_HOSTS` | `github.com` | Comma-separated hosts a repo may be cloned from |
| `AGENTSHOWDOWN_DEMO_REPO` | `../langlearn` | Repo the preflight demo check looks for |

Upgrading from an earlier checkout? Job history previously lived in
`./agentshowdown_state.sqlite3`. It is not migrated automatically — set
`AGENTSHOWDOWN_JOBS_DB=./agentshowdown_state.sqlite3` to keep reading it.

### Agents

| agent | status | resume | token usage |
|---|---|---|---|
| `claude` | flags verified against a real CLI | ✓ (`--continue`) | ✓ |
| `codex` | flags verified against a real CLI | ✓ (`resume --last`) | ✓ |
| `cursor`, `opencode` | best-effort — flags not verified | ✓ | – |
| `copilot` | best-effort — **cannot resume** (use `command:`) | – | – |
| `droid`, `gemini`, `kiro`, `shell`, … | no built-in launcher — use `command:` | – | – |

`command:` on a feature's agent entry replaces the built-in launcher with a
verbatim shell command, run with `ORCH_PROMPT` / `ORCH_STATUS_FILE` /
`ORCH_RESUME` exported. Models are validated only for `claude` and `codex`
(`KNOWN_MODELS` in `orchestrator/config.py`).

## What gets measured

Per job, in the SQLite state DB (`*_state.sqlite3`, `jobs` table):

| field | how |
|---|---|
| `duration_seconds` | monotonic wall clock |
| `files_changed`, `lines_added`, `lines_removed` | `git diff --numstat` vs the base branch, host-side — works for every agent |
| `tests_passed`, `lint_passed` | exit code of the configured commands (absent if no command is set) |
| `input_tokens`, `output_tokens`, `num_turns` | parsed from the agent's run log — **`claude` and `codex` only**; `null` ("no data") for the rest |

With `telemetry: true`, a `samples` table also gets CPU-cores and memory
readings per sandbox over time, sampled from the container's cgroup (one
shared sampler thread; ~720 rows/sandbox retained).

No dollar cost is computed.

## Repo layout

```
backend/
  main.py                FastAPI app: arena routes + serves the built SPA
  logging.py             structlog JSON logging, trace/span-id enrichment
  api/                   HTTP layer — routes, run dispatch, resumable SSE,
                         preflight checks, direct sandbox controls
  orchestrator/
    cli.py               python -m backend.orchestrator (run/status/answer)
    job.py               job expansion + the run/resume state machine + finalize
    sbx.py               the sbx wrapper + per-agent CLI builders
    config.py            Config / Feature / AgentSpec / AgentProfile + model registry
    state.py             SQLite store (jobs, runs, samples) + migrations
    metrics.py           token-usage and diff-numstat parsing
    telemetry.py         opt-in cgroup resource sampling
    events.py            thread -> asyncio event bus (keeps orchestrator/ FastAPI-free)
    vcs.py, prompt.py, process.py
demo/langlearn/          working config + feature backlog (target repo: ../langlearn)
frontend/                React + Vite SPA (the lab notebook)
tests/                   pytest — orchestrator unit tests + HTTP/SSE integration tests
```

## Status & limitations

This is a working tool with rough edges. Known ones:

- **Production API routing.** The built SPA calls `/api/*`, but the backend
  registers routes with no prefix and only Vite's dev proxy rewrites them. In
  the single production container there is no such rewrite, so API and SSE
  calls 404 unless you put a reverse proxy in front that maps
  `/api/* → /*`. Two-process development is unaffected.
- **Agent coverage.** Only `claude` and `codex` have had their CLI flags
  checked against a real binary and report token usage. `cursor`, `opencode`
  and `copilot` are best-effort; `copilot` resume raises — override it with
  `command:`.
- **Scaffolded, not wired.** Per-sandbox `cpus` / `memory` limits (accepted
  by the run request) and a "browse recorded runs" seed dataset exist in the
  schema/API but are not implemented.
- **Telemetry** is opt-in and cgroup v2 only; hardened or older images report
  nothing.
- **`POST /jobs/{name}/answer`** runs synchronously in the request handler and
  can block for up to the job timeout. There is no answer UI in the frontend
  yet — use the CLI `answer` subcommand.
- **The Docker image** contains no `sbx` / `docker` / `gh` and does not bundle
  `demo/`, so live runs cannot originate from inside the container — it can
  serve the UI and read an existing state DB.

## Development

```bash
uv run ruff check . --fix
uv run ruff format .
uv run ty check backend/
uv run pytest

cd frontend
npm run lint
npm run typecheck
npm run test
```

CI (`.github/workflows/ci.yml`) gates every push and PR on lint + types,
security (`bandit` / `pip-audit` / `gitleaks`), `pytest` on Python 3.12 and
3.13 with an 80% coverage floor, and the full frontend check (eslint, `tsc`,
vitest, `npm audit`). On `main` it also builds the wheel and container image,
emits a CycloneDX SBOM, and Trivy-scans the image.

Conventions for both humans and AI assistants working in this repo are in
[CLAUDE.md](CLAUDE.md) — CI enforces most of them.
