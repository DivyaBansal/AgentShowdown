/** A small labelled status pill.
 *
 * Tone picks the colour; the text always carries the meaning, so nothing
 * depends on colour alone.
 */

import type { ReactNode } from "react";

export type Tone = "neutral" | "active" | "ok" | "warn" | "danger";

export function StatusChip({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`chip chip--${tone}`}>{children}</span>;
}
