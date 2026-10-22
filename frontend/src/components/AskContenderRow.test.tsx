import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AskProviderInfo } from "../api";
import { AskContenderRow } from "./AskContenderRow";

const PROVIDERS: AskProviderInfo[] = [
  {
    provider: "claude",
    label: "Claude Code",
    configured: true,
    hint: "Uses your Claude subscription login.",
    suggested_models: ["claude-haiku-5-5", "claude-sonnet-5-5"],
  },
  {
    provider: "codex",
    label: "Codex",
    configured: true,
    hint: "Uses your ChatGPT login.",
    suggested_models: ["gpt-reserve", "gpt-5.6-sol"],
  },
  {
    provider: "openrouter",
    label: "OpenRouter",
    configured: false,
    hint: "Set OPENROUTER_API_KEY.",
    suggested_models: [],
  },
];

function renderRow(onChange = vi.fn(), onRemove = vi.fn()) {
  render(
    <AskContenderRow
      contender={{ provider: "claude", model: "claude-sonnet-5-5" }}
      index={0}
      providers={PROVIDERS}
      canRemove={false}
      onChange={onChange}
      onRemove={onRemove}
    />,
  );

  return { onChange, onRemove };
}

describe("AskContenderRow", () => {
  it("switching provider resets the model to that provider's default", () => {
    const { onChange } = renderRow();

    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "codex" } });

    expect(onChange).toHaveBeenCalledWith({ provider: "codex", model: "gpt-5.6-sol" });
  });

  it("lists an unconfigured provider as disabled and shows the provider hint", () => {
    renderRow();

    expect(screen.getByRole("option", { name: "OpenRouter (not set up)" })).toBeDisabled();
    expect(screen.getByText("Uses your Claude subscription login.")).toBeInTheDocument();
  });

  it("edits the model name freely", () => {
    const { onChange } = renderRow();

    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });

    expect(onChange).toHaveBeenCalledWith({ provider: "claude", model: "opus" });
  });

  it("can't remove the only model", () => {
    renderRow();

    expect(screen.getByRole("button", { name: "Remove model 1" })).toBeDisabled();
  });
});
