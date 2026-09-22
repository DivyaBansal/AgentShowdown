import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentInfo, Feature } from "../api";
import { FeatureEditor } from "./FeatureEditor";

const AGENTS: AgentInfo[] = [
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

const FEATURES: Feature[] = [
  {
    id: "add-verbs",
    description: "Add verb practice.",
    acceptance_criteria: ["it works"],
    agents: [{ agent_id: "claude", run_label: null, model: null }],
  },
];

function stubOk() {
  const mock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ count: 1, path: "/repos/app/.agentshowdown/features.yaml" }),
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => vi.restoreAllMocks());

describe("FeatureEditor", () => {
  it("prefills from the repo's existing features", () => {
    stubOk();
    render(<FeatureEditor features={FEATURES} agents={AGENTS} onSaved={() => {}} />);

    expect(screen.getByLabelText(/feature id/i)).toHaveValue("add-verbs");
    expect(screen.getByLabelText(/description/i)).toHaveValue("Add verb practice.");
    expect(screen.getByLabelText(/criterion 1/i)).toHaveValue("it works");
  });

  it("adds and removes a feature", () => {
    stubOk();
    render(<FeatureEditor features={[]} agents={AGENTS} onSaved={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /add feature/i }));
    expect(screen.getByLabelText(/feature id/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /remove feature/i }));
    expect(screen.queryByLabelText(/feature id/i)).not.toBeInTheDocument();
  });

  it("adds an agent with the full spec available", () => {
    stubOk();
    render(<FeatureEditor features={FEATURES} agents={AGENTS} onSaved={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /add agent/i }));

    expect(screen.getAllByRole("group", { name: /agent \d/i })).toHaveLength(2);
  });

  it("reveals the command field behind Advanced", () => {
    stubOk();
    render(<FeatureEditor features={FEATURES} agents={AGENTS} onSaved={() => {}} />);

    // The command is what agent kits with no built-in launcher require.
    expect(screen.getByLabelText(/^command$/i)).toBeInTheDocument();
  });

  it("edits environment variables as key/value pairs", () => {
    stubOk();
    render(<FeatureEditor features={FEATURES} agents={AGENTS} onSaved={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /add variable/i }));
    fireEvent.change(screen.getByLabelText(/variable 1 name/i), {
      target: { value: "ANTHROPIC_BASE_URL" },
    });

    expect(screen.getByLabelText(/variable 1 name/i)).toHaveValue("ANTHROPIC_BASE_URL");
  });

  it("posts the edited features, carrying env through", async () => {
    const mock = stubOk();
    render(<FeatureEditor features={FEATURES} agents={AGENTS} onSaved={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /add variable/i }));
    fireEvent.change(screen.getByLabelText(/variable 1 name/i), {
      target: { value: "ANTHROPIC_BASE_URL" },
    });
    fireEvent.change(screen.getByLabelText(/variable 1 value/i), {
      target: { value: "http://host.docker.internal:11434" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /save features/i }).closest("form")!);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    const body = JSON.parse(String(mock.mock.calls[0]?.[1]?.body)) as {
      features: { id: string; agents: { env: Record<string, string> }[] }[];
    };
    expect(body.features[0]?.id).toBe("add-verbs");
    expect(body.features[0]?.agents[0]?.env).toEqual({
      ANTHROPIC_BASE_URL: "http://host.docker.internal:11434",
    });
  });

  it("surfaces a rejected save", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: "Unprocessable",
        json: async () => ({ detail: "duplicate feature ids: same" }),
      }),
    );
    render(<FeatureEditor features={FEATURES} agents={AGENTS} onSaved={() => {}} />);

    fireEvent.submit(screen.getByRole("button", { name: /save features/i }).closest("form")!);

    expect(await screen.findByRole("alert")).toHaveTextContent(/duplicate feature ids/i);
  });
});
