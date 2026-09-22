import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { RunLauncher } from "./RunLauncher";
import type { AgentInfo, Feature } from "../api";

afterEach(() => vi.restoreAllMocks());

const features: Feature[] = [
  {
    id: "add-separable-verbs",
    description: "d",
    acceptance_criteria: [],
    agents: [
      {
        agent_id: "claude",
        run_label: null,
        model: "claude-haiku-4-5",
        command: null,
        dangerously_skip_permissions: null,
        kit: [],
        provider: null,
        env: {},
      },
    ],
  },
  { id: "add-inseparable-verbs", description: "d", acceptance_criteria: [], agents: [] },
];

const agents: AgentInfo[] = [
  {
    agent_id: "claude",
    requires_command: false,
    accepts_model: true,
    supports_skip_permissions: true,
    verified: true,
    known_models: ["claude-haiku-4-5"],
    default_model: "claude-haiku-4-5",
    reports_token_usage: true,
  },
  {
    agent_id: "cursor",
    requires_command: false,
    accepts_model: true,
    supports_skip_permissions: true,
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

function launcher(overrides: { disabled?: boolean; onLaunched?: () => void } = {}) {
  return render(
    <RunLauncher
      features={features}
      agents={agents}
      disabled={overrides.disabled ?? false}
      onLaunched={overrides.onLaunched ?? (() => {})}
    />,
  );
}

function okFetch(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentBody(fetchMock: ReturnType<typeof vi.fn>) {
  const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
  return JSON.parse(String(init?.body)) as {
    entries: { feature_id: string; agents: { agent_id: string; model: string | null }[] }[];
  };
}

describe("RunLauncher", () => {
  it("starts a run with the chosen feature and agents", async () => {
    const fetchMock = okFetch({ run_ids: ["run-abc123"] });
    const onLaunched = vi.fn();
    launcher({ onLaunched });

    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Started 1 run"));
    expect(onLaunched).toHaveBeenCalled();

    const body = sentBody(fetchMock);
    expect(body.entries).toHaveLength(1);
    expect(body.entries[0]?.feature_id).toBe("add-separable-verbs");
    expect(body.entries[0]?.agents[0]?.agent_id).toBe("claude");
  });

  it("starts several features at once, each with its own agents", async () => {
    // The whole point of the batch: different tasks, not just different
    // agents on one task.
    const fetchMock = okFetch({ run_ids: ["run-1", "run-2"] });
    launcher();

    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add feature$/i }));
    const rows = screen.getAllByRole("group", { name: /^feature \d/i });
    fireEvent.change(within(rows[1]!).getByLabelText(/^feature$/i), {
      target: { value: "add-inseparable-verbs" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Started 2 runs"));

    const body = sentBody(fetchMock);
    expect(body.entries.map((e) => e.feature_id)).toEqual([
      "add-separable-verbs",
      "add-inseparable-verbs",
    ]);
  });

  it("fills a row with the feature's own agents when it is chosen", () => {
    launcher();
    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });

    expect(screen.getByLabelText(/^model$/i)).toHaveValue("claude-haiku-4-5");
  });

  it("does not offer a feature already chosen in another row", () => {
    launcher();
    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add feature$/i }));

    const rows = screen.getAllByRole("group", { name: /^feature \d/i });
    const second = within(rows[1]!).getByLabelText(/^feature$/i);
    expect(within(second as HTMLElement).queryByRole("option", { name: "add-separable-verbs" }))
      .not.toBeInTheDocument();
  });

  it("cannot launch while a row has no feature", () => {
    launcher();
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("cannot launch while a row has no agent", () => {
    launcher();
    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.click(screen.getByRole("button", { name: /remove agent 1/i }));

    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("is disabled when the environment cannot run sandboxes", () => {
    launcher({ disabled: true });
    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });
    expect(screen.getByRole("button", { name: /start run/i })).toBeDisabled();
  });

  it("leaves out settings the chosen agent cannot use", async () => {
    // A model typed before switching to a command-only agent would be
    // rejected by the server, which never passes --model to one.
    const fetchMock = okFetch({ run_ids: ["run-1"] });
    launcher();
    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.change(screen.getByLabelText(/^agent$/i), { target: { value: "shell" } });
    fireEvent.change(screen.getByLabelText(/^command/i), { target: { value: "echo hi" } });
    fireEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(sentBody(fetchMock).entries[0]?.agents[0]?.model).toBeNull();
  });

  it("states the cost of telemetry and of opening a pull request", () => {
    launcher();
    expect(screen.getByText(/per sandbox per tick/i)).toBeInTheDocument();
    expect(screen.getByText(/writes to the real repository/i)).toBeInTheDocument();
  });

  it("surfaces a rejected launch as an alert", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => ({ detail: "'arena-f1-claude' is already running as part of run-1" }),
      }),
    );
    launcher();
    fireEvent.change(screen.getByLabelText(/^feature$/i), {
      target: { value: "add-separable-verbs" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start run/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/already running/));
  });
});
