import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ComparisonSpread } from "./ComparisonSpread";
import type { Job } from "../api";

let seq = 0;

function job(overrides: Partial<Job> = {}): Job {
  seq += 1;

  return {
    sandbox_name: `box-${seq}`,
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
    files_changed: 2,
    lines_added: 10,
    lines_removed: 5,
    input_tokens: 500,
    output_tokens: 100,
    num_turns: 3,
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

describe("ComparisonSpread", () => {
  it("renders one chart per metric rather than combining scales", () => {
    // Duration, tokens and diff size share no unit; one chart with several
    // scales (or two y-axes) would make them incomparable.
    render(<ComparisonSpread jobs={[job(), job({ agent_id: "codex" })]} />);
    const charts = screen.getAllByRole("img");
    const titles = charts.map((c) => c.getAttribute("aria-label")?.split(".")[0]);
    expect(titles).toEqual([
      "Agent time",
      "End to end",
      "Tokens used",
      "Output tokens",
      "Tool calls",
      "Model requests",
      "Diff size",
    ]);
  });

  it("includes a legend once there are two or more series", () => {
    // Identity must never rest on colour matching alone.
    render(<ComparisonSpread jobs={[job(), job({ agent_id: "codex" })]} />);
    const legend = screen.getByRole("list");
    expect(within(legend).getByText("claude")).toBeInTheDocument();
    expect(within(legend).getByText("codex")).toBeInTheDocument();
  });

  it("omits the legend for a single series", () => {
    // One colour needs no key; the chart titles already name what is plotted.
    render(<ComparisonSpread jobs={[job()]} />);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("labels an unreported metric 'no data' instead of drawing a zero bar", () => {
    render(
      <ComparisonSpread
        jobs={[job({ agent_id: "cursor", input_tokens: null, output_tokens: null })]}
      />,
    );
    expect(screen.getAllByText("no data").length).toBeGreaterThan(0);
  });

  it("describes each chart for assistive technology", () => {
    render(<ComparisonSpread jobs={[job()]} />);

    const durationChart = screen
      .getAllByRole("img")
      .find((c) => c.getAttribute("aria-label")?.startsWith("Agent time"));

    expect(durationChart?.getAttribute("aria-label")).toContain("claude");
  });

  it("prompts rather than rendering empty axes when nothing has run", () => {
    render(<ComparisonSpread jobs={[]} />);
    expect(screen.getByText(/no finished jobs yet/i)).toBeInTheDocument();
  });

  it("direct-labels every bar", () => {
    // The light-mode aqua slot sits below 3:1 on this surface, so the relief
    // rule requires visible labels rather than colour alone.
    const { container } = render(<ComparisonSpread jobs={[job()]} />);
    const bars = container.querySelectorAll("path");
    expect(bars.length).toBeGreaterThan(0);
    expect(screen.getAllByText("1m 40s").length).toBeGreaterThan(0);
  });

  it("charts only the chosen feature, defaulting to the latest one", () => {
    // Bars for different tasks measure different work.
    render(
      <ComparisonSpread
        jobs={[
          job({ feature_id: "old", agent_id: "aider", created_at: "2026-01-01" }),
          job({ feature_id: "new", agent_id: "claude", created_at: "2026-02-01" }),
        ]}
      />,
    );

    const duration = () =>
      screen.getAllByRole("img").find((c) => c.getAttribute("aria-label")?.startsWith("Agent time"));

    expect(screen.getByLabelText("Feature")).toHaveValue("new");
    expect(duration()?.getAttribute("aria-label")).toContain("claude");
    expect(duration()?.getAttribute("aria-label")).not.toContain("aider");

    fireEvent.change(screen.getByLabelText("Feature"), { target: { value: "old" } });

    expect(duration()?.getAttribute("aria-label")).toContain("aider");
    expect(duration()?.getAttribute("aria-label")).not.toContain("claude");
  });

  it("keys colour by agent and model, and names the run label on the bar", () => {
    render(
      <ComparisonSpread
        jobs={[
          job({ agent_id: "claude", model: "sonnet" }),
          job({ agent_id: "codex", model: "gpt-5", run_label: "luna" }),
        ]}
      />,
    );
    const legend = screen.getByRole("list");

    expect(within(legend).getByText("claude · sonnet")).toBeInTheDocument();
    expect(within(legend).getByText("codex · gpt-5")).toBeInTheDocument();
    // Model on the first line of the bar label, agent and run label below.
    expect(screen.getAllByText("gpt-5", { selector: "text" })).toHaveLength(7);
    expect(screen.getAllByText("codex · luna", { selector: "text" })).toHaveLength(7);
  });

  it("hatches skill-kit runs and keys the texture once both kinds are shown", () => {
    const { container } = render(
      <ComparisonSpread
        jobs={[
          job({ model: "haiku", run_label: "plain", kits: "[]" }),
          job({ model: "haiku", run_label: "with-skill", kits: '["./kits/ui"]' }),
        ]}
      />,
    );

    const legend = screen.getByRole("list");

    expect(within(legend).getByText("with skill kit")).toBeInTheDocument();
    expect(within(legend).getByText("no skill kit")).toBeInTheDocument();
    // One hatch on the with-skill row of each chart it has data for; the
    // fixture reports no agent time, tool calls or requests, which draw
    // "no data" and so no bar.
    expect(container.querySelectorAll("pattern")).toHaveLength(4);
    expect(
      screen
        .getAllByRole("img")[0]
        ?.getAttribute("aria-label"),
    ).toContain("claude · haiku · with-skill (skill kit ui)");
  });

  it("names the kit on a skill-kit run's bar, whatever its run label says", () => {
    render(
      <ComparisonSpread
        jobs={[
          job({ model: "haiku", run_label: null, kits: "[]" }),
          job({
            sandbox_name: "kit-run",
            model: "sonnet",
            run_label: null,
            kits: '["/home/me/kits/frontend-design"]',
          }),
        ]}
      />,
    );

    expect(
      screen.getAllByText("claude · +frontend-design", { selector: "text" }).length,
    ).toBeGreaterThan(0);
  });

  it("drops the run label from the bar before the kit name when short on room", () => {
    render(
      <ComparisonSpread
        jobs={[
          job({ model: "haiku", kits: "[]" }),
          job({
            sandbox_name: "kit-run",
            model: "sonnet",
            run_label: "with-skill",
            kits: '["kits/frontend-design"]',
          }),
        ]}
      />,
    );

    expect(
      screen.getAllByText("claude · +frontend-design", { selector: "text" }).length,
    ).toBeGreaterThan(0);
    // The full name, run label included, is still in the chart's text.
    expect(screen.getAllByRole("img")[0]?.getAttribute("aria-label")).toContain(
      "claude · sonnet · with-skill (skill kit frontend-design)",
    );
  });

  it("leaves out the texture key when every run is plain", () => {
    render(<ComparisonSpread jobs={[job({ kits: "[]" }), job({ agent_id: "codex", kits: "[]" })]} />);

    expect(screen.queryByText("with skill kit")).not.toBeInTheDocument();
  });

  it("puts each plain / with-skill pair on adjacent rows", () => {
    render(
      <ComparisonSpread
        jobs={[
          job({ model: "sonnet", run_label: "with-skill", kits: '["k"]' }),
          job({ model: "haiku", run_label: "with-skill", kits: '["k"]' }),
          job({ model: "sonnet", run_label: "plain", kits: "[]" }),
          job({ model: "haiku", run_label: "plain", kits: "[]" }),
        ]}
      />,
    );

    const order = screen
      .getAllByRole("img")[0]
      ?.getAttribute("aria-label")
      ?.split(". ")[1]
      ?.split("; ")
      .map((entry) => entry.split(":")[0]);

    expect(order).toEqual([
      "claude · haiku · plain",
      "claude · haiku · with-skill (skill kit k)",
      "claude · sonnet · plain",
      "claude · sonnet · with-skill (skill kit k)",
    ]);
  });

  it("charts tool calls, showing no data rather than zero when unreported", () => {
    render(
      <ComparisonSpread
        jobs={[job({ tool_calls: 42 }), job({ agent_id: "cursor", tool_calls: null })]}
      />,
    );

    const toolChart = screen
      .getAllByRole("img")
      .find((c) => c.getAttribute("aria-label")?.startsWith("Tool calls"));

    expect(toolChart?.getAttribute("aria-label")).toBe(
      "Tool calls. claude: 42; cursor: no data",
    );
  });

  it("gives repeat runs of one series their own row instead of stacking them", () => {
    // Rows used to be keyed by label, so two runs collided and drew on top
    // of each other.
    render(
      <ComparisonSpread
        jobs={[
          job({ agent_id: "claude", created_at: "2026-01-01", duration_seconds: 10 }),
          job({ agent_id: "claude", created_at: "2026-01-02", duration_seconds: 20 }),
        ]}
      />,
    );

    expect(screen.getAllByText("claude #1", { selector: "text" })).toHaveLength(7);
    expect(screen.getAllByText("claude #2", { selector: "text" })).toHaveLength(7);
    expect(screen.getByText("10.0s")).toBeInTheDocument();
    expect(screen.getByText("20.0s")).toBeInTheDocument();
  });

  it("keeps the part that tells two models apart visible", () => {
    render(
      <ComparisonSpread
        jobs={[
          job({ model: "claude-sonnet-5" }),
          job({ model: "claude-sonnet-6" }),
        ]}
      />,
    );

    expect(screen.getAllByText("claude-sonnet-5", { selector: "text" })).toHaveLength(7);
    expect(screen.getAllByText("claude-sonnet-6", { selector: "text" })).toHaveLength(7);
  });

  it("shortens a long label but keeps the full text available", () => {
    const { container } = render(
      <ComparisonSpread jobs={[job({ agent_id: "claude", model: "claude-sonnet-4-5-20250929" })]} />,
    );

    expect(container.querySelector("title")?.textContent).toBe(
      "claude · claude-sonnet-4-5-20250929: 1m 40s",
    );
    expect(screen.getAllByText(/^claude-sonnet-4-5-20.*…$/).length).toBe(7);
  });
});
