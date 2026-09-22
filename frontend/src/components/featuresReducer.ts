/** State transitions for the features editor.
 *
 * A plain reducer in its own module rather than a state library: the shape is
 * a list of records with nested lists, which `useReducer` handles directly.
 * Keeping it pure and separate also means the branchiest code in the editor
 * is unit-testable without rendering anything.
 */

import type { AgentSpec, FeatureInput } from "../api";

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
  | { type: "setAgentEnv"; index: number; agentIndex: number; env: Record<string, string> }
  | { type: "setAgentKit"; index: number; agentIndex: number; kit: string[] };

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
        mapAgent(f, action.agentIndex, (a) => ({
          ...a,
          [action.field]:
            action.field === "agent_id" ? action.value : orNull(action.value),
        })),
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
    default:
      return state;
  }
}
