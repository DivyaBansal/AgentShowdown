/** One job's tool calls as a piano roll: a row per phase, a mark per call.
 *
 * Phase is carried by *position* (which row), not by colour. Calls of any
 * phase sit next to each other along the strip, so telling eight phases
 * apart by hue would need eight colours that stay distinguishable in every
 * pairing, which no palette manages. Rows need no colour at all, so the
 * mark keeps the job's own series colour and matches the rest of the page.
 *
 * Failure is shown by shape, not hue: a failed mark is drawn taller than
 * its row with a small neutral triangle above it. A red mark would sit too
 * close to the orange series slot to tell apart.
 *
 * Every strip in a comparison is given the same x domain (`xMax`), so a
 * job that took twice as long draws twice as wide.
 */

import type { ApproachStep, Phase } from "../api";
import { PHASE_LABELS, describeStep, tickLabel, type XMode } from "../lib/approach";

const LABEL_W = 64;

// Near the width a full-width card renders at, so the SVG draws at about
// 1:1 and its 11px text stays 11px; narrower screens scale it down.
const PLOT_W = 820;
const RIGHT_PAD = 8;
const ROW_H = 14;
const ROW_GAP = 4;
const AXIS_H = 18;
const MIN_MARK_W = 2;

// Pointing at a 2px mark is hard; each one gets a wider invisible target.
const MIN_HIT_W = 6;

// How far a failed mark sticks out past its row, top and bottom, and the
// triangle that caps it.
const FAIL_OVERHANG = 3;
const CAP_H = 4;
const CAP_W = 6;

interface Mark {
  step: ApproachStep;
  x: number;
  width: number;
}

/** Where each call sits on the x axis. In time mode a call spans from when
 *  it started to when it finished (its event is sent on completion); calls
 *  that came with no time are left out, and counted by the caller. */
function layout(steps: ApproachStep[], mode: XMode, xMax: number): Mark[] {
  const scale = PLOT_W / Math.max(xMax, 1);

  if (mode === "step") {
    const band = scale;

    return steps.map((step) => ({
      step,
      x: LABEL_W + step.index * band,
      // A 1px surface gap between neighbours when there's room for one.
      width: Math.max(band > 4 ? band - 1 : band, 1),
    }));
  }

  return steps.flatMap((step) => {
    if (step.offset_s === null) return [];
    const end = step.offset_s;
    const start = Math.max(0, end - (step.duration_ms ?? 0) / 1000);

    return [{ step, x: LABEL_W + start * scale, width: Math.max((end - start) * scale, MIN_MARK_W) }];
  });
}

/** A small downward triangle whose tip touches (`cx`, `tipY`). */
function failCap(cx: number, tipY: number): string {
  return `M ${cx - CAP_W / 2} ${tipY - CAP_H} H ${cx + CAP_W / 2} L ${cx} ${tipY} Z`;
}

export function ApproachStrip({
  label,
  steps,
  phases,
  color,
  mode,
  xMax,
}: {
  /** Names the job, for the accessible summary. */
  label: string;
  steps: ApproachStep[];
  /** Rows to draw, in order: the same for every strip being compared. */
  phases: Phase[];
  color: string;
  mode: XMode;
  /** The x domain's end, seconds or steps, shared across the comparison. */
  xMax: number;
}) {
  const marks = layout(steps, mode, xMax);
  const rowY = new Map(phases.map((phase, i) => [phase, i * (ROW_H + ROW_GAP)]));
  const plotH = phases.length * (ROW_H + ROW_GAP) - ROW_GAP;
  const top = FAIL_OVERHANG + CAP_H;
  const height = top + plotH + FAIL_OVERHANG + AXIS_H;
  const width = LABEL_W + PLOT_W + RIGHT_PAD;

  const counts = phases
    .map((phase) => `${PHASE_LABELS[phase]} ${steps.filter((s) => s.phase === phase).length}`)
    .join(", ");

  const failed = steps.filter((s) => s.ok === false).length;
  const summary = `${label}: ${steps.length} tool calls (${counts})${failed ? `, ${failed} failed` : ""}.`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * Math.max(xMax, 1));

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      style={{ height: "auto", maxWidth: width }}
      role="img"
      aria-label={summary}
      className="approach-strip"
    >
      {phases.map((phase) => {
        const y = top + (rowY.get(phase) ?? 0);

        return (
          <g key={phase}>
            {/* The lane: a recessive band, so an empty phase still reads as
                "did none of this" rather than missing. */}
            <rect x={LABEL_W} y={y} width={PLOT_W} height={ROW_H} fill="var(--surface-2)" rx="2" />
            <text
              x={LABEL_W - 8}
              y={y + ROW_H / 2}
              textAnchor="end"
              dominantBaseline="central"
              fontSize="11"
              fill="var(--text-2)"
            >
              {PHASE_LABELS[phase]}
            </text>
          </g>
        );
      })}

      {ticks.map((value) => {
        const x = LABEL_W + (value / Math.max(xMax, 1)) * PLOT_W;

        return (
          <g key={value}>
            <line x1={x} x2={x} y1={top + plotH} y2={top + plotH + 4} stroke="var(--grid)" />
            <text
              x={x}
              y={top + plotH + 13}
              textAnchor={value === 0 ? "start" : value === ticks[ticks.length - 1] ? "end" : "middle"}
              fontSize="10"
              fill="var(--text-3)"
            >
              {tickLabel(value, mode)}
            </text>
          </g>
        );
      })}

      {marks.map(({ step, x, width: w }) => {
        const y = top + (rowY.get(step.phase) ?? 0);
        const failedStep = step.ok === false;
        const hitW = Math.max(w, MIN_HIT_W);

        return (
          <g key={step.index} className="approach-strip__mark">
            <title>{describeStep(step)}</title>
            <rect x={x + w / 2 - hitW / 2} y={y} width={hitW} height={ROW_H} fill="transparent" />
            <rect
              x={x}
              y={failedStep ? y - FAIL_OVERHANG : y + 2}
              width={w}
              height={failedStep ? ROW_H + FAIL_OVERHANG * 2 : ROW_H - 4}
              rx={Math.min(1, w / 2)}
              fill={color}
            />
            {failedStep && <path d={failCap(x + w / 2, y - FAIL_OVERHANG)} fill="var(--text)" />}
          </g>
        );
      })}
    </svg>
  );
}
