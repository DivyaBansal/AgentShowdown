import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { GroupedJobs } from "./GroupedJobs";
import type { Job } from "../api";

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
    run_id: "run-1",
    model: null,
    started_at: null,
    finished_at: null,
    duration_seconds: null,
    tests_passed: null,
    lint_passed: null,
    files_changed: null,
    lines_added: null,
    lines_removed: null,
    input_tokens: null,
    output_tokens: null,
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

describe("GroupedJobs", () => {
  it("groups by state by default, with the most urgent group first", () => {
    render(
      <GroupedJobs
        jobs={[
          job({ sandbox_name: "done", status: "succeeded" }),
          job({ sandbox_name: "blocked", status: "awaiting_input" }),
        ]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["Needs input", "Ready for review"]);
  });

  it("switches to grouping by feature when asked", () => {
    render(
      <GroupedJobs
        jobs={[
          job({ sandbox_name: "a", feature_id: "add-login" }),
          job({ sandbox_name: "b", feature_id: "add-logout" }),
        ]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    fireEvent.change(screen.getByLabelText(/group by/i), { target: { value: "feature" } });

    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["add-login", "add-logout"]);
  });

  it("shows every job exactly once across the groups", () => {
    render(
      <GroupedJobs
        jobs={[
          job({ sandbox_name: "box-1", status: "running" }),
          job({ sandbox_name: "box-2", status: "crashed" }),
        ]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByLabelText(/box-1/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/box-2/i)).toBeInTheDocument();
  });

  it("shows the job count on each group", () => {
    render(
      <GroupedJobs
        jobs={[job({ status: "running" }), job({ sandbox_name: "b", status: "running" })]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("2 jobs")).toBeInTheDocument();
  });
});
