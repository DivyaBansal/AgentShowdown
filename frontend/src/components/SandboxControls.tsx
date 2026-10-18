/** Ping a sandbox, remove one, or wipe them all.
 *
 * Renders only the body; the surrounding Panel supplies the heading.
 */

import { useState } from "react";
import {
  ApiError,
  killAllSandboxes,
  pingSandbox,
  removeSandbox,
  type PingResult,
  type SandboxSummary,
} from "../api";
import { Field } from "./ui/Field";
import { Notice } from "./ui/Notice";
import { StatusChip } from "./ui/StatusChip";

const CONFIRM_PHRASE = "KILL ALL";

export function SandboxControls({
  sandboxes,
  onChanged,
}: {
  sandboxes: SandboxSummary[];
  onChanged: () => void;
}) {
  const [ping, setPing] = useState<PingResult | null>(null);
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  // Set when a plain remove was refused for having commits unique to that
  // sandbox -- offers a one-click "remove anyway" rather than making the
  // force flag a permanent, easy-to-misclick checkbox.
  const [removeConflict, setRemoveConflict] = useState<{ name: string; message: string } | null>(
    null,
  );

  async function handlePing(name: string) {
    setError(null);

    try {
      setPing(await pingSandbox(name));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ping failed");
    }
  }

  async function handleRemove(name: string, force = false) {
    setError(null);
    setRemoveConflict(null);

    try {
      await removeSandbox(name, force);
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setRemoveConflict({ name, message: err.message });

        return;
      }

      setError(err instanceof ApiError ? err.message : "Could not remove the sandbox");
    }
  }

  async function handleKillAll() {
    setError(null);
    setStatus(null);

    try {
      const result = await killAllSandboxes();
      setStatus(
        `Removed ${result.removed.length} sandbox(es); ${result.jobs_marked_lost} job(s) marked lost.`,
      );
      setConfirm("");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove sandboxes");
    }
  }

  return (
    <>
      {sandboxes.length === 0 ? (
        <p className="hint">No sandboxes are running.</p>
      ) : (
        <ul className="list">
          {sandboxes.map((sandbox) => (
            <li key={sandbox.name}>
              <span className="list__main mono">{sandbox.name}</span>
              <StatusChip>{sandbox.status ?? "unknown"}</StatusChip>
              <button type="button" onClick={() => void handlePing(sandbox.name)}>
                Ping
              </button>
              <button type="button" onClick={() => void handleRemove(sandbox.name)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {removeConflict && (
        <Notice tone="warn">
          {removeConflict.message}{" "}
          <button
            type="button"
            className="btn--danger"
            onClick={() => void handleRemove(removeConflict.name, true)}
          >
            Remove anyway
          </button>
        </Notice>
      )}

      {ping && (
        <Notice tone="ok">
          <code>{ping.sandbox_name}</code>:{" "}
          {ping.listed
            ? `${ping.reachable ? "reachable" : "unreachable"} in ${ping.latency_ms}ms, agent ${
                ping.agent_alive ? "alive" : "not running"
              }`
            : "not listed; the sandbox is gone"}
        </Notice>
      )}

      <div className="panel__section stack">
        <h3 className="eyebrow">Remove every sandbox</h3>
        <p className="hint">
          This runs <code>sbx rm --all --force</code>, which removes every sandbox on
          this machine, including any this app did not create. Work not yet fetched
          from a running sandbox is lost. Type <code>{CONFIRM_PHRASE}</code> to enable.
        </p>
        <div className="field-grid field-grid--bottom">
          <Field id="kill-confirm" label="Confirmation">
            <input
              id="kill-confirm"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder={CONFIRM_PHRASE}
            />
          </Field>
          <div className="actions">
            <button
              type="button"
              className="btn--danger"
              disabled={confirm !== CONFIRM_PHRASE}
              onClick={() => void handleKillAll()}
            >
              Remove all sandboxes
            </button>
          </div>
        </div>
      </div>

      {status && <Notice tone="ok">{status}</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}
    </>
  );
}
