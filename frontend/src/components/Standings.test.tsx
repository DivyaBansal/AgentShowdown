import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Job } from "../api";
import { formatTimestamp } from "../lib/jobs";
import { Standings } from "./Standings";

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

describe("Standings", () => {
  it("lists every job in placement order by default", () => {
    render(<Standings jobs={JOBS} colorFor={() => "red"} />);
    // Finished first; cursor is still unfinished-but-not-failed; codex failed.
    expect(agentOrder()).toEqual(["claude", "cursor", "codex"]);
    expect(screen.getByRole("columnheader", { name: /rank/i })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });

  it("sorts by a column, and reverses on a second click", () => {
    render(<Standings jobs={JOBS} colorFor={() => "red"} />);

    fireEvent.click(screen.getByRole("button", { name: /duration/i }));
    expect(agentOrder()).toEqual(["codex", "claude", "cursor"]);
    expect(screen.getByRole("columnheader", { name: /duration/i })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );

    fireEvent.click(screen.getByRole("button", { name: /duration/i }));
    expect(screen.getByRole("columnheader", { name: /duration/i })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
    // Unreported stays last either way; it must not look like the longest run.
    expect(agentOrder()).toEqual(["claude", "codex", "cursor"]);
  });

  it("shows an unreported metric as a dash, never as zero", () => {
    render(<Standings jobs={JOBS} colorFor={() => "red"} />);
    const cursorRow = screen.getByText("cursor").closest("tr")!;
    const cells = within(cursorRow).getAllByRole("cell");
    expect(cells[4]).toHaveTextContent("—");
    expect(cells[4]).not.toHaveTextContent("0");
  });

  it("shows when each job started, and sorts by it", () => {
    const early = "2026-10-26T09:00:00+00:00";
    const late = "2026-10-26T10:00:00+00:00";
    render(
      <Standings
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
      <Standings
        jobs={[job({ tests_passed: 1, lint_passed: 0 })]}
        colorFor={() => "red"}
      />,
    );
    expect(screen.getByText("pass")).toBeInTheDocument();
    expect(screen.getByText("fail")).toBeInTheDocument();
  });
});
