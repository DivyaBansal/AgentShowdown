/** The comparison: one horizontal bar chart per metric.
 *
 * Small multiples rather than one chart, because duration, tokens and diff
 * size are different units on wildly different scales. Putting them on a
 * shared axis would make them incomparable; putting them on two y-axes
 * would be worse still.
 *
 * A metric the agent never reported renders as "no data", never as a
 * zero-length bar -- a false zero would make an agent look instant, or free.
 */

import type { Job } from "../api";
import { assignSeriesColors, seriesKey } from "./palette";

interface Metric {
  key: string;
  title: string;
  unit: string;
  value: (job: Job) => number | null;
  format: (value: number) => string;
}

const METRICS: Metric[] = [
  {
    key: "duration",
    title: "Wall-clock duration",
    unit: "seconds, lower is better",
    value: (j) => j.duration_seconds,
    format: (v) => (v >= 60 ? `${Math.floor(v / 60)}m ${Math.round(v % 60)}s` : `${v.toFixed(1)}s`),
  },
  {
    key: "tokens",
    title: "Tokens used",
    unit: "input + output; only claude and codex report this",
    value: (j) =>
      j.input_tokens === null && j.output_tokens === null
        ? null
        : (j.input_tokens ?? 0) + (j.output_tokens ?? 0),
    format: (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}K` : `${v}`),
  },
  {
    key: "diff",
    title: "Diff size",
    unit: "lines added + removed",
    value: (j) =>
      j.lines_added === null && j.lines_removed === null
        ? null
        : (j.lines_added ?? 0) + (j.lines_removed ?? 0),
    format: (v) => `${v}`,
  },
];

const LABEL_W = 92;
const VALUE_W = 52;
const CHART_W = 320;
const BAR_H = 18;
// >= 2px of surface between adjacent bars; the gap is what separates them,
// never a stroke drawn around the mark.
const BAR_GAP = 10;
const TOP_PAD = 6;

function MetricChart({
  metric,
  jobs,
  colors,
}: {
  metric: Metric;
  jobs: Job[];
  colors: Map<string, string>;
}) {
  const rows = jobs.map((job) => ({
    key: seriesKey(job),
    value: metric.value(job),
    color: colors.get(seriesKey(job)) ?? "var(--de-emphasis)",
  }));
  const max = Math.max(...rows.map((r) => r.value ?? 0), 0);
  const plotW = CHART_W - LABEL_W - VALUE_W;
  const height = TOP_PAD * 2 + rows.length * (BAR_H + BAR_GAP);

  const summary = rows
    .map((r) => `${r.key}: ${r.value === null ? "no data" : metric.format(r.value)}`)
    .join("; ");

  return (
    <figure className="chart">
      <h4>{metric.title}</h4>
      <p className="chart__unit">{metric.unit}</p>
      <svg
        viewBox={`0 0 ${CHART_W} ${height}`}
        width="100%"
        height={height}
        role="img"
        aria-label={`${metric.title}. ${summary}`}
      >
        {/* Recessive baseline: hairline, solid, one step off the surface. */}
        <line
          x1={LABEL_W}
          y1={TOP_PAD}
          x2={LABEL_W}
          y2={height - TOP_PAD}
          stroke="var(--grid)"
          strokeWidth="1"
        />
        {rows.map((row, i) => {
          const y = TOP_PAD + i * (BAR_H + BAR_GAP);
          const width = max > 0 && row.value !== null ? (row.value / max) * plotW : 0;
          return (
            <g key={row.key}>
              <text
                x={LABEL_W - 8}
                y={y + BAR_H / 2}
                textAnchor="end"
                dominantBaseline="central"
                fontSize="11"
                fill="var(--text-secondary)"
              >
                {row.key}
              </text>
              {row.value === null ? (
                <text
                  x={LABEL_W + 6}
                  y={y + BAR_H / 2}
                  dominantBaseline="central"
                  fontSize="11"
                  fill="var(--text-muted)"
                  fontStyle="italic"
                >
                  no data
                </text>
              ) : (
                <>
                  {/* Square at the baseline, 4px rounded at the data end. */}
                  <path
                    d={roundedBar(LABEL_W, y, Math.max(width, 2), BAR_H)}
                    fill={row.color}
                  />
                  {/* Direct label at the tip. Every bar carries one: the
                      light-mode aqua slot sits below 3:1 on this surface, so
                      the relief rule requires visible labels. Text wears a
                      text token, never the series colour. */}
                  <text
                    x={LABEL_W + Math.max(width, 2) + 6}
                    y={y + BAR_H / 2}
                    dominantBaseline="central"
                    fontSize="11"
                    fill="var(--text-primary)"
                  >
                    {metric.format(row.value)}
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

/** A bar squared at the baseline and rounded (4px) at the data end. */
function roundedBar(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w);
  return [
    `M ${x} ${y}`,
    `H ${x + w - r}`,
    `Q ${x + w} ${y} ${x + w} ${y + r}`,
    `V ${y + h - r}`,
    `Q ${x + w} ${y + h} ${x + w - r} ${y + h}`,
    `H ${x}`,
    "Z",
  ].join(" ");
}

export function ComparisonSpread({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) {
    return (
      <p className="hint">
        No finished jobs yet — the comparison fills in as agents report back.
      </p>
    );
  }
  const colors = assignSeriesColors(jobs);

  return (
    <div>
      {/* A legend is always present for two or more series: identity must
          never rest on colour matching alone. */}
      {colors.size > 1 && (
        <ul className="legend">
          {[...colors.entries()].map(([key, color]) => (
            <li key={key}>
              <span className="swatch" style={{ background: color }} aria-hidden="true" />
              {key}
            </li>
          ))}
        </ul>
      )}
      <div className="chart-grid">
        {METRICS.map((metric) => (
          <MetricChart
            key={metric.key}
            metric={metric}
            jobs={jobs}
            colors={colors}
          />
        ))}
      </div>
    </div>
  );
}
