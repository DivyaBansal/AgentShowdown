/** Live job state, kept correct across dropped connections.
 *
 * SSE is an accelerator here, never the source of truth. Three things make
 * a reconnect safe:
 *
 *  1. The browser resends `Last-Event-ID` by itself, and the server replays
 *     what was missed.
 *  2. A gap too large to replay arrives as a `resync` event instead of
 *     silently missing updates.
 *  3. This hook refetches full state on *every* connect regardless, so even
 *     with no replay at all the UI converges on the truth.
 *
 * That third one is what makes closing the laptop lid safe. The run itself
 * never depended on anyone watching -- it lives in the orchestrator's own
 * threads and in SQLite.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJobs, openEventStream, type Job } from "../api";

export interface JobStream {
  jobs: Job[];
  connected: boolean;
  error: string | null;
  /** How many times the stream has (re)connected. Surfaced so the UI can
   *  show that a reconnect happened rather than hiding it. */
  reconnects: number;
  refresh: () => void;
}

export function useJobStream(): JobStream {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reconnects, setReconnects] = useState(0);
  const mounted = useRef(true);

  const reconcile = useCallback(() => {
    fetchJobs()
      .then((next) => {
        if (mounted.current) {
          setJobs(next);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (mounted.current) {
          setError(err instanceof Error ? err.message : "Could not load jobs");
        }
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    reconcile();

    let source: EventSource;
    try {
      source = openEventStream();
    } catch {
      // No EventSource (very old browser, or a test environment without a
      // stub). Polling-free fallback: the initial reconcile still ran, and
      // the manual refresh button still works.
      return () => {
        mounted.current = false;
      };
    }

    const onOpen = () => {
      if (!mounted.current) return;
      setConnected(true);
      setReconnects((n) => n + 1);
      // Reconcile on every connect, not just the first: this is what closes
      // the gap when replay was unavailable.
      reconcile();
    };
    const onChange = () => {
      if (mounted.current) reconcile();
    };
    const onError = () => {
      if (mounted.current) setConnected(false);
    };

    source.addEventListener("open", onOpen);
    source.addEventListener("error", onError);
    source.addEventListener("job_changed", onChange);
    source.addEventListener("run_started", onChange);
    source.addEventListener("run_finished", onChange);
    // The server could not replay far enough back; full refetch is the
    // documented recovery.
    source.addEventListener("resync", onChange);

    return () => {
      mounted.current = false;
      source.removeEventListener("open", onOpen);
      source.removeEventListener("error", onError);
      source.removeEventListener("job_changed", onChange);
      source.removeEventListener("run_started", onChange);
      source.removeEventListener("run_finished", onChange);
      source.removeEventListener("resync", onChange);
      source.close();
    };
  }, [reconcile]);

  return { jobs, connected, error, reconnects, refresh: reconcile };
}
