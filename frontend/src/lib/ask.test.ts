import { describe, expect, it } from "vitest";
import type { AskProviderInfo } from "../api";
import { defaultModelFor } from "./ask";

function info(provider: AskProviderInfo["provider"], models: string[]): AskProviderInfo {
  return { provider, label: provider, configured: true, hint: "", suggested_models: models };
}

describe("defaultModelFor", () => {
  it("prefers a known-good model when the provider offers it", () => {
    expect(defaultModelFor(info("codex", ["gpt-reserve", "gpt-5.6-sol"]))).toBe("gpt-5.6-sol");
  });

  it("falls back to the first suggestion, or nothing", () => {
    expect(defaultModelFor(info("ollama", ["a", "b"]))).toBe("a");
    expect(defaultModelFor(info("openrouter", []))).toBe("");
  });
});
