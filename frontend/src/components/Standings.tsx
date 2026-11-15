/** Every job as one ranked, sortable table.
 *
 * Placement is within a feature: two agents on the same task are a match,
 * two different tasks are not, so the rank restarts per feature. The table
 * is also the accessibility backstop for the charts -- every value a chart
 * draws is here as text.
 */

import { useState } from "react";
import type { Job } from "../api";
import { seriesKey } from "../charts/palette";
import { useNow } from "../hooks/useNow";
import {
  compareForPlacement,
  canStop,
  compareNullableAscending,
  elapsedSeconds,
  endToEndSeconds,
  formatDiff,
  formatDuration,
  formatTimestamp,
  jobKits,
  kitName,
  placements,
  statusTone,
  totalTokens,
} from "../lib/jobs";
import { StatusChip } from "./ui/StatusChip";

type SortKey =
  | "rank"
  | "agent"
  | "started"
  | "duration"
  | "endToEnd"
  | "tokens"
  | "toolCalls"
  | "requests"
  | "diff";

interface Column {
  key: SortKey;
  label: string;
  numeric: boolean;
}

const COLUMNS: Column[] = [
  { key: "rank", label: "Rank", numeric: false },
  { key: "agent", label: "Agent", numeric: false },
  { key: "started", label: "Started", numeric: false },
  { key: "duration", label: "Agent time", numeric: true },
  { key: "endToEnd", label: "End to end", numeric: true },
  { key: "tokens", label: "Tokens", numeric: true },
  { key: "toolCalls", label: "Tool calls", numeric: true },
  { key: "requests", label: "Requests", numeric: true },
  { key: "diff", label: "Lines +/−", numeric: true },
];

function diffSize(job: Job): number | null {
  if (job.lines_added === null && job.lines_removed === null) return null;

  return (job.lines_added ?? 0) + (job.lines_removed ?? 0);
}

function startedMs(job: Job): number | null {
  if (job.started_at === null) return null;

  const ms = Date.parse(job.started_at);

  return Number.isNaN(ms) ? null : ms;
}

const NO_DATA = <span className="no-data">—</span>;

/** The kits a job carried, by short name; "none" is a known absence, a
 *  dash is a row recorded before kits were. */
function kitsCell(job: Job) {
  const kits = jobKits(job);

  if (kits === null) return NO_DATA;

  if (kits.length === 0) return <span className="hint">none</span>;

  return <span title={kits.join("\n")}>{kits.map(kitName).join(", ")}</span>;
}

/** Total tokens, with the input / cached / output split on hover. */
function tokensCell(job: Job) {
  const total = totalTokens(job);

  if (total === null) return NO_DATA;

  const parts = [
    `input ${(job.input_tokens ?? 0).toLocaleString()}`,
    job.cached_input_tokens === null
      ? null
      : `of which cached ${job.cached_input_tokens.toLocaleString()}`,
    `output ${(job.output_tokens ?? 0).toLocaleString()}`,
  ].filter((part): part is string => part !== null);

  return <span title={parts.join("\n")}>{total.toLocaleString()}</span>;
}

function countCell(value: number | null, failures: number | null = null) {
  if (value === null) return NO_DATA;

  return failures ? (
    <span title={`${failures} failed`}>
      {value} <span className="hint">({failures} failed)</span>
    </span>
  ) : (
    value
  );
}

function verdict(value: number | null) {
  if (value === null) return <span className="no-data">—</span>;

  return value > 0 ? (
    <span className="verdict--pass">pass</span>
  ) : (
    <span className="verdict--fail">fail</span>
  );
}

export function Standings({ jobs, colorFor }: { jobs: Job[]; colorFor: (job: Job) => string }) {
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [descending, setDescending] = useState(false);
  const now = useNow(jobs.some((job) => canStop(job.status)));

  const rank = placements(jobs);

  // Nullable columns keep unreported values last in *both* directions:
  // flipping the sort must not float "no data" to the top as if it won.
  const metric: Record<Exclude<SortKey, "rank" | "agent">, (job: Job) => number | null> = {
    started: startedMs,
    duration: (job) => elapsedSeconds(job, now),
    endToEnd: endToEndSeconds,
    tokens: totalTokens,
    toolCalls: (job) => job.tool_calls,
    requests: (job) => job.model_requests,
    diff: diffSize,
  };

  const rows = [...jobs].sort((a, b) => {
    const flip = descending ? -1 : 1;

    if (sortKey === "rank") {
      return flip * (a.feature_id.localeCompare(b.feature_id) || compareForPlacement(a, b));
    }

    if (sortKey === "agent") {
      return flip * seriesKey(a).localeCompare(seriesKey(b));
    }

    const va = metric[sortKey](a);
    const vb = metric[sortKey](b);

    if (va === null || vb === null) return compareNullableAscending(va, vb);

    return flip * (va - vb);
  });

  function sortBy(key: SortKey) {
    if (key === sortKey) {
      setDescending((d) => !d);
    } else {
      setSortKey(key);
      setDescending(false);
    }
  }

  return (
    <div className="scroll-x">
      <table className="standings">
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th
                key={column.key}
                className={column.numeric ? "is-num" : undefined}
                aria-sort={
                  sortKey === column.key ? (descending ? "descending" : "ascending") : undefined
                }
              >
                <button type="button" className="sort" onClick={() => sortBy(column.key)}>
                  {column.label}
                  <span className="sort__arrow" aria-hidden="true">
                    {sortKey === column.key ? (descending ? "▼" : "▲") : ""}
                  </span>
                </button>
              </th>
            ))}
            <th>Skill kit</th>
            <th>Status</th>
            <th>Tests</th>
            <th>Lint</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((job) => {
            const place = rank.get(job.sandbox_name) ?? 0;

            return (
              <tr key={job.sandbox_name}>
                <td className={place === 1 ? "rank rank--first" : "rank"}>
                  {place}
                  <div className="feature-tag">{job.feature_id}</div>
                </td>
                <td>
                  <span className="agent-key">
                    <span
                      className="swatch"
                      style={{ background: colorFor(job) }}
                      aria-hidden="true"
                    />
                    {seriesKey(job)}
                  </span>
                </td>
                <td className="num">{formatTimestamp(job.started_at) ?? NO_DATA}</td>
                <td className="is-num num">
                  {formatDuration(elapsedSeconds(job, now)) ?? NO_DATA}
                </td>
                <td className="is-num num">{formatDuration(endToEndSeconds(job)) ?? NO_DATA}</td>
                <td className="is-num num">{tokensCell(job)}</td>
                <td className="is-num num">{countCell(job.tool_calls, job.tool_failures)}</td>
                <td className="is-num num">{countCell(job.model_requests)}</td>
                <td className="is-num num">{formatDiff(job) ?? NO_DATA}</td>
                <td>{kitsCell(job)}</td>
                <td>
                  <StatusChip tone={statusTone(job.status)}>{job.status}</StatusChip>
                </td>
                <td>{verdict(job.tests_passed)}</td>
                <td>{verdict(job.lint_passed)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
