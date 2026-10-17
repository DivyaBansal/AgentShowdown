# Agent-view: good to have, later

Everything from the original Agent-View feature brainstorm that is **not**
in the active plan ([`agent-view-roadmap.md`](agent-view-roadmap.md)).
Nothing here is scheduled — pull an item into the roadmap when it's
actually wanted.

## A — more live visibility

- **A5. Inline telemetry sparkline on the card itself**, not just in the
  Inspect panel. Small addition once A1-A4 land; `fetchSamples` already
  exists.

## B — more list ergonomics

- **B8. Filter/search box** (`a:claude`, `s:awaiting_input`, `#<pr>`,
  free text) — Agent View's filter syntax. Natural follow-on to B6/B7's
  grouping once there's enough concurrent job volume to need it.
- **B10. Pin a job** — exempt its sandbox from any future idle-reaper
  (there isn't one yet; see H below) and keep it visually on top.
- **B11. Rename a job** to a custom label; reorder rows manually.

## C — already covered

(Nothing deferred — all of C12-16 is in the active roadmap.)

## D — dispatching work (all deferred)

- **D17. Ad-hoc task dispatch** — free-text prompt + repo + agent(s) +
  model without a `features.yaml`/stored-feature entry first. The closest
  analogue to Agent View's main input box. Would need an ephemeral
  `Feature` that isn't persisted to the features file.
- **D18. `!command` jobs** — run a shell command in a sandbox and stream
  its output, independent of any agent CLI (`claude --bg --exec`
  equivalent). `AgentSpec.command` + agent_id `"shell"` already cover the
  execution side; this would just need a lighter-weight dispatch path that
  skips the feature/acceptance-criteria framing entirely.
- **D19. Per-dispatch defaults** surfaced in the launcher UI (model,
  permission mode, effort, max-turns, verify commands) beyond what
  `RunLauncher`/`AgentSpecFields` already expose.
- **D20. "Fork this job"** — clone a job's spec with a new `run_label`
  and an optionally edited prompt, to compare approaches without
  re-filling the whole launcher. Fits naturally with E23's respawn
  plumbing once that exists.
- **D21. Re-run/respawn UI entry point independent of E23** — effectively
  subsumed once E23 ships; listed separately here only because the
  original brainstorm had it as its own item.

## F — more PR/review workflow

- **F30. Draft-PR toggle** at dispatch time; **"open PR now"** button on a
  job that pushed a branch but never opened a PR (`config.open_pr: false`
  path in `_finalize_success`).
- **F31. In-UI diff viewer** — fetch and render `git diff` for a job's
  branch (file tree + syntax highlighting), beyond the numstat summary
  already computed.
- **F32. Copy "checkout this branch" command** — one-click convenience,
  no backend change.

## G — notifications & ambient status

- **G33. Browser notifications** on needs-input / finished / failed
  (behind a user toggle; needs the Notification API + permission prompt).
- **G34. Tab title reflects count needing input** (`2 awaiting input ·
  agentshowdown`), mirroring Agent View's terminal tab behavior.
- **G35. Event toast feed** in the UI (run started, job needs input, job
  pushed) — a thin layer over the existing SSE event stream.
- **G36. Slack/webhook post on terminal states** — needs a new outbound
  integration and its own secret; bigger scope than the rest of this list.

## H — idle lifecycle (not just multi-repo)

- **H37/38 reframed**: multi-repo dispatch and per-repo verify commands
  are **already supported** (`repo` column, `JobQuery.repo`, the
  `CompareView` repo filter) — this item originally assumed they weren't.
  What's still missing: an **idle reaper** (auto-remove `awaiting_input`/
  finished sandboxes after a TTL unless pinned, per B10) and a UI for
  picking which repo a *new* run targets when more than one workspace has
  history (today the active workspace picks it implicitly).

## I — CLI parity

- **I39. CLI commands matching the new endpoints**: `stop`, `respawn`,
  `followup`, `logs <job>` subcommands on `backend/orchestrator/cli.py`,
  plus `status --json` for parity with `claude agents --json`. Natural
  follow-on once the corresponding HTTP endpoints exist (E22/E23, C16,
  Phase 0's log endpoint).
- **I40. Stable short job IDs** so CLI and UI address the same job by a
  shorter handle than the full sandbox name.

## J — keyboard-first UX

- **J41. j/k row navigation, space to peek, enter to inspect, r to reply,
  / to focus search** — a real investment, worth doing only once the
  mouse-driven versions of peek/reply/grouping (Phase 2/3) are in place
  and it's clear the UI has enough rows per session to justify it.

## Explicitly out of scope (not "later," just not a fit)

- **True interactive attach.** Headless agent CLIs have no REPL to inject
  into — pause-then-resume (what C12-16 build) is the ceiling here, not a
  stepping stone to something more live.
- **Per-tool permission-approval prompts in the UI.** Every agent CLI's
  approval UX differs; the status-file `awaiting_input` contract (and
  C14's structured options) is the agent-agnostic approximation.
- **Cross-session agent messaging, voice dictation, `/loop`-style sleeping
  sessions.** Far from this project's single-shot-feature-comparison
  premise.
