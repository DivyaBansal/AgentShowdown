/** Contenders, grouped by state, feature, agent or run.
 *
 * State grouping (the default) surfaces what needs attention first --
 * needs input, then working, then stopped -- ahead of what's already
 * finished. This sits below the flat, fully-sortable Standings table,
 * which remains the accessibility backstop for every value a card here
 * shows; grouping is an added view, not a replacement for it.
 */

import { useState } from "react";
import type { Job } from "../api";
import { groupJobs, type GroupMode } from "../lib/jobs";
import { JobCard } from "./JobCard";
import { Panel } from "./ui/Panel";

const MODE_LABELS: Record<GroupMode, string> = {
  state: "State",
  feature: "Feature",
  agent: "Agent",
  run: "Run",
};

// SAFETY: MODE_LABELS is a Record<GroupMode, string> with no extra keys, so
// its own keys are exactly the GroupMode members -- Object.keys just widens
// that to string[] by TypeScript's general design, not because this could
// actually hold anything else.
const MODES = Object.keys(MODE_LABELS) as GroupMode[];

export function GroupedJobs({
  jobs,
  colorFor,
  onSelect,
}: {
  jobs: Job[];
  colorFor: (job: Job) => string;
  onSelect: (job: Job) => void;
}) {
  const [mode, setMode] = useState<GroupMode>("state");
  const groups = groupJobs(jobs, mode);

  return (
    <section className="stack stack--tight" aria-label="Contenders">
      <div className="view-head">
        <span className="eyebrow">Contenders</span>
        <div className="toolbar">
          <label htmlFor="group-mode">Group by</label>
          <select
            id="group-mode"
            value={mode}
            onChange={(e) =>
              // SAFETY: every <option> below is rendered from MODES, so the
              // select's value can only ever be one of those.
              setMode(e.target.value as GroupMode)
            }
          >
            {MODES.map((m) => (
              <option key={m} value={m}>
                {MODE_LABELS[m]}
              </option>
            ))}
          </select>
        </div>
      </div>
      {groups.map((group) => (
        <Panel
          key={group.key}
          title={group.label}
          status={{
            tone: "neutral",
            text: `${group.jobs.length} job${group.jobs.length === 1 ? "" : "s"}`,
          }}
          defaultOpen
        >
          <div className="job-grid">
            {group.jobs.map((job) => (
              <JobCard
                key={job.sandbox_name}
                job={job}
                color={colorFor(job)}
                onSelect={onSelect}
              />
            ))}
          </div>
        </Panel>
      ))}
    </section>
  );
}
