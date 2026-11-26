/** Presentation helpers shared by every view of a job.
 *
 * One place decides how a status is coloured, how a duration reads, and how
 * a missing metric is told apart from a zero, so the runs table and the
 * contender cards can never disagree.
 */

import type { Job } from "../api";
import type { Tone } from "../components/ui/StatusChip";

const FAILED = new Set(["crashed", "tests_failed", "lint_failed", "timed_out", "error", "lost"]);

export function statusTone(status: string): Tone {
  if (status === "succeeded") return "ok";

  if (status === "awaiting_input") return "warn";

  if (status === "running" || status === "queued") return "active";

  if (FAILED.has(status)) return "danger";

  return "neutral";
}

/** Whether a job's sandbox can be stopped without losing its work --
 *  i.e. it's actually doing something right now. */
export function canStop(status: string): boolean {
  return status === "running" || status === "queued";
}

/** Whether a job can be restarted: deliberately stopped, or dead in a way
 *  that isn't "finished" (succeeded) or "needs an answer" (awaiting_input,
 *  handled by replying instead). */
export function canRespawn(status: string): boolean {
  return status === "stopped" || FAILED.has(status);
}

/** Whether a job can be sent a follow-up instruction: its agent is idle
 *  and not waiting on an answer. The server still checks the sandbox is
 *  there -- by default it's removed once a job finishes, and only the
 *  server can tell. */
export function canFollowUp(status: string): boolean {
  return !canStop(status) && status !== "stopped" && status !== "awaiting_input";
}

/** The answer choices an awaiting_input agent offered, decoded.
 *
 * The column holds a JSON-encoded list the server already validated, but
 * a malformed value still yields no options rather than a crash -- the
 * free-text answer box is always there as the fallback.
 */
export function questionOptions(job: Pick<Job, "question_options">): string[] {
  if (job.question_options === null) return [];

  let parsed: unknown;

  try {
    parsed = JSON.parse(job.question_options);
  } catch {
    return [];
  }

  if (!Array.isArray(parsed)) return [];

  return parsed.filter((item): item is string => typeof item === "string");
}

/** The sbx kit refs a job ran with, decoded; null when the row predates
 *  kits being recorded (or the value is malformed), which is "unknown",
 *  not "none". */
export function jobKits(job: Pick<Job, "kits">): string[] | null {
  if (job.kits === null) return null;

  let parsed: unknown;

  try {
    parsed = JSON.parse(job.kits);
  } catch {
    return null;
  }

  if (!Array.isArray(parsed)) return null;

  return parsed.filter((item): item is string => typeof item === "string");
}

/** A kit's short name: the last segment of its path or reference. */
export function kitName(ref: string): string {
  const parts = ref.split("/").filter(Boolean);

  return parts[parts.length - 1] ?? ref;
}

/** Whether a job ran with a kit (which is how a skill is shipped), or null
 *  when that isn't known. */
export function usesKit(job: Pick<Job, "kits">): boolean | null {
  const kits = jobKits(job);

  return kits === null ? null : kits.length > 0;
}

/** A one-line glance at what a job is doing right now, with no LLM call:
 *  the agent's own status message once it has one, falling back to the
 *  last non-empty line of its live output while it's still working.
 *  Null when there's genuinely nothing to show yet. */
export function latestActivity(job: Pick<Job, "detail" | "log_tail">): string | null {
  if (job.detail) return job.detail;

  if (!job.log_tail) return null;

  const lines = job.log_tail.split("\n").filter((line) => line.trim() !== "");

  return lines.length > 0 ? (lines[lines.length - 1] ?? null) : null;
}

