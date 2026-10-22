/** The comparison half of the app: launch runs and read the results.
 *
 * Reads top to bottom like a results page: start a showdown, the standings,
 * the head-to-head charts, then each contender's card. Sandbox housekeeping
 * sits collapsed at the bottom because it is occasional and destructive.
 */

import { useState } from "react";
import {
  type AgentInfo,
  type Feature,
  type Job,
  type Preflight,
  type SandboxSummary,
} from "../api";
import { ComparisonSpread } from "../charts/ComparisonSpread";
import { TelemetryChart } from "../charts/TelemetryChart";
import { assignSeriesColorsFor } from "../charts/palette";
import { AgentTimeline } from "../components/AgentTimeline";
import { GroupedJobs } from "../components/GroupedJobs";
import { LogView } from "../components/LogView";
import { ReplyForm } from "../components/ReplyForm";
import { RunLauncher } from "../components/RunLauncher";
import { SandboxControls } from "../components/SandboxControls";
import { Standings } from "../components/Standings";
import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import { StatusChip } from "../components/ui/StatusChip";
import { useSamples } from "../hooks/useSamples";

/** The last path segment, so a long absolute path stays readable in a cell. */
function repoLabel(repo: string | null): string {
  if (!repo) return "unknown repo";
  const parts = repo.split("/").filter(Boolean);

  return parts[parts.length - 1] ?? repo;
}

export function CompareView({
  jobs,
  features,
  agents,
  sandboxes,
  preflight,
  error,
  onLaunched,
  loadSandboxes,
}: {
  jobs: Job[];
  features: Feature[];
  agents: AgentInfo[];
  sandboxes: SandboxSummary[];
  preflight: Preflight | null;
  error: string | null;
  onLaunched: () => void;
  loadSandboxes: () => void;
}) {
  // Only the name is kept in state, not the Job snapshot itself: `jobs`
  // keeps arriving over SSE (including live log_tail updates), and looking
  // the job up fresh every render is what keeps the inspect panel's output
  // actually live instead of frozen at the moment it was opened.
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const samples = useSamples(selectedName);
  const [repoFilter, setRepoFilter] = useState("");
  const selectedJob = jobs.find((j) => j.sandbox_name === selectedName) ?? null;

  // One database now holds every workspace's history, so the board needs a
  // way to narrow to the repo in hand.
  const repos = [...new Set(jobs.map((j) => j.repo).filter((r): r is string => r !== null))];
  const visible = repoFilter === "" ? jobs : jobs.filter((j) => j.repo === repoFilter);

  // Built from the full roster, so filtering can never renumber the palette.
  const colorFor = assignSeriesColorsFor(jobs);

  return (
    <>
      <div className="view-head">
        <div>
          <h2>Compare</h2>
          <p>Same task, several agents, each in its own sandbox.</p>
        </div>
        {repos.length > 1 && (
          <div className="toolbar">
            <label htmlFor="repo-filter">Show repository</label>
            <select
              id="repo-filter"
              value={repoFilter}
              onChange={(e) => setRepoFilter(e.target.value)}
            >
              <option value="">All repositories</option>
              {repos.map((repo) => (
                <option key={repo} value={repo}>
                  {repoLabel(repo)}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <section className="surface">
        <div className="surface__head">
          <h2>New showdown</h2>
        </div>
        <div className="surface__body">
          <RunLauncher
            features={features}
            agents={agents}
            disabled={preflight !== null && !preflight.live_runs_possible}
            onLaunched={onLaunched}
          />
        </div>
      </section>

      {error && <Notice tone="danger">{error}</Notice>}

      <section className="surface">
        <div className="surface__head">
          <h2>Standings</h2>
          <StatusChip>{`${visible.length} job${visible.length === 1 ? "" : "s"}`}</StatusChip>
        </div>
        {visible.length === 0 ? (
          <div className="surface__body">
            <p className="hint">Nothing has run yet.</p>
          </div>
        ) : (
          <Standings jobs={visible} colorFor={colorFor} />
        )}
      </section>

      <section className="surface">
        <div className="surface__head">
          <h2>Head to head</h2>
        </div>
        <div className="surface__body">
          <ComparisonSpread jobs={jobs} />
        </div>
      </section>

      {visible.length > 0 && (
        <GroupedJobs
          jobs={visible}
          colorFor={colorFor}
          onSelect={(job) => setSelectedName(job.sandbox_name)}
        />
      )}

      {selectedJob && (
        <section className="surface">
          <div className="surface__head">
            <h2 className="mono">{selectedJob.sandbox_name}</h2>
            <button type="button" className="btn--quiet" onClick={() => setSelectedName(null)}>
              Close
            </button>
          </div>
          <div className="surface__body stack">
            {/* The peek panel: the one place to reply to a job, then see
                what it's doing. Keyed per job so a half-typed reply never
                carries over to the next one opened. */}
            <ReplyForm key={selectedJob.sandbox_name} job={selectedJob} />
            <TelemetryChart samples={samples} enabled={samples.length > 0} />
            {/* For a waiting job the detail *is* the question, already
                shown by the reply form. */}
            {selectedJob.detail && selectedJob.status !== "awaiting_input" && (
              <pre className="log">{selectedJob.detail}</pre>
            )}
            <AgentTimeline
              key={`timeline-${selectedJob.sandbox_name}`}
              sandboxName={selectedJob.sandbox_name}
              live={selectedJob.status === "running" || selectedJob.status === "queued"}
            />
            <LogView
              key={selectedJob.sandbox_name}
              sandboxName={selectedJob.sandbox_name}
              tail={selectedJob.log_tail}
            />
          </div>
        </section>
      )}

      <Panel
        title="Sandboxes"
        status={
          sandboxes.length > 0
            ? { tone: "active", text: `${sandboxes.length} running` }
            : { tone: "neutral", text: "none running" }
        }
      >
        <SandboxControls sandboxes={sandboxes} onChanged={loadSandboxes} />
      </Panel>
    </>
  );
}
