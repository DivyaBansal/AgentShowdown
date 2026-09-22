/** Which services have a credential stored, and a way to add one.
 *
 * Names only: `GET /secrets` reports which services sbx holds a secret for,
 * never a value. The list is re-read when the store dialog closes, so a
 * save is visible without leaving the page.
 *
 * Renders only the body; the surrounding Panel supplies the heading.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchSecrets, type SecretList } from "../api";
import { SecretModal } from "./SecretModal";
import { Notice } from "./ui/Notice";
import { StatusChip } from "./ui/StatusChip";

export function SecretsManager({
  onSecretsChange,
}: {
  onSecretsChange?: (list: SecretList) => void;
}) {
  const [list, setList] = useState<SecretList | null>(null);
  const [showModal, setShowModal] = useState(false);

  const load = useCallback(() => {
    const show = (result: SecretList) => {
      setList(result);
      onSecretsChange?.(result);
    };
    fetchSecrets()
      .then(show)
      .catch(() => show({ available: false, secrets: [] }));
    // onSecretsChange is a notification, not an input to the fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(load, [load]);

  return (
    <>
      <p className="hint">
        Stored by sbx, never by agentshowdown. Prefer a command or reference so the
        value never passes through this app.
      </p>

      {list !== null && !list.available && (
        <Notice tone="warn">
          Could not read the sbx secret store. Check that sbx is installed and its
          daemon is running.
        </Notice>
      )}

      {list !== null && list.available && list.secrets.length === 0 && (
        <p className="hint">No secrets stored yet.</p>
      )}

      {list !== null && list.secrets.length > 0 && (
        <ul className="list" aria-label="Stored secrets">
          {list.secrets.map((secret) => (
            <li key={`${secret.scope}-${secret.name}`}>
              <span className="list__main">
                <strong>{secret.name}</strong>{" "}
                <span className="hint">
                  {secret.scope} · {secret.type}
                </span>
              </span>
              <StatusChip tone="ok">{secret.state.replace(/[()]/g, "")}</StatusChip>
            </li>
          ))}
        </ul>
      )}

      <div className="actions">
        <button type="button" onClick={() => setShowModal(true)}>
          Store a secret
        </button>
      </div>

      {showModal && (
        <SecretModal
          onClose={() => {
            setShowModal(false);
            load();
          }}
        />
      )}
    </>
  );
}
