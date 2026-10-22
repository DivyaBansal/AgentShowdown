/** A job's run output: the live tail by default, or a deeper on-demand
 *  fetch once asked for. Keyed by sandbox name by the caller so switching
 *  between jobs resets the "load more" state via a remount.
 */

import { useState } from "react";
import { ApiError, fetchJobLog, fetchJobTranscript, type TranscriptEntry } from "../api";
import { Notice } from "./ui/Notice";
import { Transcript } from "./Transcript";

const DEEP_FETCH_LINES = 2000;

export function LogView({
  sandboxName,
  tail,
}: {
  sandboxName: string;
  tail: string | null;
}) {
  const [deep, setDeep] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[] | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);

  async function handleLoadMore() {
    setLoading(true);
    setError(null);

    try {
      setDeep(await fetchJobLog(sandboxName, DEEP_FETCH_LINES));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the full log");
    } finally {
      setLoading(false);
    }
  }

  async function handleShowTranscript() {
    setTranscriptLoading(true);
    setTranscriptError(null);

    try {
      setTranscript(await fetchJobTranscript(sandboxName));
    } catch (err) {
      setTranscriptError(err instanceof ApiError ? err.message : "Could not load the transcript");
    } finally {
      setTranscriptLoading(false);
    }
  }

  const text = deep ?? tail;

  return (
    <div className="stack stack--tight">
      <div className="surface__head">
        <h3 className="eyebrow">Live output</h3>
        <div className="actions">
          {transcript === null && (
            <button
              type="button"
              className="btn--quiet"
              disabled={transcriptLoading}
              onClick={() => void handleShowTranscript()}
            >
              {transcriptLoading ? "Loading…" : "Show structured view"}
            </button>
          )}
          {deep === null && (
            <button
              type="button"
              className="btn--quiet"
              disabled={loading}
              onClick={() => void handleLoadMore()}
            >
              {loading ? "Loading…" : "Load full log"}
            </button>
          )}
        </div>
      </div>
      {transcript !== null ? (
        <Transcript entries={transcript} />
      ) : text ? (
        <pre className="log">{text}</pre>
      ) : (
        <p className="hint">No output yet.</p>
      )}
      {error && <Notice tone="danger">{error}</Notice>}
      {transcriptError && <Notice tone="danger">{transcriptError}</Notice>}
    </div>
  );
}
