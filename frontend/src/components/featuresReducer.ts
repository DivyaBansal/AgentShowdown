/** State transitions for the features editor.
 *
 * A plain reducer in its own module rather than a state library: the shape is
 * a list of records with nested lists, which `useReducer` handles directly.
 * Keeping it pure and separate also means the branchiest code in the editor
 * is unit-testable without rendering anything.
 */

import type { AgentSpec, EnvVars, FeatureInput } from "../api";

export type FeaturesAction =
  | { type: "load"; features: FeatureInput[] }
  | { type: "addFeature" }
  | { type: "removeFeature"; index: number }
  | { type: "replaceFeature"; index: number; feature: FeatureInput }
  | { type: "setField"; index: number; field: "id" | "description"; value: string }
  | { type: "addCriterion"; index: number }
  | { type: "setCriterion"; index: number; criterionIndex: number; value: string }
  | { type: "removeCriterion"; index: number; criterionIndex: number }
  | { type: "addAgent"; index: number }
  | { type: "removeAgent"; index: number; agentIndex: number }
  | {
      type: "setAgentField";
      index: number;
      agentIndex: number;
      field: "agent_id" | "run_label" | "model" | "provider" | "command";
      value: string;
    }
  | { type: "setAgentSkipPermissions"; index: number; agentIndex: number; value: boolean | null }
  | { type: "setAgentEnv"; index: number; agentIndex: number; env: EnvVars }
  | { type: "setAgentKit"; index: number; agentIndex: number; kit: string[] }
  | { type: "syncFeatures"; previous: FeatureInput[]; next: FeatureInput[] };

export function emptyFeature(): FeatureInput {
  return { id: "", description: "", acceptance_criteria: [], agents: [] };
}

export function emptyAgent(): AgentSpec {
  return {
    agent_id: "claude",
    run_label: null,
    model: null,
    command: null,
    dangerously_skip_permissions: null,
    kit: [],
    provider: null,
    env: {},
  };
}

/** Applies `update` to the feature at `index`, leaving the rest untouched. */
function mapFeature(
  state: FeatureInput[],
  index: number,
  update: (feature: FeatureInput) => FeatureInput,
): FeatureInput[] {
  return state.map((feature, i) => (i === index ? update(feature) : feature));
}

function mapAgent(
  feature: FeatureInput,
  agentIndex: number,
  update: (agent: AgentSpec) => AgentSpec,
): FeatureInput {
  return {
    ...feature,
    agents: feature.agents.map((agent, i) => (i === agentIndex ? update(agent) : agent)),
  };
}

/** Empty strings become null so an untouched optional field is omitted from
 *  the YAML rather than written as "". */
function orNull(value: string): string | null {
  return value.trim() === "" ? null : value;
}

/** Carries saved feature changes into rows that were copied from them.
 *
 * A row whose agents still match what the old feature had was never
 * customised, so it takes the new feature's agents. A row that was edited
 * keeps its edits. A row whose feature no longer exists is cleared, since it
 * could only launch a run the server would reject.
 */
function syncFeatures(
  rows: FeatureInput[],
  previous: FeatureInput[],
  next: FeatureInput[],
): FeatureInput[] {
  return rows.map((row) => {
    if (row.id === "") return row;

    const updated = next.find((f) => f.id === row.id);

    if (updated === undefined) return emptyFeature();

    const before = previous.find((f) => f.id === row.id);

    const untouched =
      before !== undefined && JSON.stringify(before.agents) === JSON.stringify(row.agents);

    return untouched && updated.agents.length > 0 ? { ...row, agents: updated.agents } : row;
  });
}

export function featuresReducer(
  state: FeatureInput[],
  action: FeaturesAction,
): FeatureInput[] {
  switch (action.type) {
    case "load":
      return action.features;
    case "addFeature":
      return [...state, emptyFeature()];
    case "removeFeature":
      return state.filter((_, i) => i !== action.index);
    case "replaceFeature":
      return mapFeature(state, action.index, () => action.feature);
    case "setField":
      return mapFeature(state, action.index, (f) => ({ ...f, [action.field]: action.value }));
    case "addCriterion":
      return mapFeature(state, action.index, (f) => ({
        ...f,
        acceptance_criteria: [...f.acceptance_criteria, ""],
      }));
    case "setCriterion":
      return mapFeature(state, action.index, (f) => ({
        ...f,
        acceptance_criteria: f.acceptance_criteria.map((c, i) =>
          i === action.criterionIndex ? action.value : c,
        ),
      }));
    case "removeCriterion":
      return mapFeature(state, action.index, (f) => ({
        ...f,
        acceptance_criteria: f.acceptance_criteria.filter((_, i) => i !== action.criterionIndex),
      }));
    case "addAgent":
      return mapFeature(state, action.index, (f) => ({ ...f, agents: [...f.agents, emptyAgent()] }));
    case "removeAgent":
      return mapFeature(state, action.index, (f) => ({
        ...f,
        agents: f.agents.filter((_, i) => i !== action.agentIndex),
      }));
    case "setAgentField":
      return mapFeature(state, action.index, (f) =>
        mapAgent(f, action.agentIndex, (a) =>
          action.field === "agent_id"
            ? // A model belongs to one vendor's CLI, so it can't carry over to
              // another agent. Cleared, the field falls back to the new
              // agent's default and its suggestions are no longer filtered
              // down to the stale name.
              { ...a, agent_id: action.value, model: action.value === a.agent_id ? a.model : null }
            : { ...a, [action.field]: orNull(action.value) },
        ),
      );
    case "setAgentSkipPermissions":
      return mapFeature(state, action.index, (f) =>
        mapAgent(f, action.agentIndex, (a) => ({
          ...a,
          dangerously_skip_permissions: action.value,
        })),
      );
    case "setAgentEnv":
      return mapFeature(state, action.index, (f) =>
        mapAgent(f, action.agentIndex, (a) => ({ ...a, env: action.env })),
      );
    case "setAgentKit":
      return mapFeature(state, action.index, (f) =>
        mapAgent(f, action.agentIndex, (a) => ({ ...a, kit: action.kit })),
      );
    case "syncFeatures":
      return syncFeatures(state, action.previous, action.next);
    default:
      return state;
  }
}
