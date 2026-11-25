// Runs, jobs and sandboxes, plus the live event stream that keeps them fresh.
// Field names are snake_case to mirror the Python models exactly. Translating
// between cases at the boundary would just add a place for names to drift.

import { request } from "./client";
import type { AgentSpec, VerifyOn } from "./setup";

/** A job row. Metric fields are null when the agent never reported them --
 *  which must render as "no data", never as zero. */
export interface Job {
  sandbox_name: string;
  feature_id: string;
  agent_id: string;
  branch: string | null;
  status: string;
  pr_url: string | null;
  detail: string | null;
  created_at: string;
  updated_at: string;
  run_id: string | null;
  model: string | null;
  run_label: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  tests_passed: number | null;
  lint_passed: number | null;
  files_changed: number | null;
  lines_added: number | null;
  lines_removed: number | null;
  /** Every prompt token the model processed, prompt-cache reads and writes
   *  included, so it means the same for every agent. */
  input_tokens: number | null;
  /** The part of `input_tokens` served from the prompt cache. */
  cached_input_tokens: number | null;
  output_tokens: number | null;
  num_turns: number | null;
  /** Counted from the agent's own OTel events (claude and codex only);
   *  null when it exported none, which is not the same as zero. */
  tool_calls: number | null;
  tool_failures: number | null;
  /** Completed model round trips, counted where token usage is reported. */
  model_requests: number | null;
  /** JSON-encoded list of the sbx kit refs the job ran with; null for rows
   *  recorded before kits were. Decode with `jobKits` in lib/jobs. */
  kits: string | null;
  /** Whether the job's run sampled resource usage (1/0); null for rows
   *  recorded before the setting was. */
  telemetry: number | null;
  /** Which repo the job ran against; null for rows recorded before one
   *  shared database held every workspace. */
  repo: string | null;
  /** The agent's most recent run-log output, refreshed on the same poll
   *  tick as status. Null until the first tick after launch. Kept fresh
   *  via the `job_log` SSE event rather than a `GET /jobs` refetch -- see
   *  useJobStream. */
  log_tail: string | null;
  /** Seconds spent in each phase of the latest attempt; null when that
   *  phase hasn't run (or the row predates per-step timing). */
  launch_s: number | null;
  agent_s: number | null;
  verify_s: number | null;
  push_s: number | null;
  /** Why the job failed; null unless its status is a failure. */
  failure_category: FailureCategory | null;
  /** JSON-encoded list of the answer choices an awaiting_input agent
   *  offered with its question; null when it offered none. Decode with
   *  `questionOptions` in lib/jobs, which tolerates a malformed value. */
  question_options: string | null;
}

export type FailureCategory =
  | "infra"
  | "agent_stuck"
  | "timeout"
  | "tests"
  | "lint"
  | "internal"
  | "cancelled";

export interface Run {
  run_id: string;
  feature_id: string;
  created_at: string;
  finished_at: string | null;
  mode: string;
  status: string;
}

export interface Sample {
  sandbox_name: string;
  ts: string;
  cpu_cores: number | null;
  mem_bytes: number | null;
  mem_limit_bytes: number | null;
  pids: number | null;
}

export interface SandboxSummary {
  name: string;
  agent?: string;
  status?: string;
}

export interface PingResult {
  sandbox_name: string;
  listed: boolean;
  reachable: boolean;
  latency_ms: number | null;
  agent_alive: boolean | null;
  status_file_present: boolean | null;
}

/** One feature and the agents to run it through. */
export interface RunEntry {
  feature_id: string;
  agents: AgentSpec[];
}

export interface StartRunRequest {
  /** Each entry becomes its own run; they execute concurrently. */
  entries: RunEntry[];
  telemetry?: boolean;
  verify_on?: VerifyOn;
  open_pr?: boolean;
  poll_interval_seconds?: number;
  cpus?: number;
  memory?: string;
}

export async function fetchRuns(): Promise<Run[]> {
  return (await request<{ runs: Run[] }>("/runs")).runs;
}

export async function fetchRun(runId: string): Promise<{ run: Run; jobs: Job[] }> {
  return request<{ run: Run; jobs: Job[] }>(`/runs/${encodeURIComponent(runId)}`);
}

