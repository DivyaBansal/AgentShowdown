/** One job, as an index card pinned to the page. */

import type { Job } from "../api";
import { seriesKey } from "../charts/palette";

function formatDuration(seconds: number | null): string | null {
  if (seconds === null) return null;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/** Renders a metric, or an explicit "not reported".
 *
 * A missing value must never render as 0: cursor, opencode and copilot
 * expose no token usage at all, and showing zero would make them look free
 * next to an agent that actually reported its spend.
 */
function Metric({ label, value }: { label: string; value: string | null }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value ?? <span className="no-data">not reported</span>}</dd>
    </>
  );
}

export function JobCard({
  job,
  color,
  onSelect,
}: {
  job: Job;
  color: string;
  onSelect?: (job: Job) => void;
}) {
  const tokens =
    job.input_tokens === null && job.output_tokens === null
      ? null
      : `${(job.input_tokens ?? 0) + (job.output_tokens ?? 0)}`;
  const diff =
    job.lines_added === null && job.lines_removed === null
      ? null
      : `+${job.lines_added ?? 0} / −${job.lines_removed ?? 0}`;

  return (
    <article className="card card--pinned job-card" aria-label={`Job ${job.sandbox_name}`}>
      <div className="job-card__head">
        <span className="agent-key">
          <span className="swatch" style={{ background: color }} aria-hidden="true" />
          {seriesKey(job)}
        </span>
        <span className={`status status--${job.status}`}>{job.status}</span>
      </div>
      <dl>
        <Metric label="Model" value={job.model} />
        <Metric label="Duration" value={formatDuration(job.duration_seconds)} />
        <Metric label="Tokens" value={tokens} />
        <Metric label="Diff" value={diff} />
        <Metric label="Branch" value={job.branch} />
      </dl>
      {job.pr_url && (
        <p>
          <a href={job.pr_url}>View pull request</a>
        </p>
      )}
      {onSelect && (
        <p>
          <button type="button" onClick={() => onSelect(job)}>
            Inspect
          </button>
        </p>
      )}
    </article>
  );
}
