/** Series colour assignment.
 *
 * Colour follows the *entity*, never its rank: a job's colour is derived
 * from its own identity (agent + model), so filtering the list or
 * re-sorting it never repaints the survivors. Assigning by array index --
 * the obvious implementation -- would do exactly that.
 *
 * The colour identity is deliberately coarser than the series: two run
 * labels of the same agent and model (typically "plain" and "with-skill")
 * share a hue, and the charts tell them apart by texture and by name. That
 * keeps each pair visibly a pair, and stretches the eight slots further.
 *
 * Slots are the validated categorical palette (eight hues, checked for
 * colour-vision deficiency in both themes; see tokens.css), taken in fixed
 * order and never cycled to invent a ninth hue. Past the cap, series fold into a
 * shared de-emphasis grey rather than getting a generated colour that would
 * be indistinguishable under colour-vision deficiency.
 */

import type { Job } from "../api";

/** CSS custom properties, so light/dark swap in one place. */
export const SERIES_VARS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
] as const;

export const OVERFLOW_VAR = "var(--de-emphasis)";

type SeriesIdentity = Pick<Job, "agent_id" | "run_label" | "model">;

// Mirrors backend `_INVALID_LABEL_CHARS`: an unlabelled job is given a
// name-safe form of its model as its run label.
const INVALID_LABEL_CHARS = /[^A-Za-z0-9.-]+|\.{2,}/g;

/** Whether `label` is just the one the backend derived from `model`, which
 *  would only repeat the model in the series name. */
function isDefaultLabel(label: string, model: string | null): boolean {
  return model !== null && label === model.replace(INVALID_LABEL_CHARS, "-");
}

/** Which colour a job wears: its agent plus model. */
export function colorKey(job: SeriesIdentity): string {
  return job.model ? `${job.agent_id} · ${job.model}` : job.agent_id;
}

/** Stable, readable identity for a job's series: agent, model, then the
 *  user's run_label when it says something the model doesn't. The same
 *  agent under two models or two run_labels is two distinct series -- that
 *  difference is usually the very thing being compared. */
export function seriesKey(job: SeriesIdentity): string {
  const label = job.run_label;

  if (!label || isDefaultLabel(label, job.model)) return colorKey(job);

  return `${colorKey(job)} · ${label}`;
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
  const keys = [...new Set(roster.map(colorKey))].sort((a, b) => a.localeCompare(b));
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
): (job: SeriesIdentity) => string {
  const colors = assignSeriesColors(roster);

  return (job) => colors.get(colorKey(job)) ?? OVERFLOW_VAR;
}
