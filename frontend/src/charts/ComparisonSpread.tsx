/** The comparison: one horizontal bar chart per metric.
 *
 * Small multiples rather than one chart, because duration, tokens, tool
 * calls and diff size are different units on wildly different scales.
 * Putting them on a shared axis would make them incomparable; putting them
 * on two y-axes would be worse still.
 *
 * A metric the agent never reported renders as "no data", never as a
 * zero-length bar -- a false zero would make an agent look instant, or free.
 *
 * Only one feature is charted at a time: bars for different tasks measure
 * different work, so putting them side by side would compare nothing.
 *
 * Colour is the agent and model; a hatched bar is a run that carried a
 * skill kit. Rows are ordered so each plain / with-skill pair sits together.
 */

import { useId, useState } from "react";
import type { Job } from "../api";
import { endToEndSeconds, totalTokens, usesKit } from "../lib/jobs";
import { assignSeriesColorsFor, colorKey, seriesKey } from "./palette";

interface Metric {
  key: string;
  title: string;
  unit: string;
  value: (job: Job) => number | null;
  format: (value: number) => string;
}

function formatSeconds(v: number): string {
  return v >= 60 ? `${Math.floor(v / 60)}m ${Math.round(v % 60)}s` : `${v.toFixed(1)}s`;
}

function formatCount(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;

  return v >= 1000 ? `${(v / 1000).toFixed(1)}K` : `${v}`;
}

