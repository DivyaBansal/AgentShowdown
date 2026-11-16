/** How each contender went about the same task, side by side.
 *
 * Tool names don't compare across agents (claude's `Grep` and `Edit` vs
 * codex doing nearly everything through one shell tool), so the backend
 * sorts every call into a phase -- explore, edit, verify, ... -- by what it
 * did. This shows those phases as one strip per job, on a shared axis, so
 * "read 40 files, then one big edit" and "edit, test, edit, test" look
 * different at a glance.
 *
 * The stats table under the strips is the accessible backstop: every
 * number the strips imply is written out there.
 *
 * One feature at a time, like the head-to-head charts: strips for
 * different tasks would compare nothing.
 */

import { useState } from "react";
import type { Job } from "../api";
import { ApproachStrip } from "../charts/ApproachStrip";
import { assignSeriesColorsFor, seriesKey } from "../charts/palette";
import { ALL_PHASES, PHASE_LABELS, jobLabels, type XMode } from "../lib/approach";
import { useApproaches, type ApproachResult } from "../hooks/useApproaches";
import { canStop, formatDuration } from "../lib/jobs";
import { Notice } from "./ui/Notice";
import { StatusChip } from "./ui/StatusChip";

// Past this, the files table would bury the strips; the rest is counted.
const MAX_FILE_ROWS = 40;

const X_MODES: { value: XMode; label: string; hint: string }[] = [
  { value: "time", label: "Time", hint: "Marks placed by when each call ran." },
  { value: "step", label: "Steps", hint: "Marks placed by call order, ignoring time." },
];

/** The feature to show first: the most recently launched. */
function latestFeature(jobs: Job[]): string {
  return [...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]?.feature_id ?? "";
}

function seconds(value: number | null): string {
  return formatDuration(value) ?? "—";
}

function share(part: number, total: number): string {
  return total === 0 ? `${part}` : `${part} (${Math.round((part / total) * 100)}%)`;
}

interface StatRow {
  label: string;
  hint?: string;
  value: (r: Extract<ApproachResult, { ok: true }>["approach"]) => string;
}

const STAT_ROWS: StatRow[] = [
  { label: "Tool calls", value: (a) => `${a.stats.calls}` },
  { label: "Failed calls", value: (a) => `${a.stats.failures}` },
  {
    label: "Calls before first edit",
    hint: "how much it looked around before changing anything",
    value: (a) => (a.stats.calls_before_first_edit === null ? "never edited" : `${a.stats.calls_before_first_edit}`),
  },
  { label: "Time to first edit", value: (a) => seconds(a.stats.seconds_to_first_edit) },
  { label: "Time to first test, lint or build", value: (a) => seconds(a.stats.seconds_to_first_verify) },
  {
    label: "Edit → verify cycles",
    hint: "times it changed code, then checked it",
    value: (a) => `${a.stats.edit_verify_cycles}`,
  },
  { label: "Files read", value: (a) => `${a.stats.files_read.length}` },
  { label: "Files edited", value: (a) => `${a.stats.files_edited.length}` },
];

