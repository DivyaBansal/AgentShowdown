/** Store a credential in sbx's secret store.
 *
 * A div overlay rather than <dialog>: jsdom's HTMLDialogElement has no
 * showModal(), so a <dialog> version would throw in every test while
 * behaving differently from the browser. Hand-rolling role="dialog" keeps
 * behaviour identical in both and queryable by accessible name.
 *
 * The command/reference methods are offered first because they store a
 * *reference* that sbx resolves on demand -- the secret itself never passes
 * through this application.
 */

import { useEffect, useRef, useState } from "react";
import { ApiError, storeSecret, type SecretInput } from "../api";
import { Field } from "./ui/Field";
import { Notice } from "./ui/Notice";

const SERVICES = [
  "anthropic",
  "cursor",
  "droid",
  "github",
  "google",
  "groq",
  "mistral",
  "nebius",
  "openai",
  "openrouter",
  "xai",
];

type Method = "command" | "ref" | "token";

export function SecretModal({ onClose }: { onClose: () => void }) {
  const [service, setService] = useState("anthropic");
  const [method, setMethod] = useState<Method>("command");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    firstField.current?.focus();
    return () => previous?.focus();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const input: SecretInput = { service };
    if (method === "command") input.command = value;
    else if (method === "ref") input.ref = value;
    else input.token = value;
    try {
      await storeSecret(input);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not store the secret");
    } finally {
      setBusy(false);
    }
  }

  const labels: Record<Method, string> = {
    command: "Command",
    ref: "Reference",
    token: "Token",
  };

  return (
    <div
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
      role="presentation"
    >
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="secret-title">
        <h2 id="secret-title">Store a secret</h2>
        <form className="controls" onSubmit={handleSubmit}>
          <Field id="secret-service" label="Service">
            <select
              id="secret-service"
              ref={firstField}
              value={service}
              onChange={(e) => setService(e.target.value)}
            >
              {SERVICES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>

          <fieldset className="group">
            <legend>Source</legend>
            {(["command", "ref", "token"] as Method[]).map((m) => (
              <label key={m} className="check">
                <input
                  type="radio"
                  name="secret-method"
                  checked={method === m}
                  onChange={() => setMethod(m)}
                />
                {m === "command" && "Command (recommended)"}
                {m === "ref" && "Reference (1Password / AWS)"}
                {m === "token" && "Paste a token"}
              </label>
            ))}
          </fieldset>

          <Field id="secret-value" label={labels[method]}>
            <input
              id="secret-value"
              className={method === "token" ? undefined : "mono"}
              type={method === "token" ? "password" : "text"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={
                method === "command"
                  ? "gh auth token"
                  : method === "ref"
                    ? "op://vault/item/credential"
                    : ""
              }
              required
            />
          </Field>

          {method === "token" ? (
            <Notice tone="warn">
              Less secure: the value reaches sbx on the command line, where it is
              briefly visible in <code>ps</code>. A command or reference is stored
              as a pointer instead, so the secret never passes through this app.
            </Notice>
          ) : (
            <p className="hint">
              Stored as a reference sbx resolves on demand. The value never passes
              through agentshowdown.
            </p>
          )}

          <div className="actions">
            <button type="submit" className="btn--primary" disabled={busy || value === ""}>
              {busy ? "Storing…" : "Store"}
            </button>
            <button type="button" className="btn--quiet" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
        {error && <Notice tone="danger">{error}</Notice>}
      </div>
    </div>
  );
}
