/** The per-agent form only offers what the chosen agent actually supports.
 *
 * Offering a model for an agent that never receives --model, or a
 * permissions toggle its CLI ignores, promises a change that never happens.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AgentSpecFields } from "./AgentSpecFields";
import { emptyAgent } from "./featuresReducer";
import type { AgentInfo, AgentSpec } from "../api";

const agents: AgentInfo[] = [
  {
    agent_id: "claude",
    requires_command: false,
    accepts_model: true,
    supports_skip_permissions: true,
    verified: true,
    known_models: ["claude-haiku-4-5", "claude-sonnet-5"],
    default_model: "claude-haiku-4-5",
    reports_token_usage: true,
  },
  {
    agent_id: "opencode",
    requires_command: false,
    accepts_model: true,
    supports_skip_permissions: false,
    verified: false,
    known_models: [],
    default_model: null,
    reports_token_usage: false,
  },
  {
    agent_id: "shell",
    requires_command: true,
    accepts_model: false,
    supports_skip_permissions: false,
    verified: false,
    known_models: [],
    default_model: null,
    reports_token_usage: false,
  },
];

function fields(agent: Partial<AgentSpec> = {}) {
  const dispatch = vi.fn();
  render(
    <AgentSpecFields
      agent={{ ...emptyAgent(), ...agent }}
      index={0}
      agentIndex={0}
      agents={agents}
      dispatch={dispatch}
    />,
  );
  return dispatch;
}

describe("AgentSpecFields", () => {
  it("offers the known models for an agent that takes one", () => {
    fields({ agent_id: "claude" });

    expect(screen.getByLabelText(/^model$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^model$/i)).toHaveAttribute("placeholder", "claude-haiku-4-5");
  });

  it("hides the model for an agent that never receives one", () => {
    fields({ agent_id: "shell" });

    expect(screen.queryByLabelText(/^model$/i)).not.toBeInTheDocument();
  });

  it("requires a command for an agent with no launcher of its own", () => {
    fields({ agent_id: "shell" });

    expect(screen.getByLabelText(/^command \(required\)/i)).toBeRequired();
  });

  it("hides the permissions toggle for an agent whose CLI ignores it", () => {
    fields({ agent_id: "opencode" });

    expect(screen.queryByLabelText(/skip permissions/i)).not.toBeInTheDocument();
  });

  it("records a permissions choice, keeping the config default distinct", () => {
    const dispatch = fields({ agent_id: "claude" });

    fireEvent.change(screen.getByLabelText(/skip permissions/i), { target: { value: "false" } });

    expect(dispatch).toHaveBeenCalledWith({
      type: "setAgentSkipPermissions",
      index: 0,
      agentIndex: 0,
      value: false,
    });
  });

  it("records kits one per line, ignoring blank lines", () => {
    const dispatch = fields({ agent_id: "claude" });

    fireEvent.change(screen.getByLabelText(/^kits/i), {
      target: { value: "./kits/auth\n\n./kits/skills" },
    });

    expect(dispatch).toHaveBeenCalledWith({
      type: "setAgentKit",
      index: 0,
      agentIndex: 0,
      kit: ["./kits/auth", "./kits/skills"],
    });
  });

  it("says which agents are unverified and report no token usage", () => {
    fields({ agent_id: "opencode" });

    expect(screen.getByText(/flags unverified/i)).toBeInTheDocument();
    expect(screen.getByText(/no token data/i)).toBeInTheDocument();
  });
});
