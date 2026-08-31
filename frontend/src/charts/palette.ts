/** Series colour assignment.
 *
 * Colour follows the *entity*, never its rank: a job's colour is derived
 * from its own identity (agent + run label), so filtering the list or
 * re-sorting it never repaints the survivors. Assigning by array index --
 * the obvious implementation -- would do exactly that.
 *
 * Slots are the validated categorical palette, taken in fixed order and
 * never cycled to invent a ninth hue. Past the cap, series fold into a
 * shared de-emphasis grey rather than getting a generated colour that would
 * be indistinguishable under colour-vision deficiency.
 */

import type { Job } from "../api";

/** CSS custom properties, so light/dark swap in one place. */
export const SERIES_VARS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
] as const;

export const OVERFLOW_VAR = "var(--de-emphasis)";

/** Stable identity for a job's series: the same agent run twice under
 *  different run_labels is two distinct series, which is the whole point of
 *  run_label as a comparison axis. */
export function seriesKey(job: Pick<Job, "agent_id" | "run_label">): string {
  return job.run_label ? `${job.agent_id} · ${job.run_label}` : job.agent_id;
}

/** Builds a stable key -> colour map from the COMPLETE set of jobs.
 *
 * Pass the whole roster, never a filtered subset. Slots are handed out by
 * sorted position, so a shorter list would renumber the survivors -- which
 * is precisely the "colour follows rank" bug this module exists to avoid.
 * Build the map once from everything, then filter what you *render* using
 * the map; do not rebuild it per view.
 *
 * `assignSeriesColorsFor` below makes that shape hard to get wrong.
 */
export function assignSeriesColors(roster: Job[]): Map<string, string> {
  const keys = [...new Set(roster.map(seriesKey))].sort((a, b) => a.localeCompare(b));
  const colors = new Map<string, string>();
  keys.forEach((key, index) => {
    colors.set(key, SERIES_VARS[index] ?? OVERFLOW_VAR);
  });
  return colors;
}

/** Returns a lookup closed over the full roster.
 *
 * Handing components a *function* rather than a map built from whatever
 * subset they happen to hold means a filtered view physically cannot
 * renumber the palette: there is no per-view recomputation to get wrong.
 */
export function assignSeriesColorsFor(
  roster: Job[],
): (job: Pick<Job, "agent_id" | "run_label">) => string {
  const colors = assignSeriesColors(roster);
  return (job) => colors.get(seriesKey(job)) ?? OVERFLOW_VAR;
}
