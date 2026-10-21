<p align="center">
  <img src="docs/assets/logo.svg" alt="AgentShowdown logo: two crossed swords" width="120">
</p>

<h1 align="center">AgentShowdown</h1>

<p align="center">
<br>
<b>Run and compare different coding agents and models in parallel Docker sandboxes.<br>
Choose your own harness.<br>
Don't rely on the hype.</b>
</p>

## Why?

You get one interface to run any model + agent combination in a sandbox and
compare how it performs, without touching your current workflow. Launch
`(feature × agent)` jobs headless, wait, and compare the metrics at the end.

## How?

1. Choose a local folder or a Git repo, in the web UI's Setup tab or in the config file.
2. Write the features to work on, or pull them in from GitHub issues.
3. Pick the agent and model that should implement each one.
4. Every job runs in its own Docker sandbox, in parallel.
5. Run everything from the web UI or from the terminal.

Each finished job reports its diff size, whether tests and lint pass, wall-clock
time and token usage.

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- `sbx`, the Docker sandbox CLI, with its daemon running (`sbx daemon start`)
- `gh` (GitHub CLI), authenticated
- The agent CLIs you want to compare (`claude`, `codex`, …)
- Node 22 for the web UI

## Quickstart

```bash
uv sync --all-groups
sbx daemon start
sbx secret set github --command "gh auth token"
```

**Web UI**

```bash
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --timeout-graceful-shutdown 5   # API on :8000
cd frontend && npm install && npm run dev                                                # UI on :5173
```

**Terminal**

```bash
uv run python -m backend.orchestrator \
  --config demo/langlearn/config.yaml --features demo/langlearn/features.yaml \
  run --feature-id add-separable-verbs --max-concurrency 2

uv run python -m backend.orchestrator --config demo/langlearn/config.yaml status --watch
```

> [!WARNING]
> AgentShowdown has no authentication and runs commands on the host. Bind it to
> `127.0.0.1` and never expose the port to a network.

## Configuration

Two YAML files, stored in `<repo>/.agentshowdown/` when you use the UI. Working
examples are in [`demo/langlearn/`](demo/langlearn/).

```yaml
# features.yaml
features:
  - id: add-separable-verbs
    description: Add separable-verb practice to the German terminal app.
    acceptance_criteria:
      - tests/test_german_practice.sh still passes.
    agents:
      - agent_id: claude
        model: claude-haiku-4-5
      - agent_id: codex
        model: gpt-5.6-sol
```

To run `claude` against a local Ollama model, set the agent's `env` to
`ANTHROPIC_BASE_URL: http://host.docker.internal:11434` and `ANTHROPIC_AUTH_TOKEN: ollama`.

| Agent | Support |
|---|---|
| `claude`, `codex` | Verified, with resume and token usage |
| `cursor`, `opencode`, `copilot` | Best-effort |
| Anything else | Bring your own `command:` |

## Architecture

<a href="docs/architecture/index.html">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/architecture/overview-dark.png">
    <img alt="Run lifecycle: the React SPA calls the FastAPI app, which hands runs to the dispatcher; job threads drive sbx sandboxes, record status in jobs.sqlite3, run tests and lint against the target repo, and publish job events through the EventBus to the SPA's SSE stream." src="docs/architecture/overview-light.png">
  </picture>
</a>

## Contributing

Run `ruff`, `ty`, `pytest` and the frontend `lint` / `typecheck` / `test`
scripts before pushing. The rules CI enforces are in [CLAUDE.md](CLAUDE.md).