const METRICS: Metric[] = [
  {
    key: "duration",
    title: "Agent time",
    unit: "the agent working, launch to exit; lower is better",
    value: (j) => j.duration_seconds,
    format: formatSeconds,
  },
  {
    key: "end-to-end",
    title: "End to end",
    unit: "sandbox start + agent + verify + push",
    value: endToEndSeconds,
    format: formatSeconds,
  },
  {
    key: "tokens",
    title: "Tokens used",
    unit: "input (incl. prompt cache) + output; claude and codex only",
    value: totalTokens,
    format: formatCount,
  },
  {
    key: "output-tokens",
    title: "Output tokens",
    unit: "tokens the model generated",
    value: (j) => j.output_tokens,
    format: formatCount,
  },
  {
    key: "tool-calls",
    title: "Tool calls",
    unit: "from the agent's telemetry; claude and codex only",
    value: (j) => j.tool_calls,
    format: (v) => `${v}`,
  },
  {
    key: "model-requests",
    title: "Model requests",
    unit: "round trips to the model",
    value: (j) => j.model_requests,
    format: (v) => `${v}`,
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

// Close to the width a card actually renders at (see .chart-grid), so the
// SVG draws near 1:1 and its 11px text stays 11px.
const LABEL_W = 140;
const VALUE_W = 48;
const CHART_W = 320;
const BAR_H = 18;

// At 11px a character averages ~6px; past this a label would run into the
// baseline, so it is cut with an ellipsis and the full text kept in <title>.
const LABEL_MAX_CHARS = 22;

// >= 2px of surface between adjacent bars; the gap is what separates them,
// never a stroke drawn around the mark. Wide enough for a two-line label.
const BAR_GAP = 14;
const TOP_PAD = 6;

/** How a row is named: the model on top, since it is what most often tells
 *  rows apart and a cut-off "claude · claude-sonne…" would hide exactly
 *  that; then the agent and run label; and the whole thing for <title>. */
interface RowLabel {
  primary: string;
  secondary: string | null;
  full: string;
}

interface Row {
  id: string;
  label: RowLabel;
  value: number | null;
  color: string;
  hatched: boolean;
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** Plain runs before kit runs, then by name and launch order, grouped by
 *  colour -- so each pair being compared sits on adjacent rows. */
function orderRows(jobs: Job[]): Job[] {
  return [...jobs].sort(
    (a, b) =>
      colorKey(a).localeCompare(colorKey(b)) ||
      Number(usesKit(a) ?? false) - Number(usesKit(b) ?? false) ||
      seriesKey(a).localeCompare(seriesKey(b)) ||
      a.created_at.localeCompare(b.created_at),
  );
}

/** One label per job. Two runs of the same series get "#1", "#2" in
 *  launch order, so rows never share a label. */
function rowLabels(jobs: Job[]): Map<string, RowLabel> {
  const bySeries = new Map<string, Job[]>();

  for (const job of jobs) {
    const list = bySeries.get(seriesKey(job)) ?? [];
    list.push(job);
    bySeries.set(seriesKey(job), list);
  }

  const labels = new Map<string, RowLabel>();

  for (const [key, list] of bySeries) {
    const ordered = [...list].sort((a, b) => a.created_at.localeCompare(b.created_at));
    ordered.forEach((job, index) => {
      const repeat = list.length > 1 ? ` #${index + 1}` : "";
      const base = colorKey(job);
      const qualifier = key === base ? null : key.slice(base.length + " · ".length);
      const kit = usesKit(job) ? " (skill kit)" : "";
      const primary = job.model ?? job.agent_id;
      const rest = [job.model ? job.agent_id : null, qualifier].filter((part) => part !== null);
      const secondary = rest.length > 0 ? rest.join(" · ") : null;

      labels.set(job.sandbox_name, {
        primary: secondary === null ? primary + repeat : primary,
        secondary: secondary === null ? null : secondary + repeat,
        full: key + repeat + kit,
      });
    });
  }

  return labels;
}

function MetricChart({ metric, rows }: { metric: Metric; rows: Row[] }) {
  const patternBase = useId();
  const max = Math.max(...rows.map((r) => r.value ?? 0), 0);
  const plotW = CHART_W - LABEL_W - VALUE_W;
  const height = TOP_PAD * 2 + rows.length * (BAR_H + BAR_GAP);

  const summary = rows
    .map((r) => `${r.label.full}: ${r.value === null ? "no data" : metric.format(r.value)}`)
    .join("; ");

  return (
    <figure className="chart">
      <h4>{metric.title}</h4>
      <p className="chart__unit">{metric.unit}</p>
      <svg
        viewBox={`0 0 ${CHART_W} ${height}`}
        width="100%"
        // Height follows the width, so a narrower card scales the whole
        // drawing instead of letterboxing it inside a fixed-height box.
        style={{ height: "auto", maxWidth: CHART_W * 1.25 }}
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
          const patternId = `${patternBase}-hatch-${i}`;
          const mid = y + BAR_H / 2;
          const value = row.value === null ? "no data" : metric.format(row.value);

          return (
            <g key={row.id}>
              {/* The hover layer: the whole row, not just the bar, so a
                  short bar is still easy to point at. */}
              <title>{`${row.label.full}: ${value}`}</title>
              <rect x={0} y={y - BAR_GAP / 2} width={CHART_W} height={BAR_H + BAR_GAP} fill="transparent" />
              <text
                x={LABEL_W - 8}
                y={row.label.secondary === null ? mid : mid - 6}
                textAnchor="end"
                dominantBaseline="central"
                fontSize="11"
                fill="var(--text-2)"
              >
                {truncate(row.label.primary, LABEL_MAX_CHARS)}
              </text>
              {row.label.secondary !== null && (
                <text
                  x={LABEL_W - 8}
                  y={mid + 7}
                  textAnchor="end"
                  dominantBaseline="central"
                  fontSize="10"
                  fill="var(--text-3)"
                >
                  {truncate(row.label.secondary, LABEL_MAX_CHARS)}
                </text>
              )}
              {row.value === null ? (
                <text
                  x={LABEL_W + 6}
                  y={mid}
                  dominantBaseline="central"
                  fontSize="11"
                  fill="var(--text-3)"
                  fontStyle="italic"
                >
                  no data
                </text>
              ) : (
                <>
                  {row.hatched && (
                    // 45° texture in the series' own hue: the second
                    // encoding that marks a skill-kit run without a new
                    // colour, and survives greyscale printing.
                    <defs>
                      <pattern
                        id={patternId}
                        patternUnits="userSpaceOnUse"
                        width="6"
                        height="6"
                        patternTransform="rotate(45)"
                      >
                        <rect width="6" height="6" fill={row.color} />
                        <line x1="0" y1="0" x2="0" y2="6" stroke="var(--surface)" strokeWidth="2.5" />
                      </pattern>
                    </defs>
                  )}
                  {/* Square at the baseline, 4px rounded at the data end. */}
                  <path
                    d={roundedBar(LABEL_W, y, Math.max(width, 2), BAR_H)}
                    fill={row.hatched ? `url(#${patternId})` : row.color}
                    stroke={row.hatched ? row.color : undefined}
                    strokeWidth={row.hatched ? 1 : undefined}
                  />
                  {/* Direct label at the tip. Every bar carries one: the
                      light-mode aqua slot sits below 3:1 on this surface, so
                      the relief rule requires visible labels. Text wears a
                      text token, never the series colour. */}
                  <text
                    x={LABEL_W + Math.max(width, 2) + 6}
                    y={mid}
                    dominantBaseline="central"
                    fontSize="11"
                    fill="var(--text)"
                  >
                    {value}
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
  const shown = orderRows(jobs.filter((j) => j.feature_id === feature));
  const color = colorFor ?? assignSeriesColorsFor(jobs);
  const labels = rowLabels(shown);

  const legend = new Map<string, string>();

  for (const job of shown) legend.set(colorKey(job), color(job));

  // The texture key only earns its place when both kinds of run are shown.
  const kitKinds = new Set(shown.map((j) => usesKit(j) ?? false));
  const showTextureKey = kitKinds.size > 1;

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
      {(legend.size > 1 || showTextureKey) && (
        <ul className="legend">
          {[...legend.entries()].map(([key, swatch]) => (
            <li key={key}>
              <span className="swatch" style={{ background: swatch }} aria-hidden="true" />
              {key}
            </li>
          ))}
          {showTextureKey && (
            <>
              <li>
                <span className="swatch swatch--solid-key" aria-hidden="true" />
                no skill kit
              </li>
              <li>
                <span className="swatch swatch--hatched" aria-hidden="true" />
                with skill kit
              </li>
            </>
          )}
        </ul>
      )}
      <div className="chart-grid">
        {METRICS.map((metric) => (
          <MetricChart
            key={metric.key}
            metric={metric}
            rows={shown.map((job) => ({
              id: job.sandbox_name,
              label: labels.get(job.sandbox_name) ?? {
                primary: seriesKey(job),
                secondary: null,
                full: seriesKey(job),
              },
              // `??` also catches a field an older server never sent.
              value: metric.value(job) ?? null,
              color: color(job),
              hatched: usesKit(job) ?? false,
            }))}
          />
        ))}
      </div>
    </div>
  );
}
