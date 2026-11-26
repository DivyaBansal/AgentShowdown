import { render, screen, fireEvent } from "@testing-library/react";
import { useState, type ComponentProps } from "react";
import { describe, it, expect } from "vitest";
import { formatTimestamp, type GroupMode } from "../lib/jobs";
import { GroupModePicker, GroupedJobs } from "./GroupedJobs";
import type { Job } from "../api";

/** GroupedJobs and its picker, with the mode held the way CompareView holds it. */
function Grouped(props: Omit<ComponentProps<typeof GroupedJobs>, "mode">) {
  const [mode, setMode] = useState<GroupMode>("state");

  return (
    <>
      <GroupModePicker mode={mode} onChange={setMode} />
      <GroupedJobs {...props} mode={mode} />
    </>
  );
}

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

describe("GroupedJobs", () => {
  it("groups by state by default, with the most urgent group first", () => {
    render(
      <Grouped
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
      <Grouped
        jobs={[
          job({ sandbox_name: "a", feature_id: "add-login" }),
          job({ sandbox_name: "b", feature_id: "add-logout" }),
        ]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("radio", { name: "Feature" }));

    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["add-login", "add-logout"]);
  });

  it("shows every job exactly once across the groups", () => {
    render(
      <Grouped
        jobs={[
          job({ sandbox_name: "box-1", status: "running" }),
          job({ sandbox_name: "box-2", status: "crashed" }),
        ]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByRole("article", { name: /box-1/i })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: /box-2/i })).toBeInTheDocument();
  });

  it("gives each run its start time when grouped by run, and no run-wide delete", () => {
    const startedAt = "2026-10-26T09:30:00+00:00";
    render(
      <Grouped
        jobs={[
          job({ sandbox_name: "a", run_id: "run-0123456789ab", started_at: startedAt }),
          job({ sandbox_name: "b", run_id: "run-0123456789ab", started_at: null }),
        ]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("radio", { name: "Run" }));

    expect(screen.getByText(`Started ${formatTimestamp(startedAt)}`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete run" })).not.toBeInTheDocument();
  });

  it("labels jobs recorded without a run", () => {
    render(
      <Grouped jobs={[job({ run_id: null })]} colorFor={() => "c"} onSelect={() => {}} />,
    );

    fireEvent.click(screen.getByRole("radio", { name: "Run" }));

    expect(screen.getByRole("heading", { name: "No run" })).toBeInTheDocument();
  });

  it("shows the job count on each group", () => {
    render(
      <Grouped
        jobs={[job({ status: "running" }), job({ sandbox_name: "b", status: "running" })]}
        colorFor={() => "c"}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("2 jobs")).toBeInTheDocument();
  });

  it("marks the selected grouping", () => {
    render(<Grouped jobs={[job()]} colorFor={() => "c"} onSelect={() => {}} />);

    fireEvent.click(screen.getByRole("radio", { name: "Agent" }));

    expect(screen.getByRole("radio", { name: "Agent" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "State" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("heading", { name: "claude" })).toBeInTheDocument();
  });

  it("marks the card that is open in the inspector", () => {
    render(
      <Grouped
        jobs={[job({ sandbox_name: "open-one" }), job({ sandbox_name: "other" })]}
        colorFor={() => "c"}
        selectedName="open-one"
        onSelect={() => {}}
      />,
    );

    expect(screen.getByRole("article", { name: /open-one/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByRole("article", { name: /other/ })).not.toHaveAttribute("aria-current");
  });
});
