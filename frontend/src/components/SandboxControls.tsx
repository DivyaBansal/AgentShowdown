/** Ping a sandbox, remove one, or wipe them all. */

import { useState } from "react";
import {
  ApiError,
  killAllSandboxes,
  pingSandbox,
  type PingResult,
  type SandboxSummary,
} from "../api";

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

  async function handlePing(name: string) {
    setError(null);
    try {
      setPing(await pingSandbox(name));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ping failed");
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
    <section className="card">
      <h2>Sandboxes</h2>
      {sandboxes.length === 0 ? (
        <p className="hint">No sandboxes are running.</p>
      ) : (
        <ul>
          {sandboxes.map((sandbox) => (
            <li key={sandbox.name}>
              <code>{sandbox.name}</code> — {sandbox.status ?? "unknown"}{" "}
              <button type="button" onClick={() => void handlePing(sandbox.name)}>
                Ping
              </button>
            </li>
          ))}
        </ul>
      )}

      {ping && (
        <p role="status">
          <code>{ping.sandbox_name}</code>:{" "}
          {ping.listed
            ? `${ping.reachable ? "reachable" : "unreachable"} in ${ping.latency_ms}ms, agent ${
                ping.agent_alive ? "alive" : "not running"
              }`
            : "not listed — the sandbox is gone"}
        </p>
      )}

      <h3>Remove every sandbox</h3>
      <p className="hint">
        This runs <code>sbx rm --all --force</code>, which removes every sandbox on
        this machine — including any this app did not create. Work not yet fetched
        from a running sandbox is lost. Type <code>{CONFIRM_PHRASE}</code> to enable.
      </p>
      <div className="field-row">
        <div className="field">
          <label htmlFor="kill-confirm">Confirmation</label>
          <input
            id="kill-confirm"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={CONFIRM_PHRASE}
          />
        </div>
        <button
          type="button"
          className="danger"
          disabled={confirm !== CONFIRM_PHRASE}
          onClick={() => void handleKillAll()}
        >
          Remove all sandboxes
        </button>
      </div>

      {status && <p role="status">{status}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
