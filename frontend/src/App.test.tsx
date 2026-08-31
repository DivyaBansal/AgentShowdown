import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { App } from "./App";
import { MockEventSource } from "./test/setup";

const PREFLIGHT_OK = {
  live_runs_possible: true,
  checks: [{ name: "sbx", ok: true, detail: "/usr/bin/sbx" }],
};

const JOB = {
  sandbox_name: "arena-f1-claude",
  feature_id: "f1",
  agent_id: "claude",
  run_label: null,
  branch: "agent/f1/claude",
  status: "succeeded",
  pr_url: null,
  detail: null,
  created_at: "",
  updated_at: "",
  run_id: "run-1",
  model: "claude-haiku-4-5",
  started_at: null,
  finished_at: null,
  duration_seconds: 93.5,
  tests_passed: 1,
  lint_passed: null,
  files_changed: 3,
  lines_added: 40,
  lines_removed: 2,
  input_tokens: 900,
  output_tokens: 80,
  num_turns: 4,
};

/** Routes each endpoint the app loads on mount. */
function stubApi(overrides: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = {
    "/api/preflight": PREFLIGHT_OK,
    "/api/features": { features: [] },
    "/api/agents": { agents: [] },
    "/api/sandboxes": { sandboxes: [] },
    "/api/jobs": { jobs: [JOB] },
    ...overrides,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const key = Object.keys(routes).find((r) => url.startsWith(r));
      return Promise.resolve({
        ok: key !== undefined,
        status: key === undefined ? 404 : 200,
        statusText: "Not Found",
        json: async () => routes[key ?? ""] ?? {},
      });
    }),
  );
}

describe("App", () => {
  beforeEach(() => MockEventSource.reset());
  afterEach(() => vi.restoreAllMocks());

  it("renders the masthead", async () => {
    stubApi();
    render(<App />);
    expect(
      screen.getByRole("heading", { name: /agentshowdown/i, level: 1 }),
    ).toBeInTheDocument();
    // Let the mount-time loads settle before unmounting, so their state
    // updates land inside act() rather than warning after the test ends.
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
  });

  it("shows a job card once jobs load", async () => {
    stubApi();
    render(<App />);
    await waitFor(() =>
      expect(screen.getByLabelText(/arena-f1-claude/i)).toBeInTheDocument(),
    );
  });

  it("renders the comparison charts alongside a table of every value", async () => {
    // The table is the accessibility backstop: nothing is gated behind a chart.
    stubApi();
    render(<App />);
    await waitFor(() => expect(screen.getAllByRole("img").length).toBe(3));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /tokens/i })).toBeInTheDocument();
  });

  it("reports the environment as ready when preflight passes", async () => {
    stubApi();
    render(<App />);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/environment ready/i),
    );
  });

  it("alerts and blocks launching when the environment cannot run sandboxes", async () => {
    stubApi({
      "/api/preflight": {
        live_runs_possible: false,
        checks: [{ name: "sandboxd", ok: false, detail: "daemon not running" }],
      },
    });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/daemon not running/i),
    );
  });

  it("says so when nothing has run yet", async () => {
    stubApi({ "/api/jobs": { jobs: [] } });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/nothing has run yet/i)).toBeInTheDocument(),
    );
  });
});
