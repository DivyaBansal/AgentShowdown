import { render, screen, within } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ComparisonSpread } from "./ComparisonSpread";
import type { Job } from "../api";

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
    files_changed: 2,
    lines_added: 10,
    lines_removed: 5,
    input_tokens: 500,
    output_tokens: 100,
    num_turns: 3,
    repo: null,
    ...overrides,
  };
}

describe("ComparisonSpread", () => {
  it("renders one chart per metric rather than combining scales", () => {
    // Duration, tokens and diff size share no unit; one chart with several
    // scales (or two y-axes) would make them incomparable.
    render(<ComparisonSpread jobs={[job(), job({ agent_id: "codex" })]} />);
    const charts = screen.getAllByRole("img");
    expect(charts).toHaveLength(3);
    expect(charts.map((c) => c.getAttribute("aria-label")).join(" ")).toMatch(
      /duration.*|tokens.*|diff/i,
    );
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
      .find((c) => c.getAttribute("aria-label")?.includes("duration"));
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
});
