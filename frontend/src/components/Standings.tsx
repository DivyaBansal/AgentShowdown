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
import {
  compareForPlacement,
  compareNullableAscending,
  formatDiff,
  formatDuration,
  placements,
  statusTone,
  totalTokens,
} from "../lib/jobs";
import { StatusChip } from "./ui/StatusChip";

type SortKey = "rank" | "agent" | "duration" | "tokens" | "diff";

interface Column {
  key: SortKey;
  label: string;
  numeric: boolean;
}

const COLUMNS: Column[] = [
  { key: "rank", label: "Rank", numeric: false },
  { key: "agent", label: "Agent", numeric: false },
  { key: "duration", label: "Duration", numeric: true },
  { key: "tokens", label: "Tokens", numeric: true },
  { key: "diff", label: "Lines +/−", numeric: true },
];

function diffSize(job: Job): number | null {
  if (job.lines_added === null && job.lines_removed === null) return null;
  return (job.lines_added ?? 0) + (job.lines_removed ?? 0);
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

  const rank = placements(jobs);

  // Nullable columns keep unreported values last in *both* directions:
  // flipping the sort must not float "no data" to the top as if it won.
  const metric: Record<Exclude<SortKey, "rank" | "agent">, (job: Job) => number | null> = {
    duration: (job) => job.duration_seconds,
    tokens: totalTokens,
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
            <th>Status</th>
            <th>Tests</th>
            <th>Lint</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((job) => {
            const place = rank.get(job.sandbox_name) ?? 0;
            const tokens = totalTokens(job);
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
                <td className="is-num num">
                  {formatDuration(job.duration_seconds) ?? <span className="no-data">—</span>}
                </td>
                <td className="is-num num">
                  {tokens === null ? <span className="no-data">—</span> : tokens.toLocaleString()}
                </td>
                <td className="is-num num">
                  {formatDiff(job) ?? <span className="no-data">—</span>}
                </td>
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
