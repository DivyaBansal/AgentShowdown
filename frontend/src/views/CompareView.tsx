/** The comparison half of the app: launch runs and read the results.
 *
 * Reads top to bottom like a results page: start a showdown, the standings,
 * the head-to-head charts, then each contender's card. Sandbox housekeeping
 * sits collapsed at the bottom because it is occasional and destructive.
 */

import { useEffect, useState } from "react";
import {
  fetchSamples,
  type AgentInfo,
  type Feature,
  type Job,
  type Preflight,
  type Sample,
  type SandboxSummary,
} from "../api";
import { ComparisonSpread } from "../charts/ComparisonSpread";
import { TelemetryChart } from "../charts/TelemetryChart";
import { assignSeriesColorsFor } from "../charts/palette";
import { JobCard } from "../components/JobCard";
import { RunLauncher } from "../components/RunLauncher";
import { SandboxControls } from "../components/SandboxControls";
import { Standings } from "../components/Standings";
import { Notice } from "../components/ui/Notice";
import { Panel } from "../components/ui/Panel";
import { StatusChip } from "../components/ui/StatusChip";

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
  const [selected, setSelected] = useState<Job | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [repoFilter, setRepoFilter] = useState("");

  useEffect(() => {
    if (selected === null) {
      setSamples([]);
      return;
    }
    fetchSamples(selected.sandbox_name).then(setSamples).catch(() => setSamples([]));
  }, [selected]);

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
        <section className="stack stack--tight" aria-labelledby="contenders-title">
          <h2 id="contenders-title" className="eyebrow">
            Contenders
          </h2>
          <div className="job-grid">
            {visible.map((job) => (
              <JobCard
                key={job.sandbox_name}
                job={job}
                color={colorFor(job)}
                onSelect={setSelected}
              />
            ))}
          </div>
        </section>
      )}

      {selected && (
        <section className="surface">
          <div className="surface__head">
            <h2 className="mono">{selected.sandbox_name}</h2>
            <button type="button" className="btn--quiet" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
          <div className="surface__body">
            <TelemetryChart samples={samples} enabled={samples.length > 0} />
            {selected.detail && <pre className="log">{selected.detail}</pre>}
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
