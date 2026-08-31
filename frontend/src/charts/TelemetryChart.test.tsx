import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { TelemetryChart } from "./TelemetryChart";
import type { Sample } from "../api";

function sample(overrides: Partial<Sample> = {}): Sample {
  return {
    sandbox_name: "box-1",
    ts: "2026-08-31T00:00:00Z",
    cpu_cores: 1.5,
    mem_bytes: 278622208,
    mem_limit_bytes: 2147483648,
    pids: 59,
    ...overrides,
  };
}

describe("TelemetryChart", () => {
  it("explains the cost when telemetry is off rather than showing a blank chart", () => {
    render(<TelemetryChart samples={[]} enabled={false} />);
    expect(screen.getByText(/telemetry is off/i)).toBeInTheDocument();
    expect(screen.getByText(/sbx exec/)).toBeInTheDocument();
  });

  it("renders CPU and memory as separate charts", () => {
    // Cores and bytes share no scale; a second y-axis would be the wrong fix.
    render(<TelemetryChart samples={[sample(), sample({ cpu_cores: 2 })]} enabled />);
    const charts = screen.getAllByRole("img");
    expect(charts).toHaveLength(2);
  });

  it("shows the memory limit as the denominator when one is set", () => {
    render(<TelemetryChart samples={[sample()]} enabled />);
    expect(screen.getByText(/limit 2\.0 GiB/)).toBeInTheDocument();
  });

  it("waits rather than plotting when no samples have arrived", () => {
    render(<TelemetryChart samples={[]} enabled />);
    expect(screen.getByText(/waiting for the first sample/i)).toBeInTheDocument();
  });

  it("tolerates readings the sandbox could not report", () => {
    render(
      <TelemetryChart
        samples={[sample({ cpu_cores: null, mem_bytes: null, mem_limit_bytes: null })]}
        enabled
      />,
    );
    expect(screen.getAllByText(/no readings yet/i).length).toBe(2);
  });
});
