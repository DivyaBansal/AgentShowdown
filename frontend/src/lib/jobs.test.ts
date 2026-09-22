import { describe, expect, it } from "vitest";
import type { Job } from "../api";
import {
  compareNullableAscending,
  formatDiff,
  formatDuration,
  placements,
  statusTone,
  totalTokens,
} from "./jobs";

function job(overrides: Partial<Job> = {}): Job {
  return {
    sandbox_name: "box",
    feature_id: "f1",
    agent_id: "claude",
    run_label: null,
    branch: null,
    status: "succeeded",
    pr_url: null,
    detail: null,
    created_at: "",
    updated_at: "",
    run_id: null,
    model: null,
    started_at: null,
    finished_at: null,
    duration_seconds: 100,
    tests_passed: 1,
    lint_passed: null,
    files_changed: null,
    lines_added: null,
    lines_removed: null,
    input_tokens: null,
    output_tokens: null,
    num_turns: null,
    repo: null,
    ...overrides,
  };
}

describe("placements", () => {
  it("ranks a finished run above a faster one that failed", () => {
    const rank = placements([
      job({ sandbox_name: "fast-fail", status: "tests_failed", duration_seconds: 10 }),
      job({ sandbox_name: "slow-pass", status: "succeeded", duration_seconds: 300 }),
    ]);
    expect(rank.get("slow-pass")).toBe(1);
    expect(rank.get("fast-fail")).toBe(2);
  });

  it("breaks a tie between two finished runs on passing tests, then time", () => {
    const rank = placements([
      job({ sandbox_name: "no-tests", tests_passed: 0, duration_seconds: 10 }),
      job({ sandbox_name: "slow", tests_passed: 1, duration_seconds: 200 }),
      job({ sandbox_name: "quick", tests_passed: 1, duration_seconds: 50 }),
    ]);
    expect([rank.get("quick"), rank.get("slow"), rank.get("no-tests")]).toEqual([1, 2, 3]);
  });

  it("restarts numbering per feature, because different tasks are not a match", () => {
    const rank = placements([
      job({ sandbox_name: "a1", feature_id: "a" }),
      job({ sandbox_name: "b1", feature_id: "b" }),
    ]);
    expect(rank.get("a1")).toBe(1);
    expect(rank.get("b1")).toBe(1);
  });

  it("never treats a missing duration as instant", () => {
    const rank = placements([
      job({ sandbox_name: "unknown", duration_seconds: null }),
      job({ sandbox_name: "timed", duration_seconds: 500 }),
    ]);
    expect(rank.get("timed")).toBe(1);
    expect(rank.get("unknown")).toBe(2);
  });
});

describe("compareNullableAscending", () => {
  it("orders values ascending with nulls last, and two nulls as equal", () => {
    const values = [3, null, 1, null, 2].sort(compareNullableAscending);
    expect(values).toEqual([1, 2, 3, null, null]);
    expect(compareNullableAscending(null, null)).toBe(0);
  });
});

describe("formatters", () => {
  it("keeps a missing metric distinct from zero", () => {
    expect(totalTokens(job())).toBeNull();
    expect(totalTokens(job({ input_tokens: 0, output_tokens: 0 }))).toBe(0);
    expect(formatDiff(job())).toBeNull();
    expect(formatDuration(null)).toBeNull();
  });

  it("formats durations and diffs for reading", () => {
    expect(formatDuration(42.25)).toBe("42.3s");
    expect(formatDuration(93.5)).toBe("1m 34s");
    expect(formatDiff(job({ lines_added: 12, lines_removed: null }))).toBe("+12 / −0");
  });
});

describe("statusTone", () => {
  it("maps each status family to a tone", () => {
    expect(statusTone("succeeded")).toBe("ok");
    expect(statusTone("awaiting_input")).toBe("warn");
    expect(statusTone("running")).toBe("active");
    expect(statusTone("timed_out")).toBe("danger");
    expect(statusTone("something-new")).toBe("neutral");
  });
});
