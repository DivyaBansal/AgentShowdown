/** Presentation helpers shared by every view of a job.
 *
 * One place decides how a status is coloured, how a duration reads, and how
 * a missing metric is told apart from a zero, so the standings table and the
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

/** Lower is better. Finished beats in-flight beats failed. */
function outcomeRank(status: string): number {
  if (status === "succeeded") return 0;

  if (FAILED.has(status)) return 2;

  return 1;
}

export function formatDuration(seconds: number | null): string | null {
  if (seconds === null) return null;

  if (seconds < 60) return `${seconds.toFixed(1)}s`;

  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
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

/** Input + output, or null when neither half was reported. */
export function totalTokens(job: Pick<Job, "input_tokens" | "output_tokens">): number | null {
  if (job.input_tokens === null && job.output_tokens === null) return null;

  return (job.input_tokens ?? 0) + (job.output_tokens ?? 0);
}

export function formatDiff(job: Pick<Job, "lines_added" | "lines_removed">): string | null {
  if (job.lines_added === null && job.lines_removed === null) return null;

  return `+${job.lines_added ?? 0} / −${job.lines_removed ?? 0}`;
}

/** Compares two jobs *on the same feature* for placement.
 *
 * Outcome first, then passing tests, then wall-clock time. A missing
 * duration sorts last rather than counting as instant.
 */
export function compareForPlacement(a: Job, b: Job): number {
  const outcome = outcomeRank(a.status) - outcomeRank(b.status);

  if (outcome !== 0) return outcome;
  const tests = (b.tests_passed ?? -1) - (a.tests_passed ?? -1);

  if (tests !== 0) return tests;

  return compareNullableAscending(a.duration_seconds, b.duration_seconds);
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

/** Groups jobs for display, ordered so the most relevant group leads.
 *
 * `"state"` orders by urgency (see `STATE_BUCKET_ORDER`) and omits empty
 * buckets. The other axes group by an id already on the job and sort
 * groups alphabetically, since there's no inherent priority between two
 * features, two agents or two runs.
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

  return [...byKey.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, list]) => ({
      key: key === "" ? "(none)" : key,
      label: mode === "run" && key === "" ? "No run" : key,
      jobs: list,
    }));
}

/** Placement of each job within its own feature, keyed by sandbox name.
 *
 * Ranking across features would compare different tasks, so each feature
 * is its own bracket and numbering restarts at 1.
 */
export function placements(jobs: Job[]): Map<string, number> {
  const byFeature = new Map<string, Job[]>();

  for (const job of jobs) {
    const list = byFeature.get(job.feature_id) ?? [];
    list.push(job);
    byFeature.set(job.feature_id, list);
  }

  const result = new Map<string, number>();

  for (const list of byFeature.values()) {
    [...list].sort(compareForPlacement).forEach((job, index) => {
      result.set(job.sandbox_name, index + 1);
    });
  }

  return result;
}
