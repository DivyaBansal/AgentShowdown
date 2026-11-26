import { describe, it, expect } from "vitest";
import {
  assignSeriesColors,
  assignSeriesColorsFor,
  colorKey,
  seriesKey,
  SERIES_VARS,
  OVERFLOW_VAR,
} from "./palette";
import type { Job } from "../api";

function job(agent_id: string, run_label: string | null = null, model: string | null = null): Job {
  return {
    sandbox_name: `box-${agent_id}-${run_label ?? ""}`,
    feature_id: "f1",
    agent_id,
    run_label,
    branch: null,
    status: "succeeded",
    pr_url: null,
    detail: null,
    created_at: "",
    updated_at: "",
    run_id: null,
    model,
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
    repo: null,
    log_tail: null,
    launch_s: null,
    agent_s: null,
    verify_s: null,
    push_s: null,
    failure_category: null,
    question_options: null,
  };
}

describe("seriesKey", () => {
  it("treats the same agent under two run labels as two series", () => {
    expect(seriesKey(job("claude", "a"))).not.toBe(seriesKey(job("claude", "b")));
  });

  it("uses the bare agent id when there is no run label or model", () => {
    expect(seriesKey(job("claude"))).toBe("claude");
  });

  it("qualifies the agent with its model when there is no run label", () => {
    expect(seriesKey(job("claude", null, "sonnet"))).toBe("claude · sonnet");
  });

  it("names both the model and the user's run label", () => {
    expect(seriesKey(job("claude", "with-skill", "claude-haiku-5-5"))).toBe(
      "claude · claude-haiku-5-5 · with-skill",
    );
  });

  it("does not repeat a run label that was only derived from the model", () => {
    expect(seriesKey(job("codex", "qwen3.5-122b", "qwen3.5:122b"))).toBe("codex · qwen3.5:122b");
  });
});

describe("colorKey", () => {
  it("gives two run labels of one agent and model the same colour", () => {
    const colorFor = assignSeriesColorsFor([
      job("claude", "plain", "haiku"),
      job("claude", "with-skill", "haiku"),
      job("claude", "plain", "sonnet"),
    ]);

    expect(colorFor(job("claude", "plain", "haiku"))).toBe(
      colorFor(job("claude", "with-skill", "haiku")),
    );
    expect(colorFor(job("claude", "plain", "haiku"))).not.toBe(
      colorFor(job("claude", "plain", "sonnet")),
    );
  });

  it("ignores the run label", () => {
    expect(colorKey(job("codex", "luna", "gpt-5"))).toBe("codex · gpt-5");
  });
});

describe("assignSeriesColors", () => {
  it("gives each series a distinct palette slot", () => {
    const colors = assignSeriesColors([job("claude"), job("codex")]);
    expect(new Set(colors.values()).size).toBe(2);
    expect([...colors.values()]).toEqual(
      expect.arrayContaining([SERIES_VARS[0], SERIES_VARS[1]]),
    );
  });

  it("keeps a colour attached to its agent when the view is filtered", () => {
    // Colour must follow the entity, never its rank: narrowing what is
    // *shown* cannot repaint the survivors. The lookup is closed over the
    // full roster, so there is no per-view recomputation to get wrong.
    const roster = [job("claude"), job("codex")];
    const colorFor = assignSeriesColorsFor(roster);
    const before = colorFor(job("codex"));

    const shown = roster.filter((j) => j.agent_id === "codex");
    expect(shown.map(colorFor)).toEqual([before]);
  });

  it("rebuilding from a filtered subset is what it must not do", () => {
    // Documents the trap directly: this is why the roster-closed lookup
    // exists rather than calling assignSeriesColors per view.
    const all = assignSeriesColors([job("claude"), job("codex")]);
    const rebuiltFromSubset = assignSeriesColors([job("codex")]);
    expect(rebuiltFromSubset.get("codex")).not.toBe(all.get("codex"));
  });

  it("does not depend on the order jobs arrive in", () => {
    const a = assignSeriesColors([job("claude"), job("codex")]);
    const b = assignSeriesColors([job("codex"), job("claude")]);
    expect(a.get("claude")).toBe(b.get("claude"));
  });

  it("folds series past the cap into one de-emphasis colour", () => {
    // Never invent a new hue -- a generated one is indistinguishable under
    // colour-vision deficiency.
    const colors = assignSeriesColors([
      job("a"),
      job("b"),
      job("c"),
      job("d"),
      job("e"),
    ]);

    expect(colors.get("d")).toBe(OVERFLOW_VAR);
    expect(colors.get("e")).toBe(OVERFLOW_VAR);
  });
});