export function formatDuration(seconds: number | null): string | null {
  if (seconds === null) return null;

  if (seconds < 60) return `${seconds.toFixed(1)}s`;

  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** An ISO-8601 timestamp as a short date and time in the viewer's locale,
 *  or null when there's none (a job still queued has no `started_at`) or
 *  it won't parse. */
export function formatTimestamp(iso: string | null): string | null {
  if (iso === null) return null;

  const ms = Date.parse(iso);

  if (Number.isNaN(ms)) return null;

  return new Date(ms).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Seconds to display as a job's duration.
 *
 * The backend only records `duration_seconds` once an attempt finishes, so
 * a job still in flight is timed live from `started_at` instead -- otherwise
 * the clock would sit still for the whole run.
 */
export function elapsedSeconds(
  job: Pick<Job, "status" | "started_at" | "duration_seconds">,
  nowMs: number,
): number | null {
  if (canStop(job.status) && job.started_at !== null) {
    const started = Date.parse(job.started_at);

    if (!Number.isNaN(started)) return Math.max(0, (nowMs - started) / 1000);
  }

  return job.duration_seconds;
}

/** Launch through push: every phase of the latest attempt, so sandbox
 *  start-up and verification count too. `duration_seconds` alone is only
 *  the agent's run. Null until the agent phase has a time. */
export function endToEndSeconds(
  job: Pick<Job, "launch_s" | "agent_s" | "verify_s" | "push_s">,
): number | null {
  if (job.agent_s === null || job.agent_s === undefined) return null;

  return (job.launch_s ?? 0) + job.agent_s + (job.verify_s ?? 0) + (job.push_s ?? 0);
}

/** Input + output, or null when neither half was reported. */
export function totalTokens(job: Pick<Job, "input_tokens" | "output_tokens">): number | null {
  if (job.input_tokens === null && job.output_tokens === null) return null;

  return (job.input_tokens ?? 0) + (job.output_tokens ?? 0);
}

export function formatDiff(job: Pick<Job, "lines_added" | "lines_removed">): string | null {
  if (job.lines_added === null && job.lines_removed === null) return null;

  return `+${job.lines_added ?? 0} / −${job.lines_removed ?? 0}`;
}

/** Ascending, with nulls after every real value (and equal to each other). */
export function compareNullableAscending(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0;

  if (a === null) return 1;

  if (b === null) return -1;

  return a - b;
}

/** Which of five buckets a job's current status belongs to -- the same
 *  grouping Agent View uses for its row sections, minus "pinned" (this
 *  app has no pinning). Ordered by urgency: things waiting on a human
 *  first, then things in flight, then things parked on purpose, then the
 *  two already-finished outcomes. */
export type StateBucket = "needs_input" | "working" | "stopped" | "ready_for_review" | "failed";

export const STATE_BUCKET_ORDER: StateBucket[] = [
  "needs_input",
  "working",
  "stopped",
  "ready_for_review",
  "failed",
];

export const STATE_BUCKET_LABELS: Record<StateBucket, string> = {
  needs_input: "Needs input",
  working: "Working",
  stopped: "Stopped",
  ready_for_review: "Ready for review",
  failed: "Failed",
};

export function stateBucket(status: string): StateBucket {
  if (status === "awaiting_input") return "needs_input";

  if (status === "running" || status === "queued") return "working";

  if (status === "stopped") return "stopped";

  if (status === "succeeded") return "ready_for_review";

  // Covers the FAILED set and any status this build doesn't know about yet
  // -- an unrecognized status is more useful surfaced as "needs a look"
  // than silently dropped into a bucket that implies nothing is wrong.
  return "failed";
}

/** One group of jobs for the Contenders view, keyed and labelled for
 *  whichever axis `groupJobs` was asked to group by. */
export interface JobGroup {
  key: string;
  label: string;
  jobs: Job[];
}

export type GroupMode = "state" | "feature" | "agent" | "run";

export const GROUP_MODE_LABELS: Record<GroupMode, string> = {
  state: "State",
  feature: "Feature",
  agent: "Agent",
  run: "Run",
};

export const GROUP_MODES: readonly GroupMode[] = ["state", "feature", "agent", "run"];

/** Groups jobs for display, ordered so the most relevant group leads.
 *
 * `"state"` orders by urgency (see `STATE_BUCKET_ORDER`) and omits empty
 * buckets. `"run"` puts the latest run first. Features and agents have no
 * inherent order between them, so they sort alphabetically.
 *
 * Args:
 *   jobs: The jobs to group (already filtered to whatever repo is shown).
 *   mode: Which axis to group by.
 *
 * Returns:
 *   Groups in display order; never an empty-jobs group.
 */
export function groupJobs(jobs: Job[], mode: GroupMode): JobGroup[] {
  if (mode === "state") {
    const byBucket = new Map<StateBucket, Job[]>();

    for (const job of jobs) {
      const bucket = stateBucket(job.status);
      const list = byBucket.get(bucket) ?? [];
      list.push(job);
      byBucket.set(bucket, list);
    }

    const groups: JobGroup[] = [];

    for (const bucket of STATE_BUCKET_ORDER) {
      const list = byBucket.get(bucket);

      if (list !== undefined) {
        groups.push({ key: bucket, label: STATE_BUCKET_LABELS[bucket], jobs: list });
      }
    }

    return groups;
  }

  const keyFor =
    mode === "feature"
      ? (job: Job) => job.feature_id
      : mode === "agent"
        ? (job: Job) => job.agent_id
        : (job: Job) => job.run_id ?? "";

  const byKey = new Map<string, Job[]>();

  for (const job of jobs) {
    const key = keyFor(job);
    const list = byKey.get(key) ?? [];
    list.push(job);
    byKey.set(key, list);
  }

  // Runs are a history, so the latest leads; the rest have no inherent
  // order and go alphabetically. A run that never started sorts by when it
  // was launched instead; the jobs recorded without a run go last.
  const runSortKey = (key: string, list: Job[]): string =>
    key === "" ? "" : (runStartedAt(list) ?? earliest(list.map((j) => j.created_at)) ?? "");

  const order =
    mode === "run"
      ? ([a, la]: [string, Job[]], [b, lb]: [string, Job[]]) =>
          runSortKey(b, lb).localeCompare(runSortKey(a, la))
      : ([a]: [string, Job[]], [b]: [string, Job[]]) => a.localeCompare(b);

  return [...byKey.entries()]
    .sort(order)
    .map(([key, list]) => ({
      key: key === "" ? "(none)" : key,
      label: mode === "run" ? runLabel(key, list) : key,
      jobs: list,
    }));
}

/** The smallest of some ISO-8601 UTC stamps, which sort correctly as
 *  strings; null when there are none. */
function earliest(stamps: (string | null)[]): string | null {
  let first: string | null = null;

  for (const stamp of stamps) {
    if (stamp && (first === null || stamp < first)) first = stamp;
  }

  return first;
}

/** When a run's first job started, or null if none has yet. */
export function runStartedAt(jobs: Job[]): string | null {
  return earliest(jobs.map((j) => j.started_at));
}

/** A run's heading: which feature it ran, since a bare `run-<hex>` says
 *  nothing about what it was. */
function runLabel(runId: string, jobs: Job[]): string {
  if (runId === "") return "No run";

  const features = [...new Set(jobs.map((j) => j.feature_id))].sort();

  return `${features.join(", ")} · ${runId}`;
}
