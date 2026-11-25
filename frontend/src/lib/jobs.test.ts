import { describe, expect, it } from "vitest";
import type { Job } from "../api";
import {
  canFollowUp,
  canRespawn,
  canStop,
  compareNullableAscending,
  elapsedSeconds,
  endToEndSeconds,
  formatDiff,
  formatDuration,
  formatTimestamp,
  groupJobs,
  runStartedAt,
  jobKits,
  kitName,
  latestActivity,
  questionOptions,
  stateBucket,
  statusTone,
  totalTokens,
  usesKit,
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
    cached_input_tokens: null,
    tool_calls: null,
    tool_failures: null,
    model_requests: null,
    kits: null,
    telemetry: null,
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

describe("formatTimestamp", () => {
  it("formats a timestamp in the viewer's locale", () => {
    const iso = "2026-10-26T09:30:00+00:00";

    const expected = new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });

    expect(formatTimestamp(iso)).toBe(expected);
  });

  it("is null when there is no usable timestamp", () => {
    expect(formatTimestamp(null)).toBeNull();
    expect(formatTimestamp("not a date")).toBeNull();
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

  it("gives a deliberately stopped job a neutral tone, not a failure one", () => {
    expect(statusTone("stopped")).toBe("neutral");
  });
});

describe("canStop", () => {
  it("allows stopping only a job that's actually doing something", () => {
    expect(canStop("running")).toBe(true);
    expect(canStop("queued")).toBe(true);
    expect(canStop("stopped")).toBe(false);
    expect(canStop("succeeded")).toBe(false);
    expect(canStop("crashed")).toBe(false);
  });
});

describe("canRespawn", () => {
  it("allows respawning a stopped or dead job, not a live or finished one", () => {
    expect(canRespawn("stopped")).toBe(true);
    expect(canRespawn("crashed")).toBe(true);
    expect(canRespawn("timed_out")).toBe(true);
    expect(canRespawn("running")).toBe(false);
    expect(canRespawn("queued")).toBe(false);
    expect(canRespawn("succeeded")).toBe(false);
  });

  it("excludes awaiting_input -- that needs an answer, not a bare restart", () => {
    expect(canRespawn("awaiting_input")).toBe(false);
  });
});

describe("elapsedSeconds", () => {
  const start = "2026-10-18T12:00:00.000Z";
  const startMs = Date.parse(start);

  it("times an in-flight job live from started_at", () => {
    const running = job({ status: "running", started_at: start, duration_seconds: null });
    expect(elapsedSeconds(running, startMs + 5_000)).toBe(5);
    expect(elapsedSeconds(running, startMs + 65_000)).toBe(65);
  });

  it("ignores a stale recorded duration while running", () => {
    const respawned = job({ status: "running", started_at: start, duration_seconds: 42 });
    expect(elapsedSeconds(respawned, startMs + 3_000)).toBe(3);
  });

  it("uses the recorded duration once finished", () => {
    const done = job({ status: "succeeded", started_at: start, duration_seconds: 100 });
    expect(elapsedSeconds(done, startMs + 999_000)).toBe(100);
  });

  it("falls back to the recorded duration when started_at is missing", () => {
    expect(elapsedSeconds(job({ status: "queued", duration_seconds: null }), startMs)).toBeNull();
  });
});

describe("stateBucket", () => {
  it("maps each status to its bucket", () => {
    expect(stateBucket("awaiting_input")).toBe("needs_input");
    expect(stateBucket("running")).toBe("working");
    expect(stateBucket("queued")).toBe("working");
    expect(stateBucket("stopped")).toBe("stopped");
    expect(stateBucket("succeeded")).toBe("ready_for_review");
    expect(stateBucket("crashed")).toBe("failed");
    expect(stateBucket("tests_failed")).toBe("failed");
  });

  it("treats an unrecognized status as needing a look, not as silently fine", () => {
    expect(stateBucket("something-new")).toBe("failed");
  });
});

describe("groupJobs", () => {
  it("groups by state in urgency order, omitting empty buckets", () => {
    const groups = groupJobs(
      [
        job({ sandbox_name: "a", status: "succeeded" }),
        job({ sandbox_name: "b", status: "awaiting_input" }),
        job({ sandbox_name: "c", status: "running" }),
      ],
      "state",
    );

    expect(groups.map((g) => g.key)).toEqual(["needs_input", "working", "ready_for_review"]);
    expect(groups.map((g) => g.jobs.map((j) => j.sandbox_name))).toEqual([["b"], ["c"], ["a"]]);
  });

  it("groups by feature, sorted alphabetically", () => {
    const groups = groupJobs(
      [
        job({ sandbox_name: "a", feature_id: "zebra" }),
        job({ sandbox_name: "b", feature_id: "apple" }),
      ],
      "feature",
    );

    expect(groups.map((g) => g.key)).toEqual(["apple", "zebra"]);
  });

  it("groups by agent", () => {
    const groups = groupJobs(
      [
        job({ sandbox_name: "a", agent_id: "codex" }),
        job({ sandbox_name: "b", agent_id: "claude" }),
      ],
      "agent",
    );

    expect(groups.map((g) => g.key)).toEqual(["claude", "codex"]);
  });

  it("groups jobs with no run under a labelled catch-all", () => {
    const groups = groupJobs([job({ sandbox_name: "a", run_id: null })], "run");

    expect(groups).toHaveLength(1);
    expect(groups[0]?.label).toBe("No run");
  });

  it("puts the latest run first, not the alphabetically first id", () => {
    // Run ids are random hex, so sorting by id scrambled the history.
    const groups = groupJobs(
      [
        job({ sandbox_name: "old", run_id: "run-aaa", started_at: "2026-10-01T09:00:00Z" }),
        job({ sandbox_name: "new", run_id: "run-zzz", started_at: "2026-11-01T09:00:00Z" }),
        job({ sandbox_name: "loose", run_id: null, started_at: "2026-12-01T09:00:00Z" }),
        job({
          sandbox_name: "unstarted",
          run_id: "run-mmm",
          started_at: null,
          created_at: "2026-10-15T09:00:00Z",
        }),
      ],
      "run",
    );

    expect(groups.map((g) => g.key)).toEqual(["run-zzz", "run-mmm", "run-aaa", "(none)"]);
  });

  it("dates a run by its first job to start", () => {
    expect(
      runStartedAt([
        job({ started_at: "2026-11-25T21:19:05Z" }),
        job({ started_at: "2026-11-25T20:47:07Z" }),
        job({ started_at: null }),
      ]),
    ).toBe("2026-11-25T20:47:07Z");
    expect(runStartedAt([job({ started_at: null })])).toBeNull();
  });

  it("heads a run's group with the feature it ran", () => {
    const groups = groupJobs(
      [job({ sandbox_name: "a", run_id: "run-1a2b", feature_id: "build-ui" })],
      "run",
    );

    expect(groups[0]?.label).toBe("build-ui · run-1a2b");
  });

  it("never returns a group with no jobs in it", () => {
    const groups = groupJobs([job({ status: "succeeded" })], "state");
    expect(groups.every((g) => g.jobs.length > 0)).toBe(true);
  });
});

describe("latestActivity", () => {
  it("prefers the agent's own status message", () => {
    expect(latestActivity(job({ detail: "which option?", log_tail: "irrelevant" }))).toBe(
      "which option?",
    );
  });

  it("falls back to the last non-empty line of the live tail", () => {
    expect(
      latestActivity(job({ detail: null, log_tail: "building...\nrunning tests\n\n" })),
    ).toBe("running tests");
  });

  it("returns null when there is nothing to show yet", () => {
    expect(latestActivity(job({ detail: null, log_tail: null }))).toBeNull();
    expect(latestActivity(job({ detail: null, log_tail: "\n\n  \n" }))).toBeNull();
  });
});

describe("canFollowUp", () => {
  it("allows a follow-up once the agent is idle and not waiting on an answer", () => {
    expect(canFollowUp("succeeded")).toBe(true);
    expect(canFollowUp("tests_failed")).toBe(true);
    expect(canFollowUp("stuck")).toBe(true);
  });

  it("refuses while it's running, stopped, or waiting for an answer", () => {
    expect(canFollowUp("running")).toBe(false);
    expect(canFollowUp("queued")).toBe(false);
    expect(canFollowUp("stopped")).toBe(false);
    expect(canFollowUp("awaiting_input")).toBe(false);
  });
});

describe("questionOptions", () => {
  it("decodes the options the agent offered", () => {
    expect(questionOptions(job({ question_options: '["A", "B"]' }))).toEqual(["A", "B"]);
  });

  it("has none when the agent offered none", () => {
    expect(questionOptions(job({ question_options: null }))).toEqual([]);
  });

  it("yields none rather than crashing on a malformed value", () => {
    expect(questionOptions(job({ question_options: "not json" }))).toEqual([]);
    expect(questionOptions(job({ question_options: '{"a": 1}' }))).toEqual([]);
  });

  it("drops anything that isn't a string", () => {
    expect(questionOptions(job({ question_options: '["A", 2, null, "B"]' }))).toEqual(["A", "B"]);
  });
});

describe("endToEndSeconds", () => {
  it("adds sandbox start, verify and push to the agent's own time", () => {
    expect(endToEndSeconds({ launch_s: 10, agent_s: 50, verify_s: 1, push_s: 4 })).toBe(65);
  });

  it("is unknown until the agent phase has a time", () => {
    expect(endToEndSeconds({ launch_s: 10, agent_s: null, verify_s: null, push_s: null })).toBe(
      null,
    );
  });
});

describe("jobKits", () => {
  it("decodes the kit list, and treats a missing or malformed one as unknown", () => {
    expect(jobKits({ kits: '["./kits/ui"]' })).toEqual(["./kits/ui"]);
    expect(jobKits({ kits: "[]" })).toEqual([]);
    expect(jobKits({ kits: null })).toBeNull();
    expect(jobKits({ kits: "{oops" })).toBeNull();
  });

  it("tells a kit run from a plain one, or says it doesn't know", () => {
    expect(usesKit({ kits: '["k"]' })).toBe(true);
    expect(usesKit({ kits: "[]" })).toBe(false);
    expect(usesKit({ kits: null })).toBeNull();
    expect(kitName("/home/x/demo/kits/frontend-design")).toBe("frontend-design");
  });
});
