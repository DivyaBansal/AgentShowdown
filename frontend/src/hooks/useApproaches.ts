/** The approach (tool calls sorted into phases) of each named job.
 *
 * Re-fetched every few seconds while `live` is true, so a running job's
 * strip grows as its agent works, and once more when `live` turns false,
 * so the batch an agent flushes on exit is shown too. The agents push
 * their telemetry to the backend as they go; this only reads the database,
 * never the sandboxes, so polling costs nothing on the sandbox side.
 *
 * Results are keyed by sandbox name and only the requested names are
 * returned, so switching features never shows the previous feature's runs.
 */

import { useEffect, useState } from "react";
import { fetchApproach, type Approach } from "../api";

const POLL_MS = 3000;

export type ApproachResult = { ok: true; approach: Approach } | { ok: false; error: string };

/** A response that isn't an approach (an old backend, a proxy page) is an
 *  error, not a crash in the chart. */
function isApproach(value: unknown): value is Approach {
  return (
    typeof value === "object" &&
    value !== null &&
    "steps" in value &&
    Array.isArray(value.steps) &&
    "phases" in value &&
    Array.isArray(value.phases) &&
    "stats" in value &&
    typeof value.stats === "object" &&
    value.stats !== null
  );
}

export function useApproaches(names: string[], live: boolean): Map<string, ApproachResult> {
  const [results, setResults] = useState<Map<string, ApproachResult>>(new Map());
  // A string, so the effect re-runs when the set of jobs changes, not on
  // every render's fresh array.
  const key = [...names].sort().join("\n");

  useEffect(() => {
    const wanted = key === "" ? [] : key.split("\n");

    if (wanted.length === 0) return;
    let cancelled = false;

    const load = () => {
      void Promise.all(
        wanted.map(async (name): Promise<[string, ApproachResult]> => {
          try {
            const body: unknown = await fetchApproach(name);

            return isApproach(body)
              ? [name, { ok: true, approach: body }]
              : [name, { ok: false, error: "Unexpected response from the server" }];
          } catch (err) {
            return [name, { ok: false, error: err instanceof Error ? err.message : String(err) }];
          }
        }),
      ).then((entries) => {
        if (!cancelled) setResults(new Map(entries));
      });
    };

    load();
    const timer = live ? setInterval(load, POLL_MS) : undefined;

    return () => {
      cancelled = true;

      if (timer !== undefined) clearInterval(timer);
    };
  }, [key, live]);

  const shown = new Map<string, ApproachResult>();

  for (const name of names) {
    const result = results.get(name);

    if (result !== undefined) shown.set(name, result);
  }

  return shown;
}
