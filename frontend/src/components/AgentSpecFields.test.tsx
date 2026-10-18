/** The per-agent form only offers what the chosen agent actually supports.
 *
 * Offering a model for an agent that never receives --model, or a
 * permissions toggle its CLI ignores, promises a change that never happens.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { AgentSpecFields } from "./AgentSpecFields";
import { emptyAgent } from "./featuresReducer";
import type { AgentInfo, AgentSpec } from "../api";
import { OLLAMA_BASE_URL } from "../lib/ollama";

afterEach(() => vi.unstubAllGlobals());

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

  describe("local Ollama model", () => {
    const ollamaEnv = {
      ANTHROPIC_BASE_URL: OLLAMA_BASE_URL,
      ANTHROPIC_AUTH_TOKEN: "ollama",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "qwen3.5:122b",
    };

    function stubOllama(body: unknown) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => body }));
    }

    it("is offered for claude", () => {
      fields({ agent_id: "claude" });
      expect(screen.getByRole("checkbox", { name: /local ollama model/i })).not.toBeChecked();
    });

    it("isn't offered for other agents", () => {
      fields({ agent_id: "opencode" });
      expect(
        screen.queryByRole("checkbox", { name: /local ollama model/i }),
      ).not.toBeInTheDocument();
    });

    it("fills in the endpoint variables and drops a claude-only model", () => {
      const dispatch = fields({
        agent_id: "claude",
        model: "claude-sonnet-5",
        env: { DEBUG: "1" },
      });

      fireEvent.click(screen.getByRole("checkbox", { name: /local ollama model/i }));

      expect(dispatch).toHaveBeenCalledWith({
        type: "setAgentField",
        index: 0,
        agentIndex: 0,
        field: "model",
        value: "",
      });
      expect(dispatch).toHaveBeenCalledWith({
        type: "setAgentEnv",
        index: 0,
        agentIndex: 0,
        env: {
          DEBUG: "1",
          ANTHROPIC_BASE_URL: OLLAMA_BASE_URL,
          ANTHROPIC_AUTH_TOKEN: "ollama",
        },
      });
    });

    it("suggests the models Ollama has pulled", async () => {
      stubOllama({ available: true, models: ["qwen3.5:122b", "gemma4:31b"] });

      fields({ agent_id: "claude", model: "qwen3.5:122b", env: ollamaEnv });

      expect(screen.getByRole("checkbox", { name: /local ollama model/i })).toBeChecked();

      // Datalist options carry only a value, so they have no accessible
      // name to query by; read the values of every option role instead.
      const optionValues = () =>
        screen
          .getAllByRole("option", { hidden: true })
          .map((option) => (option as HTMLOptionElement).value);

      await waitFor(() => expect(optionValues()).toContain("gemma4:31b"));
      expect(optionValues()).not.toContain("claude-sonnet-5");
      expect(screen.getByText(/models pulled into this machine's ollama/i)).toBeInTheDocument();
    });

    it("says so when Ollama can't be listed", async () => {
      stubOllama({ available: false, models: [] });

      fields({ agent_id: "claude", env: ollamaEnv });

      expect(await screen.findByText(/couldn't list ollama's models/i)).toBeInTheDocument();
    });

    it("routes background calls to a newly chosen model", () => {
      stubOllama({ available: true, models: [] });
      const dispatch = fields({ agent_id: "claude", model: "qwen3.5:122b", env: ollamaEnv });

      fireEvent.change(screen.getByLabelText(/^model$/i), { target: { value: "gemma4:31b" } });

      expect(dispatch).toHaveBeenCalledWith({
        type: "setAgentEnv",
        index: 0,
        agentIndex: 0,
        env: { ...ollamaEnv, ANTHROPIC_DEFAULT_HAIKU_MODEL: "gemma4:31b" },
      });
    });

    it("removes only its own variables and the Ollama model when switched off", () => {
      stubOllama({ available: true, models: [] });

      const dispatch = fields({
        agent_id: "claude",
        model: "qwen3.5:122b",
        env: { ...ollamaEnv, DEBUG: "1" },
      });

      fireEvent.click(screen.getByRole("checkbox", { name: /local ollama model/i }));

      expect(dispatch).toHaveBeenCalledWith({
        type: "setAgentField",
        index: 0,
        agentIndex: 0,
        field: "model",
        value: "",
      });
      expect(dispatch).toHaveBeenCalledWith({
        type: "setAgentEnv",
        index: 0,
        agentIndex: 0,
        env: { DEBUG: "1" },
      });
    });
  });
});
