import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AskProviderInfo, AskResult } from "../api";
import { AnswerGrid } from "./AnswerGrid";

const PROVIDERS: AskProviderInfo[] = [
  { provider: "claude", label: "Claude Code", configured: true, hint: "", suggested_models: [] },
  { provider: "ollama", label: "Ollama (local)", configured: true, hint: "", suggested_models: [] },
];

const RESULT: AskResult = {
  ask_id: "a1",
  question: "Capital of France?",
  created_at: 0,
  answers: [
    {
      provider: "claude",
      model: "claude-sonnet-5-5",
      status: "done",
      text: "Paris.\nIt has been since 987.",
      error: null,
      latency_ms: 2900,
      input_tokens: 7210,
      output_tokens: 12,
    },
    {
      provider: "ollama",
      model: "qwen2.5-coder:32b",
      status: "error",
      text: "",
      error: "HTTP 404: model not found",
      latency_ms: 40,
      input_tokens: null,
      output_tokens: null,
    },
  ],
};

describe("AnswerGrid", () => {
  it("shows one card per contender with its answer and stats", () => {
    render(<AnswerGrid result={RESULT} providers={PROVIDERS} />);

    const claude = screen.getByRole("article", { name: "Claude Code · claude-sonnet-5-5" });
    expect(within(claude).getByText(/Paris\./)).toBeInTheDocument();
    expect(within(claude).getByText("2.9s")).toBeInTheDocument();
    expect(within(claude).getByText((7210).toLocaleString())).toBeInTheDocument();
    expect(within(claude).getByText("Answered")).toBeInTheDocument();
    expect(within(claude).getByRole("button", { name: "Copy answer" })).toBeInTheDocument();
  });

  it("shows a failure as an alert and unreported tokens as a dash, not zero", () => {
    render(<AnswerGrid result={RESULT} providers={PROVIDERS} />);

    const ollama = screen.getByRole("article", { name: "Ollama (local) · qwen2.5-coder:32b" });
    expect(within(ollama).getByRole("alert")).toHaveTextContent("HTTP 404: model not found");
    expect(within(ollama).getAllByText("—")).toHaveLength(2);
    expect(within(ollama).queryByText("0")).not.toBeInTheDocument();
  });
});
