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
