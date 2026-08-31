import { useCallback, useEffect, useState } from "react";
import {
  fetchAgents,
  fetchFeatures,
  fetchPreflight,
  fetchSamples,
  fetchSandboxes,
  type AgentInfo,
  type Feature,
  type Job,
  type Preflight,
  type Sample,
  type SandboxSummary,
} from "./api";
import { ComparisonSpread } from "./charts/ComparisonSpread";
import { TelemetryChart } from "./charts/TelemetryChart";
import { assignSeriesColorsFor, seriesKey } from "./charts/palette";
import { JobCard } from "./components/JobCard";
import { PreflightBanner } from "./components/PreflightBanner";
import { RunLauncher } from "./components/RunLauncher";
import { SandboxControls } from "./components/SandboxControls";
import { useJobStream } from "./hooks/useJobStream";

export function App() {
  const { jobs, connected, error, refresh } = useJobStream();
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [features, setFeatures] = useState<Feature[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [sandboxes, setSandboxes] = useState<SandboxSummary[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);

  const loadSandboxes = useCallback(() => {
    fetchSandboxes().then(setSandboxes).catch(() => setSandboxes([]));
  }, []);

  useEffect(() => {
    fetchPreflight().then(setPreflight).catch(() => setPreflight(null));
    fetchFeatures().then(setFeatures).catch(() => setFeatures([]));
    fetchAgents().then(setAgents).catch(() => setAgents([]));
    loadSandboxes();
  }, [loadSandboxes]);

  useEffect(() => {
    if (selected === null) {
      setSamples([]);
      return;
    }
    fetchSamples(selected.sandbox_name).then(setSamples).catch(() => setSamples([]));
  }, [selected]);

  // Built once from the full roster, so filtering the view can never
  // renumber the palette.
  const colorFor = assignSeriesColorsFor(jobs);

  return (
    <div className="notebook">
      <main className="notebook__inner">
        <header className="masthead">
          <h1>agentshowdown</h1>
          <span className="subtitle">
            coding agents, run in sandboxes and compared
          </span>
          <span className="margin-note">
            {connected ? "live" : "reconnecting…"}
          </span>
        </header>

        <PreflightBanner preflight={preflight} />

        <RunLauncher
          features={features}
          agents={agents}
          disabled={preflight !== null && !preflight.live_runs_possible}
          onLaunched={() => {
            refresh();
            loadSandboxes();
          }}
        />

        <section className="card">
          <h2>Comparison</h2>
          <ComparisonSpread jobs={jobs} />
        </section>

        <section>
          <h2>Jobs</h2>
          {error && <p role="alert">{error}</p>}
          {jobs.length === 0 ? (
            <p className="hint">Nothing has run yet.</p>
          ) : (
            <div className="job-grid">
              {jobs.map((job) => (
                <JobCard
                  key={job.sandbox_name}
                  job={job}
                  color={colorFor(job)}
                  onSelect={setSelected}
                />
              ))}
            </div>
          )}
        </section>

        {selected && (
          <section className="card">
            <h2>{selected.sandbox_name}</h2>
            <TelemetryChart samples={samples} enabled={samples.length > 0} />
            {selected.detail && (
              <pre className="specimen-log">{selected.detail}</pre>
            )}
            <button type="button" onClick={() => setSelected(null)}>
              Close
            </button>
          </section>
        )}

        {/* The table view is the accessibility backstop for the charts:
            every value is here in text, including the ones a chart omits. */}
        {jobs.length > 0 && (
          <section className="card">
            <h2>All values</h2>
            <div className="scroll-x">
              <table className="jobs">
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Tokens</th>
                    <th>Lines +/−</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.sandbox_name}>
                      <td>{seriesKey(job)}</td>
                      <td>{job.status}</td>
                      <td>
                        {job.duration_seconds === null
                          ? "—"
                          : `${job.duration_seconds.toFixed(1)}s`}
                      </td>
                      <td>
                        {job.input_tokens === null && job.output_tokens === null
                          ? "—"
                          : (job.input_tokens ?? 0) + (job.output_tokens ?? 0)}
                      </td>
                      <td>
                        {job.lines_added === null
                          ? "—"
                          : `+${job.lines_added} / −${job.lines_removed ?? 0}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <SandboxControls sandboxes={sandboxes} onChanged={loadSandboxes} />
      </main>
    </div>
  );
}
