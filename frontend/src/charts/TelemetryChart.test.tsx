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
    render(<TelemetryChart samples={[]} telemetry={false} live />);
    expect(screen.getByText(/telemetry is off/i)).toBeInTheDocument();
    expect(screen.getByText(/sbx exec/)).toBeInTheDocument();
  });

  it("says no samples arrived, not that telemetry is off, when a run with it on ends empty", () => {
    // A sandbox that dies before the first tick leaves no samples, which
    // used to be reported as telemetry being off.
    render(<TelemetryChart samples={[]} telemetry live={false} />);
    expect(screen.queryByText(/telemetry is off/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no samples were recorded/i)).toBeInTheDocument();
  });

  it("judges a job recorded before the setting was by its samples", () => {
    render(<TelemetryChart samples={[sample()]} telemetry={null} live={false} />);
    expect(screen.getAllByRole("img")).toHaveLength(2);
  });

  it("renders CPU and memory as separate charts", () => {
    // Cores and bytes share no scale; a second y-axis would be the wrong fix.
    render(<TelemetryChart samples={[sample(), sample({ cpu_cores: 2 })]} telemetry live />);
    const charts = screen.getAllByRole("img");
    expect(charts).toHaveLength(2);
  });

  it("shows the memory limit as the denominator when one is set", () => {
    render(<TelemetryChart samples={[sample()]} telemetry live />);
    expect(screen.getByText(/limit 2\.0 GiB/)).toBeInTheDocument();
  });

  it("waits rather than plotting when no samples have arrived", () => {
    render(<TelemetryChart samples={[]} telemetry live />);
    expect(screen.getByText(/waiting for the first sample/i)).toBeInTheDocument();
  });

  it("tolerates readings the sandbox could not report", () => {
    render(
      <TelemetryChart
        samples={[sample({ cpu_cores: null, mem_bytes: null, mem_limit_bytes: null })]}
        telemetry
        live
      />,
    );
    expect(screen.getAllByText(/no readings yet/i).length).toBe(2);
  });
});
