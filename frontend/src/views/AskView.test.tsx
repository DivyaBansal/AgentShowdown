import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AskProviderInfo, AskResult } from "../api";
import { fetchInitFor, jsonBody } from "../test/fetch";
import { AskView } from "./AskView";

const PROVIDERS: AskProviderInfo[] = [
  {
    provider: "claude",
    label: "Claude Code",
    configured: true,
    hint: "",
    suggested_models: ["claude-sonnet-5-5"],
  },
  { provider: "codex", label: "Codex", configured: true, hint: "", suggested_models: ["gpt-5.6-sol"] },
  { provider: "openrouter", label: "OpenRouter", configured: false, hint: "", suggested_models: [] },
];

function answer(provider: "claude" | "codex", model: string, done: boolean, text = "") {
  return {
    provider,
    model,
    status: done ? ("done" as const) : ("pending" as const),
    text,
    error: null,
    latency_ms: done ? 1500 : null,
    input_tokens: done ? 10 : null,
    output_tokens: done ? 3 : null,
  };
}

const PENDING: AskResult = {
  ask_id: "a1",
  question: "Capital of France?",
  created_at: 0,
  answers: [answer("claude", "claude-sonnet-5-5", false), answer("codex", "gpt-5.6-sol", false)],
};

const DONE: AskResult = {
  ...PENDING,
  answers: [
    answer("claude", "claude-sonnet-5-5", true, "Paris, from Claude."),
    answer("codex", "gpt-5.6-sol", true, "Paris, from Codex."),
  ],
};

type StubBody =
  | AskResult
  | { providers: AskProviderInfo[] }
  | { ask_id: string }
  | { detail: string }
  | Record<string, never>;

function respond(body: StubBody | undefined, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    statusText: "",
    json: async () => body,
  });
}

/** /ask/providers, POST /asks (ids a1, a2, …), then GET /asks/<id> answering
 *  from `polls` in turn, echoing back the question that ask was posted with. */
function stubApi(polls: AskResult[], { postStatus = 202 }: { postStatus?: number } = {}) {
  const questions = new Map<string, string>();
  let poll = 0;

  const mock = vi.fn((url: string, init?: RequestInit) => {
    if (url === "/api/ask/providers") return respond({ providers: PROVIDERS });

    if (url === "/api/asks" && init?.method === "POST") {
      if (postStatus !== 202) {
        return respond({ detail: "Not set up on this machine: openrouter" }, postStatus);
      }

      const id = `a${questions.size + 1}`;
      questions.set(id, String(jsonBody(init).question));

      return respond({ ask_id: id }, 202);
    }

    const id = url.replace("/api/asks/", "");
    const question = questions.get(id);

    if (question !== undefined) {
      const body = polls[Math.min(poll, polls.length - 1)];
      poll += 1;

      return respond({ ...body, ask_id: id, question });
    }

    return respond({}, 404);
  });

  vi.stubGlobal("fetch", mock);

  return mock;
}

describe("AskView", () => {
  it("updates suggestions without replacing the question or selected models", async () => {
    const mock = stubApi([DONE]);
    const { rerender } = render(<AskView />);
    await screen.findByRole("group", { name: "Model 2" });
    fireEvent.change(screen.getByLabelText(/Question/), { target: { value: "Keep this question" } });
    fireEvent.change(screen.getAllByLabelText("Model")[1]!, { target: { value: "manual-model" } });
    mock.mockImplementationOnce(() => respond({ providers: PROVIDERS.map((p) => p.provider === "codex"
      ? { ...p, suggested_models: ["new-model"], model_source: "cli",
          models_updated_at: "2026-10-18T12:00:00Z" }
      : p) }));
    rerender(<AskView modelsRevision={1} />);
    await screen.findByText(/Updated suggestions/);
    expect(screen.getByLabelText(/Question/)).toHaveValue("Keep this question");
    expect(screen.getAllByLabelText("Model")[1]).toHaveValue("manual-model");
    expect(document.querySelector('datalist option[value="new-model"]')).toBeInTheDocument();
  });

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("asks every chosen model and shows their answers side by side", async () => {
    const mock = stubApi([PENDING, DONE]);
    render(<AskView />);

    // Starts with the first two configured providers, ready to ask.
    expect(await screen.findByRole("group", { name: "Model 2" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Question/), {
      target: { value: "  Capital of France?  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(
      await screen.findByRole("article", { name: "Claude Code · claude-sonnet-5-5" }),
    ).toHaveTextContent("Thinking");
    expect(jsonBody(fetchInitFor(mock, "POST"))).toEqual({
      question: "Capital of France?",
      contenders: [
        { provider: "claude", model: "claude-sonnet-5-5" },
        { provider: "codex", model: "gpt-5.6-sol" },
      ],
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });

    const codex = screen.getByRole("article", { name: "Codex · gpt-5.6-sol" });
    expect(within(codex).getByText("Paris, from Codex.")).toBeInTheDocument();
    expect(screen.getByText("Paris, from Claude.")).toBeInTheDocument();

    // Polling stops once every answer is in.
    const calls = mock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(mock.mock.calls.length).toBe(calls);
  });

  it("can't ask an empty question", async () => {
    stubApi([DONE]);
    render(<AskView />);

    await screen.findByRole("group", { name: "Model 1" });

    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Question/), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("shows the backend's reason when an ask is rejected", async () => {
    stubApi([DONE], { postStatus: 422 });
    render(<AskView />);

    await screen.findByRole("group", { name: "Model 1" });
    fireEvent.change(screen.getByLabelText(/Question/), { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Not set up on this machine: openrouter",
    );
  });

  it("adds and removes models", async () => {
    stubApi([DONE]);
    render(<AskView />);

    await screen.findByRole("group", { name: "Model 2" });
    fireEvent.click(screen.getByRole("button", { name: "Add model" }));
    expect(screen.getByRole("group", { name: "Model 3" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove model 3" }));
    expect(screen.queryByRole("group", { name: "Model 3" })).not.toBeInTheDocument();
  });

  it("keeps earlier questions from this session to reopen", async () => {
    stubApi([DONE]);
    render(<AskView />);

    await screen.findByRole("group", { name: "Model 1" });

    for (const q of ["First question", "Second question"]) {
      fireEvent.change(screen.getByLabelText(/Question/), { target: { value: q } });
      fireEvent.click(screen.getByRole("button", { name: "Ask" }));
      await screen.findByRole("blockquote");
    }

    const history = screen.getByRole("navigation", { name: "Earlier questions" });
    fireEvent.click(within(history).getByRole("button", { name: "First question" }));

    expect(screen.getByRole("blockquote")).toHaveTextContent("First question");
  });
});
