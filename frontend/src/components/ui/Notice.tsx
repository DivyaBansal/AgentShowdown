/** An inline message after an action: saved, failed, or a warning.
 *
 * The ARIA role follows the tone so callers cannot mismatch them: problems
 * are announced assertively as alerts, confirmations politely as status.
 */

import type { ReactNode } from "react";

export type NoticeTone = "neutral" | "ok" | "warn" | "danger";

export function Notice({ tone, children }: { tone: NoticeTone; children: ReactNode }) {
  const role = tone === "danger" || tone === "warn" ? "alert" : "status";
  return (
    <div role={role} className={`notice notice--${tone}`}>
      {children}
    </div>
  );
}
