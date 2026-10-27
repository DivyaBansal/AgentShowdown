import { describe, it, expect } from "vitest";
import {
  OLLAMA_BASE_URL,
  supportsOllamaPreset,
  usesOllama,
  withOllama,
  withoutOllama,
} from "./ollama";

describe("ollama preset", () => {
  it("applies to the agents with an Ollama route", () => {
    expect(supportsOllamaPreset("claude")).toBe(true);
    expect(supportsOllamaPreset("codex")).toBe(true);
    expect(supportsOllamaPreset("cursor")).toBe(false);
  });

  it("points codex at Ollama's OpenAI-compatible API", () => {
    const env = withOllama("codex", { DEBUG: "1" }, "gpt-oss:20b");

    expect(env).toEqual({ DEBUG: "1", CODEX_OSS_BASE_URL: `${OLLAMA_BASE_URL}/v1` });
    expect(usesOllama("codex", env)).toBe(true);
    expect(usesOllama("claude", env)).toBe(false);
    expect(withoutOllama(env)).toEqual({ DEBUG: "1" });
  });

  it("points the agent at the host's Ollama and routes background calls to the model", () => {
    expect(withOllama("claude", {}, "qwen3.5:122b")).toEqual({
      ANTHROPIC_BASE_URL: OLLAMA_BASE_URL,
      ANTHROPIC_AUTH_TOKEN: "ollama",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "qwen3.5:122b",
    });
  });

  it("leaves the background model unset until a model is chosen", () => {
    const env = withOllama("claude", { ANTHROPIC_DEFAULT_HAIKU_MODEL: "old" }, null);

    expect(env).not.toHaveProperty("ANTHROPIC_DEFAULT_HAIKU_MODEL");
    expect(usesOllama("claude", env)).toBe(true);
  });

  it("keeps unrelated variables when switched on and off", () => {
    const on = withOllama("claude", { DEBUG: "1" }, "qwen3.5:122b");

    expect(on.DEBUG).toBe("1");
    expect(withoutOllama(on)).toEqual({ DEBUG: "1" });
  });

  it("doesn't mistake another base URL for Ollama", () => {
    expect(usesOllama("claude", { ANTHROPIC_BASE_URL: "https://gateway.example" })).toBe(false);
    expect(usesOllama("claude", undefined)).toBe(false);
  });
});
