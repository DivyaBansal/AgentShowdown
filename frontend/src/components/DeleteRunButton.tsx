/** Deletes one finished run's history, after a second click to confirm.
 *
 * Only the record goes -- the run, its jobs and their telemetry. Branches
 * and PRs it produced are untouched. The server refuses while anything in
 * the run is still active or a sandbox from it still exists, and that
 * refusal is shown as-is. On success the run disappears through the live
 * stream's `run_deleted` event, so there's nothing to report here.
 */

import { useState } from "react";
import { ApiError, deleteRun } from "../api";
import { Notice } from "./ui/Notice";

export function DeleteRunButton({ runId, disabled = false }: { runId: string; disabled?: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setError(null);
    setPending(true);

    try {
      await deleteRun(runId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the run");
      setConfirming(false);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="stack stack--tight">
      <div className="actions">
        {confirming ? (
          <>
            <span>Delete this run's history? Branches and PRs are kept.</span>
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
          </>
        ) : (
          <button
            type="button"
            disabled={disabled}
            title={disabled ? "Wait for every job in this run to finish" : undefined}
            onClick={() => setConfirming(true)}
          >
            Delete run
          </button>
        )}
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
    </div>
  );
}
