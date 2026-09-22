/** One contender's result, with its series colour as a rail down the side. */

import type { Job } from "../api";
import { seriesKey } from "../charts/palette";
import { formatDiff, formatDuration, statusTone, totalTokens } from "../lib/jobs";
import { StatusChip } from "./ui/StatusChip";

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
      <dd title={value ?? undefined}>
        {value ?? <span className="no-data">not reported</span>}
      </dd>
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
  const tokens = totalTokens(job);

  return (
    <article className="job-card" aria-label={`Job ${job.sandbox_name}`}>
      <span className="job-card__rail" style={{ background: color }} aria-hidden="true" />
      <div className="job-card__head">
        <div>
          <span className="agent-key">{seriesKey(job)}</span>
          <div className="job-card__feature">{job.feature_id}</div>
        </div>
        <StatusChip tone={statusTone(job.status)}>{job.status}</StatusChip>
      </div>
      <dl>
        <Metric label="Model" value={job.model} />
        <Metric label="Duration" value={formatDuration(job.duration_seconds)} />
        <Metric label="Tokens" value={tokens === null ? null : `${tokens}`} />
        <Metric label="Diff" value={formatDiff(job)} />
        <Metric label="Branch" value={job.branch} />
      </dl>
      {(job.pr_url || onSelect) && (
        <div className="job-card__foot">
          {job.pr_url ? <a href={job.pr_url}>View pull request</a> : <span />}
          {onSelect && (
            <button type="button" onClick={() => onSelect(job)}>
              Inspect
            </button>
          )}
        </div>
      )}
    </article>
  );
}
