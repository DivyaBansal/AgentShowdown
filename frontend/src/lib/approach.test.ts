import { describe, expect, it } from "vitest";
import type { ApproachStep } from "../api";
import { describeStep, jobLabels, tickLabel } from "./approach";

describe("tickLabel", () => {
  it("reads seconds as m:ss and steps as plain counts", () => {
    expect(tickLabel(65, "time")).toBe("1:05");
    expect(tickLabel(0, "time")).toBe("0:00");
    expect(tickLabel(40.4, "step")).toBe("40");
  });
});

describe("describeStep", () => {
  it("names the phase, tool, target, timing and failure", () => {
    const step: ApproachStep = {
      index: 4,
      offset_s: 75,
      phase: "verify",
      tool: "exec_command",
      target: "pytest -q",
      ok: false,
      duration_ms: 2500,
    };

    expect(describeStep(step)).toBe("Verify · exec_command\npytest -q\ncall #5, at 1:15, took 2.5s, failed");
  });

  it("leaves out what wasn't reported", () => {
    const step: ApproachStep = {
      index: 0,
      offset_s: null,
      phase: "other",
      tool: "TodoWrite",
      target: null,
      ok: null,
      duration_ms: null,
    };

    expect(describeStep(step)).toBe("Other · TodoWrite\ncall #1");
  });
});

describe("jobLabels", () => {
  const job = (sandbox_name: string, created_at: string, agent_id = "claude") => ({
    sandbox_name,
    created_at,
    agent_id,
    run_label: null,
    model: null,
  });

  it("numbers repeat runs of one series in launch order", () => {
    const labels = jobLabels([job("b", "2"), job("a", "1"), job("c", "3", "codex")]);

    expect(labels.get("a")).toBe("claude #1");
    expect(labels.get("b")).toBe("claude #2");
    expect(labels.get("c")).toBe("codex");
  });
});
