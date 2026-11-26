import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../api";
import { JobInspector } from "./JobInspector";

function job(overrides: Partial<Job> = {}): Job {
  return {
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
    cached_input_tokens: null,
    tool_calls: null,
    tool_failures: null,
    model_requests: null,
    kits: null,
    telemetry: null,
    repo: null,
    log_tail: null,
    launch_s: null,
    agent_s: null,
    verify_s: null,
    push_s: null,
    failure_category: null,
    question_options: null,
    ...overrides,
  };
}

/** Answers each per-job read the inspector makes on mount. */
function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const body = url.endsWith("/agent-events")
        ? { timeline: [], summary: { event_counts: {}, cost_usd: null, tools: {} }, events: [] }
        : url.endsWith("/samples")
          ? { samples: [] }
          : {};

      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }),
  );
}

function renderInspector(overrides: Partial<Job> = {}, nav: { prev?: () => void; next?: () => void } = {}) {
  const onClose = vi.fn();

  render(
    <JobInspector
      job={job(overrides)}
      color="red"
      position={{ index: 2, total: 3 }}
      onPrevious={nav.prev ?? null}
      onNext={nav.next ?? null}
      onClose={onClose}
    />,
  );

  return { onClose };
}

describe("JobInspector", () => {
  beforeEach(() => {
    window.localStorage.clear();
    stubFetch();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("heads the panel with the job and where it sits in the board", async () => {
    renderInspector({ sandbox_name: "arena-x", model: "m-1", input_tokens: 1000, output_tokens: 234 });

    expect(screen.getByRole("heading", { name: "arena-x" })).toBeInTheDocument();
    expect(screen.getByLabelText("Contender 2 of 3")).toBeInTheDocument();
    expect(screen.getByText("m-1")).toBeInTheDocument();
    expect(screen.getByText((1234).toLocaleString())).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });

  it("says when a number was not reported instead of showing zero", () => {
    renderInspector({ model: null, input_tokens: null, output_tokens: null });

    expect(screen.getByText("Tokens").nextElementSibling).toHaveTextContent("—");
    expect(screen.getByText("Model").nextElementSibling).toHaveTextContent("—");
  });

  it("opens on the activity tab and switches to the output", () => {
    renderInspector({ log_tail: "hello from the agent" });

    expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText(/hello from the agent/)).not.toBeVisible();

    fireEvent.click(screen.getByRole("tab", { name: "Output" }));

    expect(screen.getByText(/hello from the agent/)).toBeVisible();
  });

  it("remembers the last tab for the next job opened", () => {
    renderInspector();
    fireEvent.click(screen.getByRole("tab", { name: "Telemetry" }));

    expect(window.localStorage.getItem("agentshowdown.inspector.section")).toBe("telemetry");
  });

  it("steps between contenders and closes", () => {
    const prev = vi.fn();
    const next = vi.fn();
    const { onClose } = renderInspector({}, { prev, next });

    fireEvent.click(screen.getByRole("button", { name: /previous contender/i }));
    fireEvent.click(screen.getByRole("button", { name: /next contender/i }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(prev).toHaveBeenCalledOnce();
    expect(next).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("disables stepping past either end", () => {
    renderInspector();

    expect(screen.getByRole("button", { name: /previous contender/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /next contender/i })).toBeDisabled();
  });

  it("shows why a job failed", () => {
    renderInspector({ status: "failed", detail: "tests broke" });
    expect(screen.getByText("tests broke")).toBeInTheDocument();
  });

  it("links the pull request when there is one", () => {
    renderInspector({ pr_url: "https://example.test/pr/1" });

    expect(screen.getByRole("link", { name: /view pull request/i })).toHaveAttribute(
      "href",
      "https://example.test/pr/1",
    );
  });
});
