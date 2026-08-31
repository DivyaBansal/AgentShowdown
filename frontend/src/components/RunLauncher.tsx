/** Compose a comparison and launch it.
 *
 * The cost controls are here rather than buried in a settings page, because
 * each one has a real price in sandbox time and the person launching the
 * run is the one who should pay it deliberately.
 */

import { useState } from "react";
import { ApiError, startRun, type AgentInfo, type Feature } from "../api";

export function RunLauncher({
  features,
  agents,
  disabled,
  onLaunched,
}: {
  features: Feature[];
  agents: AgentInfo[];
  disabled: boolean;
  onLaunched: (runId: string) => void;
}) {
  const [featureId, setFeatureId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [telemetry, setTelemetry] = useState(false);
  const [verifyOn, setVerifyOn] = useState<"host" | "sandbox">("host");
  const [openPr, setOpenPr] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const launchable = agents.filter((a) => a.has_native_builder);

  function toggleAgent(agentId: string) {
    setSelected((current) =>
      current.includes(agentId)
        ? current.filter((id) => id !== agentId)
        : [...current, agentId],
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const result = await startRun({
        feature_id: featureId,
        agents: selected.map((agent_id) => ({ agent_id })),
        telemetry,
        verify_on: verifyOn,
        open_pr: openPr,
      });
      setStatus(`Run ${result.run_id} started`);
      onLaunched(result.run_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the run");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>New comparison</h2>
      <form className="controls" onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="feature">Feature</label>
          <select
            id="feature"
            value={featureId}
            onChange={(e) => setFeatureId(e.target.value)}
            required
          >
            <option value="">Choose a feature…</option>
            {features.map((feature) => (
              <option key={feature.id} value={feature.id}>
                {feature.id}
              </option>
            ))}
          </select>
        </div>

        <fieldset className="field">
          <legend>Agents</legend>
          {launchable.map((agent) => (
            <label key={agent.agent_id} className="checkbox-row">
              <input
                type="checkbox"
                checked={selected.includes(agent.agent_id)}
                onChange={() => toggleAgent(agent.agent_id)}
              />
              {agent.agent_id}
              {/* Say plainly which agents have actually been checked against
                  a real CLI, rather than offering all of them as equals. */}
              {!agent.verified && <span className="hint"> · flags unverified</span>}
              {!agent.reports_token_usage && (
                <span className="hint"> · no token data</span>
              )}
            </label>
          ))}
        </fieldset>

        <div className="field-row">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={telemetry}
              onChange={(e) => setTelemetry(e.target.checked)}
            />
            Sample resource usage
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={openPr}
              onChange={(e) => setOpenPr(e.target.checked)}
            />
            Open a pull request
          </label>
          <div className="field">
            <label htmlFor="verify-on">Run tests on</label>
            <select
              id="verify-on"
              value={verifyOn}
              onChange={(e) => setVerifyOn(e.target.value as "host" | "sandbox")}
            >
              <option value="host">host (cheaper)</option>
              <option value="sandbox">sandbox</option>
            </select>
          </div>
        </div>
        <p className="hint">
          Telemetry costs one <code>sbx exec</code> per sandbox per tick and keeps
          the sandbox alive. Opening a pull request writes to the real repository;
          the branch is pushed either way.
        </p>

        <div>
          <button type="submit" disabled={disabled || busy || selected.length === 0}>
            {busy ? "Starting…" : "Start run"}
          </button>
        </div>
      </form>

      {status && <p role="status">{status}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