/** Deletes one finished job's history, leaving the rest of its run. */
export async function deleteJob(sandboxName: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/jobs/${encodeURIComponent(sandboxName)}`, {
    method: "DELETE",
  });
}

export async function fetchJobs(repo?: string): Promise<Job[]> {
  const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";

  return (await request<{ jobs: Job[] }>(`/jobs${query}`)).jobs;
}

export async function fetchSamples(sandboxName: string): Promise<Sample[]> {
  const path = `/jobs/${encodeURIComponent(sandboxName)}/samples`;

  return (await request<{ samples: Sample[] }>(path)).samples;
}

/** Where a log read came from: the live sandbox, or -- once a finished
 *  job's sandbox is gone -- the tail saved on the job during the run. */
export type LogSource = "sandbox" | "saved";

export interface JobLog {
  tail: string;
  source: LogSource;
}

/** An on-demand deeper log tail, read straight from the sandbox -- more
 *  than the `log_tail` already on the Job from the live stream. */
export async function fetchJobLog(sandboxName: string, tail?: number): Promise<JobLog> {
  const query = tail !== undefined ? `?tail=${tail}` : "";
  const path = `/jobs/${encodeURIComponent(sandboxName)}/log${query}`;

  const body = await request<{ sandbox_name: string; tail: string; source: LogSource }>(path);

  return { tail: body.tail, source: body.source };
}

/** One best-effort parsed event from an agent's JSON output. `kind` is
 *  whatever the agent's own CLI called it (e.g. claude's "assistant" /
 *  "result"), passed through verbatim; null when the event had none. */
export interface TranscriptEntry {
  index: number;
  kind: string | null;
  text: string;
}

/** A structured view of a job's output, parsed server-side from the same
 *  tail `fetchJobLog` reads. Empty for an agent whose output isn't JSON
 *  (cursor, opencode, copilot, a custom `command:`) -- callers fall back
 *  to the raw log for those. */
export interface JobTranscript {
  entries: TranscriptEntry[];
  source: LogSource;
}

export async function fetchJobTranscript(
  sandboxName: string,
  tail?: number,
): Promise<JobTranscript> {
  const query = tail !== undefined ? `?tail=${tail}` : "";
  const path = `/jobs/${encodeURIComponent(sandboxName)}/transcript${query}`;

  const body = await request<{
    sandbox_name: string;
    entries: TranscriptEntry[];
    source: LogSource;
  }>(path);

  return { entries: body.entries, source: body.source };
}

/** One step of an agent's run, from the OTel events its CLI exported
 *  (claude and codex only). Built server-side from the raw events. */
export interface TimelineEntry {
  /** ISO-8601; the CLI's own time when it sent one. */
  at: string | null;
  kind: "tool" | "model" | "prompt" | "error" | "other";
  /** The tool, model or event, e.g. "Bash" or "claude-haiku-4-5". */
  title: string;
  /** What it acted on (a command, a file path) or produced (tokens). */
  detail: string | null;
  duration_ms: number | null;
  ok: boolean | null;
  /** The raw event name, e.g. "claude_code.tool_result". */
  name: string;
  /** Position in the timeline; stable, since events are only appended. */
  seq: number;
}

export interface ToolRollup {
  calls: number;
  failures: number;
  duration_ms: number;
}

export interface AgentEventsSummary {
  event_counts: Record<string, number>;
  /** Total model cost; null when no event reported one (codex). */
  cost_usd: number | null;
  tools: Record<string, ToolRollup>;
}

export interface AgentEvents {
  timeline: TimelineEntry[];
  summary: AgentEventsSummary;
}

/** A job's agent telemetry as a timeline, oldest first. Empty for agents
 *  that export none. */
export async function fetchAgentEvents(sandboxName: string): Promise<AgentEvents> {
  const path = `/jobs/${encodeURIComponent(sandboxName)}/agent-events`;
  const body = await request<AgentEvents & { events: unknown[] }>(path);

  return { timeline: body.timeline, summary: body.summary };
}

/** What a tool call did, judged from the tool and, for shell tools, the
 *  command it ran (see the backend's orchestrator/approach). */
export type Phase =
  | "explore"
  | "edit"
  | "verify"
  | "setup"
  | "git"
  | "web"
  | "delegate"
  | "other";

/** One tool call, placed on the job's clock. */
export interface ApproachStep {
  /** Position among the job's tool calls, 0-based. */
  index: number;
  /** Seconds since the job's first event; null when no time was sent. */
  offset_s: number | null;
  phase: Phase;
  /** The tool's own name, e.g. "Bash" or "exec_command". */
  tool: string;
  /** What it acted on (a command, a file), cut to one line. */
  target: string | null;
  ok: boolean | null;
  duration_ms: number | null;
}

export interface ApproachStats {
  calls: number;
  failures: number;
  phase_calls: Record<Phase, number>;
  phase_ms: Record<Phase, number>;
  /** Null when the agent never edited anything. */
  calls_before_first_edit: number | null;
  seconds_to_first_edit: number | null;
  seconds_to_first_verify: number | null;
  /** Times it edited, then ran tests, lint or a build. */
  edit_verify_cycles: number;
  files_read: string[];
  files_edited: string[];
  /** Seconds from the first event to the last tool call. */
  span_s: number | null;
}

export interface Approach {
  phases: Phase[];
  steps: ApproachStep[];
  stats: ApproachStats;
}

/** How a job went about its task: its tool calls sorted into phases, plus
 *  stats drawn from them. Only the current turn. Empty for agents that
 *  export no telemetry. */
export async function fetchApproach(sandboxName: string): Promise<Approach> {
  return request<Approach>(`/jobs/${encodeURIComponent(sandboxName)}/approach`);
}

export async function fetchSandboxes(): Promise<SandboxSummary[]> {
  return (await request<{ sandboxes: SandboxSummary[] }>("/sandboxes")).sandboxes;
}

export async function startRun(body: StartRunRequest): Promise<{ run_ids: string[] }> {
  return request<{ run_ids: string[] }>("/runs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Answers an agent that stopped to ask. Accepted at once (202); the agent
 *  resumes in the background and its progress arrives over the SSE stream. */
export async function answerJob(
  sandboxName: string,
  answer: string,
): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/jobs/${encodeURIComponent(sandboxName)}/answer`,
    { method: "POST", body: JSON.stringify({ answer }) },
  );
}

