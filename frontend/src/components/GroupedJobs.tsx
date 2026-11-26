/** Contenders, grouped by state, feature, agent or run.
 *
 * State grouping (the default) surfaces what needs attention first --
 * needs input, then working, then stopped -- ahead of what's already
 * finished. Grouping by run also gives each run its start time; each
 * finished card can be deleted on its own. The flat, fully-sortable Runs table (its own
 * tab) remains the accessibility backstop for every value a card here
 * shows; grouping is an added view, not a replacement for it.
 *
 * The mode is owned by the caller, which remembers it between visits,
 * shows `GroupModePicker` above the cards, and walks the same order
 * when stepping through contenders in the inspector.
 */

import type { Job } from "../api";
import {
  GROUP_MODES,
  GROUP_MODE_LABELS,
  formatTimestamp,
  groupJobs,
  runStartedAt,
  type GroupMode,
} from "../lib/jobs";
import { JobCard } from "./JobCard";
import { Panel } from "./ui/Panel";
import { Segmented } from "./ui/Segmented";

// Cards past this many enter together: a long stagger reads as lag.
const MAX_STAGGER = 12;
const STAGGER_MS = 30;

/** The grouping control: its own row above the cards, label and choices
 *  side by side. */
export function GroupModePicker({
  mode,
  onChange,
}: {
  mode: GroupMode;
  onChange: (mode: GroupMode) => void;
}) {
  return (
    <div className="group-picker">
      <span className="eyebrow" aria-hidden="true">
        Group by
      </span>
      <Segmented
        label="Group by"
        options={GROUP_MODES.map((m) => ({ value: m, label: GROUP_MODE_LABELS[m] }))}
        value={mode}
        onChange={onChange}
      />
    </div>
  );
}

export function GroupedJobs({
  jobs,
  colorFor,
  mode,
  selectedName = null,
  onSelect,
}: {
  jobs: Job[];
  colorFor: (job: Job) => string;
  mode: GroupMode;
  /** The job open in the inspector, marked on its card. */
  selectedName?: string | null;
  onSelect: (job: Job) => void;
}) {
  const groups = groupJobs(jobs, mode);

  return (
    <section className="stack stack--tight" aria-label="Contenders">
      {groups.map((group) => {
        // Jobs recorded before runs existed share a "No run" group, which
        // has no start time of its own.
        const runId = mode === "run" && group.jobs[0]?.run_id ? group.jobs[0].run_id : null;
        const started = runId === null ? null : formatTimestamp(runStartedAt(group.jobs));

        return (
          <Panel
            key={group.key}
            title={group.label}
            summary={started === null ? undefined : `Started ${started}`}
            status={{
              tone: "neutral",
              text: `${group.jobs.length} job${group.jobs.length === 1 ? "" : "s"}`,
            }}
            defaultOpen
          >
            <div className="job-grid">
              {group.jobs.map((job, index) => (
                <div
                  key={job.sandbox_name}
                  className="job-grid__item"
                  style={{ animationDelay: `${Math.min(index, MAX_STAGGER) * STAGGER_MS}ms` }}
                >
                  <JobCard
                    job={job}
                    color={colorFor(job)}
                    selected={job.sandbox_name === selectedName}
                    onSelect={onSelect}
                  />
                </div>
              ))}
            </div>
          </Panel>
        );
      })}
    </section>
  );
}
