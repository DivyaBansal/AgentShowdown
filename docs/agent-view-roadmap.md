# Agent-view roadmap

Implementation plan for bringing Claude Code's `claude agents` UX to
agentshowdown, agent-agnostically. Scope, as requested:

- **A1-A4** — live log tail, structured transcript, rolling summary, live-ticking age
- **B6, B7, B9** — group by state, alternate groupings, animated/coloured status
- **C (12-16, all)** — reply to a blocked agent, async answer, structured questions, peek panel, follow-up to any job
- **E22-E25** — stop, respawn, delete-with-safeguards, reattach-on-restart
- **F28, F29** — PR status badge, "ready for review" grouping

Everything else from the original brainstorm is in
[`agent-view-nice-to-haves.md`](agent-view-nice-to-haves.md). Once a phase
ships, record it in `docs/agent-view-features-added.md` (created at that
point, not now).

This plan is grounded in the **current** `backend/`/`frontend/src/` tree
(audited 2026-10-02, HEAD `01ad8c0`) — not the `src/agentshowdown/` layout
from an earlier pass in this conversation, which no longer exists on disk.
File paths below are real.

## Reuse before building

- **`StatusChip` + `lib/jobs.ts:statusTone`** already colour every job
  status (`ok`/`warn`/`active`/`danger`/`neutral`). B9 is mostly done —
  it needs an animation on the `active` tone, not new colour logic.
- **`lib/jobs.ts:placements`/`compareForPlacement`** already group/rank
  per feature. B6/B7 should extend this module, not invent a parallel one.
- **`backend/api/runner.py`'s `_claim`/`_release`/`_dispatcher`/`_job_slots`**
  is the existing concurrency-safety machinery for "a sandbox name is
  owned by a live run." Every new action that resumes or restarts a job
  (C13, C16, E23, E25) must go through it, the same way `start_run` does —
  otherwise a fresh `run` and a resume/respawn can race on the same
  sandbox and double-finalize it.
- **`sbx stop` / `sbx run --name X`** (confirmed via `sbx stop --help` /
  `sbx run --help`) stop a container without removing it and restart it
  by name later. E22/E23 build on these directly rather than killing
  processes by hand.
- **`CompareView`'s repo filter** and **`Standings`' per-feature ranking**
  are the two groupings that already exist; B7 generalizes the pattern
  into one explicit control instead of two different ad hoc ones.

## Phase 0 — Live log capture (prerequisite for A1-A3, C15)

Nothing today streams a running agent's output; `_extract_before_teardown`
in `backend/orchestrator/job.py` only reads the log once, at teardown.

1. **`backend/orchestrator/sbx.py`**: add a combined probe that tails the
   log *and* reads the status file in one `sbx exec` (mirror the
   marker-splitting trick already used in `_extract_before_teardown`), so
   the expensive polling tier doesn't double its per-tick sandbox execs.
   Suggested signature: `sbx_read_status_and_tail(sandbox_name, status_file, log_path, tail_lines) -> tuple[dict | None, str]`.
2. **`backend/orchestrator/job.py:poll_until_signal`**: replace the
   `sbx_read_status` call in the expensive tier with the combined probe;
   `store.upsert(..., log_tail=tail)` and `bus.publish("job_log", sandbox_name=..., tail=...)` each tick.
3. **`backend/orchestrator/state.py`**: add `log_tail TEXT` to
   `_WRITABLE_COLUMNS`, `_MIGRATIONS`, and the `_UPSERT_SQL` literal
   (follow the existing pattern exactly — no string-built SQL).
4. **`backend/orchestrator/config.py`**: add `log_tail_lines: int = 200`
   to `Config` (how much tail to pull each tick).
5. **New endpoint** `GET /jobs/{sandbox_name}/log?tail=N` for an on-demand
   larger fetch straight from the sandbox. New `LogQuery` Pydantic model
   (`tail: int = Field(200, ge=1, le=2000)`), bound with `Depends()` like
   the existing `JobQuery`.
