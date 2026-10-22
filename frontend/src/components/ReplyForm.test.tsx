import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import type { Job } from "../api";
import { fetchInit, jsonBody } from "../test/fetch";
import { ReplyForm } from "./ReplyForm";

afterEach(() => vi.restoreAllMocks());

function job(overrides: Partial<Job> = {}): Job {
  return {
    sandbox_name: "arena-f1-claude",
    feature_id: "f1",
    agent_id: "claude",
    run_label: null,
    branch: "agent/f1/claude",
    status: "awaiting_input",
    pr_url: null,
    detail: "Keep the old API or break it?",
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

function accepted() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ status: "resuming", sandbox_name: "arena-f1-claude" }),
  });

  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;
}

describe("ReplyForm answering a question", () => {
  it("shows the question the agent asked", () => {
    render(<ReplyForm job={job()} />);
    expect(screen.getByText("Keep the old API or break it?")).toBeInTheDocument();
  });

  it("leaves the question out when the caller already shows it", () => {
    render(<ReplyForm job={job()} showQuestion={false} />);
    expect(screen.queryByText("Keep the old API or break it?")).not.toBeInTheDocument();
  });

  it("sends a typed answer and confirms it", async () => {
    const fetchMock = accepted();
    render(<ReplyForm job={job()} />);

    fireEvent.change(screen.getByLabelText(/your answer/i), { target: { value: " break it " } });
    fireEvent.click(screen.getByRole("button", { name: /send answer/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/answer sent/i));
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/jobs/arena-f1-claude/answer");
    expect(jsonBody(fetchInit(fetchMock))).toEqual({ answer: "break it" });
  });

  it("won't send an empty answer", () => {
    render(<ReplyForm job={job()} />);
    fireEvent.change(screen.getByLabelText(/your answer/i), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: /send answer/i })).toBeDisabled();
  });

  it("offers the agent's options as one-click answers, sent by index", async () => {
    const fetchMock = accepted();
    render(<ReplyForm job={job({ question_options: '["Keep the old API", "Break it"]' })} />);

    const group = screen.getByRole("group", { name: /suggested answers/i });
    expect(group).toHaveTextContent("Keep the old API");

    fireEvent.click(screen.getByRole("button", { name: "Break it" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/answer sent/i));
    expect(jsonBody(fetchInit(fetchMock))).toEqual({ option_index: 1 });
  });

  it("still offers free text when the agent gave no options", () => {
    render(<ReplyForm job={job()} />);
    expect(screen.queryByRole("group", { name: /suggested answers/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/your answer/i)).toBeInTheDocument();
  });

  it("surfaces a refused answer as an alert and keeps the form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => ({ detail: "'arena-f1-claude' is already running" }),
      }),
    );
    render(<ReplyForm job={job()} />);

    fireEvent.change(screen.getByLabelText(/your answer/i), { target: { value: "B" } });
    fireEvent.click(screen.getByRole("button", { name: /send answer/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/already running/i));
    expect(screen.getByLabelText(/your answer/i)).toBeInTheDocument();
  });

  it("offers a fresh form when the same job stops to ask again", async () => {
    // "Sent" belongs to one question; a second question needs a new form.
    accepted();
    const { rerender } = render(<ReplyForm job={job()} />);
    fireEvent.change(screen.getByLabelText(/your answer/i), { target: { value: "B" } });
    fireEvent.click(screen.getByRole("button", { name: /send answer/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());

    rerender(<ReplyForm job={job({ status: "running" })} />);
    rerender(<ReplyForm job={job({ detail: "And the tests?" })} />);

    expect(screen.getByText("And the tests?")).toBeInTheDocument();
    expect(screen.getByLabelText(/your answer/i)).toHaveValue("");
  });
});

describe("ReplyForm following up", () => {
  it("sends a follow-up to an idle job", async () => {
    const fetchMock = accepted();
    render(<ReplyForm job={job({ status: "tests_failed", detail: "1 failing" })} />);

    fireEvent.change(screen.getByLabelText(/follow-up instruction/i), {
      target: { value: "fix the failing test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send follow-up/i }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/follow-up sent/i));
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe("/api/jobs/arena-f1-claude/followup");
    expect(jsonBody(fetchInit(fetchMock))).toEqual({ prompt: "fix the failing test" });
  });

  it.each(["running", "queued", "stopped"])("renders nothing for a %s job", (status) => {
    const { container } = render(<ReplyForm job={job({ status })} />);

    // Nothing at all, so this is the documented container-query exception.
    expect(container).toBeEmptyDOMElement();
  });
});
