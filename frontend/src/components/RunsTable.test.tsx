import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Job } from "../api";
import { formatTimestamp } from "../lib/jobs";
import { RunsTable } from "./RunsTable";

function job(overrides: Partial<Job> = {}): Job {
  return {
    sandbox_name: "box",
    feature_id: "f1",
    agent_id: "claude",
    run_label: null,
    branch: null,
    status: "succeeded",
    pr_url: null,
    detail: null,
    created_at: "",
    updated_at: "",
    run_id: null,
    model: null,
    started_at: null,
    finished_at: null,
    duration_seconds: 100,
    tests_passed: 1,
    lint_passed: null,
    files_changed: null,
    lines_added: 10,
    lines_removed: 2,
    input_tokens: 900,
    output_tokens: 100,
    num_turns: null,
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

const JOBS = [
  job({ sandbox_name: "codex", agent_id: "codex", status: "tests_failed", duration_seconds: 40 }),
  job({ sandbox_name: "claude", agent_id: "claude", duration_seconds: 120 }),
  job({ sandbox_name: "cursor", agent_id: "cursor", duration_seconds: null, input_tokens: null, output_tokens: null }),
];

/** The agent name in each body row, top to bottom. */
function agentOrder(): string[] {
  const [, body] = screen.getAllByRole("rowgroup");

  return within(body!)
    .getAllByRole("row")
    .map((row) => within(row).getAllByRole("cell")[1]!.textContent ?? "");
}

describe("RunsTable", () => {
  it("lists the newest run first by default, with no ranking", () => {
    render(
      <RunsTable
        jobs={[
          job({ sandbox_name: "old", agent_id: "codex", started_at: "2026-10-01T09:00:00Z" }),
          job({ sandbox_name: "new", agent_id: "claude", started_at: "2026-10-03T09:00:00Z" }),
          job({ sandbox_name: "unstarted", agent_id: "cursor", started_at: null }),
        ]}
        colorFor={() => "red"}
      />,
    );

    // A run that hasn't started has no time to sort by, so it goes last.
    expect(agentOrder()).toEqual(["claude", "codex", "cursor"]);
    expect(screen.getByRole("columnheader", { name: /started/i })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    expect(screen.queryByRole("columnheader", { name: /rank/i })).not.toBeInTheDocument();
  });

  it("sorts by feature, newest first within each one", () => {
    render(
      <RunsTable
        jobs={[
          job({ sandbox_name: "b-new", feature_id: "b", agent_id: "codex", started_at: "2026-10-03T09:00:00Z" }),
          job({ sandbox_name: "a-old", feature_id: "a", agent_id: "claude", started_at: "2026-10-01T09:00:00Z" }),
          job({ sandbox_name: "a-new", feature_id: "a", agent_id: "cursor", started_at: "2026-10-02T09:00:00Z" }),
        ]}
        colorFor={() => "red"}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /feature/i }));
    expect(agentOrder()).toEqual(["cursor", "claude", "codex"]);

    fireEvent.click(screen.getByRole("button", { name: /feature/i }));
    expect(agentOrder()).toEqual(["codex", "cursor", "claude"]);
  });

  it("sorts by a column, and reverses on a second click", () => {
    render(<RunsTable jobs={JOBS} colorFor={() => "red"} />);

    fireEvent.click(screen.getByRole("button", { name: /agent time/i }));
    expect(agentOrder()).toEqual(["codex", "claude", "cursor"]);
    expect(screen.getByRole("columnheader", { name: /agent time/i })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );

    fireEvent.click(screen.getByRole("button", { name: /agent time/i }));
    expect(screen.getByRole("columnheader", { name: /agent time/i })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    // Unreported stays last either way; it must not look like the longest run.
    expect(agentOrder()).toEqual(["claude", "codex", "cursor"]);
  });

  it("shows an unreported metric as a dash, never as zero", () => {
    render(<RunsTable jobs={JOBS} colorFor={() => "red"} />);
    const cursorRow = screen.getByText("cursor").closest("tr")!;
    const cells = within(cursorRow).getAllByRole("cell");
    // Index 5 is the tokens column.
    expect(cells[5]).toHaveTextContent("—");
    expect(cells[5]).not.toHaveTextContent("0");
  });

  it("shows tool calls and model requests, sortable, with failures noted", () => {
    render(
      <RunsTable
        jobs={[
          job({ sandbox_name: "a", agent_id: "claude", tool_calls: 40, model_requests: 12 }),
          job({
            sandbox_name: "b",
            agent_id: "codex",
            tool_calls: 9,
            tool_failures: 2,
            model_requests: 30,
          }),
        ]}
        colorFor={() => "red"}
      />,
    );

    expect(screen.getByText("(2 failed)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /tool calls/i }));
    expect(agentOrder()).toEqual(["codex", "claude"]);
    fireEvent.click(screen.getByRole("button", { name: /requests/i }));
    expect(agentOrder()).toEqual(["claude", "codex"]);
  });

  it("says which skill kit a job carried, and none when it carried none", () => {
    render(
      <RunsTable
        jobs={[
          job({ sandbox_name: "a", kits: '["./demo/kits/frontend-design"]' }),
          job({ sandbox_name: "b", agent_id: "codex", kits: "[]" }),
        ]}
        colorFor={() => "red"}
      />,
    );

    expect(screen.getByText("frontend-design")).toBeInTheDocument();
    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("breaks total tokens down into input, cached and output on hover", () => {
    render(
      <RunsTable
        jobs={[job({ input_tokens: 1000, cached_input_tokens: 800, output_tokens: 50 })]}
        colorFor={() => "red"}
      />,
    );

    expect(screen.getByText((1050).toLocaleString())).toHaveAttribute(
      "title",
      expect.stringContaining("of which cached 800"),
    );
  });

  it("shows when each job started, and sorts by it", () => {
    const early = "2026-10-26T09:00:00+00:00";
    const late = "2026-10-26T10:00:00+00:00";
    render(
      <RunsTable
        jobs={[
          job({ sandbox_name: "b", agent_id: "codex", started_at: late }),
          job({ sandbox_name: "a", agent_id: "claude", started_at: early }),
        ]}
        colorFor={() => "red"}
      />,
    );

    expect(screen.getByText(formatTimestamp(early)!)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /started/i }));
    expect(agentOrder()).toEqual(["claude", "codex"]);
    fireEvent.click(screen.getByRole("button", { name: /started/i }));
    expect(agentOrder()).toEqual(["codex", "claude"]);
  });

  it("reports test and lint results as pass or fail", () => {
    render(
      <RunsTable
        jobs={[job({ tests_passed: 1, lint_passed: 0 })]}
        colorFor={() => "red"}
      />,
    );
    expect(screen.getByText("pass")).toBeInTheDocument();
    expect(screen.getByText("fail")).toBeInTheDocument();
  });
});

describe("RunsTable row selection", () => {
  it("opens a job when its row is clicked", () => {
    const onSelect = vi.fn();
    render(<RunsTable jobs={JOBS} colorFor={() => "red"} onSelect={onSelect} />);
    const button = screen.getByRole("button", { name: `Inspect ${JOBS[0]!.sandbox_name}` });

    fireEvent.click(button.closest("tr")!.querySelector("td.num")!);

    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect.mock.calls[0]?.[0]).toMatchObject({ sandbox_name: JOBS[0]!.sandbox_name });
  });

  it("opens a job from the keyboard through the agent button, once", () => {
    const onSelect = vi.fn();
    render(<RunsTable jobs={JOBS} colorFor={() => "red"} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: `Inspect ${JOBS[1]!.sandbox_name}` }));

    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect.mock.calls[0]?.[0]).toMatchObject({ sandbox_name: JOBS[1]!.sandbox_name });
  });

  it("marks the row open in the inspector", () => {
    render(
      <RunsTable
        jobs={JOBS}
        colorFor={() => "red"}
        selectedName={JOBS[0]!.sandbox_name}
        onSelect={() => {}}
      />,
    );

    const row = screen.getByRole("button", { name: `Inspect ${JOBS[0]!.sandbox_name}` }).closest("tr");
    expect(row).toHaveAttribute("aria-current", "true");
  });

  it("offers no inspect buttons without a handler", () => {
    render(<RunsTable jobs={JOBS} colorFor={() => "red"} />);

    expect(screen.queryByRole("button", { name: /^inspect/i })).not.toBeInTheDocument();
  });
});