/** Answers by picking one of the options the agent offered. The server
 *  resolves the index to that option's text, so the agent sees the same
 *  prompt it would for a typed answer. */
export async function answerJobWithOption(
  sandboxName: string,
  optionIndex: number,
): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/jobs/${encodeURIComponent(sandboxName)}/answer`,
    { method: "POST", body: JSON.stringify({ option_index: optionIndex }) },
  );
}

/** Sends a new instruction to a job whose agent isn't waiting on anything
 *  but whose sandbox is still around. Also accepted at once (202). */
export async function followupJob(
  sandboxName: string,
  prompt: string,
): Promise<{ status: string }> {
  return request<{ status: string }>(
    `/jobs/${encodeURIComponent(sandboxName)}/followup`,
    { method: "POST", body: JSON.stringify({ prompt }) },
  );
}

/** Restarts a stopped or dead job. Runs in the background on the server;
 *  the real status arrives over the SSE stream, not in this response. */
export async function respawnJob(
  sandboxName: string,
): Promise<{ status: string; sandbox_name: string }> {
  return request<{ status: string; sandbox_name: string }>(
    `/jobs/${encodeURIComponent(sandboxName)}/respawn`,
    { method: "POST" },
  );
}

export async function pingSandbox(sandboxName: string): Promise<PingResult> {
  return request<PingResult>(
    `/sandboxes/${encodeURIComponent(sandboxName)}/ping`,
    { method: "POST" },
  );
}

/** Stops a sandbox's container without removing it -- its filesystem and
 *  git history survive, so `respawnJob` can restart it later. */
export async function stopSandbox(
  sandboxName: string,
): Promise<{ stopped: string }> {
  return request<{ stopped: string }>(
    `/sandboxes/${encodeURIComponent(sandboxName)}/stop`,
    { method: "POST" },
  );
}

/** Removes one sandbox. Refused (ApiError, status 409) when its branch has
 *  commits that exist only in that sandbox, unless `force` is set. */
export async function removeSandbox(
  sandboxName: string,
  force = false,
): Promise<{ removed: string[] }> {
  return request<{ removed: string[] }>(
    `/sandboxes/${encodeURIComponent(sandboxName)}/rm`,
    { method: "POST", body: JSON.stringify({ force }) },
  );
}

/** Removes every sandbox on the host. The confirmation phrase is checked
 *  server-side too; passing it here is not what makes this safe. */
export async function killAllSandboxes(): Promise<{
  removed: string[];
  jobs_marked_lost: number;
}> {
  return request<{ removed: string[]; jobs_marked_lost: number }>(
    "/sandboxes/kill-all",
    { method: "POST", body: JSON.stringify({ confirm: "KILL ALL" }) },
  );
}

/** Opens the live event stream.
 *
 *  EventSource belongs here with the fetch calls: it is a backend network
 *  call, and centralising them is the point of this module. The browser
 *  resends Last-Event-ID on reconnect by itself, so callers get replay for
 *  free -- but they must still reconcile over HTTP on connect, because a
 *  gap too large to replay arrives as a `resync` event instead. */
export function openEventStream(): EventSource {
  return new EventSource("/api/events");
}
