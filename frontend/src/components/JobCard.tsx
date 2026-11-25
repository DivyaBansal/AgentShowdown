/** One contender's result, with its series colour as a rail down the side. */

import { useState } from "react";
import { ApiError, respawnJob, stopSandbox, type Job } from "../api";
import { seriesKey } from "../charts/palette";
import { useNow } from "../hooks/useNow";
import {
  canRespawn,
  canStop,
  elapsedSeconds,
  formatDiff,
  formatDuration,
  formatTimestamp,
  latestActivity,
  statusTone,
  totalTokens,
} from "../lib/jobs";
import { DeleteJobButton } from "./DeleteJobButton";
import { ReplyForm } from "./ReplyForm";
import { Notice } from "./ui/Notice";
import { StatusChip } from "./ui/StatusChip";

/** Renders a metric, or an explicit "not reported".
 *
 * A missing value must never render as 0: cursor, opencode and copilot
 * expose no token usage at all, and showing zero would make them look free
 * next to an agent that actually reported its spend.
 */
function Metric({
  label,
  value,
  missing = "not reported",
}: {
  label: string;
  value: string | null;
  missing?: string;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd title={value ?? undefined}>
        {value ?? <span className="no-data">{missing}</span>}
      </dd>
    </>
  );
}

export function JobCard({
  job,
  color,
  selected = false,
  onSelect,
}: {
  job: Job;
  color: string;
  /** Whether this job is the one open in the inspector. */
  selected?: boolean;
  onSelect?: (job: Job) => void;
}) {
  const tokens = totalTokens(job);
  const activity = latestActivity(job);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const showStop = canStop(job.status);
  const showRespawn = canRespawn(job.status);
  // Finished one way or another; the server still refuses if its sandbox
  // is around, and says so.
  const showDelete = !showStop;
  const now = useNow(showStop);

  async function handleStop() {
    setError(null);
    setPending(true);

    try {
      await stopSandbox(job.sandbox_name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not stop the job");
    } finally {
      setPending(false);
    }
  }

  async function handleRespawn() {
    setError(null);
    setPending(true);

    try {
      await respawnJob(job.sandbox_name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not respawn the job");
    } finally {
      setPending(false);
    }
  }

  return (
    <article
      className={selected ? "job-card job-card--selected" : "job-card"}
      aria-label={`Job ${job.sandbox_name}`}
      aria-current={selected ? "true" : undefined}
    >
      <span className="job-card__rail" style={{ background: color }} aria-hidden="true" />
      <div className="job-card__head">
        <div>
          <span className="agent-key">{seriesKey(job)}</span>
          <div className="job-card__feature">{job.feature_id}</div>
        </div>
        {/* Keyed on status so the chip pops when the job moves on. */}
        <StatusChip key={job.status} tone={statusTone(job.status)}>
          {job.status}
        </StatusChip>
      </div>
      {activity && (
        <p
          className={
            job.status === "awaiting_input"
              ? "job-card__activity job-card__activity--question"
              : "job-card__activity"
          }
          title={activity}
        >
          {activity}
        </p>
      )}
      {/* A blocked agent is the one thing on the board that needs a person,
          so the reply goes right on the card. The question itself is the
          activity line above, so it isn't repeated here. */}
      {job.status === "awaiting_input" && <ReplyForm job={job} showQuestion={false} />}
      <dl>
        <Metric label="Model" value={job.model} />
        <Metric
          label="Started"
          value={formatTimestamp(job.started_at)}
          missing="not started"
        />
        <Metric label="Duration" value={formatDuration(elapsedSeconds(job, now))} />
        <Metric label="Tokens" value={tokens === null ? null : `${tokens}`} />
        <Metric label="Tool calls" value={job.tool_calls === null ? null : `${job.tool_calls}`} />
        <Metric label="Diff" value={formatDiff(job)} />
        <Metric label="Branch" value={job.branch} />
      </dl>
      {(job.pr_url || onSelect || showStop || showRespawn || showDelete) && (
        <div className="job-card__foot">
          {job.pr_url ? <a href={job.pr_url}>View pull request</a> : <span />}
          <div className="actions">
            {showStop && (
              <button type="button" disabled={pending} onClick={() => void handleStop()}>
                Stop
              </button>
            )}
            {showRespawn && (
              <button type="button" disabled={pending} onClick={() => void handleRespawn()}>
                Respawn
              </button>
            )}
            {onSelect && (
              <button
                type="button"
                aria-pressed={selected}
                onClick={() => onSelect(job)}
              >
                Inspect
              </button>
            )}
            {showDelete && <DeleteJobButton sandboxName={job.sandbox_name} />}
          </div>
        </div>
      )}
      {error && <Notice tone="danger">{error}</Notice>}
    </article>
  );
}
