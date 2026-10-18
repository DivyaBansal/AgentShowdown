import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { JobCard } from "./JobCard";
import type { Job } from "../api";

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
    repo: null,
    log_tail: null,
    ...overrides,
  };
}

describe("JobCard", () => {
  it("shows the agent, status and metrics", () => {
    render(<JobCard job={job()} color="var(--series-1)" />);
    expect(screen.getByLabelText(/arena-f1-claude/i)).toBeInTheDocument();
    expect(screen.getByText("succeeded")).toBeInTheDocument();
    expect(screen.getByText("980")).toBeInTheDocument(); // 900 + 80
  });

  it("renders an unreported metric as 'not reported', never as zero", () => {
    // cursor/opencode/copilot expose no token usage. A zero here would make
    // them look free next to an agent that actually reported its spend.
    render(
      <JobCard
        job={job({ agent_id: "cursor", input_tokens: null, output_tokens: null })}
        color="var(--series-2)"
      />,
    );
    const tokens = screen.getByText("Tokens").nextElementSibling;
    expect(tokens).toHaveTextContent("not reported");
    expect(tokens).not.toHaveTextContent("0");
  });

  it("counts the duration up while the job is running", () => {
    vi.useFakeTimers();

    try {
      vi.setSystemTime(new Date("2026-10-18T12:00:10.000Z"));
      render(
        <JobCard
          job={job({
            status: "running",
            started_at: "2026-10-18T12:00:00.000Z",
            duration_seconds: null,
          })}
          color="var(--series-1)"
        />,
      );
      const duration = screen.getByText("Duration").nextElementSibling;
      expect(duration).toHaveTextContent("10.0s");

      act(() => {
        vi.advanceTimersByTime(5_000);
      });
      expect(duration).toHaveTextContent("15.0s");
    } finally {
      vi.useRealTimers();
    }
  });

  it("distinguishes two runs of the same agent by run label", () => {
    render(<JobCard job={job({ run_label: "b" })} color="var(--series-1)" />);
    expect(screen.getByText(/claude · b/)).toBeInTheDocument();
  });

  it("links to the pull request only when one was opened", () => {
    const { rerender } = render(<JobCard job={job()} color="c" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();

    rerender(<JobCard job={job({ pr_url: "https://example.com/pr/1" })} color="c" />);
    expect(screen.getByRole("link", { name: /pull request/i })).toBeInTheDocument();
  });
});

describe("JobCard partial metrics", () => {
  it("counts a reported half even when the other half is missing", () => {
    // Only one of the two token fields present still means "reported".
    render(
      <JobCard job={job({ input_tokens: 900, output_tokens: null })} color="c" />,
    );
    expect(screen.getByText("Tokens").nextElementSibling).toHaveTextContent("900");
  });

  it("shows a diff with only additions", () => {
    render(
      <JobCard job={job({ lines_added: 12, lines_removed: null })} color="c" />,
    );
    expect(screen.getByText("Diff").nextElementSibling).toHaveTextContent("+12");
  });

  it("marks a missing duration, model and branch as not reported", () => {
    render(
      <JobCard
        job={job({ duration_seconds: null, model: null, branch: null })}
        color="c"
      />,
    );
    expect(screen.getByText("Duration").nextElementSibling).toHaveTextContent(
      "not reported",
    );
    expect(screen.getByText("Model").nextElementSibling).toHaveTextContent(
      "not reported",
    );
  });

  it("formats a sub-minute duration in seconds", () => {
    render(<JobCard job={job({ duration_seconds: 42.25 })} color="c" />);
    expect(screen.getByText("Duration").nextElementSibling).toHaveTextContent("42.3s");
  });

  it("omits the inspect button when no handler is given", () => {
    render(<JobCard job={job()} color="c" />);
    expect(screen.queryByRole("button", { name: /inspect/i })).not.toBeInTheDocument();
  });
});

describe("JobCard actions", () => {
  afterEach(() => vi.restoreAllMocks());

  it("offers Stop for a running job, and calls the stop endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ stopped: "arena-f1-claude" }) }),
    );
    render(<JobCard job={job({ status: "running" })} color="c" />);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/sandboxes/arena-f1-claude/stop",
        expect.anything(),
      ),
    );
  });

  it("offers Respawn for a dead job, and calls the respawn endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "respawning", sandbox_name: "arena-f1-claude" }),
      }),
    );
    render(<JobCard job={job({ status: "crashed" })} color="c" />);

    fireEvent.click(screen.getByRole("button", { name: "Respawn" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/jobs/arena-f1-claude/respawn",
        expect.anything(),
      ),
    );
  });

  it("offers neither Stop nor Respawn for a job that already succeeded", () => {
    render(<JobCard job={job({ status: "succeeded" })} color="c" />);
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Respawn" })).not.toBeInTheDocument();
  });

  it("surfaces a stop failure as an alert", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Server Error",
        json: async () => ({ detail: "sbx unreachable" }),
      }),
    );
    render(<JobCard job={job({ status: "running" })} color="c" />);

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/sbx unreachable/i),
    );
  });
});
