import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchInitFor, jsonBody, type Json } from "../test/fetch";
import { ConfigEditor } from "./ConfigEditor";

const DOC = {
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
      verify_on: "host",
      test_command: "",
      lint_command: "",
      telemetry: false,
      telemetry_interval_seconds: 5,
      capture_usage: true,
      agent_otel_endpoint: "http://host.docker.internal:8000",
      remove_sandbox_on_success: true,
      remove_sandbox_on_failure: true,
    },
    agents: {},
  },
};

function stub(doc: Json = DOC, putOk = true) {
  const mock = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      return Promise.resolve({
        ok: putOk,
        status: putOk ? 200 : 409,
        statusText: "Conflict",
        json: async () =>
          putOk ? { path: DOC.path } : { detail: "The bundled demo preset is read-only" },
      });
    }

    return Promise.resolve({ ok: true, status: 200, json: async () => doc });
  });

  vi.stubGlobal("fetch", mock);

  return mock;
}

afterEach(() => vi.restoreAllMocks());

describe("ConfigEditor", () => {
  it("places one Update models button before GitHub without changing unsaved values", async () => {
    stub();
    const { rerender } = render(<ConfigEditor reloadKey={0} />);
    const button = await screen.findByRole("button", { name: "Update models" });
    const github = screen.getByText("GitHub", { selector: "legend" });
    expect(button.compareDocumentPosition(github) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Update models" })).toHaveLength(1);
    fireEvent.change(screen.getByLabelText(/default model/i), { target: { value: "manual-model" } });
    rerender(<ConfigEditor reloadKey={0} agents={[{
      agent_id: "claude", accepts_model: true, requires_command: false,
      supports_skip_permissions: true, verified: true, reports_token_usage: true,
      known_models: ["new-model"], default_model: "claude-haiku-4-5",
      model_source: "cli", models_updated_at: "2026-10-18T12:00:00Z",
    }]} />);
    expect(screen.getByLabelText(/default model/i)).toHaveValue("manual-model");
    expect(screen.getByText(/This model is not listed/)).toBeInTheDocument();
    expect(document.querySelector('datalist option[value="new-model"]')).toBeInTheDocument();
  });

  it("disables model updates when the demo configuration is read-only", async () => {
    stub({ ...DOC, editable: false });
    render(<ConfigEditor reloadKey={0} />);
    expect(await screen.findByRole("button", { name: "Update models" })).toBeDisabled();
  });
  it("prefills from the workspace's saved config", async () => {
    stub();
    render(<ConfigEditor reloadKey={0} />);

    expect(await screen.findByLabelText(/repository \(owner\/name\)/i)).toHaveValue("owner/app");
    expect(screen.getByLabelText(/default model/i)).toHaveValue("claude-haiku-4-5");
  });

  it("says when it is showing an unsaved draft", async () => {
    stub({ ...DOC, exists: false });
    render(<ConfigEditor reloadKey={0} />);

    expect(await screen.findByText(/no configuration saved at/i)).toBeInTheDocument();
  });

  it("does not load the demo-seeded draft values when nothing is saved", async () => {
    // Regression: the server seeds an unsaved draft from the bundled demo, and
    // the form used to prefill it, so a blind Save wrote the demo's agent,
    // model and test command into the user's own config.
    const draft = {
      ...DOC,
      exists: false,
      config: {
        ...DOC.config,
        run: { ...DOC.config.run, test_command: "bash tests/test_german_practice.sh" },
      },
    };

    const mock = stub(draft);
    render(<ConfigEditor reloadKey={0} />);

    const repo = await screen.findByLabelText(/repository \(owner\/name\)/i);
    expect(repo).toHaveValue("");
    expect(screen.getByLabelText(/default agent/i)).toHaveValue("");
    expect(screen.getByLabelText(/default model/i)).toHaveValue("");
    expect(screen.getByLabelText(/test command/i)).toHaveValue("");
    // Generic settings are still there, so the form is not a blank slate.
    expect(screen.getByLabelText(/base branch/i)).toHaveValue("main");
    expect(screen.getByLabelText(/timeout/i)).toHaveValue(60);

    fireEvent.submit(
      screen.getByRole("button", { name: /save configuration/i }).closest("form")!,
    );
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(jsonBody(fetchInitFor(mock, "PUT"))).toMatchObject({
      agent: { model: "" },
      run: { test_command: "", status_file: ".agent_status.json" },
    });
  });

  it("offers the repo's detected remote and the agent's default model as hints", async () => {
    stub({ ...DOC, exists: false });
    render(
      <ConfigEditor
        reloadKey={0}
        agents={[
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
        ]}
      />,
    );

    // The remote comes from the selected repo itself, not the demo.
    expect(await screen.findByLabelText(/repository \(owner\/name\)/i)).toHaveAttribute(
      "placeholder",
      "owner/app",
    );
    fireEvent.change(screen.getByLabelText(/default agent/i), { target: { value: "claude" } });
    expect(screen.getByLabelText(/default model/i)).toHaveAttribute(
      "placeholder",
      "claude-haiku-4-5",
    );
  });

  it("swaps the default model for the new agent's own when the agent changes", async () => {
    stub();

    const agent = {
      requires_command: false,
      accepts_model: true,
      supports_skip_permissions: true,
      verified: true,
      reports_token_usage: true,
    };

    render(
      <ConfigEditor
        reloadKey={0}
        agents={[
          {
            ...agent,
            agent_id: "claude",
            known_models: ["claude-haiku-4-5"],
            default_model: "claude-haiku-4-5",
          },
          { ...agent, agent_id: "codex", known_models: ["gpt-5-codex"], default_model: "gpt-5-codex" },
        ]}
      />,
    );

    fireEvent.change(await screen.findByLabelText(/default agent/i), {
      target: { value: "codex" },
    });

    expect(screen.getByLabelText(/default model/i)).toHaveValue("gpt-5-codex");
    // A fresh list, not the claude one with its options swapped.
    const listId = screen.getByLabelText(/default model/i).getAttribute("list");
    const options = [...(document.getElementById(listId ?? "")?.querySelectorAll("option") ?? [])];
    expect(listId).toContain("codex");
    expect(options.map((o) => o.value)).toEqual(["gpt-5-codex"]);
  });

  it("reports the loaded document and marks it saved after a save", async () => {
    stub({ ...DOC, exists: false });
    const docs: boolean[] = [];
    render(<ConfigEditor reloadKey={0} onDocChange={(d) => docs.push(d.exists)} />);

    fireEvent.submit(
      (await screen.findByRole("button", { name: /save configuration/i })).closest("form")!,
    );
    await waitFor(() => expect(docs).toEqual([false, true]));
  });

  it("marks the bundled demo preset read-only and blocks saving", async () => {
    stub({ ...DOC, editable: false });
    render(<ConfigEditor reloadKey={0} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/read-only/i);
    expect(screen.getByRole("button", { name: /save configuration/i })).toBeDisabled();
  });

  it("saves the edited values", async () => {
    const mock = stub();
    render(<ConfigEditor reloadKey={0} />);

    fireEvent.change(await screen.findByLabelText(/base branch/i), {
      target: { value: "develop" },
    });
    fireEvent.submit(
      screen.getByRole("button", { name: /save configuration/i }).closest("form")!,
    );

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(jsonBody(fetchInitFor(mock, "PUT"))).toMatchObject({
      github: { base_branch: "develop" },
    });
  });

  it("requires a typed confirmation before a host-run test command", async () => {
    // This command runs on the host via `bash -c`, not in a sandbox.
    stub();
    render(<ConfigEditor reloadKey={0} />);

    fireEvent.change(await screen.findByLabelText(/test command/i), {
      target: { value: "make test" },
    });

    expect(screen.getByRole("alert")).toHaveTextContent(/runs on/i);
    expect(screen.getByRole("button", { name: /save configuration/i })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/confirmation/i), {
      target: { value: "RUN ON HOST" },
    });

    expect(screen.getByRole("button", { name: /save configuration/i })).toBeEnabled();
  });

  it("does not demand confirmation when tests run in the sandbox", async () => {
    stub();
    render(<ConfigEditor reloadKey={0} />);

    fireEvent.change(await screen.findByLabelText(/run tests on/i), {
      target: { value: "sandbox" },
    });
    fireEvent.change(screen.getByLabelText(/test command/i), {
      target: { value: "make test" },
    });

    expect(screen.queryByLabelText(/confirmation/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save configuration/i })).toBeEnabled();
  });

  it("surfaces a rejected save", async () => {
    stub(DOC, false);
    render(<ConfigEditor reloadKey={0} />);

    fireEvent.submit(
      (await screen.findByRole("button", { name: /save configuration/i })).closest("form")!,
    );

    expect(await screen.findByText(/read-only/i)).toBeInTheDocument();
  });
});
