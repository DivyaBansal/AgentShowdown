/** What an agent did inside its sandbox, step by step: every tool call,
 *  with the command or file it acted on, and every model request.
 *
 * Built from the OTel events claude and codex export (see the backend's
 * agent_otel). Other agents export none, so for them this says so rather
 * than showing an empty list that looks like a stuck run.
 *
 * Polls while the job is live and fetches once more when it stops, so the
 * batch the agent flushed on exit is shown too. Keyed by sandbox name by
 * the caller, so switching jobs starts clean.
 */

import { useEffect, useState } from "react";
import { ApiError, fetchAgentEvents, type AgentEvents, type TimelineEntry } from "../api";
import { Notice } from "./ui/Notice";
import { StatusChip, type Tone } from "./ui/StatusChip";

const POLL_MS = 3000;

type Filter = "tools" | "all";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "tools", label: "Tool calls" },
  { value: "all", label: "Everything" },
];

function toneFor(entry: TimelineEntry): Tone {
  if (entry.ok === false || entry.kind === "error") return "danger";

  if (entry.kind === "tool") return "ok";

  if (entry.kind === "model") return "active";

  return "neutral";
}

/** "+m:ss" from the first event, or "" when either time is unknown. */
function offset(at: string | null, start: number): string {
  if (at === null || Number.isNaN(start)) return "";
  const ms = Date.parse(at) - start;

  if (Number.isNaN(ms)) return "";
  const seconds = Math.max(0, Math.round(ms / 1000));

  return `+${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function formatDuration(ms: number | null): string {
  if (ms === null) return "";

  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function ToolTable({ tools }: { tools: AgentEvents["summary"]["tools"] }) {
  const rows = Object.entries(tools).sort(([, a], [, b]) => b.calls - a.calls);

  if (rows.length === 0) return null;

  return (
    <table className="timeline__tools">
      <caption className="visually-hidden">Tool calls by tool</caption>
      <thead>
        <tr>
          <th scope="col">Tool</th>
          <th scope="col">Calls</th>
          <th scope="col">Failed</th>
          <th scope="col">Time</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([tool, rollup]) => (
          <tr key={tool}>
            <th scope="row" className="mono">{tool}</th>
            <td className="num">{rollup.calls}</td>
            <td className="num">{rollup.failures}</td>
            <td className="num">{formatDuration(rollup.duration_ms)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AgentTimeline({ sandboxName, live }: { sandboxName: string; live: boolean }) {
  const [data, setData] = useState<AgentEvents | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("tools");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const next = await fetchAgentEvents(sandboxName);

        if (!cancelled) {
          setData(next);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Could not load the agent timeline");
        }
      }
    }

    void load();
    const timer = live ? setInterval(() => void load(), POLL_MS) : undefined;

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sandboxName, live]);

  const timeline = data?.timeline ?? [];
  const shown = filter === "tools" ? timeline.filter((e) => e.kind === "tool" || e.kind === "error") : timeline;
  const firstAt = timeline.find((e) => e.at !== null)?.at ?? null;
  const start = firstAt === null ? Number.NaN : Date.parse(firstAt);
  const toolCalls = timeline.filter((e) => e.kind === "tool");
  const failed = toolCalls.filter((e) => e.ok === false).length;
  const cost = data?.summary.cost_usd ?? null;

  return (
    <section className="stack stack--tight" aria-labelledby={`timeline-${sandboxName}`}>
      <div className="surface__head">
        <h3 className="eyebrow" id={`timeline-${sandboxName}`}>Agent timeline</h3>
        <div className="actions" role="group" aria-label="Show">
          {FILTERS.map((option) => (
            <button
              key={option.value}
              type="button"
              className="btn--quiet"
              aria-pressed={filter === option.value}
              onClick={() => setFilter(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {error && <Notice tone="danger">{error}</Notice>}

      {data !== null && timeline.length === 0 && (
        <p className="hint">
          {live
            ? "No telemetry yet. Events appear here as the agent works."
            : "No telemetry from this agent. Only claude and codex export it, and only when sbx lets the sandbox reach this server."}
        </p>
      )}

      {timeline.length > 0 && (
        <>
          <p className="hint">
            {toolCalls.length} tool {toolCalls.length === 1 ? "call" : "calls"}
            {failed > 0 && `, ${failed} failed`}
            {cost !== null && ` · $${cost.toFixed(4)}`}
          </p>
          <ToolTable tools={data?.summary.tools ?? {}} />
          {shown.length === 0 ? (
            <p className="hint">No tool calls yet.</p>
          ) : (
            <ol className="list timeline" aria-label="Agent steps">
              {shown.map((entry) => (
                <li key={entry.seq}>
                  <span className="num timeline__at">{offset(entry.at, start)}</span>
                  <StatusChip tone={toneFor(entry)}>
                    {entry.title}
                    {entry.ok === false && <span className="visually-hidden"> (failed)</span>}
                  </StatusChip>
                  <span className={`list__main${entry.kind === "tool" ? " mono" : ""}`}>
                    {entry.detail ?? ""}
                  </span>
                  <span className="num hint">{formatDuration(entry.duration_ms)}</span>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
