import { describe, expect, it } from "vitest";
import type { ConfigValues } from "../api";
import { withoutDraftValues } from "./config";

const DEMO_DRAFT: ConfigValues = {
  github: { repo: "owner/app", pat_secret_name: "github", base_branch: "develop", open_pr: false },
  agent: {
    sbx_agent: "claude",
    model: "claude-haiku-4-5",
    dangerously_skip_permissions: true,
    max_turns: null,
    provider: null,
  },
  run: {
    status_file: ".agent_status.json",
    liveness_interval_seconds: 3,
    poll_interval_seconds: 5,
    timeout_minutes: 45,
    max_concurrency: 2,
    verify_on: "sandbox",
    test_command: "bash tests/test_german_practice.sh",
    lint_command: "ruff check .",
    telemetry: false,
    telemetry_interval_seconds: 5,
    capture_usage: true,
    remove_sandbox_on_success: true,
    remove_sandbox_on_failure: true,
  },
  agents: { "claude-ollama": { model: "qwen", provider: null, dangerously_skip_permissions: null, kit: [], env: {} } },
};

describe("withoutDraftValues", () => {
  it("clears every field that describes the demo rather than this repo", () => {
    const cleared = withoutDraftValues(DEMO_DRAFT);

    expect(cleared.github.repo).toBe("");
    expect(cleared.agent.sbx_agent).toBe("");
    expect(cleared.agent.model).toBe("");
    expect(cleared.run.test_command).toBe("");
    expect(cleared.run.lint_command).toBe("");
    expect(cleared.agents).toEqual({});
  });

  it("keeps generic settings and the plumbing a save must round-trip", () => {
    const cleared = withoutDraftValues(DEMO_DRAFT);

    expect(cleared.github.base_branch).toBe("develop");
    expect(cleared.run.timeout_minutes).toBe(45);
    expect(cleared.run.max_concurrency).toBe(2);
    expect(cleared.run.verify_on).toBe("sandbox");
    expect(cleared.run.status_file).toBe(".agent_status.json");
    expect(cleared.run.liveness_interval_seconds).toBe(3);
  });

  it("does not mutate the draft it was given", () => {
    withoutDraftValues(DEMO_DRAFT);
    expect(DEMO_DRAFT.github.repo).toBe("owner/app");
  });
});
