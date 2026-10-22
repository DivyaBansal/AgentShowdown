/** Telemetry samples for the job named `name`, or none when `name` is null.
 *
 * Results are kept with the name they were fetched for, so switching jobs
 * never shows the previous job's chart, and a slow response for a job that
 * is no longer selected is dropped instead of overwriting the current one.
 */

import { useEffect, useState } from "react";
import { fetchSamples, type Sample } from "../api";

interface Loaded {
  name: string;
  samples: Sample[];
}

export function useSamples(name: string | null): Sample[] {
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    if (name === null) return;
    let cancelled = false;

    fetchSamples(name)
      .then((samples) => { if (!cancelled) setLoaded({ name, samples }); })
      .catch(() => { if (!cancelled) setLoaded({ name, samples: [] }); });

    return () => { cancelled = true; };
  }, [name]);

  return loaded !== null && loaded.name === name ? loaded.samples : [];
}
