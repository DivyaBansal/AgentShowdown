/** The full agent spec for one agent on one feature.
 *
 * Every field the YAML supports is here, because the narrower set could not
 * express `env` (how an agent is pointed at a local Ollama or a gateway) or
 * `command` (required by agent kits with no built-in launcher).
 *
 * `model` is a free-text input backed by a datalist rather than a closed
 * select: a custom endpoint serves whatever model names it likes, which the
 * server's KNOWN_MODELS list cannot know ahead of time.
 *
 * "Local Ollama model" is a preset over `env`, not a separate setting: it
 * writes the variables that point claude at the host's Ollama and swaps the
 * model suggestions for what Ollama has pulled. Clearing it removes exactly
 * those variables and leaves any others alone.
 */

import { useEffect, useState } from "react";
import { fetchOllamaModels, type AgentInfo, type AgentSpec, type OllamaModels } from "../api";
import { supportsOllamaPreset, usesOllama, withOllama, withoutOllama } from "../lib/ollama";
import type { FeaturesAction } from "./featuresReducer";
import { Field } from "./ui/Field";

export function AgentSpecFields({
  agent,
  index,
  agentIndex,
  agents,
  dispatch,
}: {
  agent: AgentSpec;
  index: number;
  agentIndex: number;
  agents: AgentInfo[];
  dispatch: (action: FeaturesAction) => void;
}) {
  const envPairs = Object.entries(agent.env ?? {});
  const known = agents.find((a) => a.agent_id === agent.agent_id);
  // An agent absent from /agents is one this build doesn't know. Offering
  // every field is the safer guess there than hiding them all.
  const acceptsModel = known?.accepts_model ?? true;
  const requiresCommand = known?.requires_command ?? false;
  const supportsSkipPermissions = known?.supports_skip_permissions ?? true;
  const commandId = `command-${index}-${agentIndex}`;
  const offersOllama = acceptsModel && supportsOllamaPreset(agent.agent_id);
  const ollama = offersOllama && usesOllama(agent.env);

  // The id names the suggestion source, not just the row: Chromium keeps
  // showing a datalist's old options when they are swapped in place, so a
  // new agent (or the Ollama preset) has to point the input at a new list.
  const listId = `models-${index}-${agentIndex}-${agent.agent_id.replace(/[^\w-]/g, "_")}${
    ollama ? "-ollama" : ""
  }`;

  const [ollamaModels, setOllamaModels] = useState<OllamaModels | null>(null);

  // Asked only once the preset is on: most agents never use it, and each
  // ask shells out to `ollama list` on the host.
  useEffect(() => {
    if (!ollama || ollamaModels !== null) return;
    let cancelled = false;
    fetchOllamaModels()
      .catch((): OllamaModels => ({ available: false, models: [] }))
      .then((result) => {
        if (!cancelled) setOllamaModels(result);
      });
    return () => {
      cancelled = true;
    };
  }, [ollama, ollamaModels]);

  const suggestions = ollama ? (ollamaModels?.models ?? []) : (known?.known_models ?? []);
  let modelHint: string | undefined;
  if (ollama) {
    modelHint =
      ollamaModels === null || ollamaModels.available
        ? "Models pulled into this machine's Ollama."
        : "Couldn't list Ollama's models -- type the name as `ollama list` shows it.";
  } else if ((known?.known_models.length ?? 0) > 0) {
    modelHint = "Any name is accepted once a base-URL variable points the agent elsewhere.";
  }

  const commandField = (
    <Field
      id={commandId}
      label={requiresCommand ? "Command (required)" : "Command"}
      hint={
        requiresCommand
          ? "This agent kit has no launcher of its own, so it runs only what you put here."
          : "Replaces the built-in launcher and runs inside the sandbox."
      }
    >
      <textarea
        id={commandId}
        className="mono"
        value={agent.command ?? ""}
        rows={2}
        required={requiresCommand}
        aria-required={requiresCommand}
        onChange={(e) =>
          dispatch({
            type: "setAgentField",
            index,
            agentIndex,
            field: "command",
            value: e.target.value,
          })
        }
      />
    </Field>
  );

  function setEnv(pairs: [string, string][]) {
    // Blank keys are kept while editing -- a newly added row starts empty and
    // would otherwise vanish before it could be typed into. They are stripped
    // when the feature list is saved.
    dispatch({ type: "setAgentEnv", index, agentIndex, env: Object.fromEntries(pairs) });
  }

  function toggleOllama(on: boolean) {
    // A model that only one side serves can't survive the switch: a claude
    // name means nothing to Ollama, and an Ollama tag fails the known-model
    // check once the endpoint is gone. Either way, start the field over.
    const isKnown = agent.model !== null && (known?.known_models ?? []).includes(agent.model);
    const keepModel = agent.model !== null && (on ? !isKnown : isKnown);
    if (agent.model !== null && !keepModel) {
      dispatch({ type: "setAgentField", index, agentIndex, field: "model", value: "" });
    }
    dispatch({
      type: "setAgentEnv",
      index,
      agentIndex,
      env: on ? withOllama(agent.env, keepModel ? agent.model : null) : withoutOllama(agent.env),
    });
  }

  return (
    <fieldset className="group group--framed">
      <legend>
        Agent {agentIndex + 1}
        {/* Say plainly which agents have actually been checked against a
            real CLI, rather than offering them all as equals. */}
        {known && !known.verified && <span className="contender__meta"> · flags unverified</span>}
        {known && !known.reports_token_usage && (
          <span className="contender__meta"> · no token data</span>
        )}
      </legend>

      <div className="field-grid">
        <Field id={`agent-id-${index}-${agentIndex}`} label="Agent">
          <select
            id={`agent-id-${index}-${agentIndex}`}
            value={agent.agent_id}
            onChange={(e) =>
              dispatch({
                type: "setAgentField",
                index,
                agentIndex,
                field: "agent_id",
                value: e.target.value,
              })
            }
          >
            {agents.map((a) => (
              <option key={a.agent_id} value={a.agent_id}>
                {a.agent_id}
                {a.requires_command ? " (needs a command)" : ""}
              </option>
            ))}
          </select>
        </Field>

        {acceptsModel && (
          <Field
            id={`model-${index}-${agentIndex}`}
            label="Model"
            hint={modelHint}
          >
            <input
              id={`model-${index}-${agentIndex}`}
              list={listId}
              value={agent.model ?? ""}
              onChange={(e) => {
                dispatch({
                  type: "setAgentField",
                  index,
                  agentIndex,
                  field: "model",
                  value: e.target.value,
                });
                // Background calls follow the chosen model, or they would
                // ask Ollama for a claude model it doesn't serve.
                if (ollama) {
                  dispatch({
                    type: "setAgentEnv",
                    index,
                    agentIndex,
                    env: withOllama(agent.env, e.target.value.trim() || null),
                  });
                }
              }}
              placeholder={ollama ? "e.g. qwen3.5:122b" : (known?.default_model ?? "Model name")}
            />
            <datalist key={listId} id={listId}>
              {suggestions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
        )}

        {offersOllama && (
          <div className="checks checks--field">
            <label className="check">
              <input
                type="checkbox"
                checked={ollama}
                onChange={(e) => toggleOllama(e.target.checked)}
              />
              Local Ollama model
            </label>
          </div>
        )}

        <Field id={`run-label-${index}-${agentIndex}`} label="Run label">
          <input
            id={`run-label-${index}-${agentIndex}`}
            value={agent.run_label ?? ""}
            onChange={(e) =>
              dispatch({
                type: "setAgentField",
                index,
                agentIndex,
                field: "run_label",
                value: e.target.value,
              })
            }
            placeholder="Only to run one agent twice"
          />
        </Field>

        {supportsSkipPermissions && (
          <Field
            id={`skip-${index}-${agentIndex}`}
            label="Skip permissions"
            hint="Lets the agent act without approving each tool call."
          >
            <select
              id={`skip-${index}-${agentIndex}`}
              value={
                agent.dangerously_skip_permissions === null ||
                agent.dangerously_skip_permissions === undefined
                  ? "default"
                  : String(agent.dangerously_skip_permissions)
              }
              onChange={(e) =>
                dispatch({
                  type: "setAgentSkipPermissions",
                  index,
                  agentIndex,
                  value: e.target.value === "default" ? null : e.target.value === "true",
                })
              }
            >
              <option value="default">Config default</option>
              <option value="true">On</option>
              <option value="false">Off</option>
            </select>
          </Field>
        )}
      </div>

      {requiresCommand && commandField}

      <details className="disclosure">
        <summary>Advanced</summary>
        <div className="disclosure__body">
          <Field id={`provider-${index}-${agentIndex}`} label="Provider">
            <input
              id={`provider-${index}-${agentIndex}`}
              value={agent.provider ?? ""}
              onChange={(e) =>
                dispatch({
                  type: "setAgentField",
                  index,
                  agentIndex,
                  field: "provider",
                  value: e.target.value,
                })
              }
            />
          </Field>

          {!requiresCommand && commandField}

          <Field
            id={`kit-${index}-${agentIndex}`}
            label="Kits"
            hint="One sbx kit reference per line, attached when the sandbox is created."
          >
            <textarea
              id={`kit-${index}-${agentIndex}`}
              className="mono"
              value={(agent.kit ?? []).join("\n")}
              rows={2}
              onChange={(e) =>
                dispatch({
                  type: "setAgentKit",
                  index,
                  agentIndex,
                  kit: e.target.value.split("\n").filter((ref) => ref.trim() !== ""),
                })
              }
            />
          </Field>

          <fieldset className="group">
            <legend>Environment variables</legend>
            {envPairs.map(([key, value], i) => (
              <div className="kv-row" key={i}>
                <input
                  aria-label={`Variable ${i + 1} name`}
                  className="mono"
                  value={key}
                  placeholder="NAME"
                  onChange={(e) => {
                    const next: [string, string][] = [...envPairs] as [string, string][];
                    next[i] = [e.target.value, value];
                    setEnv(next);
                  }}
                />
                <input
                  aria-label={`Variable ${i + 1} value`}
                  className="mono"
                  value={value}
                  placeholder="value"
                  onChange={(e) => {
                    const next: [string, string][] = [...envPairs] as [string, string][];
                    next[i] = [key, e.target.value];
                    setEnv(next);
                  }}
                />
                <button
                  type="button"
                  className="btn--quiet"
                  onClick={() => setEnv(envPairs.filter((_, j) => j !== i) as [string, string][])}
                >
                  Remove
                </button>
              </div>
            ))}
            <div className="actions">
              <button
                type="button"
                onClick={() => setEnv([...(envPairs as [string, string][]), ["", ""]])}
              >
                Add variable
              </button>
            </div>
            <p className="hint">
              Setting a base-URL variable (for example ANTHROPIC_BASE_URL) points the
              agent at a custom endpoint and waives the known-model check.
            </p>
          </fieldset>
        </div>
      </details>

      <div className="actions">
        <button
          type="button"
          className="btn--quiet"
          onClick={() => dispatch({ type: "removeAgent", index, agentIndex })}
        >
          Remove agent {agentIndex + 1}
        </button>
      </div>
    </fieldset>
  );
}
