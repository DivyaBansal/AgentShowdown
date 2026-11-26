<p align="center">
  <img src="docs/assets/logo.svg" alt="AgentShowdown logo: two crossed swords" width="120">
</p>

<h1 align="center">AgentShowdown</h1>

<p align="center">
<br>
<b>Run and compare different coding agents and models in parallel Docker sandboxes on your own machine.<br>
Choose your own harness.<br>
Don't rely on the hype.</b>
</p>

<p align="center">
  <img alt="Python 3.12 | 3.13" src="https://img.shields.io/badge/python-3.12%20%7C%203.13-3776AB?logo=python&logoColor=white">
  <a href="https://github.com/astral-sh/uv"><img alt="uv" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json"></a>
  <a href="https://github.com/astral-sh/ruff"><img alt="Ruff" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json"></a>
  <a href="https://github.com/astral-sh/ty"><img alt="types: ty" src="https://img.shields.io/badge/types-ty-261230"></a>
  <img alt="status: alpha" src="https://img.shields.io/badge/status-alpha-orange">
</p>

> [!NOTE]
> **Status: alpha.** Usable day to day, with rough edges. `claude` and
> `codex` are verified end to end; other agents are best-effort. Only tested on
> Linux (see [Platform and sizing](#platform-and-sizing)).

<table>
  <tr>
    <td><img src="demo/.screenshots/setup.png" alt="Setup tab: repository, configuration, secrets and features" width="640"></td>
    <td><img src="demo/.screenshots/start_run.png" alt="New showdown form: pick features and agents, sandbox CPU and memory, then start the run" width="640"></td>
    <td><img src="demo/.screenshots/compare.png" alt="Standings table and head-to-head charts of duration, tokens and diff size per agent" width="640"></td>
  </tr>
  <tr>
    <td align="center"><b>1. Setup</b></td>
    <td align="center"><b>2. Start a run</b></td>
    <td align="center"><b>3. Compare</b></td>
  </tr>
</table>

## Why?

You get one interface to run any model + agent combination in a sandbox and
compare how it performs, without touching your current workflow. Launch
`(feature × agent)` jobs headless, wait, and compare the metrics at the end.

**Benchmark coding agents on your own work.** Benchmarks like SWE-bench and
Terminal-Bench score agents on a fixed, public task set: someone else's repos,
someone else's tests, and tasks that may already be in the model's training
data. AgentShowdown runs agents on your repo, your backlog and your tests, so
the results tell you which setup works best for *your* code.

## How?

1. Choose a local folder or a Git repo, in the web UI's Setup tab or in the config file.
2. Write the features to work on, or pull them in from GitHub issues.
3. Pick the agent and model that should implement each one.
4. Every job runs in its own Docker sandbox, in parallel.
5. Run everything from the web UI or from the terminal.

Each finished job reports its diff size, whether tests and lint pass, wall-clock
and agent time, token usage (input counts prompt-cache reads, so agents compare
like for like), tool calls and model requests, and which skill kit it carried.

**How they worked** shows each contender's tool calls side by side, on a shared
time axis (live while a job runs): every call is sorted into a phase (explore,
edit, verify, setup, git, web) by what it did, so claude's `Grep`/`Edit` and
codex's shell commands compare directly. Alongside: calls before the first edit,
edit → verify cycles, failures, and which files each agent touched. Needs the
agent's telemetry, so `claude` and `codex` only.

### A finished showdown

One feature (`add-ui`, a web UI for the LangLearn demo) given to six
agent + model combinations:

<table>
  <tr>
    <td width="50%"><a href="demo/.screenshots/comparison.png"><img src="demo/.screenshots/comparison.png" alt="Contender cards for the add-ui feature: one card per agent and model with status, duration, tokens, tool calls, diff size, branch and a link to the pull request; a crashed job offers Respawn"></a></td>
    <td width="50%"><a href="demo/.screenshots/Final_Standings.png"><img src="demo/.screenshots/Final_Standings.png" alt="Standings table ranking each job by agent time, with end-to-end time, tokens, tool calls (and how many failed), model requests, lines added and removed, and skill kit"></a></td>
  </tr>
  <tr>
    <td><b>Contenders.</b> One card per job, grouped by Feature. Shows status, duration, tokens, tool calls, diff and a link to its pull request. Crashed jobs can be respawned and outputs can be Inspected.</td>
    <td><b>Standings.</b> Every job ranked within its feature, with agent and end-to-end time, tokens, tool calls (and failures), model requests, diff size and skill kit.</td>
  </tr>
  <tr>
    <td><a href="demo/.screenshots/Head-to-head-comparison.png"><img src="demo/.screenshots/Head-to-head-comparison.png" alt="Head-to-head bar charts per agent: agent time, end-to-end time, tokens used, output tokens, tool calls, model requests and diff size; hatched bars mark runs with a skill kit"></a></td>
    <td><a href="demo/.screenshots/Analysis-of-tool-calls.png"><img src="demo/.screenshots/Analysis-of-tool-calls.png" alt="How they worked: one strip per contender with each tool call placed in an explore, edit, verify, git or other lane, in step order; failed calls are drawn taller with a triangle"></a></td>
  </tr>
  <tr>
    <td><b>Head to head.</b> Bar charts of time, tokens, tool calls, model requests and diff size. Hatched bars are runs that carried a skill kit.</td>
    <td><b>How they worked.</b> Each contender's tool calls sorted into phases, step by step or on a time axis. Failed calls stand out and hovering over a shows the command or file.</td>
  </tr>
  <tr>
    <td><a href="demo/.screenshots/Tool-calls-stats.png"><img src="demo/.screenshots/Tool-calls-stats.png" alt="Approach stats table per contender: tool calls, failed calls, calls before first edit, time to first edit and first test, edit-to-verify cycles, files read and edited, and the share of calls in each phase"></a></td>
    <td><a href="demo/.screenshots/files-edited.png"><img src="demo/.screenshots/files-edited.png" alt="Files edited matrix: one row per file, one column per contender, marking which agent edited which file"></a></td>
  </tr>
  <tr>
    <td><b>Approach stats.</b> How long each agent looked around before its first edit, how quickly it ran tests, how many edit → verify cycles it did, and how its calls split across phases.</td>
    <td><b>Files edited.</b> Which files each contender touched, so you can see at a glance which agents took the same route and which built something different.</td>
  </tr>
</table>

## Why Docker Sandboxes?

Each sandbox is a microVM (a lightweight virtual machine) with its own Linux kernel, isolated from the host by a hardware-backed hypervisor boundary. An agent can only touch the files and network
access its sandbox gets, so agents can safely run with permission prompts
skipped (`dangerously_skip_permissions: true`, the web UI's default). Moreover, they are ephemeral and can run headless and in parallel.

## What you can compare

- **Agents and models:** `claude` vs `codex` vs …, or one agent on different models.
- **Skills:** run the same agent and model twice with different `run_label`s,
  and give one of them a `kit` that carries the skill (passed to
  `sbx create --kit`; sbx marks kits as experimental).
- **Prompts:** describe the same task as two features, worded differently
  (for example a one-line request vs. a detailed spec), run both, and see
  which description gets the better result.
- **Variance:** the same agent, model and prompt twice, to see how much of a
  difference is just chance.

```yaml
# features.yaml: does the skill help?
features:
  - id: add-separable-verbs
    description: Add separable-verb practice to the German terminal app.
    agents:
      - agent_id: claude
        run_label: plain
      - agent_id: claude
        run_label: with-skill
        kit: [/abs/path/to/german-grammar-skill]   # dir, zip or OCI ref
```

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- [`sbx`](https://docs.docker.com/ai/sandboxes/install/), the Docker Sandboxes
  CLI, signed in with a Docker account (`sbx login`). On Linux it needs KVM, and
  your user must be in the `kvm` group.
- `gh` (GitHub CLI), authenticated
- Access to a model for each agent you compare. Inside a sandbox, agents don't
  use your host logins. Give them either:
  - an API key, stored with sbx: `sbx secret set anthropic` for `claude`,
    `sbx secret set openai` for `codex` (see `sbx secret set --help` for others),
  - your subscription account: run the agent in a sandbox once (`sbx run claude`
    or `sbx run codex`), sign in with `/login`, then exit. sbx keeps the login as
    a global secret (`sbx secret ls` shows it as `oauth configured`) and new
    sandboxes reuse it, or
  - a local [Ollama](https://ollama.com) model, through the agent's `env` (see
    [Configuration](#configuration)).
- Node 22 for the web UI

## Platform and sizing

AgentShowdown runs on **Linux** (tested on Ubuntu 24.04 with `sbx` v0.48.0 and
Docker 29.8).

**A laptop with 16 GB of RAM is enough to run 3 sandboxes in parallel** (the
default `max_concurrency`), as long as you:

- cap each sandbox's memory, e.g. `--memory 3g` (or *Memory per sandbox* = `3g`
  in the web UI). Otherwise sbx lets each one grow to half your RAM, and three
  of them can overcommit a 16 GB machine.
- use hosted models (API keys). A local Ollama model on the same laptop
  competes for that RAM; leave room for it or lower the concurrency.

Each sandbox is a small VM. Measured resident memory of the sandbox's VM
process on the host:

| Sandbox state | Host memory |
|---|---|
| Idle, nothing running | ~1.5 GiB |
| While the agent's work uses *N* GiB | ~1.5 GiB + *N* |
| After that memory is freed | back to ~1.5 GiB |

How many sandboxes you can run in parallel depends on the agent and on the
repo's build and tests, so there is no fixed number. As a rule of thumb, plan
for **2–3 GiB of RAM per sandbox**, plus whatever your test suite needs. Coding
agents mostly wait on the model, so memory usually runs out before CPU does.
Concurrency is set by `max_concurrency` (default 3).

By default sbx gives each sandbox every host CPU and half the host's RAM (at
most 32 GiB). That default is a ceiling, not a reservation. To set a hard cap,
use `--cpus` / `--memory` on `run`, or the *CPUs/Memory per sandbox* fields in
the web UI. The memory limit can range from 512 MiB to 75% of host RAM.

## Quickstart

```bash
git clone https://github.com/DivyaBansal/AgentShowdown.git && cd AgentShowdown
uv sync --all-groups
sbx login                                          # opens a browser to sign in to Docker
sbx daemon start
sbx secret set github --command "gh auth token"    # lets sandboxes push branches
sbx secret set anthropic                           # and/or openai, for the agents you use
```

**Demo target.** The examples below use [`demo/langlearn/`](demo/langlearn/),
which runs agents against [LangLearn](https://github.com/DivyaBansal/LangLearn),
a small German-practice terminal app with its own tests. Clone it next to this
repo (the config expects `../langlearn`):

```bash
git clone https://github.com/DivyaBansal/LangLearn.git ../langlearn
```

A successful job pushes its branch to the target repo's `origin`. To let that
push succeed, fork LangLearn, clone your fork instead, and set `github.repo` in
`demo/langlearn/config.yaml` to your fork.

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

### Agent telemetry

`claude` and `codex` export their own OpenTelemetry events (model requests
with tokens and cost, tool calls with duration and outcome) to the server's
OTLP receiver at `/otel/<sandbox>/v1/logs`. Open a job to see its **agent
timeline**: every tool call with the command it ran or the file it touched,
how long it took and whether it failed, plus model requests with tokens and
cost. The same data is at `GET /api/jobs/<sandbox>/agent-events`. Token
counts come from these events when they arrive, and from the agent's run log
when they don't.

Tool commands and file paths are recorded (claude's
`OTEL_LOG_TOOL_DETAILS=1`) and stored in the local jobs database. Prompts and
model responses stay redacted.

Sandboxes reach the server at `run.agent_otel_endpoint`, by default
`http://host.docker.internal:8000`. Change it if the API runs on another
port, or set it to `null` to turn export off. A custom `command:` exports
nothing.

sbx's network policy denies that route by default, so each sandbox that
exports gets an allow rule for the endpoint, scoped to that sandbox alone
(`sbx policy allow network --sandbox <name> localhost:8000`). sbx deletes the
rule with the sandbox. If sbx refuses the rule, the agent still runs and
token counts come from the run log.

| Agent | Support |
|---|---|
| `claude`, `codex` | Verified, with resume, token usage and agent telemetry |
| `cursor`, `opencode`, `copilot` | Best-effort |
| Anything else | Bring your own `command:` |

## Architecture

<a href="docs/architecture/index.html">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/architecture/overview-dark.png">
    <img alt="Run lifecycle: the React SPA calls the FastAPI app, which hands runs to the dispatcher; job threads drive sbx sandboxes, record status in jobs.sqlite3, run tests and lint against the target repo, and publish job events through the EventBus to the SPA's SSE stream." src="docs/architecture/overview-light.png">
  </picture>
</a>

## Acknowledgements

- [Docker Sandboxes (`sbx`)](https://docs.docker.com/ai/sandboxes/) runs every
  agent in its own microVM.
- [archify](https://github.com/tt-a1i/archify) generated the architecture diagram.

## Contributing

Run `ruff`, `ty`, `pytest` and the frontend `lint` / `typecheck` / `test`
scripts before pushing. The rules CI enforces are in [CLAUDE.md](CLAUDE.md).
