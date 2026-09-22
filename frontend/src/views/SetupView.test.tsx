import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Feature, WorkspaceList } from "../api";
import { SetupView } from "./SetupView";

const SAVED_CONFIG = {
  path: "/repos/app/.agentshowdown/config.yaml",
  exists: true,
  source: "workspace",
  editable: true,
  repo_path: "/repos/app",
  config: {
    github: { repo: "owner/app", pat_secret_name: "github", base_branch: "main", open_pr: false },
    agent: {
      sbx_agent: "claude",
      model: "claude-haiku-4-5",
      dangerously_skip_permissions: true,
      max_turns: null,
      provider: null,
    },
    run: {
      status_file: ".agent_status.json",
      liveness_interval_seconds: 3,
      poll_interval_seconds: 5,
      timeout_minutes: 60,
      max_concurrency: 3,
      verify_on: "sandbox",
      test_command: "",
      lint_command: "",
      telemetry: false,
      telemetry_interval_seconds: 5,
      capture_usage: true,
      remove_sandbox_on_success: true,
      remove_sandbox_on_failure: true,
    },
    agents: {},
  },
};

const FEATURE: Feature = {
  id: "add-verbs",
  description: "Add verb practice.",
  acceptance_criteria: [],
  agents: [],
};

const NO_REPO: WorkspaceList = { active: null, workspaces: [] };
const REPO: WorkspaceList = {
  active: "/repos/app",
  workspaces: [{ path: "/repos/app", origin_url: null, github_repo: null, cloned: false }],
};

function stubApi(config: unknown) {
  const routes: Record<string, unknown> = {
    "/api/config": config,
    "/api/secrets": { available: true, secrets: [] },
    "/api/workspaces": NO_REPO,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const key = Object.keys(routes)
        .filter((r) => url.startsWith(r))
        .sort((a, b) => b.length - a.length)[0];
      return Promise.resolve({
        ok: key !== undefined,
        status: key === undefined ? 404 : 200,
        statusText: "Not Found",
        json: async () => routes[key ?? ""] ?? {},
      });
    }),
  );
}

function panel(name: string): HTMLDetailsElement {
  return screen.getByRole("heading", { name }).closest("details")!;
}

beforeEach(() => vi.useRealTimers());
afterEach(() => vi.restoreAllMocks());

describe("SetupView", () => {
  it("opens only the Repository panel when nothing is selected", async () => {
    stubApi({ ...SAVED_CONFIG, exists: false });
    render(<SetupView features={[]} agents={[]} workspaces={NO_REPO} onChanged={() => {}} />);

    expect(await screen.findByText("0 of 3 ready")).toBeInTheDocument();
    expect(panel("Repository").open).toBe(true);
    expect(panel("Configuration").open).toBe(false);
    expect(panel("Secrets").open).toBe(false);
    expect(panel("Features").open).toBe(false);
    expect(screen.getByText("required")).toBeInTheDocument();
  });

  it("collapses everything and says so once the workspace is ready", async () => {
    stubApi(SAVED_CONFIG);
    render(
      <SetupView features={[FEATURE]} agents={[]} workspaces={REPO} onChanged={() => {}} />,
    );

    expect(await screen.findByText("Ready to run")).toBeInTheDocument();
    expect(panel("Repository").open).toBe(false);
    expect(screen.getByText("saved")).toBeInTheDocument();
    expect(screen.getByText("1 defined")).toBeInTheDocument();
    expect(await screen.findByText("none stored")).toBeInTheDocument();
  });

  it("flags a configuration that has not been saved yet", async () => {
    stubApi({ ...SAVED_CONFIG, exists: false });
    render(
      <SetupView features={[FEATURE]} agents={[]} workspaces={REPO} onChanged={() => {}} />,
    );

    expect(await screen.findByText("not saved")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 ready")).toBeInTheDocument();
  });
});
