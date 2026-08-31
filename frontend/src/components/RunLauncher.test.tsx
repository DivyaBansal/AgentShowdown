import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { RunLauncher } from "./RunLauncher";
import type { AgentInfo, Feature } from "../api";

afterEach(() => vi.restoreAllMocks());

const features: Feature[] = [
  { id: "add-separable-verbs", description: "d", acceptance_criteria: [], agents: [] },
];

const agents: AgentInfo[] = [
  {
    agent_id: "claude",
    has_native_builder: true,
    verified: true,
    known_models: ["claude-haiku-4-5"],
    default_model: "claude-haiku-4-5",
    reports_token_usage: true,
  },
  {
    agent_id: "cursor",
    has_native_builder: true,
    verified: false,
    known_models: [],
    default_model: null,
    reports_token_usage: false,
  },
  {
    agent_id: "shell",
    has_native_builder: false,
    verified: false,
    known_models: [],
    default_model: null,
    reports_token_usage: false,
  },
];

describe("RunLauncher", () => {
  it("starts a run with the chosen feature and agents", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ run_id: "run-abc123" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const onLaunched = vi.fn();

    render(
      <RunLauncher
        features={features}
        agents={agents}
        disabled={false}
        onLaunched={onLaunched}
      />,
    );
    fireEvent.change(screen.getByLabelText(/feature/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /claude/i }));
    fireEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("run-abc123"),
    );
    expect(onLaunched).toHaveBeenCalledWith("run-abc123");

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const body = JSON.parse(String(init?.body)) as {
      feature_id: string;
      agents: { agent_id: string }[];
    };
    expect(body.feature_id).toBe("add-separable-verbs");
    expect(body.agents).toEqual([{ agent_id: "claude" }]);
  });

  it("cannot launch with no agent selected", () => {
    render(
      <RunLauncher features={features} agents={agents} disabled={false} onLaunched={() => {}} />,
    );
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("is disabled when the environment cannot run sandboxes", () => {
    render(
      <RunLauncher features={features} agents={agents} disabled onLaunched={() => {}} />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /claude/i }));
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("marks agents whose flags are unverified and which report no tokens", () => {
    // Presenting every agent as equally supported would be misleading.
    render(
      <RunLauncher features={features} agents={agents} disabled={false} onLaunched={() => {}} />,
    );
    expect(screen.getByText(/flags unverified/i)).toBeInTheDocument();
    expect(screen.getByText(/no token data/i)).toBeInTheDocument();
  });

  it("omits agents that have no native builder", () => {
    // `shell` has no coding-agent CLI of its own; offering it would fail.
    render(
      <RunLauncher features={features} agents={agents} disabled={false} onLaunched={() => {}} />,
    );
    expect(screen.queryByRole("checkbox", { name: /shell/i })).not.toBeInTheDocument();
  });

  it("states the cost of telemetry and of opening a pull request", () => {
    render(
      <RunLauncher features={features} agents={agents} disabled={false} onLaunched={() => {}} />,
    );
    expect(screen.getByText(/per sandbox per tick/i)).toBeInTheDocument();
    expect(screen.getByText(/writes to the real repository/i)).toBeInTheDocument();
  });

  it("surfaces a rejected launch as an alert", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: "Unprocessable",
        json: async () => ({ detail: "Unknown model 'gpt-4-turbo'" }),
      }),
    );
    render(
      <RunLauncher features={features} agents={agents} disabled={false} onLaunched={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText(/feature/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /claude/i }));
    fireEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/gpt-4-turbo/),
    );
  });
});