6. **Frontend**: `Job.log_tail: string | null` in `api.ts`; a
   `fetchJobLog(sandboxName, tail?)` call. **Don't** wire `job_log` into
   `useJobStream`'s existing "any event triggers a full `fetchJobs()`"
   pattern — a running job emits this every poll tick, and a full
   reconcile that often during a multi-job run would hammer the API.
   Instead, merge the tail into the matching job in local state in place.

Tests: `tests/orchestrator/test_sbx.py` (new probe, mocked `run`),
`tests/orchestrator/test_job.py` (log_tail gets upserted during polling),
`tests/orchestrator/test_state.py` (migration), `tests/test_api.py` (new
route, happy path + unknown-sandbox 404).

## Phase 1 — A1-A4: see what a running agent is doing

- **A1 (live tail)**: surfaced by Phase 0; render it in whatever component
  ends up being the peek panel (C15) rather than building a second log
  viewer — build the tail-rendering piece once, use it in both places.
- **A2 (structured transcript)**: new `backend/orchestrator/transcript.py`
  with a `TranscriptEntry` dataclass (`role`, `kind`, `text`, `ts`) and
  best-effort parsers for `claude --output-format stream-json` and
  `codex --json` (the two `VERIFIED_AGENTS`). Everything else falls back
  to the raw tail. New `GET /jobs/{sandbox_name}/transcript` endpoint.
  Frontend: a `Transcript.tsx` that renders role-tagged rows for
  claude/codex jobs, `<pre>` raw text otherwise.
