/** Everything about one contender, for the side drawer.
 *
 * The headline numbers and any reply the job is waiting on sit at the top.
 * The long views (activity, output, telemetry) are tabs below, not stacked,
 * so the one you want is a click away instead of a scroll past the others.
 * Every tab stays mounted while hidden: the timeline keeps polling and the
 * log keeps any deeper fetch, so flipping between them loses nothing.
 */

import type { Job } from "../api";
import { TelemetryChart } from "../charts/TelemetryChart";
import { seriesKey } from "../charts/palette";
import { useNow } from "../hooks/useNow";
import { usePref } from "../hooks/usePref";
import { useSamples } from "../hooks/useSamples";
import {
  canStop,
  elapsedSeconds,
  formatDiff,
  formatDuration,
  statusTone,
  totalTokens,
} from "../lib/jobs";
import { AgentTimeline } from "./AgentTimeline";
import { LogView } from "./LogView";
import { ReplyForm } from "./ReplyForm";
import { StatusChip } from "./ui/StatusChip";
import { Tabs } from "./ui/Tabs";
import { panelId, tabId } from "./ui/tabIds";

const SECTIONS = ["activity", "output", "telemetry"] as const;

type Section = (typeof SECTIONS)[number];

const SECTION_LABELS: Record<Section, string> = {
  activity: "Activity",
  output: "Output",
  telemetry: "Telemetry",
};

const GROUP = "inspector";

export function JobInspector({
  job,
  color,
  position,
  onPrevious,
  onNext,
  onClose,
}: {
  job: Job;
  color: string;
  /** Where this job sits in the board's current order, 1-based. */
  position: { index: number; total: number };
  onPrevious: (() => void) | null;
  onNext: (() => void) | null;
  onClose: () => void;
}) {
  const [section, setSection] = usePref<Section>("inspector.section", SECTIONS, "activity");
  const samples = useSamples(job.sandbox_name);
  const live = job.status === "running" || job.status === "queued";
  const now = useNow(canStop(job.status));
  const tokens = totalTokens(job);

  const facts: [string, string | null][] = [
    ["Model", job.model],
    ["Duration", formatDuration(elapsedSeconds(job, now))],
    ["Tokens", tokens === null ? null : tokens.toLocaleString()],
    ["Diff", formatDiff(job)],
  ];

  return (
    <div className="inspector">
      <header className="inspector__head">
        <span className="inspector__rail" style={{ background: color }} aria-hidden="true" />
        <div className="inspector__title">
          <span className="agent-key">{seriesKey(job)}</span>
          <h2 className="mono">{job.sandbox_name}</h2>
          <span className="hint">{job.feature_id}</span>
        </div>
        <div className="inspector__nav">
          <span className="hint num" aria-label={`Contender ${position.index} of ${position.total}`}>
            {position.index}/{position.total}
          </span>
          <button
            type="button"
            className="btn--quiet"
            aria-label="Previous contender"
            disabled={onPrevious === null}
            onClick={() => onPrevious?.()}
          >
            ‹
          </button>
          <button
            type="button"
            className="btn--quiet"
            aria-label="Next contender"
            disabled={onNext === null}
            onClick={() => onNext?.()}
          >
            ›
          </button>
          <button type="button" className="btn--quiet" onClick={onClose}>
            Close
          </button>
        </div>
      </header>

      {/* Keyed per job so switching contenders replays the entrance and a
          half-typed reply never carries over to the next one. */}
      <div key={job.sandbox_name} className="inspector__body">
        <div className="inspector__summary">
          {/* Keyed on status so the chip pops when it changes. */}
          <StatusChip key={job.status} tone={statusTone(job.status)}>
            {job.status}
          </StatusChip>
          <dl className="inspector__facts">
            {facts.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd className="num">{value ?? <span className="no-data">—</span>}</dd>
              </div>
            ))}
          </dl>
          {job.pr_url && <a href={job.pr_url}>View pull request</a>}
        </div>

        <ReplyForm job={job} />
        {/* For a waiting job the detail *is* the question, already shown by
            the reply form. */}
        {job.detail && job.status !== "awaiting_input" && <pre className="log">{job.detail}</pre>}

        <Tabs
          group={GROUP}
          label="Contender details"
          className="subtabs"
          items={SECTIONS.map((value) => ({ value, label: SECTION_LABELS[value] }))}
          value={section}
          onChange={setSection}
        />

        <div
          role="tabpanel"
          id={panelId(GROUP, "activity")}
          aria-labelledby={tabId(GROUP, "activity")}
          className="section-panel"
          hidden={section !== "activity"}
        >
          <AgentTimeline sandboxName={job.sandbox_name} live={live} />
        </div>
        <div
          role="tabpanel"
          id={panelId(GROUP, "output")}
          aria-labelledby={tabId(GROUP, "output")}
          className="section-panel"
          hidden={section !== "output"}
        >
          <LogView sandboxName={job.sandbox_name} tail={job.log_tail} />
        </div>
        <div
          role="tabpanel"
          id={panelId(GROUP, "telemetry")}
          aria-labelledby={tabId(GROUP, "telemetry")}
          className="section-panel"
          hidden={section !== "telemetry"}
        >
          <TelemetryChart
            samples={samples}
            telemetry={job.telemetry === null ? null : job.telemetry === 1}
            live={live}
          />
        </div>
      </div>
    </div>
  );
}
