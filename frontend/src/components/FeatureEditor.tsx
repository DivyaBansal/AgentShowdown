/** Edit the workspace's feature backlog.
 *
 * State is one useReducer over the feature list; the reducer itself lives in
 * featuresReducer.ts so the branchiest part is testable without rendering.
 *
 * Renders only the body; the surrounding Panel supplies the heading.
 */

import { useEffect, useReducer, useState } from "react";
import {
  ApiError,
  saveFeatures,
  type AgentInfo,
  type Feature,
  type FeatureInput,
} from "../api";
import { AgentSpecFields } from "./AgentSpecFields";
import { featuresReducer } from "./featuresReducer";
import { Field } from "./ui/Field";
import { Notice } from "./ui/Notice";

/** Drops the placeholder rows the env editor keeps while you type. */
function withoutBlankEnvKeys(items: FeatureInput[]): FeatureInput[] {
  return items.map((feature) => ({
    ...feature,
    agents: feature.agents.map((agent) => ({
      ...agent,
      env: Object.fromEntries(
        Object.entries(agent.env ?? {}).filter(([key]) => key.trim() !== ""),
      ),
    })),
  }));
}

function toInput(feature: Feature): FeatureInput {
  return {
    id: feature.id,
    description: feature.description,
    acceptance_criteria: feature.acceptance_criteria,
    agents: feature.agents,
  };
}

export function FeatureEditor({
  features,
  agents,
  onSaved,
}: {
  features: Feature[];
  agents: AgentInfo[];
  onSaved: () => void;
}) {
  const [items, dispatch] = useReducer(featuresReducer, []);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Prefill from whatever the repo already has.
  useEffect(() => {
    dispatch({ type: "load", features: features.map(toInput) });
  }, [features]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const saved = await saveFeatures(withoutBlankEnvKeys(items));
      setStatus(`Saved ${saved.count} feature(s)`);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the features");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="controls" onSubmit={handleSubmit}>
        {items.length === 0 && (
          <p className="hint">No features yet. Add one, or import from GitHub issues.</p>
        )}

        {items.map((feature, index) => (
          <div className="surface" key={index}>
            <div className="surface__head">
              <h3>
                <span className="panel__step">{String(index + 1).padStart(2, "0")}</span>{" "}
                <span className="mono">{feature.id || "untitled"}</span>
              </h3>
              <button
                type="button"
                className="btn--danger"
                onClick={() => dispatch({ type: "removeFeature", index })}
              >
                Remove feature {feature.id || index + 1}
              </button>
            </div>

            <div className="surface__body">
              <Field id={`feature-id-${index}`} label="Feature id">
                <input
                  id={`feature-id-${index}`}
                  className="mono"
                  value={feature.id}
                  onChange={(e) =>
                    dispatch({ type: "setField", index, field: "id", value: e.target.value })
                  }
                  placeholder="add-login-page"
                  required
                />
              </Field>

              <Field id={`feature-description-${index}`} label="Description">
                <textarea
                  id={`feature-description-${index}`}
                  rows={4}
                  value={feature.description}
                  onChange={(e) =>
                    dispatch({
                      type: "setField",
                      index,
                      field: "description",
                      value: e.target.value,
                    })
                  }
                  required
                />
              </Field>

              <fieldset className="group">
                <legend>Acceptance criteria</legend>
                {feature.acceptance_criteria.map((criterion, criterionIndex) => (
                  <div className="repeat-row" key={criterionIndex}>
                    <input
                      aria-label={`Criterion ${criterionIndex + 1}`}
                      value={criterion}
                      onChange={(e) =>
                        dispatch({
                          type: "setCriterion",
                          index,
                          criterionIndex,
                          value: e.target.value,
                        })
                      }
                    />
                    <button
                      type="button"
                      className="btn--quiet"
                      onClick={() =>
                        dispatch({ type: "removeCriterion", index, criterionIndex })
                      }
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <div className="actions">
                  <button type="button" onClick={() => dispatch({ type: "addCriterion", index })}>
                    Add criterion
                  </button>
                </div>
              </fieldset>

              <fieldset className="group">
                <legend>Contenders</legend>
                {feature.agents.map((agent, agentIndex) => (
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
                </div>
              </fieldset>
            </div>
          </div>
        ))}

        <div className="actions">
          <button type="button" onClick={() => dispatch({ type: "addFeature" })}>
            Add feature
          </button>
          <button
            type="submit"
            className="btn--primary"
            disabled={busy || items.length === 0}
          >
            {busy ? "Saving…" : "Save features"}
          </button>
        </div>
      </form>

      {status && <Notice tone="ok">{status}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
    </>
  );
}