function FilesTable({
  jobs,
  labels,
  results,
}: {
  jobs: Job[];
  labels: Map<string, string>;
  results: Map<string, ApproachResult>;
}) {
  const edited = new Map<string, Set<string>>();

  for (const job of jobs) {
    const result = results.get(job.sandbox_name);

    if (!result?.ok) continue;

    for (const path of result.approach.stats.files_edited) {
      // Paths arrive relative to the repo (the backend strips it), so the
      // same file from two agents is the same string.
      edited.set(path, (edited.get(path) ?? new Set()).add(job.sandbox_name));
    }
  }

  if (edited.size === 0) return null;

  // Files more agents touched first: that's where the approaches agree.
  const rows = [...edited.entries()].sort(
    ([a, x], [b, y]) => y.size - x.size || a.localeCompare(b),
  );

  const shown = rows.slice(0, MAX_FILE_ROWS);

  return (
    <details className="approach__files">
      <summary>{`Files edited (${edited.size})`}</summary>
      <div className="scroll-x" role="region" aria-label="Files edited per contender" tabIndex={0}>
        <table className="standings">
          <thead>
            <tr>
              <th scope="col">File</th>
              {jobs.map((job) => (
                <th scope="col" key={job.sandbox_name}>
                  {labels.get(job.sandbox_name)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map(([path, by]) => (
              <tr key={path}>
                <th scope="row" className="mono">
                  {path}
                </th>
                {jobs.map((job) => (
                  <td key={job.sandbox_name}>{by.has(job.sandbox_name) ? "edited" : ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > shown.length && <p className="hint">{`and ${rows.length - shown.length} more`}</p>}
    </details>
  );
}

// Agents whose CLI exports tool calls (the backend's OTEL_AGENTS).
const TELEMETRY_AGENTS = new Set(["claude", "codex"]);

/** Why a contender has no strip: still starting, an agent that exports
 *  nothing, or a run that ended before its first tool call. */
function emptyMessage(job: Job): string {
  if (canStop(job.status)) return "No tool calls yet.";

  if (!TELEMETRY_AGENTS.has(job.agent_id)) return "No tool calls recorded. Only claude and codex export them.";

  return `No tool calls recorded for this run (${job.status}).`;
}

export function ApproachComparison({
  jobs,
  colorFor,
}: {
  jobs: Job[];
  /** Built from the full roster by the caller, so colours match the page. */
  colorFor?: (job: Job) => string;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [mode, setMode] = useState<XMode>("time");

  const features = [...new Set(jobs.map((j) => j.feature_id))].sort((a, b) => a.localeCompare(b));
  const feature = picked !== null && features.includes(picked) ? picked : latestFeature(jobs);

  const shown = jobs
    .filter((j) => j.feature_id === feature)
    .sort((a, b) => seriesKey(a).localeCompare(seriesKey(b)) || a.created_at.localeCompare(b.created_at));

  const live = shown.some((j) => canStop(j.status));

  const results = useApproaches(
    shown.map((j) => j.sandbox_name),
    live,
  );

  const color = colorFor ?? assignSeriesColorsFor(jobs);
  const labels = jobLabels(shown);

  if (jobs.length === 0) {
    return <p className="hint">Strips appear here once agents start calling tools.</p>;
  }

  const loaded = shown
    .map((job) => ({ job, result: results.get(job.sandbox_name) }))
    .filter((e): e is { job: Job; result: Extract<ApproachResult, { ok: true }> } => e.result?.ok === true);

  const withSteps = loaded.filter((e) => e.result.approach.steps.length > 0);
  // Only phases someone used get a row, but every strip gets the same rows.
  const phases = ALL_PHASES.filter((p) => withSteps.some((e) => e.result.approach.stats.phase_calls[p] > 0));

  const xMax =
    mode === "step"
      ? Math.max(1, ...withSteps.map((e) => e.result.approach.steps.length))
      : Math.max(1, ...withSteps.map((e) => e.result.approach.stats.span_s ?? 0));

  return (
    <div className="stack stack--tight approach">
      <div className="toolbar">
        <label htmlFor="approach-feature">Feature</label>
        <select id="approach-feature" value={feature} onChange={(e) => setPicked(e.target.value)}>
          {features.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <div className="actions" role="group" aria-label="Place calls by">
          {X_MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              className="btn--quiet"
              title={option.hint}
              aria-pressed={mode === option.value}
              onClick={() => setMode(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {withSteps.length > 0 && (
        <ul className="legend">
          <li>
            <svg width="8" height="16" viewBox="0 0 8 16" aria-hidden="true">
              <path d="M 1 0 H 7 L 4 4 Z" fill="var(--text)" />
              <rect x="3" y="4" width="2" height="12" fill="var(--text-2)" />
            </svg>
            failed call (taller, with a triangle)
          </li>
          <li>Hover a mark for the command or file.</li>
        </ul>
      )}

      {shown.map((job) => {
        const result = results.get(job.sandbox_name);
        const label = labels.get(job.sandbox_name) ?? job.sandbox_name;
        const steps = result?.ok ? result.approach.steps : [];
        const untimed = mode === "time" ? steps.filter((s) => s.offset_s === null).length : 0;

        return (
          <figure className="chart approach__job" key={job.sandbox_name}>
            <div className="approach__head">
              <span className="swatch" style={{ background: color(job) }} aria-hidden="true" />
              <h4>{label}</h4>
              {canStop(job.status) && <StatusChip tone="active">live</StatusChip>}
            </div>
            {result === undefined && <p className="hint">Loading…</p>}
            {result !== undefined && !result.ok && <Notice tone="danger">{result.error}</Notice>}
            {result?.ok && steps.length === 0 && (
              <p className="hint">
                {emptyMessage(job)}
              </p>
            )}
            {steps.length > 0 && (
              // Below the strip's minimum width it scrolls sideways rather
              // than shrinking its 11px labels into illegibility.
              <div className="scroll-x" role="region" aria-label={`${label} tool calls`} tabIndex={0}>
                <ApproachStrip
                  label={label}
                  steps={steps}
                  phases={phases}
                  color={color(job)}
                  mode={mode}
                  xMax={xMax}
                />
              </div>
            )}
            {untimed > 0 && <p className="hint">{`${untimed} calls came with no time and aren't shown; switch to Steps.`}</p>}
          </figure>
        );
      })}

      {loaded.length > 0 && (
        <div className="scroll-x" role="region" aria-label="Approach stats" tabIndex={0}>
          <table className="standings">
            <caption className="visually-hidden">Approach stats per contender</caption>
            <thead>
              <tr>
                <th scope="col">Measure</th>
                {loaded.map(({ job }) => (
                  <th scope="col" key={job.sandbox_name}>
                    {labels.get(job.sandbox_name)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {STAT_ROWS.map((row) => (
                <tr key={row.label}>
                  <th scope="row">
                    {row.label}
                    {row.hint && <span className="hint approach__hint">{row.hint}</span>}
                  </th>
                  {loaded.map(({ job, result }) => (
                    <td className="mono" key={job.sandbox_name}>
                      {row.value(result.approach)}
                    </td>
                  ))}
                </tr>
              ))}
              {phases.map((phase) => (
                <tr key={phase}>
                  <th scope="row">{`${PHASE_LABELS[phase]} calls`}</th>
                  {loaded.map(({ job, result }) => (
                    <td className="mono" key={job.sandbox_name}>
                      {share(result.approach.stats.phase_calls[phase], result.approach.stats.calls)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <FilesTable jobs={loaded.map((e) => e.job)} labels={labels} results={results} />
    </div>
  );
}
