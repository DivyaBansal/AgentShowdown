/** The comparison: one horizontal bar chart per metric.
 *
 * Small multiples rather than one chart, because duration, tokens and diff
 * size are different units on wildly different scales. Putting them on a
 * shared axis would make them incomparable; putting them on two y-axes
 * would be worse still.
 *
 * A metric the agent never reported renders as "no data", never as a
 * zero-length bar -- a false zero would make an agent look instant, or free.
 *
 * Only one feature is charted at a time: bars for different tasks measure
 * different work, so putting them side by side would compare nothing.
 */

import { useState } from "react";
import type { Job } from "../api";
import { assignSeriesColorsFor, seriesKey } from "./palette";

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

const LABEL_W = 132;
const VALUE_W = 56;
const CHART_W = 360;
const BAR_H = 18;

// At 11px a character averages ~6px; past this a label would run into the
// baseline, so it is cut with an ellipsis and the full text kept in <title>.
const LABEL_MAX_CHARS = 20;

// >= 2px of surface between adjacent bars; the gap is what separates them,
// never a stroke drawn around the mark.
const BAR_GAP = 10;
const TOP_PAD = 6;

interface Row {
  id: string;
  label: string;
  value: number | null;
  color: string;
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** One bar label per job. Two runs of the same series get "#1", "#2" in
 *  launch order, so rows never share a label. */
function rowLabels(jobs: Job[]): Map<string, string> {
  const bySeries = new Map<string, Job[]>();

  for (const job of jobs) {
    const list = bySeries.get(seriesKey(job)) ?? [];
    list.push(job);
    bySeries.set(seriesKey(job), list);
  }

  const labels = new Map<string, string>();

  for (const [key, list] of bySeries) {
    const ordered = [...list].sort((a, b) => a.created_at.localeCompare(b.created_at));
    ordered.forEach((job, index) => {
      labels.set(job.sandbox_name, list.length > 1 ? `${key} #${index + 1}` : key);
    });
  }

  return labels;
}

function MetricChart({
  metric,
  jobs,
  labels,
  colorFor,
}: {
  metric: Metric;
  jobs: Job[];
  labels: Map<string, string>;
  colorFor: (job: Job) => string;
}) {
  // Keyed by sandbox name, never by label: two jobs of one series used to
  // collide on the key, and React drew both rows on top of each other.
  const rows: Row[] = jobs.map((job) => ({
    id: job.sandbox_name,
    label: labels.get(job.sandbox_name) ?? seriesKey(job),
    value: metric.value(job),
    color: colorFor(job),
  }));

  const max = Math.max(...rows.map((r) => r.value ?? 0), 0);
  const plotW = CHART_W - LABEL_W - VALUE_W;
  const height = TOP_PAD * 2 + rows.length * (BAR_H + BAR_GAP);

  const summary = rows
    .map((r) => `${r.label}: ${r.value === null ? "no data" : metric.format(r.value)}`)
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
            <g key={row.id}>
              <text
                x={LABEL_W - 8}
                y={y + BAR_H / 2}
                textAnchor="end"
                dominantBaseline="central"
                fontSize="11"
                fill="var(--text-2)"
              >
                <title>{row.label}</title>
                {truncate(row.label, LABEL_MAX_CHARS)}
              </text>
              {row.value === null ? (
                <text
                  x={LABEL_W + 6}
                  y={y + BAR_H / 2}
                  dominantBaseline="central"
                  fontSize="11"
                  fill="var(--text-3)"
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
                    fill="var(--text)"
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

/** The feature to chart first: the one most recently launched, since that
 *  is almost always the showdown being looked at. */
function latestFeature(jobs: Job[]): string {
  return [...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]?.feature_id ?? "";
}

export function ComparisonSpread({
  jobs,
  colorFor,
}: {
  jobs: Job[];
  /** Built from the full roster by the caller so colours match the rest of
   *  the page; defaults to one built from `jobs`. */
  colorFor?: (job: Job) => string;
}) {
  const [picked, setPicked] = useState<string | null>(null);

  if (jobs.length === 0) {
    return (
      <p className="hint">
        No finished jobs yet. The comparison fills in as agents report back.
      </p>
    );
  }

  const features = [...new Set(jobs.map((j) => j.feature_id))].sort((a, b) => a.localeCompare(b));
  // A pick that has since vanished (e.g. filtered out by repo) falls back
  // rather than rendering an empty chart.
  const feature = picked !== null && features.includes(picked) ? picked : latestFeature(jobs);
  const shown = jobs.filter((j) => j.feature_id === feature);
  const color = colorFor ?? assignSeriesColorsFor(jobs);
  const labels = rowLabels(shown);

  const legend = new Map<string, string>();

  for (const job of shown) legend.set(seriesKey(job), color(job));

  return (
    <div className="stack stack--tight">
      <div className="toolbar">
        <label htmlFor="compare-feature">Feature</label>
        <select
          id="compare-feature"
          value={feature}
          onChange={(e) => setPicked(e.target.value)}
        >
          {features.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </div>
      {/* A legend is always present for two or more series: identity must
          never rest on colour matching alone. */}
      {legend.size > 1 && (
        <ul className="legend">
          {[...legend.entries()].map(([key, swatch]) => (
            <li key={key}>
              <span className="swatch" style={{ background: swatch }} aria-hidden="true" />
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
            jobs={shown}
            labels={labels}
            colorFor={color}
          />
        ))}
      </div>
    </div>
  );
}
