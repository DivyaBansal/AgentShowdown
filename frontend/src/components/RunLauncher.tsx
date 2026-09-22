/** Compose a batch of comparisons and launch it.
 *
 * Rows stack the way features.yaml does: a feature, then the agents to run
 * it through, each with the settings that agent actually supports. Every
 * row becomes its own run, and the runs go at the same time.
 *
 * The cost controls are here rather than buried in a settings page, because
 * each one has a real price in sandbox time and the person launching the
 * run is the one who should pay it deliberately.
 *
 * Renders only the form; the surrounding view supplies the heading.
 */

import { useReducer, useState } from "react";
import { ApiError, startRun, type AgentInfo, type AgentSpec, type Feature } from "../api";
import { AgentSpecFields } from "./AgentSpecFields";
import { emptyAgent, emptyFeature, featuresReducer } from "./featuresReducer";
import { Field } from "./ui/Field";
import { Notice } from "./ui/Notice";

/** Drops settings the chosen agent can't use, so a model typed before
 *  switching to a command-only agent is never sent. Blank env keys exist
 *  only while a variable row is being typed into. */
function forRequest(agent: AgentSpec, agents: AgentInfo[]): AgentSpec {
  const known = agents.find((a) => a.agent_id === agent.agent_id);
  return {
    ...agent,
    model: (known?.accepts_model ?? true) ? agent.model : null,
    dangerously_skip_permissions: (known?.supports_skip_permissions ?? true)
      ? (agent.dangerously_skip_permissions ?? null)
      : null,
    env: Object.fromEntries(Object.entries(agent.env ?? {}).filter(([key]) => key.trim() !== "")),
  };
}

export function RunLauncher({
  features,
  agents,
  disabled,
  onLaunched,
}: {
  features: Feature[];
  agents: AgentInfo[];
  disabled: boolean;
  onLaunched: () => void;
}) {
  const [rows, dispatch] = useReducer(featuresReducer, [emptyFeature()]);
  const [telemetry, setTelemetry] = useState(false);
  const [verifyOn, setVerifyOn] = useState<"host" | "sandbox">("host");
  const [openPr, setOpenPr] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const complete = rows.every((row) => row.id !== "" && row.agents.length > 0);

  /** Picking a feature starts the row off with that feature's own agents,
   *  so the common case needs no further editing. */
  function chooseFeature(index: number, featureId: string) {
    const chosen = features.find((f) => f.id === featureId);
    dispatch({
      type: "replaceFeature",
      index,
      feature: {
        id: featureId,
        description: "",
        acceptance_criteria: [],
        agents: chosen && chosen.agents.length > 0 ? chosen.agents : [emptyAgent()],
      },
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const result = await startRun({
        entries: rows.map((row) => ({
          feature_id: row.id,
          agents: row.agents.map((agent) => forRequest(agent, agents)),
        })),
        telemetry,
        verify_on: verifyOn,
        open_pr: openPr,
      });
      const count = result.run_ids.length;
      setStatus(`Started ${count} ${count === 1 ? "run" : "runs"}`);
      onLaunched();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the run");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="controls" onSubmit={handleSubmit}>
        {rows.map((row, index) => (
          <fieldset className="group group--framed" key={index}>
            <legend>Feature {index + 1}</legend>

            <Field id={`feature-${index}`} label="Feature">
              <select
                id={`feature-${index}`}
                value={row.id}
                onChange={(e) => chooseFeature(index, e.target.value)}
                required
              >
                <option value="">Choose a feature…</option>
                {features
                  // A feature can only be in one row: two rows of the same
                  // feature would be one run each, racing for its sandboxes.
                  .filter((f) => f.id === row.id || !rows.some((r) => r.id === f.id))
                  .map((feature) => (
                    <option key={feature.id} value={feature.id}>
                      {feature.id}
                    </option>
                  ))}
              </select>
            </Field>

            {row.agents.map((agent, agentIndex) => (
              <AgentSpecFields
                key={agentIndex}
                agent={agent}
                index={index}
                agentIndex={agentIndex}
                agents={agents}
                dispatch={dispatch}
              />
            ))}

            <div className="actions">
              <button type="button" onClick={() => dispatch({ type: "addAgent", index })}>
                Add agent
              </button>
              <button
                type="button"
                className="btn--quiet"
                disabled={rows.length === 1}
                onClick={() => dispatch({ type: "removeFeature", index })}
              >
                Remove feature {index + 1}
              </button>
            </div>
          </fieldset>
        ))}

        <div className="actions">
          <button type="button" onClick={() => dispatch({ type: "addFeature" })}>
            Add feature
          </button>
        </div>

        <div className="field-grid">
          <Field id="verify-on" label="Run tests on">
            <select
              id="verify-on"
              value={verifyOn}
              onChange={(e) => setVerifyOn(e.target.value as "host" | "sandbox")}
            >
              <option value="host">host (cheaper)</option>
              <option value="sandbox">sandbox</option>
            </select>
          </Field>
          <div className="checks checks--field">
            <label className="check">
              <input
                type="checkbox"
                checked={telemetry}
                onChange={(e) => setTelemetry(e.target.checked)}
              />
              Sample resource usage
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={openPr}
                onChange={(e) => setOpenPr(e.target.checked)}
              />
              Open a pull request
            </label>
          </div>
        </div>
        <p className="hint">
          These settings apply to every feature in the batch. Telemetry costs one{" "}
          <code>sbx exec</code> per sandbox per tick and keeps the sandbox alive. Opening a
          pull request writes to the real repository; the branch is pushed either way.
        </p>

        <div className="actions">
          <button type="submit" className="btn--primary" disabled={disabled || busy || !complete}>
            {busy ? "Starting…" : "Start run"}
          </button>
        </div>
      </form>

      {status && <Notice tone="ok">{status}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
    </>
  );
}
