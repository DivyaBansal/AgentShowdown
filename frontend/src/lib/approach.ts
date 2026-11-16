/** Shared pieces of the approach view: phase names, labels and formatting
 *  used by both the strip chart and the comparison around it. */

import type { ApproachStep, Job, Phase } from "../api";
import { seriesKey } from "../charts/palette";
import { formatDuration } from "./jobs";

/** Every phase, in display order (matches the backend's `PHASES`). */
export const ALL_PHASES: Phase[] = ["explore", "edit", "verify", "setup", "git", "web", "delegate", "other"];

export type XMode = "time" | "step";

export const PHASE_LABELS: Record<Phase, string> = {
  explore: "Explore",
  edit: "Edit",
  verify: "Verify",
  setup: "Setup",
  git: "Git",
  web: "Web",
  delegate: "Subagent",
  other: "Other",
};

/** "1:05" for time, "40" for steps: the axis' tick labels. */
export function tickLabel(value: number, mode: XMode): string {
  if (mode === "step") return `${Math.round(value)}`;
  const seconds = Math.round(value);

  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** What a mark's tooltip says: the tool, what it acted on, when, how long. */
export function describeStep(step: ApproachStep): string {
  const parts = [`${PHASE_LABELS[step.phase]} · ${step.tool}`];

  if (step.target) parts.push(step.target);
  const when = step.offset_s === null ? null : `at ${tickLabel(step.offset_s, "time")}`;
  const took = step.duration_ms === null ? null : `took ${formatDuration(step.duration_ms / 1000)}`;
  const meta = [`call #${step.index + 1}`, when, took, step.ok === false ? "failed" : null];
  parts.push(meta.filter((p) => p !== null).join(", "));

  return parts.join("\n");
}


/** The parts of a job its label is built from. */
type LabelledJob = Pick<Job, "sandbox_name" | "created_at" | "agent_id" | "run_label" | "model">;

/** A name per job; two runs of one series get "#1", "#2" in launch order. */
export function jobLabels(jobs: LabelledJob[]): Map<string, string> {
  const labels = new Map<string, string>();
  const bySeries = new Map<string, LabelledJob[]>();

  for (const job of jobs) bySeries.set(seriesKey(job), [...(bySeries.get(seriesKey(job)) ?? []), job]);

  for (const [key, list] of bySeries) {
    [...list]
      .sort((a, b) => a.created_at.localeCompare(b.created_at))
      .forEach((job, i) => labels.set(job.sandbox_name, list.length > 1 ? `${key} #${i + 1}` : key));
  }

  return labels;
}
