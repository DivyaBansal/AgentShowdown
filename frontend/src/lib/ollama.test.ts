import { describe, it, expect } from "vitest";
import {
  OLLAMA_BASE_URL,
  supportsOllamaPreset,
  usesOllama,
  withOllama,
  withoutOllama,
} from "./ollama";

describe("ollama preset", () => {
  it("applies only to claude, the agent that reads Anthropic variables", () => {
    expect(supportsOllamaPreset("claude")).toBe(true);
    expect(supportsOllamaPreset("codex")).toBe(false);
  });

  it("points the agent at the host's Ollama and routes background calls to the model", () => {
    expect(withOllama({}, "qwen3.5:122b")).toEqual({
      ANTHROPIC_BASE_URL: OLLAMA_BASE_URL,
      ANTHROPIC_AUTH_TOKEN: "ollama",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "qwen3.5:122b",
    });
  });

  it("leaves the background model unset until a model is chosen", () => {
    const env = withOllama({ ANTHROPIC_DEFAULT_HAIKU_MODEL: "old" }, null);

    expect(env).not.toHaveProperty("ANTHROPIC_DEFAULT_HAIKU_MODEL");
    expect(usesOllama(env)).toBe(true);
  });

  it("keeps unrelated variables when switched on and off", () => {
    const on = withOllama({ DEBUG: "1" }, "qwen3.5:122b");

    expect(on.DEBUG).toBe("1");
    expect(withoutOllama(on)).toEqual({ DEBUG: "1" });
  });

  it("doesn't mistake another base URL for Ollama", () => {
    expect(usesOllama({ ANTHROPIC_BASE_URL: "https://gateway.example" })).toBe(false);
    expect(usesOllama(undefined)).toBe(false);
  });
});
