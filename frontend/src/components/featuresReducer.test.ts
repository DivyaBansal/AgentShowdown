/** Pure reducer tests: the branchiest part of the editor, with no rendering. */

import { describe, expect, it } from "vitest";
import type { FeatureInput } from "../api";
import { emptyAgent, emptyFeature, featuresReducer } from "./featuresReducer";

function withOneFeature(): FeatureInput[] {
  return [{ id: "f1", description: "d", acceptance_criteria: [], agents: [] }];
}

describe("featuresReducer", () => {
  it("loads a feature list", () => {
    const loaded = featuresReducer([], { type: "load", features: withOneFeature() });

    expect(loaded).toHaveLength(1);
    expect(loaded[0]?.id).toBe("f1");
  });

  it("adds and removes features", () => {
    const added = featuresReducer([], { type: "addFeature" });
    expect(added).toHaveLength(1);

    expect(featuresReducer(added, { type: "removeFeature", index: 0 })).toHaveLength(0);
  });

  it("replaces one feature, leaving its neighbours alone", () => {
    // How the launcher fills a row with the chosen feature's own agents.
    const state = [...withOneFeature(), emptyFeature()];

    const replacement: FeatureInput = {
      id: "f2",
      description: "",
      acceptance_criteria: [],
      agents: [emptyAgent()],
    };

    const next = featuresReducer(state, { type: "replaceFeature", index: 1, feature: replacement });

    expect(next[0]?.id).toBe("f1");
    expect(next[1]?.id).toBe("f2");
    expect(next[1]?.agents).toHaveLength(1);
  });

  it("edits a field without touching its neighbours", () => {
    const state = [...withOneFeature(), emptyFeature()];

    const next = featuresReducer(state, {
      type: "setField",
      index: 0,
      field: "description",
      value: "changed",
    });

    expect(next[0]?.description).toBe("changed");
    expect(next[1]).toBe(state[1]);
  });

  it("adds, edits and removes acceptance criteria", () => {
    let state = featuresReducer(withOneFeature(), { type: "addCriterion", index: 0 });
    state = featuresReducer(state, {
      type: "setCriterion",
      index: 0,
      criterionIndex: 0,
      value: "it works",
    });
    expect(state[0]?.acceptance_criteria).toEqual(["it works"]);

    state = featuresReducer(state, { type: "removeCriterion", index: 0, criterionIndex: 0 });
    expect(state[0]?.acceptance_criteria).toEqual([]);
  });

  it("adds and removes agents", () => {
    let state = featuresReducer(withOneFeature(), { type: "addAgent", index: 0 });
    expect(state[0]?.agents).toHaveLength(1);

    state = featuresReducer(state, { type: "removeAgent", index: 0, agentIndex: 0 });
    expect(state[0]?.agents).toHaveLength(0);
  });

  it("keeps agent_id as a string but blanks other empty fields to null", () => {
    // An untouched optional field must be omitted from the YAML, not written
    // as an empty string.
    let state = featuresReducer(withOneFeature(), { type: "addAgent", index: 0 });

    state = featuresReducer(state, {
      type: "setAgentField",
      index: 0,
      agentIndex: 0,
      field: "run_label",
      value: "   ",
    });
    expect(state[0]?.agents[0]?.run_label).toBeNull();

    state = featuresReducer(state, {
      type: "setAgentField",
      index: 0,
      agentIndex: 0,
      field: "agent_id",
      value: "codex",
    });
    expect(state[0]?.agents[0]?.agent_id).toBe("codex");
  });

  it("sets env, kit and the permissions override", () => {
    let state = featuresReducer(withOneFeature(), { type: "addAgent", index: 0 });

    state = featuresReducer(state, {
      type: "setAgentEnv",
      index: 0,
      agentIndex: 0,
      env: { ANTHROPIC_BASE_URL: "http://x:11434" },
    });
    state = featuresReducer(state, {
      type: "setAgentKit",
      index: 0,
      agentIndex: 0,
      kit: ["./kit"],
    });
    state = featuresReducer(state, {
      type: "setAgentSkipPermissions",
      index: 0,
      agentIndex: 0,
      value: false,
    });

    const agent = state[0]?.agents[0];
    expect(agent?.env).toEqual({ ANTHROPIC_BASE_URL: "http://x:11434" });
    expect(agent?.kit).toEqual(["./kit"]);
    expect(agent?.dangerously_skip_permissions).toBe(false);
  });

  it("clears the model when the agent changes, so another vendor's name never sticks", () => {
    const state: FeatureInput[] = [
      { ...withOneFeature()[0]!, agents: [{ ...emptyAgent(), model: "claude-haiku-4-5" }] },
    ];

    const switched = featuresReducer(state, {
      type: "setAgentField",
      index: 0,
      agentIndex: 0,
      field: "agent_id",
      value: "codex",
    });

    expect(switched[0]?.agents[0]?.agent_id).toBe("codex");
    expect(switched[0]?.agents[0]?.model).toBeNull();
  });

  it("keeps the model when the agent is re-selected unchanged", () => {
    const state: FeatureInput[] = [
      { ...withOneFeature()[0]!, agents: [{ ...emptyAgent(), model: "claude-haiku-4-5" }] },
    ];

    const same = featuresReducer(state, {
      type: "setAgentField",
      index: 0,
      agentIndex: 0,
      field: "agent_id",
      value: "claude",
    });

    expect(same[0]?.agents[0]?.model).toBe("claude-haiku-4-5");
  });

  it("carries saved feature changes into untouched rows but keeps edited ones", () => {
    const haiku = { ...emptyAgent(), model: "claude-haiku-4-5" };
    const sonnet = { ...emptyAgent(), model: "claude-sonnet-4-5" };

    const previous: FeatureInput[] = [
      { id: "f1", description: "", acceptance_criteria: [], agents: [haiku] },
      { id: "f2", description: "", acceptance_criteria: [], agents: [haiku] },
    ];

    const next: FeatureInput[] = [
      { id: "f1", description: "", acceptance_criteria: [], agents: [sonnet] },
      { id: "f2", description: "", acceptance_criteria: [], agents: [sonnet] },
    ];

    const rows: FeatureInput[] = [
      { id: "f1", description: "", acceptance_criteria: [], agents: [haiku] },
      { id: "f2", description: "", acceptance_criteria: [], agents: [{ ...haiku, run_label: "mine" }] },
    ];

    const synced = featuresReducer(rows, { type: "syncFeatures", previous, next });

    expect(synced[0]?.agents[0]?.model).toBe("claude-sonnet-4-5");
    expect(synced[1]?.agents[0]?.model).toBe("claude-haiku-4-5");
    expect(synced[1]?.agents[0]?.run_label).toBe("mine");
  });

  it("clears a row whose feature was deleted", () => {
    const rows: FeatureInput[] = withOneFeature();

    const synced = featuresReducer(rows, { type: "syncFeatures", previous: rows, next: [] });

    expect(synced[0]).toEqual(emptyFeature());
  });

  it("starts a new agent on a sane default", () => {
    expect(emptyAgent().agent_id).toBe("claude");
    expect(emptyAgent().env).toEqual({});
  });
});
