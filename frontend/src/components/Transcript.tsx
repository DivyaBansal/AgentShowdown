/** A best-effort structured view of an agent's JSON output: one row per
 *  parsed event, tagged with whatever the agent itself called it.
 *
 * Agents that emit no JSON (cursor, opencode, copilot, a custom
 * `command:`) have nothing to show here -- the caller falls back to the
 * raw log for those.
 */

import type { TranscriptEntry } from "../api";
import { StatusChip } from "./ui/StatusChip";

export function Transcript({ entries }: { entries: TranscriptEntry[] }) {
  if (entries.length === 0) {
    return <p className="hint">No structured events found in this agent&rsquo;s output.</p>;
  }

  return (
    <ol className="list">
      {entries.map((entry) => (
        <li key={entry.index}>
          {entry.kind && <StatusChip>{entry.kind}</StatusChip>}
          <span className="list__main">{entry.text}</span>
        </li>
      ))}
    </ol>
  );
}
