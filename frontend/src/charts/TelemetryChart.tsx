/** Live CPU and memory for one sandbox.
 *
 * Two charts, not one: cores and bytes share no scale, and a second y-axis
 * would be the wrong fix. Each is a single series, so neither carries a
 * legend -- the title names what is plotted.
 *
 * Telemetry is opt-in and off by default, because sampling costs one
 * `sbx exec` per sandbox per tick and needs the sandbox kept alive. When it
 * is off this renders an empty state that says so, rather than an
 * indistinguishable blank chart.
 */

import type { Sample } from "../api";

const W = 320;
const H = 72;
const PAD = 6;

interface Series {
  title: string;
  unit: string;
  values: (number | null)[];
  /** Fixed upper bound, when the metric has a real one (a memory limit). */
  max: number | null;
  format: (value: number) => string;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MiB`;
  return `${(bytes / 1024).toFixed(0)} KiB`;
}

function Sparkline({ series }: { series: Series }) {
  const points = series.values
    .map((value, index) => ({ value, index }))
    .filter((p): p is { value: number; index: number } => p.value !== null);

  if (points.length === 0) {
    return (
      <figure className="chart">
        <h4>{series.title}</h4>
        <p className="chart__unit">{series.unit}</p>
        <p className="hint">No readings yet.</p>
      </figure>
    );
  }

  const observed = Math.max(...points.map((p) => p.value));
  const max = series.max ?? Math.max(observed, 1);
  const span = Math.max(series.values.length - 1, 1);
  const x = (index: number) => PAD + (index / span) * (W - PAD * 2);
  const y = (value: number) => H - PAD - (value / max) * (H - PAD * 2);

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.index)} ${y(p.value)}`).join(" ");
  const areaPath = `${path} L ${x(points[points.length - 1]!.index)} ${H - PAD} L ${x(points[0]!.index)} ${H - PAD} Z`;
  const last = points[points.length - 1]!;
  const latest = series.format(last.value);

  return (
    <figure className="chart">
      <h4>{series.title}</h4>
      <p className="chart__unit">
        {series.unit} · now {latest}
      </p>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        role="img"
        aria-label={`${series.title}: currently ${latest}, peak ${series.format(observed)}`}
      >
        <line
          x1={PAD}
          y1={H - PAD}
          x2={W - PAD}
          y2={H - PAD}
          stroke="var(--grid)"
          strokeWidth="1"
        />
        {/* A wash, never a saturated block. */}
        <path d={areaPath} fill="var(--series-1)" fillOpacity="0.1" />
        <path
          d={path}
          fill="none"
          stroke="var(--series-1)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {/* End marker with a surface ring, so it stays legible where it
            crosses the line or the baseline. */}
        <circle
          cx={x(last.index)}
          cy={y(last.value)}
          r="4"
          fill="var(--series-1)"
          stroke="var(--surface-1)"
          strokeWidth="2"
        />
      </svg>
    </figure>
  );
}

export function TelemetryChart({
  samples,
  enabled,
}: {
  samples: Sample[];
  enabled: boolean;
}) {
  if (!enabled) {
    return (
      <p className="hint">
        Telemetry is off for this run. Enabling it costs one <code>sbx exec</code>{" "}
        per sandbox per tick and keeps the sandbox alive, so it is a debugging
        choice rather than the default.
      </p>
    );
  }
  if (samples.length === 0) {
    return <p className="hint">Waiting for the first sample…</p>;
  }

  const memLimit = samples.map((s) => s.mem_limit_bytes).filter((v): v is number => v !== null).pop() ?? null;

  return (
    <div className="chart-grid">
      <Sparkline
        series={{
          title: "CPU",
          unit: "cores in use",
          values: samples.map((s) => s.cpu_cores),
          max: null,
          format: (v) => `${v.toFixed(2)} cores`,
        }}
      />
      <Sparkline
        series={{
          title: "Memory",
          unit: memLimit ? `resident, limit ${formatBytes(memLimit)}` : "resident",
          values: samples.map((s) => s.mem_bytes),
          max: memLimit,
          format: formatBytes,
        }}
      />
    </div>
  );
}
