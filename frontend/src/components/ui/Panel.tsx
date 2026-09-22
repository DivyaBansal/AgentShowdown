/** A collapsible section with a title, a one-line summary and a status.
 *
 * Built on <details>/<summary> rather than a button toggling a hidden
 * region: keyboard and screen-reader behaviour come from the platform, and
 * collapsed content stays in the DOM so a form inside keeps its state.
 *
 * Open state is owned here after the first render. `defaultOpen` is read
 * once, so data arriving later (a repository getting selected, say) never
 * snaps a panel shut while someone is working in it.
 */

import { useState, type ReactNode } from "react";
import { StatusChip, type Tone } from "./StatusChip";

export interface PanelStatus {
  tone: Tone;
  text: string;
}

export function Panel({
  title,
  step,
  summary,
  status,
  defaultOpen = false,
  children,
}: {
  title: string;
  step?: number;
  summary?: ReactNode | undefined;
  status?: PanelStatus | undefined;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <details
      className="panel"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="panel__summary">
        <span className="panel__chevron" aria-hidden="true" />
        {step !== undefined ? (
          <span className="panel__step" aria-hidden="true">
            {String(step).padStart(2, "0")}
          </span>
        ) : (
          <span />
        )}
        <span className="panel__heading">
          <h2>{title}</h2>
          {summary && <span className="panel__summary-text">{summary}</span>}
        </span>
        {status ? <StatusChip tone={status.tone}>{status.text}</StatusChip> : <span />}
      </summary>
      <div className="panel__body">{children}</div>
    </details>
  );
}