- **A3 (rolling one-line summary)**: **needs a decision before building.**
  A real summary needs an LLM call (e.g. Haiku) from the backend, which
  means a new dependency, a stored `anthropic` secret (the `SecretService`
  Literal in `backend/api/models.py` already lists `"anthropic"` — reuse
  that, don't invent a second secret path), and per-tick cost/latency.
  Recommend shipping a **zero-dependency heuristic first**: the summary is
  just the latest non-empty status `message`, or the last line of the log
  tail when there isn't one. Gate a real LLM summarizer behind
  `Config.summarize_with_llm: bool = False` as a follow-up, not this pass —
  don't spend API budget on this without asking first.
- **A4 (live-ticking age)**: pure frontend. New
  `frontend/src/hooks/useElapsedTime.ts` (ticks from `started_at`/`created_at`,
  freezes at `duration_seconds` once a job reaches a terminal status).
  Used by `JobCard` and `Standings`.

## Phase 2 — B6, B7, B9: list ergonomics

- **B9 (animate the active state)**: smallest item. Add a pulse animation
  to the `.chip--active` tone in `frontend/src/styles/` so "running" reads
  as alive, not just differently coloured. No logic change —
  `statusTone` already maps every status correctly, including the ones
  Phase 4 adds (`resuming`, `stopped` — see below).
- **B6 (group by state)**: add `stateBucket(job)` to `lib/jobs.ts`
  (`"working" | "needs_input" | "ready_for_review" | "done" | "failed"`;
  `ready_for_review` = `succeeded` + `pr_url` or pushed branch, feeding
  F29 directly). Render as collapsible `Panel` sections (reuse the
  existing `Panel` component — it already does collapsible + status chip).
  Keep `Standings` as the flat, fully-sortable table underneath: per
  `docs/decisions.md`, it is the deliberate accessibility backstop for
  every chart, and grouping should be an added view, not a replacement.
- **B7 (alternate groupings)**: one explicit "Group by: state / feature /
  agent / run" control in `CompareView`, built on `stateBucket` (new) and
  `placements`/`feature_id` (existing) rather than parallel one-off logic
  per axis. Fold the existing ad hoc repo filter into the same control
  family conceptually, even if it stays its own dropdown.

Component tests via Testing Library, querying by role, per CLAUDE.md.

## Phase 3 — C12-16: talk to a blocked (or running) agent

This is the biggest functional gap: `answerJob()` exists in `api.ts` but
nothing calls it, and the backend route blocks synchronously.

- **C13 first (make `/jobs/{sandbox_name}/answer` async)**: move the
  `resume_job` call into `_dispatcher` (the same pool `start_run` uses),
  claiming the sandbox name via `_claim`/`_release` so a resume can't race
  a fresh run on it. Flip the job to a new transient status (`"resuming"`)
  synchronously before returning `202 Accepted`. Add `"resuming"` to
  `statusTone` (→ `active`, same as `running`/`queued`) and to
  `cli.py`'s status rendering if it special-cases statuses. New 409 path
  via the existing `RunConflictError`. Do this before C12 so the UI is
  built against the real (non-blocking) contract from the start.
- **C12 (in-UI reply form)**: `AnswerForm.tsx`, shown when
  `job.status === "awaiting_input"`, calling `answerJob()`. Test by role
  per CLAUDE.md frontend rules.
- **C14 (structured questions)**: extend the status-file contract in
  `backend/orchestrator/prompt.py`'s `build_prompt` docstring/instructions
  to allow an optional `"options": [...]` array alongside `state`/`message`.
  New `question_options` column (JSON-encoded TEXT, matching the existing
  `cpus`/`memory_limit`-style plain-column pattern — no new table).
  `AnswerRequest` gains optional `option_index: int | None`, resolved to
  the option's text server-side before it reaches `build_resume_prompt`
  (which stays untouched — it only ever sees plain text). `AnswerForm`
  renders buttons when `question_options` is non-empty, free text
  otherwise.
- **C15 (peek panel)**: `PeekPanel.tsx` — live tail (A1/Phase 0),
  `AnswerForm` when applicable (C12/C14), PR badge (F28). **Open UX
  question to settle before building**: does this replace the current
  "Inspect" telemetry panel on `JobCard`, or sit alongside it as a second
  entry point? Recommend folding Inspect's content in as a tab rather
  than running three different "see more about this job" affordances.
- **C16 (follow-up to any job)**: new `POST /jobs/{sandbox_name}/followup`
  (`FollowupRequest(prompt: str)`). Needs a new orchestrator function,
  `followup_job` — `resume_job` generalized to not require
  `status == "awaiting_input"` and to use a new `build_followup_prompt`
  (distinct from `build_resume_prompt`, since there was no question to
  answer). Claims the sandbox name the same way C13 does.

## Phase 4 — E22-25: lifecycle control

Confirmed primitives: `sbx stop SANDBOX` stops a container and preserves
its state ("can be restarted with `sbx run`"); `sbx run AGENT --name NAME`
restarts a stopped sandbox of that name. Neither exists in `sbx.py` yet.

- **E22 (stop)**: add `sbx_stop(sandbox_name)` to `sbx.py` (mirrors
  `sbx_rm`'s `RunOptions(check=False)` pattern). New
  `POST /jobs/{sandbox_name}/stop`. New terminal-ish status `"stopped"`,
  distinct from `CONTAINER_FAILURE_STATES`. `poll_until_signal` needs to
  recognize `sbx_status(...) == "stopped"` as a deliberate stop (not a
  crash) and return `"stopped"`; `_await_and_finalize` treats it like
  `"awaiting_input"` — leave the sandbox in place, skip finalize. Add
  `"stopped"` to `statusTone` (→ `neutral`). Log `job_stop_requested`
  (structured event, per CLAUDE.md's "destructive action" rule).
- **E23 (respawn)**: for a `stopped` sandbox, `sbx run <agent> --name
  <sandbox_name> --detached` then relaunch the agent with `resume=True`
  (the sandbox's filesystem/git state survives a stop). For a genuinely
  **gone** sandbox (`lost`/`crashed`/`timed_out`/`error`), this is already
  almost what `run_job` does when it finds a terminal-failed status and no
  live sandbox ("removed and relaunched fresh") — so `POST
  /jobs/{sandbox_name}/respawn` for that case can mostly delegate to the
  existing `run_job` path (claimed via `_claim`, dispatched via
  `_dispatcher`) rather than new orchestrator logic.
- **E24 (delete-with-safeguards)**: extend `backend/api/sandboxes.py:remove()`.
  Before `sbx_rm`, if the job has `branch` + `repo`, `fetch_branch` then
  check `git rev-list --count origin/<base>..<ref>` (reuse `vcs.py`'s
  `sandbox_ref`); refuse with 409 unless the request passes `force=true`.
  New `DeleteSandboxRequest(force: bool = False)` body — **this changes
  the route's contract** (currently a bare POST with no body), so update
  `removeSandbox()` in `api.ts` too. Log `sandbox_delete_forced` when
  force is used, mirroring `kill_all_requested`. Scope note: this only
  checks commits not yet on `origin` (checkable from already-fetched host
  refs); it does not check uncommitted changes inside the sandbox, which
  would need an extra `sbx exec` — start without that, add later if it
  matters in practice.
- **E25 (reattach on restart)**: rewrite `recover_in_flight_jobs` in
  `backend/api/runner.py`. Today, when a job's sandbox is still alive it
  just `continue`s — nothing is actually watching it anymore after the
  restart, so it sits "running" forever with no poll loop behind it. Fix:
  for each such job, reconstruct its `(Feature, AgentSpec)` (same lookup
  `resume_job` does via `find_spec_for_sandbox`), claim it, and resubmit
  into `_dispatcher` to re-enter `_await_and_finalize` directly — **do
  not** relaunch the agent, it's still running. New helper in `job.py`,
  e.g. `reattach_job(config, feature, spec, sandbox_name)`.

## Phase 5 — F28, F29: PR status

- **F28 (PR status badge)**: new `vcs.py:pr_status(repo_path, branch,
  github_repo) -> PrStatus | None` calling `gh pr view <branch> --json
  state,statusCheckRollup,reviewDecision --repo <github_repo>` (`gh` is
  already a hard requirement). New columns `pr_state`, `pr_checks`,
  `pr_review_decision` (TEXT). Refresh strategy: start **on-demand** (a
  `POST /jobs/{sandbox_name}/refresh-pr` the UI calls when a succeeded
  job's card renders, or on a manual button) rather than a background
  poller — matches the project's existing cost-conscious, opt-in stance
  (telemetry, log tailing). A periodic poller is a reasonable later
  upgrade, not this pass. Frontend `PrBadge.tsx` using `StatusChip`;
  `StatusChip`'s `Tone` has no distinct "merged" colour — reuse `neutral`
  for merged/draft/closed rather than extending the shared `Tone` type,
  unless you want a fifth tone across the whole app.
- **F29 (ready-for-review group)**: just the `stateBucket` case already
  defined in B6 (`succeeded` + has a `pr_url`/pushed branch). No new
  backend work.

## Suggested sequencing

Phases 2, 4 and 5 don't depend on each other or on Phase 0/1/3 and can
ship in any order (or in parallel across PRs). Suggested order, cheapest
and most independent first:

1. **Phase 0** (prerequisite for 1 and 3)
2. **Phase 4 (E)** — independent, high value, builds on primitives that
   already exist (`sbx stop`/`sbx run --name`)
3. **Phase 2 (B)** — frontend-only, fast, half-done already
4. **Phase 1 (A)** — needs Phase 0
5. **Phase 3 (C)** — needs Phase 0 (for the peek panel's tail) and reuses
   the claim/dispatch pattern
6. **Phase 5 (F)** — independent, slot in anywhere

## Per-PR checklist (from CLAUDE.md, not repeated per phase above)

- Pydantic model for every new endpoint's input, even a one-field body.
- One happy-path + one validation-failure test per new endpoint.
- A distinct structured log event for anything destructive (stop, forced
  delete, kill-all-style actions) — copy the `kill_all_requested` pattern.
- No `any` in new TypeScript; new components tested by role via Testing
  Library; all `fetch` calls added to `frontend/src/api.ts`, never inline.
- `uv run ruff check . --fix && uv run ruff format . && uv run ty check backend/ && uv run pytest`,
  plus `npm run lint && npm run typecheck && npm run test` in `frontend/`
  for any phase touching it. Frontend tests need Node 22 on this machine
  (`nvm use 22` — the system Node 18 fails under vite 7/vitest 5).
