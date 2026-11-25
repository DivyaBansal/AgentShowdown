/** Deletes one finished contender's history, after a second click to confirm.
 *
 * Only this contender goes; the rest of its run stays.
 * Only the record goes -- the job and its telemetry; its branch and PR are
 * untouched. The server refuses while the job is live or its sandbox still
 * exists, and that refusal is shown as-is. On success the card disappears
 * through the live stream's `job_deleted` event.
 */

import { useState } from "react";
import { ApiError, deleteJob } from "../api";
import { Notice } from "./ui/Notice";

export function DeleteJobButton({ sandboxName }: { sandboxName: string }) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setError(null);
    setPending(true);

    try {
      await deleteJob(sandboxName);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the job");
      setConfirming(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="delete-job">
      {confirming ? (
        <div className="actions">
          <span>Delete this contender? Its branch and PR are kept.</span>
          <button type="button" disabled={pending} onClick={() => void handleDelete()}>
            Confirm delete
          </button>
          <button
            type="button"
            className="btn--quiet"
            disabled={pending}
            onClick={() => setConfirming(false)}
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="btn--quiet"
          aria-label={`Delete ${sandboxName}`}
          onClick={() => setConfirming(true)}
        >
          Delete
        </button>
      )}
      {error && <Notice tone="danger">{error}</Notice>}
    </div>
  );
}
