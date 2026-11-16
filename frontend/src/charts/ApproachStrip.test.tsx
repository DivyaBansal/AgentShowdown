import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ApproachStep } from "../api";
import { ApproachStrip } from "./ApproachStrip";

function step(overrides: Partial<ApproachStep>): ApproachStep {
  return {
    index: 0,
    offset_s: 1,
    phase: "explore",
    tool: "Grep",
    target: null,
    ok: true,
    duration_ms: null,
    ...overrides,
  };
}

const STEPS = [
  step({ index: 0, offset_s: 2, phase: "explore", tool: "Grep", target: "separable" }),
  step({ index: 1, offset_s: 10, phase: "edit", tool: "Edit", target: "src/verbs.py" }),
  step({ index: 2, offset_s: 20, phase: "verify", tool: "Bash", target: "pytest -q", ok: false, duration_ms: 4000 }),
];

describe("ApproachStrip", () => {
  it("summarizes the calls per phase for screen readers", () => {
    render(
      <ApproachStrip label="claude" steps={STEPS} phases={["explore", "edit", "verify"]} color="red" mode="time" xMax={20} />,
    );

    expect(screen.getByRole("img")).toHaveAccessibleName(
      "claude: 3 tool calls (Explore 1, Edit 1, Verify 1), 1 failed.",
    );
  });

  it("labels one lane per phase it is given, in order", () => {
    const { container } = render(
      <ApproachStrip label="codex" steps={STEPS} phases={["explore", "edit", "verify", "git"]} color="red" mode="time" xMax={20} />,
    );

    // Lane names are capitalized; axis ticks are numbers.
    const lanes = [...container.querySelectorAll("text")].flatMap((t) =>
      t.textContent !== null && /^[A-Z]/.test(t.textContent) ? [t.textContent] : [],
    );

    // A lane with no calls still shows, so "did none of this" is visible.
    expect(lanes).toEqual(["Explore", "Edit", "Verify", "Git"]);
  });

  it("tells each call's tool, target and outcome on hover", () => {
    const { container } = render(
      <ApproachStrip label="claude" steps={STEPS} phases={["explore", "edit", "verify"]} color="red" mode="time" xMax={20} />,
    );

    const titles = [...container.querySelectorAll("title")].map((t) => t.textContent);

    expect(titles).toHaveLength(3);
    expect(titles[2]).toContain("Verify · Bash");
    expect(titles[2]).toContain("pytest -q");
    expect(titles[2]).toContain("failed");
  });

  it("leaves out calls with no time in time mode but keeps them in step mode", () => {
    const steps = [...STEPS, step({ index: 3, offset_s: null, phase: "edit", tool: "Write" })];

    const timed = render(
      <ApproachStrip label="a" steps={steps} phases={["explore", "edit", "verify"]} color="red" mode="time" xMax={20} />,
    );

    expect(timed.container.querySelectorAll("title")).toHaveLength(3);
    timed.unmount();

    const stepped = render(
      <ApproachStrip label="a" steps={steps} phases={["explore", "edit", "verify"]} color="red" mode="step" xMax={4} />,
    );

    expect(stepped.container.querySelectorAll("title")).toHaveLength(4);
  });
});
