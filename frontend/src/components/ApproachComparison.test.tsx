import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Approach, ApproachStep, Job, Phase } from "../api";
import { ApproachComparison } from "./ApproachComparison";

afterEach(() => vi.unstubAllGlobals());

function job(overrides: Partial<Job>): Job {
  return {
    sandbox_name: "box",
    feature_id: "f1",
    agent_id: "claude",
    run_label: null,
    branch: null,
    status: "succeeded",
    pr_url: null,
    detail: null,
    created_at: "2026-10-22T10:00:00Z",
    updated_at: "",
    run_id: null,
    model: null,
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
    ...overrides,
  };
}

const PHASES: Phase[] = ["explore", "edit", "verify", "setup", "git", "web", "delegate", "other"];

function perPhase(value: (phase: Phase) => number): Record<Phase, number> {
  return {
    explore: value("explore"),
    edit: value("edit"),
    verify: value("verify"),
    setup: value("setup"),
    git: value("git"),
    web: value("web"),
    delegate: value("delegate"),
    other: value("other"),
  };
}

function approach(phases: Phase[], edited: string[], overrides: Partial<Approach["stats"]> = {}): Approach {
  const steps: ApproachStep[] = phases.map((phase, index) => ({
    index,
    offset_s: (index + 1) * 5,
    phase,
    tool: phase === "edit" ? "Edit" : "Bash",
    target: null,
    ok: true,
    duration_ms: null,
  }));

  return {
    phases: PHASES,
    steps,
    stats: {
      calls: steps.length,
      failures: 0,
      phase_calls: perPhase((p) => phases.filter((q) => q === p).length),
      phase_ms: perPhase(() => 0),
      calls_before_first_edit: phases.indexOf("edit") === -1 ? null : phases.indexOf("edit"),
      seconds_to_first_edit: null,
      seconds_to_first_verify: null,
      edit_verify_cycles: 0,
      files_read: [],
      files_edited: edited,
      span_s: steps.length * 5,
      ...overrides,
    },
  };
}

function stubApproaches(byName: Record<string, Approach | null>) {
  const fetchMock = vi.fn((url: string) => {
    const name = decodeURIComponent(url.split("/")[3] ?? "");
    const body = byName[name];

    return Promise.resolve({
      ok: body !== null && body !== undefined,
      status: body ? 200 : 500,
      statusText: "Server Error",
      json: async () => body ?? { detail: "boom" },
    });
  });

  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;
}

const CLAUDE = job({ sandbox_name: "arena-f1-claude", agent_id: "claude" });
const CODEX = job({ sandbox_name: "arena-f1-codex", agent_id: "codex" });

describe("ApproachComparison", () => {
  it("draws one strip per contender on the chosen feature", async () => {
    stubApproaches({
      "arena-f1-claude": approach(["explore", "explore", "explore", "edit", "verify"], ["/workspace/app/src/a.py"]),
      "arena-f1-codex": approach(["edit", "verify", "edit", "verify"], ["src/a.py", "src/b.py"], { edit_verify_cycles: 2 }),
    });

    render(<ApproachComparison jobs={[CLAUDE, CODEX]} />);

    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(screen.getByRole("img", { name: /^claude: 5 tool calls \(Explore 3, Edit 1, Verify 1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^codex: 4 tool calls/ })).toBeInTheDocument();
  });

  it("writes every number out in a table, so nothing rests on the strips alone", async () => {
    stubApproaches({
      "arena-f1-claude": approach(["explore", "explore", "explore", "edit", "verify"], []),
      "arena-f1-codex": approach(["edit", "verify", "edit", "verify"], [], { edit_verify_cycles: 2 }),
    });

    render(<ApproachComparison jobs={[CLAUDE, CODEX]} />);

    const table = within(await screen.findByRole("region", { name: "Approach stats" })).getByRole("table");
    const beforeEdit = within(table).getByRole("row", { name: /calls before first edit/i });
    const cycles = within(table).getByRole("row", { name: /edit → verify cycles/i });
    const explore = within(table).getByRole("row", { name: /explore calls/i });

    expect(within(beforeEdit).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3", "0"]);
    expect(within(cycles).getAllByRole("cell").map((c) => c.textContent)).toEqual(["0", "2"]);
    expect(within(explore).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3 (60%)", "0 (0%)"]);
    // Phases nobody used get no row.
    expect(within(table).queryByRole("row", { name: /web calls/i })).not.toBeInTheDocument();
  });

  it("lines up the files both agents edited", async () => {
    stubApproaches({
      "arena-f1-claude": approach(["edit"], ["src/a.py"]),
      "arena-f1-codex": approach(["edit", "edit"], ["src/a.py", "src/b.py"]),
    });

    render(<ApproachComparison jobs={[CLAUDE, CODEX]} />);

    const files = within(await screen.findByRole("region", { name: /files edited per contender/i }));
    const shared = files.getByRole("row", { name: /src\/a\.py/ });

    expect(within(shared).getAllByRole("cell").map((c) => c.textContent)).toEqual(["edited", "edited"]);
    expect(within(files.getByRole("row", { name: /src\/b\.py/ })).getAllByRole("cell").map((c) => c.textContent)).toEqual(["", "edited"]);
  });

  it("switches between placing calls by time and by order", async () => {
    stubApproaches({ "arena-f1-claude": approach(["explore", "edit"], []) });

    render(<ApproachComparison jobs={[CLAUDE]} />);
    await screen.findByRole("img");

    const time = screen.getByRole("button", { name: "Time" });
    const steps = screen.getByRole("button", { name: "Steps" });

    expect(time).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(steps);
    expect(steps).toHaveAttribute("aria-pressed", "true");
    expect(time).toHaveAttribute("aria-pressed", "false");
  });

  it("explains an agent that exports no telemetry instead of drawing nothing", async () => {
    stubApproaches({ "arena-f1-opencode": approach([], []) });

    render(<ApproachComparison jobs={[job({ sandbox_name: "arena-f1-opencode", agent_id: "opencode" })]} />);

    expect(await screen.findByText(/only claude and codex export them/i)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("shows a failed fetch for one contender without hiding the others", async () => {
    stubApproaches({ "arena-f1-claude": approach(["explore"], []), "arena-f1-codex": null });

    render(<ApproachComparison jobs={[CLAUDE, CODEX]} />);

    expect(await screen.findByRole("img", { name: /^claude/ })).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });

  it("only compares jobs from the picked feature", async () => {
    const other = job({ sandbox_name: "arena-f2-claude", feature_id: "f2", created_at: "2026-10-21T00:00:00Z" });

    const fetchMock = stubApproaches({
      "arena-f1-claude": approach(["explore"], []),
      "arena-f2-claude": approach(["edit", "edit"], []),
    });

    render(<ApproachComparison jobs={[CLAUDE, other]} />);
    await screen.findByRole("img", { name: /1 tool calls/ });

    fireEvent.change(screen.getByLabelText("Feature"), { target: { value: "f2" } });

    expect(await screen.findByRole("img", { name: /2 tool calls/ })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("arena-f2-claude"), expect.anything());
  });
});
